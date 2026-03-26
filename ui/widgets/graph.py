import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QCursor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget


class InputGraphWidget(QWidget):
    """Track-distance input graph (brake/throttle) with ideal overlay."""

    MIN_WINDOW = 200.0
    MAX_WINDOW = 3000.0
    DEFAULT_WINDOW = 800.0

    def __init__(
        self,
        color_base: tuple[int, int, int],
        color_live: tuple[int, int, int],
        value_mode: str = "percent",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setMinimumHeight(80)
        self._window_m = self.DEFAULT_WINDOW

        self._color_base = color_base
        self._color_live = color_live
        self._value_mode = value_mode

        self._live_samples: list[tuple[float, float]] = []

        self._historical_datasets: dict[str, list[tuple[float, float]]] = {}

        self._current_distance: float = 0.0
        self._last_lap_number: int = -1
        self._lock_start_at_zero: bool = False

        self._ref_samples: list[tuple[float, float]] = []
        self._track_length: float = 0.0
        self._ref_offset: float = 0.0

    @property
    def window_metres(self) -> float:
        return self._window_m

    @window_metres.setter
    def window_metres(self, val: float) -> None:
        if not math.isfinite(val):
            return
        self._window_m = max(self.MIN_WINDOW, min(self.MAX_WINDOW, val))

    def set_reference(self, samples: list[tuple[float, float]], length: float) -> None:
        self._ref_samples = sorted(samples, key=lambda x: x[0])
        self._track_length = length

    def clear_reference(self) -> None:
        self._ref_samples = []
        self._track_length = 0.0
        self.update()

    def set_historical(self, label: str, samples: list[tuple[float, float]]) -> None:
        self._historical_datasets[label] = sorted(samples, key=lambda x: x[0])

    def clear_historical(self, label: str | None = None) -> None:
        if label:
            self._historical_datasets.pop(label, None)
        else:
            self._historical_datasets.clear()

    def add_sample(self, dist: float, brake: float, lap: int) -> None:
        if lap != self._last_lap_number:
            self._live_samples.clear()
            self._last_lap_number = lap
        elif (
            self._live_samples
            and dist < self._current_distance - 500
            and dist < 200.0
            and self._current_distance > 400.0
        ):
            self._live_samples.clear()

        self._current_distance = dist
        self._live_samples.append((dist, brake))

    def _value_to_y(self, value: float, gy: float, gh: float) -> float:
        if self._value_mode == "gear":
            clamped = max(1.0, min(8.0, value))
            normalized = (clamped - 1.0) / 7.0
            return gy + gh - normalized * gh
        clamped = max(0.0, min(1.0, value))
        return gy + gh - clamped * gh

    def _build_path(
        self,
        samples: list[tuple[float, float]],
        gx: float,
        gy: float,
        gw: float,
        gh: float,
        vs: float,
        ve: float,
    ) -> tuple[QPainterPath, QPainterPath] | None:
        span = ve - vs
        if span <= 0:
            return None
        path = QPainterPath()
        fill = QPainterPath()
        first = True
        for d, b in samples:
            if d < vs or d > ve:
                continue
            x = gx + ((d - vs) / span) * gw
            y = self._value_to_y(b, gy, gh)
            pt = QPointF(x, y)
            if first:
                path.moveTo(pt)
                fill.moveTo(QPointF(x, gy + gh))
                fill.lineTo(pt)
                first = False
            else:
                if self._value_mode == "gear":
                    prev_y = path.currentPosition().y()
                    path.lineTo(QPointF(x, prev_y))
                    path.lineTo(pt)
                    fill.lineTo(QPointF(x, prev_y))
                    fill.lineTo(pt)
                else:
                    path.lineTo(pt)
                    fill.lineTo(pt)
        if first:
            return None
        fill.lineTo(QPointF(path.currentPosition().x(), gy + gh))
        fill.closeSubpath()
        return path, fill

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w, h = self.width(), self.height()
        ml, mr, mt, mb = 38, 10, 8, 22
        gx, gy = ml, mt
        gw = w - ml - mr
        gh = h - mt - mb

        if gw < 10 or gh < 10:
            p.end()
            return

        if getattr(self, "_lock_start_at_zero", False):
            vs = 0.0
            ve = self._window_m
        else:
            half = self._window_m / 2
            vs = self._current_distance - half
            ve = self._current_distance + half

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(18, 18, 22, 255))
        p.drawRoundedRect(QRectF(gx - 2, gy - 2, gw + 4, gh + 4), 6, 6)

        p.setClipRect(QRectF(gx, gy, gw, gh))

        p.setFont(QFont("Segoe UI", 7))
        if self._value_mode == "gear":
            for gear in range(1, 9):
                y = self._value_to_y(float(gear), gy, gh)
                p.setPen(QPen(QColor(255, 255, 255, 30), 1))
                p.drawLine(QPointF(gx, y), QPointF(gx + gw, y))
        else:
            for pct in (0, 25, 50, 75, 100):
                y = gy + gh - (pct / 100.0) * gh
                p.setPen(QPen(QColor(255, 255, 255, 30), 1))
                p.drawLine(QPointF(gx, y), QPointF(gx + gw, y))

        span = ve - vs
        if span > 0:
            min_px = 60
            raw_step = (span * min_px) / max(gw, 1)
            for nice in (50, 100, 200, 500, 1000, 2000):
                if nice >= raw_step:
                    step = nice
                    break
            else:
                step = 2000
            first_mark = int(vs / step) * step
            for mark in range(first_mark, int(ve) + step, step):
                if mark < vs or mark > ve:
                    continue
                x = gx + ((mark - vs) / span) * gw
                p.setPen(QPen(QColor(255, 255, 255, 20), 1))
                p.drawLine(QPointF(x, gy), QPointF(x, gy + gh))

        cx = gx + gw / 2
        p.setPen(QPen(QColor(255, 255, 255, 60), 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(cx, gy), QPointF(cx, gy + gh))

        if self._ref_samples:
            shifted = [(d + self._ref_offset, b) for d, b in self._ref_samples]
            r = self._build_path(shifted, gx, gy, gw, gh, vs, ve)
            if r:
                rp, rf = r
                g = QLinearGradient(0, gy, 0, gy + gh)
                g.setColorAt(0.0, QColor(160, 160, 160, 80))
                g.setColorAt(1.0, QColor(160, 160, 160, 10))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(g)
                p.drawPath(rf)
                p.setPen(QPen(QColor(180, 180, 180, 180), 1.5))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(rp)

        for ds in self._historical_datasets.values():
            if len(ds) >= 2:
                r = self._build_path(ds, gx, gy, gw, gh, vs, ve)
                if r:
                    hp, hf = r
                    g = QLinearGradient(0, gy, 0, gy + gh)
                    br, bg, bb = self._color_base
                    g.setColorAt(0.0, QColor(br, bg, bb, 50))
                    g.setColorAt(1.0, QColor(br, bg, bb, 5))
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(g)
                    p.drawPath(hf)
                    p.setPen(QPen(QColor(br, bg, bb, 140), 1.5, Qt.PenStyle.DashLine))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawPath(hp)

        if len(self._live_samples) >= 2:
            r = self._build_path(self._live_samples, gx, gy, gw, gh, vs, ve)
            if r:
                lp, lf = r
                g = QLinearGradient(0, gy, 0, gy + gh)
                br, bg, bb = self._color_base
                lr, lg, lb = self._color_live
                g.setColorAt(0.0, QColor(br, bg, bb, 160))
                g.setColorAt(1.0, QColor(br, bg, bb, 20))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(g)
                p.drawPath(lf)
                p.setPen(QPen(QColor(lr, lg, lb, 240), 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(lp)

        p.setClipping(False)

        if self._value_mode == "gear":
            for gear in range(1, 9):
                y = self._value_to_y(float(gear), gy, gh)
                p.setPen(QColor(180, 180, 180, 180))
                p.drawText(
                    QRectF(0, y - 7, ml - 4, 14),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    f"G{gear}",
                )
        else:
            for pct in (0, 25, 50, 75, 100):
                y = gy + gh - (pct / 100.0) * gh
                p.setPen(QColor(180, 180, 180, 180))
                p.drawText(
                    QRectF(0, y - 7, ml - 4, 14),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    f"{pct}%",
                )

        if span > 0:
            min_px = 60
            raw_step = (span * min_px) / max(gw, 1)
            nice_steps = (50, 100, 200, 500, 1000, 2000)
            for nice in nice_steps:
                if nice >= raw_step:
                    step = nice
                    break
            else:
                step = nice_steps[-1]
            first_mark = int(vs / step) * step
            for mark in range(first_mark, int(ve) + step, step):
                if mark < vs or mark > ve or mark < 0:
                    continue
                x = gx + ((mark - vs) / span) * gw
                p.setPen(QColor(150, 150, 150, 160))
                label = f"{mark}m"
                p.drawText(
                    QRectF(x - 24, gy + gh + 3, 48, 16),
                    Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                    label,
                )

        p.end()
