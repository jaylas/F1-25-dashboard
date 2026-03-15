import json
import socket
import struct
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QObject, QPoint, QPointF, QRectF, Qt, QTimer, pyqtSignal, QEvent
from PyQt6.QtGui import QColor, QCursor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)


# ── F1 UDP constants ────────────────────────────────────────────────
PACKET_SESSION = 1
PACKET_LAP_DATA = 2
PACKET_CAR_TELEMETRY = 6

UDP_PORT = 20777
HEADER_FORMAT = "<HBBBBBQfIIBB"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)  # 29 bytes

# Per-car struct sizes (from actual F1 25 packets):
LAP_DATA_SIZE = 57   # (1285 - 29 - 2) / 22
CAR_TELEMETRY_DATA_SIZE = 60  # (1352 - 29 - 3) / 22

# Offsets within per-car struct
LAP_DISTANCE_OFFSET = 20   # float, will be auto-probed
CURRENT_LAP_NUM_OFFSET = 39  # uint8
CAR_TELEMETRY_THROTTLE_OFFSET = 2   # float
CAR_TELEMETRY_BRAKE_OFFSET = 10  # float
CAR_TELEMETRY_GEAR_OFFSET = 33   # int8

# Session packet: m_trackId offset within session data (after header)
SESSION_TRACK_ID_OFFSET = 7  # int8

# ── Track ID mapping ───────────────────────────────────────────────
# Name your reference files like:  refs/<track_name>.json
# e.g. refs/bahrain.json, refs/monaco.json
TRACK_NAMES: dict[int, str] = {
    0: "melbourne",
    1: "paul_ricard",
    2: "shanghai",
    3: "bahrain",
    4: "barcelona",
    5: "monaco",
    6: "montreal",
    7: "silverstone",
    8: "hockenheim",
    9: "hungaroring",
    10: "spa",
    11: "monza",
    12: "singapore",
    13: "suzuka",
    14: "abu_dhabi",
    15: "austin",
    16: "interlagos",
    17: "red_bull_ring",
    18: "sochi",
    19: "mexico",
    20: "baku",
    21: "bahrain_short",
    22: "silverstone_short",
    23: "austin_short",
    24: "suzuka_short",
    25: "hanoi",
    26: "zandvoort",
    27: "imola",
    28: "portimao",
    29: "jeddah",
    30: "miami",
    31: "las_vegas",
    32: "losail",
    33: "lusail",
}

REFS_DIR = "refs"


@dataclass
class TelemetryFrame:
    brake: float = 0.0
    throttle: float = 0.0
    gear: int = 0
    lap_distance: float = 0.0
    lap_number: int = 0
    session_time: float = 0.0


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
        self._current_track_id: int = -1

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
                _packet_format, _game_year, _game_major, _game_minor,
                _packet_version, packet_id, _session_uid, session_time,
                _frame_identifier, _overall_frame_identifier,
                player_car_index, _secondary_player_car_index,
            ) = struct.unpack_from(HEADER_FORMAT, data, 0)
        except struct.error:
            return None

        # ── Session packet → extract track ID ──
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
        )

        if packet_id == PACKET_CAR_TELEMETRY:
            b, t, g = self._extract_inputs(data, player_car_index)
            if b is not None:
                frame.brake = b
            if t is not None:
                frame.throttle = t
            if g is not None:
                frame.gear = g

        elif packet_id == PACKET_LAP_DATA:
            if not self._lap_offset_found and player_car_index == 0:
                self._probe_lap_offset(data)
            lap_distance, lap_number = self._extract_lap_data(data, player_car_index)
            if self._debug_count <= 20:
                print(f"[LAP] dist={lap_distance}, lap={lap_number}")
            if lap_distance is not None:
                frame.lap_distance = lap_distance
            if lap_number is not None:
                frame.lap_number = lap_number
        else:
            return None

        self._latest_frame = frame
        return frame

    def _extract_track_id(self, data: bytes) -> None:
        offset = HEADER_SIZE + SESSION_TRACK_ID_OFFSET
        if offset + 1 > len(data):
            return
        try:
            (track_id,) = struct.unpack_from("<b", data, offset)  # int8
        except struct.error:
            return
        if track_id != self._current_track_id and track_id >= 0:
            self._current_track_id = track_id
            name = TRACK_NAMES.get(track_id, f"unknown_{track_id}")
            print(f"[TRACK] Detected track_id={track_id} → {name}")
            self.track_changed.emit(track_id, name)

    @staticmethod
    def _extract_inputs(data: bytes, player_car_index: int) -> tuple[float | None, float | None, int | None]:
        base = HEADER_SIZE + (player_car_index * CAR_TELEMETRY_DATA_SIZE)
        off_b = base + CAR_TELEMETRY_BRAKE_OFFSET
        off_t = base + CAR_TELEMETRY_THROTTLE_OFFSET
        off_g = base + CAR_TELEMETRY_GEAR_OFFSET
        if off_g + 1 > len(data):
            return None, None, None
        try:
            (b,) = struct.unpack_from("<f", data, off_b)
            (t,) = struct.unpack_from("<f", data, off_t)
            (g,) = struct.unpack_from("<b", data, off_g)
            return float(b), float(t), int(g)
        except struct.error:
            return None, None, None

    def _probe_lap_offset(self, data: bytes) -> None:
        global LAP_DISTANCE_OFFSET
        base = HEADER_SIZE
        best_off = LAP_DISTANCE_OFFSET
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
            LAP_DISTANCE_OFFSET = best_off
            print(f"[PROBE] Using LAP_DISTANCE_OFFSET={best_off} (val={best_val:.1f}m)")
        self._lap_offset_found = True

    @staticmethod
    def _extract_lap_data(data: bytes, car_idx: int) -> tuple[float | None, int | None]:
        base = HEADER_SIZE + (car_idx * LAP_DATA_SIZE)
        d_off = base + LAP_DISTANCE_OFFSET
        l_off = base + CURRENT_LAP_NUM_OFFSET
        if d_off + 4 > len(data) or l_off + 1 > len(data):
            return None, None
        try:
            (dist,) = struct.unpack_from("<f", data, d_off)
            (lap,) = struct.unpack_from("<B", data, l_off)
        except struct.error:
            return None, None
        return max(0.0, dist), int(lap)


