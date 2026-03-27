import json
import socket
import struct
import time
import random
import argparse
import os
import sys
from pathlib import Path

# Add root directory to python path for module imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# F1 UDP Constants (matching models.py)
PACKET_SESSION = 1
PACKET_LAP_DATA = 2
PACKET_CAR_TELEMETRY = 6
PACKET_CAR_DAMAGE = 10

HEADER_FORMAT = "<HBBBBBQfIIBB"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
LAP_DATA_SIZE = 57
CAR_TELEMETRY_DATA_SIZE = 60
CAR_DAMAGE_DATA_SIZE = 42

# Offsets/Positions within structs (matching models.py)
# Note: we need to pad the packets to the expected size
# Lap Data structure for F1 24/25 is large, but the dashboard only reads specific offsets.
# HIER WIRD NUR GEAR THROTTLE UND BRAKE ÜBERTRAGEN???
# We'll just build a buffer of the required size and pack values at the right spots.

def pack_header(packet_id, session_time, frame_id, player_car_index=0):
    return struct.pack(
        HEADER_FORMAT,
        2024,                # m_packetFormat
        24,                  # m_gameYear
        24,                  # m_gameMajorVersion
        1,                   # m_gameMinorVersion
        1,                   # m_packetVersion
        packet_id,           # m_packetId
        12345678,            # m_sessionUID
        session_time,        # m_sessionTime
        frame_id,            # m_frameIdentifier
        frame_id,            # m_overallFrameIdentifier
        player_car_index,    # m_playerCarIndex
        255                  # m_secondaryPlayerCarIndex
    )

def pack_car_telemetry(throttle, brake, gear, steering, player_car_index=0):
    # Car Telemetry packet contains data for all cars (usually 20 or 22)
    # The dashboard reads data for player_car_index
    num_cars = 22
    packet_body = bytearray(num_cars * CAR_TELEMETRY_DATA_SIZE)
    
    base = player_car_index * CAR_TELEMETRY_DATA_SIZE
    # Offsets from core.models.py:
    # CAR_TELEMETRY_THROTTLE_OFFSET = 2
    # CAR_TELEMETRY_BRAKE_OFFSET = 10
    # CAR_TELEMETRY_GEAR_OFFSET = 15
    struct.pack_into("<f", packet_body, base + 2, throttle)
    struct.pack_into("<f", packet_body, base + 10, brake)
    struct.pack_into("<b", packet_body, base + 15, gear)
    # Steering is not explicitly offset in models.py, but usually it's there.
    # We'll put it at offset 6 (typical for F1 UDP)
    struct.pack_into("<h", packet_body, base + 6, int(steering * 32767))
    
    # Add some padding for the rest of the packet if needed, but the listener just checks offsets.
    # Actual Car Telemetry packet has more fields at the end (MFD, etc)
    # Total size for PacketCarTelemetryData is header + (22 * 60) + buttons + etc.
    # The listener just reads from the data buffer.
    return bytes(packet_body)

def pack_lap_data(lap_distance, lap_number, player_car_index=0):
    num_cars = 22
    packet_body = bytearray(num_cars * LAP_DATA_SIZE)
    
    base = player_car_index * LAP_DATA_SIZE
    # Offsets from core.models.py:
    # DEFAULT_LAP_DISTANCE_OFFSET = 20
    # CURRENT_LAP_NUM_OFFSET = 39
    struct.pack_into("<f", packet_body, base + 20, lap_distance)
    struct.pack_into("<B", packet_body, base + 39, lap_number)
    
    return bytes(packet_body)

def pack_car_damage(tyre_wear, player_car_index=0):
    num_cars = 22
    packet_body = bytearray(num_cars * CAR_DAMAGE_DATA_SIZE)
    base = player_car_index * CAR_DAMAGE_DATA_SIZE
    # Raw F1 order: RL, RR, FL, FR (float, percent)
    struct.pack_into("<ffff", packet_body, base + 0, *tyre_wear)
    return bytes(packet_body)

def pack_session_packet(track_id):
    # PacketSessionData
    packet_body = bytearray(200) # plenty of space
    # SESSION_TRACK_ID_OFFSET = 7
    struct.pack_into("<b", packet_body, 7, track_id)
    return bytes(packet_body)

