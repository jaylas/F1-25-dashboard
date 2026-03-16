# F1-25-dashboard

A lightweight always-on-top PyQt6 overlay for F1 25 that listens to UDP telemetry and displays live brake, throttle, and gear traces against track distance.

## Features

- Live brake, throttle, and gear graphs (scroll to zoom distance window)
- Optional reference lap overlay with manual offset alignment
- Auto track detection and auto-load from refs/<track>.json
- Lap recording, archive history, and review window
- Reference lap save/load (internal and external JSON formats)

## Project Structure

- main.py: Application entrypoint
- models.py: Shared constants and data models
- telemetry.py: UDP listener and packet parsing
- widgets/graph.py: Reusable graph widget
- windows/overlay.py: Main overlay window
- windows/review.py: Lap review window
- penis_archive.py: Archived pre-refactor monolith
- refs/: Per-track reference JSON files

## Requirements

- Python 3.10+
- PyQt6

Install dependencies:

```bash
pip install PyQt6
```

## Run

```bash
python main.py
```

## Telemetry Setup

In F1 25, enable UDP telemetry and use port 20777.

## Notes

- The app binds UDP on all interfaces and updates connection status in the UI.
- If refs/<track>.json exists for the detected track, it is loaded automatically.

### Simulate UDP

- Standard (Bahrain)
```python3 simulate_udp.py```
- Eine andere Strecke wählen
```python3 simulate_udp.py --ref refs/melbourne.json```
- Simulation beschleunigen (z.B. 10x Speed für schnelles Testen)
```python3 simulate_udp.py --speed 10.0```
- Mehr oder weniger Zufall/Abweichung (Standard ist 0.02)
```python3 simulate_udp.py --noise 0.05```
- Automatisch mehrere Runden Simulieren
```python3 simulate_udp.py --laps 3```
