"""
Module die entlang des Bézier-Bogens platziert werden können.
Jedes Modul zeichnet sich innerhalb seiner zugewiesenen Zone auf dem Balken.
"""
from __future__ import annotations
import math
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from PyQt6.QtCore import Qt, QPointF, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen, QPainterPath, QFont

from ui.widgets.arc.geometry import point_at, angle_at, build_strip, build_graph_strip, build_graph_strip_direct, PathCache
from ui.widgets.arc.radar_renderer import draw_radar


@dataclass
class ArcModule:
    """Basis für alle Bogen-Module."""
    name: str = "Module"
    t_start: float = 0.0       # Position auf dem Bogen (0.0 = links, 1.0 = rechts)
    t_end: float = 0.2         # Ende auf dem Bogen
    side: str = "outside"      # "outside" | "inside" | "center"
    height: float = 1.0        # Anteil der halben Balkendicke (0.0–1.0)
    color: tuple[int, int, int, int] = (255, 255, 255, 200)
    visible: bool = True

    def _offsets(self, ht: float) -> tuple[float, float]:
        """Berechnet inner/outer Offset basierend auf side + height."""
        h = ht * self.height
        if self.side == "outside":
            return 2.0, h        # von knapp über Mitte bis zur Außenkante
        elif self.side == "inside":
            return -h, -2.0      # von Innenkante bis knapp unter Mitte
        else:  # center
            return -h / 2, h / 2

    def paint(self, painter: QPainter, base_path: QPainterPath, ht: float) -> None:
        """Überschreiben in Unterklassen."""
        pass

    def mask_path(self, base_path: QPainterPath, ht: float) -> QPainterPath | None:
        """Gibt den Masken-Pfad zurück (für Click-Through). None = nur Basislinie."""
        return None

    def to_dict(self) -> dict:
        """Serialisierung für JSON."""
        return {
            "type": type(self).__name__,
            "name": self.name,
            "t_start": self.t_start,
            "t_end": self.t_end,
            "side": self.side,
            "height": self.height,
            "color": list(self.color),
            "visible": self.visible,
        }

    @staticmethod
    def from_dict(d: dict) -> "ArcModule":
        """Deserialisierung aus JSON."""
        cls_map = {
            "TextModule": TextModule,
            "GraphModule": GraphModule,
            "BarModule": BarModule,
            "RadarModule": RadarModule,
            "BrakeIndicatorModule": BrakeIndicatorModule,
        }
        cls = cls_map.get(d.get("type", ""), ArcModule)
        base_kw = {
            "name": d.get("name", "Module"),
            "t_start": d.get("t_start", 0.0),
            "t_end": d.get("t_end", 0.2),
            "side": d.get("side", "outside"),
            "height": d.get("height", 1.0),
            "color": tuple(d.get("color", [255, 255, 255, 200])),
            "visible": d.get("visible", True),
        }
        if cls == TextModule:
            return TextModule(
                **base_kw,
                text=d.get("text", ""),
                sub_text=d.get("sub_text", ""),
                font_size=d.get("font_size", 14),
            )
        elif cls == GraphModule:
            return GraphModule(
                **base_kw,
                data_key=d.get("data_key", "throttle"),
                fill_color=tuple(d.get("fill_color", [0, 255, 100, 60])),
                value_mode=d.get("value_mode", "percent"),
            )
        elif cls == BarModule:
            return BarModule(
                **base_kw,
                value=d.get("value", 0.5),
                bar_color=tuple(d.get("bar_color", [0, 255, 100, 200])),
            )
        elif cls == RadarModule:
            return RadarModule(
                **base_kw,
                radius_m=d.get("radius_m", 20.0),
            )
        elif cls == BrakeIndicatorModule:
            return BrakeIndicatorModule(
                **base_kw,
                dist_threshold=d.get("dist_threshold", 20.0),
                fill_color=tuple(d.get("fill_color", [255, 50, 50, 200])),
            )
        return ArcModule(**base_kw)


