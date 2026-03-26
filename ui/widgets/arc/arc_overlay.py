"""
ArcOverlayWidget – Transparentes Overlay-Fenster mit Bézier-Bogen und Modulen.
"""
from __future__ import annotations
import json, os
from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QPainter, QColor, QPen, QPainterPath, QPixmap
from PyQt6.QtWidgets import QWidget

from ui.widgets.arc.geometry import point_at, build_strip, PathCache
from ui.widgets.arc.modules import ArcModule, GraphModule


class ArcOverlayWidget(QWidget):
    """Transparentes Fenster, das einen Bézier-Bogen mit Modulen darstellt."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # Bogen-Parameter
        self.win_w: int = 1400
        self.win_h: int = 700
        self.bezier_w: int = 1100
        self.bezier_h: int = 500
        self.ctrl_x: int = 200
        self.ctrl_y_extra: int = 80
        self.base_thick: int = 60
        self.base_color: QColor = QColor(25, 28, 35, 255)
        self.round_caps: bool = True

        # Module
        self.modules: list[ArcModule] = []
        self._cached_pc = None

        self._drag = None
        self.resize(self.win_w, self.win_h)

    # ── API ───────────────────────────────────────────────────────────
    def set_arc_params(self, **kw) -> None:
        for k, v in kw.items():
            setattr(self, k, v)
        if self.width() != self.win_w or self.height() != self.win_h:
            self.resize(self.win_w, self.win_h)
        self.rebuild()

    def add_module(self, module: ArcModule) -> None:
        self.modules.append(module)
        self.rebuild()

    def remove_module(self, index: int) -> None:
        if 0 <= index < len(self.modules):
            self.modules.pop(index)
            self.rebuild()

    def rebuild(self) -> None:
        self._cached_pc = None
        self._update_mask()
        self.update()

    # ── Zoom ─────────────────────────────────────────────────────────
    def _graph_modules(self) -> list[GraphModule]:
        return [m for m in self.modules if isinstance(m, GraphModule)]

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return
        factor = 0.9 if delta > 0 else 1.1
        for gm in self._graph_modules():
            gm.apply_zoom(factor)
        self.update()
        event.accept()

    # ── Bézier-Pfad ──────────────────────────────────────────────────
    def base_path(self) -> QPainterPath:
        path = QPainterPath()
        cx = self.width() / 2
        sx = cx - self.bezier_w / 2
        ex = cx + self.bezier_w / 2
        by = self.height() - 60

        peak_y = by - self.bezier_h
        ctrl_y = peak_y - self.ctrl_y_extra

        c1 = QPointF(sx + self.ctrl_x, ctrl_y)
        c2 = QPointF(ex - self.ctrl_x, ctrl_y)
        path.moveTo(QPointF(sx, by))
        path.cubicTo(c1, c2, QPointF(ex, by))
        return path

    def cached_path(self) -> PathCache:
        if self._cached_pc is None:
            self._cached_pc = PathCache(self.base_path())
        return self._cached_pc

    # ── Maske ────────────────────────────────────────────────────────
    def _update_mask(self) -> None:
        pm = QPixmap(self.size())
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bp = self.base_path()
        pc = self.cached_path()

        # Basislinie
        pen = QPen(Qt.GlobalColor.white, self.base_thick)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap if self.round_caps else Qt.PenCapStyle.FlatCap)
        p.setPen(pen)
        p.drawPath(bp)

        # Module
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(Qt.GlobalColor.white)
        ht = self.base_thick / 2
        for mod in self.modules:
            if mod.visible:
                mp = mod.mask_path(pc, ht)
                if mp:
                    p.drawPath(mp)
        p.end()
        self.setMask(pm.mask())

    # ── Zeichnen ─────────────────────────────────────────────────────
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bp = self.base_path()
        pc = self.cached_path()
        ht = self.base_thick / 2

        # Basisbalken
        pen = QPen(self.base_color, self.base_thick)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap if self.round_caps else Qt.PenCapStyle.FlatCap)
        p.setPen(pen)
        p.drawPath(bp)

        # Kanten-Highlights
        p.setPen(QPen(QColor(80, 180, 255, 40), 1))
        for off in [ht, -ht]:
            edge = QPainterPath()
            for i in range(101):
                t = i / 100.0
                pt = point_at(pc, t, off)
                edge.moveTo(pt) if i == 0 else edge.lineTo(pt)
            p.drawPath(edge)

        # Module zeichnen
        for mod in self.modules:
            if mod.visible:
                mod.paint(p, pc, ht)

        p.end()

    # ── Drag ─────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag and e.buttons() == Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None

    # ── Serialisierung ───────────────────────────────────────────────
    def save_config(self, path: str) -> None:
        data = {
            "arc": {
                "win_w": self.win_w, "win_h": self.win_h,
                "bezier_w": self.bezier_w, "bezier_h": self.bezier_h,
                "ctrl_x": self.ctrl_x, "ctrl_y_extra": self.ctrl_y_extra,
                "base_thick": self.base_thick, "round_caps": self.round_caps,
                "base_color": [self.base_color.red(), self.base_color.green(),
                               self.base_color.blue(), self.base_color.alpha()],
            },
            "modules": [m.to_dict() for m in self.modules],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def load_config(self, path: str) -> None:
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        arc = data.get("arc", {})
        for k in ("win_w", "win_h", "bezier_w", "bezier_h",
                   "ctrl_x", "ctrl_y_extra", "base_thick", "round_caps"):
            if k in arc:
                setattr(self, k, arc[k])
        if "base_color" in arc:
            self.base_color = QColor(*arc["base_color"])
            self.base_color.setAlpha(255) # Erzwinge 100% Opaque-Hintergrund für den neuen Global-Slider
        self.modules = [ArcModule.from_dict(d) for d in data.get("modules", [])]
        self.resize(self.win_w, self.win_h)
        self.rebuild()
