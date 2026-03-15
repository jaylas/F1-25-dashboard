# F1-25-dashboard

A lightweight **always-on-top PyQt6 overlay** for **F1 25** that listens to the game’s **UDP telemetry** and shows:

- Live **brake %**
- A **brake trace vs lap distance** graph (scroll to zoom the distance window)
- An optional **reference lap overlay** (grey) to compare against your current braking (red)
- Auto track detection and auto-loading of `refs/<track>.json` when available

> Note: The main script file is currently named `penis.py`. You may want to rename it to something like `overlay.py` for cleanliness.

---

## Features

- **UDP listener** on port `20777` (F1 default)
- Parses key packets:
  - Session packet → track ID (to pick a reference file)
  - Lap data → lap distance + lap number
  - Car telemetry → brake input
- **Reference lap workflow**
  - Record a lap and stop manually, or auto-finish when a new lap starts and distance resets
  - Save to JSON
  - Load JSON (supports two formats; see below)
- **Reference alignment**
  - Shift the reference trace left/right in 10m steps using buttons

---

## Requirements

- Python 3.10+ recommended
- PyQt6

Install deps:

```bash
pip install PyQt6