# ── Graph widget ────────────────────────────────────────────────────
class InputGraphWidget(QWidget):
    """Track-distance input graph (brake/throttle) with ideal overlay."""

    MIN_WINDOW = 200.0
    MAX_WINDOW = 3000.0
    DEFAULT_WINDOW = 800.0

    def __init__(
        self,
        color_base: tuple[int, int, int],
        color_live: tuple[int, int, int],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setMinimumHeight(80)
        self._window_m = self.DEFAULT_WINDOW
        
        # Colors (R, G, B)
        self._color_base = color_base
        self._color_live = color_live

        self._live_samples: list[tuple[float, float]] = []
        
        # historical sets: label -> points
        self._historical_datasets: dict[str, list[tuple[float, float]]] = {}
        
        self._current_distance: float = 0.0
        self._last_lap_number: int = -1

        self._ref_samples: list[tuple[float, float]] = []
        self._track_length: float = 0.0
        self._ref_offset: float = 0.0  # Shift reference data (metres)

    @property
    def window_metres(self) -> float:
        return self._window_m

    @window_metres.setter
    def window_metres(self, val: float) -> None:
        self._window_m = max(self.MIN_WINDOW, min(self.MAX_WINDOW, val))

    def set_reference(self, samples: list[tuple[float, float]], length: float) -> None:
        self._ref_samples = sorted(samples, key=lambda x: x[0])
        self._track_length = length

    def clear_reference(self) -> None:
        self._ref_samples = []
        self._track_length = 0.0
        self.update()

    def set_historical(self, label: str, samples: list[tuple[float, float]]) -> None:
        self._historical_datasets[label] = sorted(samples, key=lambda x: x[0])
        
    def clear_historical(self, label: str | None = None) -> None:
        if label:
            self._historical_datasets.pop(label, None)
        else:
            self._historical_datasets.clear()

    def add_sample(self, dist: float, brake: float, lap: int) -> None:
        # Reset on lap change OR when distance drops significantly (finish line)
        if lap != self._last_lap_number:
            self._live_samples.clear()
            self._last_lap_number = lap
        elif self._live_samples and dist < self._current_distance - 500:
            self._live_samples.clear()

        self._current_distance = dist
        self._live_samples.append((dist, brake))

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        factor = 0.9 if delta > 0 else 1.1
        self.window_metres = self._window_m * factor
        event.accept()

    @staticmethod
    def _build_path(
        samples: list[tuple[float, float]],
        gx: float, gy: float, gw: float, gh: float,
        vs: float, ve: float,
    ) -> tuple[QPainterPath, QPainterPath] | None:
        span = ve - vs
        if span <= 0:
            return None
        path = QPainterPath()
        fill = QPainterPath()
        first = True
        for d, b in samples:
            if d < vs or d > ve:
                continue
            x = gx + ((d - vs) / span) * gw
            y = gy + gh - b * gh
            pt = QPointF(x, y)
            if first:
                path.moveTo(pt)
                fill.moveTo(QPointF(x, gy + gh))
                fill.lineTo(pt)
                first = False
            else:
                path.lineTo(pt)
                fill.lineTo(pt)
        if first:
            return None
        fill.lineTo(QPointF(path.currentPosition().x(), gy + gh))
        fill.closeSubpath()
        return path, fill

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w, h = self.width(), self.height()
        ml, mr, mt, mb = 38, 10, 8, 22
        gx, gy = ml, mt
        gw = w - ml - mr
        gh = h - mt - mb

        if gw < 10 or gh < 10:
            p.end()
            return

        half = self._window_m / 2
        vs = self._current_distance - half
        ve = self._current_distance + half

        # Background
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(18, 18, 22, 200))
        p.drawRoundedRect(QRectF(gx - 2, gy - 2, gw + 4, gh + 4), 6, 6)

        # Clip to graph area to prevent drawing outside bounds
        p.setClipRect(QRectF(gx, gy, gw, gh))

        # Y grid
        p.setFont(QFont("Segoe UI", 7))
        for pct in (0, 25, 50, 75, 100):
            y = gy + gh - (pct / 100.0) * gh
            p.setPen(QPen(QColor(255, 255, 255, 30), 1))
            p.drawLine(QPointF(gx, y), QPointF(gx + gw, y))

        # X grid — dynamic step to avoid label overlap
        span = ve - vs
        if span > 0:
            # Pick a step so labels are ≥60px apart
            min_px = 60
            raw_step = (span * min_px) / max(gw, 1)
            # Snap to a nice round step: 50, 100, 200, 500, 1000...
            for nice in (50, 100, 200, 500, 1000, 2000):
                if nice >= raw_step:
                    step = nice
                    break
            else:
                step = 2000
            first_mark = int(vs / step) * step
            for mark in range(first_mark, int(ve) + step, step):
                if mark < vs or mark > ve:
                    continue
                x = gx + ((mark - vs) / span) * gw
                p.setPen(QPen(QColor(255, 255, 255, 20), 1))
                p.drawLine(QPointF(x, gy), QPointF(x, gy + gh))

        # Current pos line
        cx = gx + gw / 2
        p.setPen(QPen(QColor(255, 255, 255, 60), 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(cx, gy), QPointF(cx, gy + gh))

        # Reference input (grey) — apply offset
        if self._ref_samples:
            shifted = [(d + self._ref_offset, b) for d, b in self._ref_samples]
            r = self._build_path(shifted, gx, gy, gw, gh, vs, ve)
            if r:
                rp, rf = r
                g = QLinearGradient(0, gy, 0, gy + gh)
                g.setColorAt(0.0, QColor(160, 160, 160, 80))
                g.setColorAt(1.0, QColor(160, 160, 160, 10))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(g)
                p.drawPath(rf)
                p.setPen(QPen(QColor(180, 180, 180, 180), 1.5))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(rp)

        # Historical datasets (muted base color)
        for ds in self._historical_datasets.values():
            if len(ds) >= 2:
                r = self._build_path(ds, gx, gy, gw, gh, vs, ve)
                if r:
                    hp, hf = r
                    g = QLinearGradient(0, gy, 0, gy + gh)
                    br, bg, bb = self._color_base
                    g.setColorAt(0.0, QColor(br, bg, bb, 50))
                    g.setColorAt(1.0, QColor(br, bg, bb, 5))
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(g)
                    p.drawPath(hf)
                    p.setPen(QPen(QColor(br, bg, bb, 140), 1.5, Qt.PenStyle.DashLine))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawPath(hp)

        # Live input (bright colored)
        if len(self._live_samples) >= 2:
            r = self._build_path(self._live_samples, gx, gy, gw, gh, vs, ve)
            if r:
                lp, lf = r
                g = QLinearGradient(0, gy, 0, gy + gh)
                br, bg, bb = self._color_base
                lr, lg, lb = self._color_live
                g.setColorAt(0.0, QColor(br, bg, bb, 160))
                g.setColorAt(1.0, QColor(br, bg, bb, 20))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(g)
                p.drawPath(lf)
                p.setPen(QPen(QColor(lr, lg, lb, 240), 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(lp)

        # Remove clip for labels (drawn outside graph area)
        p.setClipping(False)

        # Y labels
        for pct in (0, 25, 50, 75, 100):
            y = gy + gh - (pct / 100.0) * gh
            p.setPen(QColor(180, 180, 180, 180))
            p.drawText(
                QRectF(0, y - 7, ml - 4, 14),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{pct}%",
            )

        # X labels (same dynamic step as grid)
        if span > 0:
            min_px = 60
            raw_step = (span * min_px) / max(gw, 1)
            nice_steps = (50, 100, 200, 500, 1000, 2000)
            for nice in nice_steps:
                if nice >= raw_step:
                    step = nice
                    break
            else:
                step = nice_steps[-1]
            first_mark = int(vs / step) * step
            for mark in range(first_mark, int(ve) + step, step):
                if mark < vs or mark > ve or mark < 0:
                    continue
                x = gx + ((mark - vs) / span) * gw
                p.setPen(QColor(150, 150, 150, 160))
                label = f"{mark}m"
                p.drawText(
                    QRectF(x - 24, gy + gh + 3, 48, 16),
                    Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                    label,
                )

        p.end()


class ReviewWindow(QWidget):
    def __init__(self, historical_laps: list[dict], ref_brake: list, ref_throttle: list):
        super().__init__()
        self.setWindowTitle("Lap Review Data")
        self.resize(1000, 600)
        
        self.historical_laps = historical_laps
        self.ref_brake = ref_brake
        self.ref_throttle = ref_throttle
        
        self._build_ui()
        self.update_laps(historical_laps)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        
        # Top toolbar
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Select Lap:"))
        
        self.lap_combo = QComboBox()
        self.lap_combo.currentIndexChanged.connect(self._on_lap_selected)
        toolbar.addWidget(self.lap_combo)
        
        toolbar.addSpacing(20)
        
        self.cb_brake = QCheckBox("Brake")
        self.cb_brake.setChecked(True)
        self.cb_brake.toggled.connect(self._update_visibility)
        toolbar.addWidget(self.cb_brake)
        
        self.cb_throttle = QCheckBox("Throttle")
        self.cb_throttle.setChecked(True)
        self.cb_throttle.toggled.connect(self._update_visibility)
        toolbar.addWidget(self.cb_throttle)
        
        self.cb_gear = QCheckBox("Gear")
        self.cb_gear.setChecked(True)
        self.cb_gear.toggled.connect(self._update_visibility)
        toolbar.addWidget(self.cb_gear)
        
        toolbar.addStretch(1)
        root.addLayout(toolbar)
        
        # Scroll Area for panning
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        root.addWidget(self.scroll, stretch=1)
        
        self.graphs_container = QWidget()
        self.graphs_layout = QVBoxLayout(self.graphs_container)
        
        # We make the container much wider than the screen to allow scrolling
        self.graphs_container.setMinimumWidth(3000)
        
        self.brake_graph = InputGraphWidget(
            color_base=(232, 42, 42),
            color_live=(232, 60, 60),
            parent=self.graphs_container
        )
        self.brake_graph.setMinimumHeight(150)
        if self.ref_brake:
            self.brake_graph.set_reference(self.ref_brake, 0)
        self.graphs_layout.addWidget(self.brake_graph)
        
        self.throttle_graph = InputGraphWidget(
            color_base=(42, 232, 80),
            color_live=(60, 232, 100),
            parent=self.graphs_container
        )
        self.throttle_graph.setMinimumHeight(150)
        if self.ref_throttle:
            self.throttle_graph.set_reference(self.ref_throttle, 0)
        self.graphs_layout.addWidget(self.throttle_graph)
        
        self.gear_graph = InputGraphWidget(
            color_base=(232, 180, 42),
            color_live=(255, 210, 60),
            parent=self.graphs_container
        )
        self.gear_graph.setMinimumHeight(100)
        self.graphs_layout.addWidget(self.gear_graph)
        
        self.scroll.setWidget(self.graphs_container)

    def update_laps(self, historical_laps: list[dict]) -> None:
        self.historical_laps = historical_laps
        idx = self.lap_combo.currentIndex()
        self.lap_combo.blockSignals(True)
        self.lap_combo.clear()
        
        if not self.historical_laps:
            self.lap_combo.addItem("No laps recorded")
        else:
            for i in range(len(self.historical_laps)):
                self.lap_combo.addItem(f"Lap {i+1}")
            if len(self.historical_laps) > 1:
                self.lap_combo.addItem("Average Lap")
                
        if idx >= 0 and idx < self.lap_combo.count():
            self.lap_combo.setCurrentIndex(idx)
        else:
            self.lap_combo.setCurrentIndex(0)
            
        self.lap_combo.blockSignals(False)
        self._on_lap_selected(self.lap_combo.currentIndex())
        
    def _update_visibility(self) -> None:
        self.brake_graph.setVisible(self.cb_brake.isChecked())
        self.throttle_graph.setVisible(self.cb_throttle.isChecked())
        self.gear_graph.setVisible(self.cb_gear.isChecked())

    def _on_lap_selected(self, index: int) -> None:
        if index < 0 or not self.historical_laps:
            return
            
        text = self.lap_combo.itemText(index)
        
        self.brake_graph.clear_historical()
        self.throttle_graph.clear_historical()
        self.gear_graph.clear_historical()
        self.brake_graph._live_samples.clear()
        self.throttle_graph._live_samples.clear()
        self.gear_graph._live_samples.clear()
        
        b_data, t_data, g_data = [], [], []
        
        if text.startswith("Lap "):
            lap_idx = int(text.split(" ")[1]) - 1
            if 0 <= lap_idx < len(self.historical_laps):
                lap = self.historical_laps[lap_idx]
                b_data = lap.get("brake", [])
                t_data = lap.get("throttle", [])
                g_data = lap.get("gear", [])
                
        elif text == "Average Lap":
            # Simplified average just for ReviewWindow
            bucket_size = 20.0
            buckets: dict[int, dict[str, list[float]]] = {}
            for lap in self.historical_laps:
                for dist, brake in lap.get("brake", []):
                    b_idx = int(dist / bucket_size)
                    buckets.setdefault(b_idx, {"b": [], "t": [], "g": []})["b"].append(brake)
                for dist, throttle in lap.get("throttle", []):
                    b_idx = int(dist / bucket_size)
                    buckets.setdefault(b_idx, {"b": [], "t": [], "g": []})["t"].append(throttle)
                for dist, gear in lap.get("gear", []):
                    b_idx = int(dist / bucket_size)
                    buckets.setdefault(b_idx, {"b": [], "t": [], "g": []})["g"].append(gear)
                    
            for b_idx in sorted(buckets.keys()):
                dist = b_idx * bucket_size
                b_list = buckets[b_idx]["b"]
                if b_list: b_data.append((dist, sum(b_list)/len(b_list)))
                t_list = buckets[b_idx]["t"]
                if t_list: t_data.append((dist, sum(t_list)/len(t_list)))
                g_list = buckets[b_idx]["g"]
                if g_list: g_data.append((dist, sum(g_list)/len(g_list)))
        
        # Plot as "live" samples so they show up brightly
        self.brake_graph._live_samples = b_data
        self.throttle_graph._live_samples = t_data
        self.gear_graph._live_samples = g_data
        
        self.brake_graph.update()
        self.throttle_graph.update()
        self.gear_graph.update()


# ── Resize edge size ────────────────────────────────────────────────
_RESIZE_MARGIN = 8


class OverlayWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("F1 25 Brake Overlay")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMinimumSize(320, 200)
        self.resize(520, 320)
        self.setMouseTracking(True)  # Enable hover cursor changes

        # Position in top-right corner
        screen = QApplication.primaryScreen()
        if screen:
            sg = screen.availableGeometry()
            self.move(sg.right() - self.width() - 20, sg.top() + 20)

        self.telemetry = TelemetryListener(port=UDP_PORT)
        self.telemetry.telemetry_received.connect(self.on_telemetry)
        self.telemetry.connection_state_changed.connect(self.on_connection_state)
        self.telemetry.track_changed.connect(self.on_track_changed)

        self.drag_origin: QPoint | None = None
        self._resize_edge: str = ""
        self._resize_origin: QPoint | None = None
        self._resize_geom: QRectF | None = None

        self._ui_visible = True
        self._bg_opacity = 255
        self.setWindowOpacity(self._bg_opacity / 255.0)

        self.current_brake = 0.0
        self.current_throttle = 0.0
        self.current_lap_distance = 0.0
        self.current_lap_number = 0
        self.current_track_name = ""

        self.recording = False
        self.record_start_lap_number: int | None = None
        self.reference_samples: list[dict[str, float]] = []
        self.reference_track_length = 0.0

        # Archive for lap history
        self.historical_laps: list[dict[str, list[tuple[float, float]]]] = []
        self._current_lap: int = -1
        self._current_distance: float = 0.0

        self.ui_timer = QTimer(self)
        self.ui_timer.setInterval(16)
        self.ui_timer.timeout.connect(self.update)
        self.ui_timer.start()

        self._build_ui()
        self.telemetry.start()

        # Auto-load default reference file
        self._try_load_default()

    def _try_load_default(self) -> None:
        """No fallback — just show status."""
        self.status_label.setText("No reference lap loaded. Tracking live braking only.")

    def _try_load_track_ref(self, track_name: str) -> None:
        """Try to load refs/<track_name>.json."""
        p = Path(__file__).parent / REFS_DIR / f"{track_name}.json"
        if p.exists():
            self._load_reference_file(str(p))
        else:
            self.brake_graph.clear_reference()
            self.throttle_graph.clear_reference()
            self.status_label.setText(
                f"Track: {track_name} — no ref file found (refs/{track_name}.json). "
                f"Tracking live inputs only."
            )

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(8)

        self.connection_label = QLabel("UDP: waiting")
        self.connection_label.setStyleSheet("color: #d7d7d7; font-size: 13px;")
        top.addWidget(self.connection_label)

        top.addStretch(1)

        self.record_button = QPushButton("Record Ref Lap")
        self.record_button.clicked.connect(self.toggle_recording)
        top.addWidget(self.record_button)

        self.load_button = QPushButton("Load")
        self.load_button.clicked.connect(self.load_reference)
        top.addWidget(self.load_button)

        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self.save_reference)
        top.addWidget(self.save_button)

        self.top_widget = QWidget()
        self.top_widget.setLayout(top)
        root.addWidget(self.top_widget)

        self.mid_widget = QWidget()
        mid_row = QHBoxLayout(self.mid_widget)
        mid_row.setContentsMargins(0, 0, 0, 0)

        # Labels (Left)
        labels_vbox = QVBoxLayout()
        self.status_label = QLabel("No reference lap loaded.")
        self.status_label.setStyleSheet("color: #b5b5b5; font-size: 12px;")
        labels_vbox.addWidget(self.status_label)
        
        mid_row.addLayout(labels_vbox)
        mid_row.addStretch(1)
        
        # Middle (Review Button)
        self.review_btn = QPushButton("Review Laps")
        self.review_btn.clicked.connect(self._open_review_window)
        mid_row.addWidget(self.review_btn)
        
        mid_row.addStretch(1)
        
        # Toggle Buttons (Right)
        self.toggles_widget = QWidget()
        toggles_layout = QVBoxLayout(self.toggles_widget)
        toggles_layout.setContentsMargins(0, 0, 0, 0)
        
        self.toggle_throttle_btn = QPushButton("Toggle Throttle")
        self.toggle_throttle_btn.clicked.connect(self._toggle_throttle_graph)
        toggles_layout.addWidget(self.toggle_throttle_btn)

        self.toggle_gear_btn = QPushButton("Toggle Gear")
        self.toggle_gear_btn.clicked.connect(self._toggle_gear_graph)
        toggles_layout.addWidget(self.toggle_gear_btn)
        
        mid_row.addWidget(self.toggles_widget)
        root.addWidget(self.mid_widget)

        self.brake_graph = InputGraphWidget(
            color_base=(232, 42, 42),
            color_live=(232, 60, 60),
        )
        root.addWidget(self.brake_graph, stretch=1)
        
        self.throttle_graph = InputGraphWidget(
            color_base=(42, 232, 80),
            color_live=(60, 232, 100),
        )
        self.throttle_graph.hide()
        root.addWidget(self.throttle_graph, stretch=1)

        self.gear_graph = InputGraphWidget(
            color_base=(232, 180, 42),
            color_live=(255, 210, 60),
        )
        self.gear_graph.hide()
        root.addWidget(self.gear_graph, stretch=1)

        # Offset row
        self.offset_widget = QWidget()
        offset_row = QHBoxLayout(self.offset_widget)
        offset_row.setContentsMargins(0, 0, 0, 0)
        offset_row.setSpacing(4)

        self.offset_label = QLabel("Ref offset: 0m")
        self.offset_label.setStyleSheet("color: rgba(255,255,255,120); font-size: 11px;")
        offset_row.addWidget(self.offset_label)

        offset_row.addStretch(1)

        # Opacity Slider
        op_label = QLabel("Opacity:")
        op_label.setStyleSheet("color: rgba(255,255,255,120); font-size: 11px;")
        offset_row.addWidget(op_label)
        
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setMinimum(0)
        self.opacity_slider.setMaximum(255)
        self.opacity_slider.setValue(self._bg_opacity)
        self.opacity_slider.setFixedWidth(80)
        self.opacity_slider.valueChanged.connect(self._update_opacity)
        offset_row.addWidget(self.opacity_slider)

        offset_row.addSpacing(10)

        btn_minus = QPushButton("◀ -1m")
        btn_minus.setFixedWidth(60)
        btn_minus.clicked.connect(lambda: self._adjust_offset(-1))
        offset_row.addWidget(btn_minus)

        btn_plus = QPushButton("+1m ▶")
        btn_plus.setFixedWidth(60)
        btn_plus.clicked.connect(lambda: self._adjust_offset(1))
        offset_row.addWidget(btn_plus)

        hint = QLabel("Scroll to zoom")
        hint.setStyleSheet("color: rgba(255,255,255,60); font-size: 10px;")
        offset_row.addWidget(hint)

        root.addWidget(self.offset_widget)

        self.setStyleSheet(
            """
            QWidget { background: transparent; }
            QPushButton {
                background: rgba(22,22,22,200);
                color: #ededed;
                border: 1px solid rgba(255,255,255,60);
                border-radius: 6px;
                padding: 5px 8px;
                font-size: 12px;
            }
            QPushButton:hover { border: 1px solid rgba(255,255,255,110); }
            """
        )

    def _toggle_throttle_graph(self) -> None:
        if self.throttle_graph.isVisible():
            self.throttle_graph.hide()
        else:
            self.throttle_graph.show()

    # ── Offset adjustment ────────────────────────────────────────────

    def _adjust_offset(self, delta: float) -> None:
        self.brake_graph._ref_offset += delta
        self.throttle_graph._ref_offset += delta
        off = self.brake_graph._ref_offset
        self.offset_label.setText(f"Ref offset: {off:+.0f}m")

    def _update_opacity(self, value: int) -> None:
        self._bg_opacity = value
        self.setWindowOpacity(value / 255.0)

    def _on_lap_select(self, index: int) -> None:
        self.brake_graph.clear_historical()
        self.throttle_graph.clear_historical()
        
        if index <= 0:  # Live Only
            self.brake_graph.update()
            self.throttle_graph.update()
            return

        text = self.lap_dropdown.itemText(index)
        
        if text.startswith("Lap "):
            try:
                lap_idx = int(text.split(" ")[1]) - 1
                if 0 <= lap_idx < len(self.historical_laps):
                    lap_data = self.historical_laps[lap_idx]
                    self.brake_graph.set_historical("lap", lap_data["brake"])
                    self.throttle_graph.set_historical("lap", lap_data["throttle"])
            except (ValueError, IndexError):
                pass
                
        elif text == "All Laps":
            for i, lap_data in enumerate(self.historical_laps):
                self.brake_graph.set_historical(f"lap_{i}", lap_data["brake"])
                self.throttle_graph.set_historical(f"lap_{i}", lap_data["throttle"])
                
        elif text == "Average Lap":
            avg_b, avg_t = self._calculate_average_lap()
            self.brake_graph.set_historical("avg", avg_b)
            self.throttle_graph.set_historical("avg", avg_t)

        self.brake_graph.update()
        self.throttle_graph.update()

    def _calculate_average_lap(self) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
        if not self.historical_laps:
            return [], []
            
        # We bucket the distance into 20m increments to average the inputs
        bucket_size = 20.0
        tl = self.brake_graph._track_length or 5000.0
        buckets: dict[int, dict[str, list[float]]] = {}
        
        for lap in self.historical_laps:
            for dist, brake in lap["brake"]:
                b_idx = int(dist / bucket_size)
                if b_idx not in buckets:
                    buckets[b_idx] = {"brake": [], "throttle": []}
                buckets[b_idx]["brake"].append(brake)
            for dist, throttle in lap["throttle"]:
                b_idx = int(dist / bucket_size)
                if b_idx not in buckets:
                    buckets[b_idx] = {"brake": [], "throttle": []}
                buckets[b_idx]["throttle"].append(throttle)
                
        avg_b, avg_t = [], []
        for b_idx in sorted(buckets.keys()):
            dist = b_idx * bucket_size
            b_list = buckets[b_idx]["brake"]
            t_list = buckets[b_idx]["throttle"]
            if b_list:
                avg_b.append((dist, sum(b_list) / len(b_list)))
            if t_list:
                avg_t.append((dist, sum(t_list) / len(t_list)))
                
        return avg_b, avg_t

    def _toggle_gear_graph(self) -> None:
        if self.gear_graph.isVisible():
            self.gear_graph.hide()
        else:
            self.gear_graph.show()

    def _open_review_window(self) -> None:
        if not hasattr(self, "review_window") or self.review_window is None:
            self.review_window = ReviewWindow(self.historical_laps, self.brake_graph._ref_samples, self.throttle_graph._ref_samples)
        else:
            self.review_window.update_laps(self.historical_laps)
        self.review_window.show()
        self.review_window.raise_()

    # ── Slots ───────────────────────────────────────────────────────

    def on_connection_state(self, connected: bool) -> None:
        self.connection_label.setText("UDP: connected" if connected else "UDP: waiting")
        self.connection_label.setStyleSheet(
            "color: #78ff8a; font-size: 13px;" if connected else "color: #d7d7d7; font-size: 13px;"
        )

    def on_track_changed(self, track_id: int, track_name: str) -> None:
        self.current_track_name = track_name
        self._try_load_track_ref(track_name)

    def on_telemetry(self, frame: TelemetryFrame) -> None:
        # Detect lap finish and save to history (if distance drops significantly)
        dist_drop = self._current_distance - frame.lap_distance
        if dist_drop > 1000.0:  # Crossed the finish line
            if len(self.brake_graph._live_samples) > 50:
                self.historical_laps.append({
                    "brake": list(self.brake_graph._live_samples),
                    "throttle": list(self.throttle_graph._live_samples),
                    "gear": list(self.gear_graph._live_samples),
                })
                self.status_label.setText(f"Archived lap {len(self.historical_laps)} (Live tracking lap {frame.lap_number})")

        self._current_lap = frame.lap_number
        self._current_distance = frame.lap_distance
        
        self.current_brake = frame.brake
        self.current_throttle = frame.throttle
        self.current_lap_distance = frame.lap_distance
        self.current_lap_number = frame.lap_number
        
        self.brake_graph.add_sample(frame.lap_distance, frame.brake, frame.lap_number)
        self.throttle_graph.add_sample(frame.lap_distance, frame.throttle, frame.lap_number)
        self.gear_graph.add_sample(frame.lap_distance, max(0.0, frame.gear) / 8.0, frame.lap_number)

        # Sync windows
        if self.throttle_graph.isVisible():
            self.throttle_graph.window_metres = self.brake_graph.window_metres

        if self.recording:
            self._record_sample(frame)

    # ── Recording ───────────────────────────────────────────────────

    def _record_sample(self, frame: TelemetryFrame) -> None:
        if self.record_start_lap_number is None:
            self.record_start_lap_number = frame.lap_number
        if (
            frame.lap_number != self.record_start_lap_number
            and len(self.reference_samples) > 100
            and frame.lap_distance < 60.0
        ):
            self.finish_recording(auto_finished=True)
            return
        self.reference_samples.append(
            {
                "lap_distance": float(frame.lap_distance),
                "brake": float(frame.brake),
                "throttle": float(frame.throttle)
            }
        )

    def toggle_recording(self) -> None:
        if self.recording:
            self.finish_recording(auto_finished=False)
            return
        self.recording = True
        self.record_start_lap_number = self.current_lap_number
        self.reference_samples = []
        self.record_button.setText("Stop Recording")
        self.status_label.setText("Recording reference lap...")

    def finish_recording(self, auto_finished: bool) -> None:
        self.recording = False
        self.record_button.setText("Record Ref Lap")
        if not self.reference_samples:
            self.status_label.setText("Recording finished — no samples.")
            return
        self.reference_track_length = max(s["lap_distance"] for s in self.reference_samples)
        b_graph = [(s["lap_distance"], s.get("brake", 0.0)) for s in self.reference_samples]
        t_graph = [(s["lap_distance"], s.get("throttle", 0.0)) for s in self.reference_samples]
        
        self.brake_graph.set_reference(b_graph, self.reference_track_length)
        self.throttle_graph.set_reference(t_graph, self.reference_track_length)
        tag = "Auto" if auto_finished else "Saved"
        self.status_label.setText(
            f"{tag} ref lap: {len(self.reference_samples)} pts, {self.reference_track_length:.0f}m."
        )

    # ── Save / Load ─────────────────────────────────────────────────

    def save_reference(self) -> None:
        if not self.reference_samples:
            self.status_label.setText("Nothing to save.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save", str(Path.cwd() / "reference_lap.json"), "JSON (*.json)")
        if not path:
            return
        payload = {"version": 2, "track_length": self.reference_track_length,
                   "samples": self.reference_samples}
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except OSError as e:
            self.status_label.setText(f"Save failed: {e}")
            return
        self.status_label.setText(f"Saved: {Path(path).name}")

    def load_reference(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load", str(Path.cwd()), "JSON (*.json)")
        if path:
            self._load_reference_file(path)

    def _load_reference_file(self, file_path: str) -> None:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            self.status_label.setText(f"Load failed: {e}")
            return

        pts: list[tuple[float, float]] = []
        t_pts: list[tuple[float, float]] = []
        tl = 0.0

        # External format:  data.distance[] + data.brake[] + data.throttle[] (0-100)
        if "data" in payload and isinstance(payload["data"], dict):
            data = payload["data"]
            dists = data.get("distance", [])
            brakes = data.get("brake", [])
            throttles = data.get("throttle", [])
            tl = float(data.get("maxDistance", 0.0))
            
            nb = min(len(dists), len(brakes))
            for i in range(nb):
                pts.append((float(dists[i]), max(0.0, min(1.0, float(brakes[i]) / 100.0))))
                
            nt = min(len(dists), len(throttles))
            for i in range(nt):
                t_pts.append((float(dists[i]), max(0.0, min(1.0, float(throttles[i]) / 100.0))))
                
            if tl <= 0 and dists:
                tl = float(dists[-1])

        # Internal format: version 2
        elif "samples" in payload:
            tl = float(payload.get("track_length", 0.0))
            for s in payload.get("samples", []):
                if isinstance(s, dict):
                    pts.append((float(s.get("lap_distance", 0)), float(s.get("brake", 0))))
                    t_pts.append((float(s.get("lap_distance", 0)), float(s.get("throttle", 0))))
        else:
            self.status_label.setText("Unknown file format.")
            return

        if not pts:
            self.status_label.setText("File has no data.")
            return

        self.brake_graph.set_reference(pts, tl)
        if t_pts:
            self.throttle_graph.set_reference(t_pts, tl)
            
        self.status_label.setText(
            f"Loaded {len(pts)} pts from {Path(file_path).name} ({tl:.0f}m)."
        )

    # ── Painting ────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(10, 10, 12, 170))
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 16, 16)

    # ── Resize edges ────────────────────────────────────────────────

    def _update_ui_visibility(self, visible: bool) -> None:
        if self._ui_visible == visible:
            return
        self._ui_visible = visible
        if visible:
            self.top_widget.show()
            self.offset_widget.show()
            self.mid_widget.show()
        else:
            self.top_widget.hide()
            self.offset_widget.hide()
            self.mid_widget.hide()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        if event.type() == QEvent.Type.ActivationChange:
            self._update_ui_visibility(self.isActiveWindow())
        super().changeEvent(event)

    def _edge_at(self, pos: QPoint) -> str:
        m = _RESIZE_MARGIN
        x, y, w, h = pos.x(), pos.y(), self.width(), self.height()
        edge = ""
        if y < m:
            edge += "t"
        elif y > h - m:
            edge += "b"
        if x < m:
            edge += "l"
        elif x > w - m:
            edge += "r"
        return edge

    def _cursor_for_edge(self, edge: str) -> Qt.CursorShape:
        mapping = {
            "tl": Qt.CursorShape.SizeFDiagCursor,
            "br": Qt.CursorShape.SizeFDiagCursor,
            "tr": Qt.CursorShape.SizeBDiagCursor,
            "bl": Qt.CursorShape.SizeBDiagCursor,
            "t": Qt.CursorShape.SizeVerCursor,
            "b": Qt.CursorShape.SizeVerCursor,
            "l": Qt.CursorShape.SizeHorCursor,
            "r": Qt.CursorShape.SizeHorCursor,
        }
        return mapping.get(edge, Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position().toPoint()
        edge = self._edge_at(pos)
        if edge:
            self._resize_edge = edge
            self._resize_origin = event.globalPosition().toPoint()
            self._resize_geom = QRectF(self.geometry())
        else:
            self.drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._resize_origin is not None and self._resize_geom is not None:
            delta = event.globalPosition().toPoint() - self._resize_origin
            g = QRectF(self._resize_geom)
            if "r" in self._resize_edge:
                g.setRight(g.right() + delta.x())
            if "b" in self._resize_edge:
                g.setBottom(g.bottom() + delta.y())
            if "l" in self._resize_edge:
                g.setLeft(g.left() + delta.x())
            if "t" in self._resize_edge:
                g.setTop(g.top() + delta.y())
            r = g.toRect()
            if r.width() >= self.minimumWidth() and r.height() >= self.minimumHeight():
                self.setGeometry(r)
            event.accept()
        elif self.drag_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_origin)
            event.accept()
        else:
            edge = self._edge_at(event.position().toPoint())
            if edge:
                self.setCursor(QCursor(self._cursor_for_edge(edge)))
            else:
                self.unsetCursor()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_origin = None
            self._resize_origin = None
            self._resize_edge = ""
            self._resize_geom = None
            event.accept()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.telemetry.stop()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    overlay = OverlayWindow()
    overlay.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
