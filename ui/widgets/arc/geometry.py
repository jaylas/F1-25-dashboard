"""
Geometrie-Hilfsfunktionen für den Bézier-Bogen.
Reine Mathematik — nur QPainterPath / QPointF als Qt-Abhängigkeit.
"""
from __future__ import annotations
import math
from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QPainterPath


class PathCache:
    """Caching für QPainterPath Punkte und Normalen (für ~20x höhere Performance)."""
    def __init__(self, path: QPainterPath, steps: int = 2000):
        self.steps = steps
        self.pts = []
        self.normals = []
        for i in range(steps + 1):
            t = i / steps
            self.pts.append(path.pointAtPercent(t))
        for i in range(steps + 1):
            p1 = self.pts[max(0, i - 1)]
            p2 = self.pts[min(steps, i + 1)]
            dx, dy = p2.x() - p1.x(), p2.y() - p1.y()
            ln = math.hypot(dx, dy)
            if ln < 1e-6:
                self.normals.append((0.0, -1.0))
            else:
                self.normals.append((dy / ln, -dx / ln))

    def point_at(self, t: float, offset: float = 0.0) -> QPointF:
        idx = max(0, min(self.steps, int(t * self.steps + 0.5)))
        p = self.pts[idx]
        if abs(offset) < 0.01: return p
        nx, ny = self.normals[idx]
        return QPointF(p.x() + nx * offset, p.y() + ny * offset)

    def angle_at(self, t: float) -> float:
        idx = max(0, min(self.steps, int(t * self.steps + 0.5)))
        nx, ny = self.normals[idx]
        return math.degrees(math.atan2(ny, nx)) + 90


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


def point_at(path: QPainterPath | PathCache, t: float, offset: float = 0.0) -> QPointF:
    """Punkt auf dem Pfad bei t, um offset Pixel nach außen verschoben."""
    if isinstance(path, PathCache):
        return path.point_at(t, offset)
    p = path.pointAtPercent(t)
    if abs(offset) < 0.01:
        return p
    nx, ny = normal_at(path, t)
    return QPointF(p.x() + nx * offset, p.y() + ny * offset)


def angle_at(path: QPainterPath | PathCache, t: float) -> float:
    """Rotationswinkel (Grad) für Text-Ausrichtung entlang des Pfades."""
    if isinstance(path, PathCache):
        return path.angle_at(t)
    nx, ny = normal_at(path, t)
    return math.degrees(math.atan2(ny, nx)) + 90


def build_strip(path: QPainterPath, t0: float, t1: float,
                inner: float, outer: float, steps: int = 40) -> QPainterPath:
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


def build_graph_strip_direct(
    path: QPainterPath,
    t_value_pairs: list[tuple[float, float]],
    base_off: float,
    max_amp: float,
) -> QPainterPath:
    """Graph-Streifen aus exakten (t, normalisierter_wert) Paaren.

    Im Gegensatz zu build_graph_strip wird hier jeder Sample an seiner
    exakten Position auf dem Bogen gezeichnet — keine Interpolation,
    keine festen Schritte. Das ergibt die gleiche Schärfe wie
    InputGraphWidget._build_path im Main Dashboard.
    """
    s = QPainterPath()
    if not t_value_pairs:
        return s
    # Obere Kante (Werte)
    first_t = t_value_pairs[0][0]
    s.moveTo(point_at(path, first_t, base_off))
    for t, v in t_value_pairs:
        s.lineTo(point_at(path, t, base_off + v * max_amp))
    # Untere Kante (Baseline), rückwärts
    last_t = t_value_pairs[-1][0]
    s.lineTo(point_at(path, last_t, base_off))
    for t, _v in reversed(t_value_pairs):
        s.lineTo(point_at(path, t, base_off))
    s.closeSubpath()
    return s