def main():
    parser = argparse.ArgumentParser(description="Simulate F1 UDP packets from a ref file.")
    parser.add_argument("--ref", type=str, default="refs/bahrain.json", help="Path to the reference JSON file")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Target host")
    parser.add_argument("--port", type=int, default=20777, help="Target UDP port")
    parser.add_argument("--noise", type=float, default=0.02, help="Noise intensity for inputs")
    parser.add_argument("--speed", type=float, default=1.0, help="Simulation speed multiplier")
    parser.add_argument("--laps", type=int, default=0, help="Number of laps to simulate (0 = infinite, runs at max speed)")
    
    args = parser.parse_args()
    
    if not os.path.exists(args.ref):
        print(f"Error: Ref file {args.ref} not found.")
        return

    print(f"Loading {args.ref}...")
    with open(args.ref, "r") as f:
        full_data = json.load(f)
        data = full_data["data"]

    # Map filename to track ID if possible
    track_id = -1
    clean_ref_name = os.path.basename(args.ref).lower().replace(".json", "")
    from core.models import TRACK_NAMES, REFS_DIR
    
    # Try to find track ID by matching filename to TRACK_NAMES
    for tid, name in TRACK_NAMES.items():
        if name in clean_ref_name:
            track_id = tid
            break
    
    if track_id == -1:
        print(f"Warning: Could not determine track ID for '{clean_ref_name}'. Using -1.")
    else:
        print(f"Detected track: {TRACK_NAMES[track_id]} (ID: {track_id})")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    points = len(data["millis"])
    track_length = max(1.0, float(max(data.get("distance", [5000.0]))))
    print(f"Starting simulation with {points} data points. Press Ctrl+C to stop.")
    
    lap_number = 1
    frame_id = 0
    
    # Pre-calculate noise factors for this lap to make it "different" but somewhat consistent
    # instead of just white noise.
    throttle_bias = random.uniform(-args.noise, args.noise)
    brake_bias = random.uniform(-args.noise, args.noise)
    
    # State for smoothing
    curr_throttle = 0.0
    curr_brake = 0.0
    curr_steering = 0.0
    
    # Smoothing factor (alpha for EMA). 1.0 = no smoothing, 0.1 = very smooth
    smoothing = 0.4
    
    try:
        while args.laps == 0 or lap_number <= args.laps:
            start_sim_time = time.time()
            
            # Send session packet at start of lap
            session_pkt = pack_header(PACKET_SESSION, 0, frame_id) + pack_session_packet(track_id)
            sock.sendto(session_pkt, (args.host, args.port))
            
            # Reset smoothing state at start of lap
            curr_throttle = data["throttle"][0] / 100.0
            curr_brake = data["brake"][0] / 100.0
            
            for i in range(points):
                # Timing
                current_millis = data["millis"][i]
                if i < points - 1:
                    next_millis = data["millis"][i+1]
                else:
                    next_millis = current_millis + 100
                
                wait_time = (next_millis - current_millis) / 1000.0 / args.speed
                
                # Data (Scale from 0-100 to 0-1)
                target_throttle = data["throttle"][i] / 100.0
                target_brake = data["brake"][i] / 100.0
                target_steering = data.get("steering", [0.0]*points)[i]
                if abs(target_steering) > 1.0: # Some ref files might use -100 to 100 for steering too
                    target_steering /= 100.0

                gear = data["gear"][i]
                distance = data["distance"][i]
                
                # Improved Noise: Random walk noise is more realistic than white noise
                # We add a small random change to the target
                noise_scale = args.noise
                t_noise = random.uniform(-noise_scale, noise_scale) * 0.2 + throttle_bias
                b_noise = random.uniform(-noise_scale, noise_scale) * 0.2 + brake_bias
                s_noise = random.uniform(-noise_scale, noise_scale) * 0.1
                
                target_throttle = max(0.0, min(1.0, target_throttle + t_noise))
                target_brake = max(0.0, min(1.0, target_brake + b_noise))
                target_steering = max(-1.0, min(1.0, target_steering + s_noise))
                
                # Apply Smoothing (EMA)
                curr_throttle = curr_throttle + smoothing * (target_throttle - curr_throttle)
                curr_brake = curr_brake + smoothing * (target_brake - curr_brake)
                curr_steering = curr_steering + smoothing * (target_steering - curr_steering)

                # Session time for header
                session_time = current_millis / 1000.0

                # Simulate increasing tyre wear over lap distance and lap count.
                lap_factor = max(0.0, float(lap_number - 1))
                base_wear = (lap_factor * 8.5) + ((distance / track_length) * 9.0)
                fl = min(100.0, max(0.0, base_wear + curr_brake * 4.0 + abs(curr_steering) * 2.0))
                fr = min(100.0, max(0.0, base_wear + curr_brake * 3.5 + abs(curr_steering) * 2.0))
                rl = min(100.0, max(0.0, base_wear + (1.0 - curr_throttle) * 1.5))
                rr = min(100.0, max(0.0, base_wear + (1.0 - curr_throttle) * 1.5))
                
                # Pack and send Car Telemetry
                header = pack_header(PACKET_CAR_TELEMETRY, session_time, frame_id)
                telemetry = pack_car_telemetry(curr_throttle, curr_brake, gear, curr_steering)
                sock.sendto(header + telemetry, (args.host, args.port))

                # Send Car Damage (for tyre wear module)
                header = pack_header(PACKET_CAR_DAMAGE, session_time, frame_id)
                # Packet expects RL, RR, FL, FR
                damage_pkt = pack_car_damage((rl, rr, fl, fr))
                sock.sendto(header + damage_pkt, (args.host, args.port))
                
                # Send Lap Data
                header = pack_header(PACKET_LAP_DATA, session_time, frame_id)
                lap_pkt = pack_lap_data(distance, lap_number)
                sock.sendto(header + lap_pkt, (args.host, args.port))
                
                frame_id += 1
                
                if frame_id % 100 == 0:
                    session_pkt = pack_header(PACKET_SESSION, session_time, frame_id) + pack_session_packet(track_id)
                    sock.sendto(session_pkt, (args.host, args.port))
                
                if args.laps == 0:
                    time.sleep(max(0, wait_time))
            
            print(f"Completed lap {lap_number}")
            lap_number += 1
            # Update biases for next lap
            throttle_bias = random.uniform(-args.noise, args.noise)
            brake_bias = random.uniform(-args.noise, args.noise)

    except KeyboardInterrupt:
        print("\nSimulation stopped.")
    finally:
        sock.close()

if __name__ == "__main__":
    main()
