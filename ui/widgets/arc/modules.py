"""
Module die entlang des Bézier-Bogens platziert werden können.
Jedes Modul zeichnet sich innerhalb seiner zugewiesenen Zone auf dem Balken.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from PyQt6.QtCore import Qt, QPointF, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen, QPainterPath, QFont

from widgets.arc.geometry import point_at, angle_at, build_strip, build_graph_strip


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
            )
        elif cls == BarModule:
            return BarModule(
                **base_kw,
                value=d.get("value", 0.5),
                bar_color=tuple(d.get("bar_color", [0, 255, 100, 200])),
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
    _values: list[float] = field(default_factory=list, repr=False)

    def set_values(self, values: list[float]) -> None:
        """Externe Daten setzen (0.0–1.0 pro Sample)."""
        self._values = values

    def paint(self, painter: QPainter, base_path: QPainterPath, ht: float) -> None:
        if not self.visible:
            return
        inner, outer = self._offsets(ht)

        # Hintergrund
        bg = build_strip(base_path, self.t_start, self.t_end, inner, outer)
        painter.setPen(QPen(QColor(80, 200, 255, 20), 1))
        painter.setBrush(QColor(10, 14, 20, 140))
        painter.drawPath(bg)

        # Graph
        if self._values:
            amp = outer - inner
            graph = build_graph_strip(
                base_path, self.t_start, self.t_end,
                self._values, inner, amp
            )
            c = QColor(*self.color)
            painter.setPen(QPen(c, 1.5))
            painter.setBrush(QColor(*self.fill_color))
            painter.drawPath(graph)

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
        d.update(data_key=self.data_key, fill_color=list(self.fill_color))
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
