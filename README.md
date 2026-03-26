# F1-25 Dashboard

An interactive, modular, and always-on-top PyQt6 overlay for F1 25. It listens to live UDP telemetry and visualizes brake, throttle, and gear traces against track distance. It also features a complex Halo/Arc HUD and a standalone radar for detecting opponents.

## Features

- **Central Launcher:** Control everything from a compact main window (visibility, opacity, toggles).
- **Live Graph Dashboard:** Detailed brake, throttle, and gear curves. Scroll to zoom in/out of the distance window.
- **Halo/Arc HUD:** A transparent, fully customizable semi-circular layout that perfectly wraps around your in-game cockpit, featuring:
  - Dynamic throttle, brake, and gear bars
  - **Brake Indicator:** A smart bar that automatically fills up as you approach the braking point (up to 3000m ahead) of your own reference lap.
- **Standalone Radar:** Interactive radar for tracking opponent positions (with an optional smart auto-hide feature).
- **Auto Track Loading:** Automatically detects tracks (e.g. Bahrain) and loads the corresponding reference lap from `refs/bahrain.json`.
- **Lap Recording & Review:** Built-in system to record your own reference laps, visually compare them in a dedicated "Review Window," and save them as JSON.

## Project Structure

- `main.py`: Main entry point. Starts the **Launcher**.
- `config/`: JSON configs for layout states, GUI settings, and saved Arc designs.
- `core/`: The core logic architecture (UDP `telemetry.py` and `models.py`).
- `assets/`: Project graphics, e.g. icons for the radar (`f1-pictogram-*.png`).
- `scripts/`: Useful utility scripts like `simulate_udp.py`.
- `archive/`: Deprecated backup files for old iterations.
- `refs/`: Recorded reference laps (stores `.json` files per track).
- `ui/`: All frontend views
  - `ui/windows/`: Dedicated window classes (`launcher.py`, `graph_window.py`, `radar_window.py`, `review.py`).
  - `ui/widgets/`: Reusable widgets and the highly modular **Arc System** (`arc_overlay.py`, `modules.py`, etc.).

## Requirements

- Python 3.10+
- PyQt6

Install dependencies:

```bash
pip install PyQt6
```

## How to Run

```bash
python main.py
```

## Telemetry Setup (F1 25)

Enable UDP telemetry in F1 25 and set the port to **20777** (default). A telemetry rate of 20Hz-60Hz is recommended for smooth graph curves.

## Simulation / Test Script

If the game isn't running, you can use the included test script to broadcast mock telemetry data:

- Standard (Bahrain) mock on port 20777:
```bash
python scripts/simulate_udp.py
```
- Load a specific reference lap for the mock:
```bash
python scripts/simulate_udp.py --ref refs/losail.json
```
- Speed up the simulation (e.g. 10x speed for quick testing):
```bash
python scripts/simulate_udp.py --speed 10.0
```
- Adjust noise/randomness (default is 0.02):
```bash
python scripts/simulate_udp.py --noise 0.05
```
- Automatically simulate multiple laps in a loop:
```bash
python scripts/simulate_udp.py --laps 3
```
