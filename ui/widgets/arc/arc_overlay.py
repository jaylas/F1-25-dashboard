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
        self.arc_left_trim_pct: int = 0
        self.arc_right_trim_pct: int = 0

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

    def _visible_t_range(self) -> tuple[float, float]:
        left = max(0.0, min(90.0, float(self.arc_left_trim_pct)))
        right = max(0.0, min(90.0, float(self.arc_right_trim_pct)))
        t_min = left / 100.0
        t_max = 1.0 - (right / 100.0)

        min_span = 0.05
        if t_max - t_min < min_span:
            mid = (t_min + t_max) * 0.5
            t_min = max(0.0, mid - (min_span * 0.5))
            t_max = min(1.0, mid + (min_span * 0.5))
            if t_max - t_min < min_span:
                if t_min <= 0.0:
                    t_max = min(1.0, min_span)
                else:
                    t_min = max(0.0, 1.0 - min_span)
        return t_min, t_max

    @staticmethod
    def _segment_path(path: PathCache, t0: float, t1: float, steps: int = 140) -> QPainterPath:
        seg = QPainterPath()
        if t1 <= t0:
            pt = point_at(path, t0, 0.0)
            seg.moveTo(pt)
            seg.lineTo(pt)
            return seg
        for i in range(steps + 1):
            t = t0 + (t1 - t0) * (i / steps)
            pt = point_at(path, t, 0.0)
            if i == 0:
                seg.moveTo(pt)
            else:
                seg.lineTo(pt)
        return seg

    @staticmethod
    def _effective_module_range(mod: ArcModule, t_min: float, t_max: float) -> tuple[float, float]:
        start = float(mod.t_start)
        end = float(mod.t_end)
        if end < start:
            start, end = end, start

        vis_span = max(0.0, t_max - t_min)
        mod_span = max(0.0, end - start)
        if vis_span <= 0.0:
            return t_min, t_min
        if mod_span >= vis_span:
            return t_min, t_max

        if start < t_min:
            delta = t_min - start
            start += delta
            end += delta
        if end > t_max:
            delta = end - t_max
            start -= delta
            end -= delta

        start = max(t_min, min(t_max, start))
        end = max(t_min, min(t_max, end))
        if end < start:
            end = start
        return start, end

    # ── Maske ────────────────────────────────────────────────────────
    def _update_mask(self) -> None:
        pm = QPixmap(self.size())
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pc = self.cached_path()
        t_min, t_max = self._visible_t_range()
        bp_seg = self._segment_path(pc, t_min, t_max)

        # Basislinie
        pen = QPen(Qt.GlobalColor.white, self.base_thick)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap if self.round_caps else Qt.PenCapStyle.FlatCap)
        p.setPen(pen)
        p.drawPath(bp_seg)

        # Module
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(Qt.GlobalColor.white)
        ht = self.base_thick / 2
        for mod in self.modules:
            if mod.visible:
                old_start, old_end = mod.t_start, mod.t_end
                eff_start, eff_end = self._effective_module_range(mod, t_min, t_max)
                mod.t_start, mod.t_end = eff_start, eff_end
                mp = mod.mask_path(pc, ht)
                mod.t_start, mod.t_end = old_start, old_end
                if mp:
                    p.drawPath(mp)
        p.end()
        self.setMask(pm.mask())

    # ── Zeichnen ─────────────────────────────────────────────────────
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pc = self.cached_path()
        t_min, t_max = self._visible_t_range()
        bp_seg = self._segment_path(pc, t_min, t_max)
        ht = self.base_thick / 2

        # Basisbalken
        pen = QPen(self.base_color, self.base_thick)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap if self.round_caps else Qt.PenCapStyle.FlatCap)
        p.setPen(pen)
        p.drawPath(bp_seg)

        # Kanten-Highlights
        p.setPen(QPen(QColor(80, 180, 255, 40), 1))
        for off in [ht, -ht]:
            edge = QPainterPath()
            for i in range(101):
                t = t_min + (t_max - t_min) * (i / 100.0)
                pt = point_at(pc, t, off)
                edge.moveTo(pt) if i == 0 else edge.lineTo(pt)
            p.drawPath(edge)

        # Module zeichnen
        for mod in self.modules:
            if mod.visible:
                old_start, old_end = mod.t_start, mod.t_end
                eff_start, eff_end = self._effective_module_range(mod, t_min, t_max)
                mod.t_start, mod.t_end = eff_start, eff_end
                mod.paint(p, pc, ht)
                mod.t_start, mod.t_end = old_start, old_end

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
                "arc_left_trim_pct": self.arc_left_trim_pct,
                "arc_right_trim_pct": self.arc_right_trim_pct,
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
                   "ctrl_x", "ctrl_y_extra", "base_thick", "round_caps",
                   "arc_left_trim_pct", "arc_right_trim_pct"):
            if k in arc:
                setattr(self, k, arc[k])
        if "base_color" in arc:
            self.base_color = QColor(*arc["base_color"])
            self.base_color.setAlpha(255) # Erzwinge 100% Opaque-Hintergrund für den neuen Global-Slider
        self.modules = [ArcModule.from_dict(d) for d in data.get("modules", [])]
        self.resize(self.win_w, self.win_h)
        self.rebuild()