@dataclass
class TextModule(ArcModule):
    """Zeigt Text zentriert in der Modul-Zone an."""
    text: str = ""
    sub_text: str = ""
    font_size: int = 14

    def paint(self, painter: QPainter, base_path: QPainterPath, ht: float) -> None:
        if not self.visible or not self.text:
            return
        inner, outer = self._offsets(ht)

        # Hintergrund
        bg = build_strip(base_path, self.t_start, self.t_end, inner, outer)
        painter.setPen(QPen(QColor(80, 200, 255, 30), 1))
        painter.setBrush(QColor(10, 14, 20, 160))
        painter.drawPath(bg)

        # Text in der Mitte der Zone
        t_mid = (self.t_start + self.t_end) / 2
        offset = (inner + outer) / 2
        pt = point_at(base_path, t_mid, offset)
        rot = angle_at(base_path, t_mid)

        painter.save()
        painter.translate(pt)
        painter.rotate(rot)

        c = QColor(*self.color)
        painter.setPen(c)
        painter.setFont(QFont("Consolas", self.font_size, QFont.Weight.Bold))
        painter.drawText(QRectF(-60, -12, 120, 20),
                         Qt.AlignmentFlag.AlignCenter, self.text)
        if self.sub_text:
            painter.setFont(QFont("Consolas", max(6, self.font_size - 4)))
            painter.setPen(QColor(160, 160, 160))
            painter.drawText(QRectF(-60, 8, 120, 14),
                             Qt.AlignmentFlag.AlignCenter, self.sub_text)
        painter.restore()

    def mask_path(self, base_path, ht):
        inner, outer = self._offsets(ht)
        return build_strip(base_path, self.t_start, self.t_end, inner, outer)

    def to_dict(self):
        d = super().to_dict()
        d.update(text=self.text, sub_text=self.sub_text, font_size=self.font_size)
        return d


