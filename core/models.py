from dataclasses import dataclass
import struct

# F1 UDP constants
PACKET_MOTION_DATA = 0
PACKET_SESSION = 1
PACKET_LAP_DATA = 2
PACKET_CAR_TELEMETRY = 6
PACKET_CAR_DAMAGE = 10

UDP_PORT = 20777
HEADER_FORMAT = "<HBBBBBQfIIBB"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)  # 29 bytes

# Per-car struct sizes (from actual F1 25 packets)
MOTION_DATA_SIZE = 60
LAP_DATA_SIZE = 57
CAR_TELEMETRY_DATA_SIZE = 60

# Offsets within per-car struct
DEFAULT_LAP_DISTANCE_OFFSET = 20
CURRENT_LAP_NUM_OFFSET = 39
CAR_TELEMETRY_THROTTLE_OFFSET = 2
CAR_TELEMETRY_BRAKE_OFFSET = 10
# Per Codemasters UDP layout the gear byte is near the start of CarTelemetryData.
CAR_TELEMETRY_GEAR_OFFSET = 15
CAR_TELEMETRY_GEAR_FALLBACK_OFFSETS = (33,)
# CarDamageData starts with tyresWear[4] floats (order: RL, RR, FL, FR)
CAR_DAMAGE_TYRE_WEAR_OFFSET = 0
CAR_DAMAGE_TYRE_WEAR_VALUES = 4
# F1 25 layout with tyreBlisters and extra fault bytes is 45 bytes per car.
CAR_DAMAGE_DATA_SIZE_FALLBACK = 45

# Session packet: m_trackId offset within session data (after header)
SESSION_TRACK_ID_OFFSET = 7

# Shared constant used in lap averaging views
LAP_AVERAGING_BUCKET_METRES = 20.0

# Track ID mapping
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
    source_packet_id: int = -1
    # Radar Data
    player_pos: tuple[float, float, float] = (0.0, 0.0, 0.0)
    player_forward: tuple[float, float, float] = (0.0, 0.0, 1.0)
    player_right: tuple[float, float, float] = (1.0, 0.0, 0.0)
    opponents: list[tuple[float, float, float]] = None
    # Normalized to FL, FR, RL, RR in percent (0-100)
    tyre_wear: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    # Raw candidates from packet 10 (already normalized to FL, FR, RL, RR)
    tyre_wear_raw: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    tyre_damage_raw: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    tyre_source: str = "none"  # none | wear

    def __post_init__(self):
        if self.opponents is None:
            self.opponents = []
