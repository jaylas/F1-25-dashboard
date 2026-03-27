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
            "BrakeOverlayModule": BrakeOverlayModule,
            "TyreWearModule": TyreWearModule,
            "RelativeDeltaModule": RelativeDeltaModule,
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
            legacy_fill = tuple(d.get("fill_color", [255, 50, 50, 200]))
            return BrakeIndicatorModule(
                **base_kw,
                dist_threshold=d.get("dist_threshold", 20.0),
                fill_color_start=tuple(d.get("fill_color_start", legacy_fill)),
                fill_color_end=tuple(d.get("fill_color_end", legacy_fill)),
                fill_direction=d.get("fill_direction", "start_to_end"),
            )
        elif cls == BrakeOverlayModule:
            ref_rgb = tuple(d.get("ref_color_rgb", [255, 50, 50]))
            if len(ref_rgb) >= 3:
                ref_rgb = (int(ref_rgb[0]), int(ref_rgb[1]), int(ref_rgb[2]))
            else:
                ref_rgb = (255, 50, 50)
            live_rgb = tuple(d.get("live_color_rgb", [235, 235, 235]))
            if len(live_rgb) >= 3:
                live_rgb = (int(live_rgb[0]), int(live_rgb[1]), int(live_rgb[2]))
            else:
                live_rgb = (235, 235, 235)
            return BrakeOverlayModule(
                **base_kw,
                ref_color_rgb=ref_rgb,
                live_color_rgb=live_rgb,
                ref_opacity=int(d.get("ref_opacity", 70)),
                live_opacity=int(d.get("live_opacity", 45)),
                fill_direction=d.get("fill_direction", "start_to_end"),
            )
        elif cls == TyreWearModule:
            return TyreWearModule(
                **base_kw,
                show_values=d.get("show_values", True),
                smoothing_alpha=d.get("smoothing_alpha", 0.35),
                red_at_wear_pct=d.get("red_at_wear_pct", 60.0),
            )
        elif cls == RelativeDeltaModule:
            return RelativeDeltaModule(
                **base_kw,
                speed_scale=d.get("speed_scale", 5.0),
                response_alpha=d.get("response_alpha", 0.35),
                decay_factor=d.get("decay_factor", 0.96),
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
    fill_color_start: tuple[int, int, int, int] = (60, 120, 255, 220)
    fill_color_end: tuple[int, int, int, int] = (255, 50, 50, 220)
    fill_direction: str = "start_to_end"          # start_to_end | end_to_start | outside_to_inside

    _current_distance: float = 0.0
    _ref_samples: list[tuple[float, float]] = field(default_factory=list, repr=False)
    _ref_offset: float = 0.0

    def set_ref_samples(self, samples: list[tuple[float, float]], offset: float = 0.0) -> None:
        self._ref_samples = samples
        self._ref_offset = offset

    def set_current_distance(self, dist: float) -> None:
        self._current_distance = dist

    @staticmethod
    def _mix_color(c0: tuple[int, int, int, int], c1: tuple[int, int, int, int], ratio: float) -> QColor:
        r = max(0.0, min(1.0, float(ratio)))
        a0 = c0[3] if len(c0) > 3 else 220
        a1 = c1[3] if len(c1) > 3 else 220
        return QColor(
            int(c0[0] + (c1[0] - c0[0]) * r),
            int(c0[1] + (c1[1] - c0[1]) * r),
            int(c0[2] + (c1[2] - c0[2]) * r),
            int(a0 + (a1 - a0) * r),
        )

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
            span = self.t_end - self.t_start
            fill_col = self._mix_color(self.fill_color_start, self.fill_color_end, fill_ratio)
            if self.fill_direction == "outside_to_inside":
                mid = (self.t_start + self.t_end) * 0.5
                half_fill = (span * 0.5) * fill_ratio
                left_to = min(mid, self.t_start + half_fill)
                right_from = max(mid, self.t_end - half_fill)
                if left_to - self.t_start > 1e-6:
                    left_fill = build_strip(base_path, self.t_start, left_to, inner + 2, outer - 2)
                    painter.setBrush(fill_col)
                    painter.drawPath(left_fill)
                if self.t_end - right_from > 1e-6:
                    right_fill = build_strip(base_path, right_from, self.t_end, inner + 2, outer - 2)
                    painter.setBrush(fill_col)
                    painter.drawPath(right_fill)
            elif self.fill_direction == "end_to_start":
                t_from = self.t_end - (span * fill_ratio)
                fill = build_strip(base_path, t_from, self.t_end, inner + 2, outer - 2)
                painter.setBrush(fill_col)
                painter.drawPath(fill)
            else:
                t_fill = self.t_start + span * fill_ratio
                fill = build_strip(base_path, self.t_start, t_fill, inner + 2, outer - 2)
                painter.setBrush(fill_col)
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
        d.update(
            dist_threshold=self.dist_threshold,
            fill_color_start=list(self.fill_color_start),
            fill_color_end=list(self.fill_color_end),
            # Legacy compatibility for old readers.
            fill_color=list(self.fill_color_end),
            fill_direction=self.fill_direction,
        )
        return d


@dataclass
class BrakeOverlayModule(ArcModule):
    """Überlagert Referenz- und Live-Bremsbalken im selben Modul."""
    ref_color_rgb: tuple[int, int, int] = (255, 50, 50)
    live_color_rgb: tuple[int, int, int] = (235, 235, 235)
    ref_opacity: int = 70
    live_opacity: int = 45
    fill_direction: str = "start_to_end"  # start_to_end | end_to_start | outside_to_inside

    _current_distance: float = 0.0
    _ref_samples: list[tuple[float, float]] = field(default_factory=list, repr=False)
    _ref_offset: float = 0.0
    _live_samples: list[tuple[float, float]] = field(default_factory=list, repr=False)

    @staticmethod
    def _clamp01(value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    @staticmethod
    def _sample_value_at(samples: list[tuple[float, float]], distance_m: float) -> float | None:
        if not samples:
            return None
        idx = bisect_left(samples, distance_m, key=lambda s: s[0])
        if idx <= 0:
            return samples[0][1]
        if idx >= len(samples):
            return samples[-1][1]
        d0, v0 = samples[idx - 1]
        d1, v1 = samples[idx]
        if abs(d1 - d0) < 1e-6:
            return v1
        t = (distance_m - d0) / (d1 - d0)
        return v0 + (v1 - v0) * t

    def set_current_distance(self, dist: float) -> None:
        self._current_distance = float(dist)

    def set_ref_samples(self, samples: list[tuple[float, float]], offset: float = 0.0) -> None:
        self._ref_samples = samples
        self._ref_offset = float(offset)

    def set_live_samples(self, samples: list[tuple[float, float]]) -> None:
        self._live_samples = samples

    def _fill_bounds(self, ratio: float) -> list[tuple[float, float]]:
        span = max(0.0, self.t_end - self.t_start)
        clipped = self._clamp01(ratio)
        if self.fill_direction == "outside_to_inside":
            mid = (self.t_start + self.t_end) * 0.5
            half_fill = (span * 0.5) * clipped
            left = (self.t_start, min(mid, self.t_start + half_fill))
            right = (max(mid, self.t_end - half_fill), self.t_end)
            return [left, right]
        if self.fill_direction == "end_to_start":
            return [(self.t_end - (span * clipped), self.t_end)]
        return [(self.t_start, self.t_start + (span * clipped))]

    def paint(self, painter: QPainter, base_path: QPainterPath, ht: float) -> None:
        if not self.visible:
            return

        inner, outer = self._offsets(ht)
        bg = build_strip(base_path, self.t_start, self.t_end, inner, outer)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(10, 14, 20, 160))
        painter.drawPath(bg)

        ref_value = self._sample_value_at(self._ref_samples, self._current_distance - self._ref_offset)
        if ref_value is not None:
            for t0, t1 in self._fill_bounds(ref_value):
                if t1 - t0 > 1e-6:
                    ref_fill = build_strip(base_path, t0, t1, inner + 2, outer - 2)
                    ref_alpha = int(max(0, min(100, self.ref_opacity)) * 255 / 100)
                    rr, rg, rb = self.ref_color_rgb
                    painter.setBrush(QColor(rr, rg, rb, ref_alpha))
                    painter.drawPath(ref_fill)

        live_value = self._sample_value_at(self._live_samples, self._current_distance)
        if live_value is not None:
            for t0, t1 in self._fill_bounds(live_value):
                if t1 - t0 > 1e-6:
                    live_fill = build_strip(base_path, t0, t1, inner + 2, outer - 2)
                    live_alpha = int(max(0, min(100, self.live_opacity)) * 255 / 100)
                    lr, lg, lb = self.live_color_rgb
                    painter.setBrush(QColor(lr, lg, lb, live_alpha))
                    painter.drawPath(live_fill)

    def mask_path(self, base_path, ht):
        inner, outer = self._offsets(ht)
        return build_strip(base_path, self.t_start, self.t_end, inner, outer)

    def to_dict(self):
        d = super().to_dict()
        d.update(
            ref_color_rgb=list(self.ref_color_rgb),
            live_color_rgb=list(self.live_color_rgb),
            ref_opacity=int(self.ref_opacity),
            live_opacity=int(self.live_opacity),
            fill_direction=self.fill_direction,
        )
        return d


@dataclass
class TyreWearModule(ArcModule):
    """Kompakte 2x2 Darstellung fÃ¼r ReifenverschleiÃŸ (FL/FR/RL/RR)."""
    show_values: bool = True
    smoothing_alpha: float = 0.35
    red_at_wear_pct: float = 60.0
    _tyre_wear: tuple[float, float, float, float] = field(default=(0.0, 0.0, 0.0, 0.0), repr=False)

    def set_tyre_wear(self, wear: tuple[float, float, float, float]) -> None:
        target = tuple(max(0.0, min(100.0, float(v))) for v in wear)
        alpha = max(0.01, min(1.0, float(self.smoothing_alpha)))
        self._tyre_wear = tuple(
            old + (new - old) * alpha
            for old, new in zip(self._tyre_wear, target)
        )

    def _wear_color(self, wear_pct: float) -> QColor:
        # Reach full red at configured wear threshold and keep red above it.
        red_at = max(1.0, min(100.0, float(self.red_at_wear_pct)))
        t = max(0.0, min(1.0, wear_pct / red_at))
        green = (60, 220, 60)
        yellow = (255, 220, 60)
        red = (255, 50, 60)

        if t <= 0.5:
            u = t / 0.5
            r = int(green[0] + (yellow[0] - green[0]) * u)
            g = int(green[1] + (yellow[1] - green[1]) * u)
            b = int(green[2] + (yellow[2] - green[2]) * u)
        else:
            u = (t - 0.5) / 0.5
            r = int(yellow[0] + (red[0] - yellow[0]) * u)
            g = int(yellow[1] + (red[1] - yellow[1]) * u)
            b = int(yellow[2] + (red[2] - yellow[2]) * u)
        return QColor(r, g, b, 220)

    def paint(self, painter: QPainter, base_path: QPainterPath, ht: float) -> None:
        if not self.visible:
            return

        inner, outer = self._offsets(ht)
        zone = build_strip(base_path, self.t_start, self.t_end, inner, outer)

        painter.save()
        painter.setClipPath(zone)
        painter.setPen(QPen(QColor(80, 200, 255, 30), 1))
        painter.setBrush(QColor(10, 14, 20, 170))
        painter.drawPath(zone)

        t_mid = (self.t_start + self.t_end) / 2
        mid_off = (inner + outer) / 2
        pt = point_at(base_path, t_mid, mid_off)
        rot = angle_at(base_path, t_mid)

        painter.translate(pt)
        painter.rotate(rot)

        labels = ("", "", "", "")
        positions = (
            QRectF(-52, -26, 50, 22),
            QRectF(2, -26, 50, 22),
            QRectF(-52, 2, 50, 22),
            QRectF(2, 2, 50, 22),
        )

        painter.setFont(QFont("Consolas", 7, QFont.Weight.Bold))
        for i, rect in enumerate(positions):
            wear = self._tyre_wear[i]
            painter.setPen(QPen(QColor(220, 220, 220, 100), 1))
            painter.setBrush(self._wear_color(wear))
            painter.drawRoundedRect(rect, 4, 4)

            text = labels[i]
            if self.show_values:
                text = f"{labels[i]} {wear:.0f}%"
            painter.setPen(QColor(20, 20, 20, 240))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

        painter.restore()

    def mask_path(self, base_path, ht):
        inner, outer = self._offsets(ht)
        return build_strip(base_path, self.t_start, self.t_end, inner, outer)

    def to_dict(self):
        d = super().to_dict()
        d.update(
            show_values=self.show_values,
            smoothing_alpha=self.smoothing_alpha,
            red_at_wear_pct=self.red_at_wear_pct,
        )
        return d


@dataclass
class RelativeDeltaModule(ArcModule):
    """
    Center-Bar: grÃ¼n bei AnnÃ¤herung an den Vordermann, rot bei Distanzverlust.
    Wert ist eine normierte Relativgeschwindigkeits-Tendenz.
    """
    speed_scale: float = 5.0
    response_alpha: float = 0.35
    decay_factor: float = 0.96
    _trend_value: float = field(default=0.0, repr=False)
    _last_gap_m: float | None = field(default=None, repr=False)
    _last_time_s: float | None = field(default=None, repr=False)
    _closing_speed_ms: float = field(default=0.0, repr=False)

    def update_gap(self, gap_m: float | None, session_time_s: float) -> None:
        alpha = max(0.01, min(1.0, float(self.response_alpha)))
        decay = max(0.50, min(0.999, float(self.decay_factor)))

        if gap_m is None or session_time_s <= 0.0:
            self._trend_value *= decay
            self._last_gap_m = None
            self._last_time_s = session_time_s if session_time_s > 0.0 else None
            self._closing_speed_ms = 0.0
            return

        target = 0.0
        if self._last_gap_m is not None and self._last_time_s is not None:
            dt = session_time_s - self._last_time_s
            if 0.005 < dt < 1.0:
                closing_speed = (self._last_gap_m - gap_m) / dt
                self._closing_speed_ms = closing_speed
                scale = max(0.1, float(self.speed_scale))
                target = max(-1.0, min(1.0, closing_speed / scale))
        self._trend_value += (target - self._trend_value) * alpha
        self._last_gap_m = gap_m
        self._last_time_s = session_time_s

    def paint(self, painter: QPainter, base_path: QPainterPath, ht: float) -> None:
        if not self.visible:
            return

        inner, outer = self._offsets(ht)
        bg = build_strip(base_path, self.t_start, self.t_end, inner, outer)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(10, 14, 20, 160))
        painter.drawPath(bg)

        t_mid = (self.t_start + self.t_end) / 2
        amp_pos = self.t_end - t_mid
        amp_neg = t_mid - self.t_start
        v = max(-1.0, min(1.0, self._trend_value))

        if v > 0.001:
            t_to = t_mid + amp_pos * v
            fill = build_strip(base_path, t_mid, t_to, inner + 2, outer - 2)
            painter.setBrush(QColor(42, 232, 80, 220))
            painter.drawPath(fill)
        elif v < -0.001:
            t_to = t_mid + amp_neg * v
            fill = build_strip(base_path, t_to, t_mid, inner + 2, outer - 2)
            painter.setBrush(QColor(232, 42, 42, 220))
            painter.drawPath(fill)

        # Center marker
        top = point_at(base_path, t_mid, outer)
        bot = point_at(base_path, t_mid, inner)
        painter.setPen(QPen(QColor(255, 255, 255, 90), 1))
        painter.drawLine(top, bot)

        label_pt = point_at(base_path, t_mid, outer + 4)
        rot = angle_at(base_path, t_mid)
        painter.save()
        painter.translate(label_pt)
        painter.rotate(rot)
        painter.setPen(QColor(220, 220, 220, 170))
        painter.setFont(QFont("Consolas", 7, QFont.Weight.Bold))
        txt = f"{self.name} {self._closing_speed_ms:+.1f} m/s"
        painter.drawText(QRectF(-90, -6, 180, 12), Qt.AlignmentFlag.AlignCenter, txt)
        painter.restore()

    def mask_path(self, base_path, ht):
        inner, outer = self._offsets(ht)
        return build_strip(base_path, self.t_start, self.t_end, inner, outer)

    def to_dict(self):
        d = super().to_dict()
        d.update(
            speed_scale=self.speed_scale,
            response_alpha=self.response_alpha,
            decay_factor=self.decay_factor,
        )
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
