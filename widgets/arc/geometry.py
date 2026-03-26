"""
Geometrie-Hilfsfunktionen für den Bézier-Bogen.
Reine Mathematik — nur QPainterPath / QPointF als Qt-Abhängigkeit.
"""
from __future__ import annotations
import math
from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QPainterPath


def normal_at(path: QPainterPath, t: float) -> tuple[float, float]:
    """Numerischer Normalenvektor an Position t. Positiv = nach außen/oben."""
    dt = 0.003
    t1, t2 = max(0.0, t - dt), min(1.0, t + dt)
    p1, p2 = path.pointAtPercent(t1), path.pointAtPercent(t2)
    dx, dy = p2.x() - p1.x(), p2.y() - p1.y()
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return 0.0, -1.0
    # Tangente (dx, dy) → Normale 90° nach links = (dy, -dx) → zeigt "nach oben"
    return dy / length, -dx / length


def point_at(path: QPainterPath, t: float, offset: float = 0.0) -> QPointF:
    """Punkt auf dem Pfad bei t, um offset Pixel nach außen verschoben."""
    p = path.pointAtPercent(t)
    if abs(offset) < 0.01:
        return p
    nx, ny = normal_at(path, t)
    return QPointF(p.x() + nx * offset, p.y() + ny * offset)


def angle_at(path: QPainterPath, t: float) -> float:
    """Rotationswinkel (Grad) für Text-Ausrichtung entlang des Pfades."""
    nx, ny = normal_at(path, t)
    return math.degrees(math.atan2(ny, nx)) + 90


def build_strip(path: QPainterPath, t0: float, t1: float,
                inner: float, outer: float, steps: int = 100) -> QPainterPath:
    """Streifen-Polygon entlang des Pfades von t0..t1, inner/outer = Offset."""
    s = QPainterPath()
    for i in range(steps + 1):
        t = t0 + (t1 - t0) * i / steps
        pt = point_at(path, t, outer)
        s.moveTo(pt) if i == 0 else s.lineTo(pt)
    for i in range(steps, -1, -1):
        t = t0 + (t1 - t0) * i / steps
        s.lineTo(point_at(path, t, inner))
    s.closeSubpath()
    return s


def build_graph_strip(path: QPainterPath, t0: float, t1: float,
                      values: list[float], base_off: float, max_amp: float,
                      steps: int = 100) -> QPainterPath:
    """Streifen, dessen äußere Kante den Werten (0..1) folgt."""
    s = QPainterPath()
    n = len(values)
    if n == 0:
        return s
    for i in range(steps + 1):
        t = t0 + (t1 - t0) * i / steps
        idx = min(int((i / steps) * (n - 1)), n - 1)
        pt = point_at(path, t, base_off + values[idx] * max_amp)
        s.moveTo(pt) if i == 0 else s.lineTo(pt)
    for i in range(steps, -1, -1):
        t = t0 + (t1 - t0) * i / steps
        s.lineTo(point_at(path, t, base_off))
    s.closeSubpath()
    return s
