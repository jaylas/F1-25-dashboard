import socket
import struct
import threading
import time

from PyQt6.QtCore import QObject, pyqtSignal

from core.models import (
    CAR_TELEMETRY_BRAKE_OFFSET,
    CAR_DAMAGE_DATA_SIZE_FALLBACK,
    CAR_DAMAGE_TYRE_WEAR_OFFSET,
    CAR_DAMAGE_TYRE_WEAR_VALUES,
    CAR_TELEMETRY_DATA_SIZE,
    CAR_TELEMETRY_GEAR_FALLBACK_OFFSETS,
    CAR_TELEMETRY_GEAR_OFFSET,
    CAR_TELEMETRY_THROTTLE_OFFSET,
    CURRENT_LAP_NUM_OFFSET,
    DEFAULT_LAP_DISTANCE_OFFSET,
    HEADER_FORMAT,
    HEADER_SIZE,
    LAP_DATA_SIZE,
    PACKET_MOTION_DATA,
    MOTION_DATA_SIZE,
    PACKET_CAR_DAMAGE,
    PACKET_CAR_TELEMETRY,
    PACKET_LAP_DATA,
    PACKET_SESSION,
    SESSION_TRACK_ID_OFFSET,
    TRACK_NAMES,
    TelemetryFrame,
    UDP_PORT,
)