@dataclass
class GraphModule(ArcModule):
    """Zeichnet einen Werte-Graph innerhalb der Modul-Zone."""
    data_key: str = "throttle"                    # Key für Live-Daten
    fill_color: tuple[int, int, int, int] = (0, 255, 100, 60)
    value_mode: str = "percent"                   # "percent" oder "gear"
    _values: list[float] = field(default_factory=list, repr=False)

    # Distance-basierte Daten (wie InputGraphWidget)
    _live_samples: list[tuple[float, float]] = field(default_factory=list, repr=False)
    _ref_samples: list[tuple[float, float]] = field(default_factory=list, repr=False)
    _ref_offset: float = field(default=0.0, repr=False)
    _current_distance: float = field(default=0.0, repr=False)
    _window_m: float = field(default=800.0, repr=False)

    MIN_WINDOW: float = field(default=200.0, repr=False, init=False)
    MAX_WINDOW: float = field(default=3000.0, repr=False, init=False)

    def set_values(self, values: list[float]) -> None:
        """Legacy: Externe Daten setzen (0.0–1.0 pro Sample)."""
        self._values = values

    def set_live_samples(self, samples: list[tuple[float, float]],
                         current_distance: float) -> None:
        """Distance-basierte Live-Daten setzen (Referenz, kein Copy!)."""
        self._live_samples = samples
        self._current_distance = current_distance

    def set_ref_samples(self, samples: list[tuple[float, float]],
                        offset: float = 0.0) -> None:
        """Reference-Lap-Daten setzen."""
        self._ref_samples = samples
        self._ref_offset = offset

    def clear_ref(self) -> None:
        self._ref_samples = []
        self._ref_offset = 0.0

    def apply_zoom(self, factor: float) -> None:
        self._window_m = max(self.MIN_WINDOW,
                             min(self.MAX_WINDOW, self._window_m * factor))

    def _normalize_value(self, v: float) -> float:
        """Normiert den Wert auf 0-1 je nach value_mode."""
        if self.value_mode == "gear":
            return max(0.0, min(1.0, (max(1.0, min(8.0, v)) - 1.0) / 7.0))
        return max(0.0, min(1.0, v))

    def _map_samples_to_arc(
        self,
        samples: list[tuple[float, float]],
        vs: float, ve: float,
        t_start: float, t_end: float,
        max_points: int = 200,
    ) -> list[tuple[float, float]]:
        """Mappt sichtbare (distance, value) Samples auf (t_arc, norm_value).

        Verwendet bisect zum schnellen Filtern und downsampled bei Bedarf.
        Identisch zum Main-Dashboard — jeder Sample an seiner exakten Position.
        """
        span = ve - vs
        if span <= 0 or not samples:
            return []

        # Binäre Suche nach sichtbarem Fenster
        lo = bisect_left(samples, vs, key=lambda s: s[0])
        hi = bisect_right(samples, ve, key=lambda s: s[0])

        visible = samples[max(0, lo - 1):min(len(samples), hi + 1)]
        if not visible:
            return []

        # Downsampling wenn zu viele Punkte
        if len(visible) > max_points:
            step = len(visible) / max_points
            downsampled = []
            acc = 0.0
            for i, s in enumerate(visible):
                if i >= acc:
                    downsampled.append(s)
                    acc += step
            # Immer letzten Punkt mitnehmen
            if downsampled[-1] != visible[-1]:
                downsampled.append(visible[-1])
            visible = downsampled

        # Distance → t auf dem Bogen-Segment
        t_span = t_end - t_start
        result = []
        for d, v in visible:
            t = t_start + ((d - vs) / span) * t_span
            result.append((t, self._normalize_value(v)))
        return result

    def paint(self, painter: QPainter, base_path: QPainterPath, ht: float) -> None:
        if not self.visible:
            return
        inner, outer = self._offsets(ht)

        # Hintergrund-Streifen (40 steps statt 100)
        bg = build_strip(base_path, self.t_start, self.t_end, inner, outer)
        
        painter.save()
        painter.setClipPath(bg)
        
        painter.setPen(QPen(QColor(80, 200, 255, 20), 1))
        painter.setBrush(QColor(10, 14, 20, 140))
        painter.drawPath(bg)

        amp = outer - inner

        # Sichtfenster berechnen
        half = self._window_m / 2
        vs = self._current_distance - half
        ve = self._current_distance + half

        # --- Reference Lap (grau) ---
        if self._ref_samples:
            shifted = [(d + self._ref_offset, v) for d, v in self._ref_samples]
            ref_pairs = self._map_samples_to_arc(
                shifted, vs, ve, self.t_start, self.t_end
            )
            if len(ref_pairs) >= 2:
                ref_graph = build_graph_strip_direct(
                    base_path, ref_pairs, inner, amp
                )
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(160, 160, 160, 35))
                painter.drawPath(ref_graph)
                painter.setPen(QPen(QColor(180, 180, 180, 130), 1.2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(ref_graph)

        # --- Live-Daten (farbig) ---
        if self._live_samples:
            live_pairs = self._map_samples_to_arc(
                self._live_samples, vs, ve, self.t_start, self.t_end
            )
            if len(live_pairs) >= 2:
                live_graph = build_graph_strip_direct(
                    base_path, live_pairs, inner, amp
                )
                c = QColor(*self.color)
                painter.setPen(QPen(c, 1.5))
                painter.setBrush(QColor(*self.fill_color))
                painter.drawPath(live_graph)
        elif self._values:
            # Legacy-Modus
            graph = build_graph_strip(
                base_path, self.t_start, self.t_end,
                self._values, inner, amp
            )
            c = QColor(*self.color)
            painter.setPen(QPen(c, 1.5))
            painter.setBrush(QColor(*self.fill_color))
            painter.drawPath(graph)

        # Mittellinie (aktuelle Position)
        if self._live_samples:
            t_mid_line = (self.t_start + self.t_end) / 2
            pt_top = point_at(base_path, t_mid_line, outer)
            pt_bot = point_at(base_path, t_mid_line, inner)
            painter.setPen(QPen(QColor(255, 255, 255, 50), 1, Qt.PenStyle.DashLine))
            painter.drawLine(pt_top, pt_bot)

        painter.restore()

        # Label
        t_mid = (self.t_start + self.t_end) / 2
        pt = point_at(base_path, t_mid, outer + 4)
        rot = angle_at(base_path, t_mid)
        painter.save()
        painter.translate(pt)
        painter.rotate(rot)
        painter.setPen(QColor(*self.color[:3], 120))
        painter.setFont(QFont("Consolas", 7))
        painter.drawText(QRectF(-40, -6, 80, 12),
                         Qt.AlignmentFlag.AlignCenter, self.name)
        painter.restore()

    def mask_path(self, base_path, ht):
        inner, outer = self._offsets(ht)
        return build_strip(base_path, self.t_start, self.t_end, inner, outer)

    def to_dict(self):
        d = super().to_dict()
        d.update(data_key=self.data_key, fill_color=list(self.fill_color),
                 value_mode=self.value_mode)
        return d


@dataclass
class BarModule(ArcModule):
    """Füllstands-Balken (z.B. RPM, ERS)."""
    value: float = 0.5                            # Aktueller Füllstand 0.0–1.0
    bar_color: tuple[int, int, int, int] = (0, 255, 100, 200)

    def set_value(self, v: float) -> None:
        self.value = max(0.0, min(1.0, v))

    def paint(self, painter: QPainter, base_path: QPainterPath, ht: float) -> None:
        if not self.visible:
            return
        inner, outer = self._offsets(ht)

        # Hintergrund
        bg = build_strip(base_path, self.t_start, self.t_end, inner, outer)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(10, 14, 20, 160))
        painter.drawPath(bg)

        # Füll-Balken
        t_fill = self.t_start + (self.t_end - self.t_start) * self.value
        fill = build_strip(base_path, self.t_start, t_fill, inner + 2, outer - 2)
        painter.setBrush(QColor(*self.bar_color))
        painter.drawPath(fill)

        # Label
        t_mid = (self.t_start + self.t_end) / 2
        mid_off = (inner + outer) / 2
        pt = point_at(base_path, t_mid, mid_off)
        rot = angle_at(base_path, t_mid)
        painter.save()
        painter.translate(pt)
        painter.rotate(rot)
        painter.setPen(QColor(255, 255, 255, 200))
        painter.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        painter.drawText(QRectF(-40, -6, 80, 12),
                         Qt.AlignmentFlag.AlignCenter, self.name)
        painter.restore()

    def mask_path(self, base_path, ht):
        inner, outer = self._offsets(ht)
        return build_strip(base_path, self.t_start, self.t_end, inner, outer)

    def to_dict(self):
        d = super().to_dict()
        d.update(value=self.value, bar_color=list(self.bar_color))
        return d


@dataclass
class BrakeIndicatorModule(ArcModule):
    """Zieht einen Balken auf, der sich füllt, je näher man dem Bremspunkt kommt."""
    dist_threshold: float = 20.0                  # Sichtbare Distanz zum Bremspunkt (Meter)
    fill_color: tuple[int, int, int, int] = (255, 50, 50, 200) # Rote Warnfarbe

    _current_distance: float = 0.0
    _ref_samples: list[tuple[float, float]] = field(default_factory=list, repr=False)
    _ref_offset: float = 0.0

    def set_ref_samples(self, samples: list[tuple[float, float]], offset: float = 0.0) -> None:
        self._ref_samples = samples
        self._ref_offset = offset

    def set_current_distance(self, dist: float) -> None:
        self._current_distance = dist

    def paint(self, painter: QPainter, base_path: QPainterPath, ht: float) -> None:
        if not self.visible:
            return
            
        inner, outer = self._offsets(ht)

        fill_ratio = 0.0
        if self._ref_samples:
            # 1. Finde unsere Position in der Referenzrunde
            idx = bisect_left(self._ref_samples, self._current_distance - self._ref_offset, key=lambda s: s[0])
            
            # Prüfen, ob wir genau jetzt schon IN der Bremszone sind
            if idx < len(self._ref_samples) and self._ref_samples[idx][1] > 0.05:
                fill_ratio = 1.0
            else:
                # 2. Vorausschau: Finde den nächsten Bremspunkt vor uns
                target_dist = -1.0
                for i in range(idx, min(idx + 500, len(self._ref_samples))):
                    d, brake_val = self._ref_samples[i]
                    if brake_val > 0.05:
                        target_dist = d + self._ref_offset
                        break
                        
                # 3. Ratio berechnen, falls nah genug
                if target_dist >= self._current_distance:
                    dist_to_brake = target_dist - self._current_distance
                    if dist_to_brake <= self.dist_threshold:
                        # nähert sich 1.0, je kleiner dist_to_brake wird
                        fill_ratio = 1.0 - (dist_to_brake / self.dist_threshold)

        fill_ratio = max(0.0, min(1.0, fill_ratio))

        # Hintergrund
        bg = build_strip(base_path, self.t_start, self.t_end, inner, outer)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(10, 14, 20, 160))
        painter.drawPath(bg)

        # Füll-Balken
        if fill_ratio > 0.001:
            t_fill = self.t_start + (self.t_end - self.t_start) * fill_ratio
            fill = build_strip(base_path, self.t_start, t_fill, inner + 2, outer - 2)
            painter.setBrush(QColor(*self.fill_color))
            painter.drawPath(fill)

        # Label
        t_mid = (self.t_start + self.t_end) / 2
        mid_off = (inner + outer) / 2
        pt = point_at(base_path, t_mid, mid_off)
        rot = angle_at(base_path, t_mid)
        painter.save()
        painter.translate(pt)
        painter.rotate(rot)
        painter.setPen(QColor(255, 255, 255, 200))
        painter.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        text = f"{self.name}" if fill_ratio == 0 else f"{self.name} ({fill_ratio*100:.0f}%)"
        painter.drawText(QRectF(-80, -10, 160, 20),
                         Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def mask_path(self, base_path, ht):
        inner, outer = self._offsets(ht)
        return build_strip(base_path, self.t_start, self.t_end, inner, outer)

    def to_dict(self):
        d = super().to_dict()
        d.update(dist_threshold=self.dist_threshold, fill_color=list(self.fill_color))
        return d


class RadarModule(ArcModule):
    """
    Zeichnet ein Top-Down-Radar (Sonar) der Autos relativ zur eigenen Position.
    """
    def __init__(self, name: str, t_start: float, t_end: float,
                 radius_m: float = 20.0,
                 side: str = "outside", height: float = 1.0, 
                 color: tuple = (255, 255, 255, 200), visible: bool = True):
        super().__init__(name, t_start, t_end, side, height, color, visible)
        self.radius_m = radius_m
        
        self._player_pos = (0.0, 0.0, 0.0)
        self._player_fwd = (0.0, 0.0, 1.0)
        self._player_right = (1.0, 0.0, 0.0)
        self._opponents = []

    def update_telemetry(self, pos: tuple, fwd: tuple, right: tuple, opps: list):
        self._player_pos = pos
        self._player_fwd = fwd
        self._player_right = right
        self._opponents = opps

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["type"] = "RadarModule"
        d["radius_m"] = self.radius_m
        return d

    def paint(self, painter: QPainter, base_path: PathCache | QPainterPath, ht: float) -> None:
        if not self.visible:
            return

        inner, outer = self._offsets(ht)
        
        # Position the radar exactly in the middle of t_start and t_end
        t_mid = (self.t_start + self.t_end) / 2
        mid_off = (inner + outer) / 2
        
        pt = point_at(base_path, t_mid, mid_off)
        size = abs(outer - inner)
        
        painter.save()
        draw_radar(
            painter, cx=pt.x(), cy=pt.y(), size=size,
            player_pos=self._player_pos, player_fwd=self._player_fwd,
            player_right=self._player_right, opponents=self._opponents,
            radius_m=self.radius_m
        )
        painter.restore()
        
        # Draw radius value text
        angle = angle_at(base_path, t_mid)
        painter.save()
        painter.translate(pt)
        # Radar is pinned, text rotates with the line
        painter.rotate(angle)
        painter.setPen(QColor(*self.color))
        f = painter.font()
        f.setPointSize(8)
        painter.setFont(f)
        painter.drawText(int(-size/2), int(-size/2 - 4), f"{self.radius_m}m")
        painter.restore()
