import math
import os
from pathlib import Path
from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QPainter, QColor, QPen, QPainterPath, QPixmap

_IMAGE_CACHE = {}

def get_car_image(color: str, width: int, height: int) -> QPixmap:
    key = (color, width, height)
    if key not in _IMAGE_CACHE:
        orig_key = (color, "orig")
        if orig_key not in _IMAGE_CACHE:
            # f1-pictogram-color.png is in the project root
            root_dir = Path(__file__).resolve().parent.parent.parent.parent
            path = str(root_dir / "assets" / f"f1-pictogram-{color}.png")
            _IMAGE_CACHE[orig_key] = QPixmap(path)
            
        orig = _IMAGE_CACHE[orig_key]
        if orig.isNull():
            _IMAGE_CACHE[key] = orig
        else:
            _IMAGE_CACHE[key] = orig.scaled(
                width, height, 
                Qt.AspectRatioMode.IgnoreAspectRatio, 
                Qt.TransformationMode.SmoothTransformation
            )
    return _IMAGE_CACHE[key]

def draw_radar(painter: QPainter, 
               cx: float, cy: float, 
               size: float, 
               player_pos: tuple[float, float, float],
               player_fwd: tuple[float, float, float],
               player_right: tuple[float, float, float],
               opponents: list[tuple[float, float, float]],
               radius_m: float = 20.0,
               bg_color: tuple[int, int, int, int] = (10, 14, 20, 180),
               border_color: tuple[int, int, int, int] = (80, 200, 255, 100)) -> None:
    """
    Zeichnet ein Radar (Sonar) an (cx, cy) mit Durchmesser `size`.
    - player_pos: (X, Y, Z) des Spielers (Y ist meist Höhe, ignorieren wir für 2D).
    - player_fwd: Forward-Vektor des Spielers.
    - player_right: Right-Vektor des Spielers.
    - opponents: Liste von (X, Y, Z) der Gegner.
    - radius_m: Wie viele In-Game Meter entsprechen dem Rand des Radars?
    """
    r = size / 2

    # Hintergrund Kreis
    painter.setPen(QPen(QColor(*border_color), 1.5))
    painter.setBrush(QColor(*bg_color))
    painter.drawEllipse(QPointF(cx, cy), r, r)

    # Hilfslinien (Fadenkreuz) / Ringe
    painter.setPen(QPen(QColor(255, 255, 255, 30), 1, Qt.PenStyle.DashLine))
    painter.drawEllipse(QPointF(cx, cy), r / 2, r / 2) # Innerer 50% Ring
    painter.drawLine(QPointF(cx - r, cy), QPointF(cx + r, cy))
    painter.drawLine(QPointF(cx, cy - r), QPointF(cx, cy + r))

    # Skalierungsfaktor von In-Game Metern auf Pixel
    scale = r / radius_m

    px, py, pz = player_pos
    fx, fy, fz = player_fwd
    rx, ry, rz = player_right

    # Vektorlänge (normalisieren für Sicherheit)
    f_len = math.hypot(fx, fz)
    r_len = math.hypot(rx, rz)
    if f_len < 1e-6 or r_len < 1e-6:
        # Failsafe if vectors are zero
        fx, fz = 0, 1
        rx, rz = 1, 0
    else:
        fx, fz = fx/f_len, fz/f_len
        rx, rz = rx/r_len, rz/r_len

    # ==========================================================
    # HIER KANNST DU DIE PROPORTIONEN DER AUTOS ANPASSEN:
    # (in In-Game Metern)
    # 5.5m Länge ist Standard, experimentiere mit der Breite (z.B. 1.8 oder 2.5)
    # ==========================================================
    CAR_LENGTH_METERS = 5.5
    CAR_WIDTH_METERS = 3.0  
    
    car_h = max(2, int(CAR_LENGTH_METERS * scale))
    car_w = max(2, int(CAR_WIDTH_METERS * scale))
    
    red_car = get_car_image("red", car_w, car_h)

    # Gegner zeichnen
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(255, 50, 50, 200)) # Rot für Gegner (Fallback)

    for (ox, oy, oz) in opponents:
        dx = ox - px
        dz = oz - pz
        
        # 3D Abstand ignorieren wir nicht ganz, aber fürs Radar ist meist 2D (X/Z Plane) relevanter.
        dist = math.hypot(dx, dz)
        if dist > radius_m * 1.5:  # Bisschen Puffer, falls wir clipping machen
            continue

        local_x = dx * rx + dz * rz
        local_z = dx * fx + dz * fz

        ui_x = cx + local_x * scale
        ui_y = cy - local_z * scale

        if math.hypot(ui_x - cx, ui_y - cy) > r:
            continue

        if not red_car.isNull():
            painter.drawPixmap(int(ui_x - car_w / 2), int(ui_y - car_h / 2), red_car)
        else:
            painter.drawEllipse(QPointF(ui_x, ui_y), 4.0, 4.0)

    # Spieler selbst zeichnen (Zentrum)
    green_car = get_car_image("green", car_w, car_h)
    if not green_car.isNull():
        painter.drawPixmap(int(cx - car_w / 2), int(cy - car_h / 2), green_car)
    else:
        painter.setBrush(QColor(0, 255, 100, 255)) # Grün für Spieler
        # Ein kleines Dreieck, um die Ausrichtung zu zeigen
        poly = [
            QPointF(cx, cy - 6),      # Spitze vorn
            QPointF(cx - 4, cy + 5),   # Hinten links
            QPointF(cx + 4, cy + 5)    # Hinten rechts
        ]
        path = QPainterPath()
        path.moveTo(poly[0])
        path.lineTo(poly[1])
        path.lineTo(poly[2])
        path.closeSubpath()
        painter.drawPath(path)