class TelemetryListener(QObject):
    telemetry_received = pyqtSignal(object)
    connection_state_changed = pyqtSignal(bool)
    track_changed = pyqtSignal(int, str)  # track_id, track_name

    def __init__(self, port: int = UDP_PORT) -> None:
        super().__init__()
        self.port = port
        self._running = False
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None
        self._last_packet_time = 0.0
        self._connected = False
        self._latest_frame = TelemetryFrame()
        self._debug_count = 0
        self._lap_offset_found = False
        self._lap_distance_offset = DEFAULT_LAP_DISTANCE_OFFSET
        self._current_track_id: int = -1
        self._player_car_index: int = 0

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._socket:
            try:
                self._socket.close()
            except OSError:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _set_connected(self, connected: bool) -> None:
        if connected != self._connected:
            self._connected = connected
            self.connection_state_changed.emit(connected)

    def _run(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", self.port))
            sock.settimeout(0.2)
            self._socket = sock
        except OSError:
            self._set_connected(False)
            self._running = False
            return

        while self._running:
            try:
                data, _addr = sock.recvfrom(2048)
            except socket.timeout:
                if time.time() - self._last_packet_time > 1.0:
                    self._set_connected(False)
                continue
            except OSError:
                break

            self._last_packet_time = time.time()
            self._set_connected(True)

            parsed = self._parse_packet(data)
            if parsed is not None:
                self.telemetry_received.emit(parsed)

            self._debug_count += 1
            if self._debug_count <= 10:
                if len(data) >= HEADER_SIZE:
                    try:
                        vals = struct.unpack_from(HEADER_FORMAT, data, 0)
                        print(f"[PKT #{self._debug_count}] id={vals[5]}, size={len(data)}")
                    except struct.error:
                        pass

        self._set_connected(False)

    def _parse_packet(self, data: bytes) -> TelemetryFrame | None:
        if len(data) < HEADER_SIZE:
            return None

        try:
            (
                _packet_format,
                _game_year,
                _game_major,
                _game_minor,
                _packet_version,
                packet_id,
                _session_uid,
                session_time,
                _frame_identifier,
                _overall_frame_identifier,
                player_car_index,
                _secondary_player_car_index,
            ) = struct.unpack_from(HEADER_FORMAT, data, 0)
        except struct.error:
            return None

        if 0 <= int(player_car_index) < 22:
            self._player_car_index = int(player_car_index)
        p_idx = self._player_car_index

        if packet_id == PACKET_SESSION:
            self._extract_track_id(data)
            return None

        frame = TelemetryFrame(
            brake=self._latest_frame.brake,
            throttle=self._latest_frame.throttle,
            gear=self._latest_frame.gear,
            lap_distance=self._latest_frame.lap_distance,
            lap_number=self._latest_frame.lap_number,
            session_time=session_time,
            source_packet_id=int(packet_id),
            player_pos=self._latest_frame.player_pos,
            player_forward=self._latest_frame.player_forward,
            player_right=self._latest_frame.player_right,
            opponents=self._latest_frame.opponents,
            tyre_wear=self._latest_frame.tyre_wear,
            tyre_wear_raw=self._latest_frame.tyre_wear_raw,
            tyre_damage_raw=self._latest_frame.tyre_damage_raw,
            tyre_source=self._latest_frame.tyre_source,
        )

        if packet_id == PACKET_CAR_TELEMETRY:
            b, t, g = self._extract_inputs(data, p_idx)
            if b is not None:
                frame.brake = b
            if t is not None:
                frame.throttle = t
            if g is not None:
                frame.gear = g

        elif packet_id == PACKET_MOTION_DATA:
            pos, fwd, right, opps = self._extract_motion_data(data, p_idx)
            if pos is not None:
                frame.player_pos = pos
            if fwd is not None:
                frame.player_forward = fwd
            if right is not None:
                frame.player_right = right
            if opps is not None:
                frame.opponents = opps

        elif packet_id == PACKET_LAP_DATA:
            if not self._lap_offset_found and p_idx == 0:
                self._probe_lap_offset(data)
            lap_distance, lap_number = self._extract_lap_data(data, p_idx)
            if self._debug_count <= 20:
                print(f"[LAP] dist={lap_distance}, lap={lap_number}")
            if lap_distance is not None:
                frame.lap_distance = lap_distance
            if lap_number is not None:
                frame.lap_number = lap_number
        elif packet_id == PACKET_CAR_DAMAGE:
            wear_raw, damage_raw = self._extract_tyre_sources(data, p_idx)
            if wear_raw is not None:
                frame.tyre_wear_raw = wear_raw
                frame.tyre_wear = wear_raw
                frame.tyre_source = "wear"
            if damage_raw is not None:
                frame.tyre_damage_raw = damage_raw
        else:
            return None

        self._latest_frame = frame
        return frame

    def _extract_track_id(self, data: bytes) -> None:
        offset = HEADER_SIZE + SESSION_TRACK_ID_OFFSET
        if offset + 1 > len(data):
            return
        try:
            (track_id,) = struct.unpack_from("<b", data, offset)
        except struct.error:
            return
        if track_id != self._current_track_id and track_id >= 0:
            self._current_track_id = track_id
            name = TRACK_NAMES.get(track_id, f"unknown_{track_id}")
            print(f"[TRACK] Detected track_id={track_id} -> {name}")
            self.track_changed.emit(track_id, name)

    @staticmethod
    def _extract_motion_data(data: bytes, p_idx: int) -> tuple[tuple[float, float, float] | None, tuple[float, float, float] | None, tuple[float, float, float] | None, list[tuple[float, float, float]] | None]:
        if len(data) < HEADER_SIZE + (22 * MOTION_DATA_SIZE):
            return None, None, None, None
        
        pos = None
        fwd = None
        right = None
        opps = []
        
        for i in range(22):
            base = HEADER_SIZE + i * MOTION_DATA_SIZE
            try:
                x, y, z = struct.unpack_from("<fff", data, base)
            except struct.error:
                continue
                
            if i == p_idx:
                pos = (x, y, z)
                try:
                    # Offsets: pos=0, vel=12, fwd=24, right=30
                    fx, fy, fz, rx, ry, rz = struct.unpack_from("<hhhhhh", data, base + 24)
                    fwd = (fx / 32767.0, fy / 32767.0, fz / 32767.0)
                    right = (rx / 32767.0, ry / 32767.0, rz / 32767.0)
                except struct.error:
                    pass
            else:
                # Exclude completely inactive cars
                if abs(x) > 0.001 or abs(y) > 0.001 or abs(z) > 0.001:
                    opps.append((x, y, z))
                    
        return pos, fwd, right, opps

    @staticmethod
    def _extract_inputs(data: bytes, player_car_index: int) -> tuple[float | None, float | None, int | None]:
        base = HEADER_SIZE + (player_car_index * CAR_TELEMETRY_DATA_SIZE)
        off_b = base + CAR_TELEMETRY_BRAKE_OFFSET
        off_t = base + CAR_TELEMETRY_THROTTLE_OFFSET
        if off_t + 4 > len(data):
            return None, None, None
        try:
            (b,) = struct.unpack_from("<f", data, off_b)
            (t,) = struct.unpack_from("<f", data, off_t)
        except struct.error:
            return None, None, None

        gear_offsets = (CAR_TELEMETRY_GEAR_OFFSET, *CAR_TELEMETRY_GEAR_FALLBACK_OFFSETS)
        g_val: int | None = None
        for rel_off in gear_offsets:
            off_g = base + rel_off
            if off_g + 1 > len(data):
                continue
            try:
                (candidate,) = struct.unpack_from("<b", data, off_g)
            except struct.error:
                continue
            if -1 <= candidate <= 8:
                g_val = int(candidate)
                break

        return float(b), float(t), g_val

    @staticmethod
    def _extract_tyre_sources(
        data: bytes,
        player_car_index: int,
    ) -> tuple[tuple[float, float, float, float] | None, tuple[float, float, float, float] | None]:
        payload_size = len(data) - HEADER_SIZE
        min_bytes = CAR_DAMAGE_TYRE_WEAR_OFFSET + (CAR_DAMAGE_TYRE_WEAR_VALUES * 4)
        if payload_size < min_bytes or player_car_index < 0:
            return None, None

        stride_candidates: list[int] = []
        if payload_size >= (22 * CAR_DAMAGE_DATA_SIZE_FALLBACK):
            stride_candidates.append(CAR_DAMAGE_DATA_SIZE_FALLBACK)

        # 22 cars is standard, but try other plausible divisors too.
        for car_count in (22, 20):
            dynamic_stride = payload_size // car_count
            if dynamic_stride >= min_bytes:
                stride_candidates.append(dynamic_stride)
            remainder_stride = dynamic_stride + 1
            if remainder_stride >= min_bytes:
                stride_candidates.append(remainder_stride)

        # Keep order and avoid duplicate candidates.
        seen: set[int] = set()
        strides = [s for s in stride_candidates if not (s in seen or seen.add(s))]

        def _parse_at(base: int) -> tuple[tuple[float, float, float, float] | None, tuple[float, float, float, float] | None]:
            wear_vals: tuple[float, float, float, float] | None = None
            damage_vals: tuple[float, float, float, float] | None = None

            # Wear float[4], raw order RL, RR, FL, FR.
            if base + 16 <= len(data):
                try:
                    rl, rr, fl, fr = struct.unpack_from("<ffff", data, base)
                    vals = (float(fl), float(fr), float(rl), float(rr))
                    if all(0.0 <= v <= 100.0 for v in vals):
                        wear_vals = vals
                except struct.error:
                    pass

            # Tyre damage byte[4] right after wear block.
            damage_off = base + 16
            if damage_off + 4 <= len(data):
                try:
                    rl_b, rr_b, fl_b, fr_b = struct.unpack_from("<BBBB", data, damage_off)
                    vals_b = (float(fl_b), float(fr_b), float(rl_b), float(rr_b))
                    if all(0.0 <= v <= 100.0 for v in vals_b):
                        damage_vals = vals_b
                except struct.error:
                    pass
            return wear_vals, damage_vals

        best_wear: tuple[float, float, float, float] | None = None
        best_damage: tuple[float, float, float, float] | None = None

        for stride in strides:
            base = HEADER_SIZE + (player_car_index * stride) + CAR_DAMAGE_TYRE_WEAR_OFFSET
            wear_vals, damage_vals = _parse_at(base)
            if wear_vals is not None and any(v > 0.0 for v in wear_vals):
                best_wear = wear_vals
            if damage_vals is not None and any(v > 0.0 for v in damage_vals):
                best_damage = damage_vals
            if best_wear is not None or best_damage is not None:
                return best_wear, best_damage

        return best_wear, best_damage

    def _probe_lap_offset(self, data: bytes) -> None:
        base = HEADER_SIZE
        best_off = self._lap_distance_offset
        best_val = 0.0

        for off in range(0, LAP_DATA_SIZE - 3):
            try:
                (val,) = struct.unpack_from("<f", data, base + off)
            except struct.error:
                continue
            if 10.0 < val < 8000.0 and val > best_val:
                best_val = val
                best_off = off

        if best_val > 10.0:
            self._lap_distance_offset = best_off
            print(f"[PROBE] Using LAP_DISTANCE_OFFSET={best_off} (val={best_val:.1f}m)")
        self._lap_offset_found = True

    def _extract_lap_data(self, data: bytes, car_idx: int) -> tuple[float | None, int | None]:
        base = HEADER_SIZE + (car_idx * LAP_DATA_SIZE)
        d_off = base + self._lap_distance_offset
        l_off = base + CURRENT_LAP_NUM_OFFSET
        if d_off + 4 > len(data) or l_off + 1 > len(data):
            return None, None
        try:
            (dist,) = struct.unpack_from("<f", data, d_off)
            (lap,) = struct.unpack_from("<B", data, l_off)
        except struct.error:
            return None, None
        return max(0.0, dist), int(lap)
