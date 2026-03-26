from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.models import LAP_AVERAGING_BUCKET_METRES
from ui.widgets.graph import InputGraphWidget


LAP_DEBUG = True


class ReviewWindow(QWidget):
    SINGLE_LAP_BUCKET_METRES = 2.0

    @staticmethod
    def _lap_dbg(message: str) -> None:
        if LAP_DEBUG:
            print(f"[LAP-DEBUG][Review] {message}")

    def __init__(
        self,
        historical_laps: list[dict],
        ref_brake: list,
        ref_throttle: list,
        ref_gear: list,
    ):
        super().__init__()
        self.setWindowTitle("Lap Review Data")
        self.resize(1000, 600)

        self.historical_laps = historical_laps
        self.ref_brake = ref_brake
        self.ref_throttle = ref_throttle
        self.ref_gear = ref_gear
        self._middle_drag_active = False
        self._middle_drag_last_global: QPoint | None = None

        self._build_ui()
        self._max_track_dist = 5000.0  # Default until data loaded
        self._current_zoom_m = 5000.0
        self._is_full_view = True
        self.update_laps(historical_laps)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if getattr(self, "_is_full_view", False):
            self._current_zoom_m = self._max_track_dist
        self._sync_container_width()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if getattr(self, "_is_full_view", True):
            self._zoom_full_view()

    def _sync_container_width(self) -> None:
        if not hasattr(self, "_max_track_dist") or self._max_track_dist <= 0:
            return

        # Target meters to show in viewport
        margin_left = 38
        margin_right = 10
        visible_width = self.scroll_area.viewport().width() - margin_left - margin_right

        if visible_width <= 0:
            return

        # Clamp zoom: prevent zooming out further than full lap
        if self._current_zoom_m > self._max_track_dist:
            self._current_zoom_m = self._max_track_dist
            self._is_full_view = True

        # Minimum zoom
        if self._current_zoom_m < 50.0:
            self._current_zoom_m = 50.0

        # pixels_per_meter = visible_width / current_zoom_m
        # total_width = (max_track_dist * pixels_per_meter) + margins
        pixels_per_m = visible_width / self._current_zoom_m
        total_width = (self._max_track_dist * pixels_per_m) + margin_left + margin_right

        # Block signals to avoid recursive updates if any
        self.graphs_container.setFixedWidth(int(total_width))

        for graph in self._all_graphs():
            graph.window_metres = self._max_track_dist
            graph.update()

    def update_references(
        self,
        ref_brake: list[tuple[float, float]],
        ref_throttle: list[tuple[float, float]],
        ref_gear: list[tuple[float, float]],
        offset: float = 0.0,
    ) -> None:
        self.ref_brake = list(ref_brake)
        self.ref_throttle = list(ref_throttle)
        self.ref_gear = list(ref_gear)

        if self.ref_brake:
            self.brake_graph.set_reference(self.ref_brake, 0.0)
            self.brake_graph._ref_offset = offset
        else:
            self.brake_graph.clear_reference()

        if self.ref_throttle:
            self.throttle_graph.set_reference(self.ref_throttle, 0.0)
            self.throttle_graph._ref_offset = offset
        else:
            self.throttle_graph.clear_reference()

        if self.ref_gear:
            self.gear_graph.set_reference(self.ref_gear, 0.0)
            self.gear_graph._ref_offset = offset
        else:
            self.gear_graph.clear_reference()

        self.brake_graph.update()
        self.throttle_graph.update()
        self.gear_graph.update()

    @staticmethod
    def _downsample_samples(
        samples: list[tuple[float, float]],
        bucket_size: float,
        mode: str,
    ) -> list[tuple[float, float]]:
        if not samples or bucket_size <= 0:
            return samples
        buckets: dict[int, list[float]] = {}
        for dist, value in samples:
            b_idx = int(dist / bucket_size)
            buckets.setdefault(b_idx, []).append(value)

        out: list[tuple[float, float]] = []
        for b_idx in sorted(buckets.keys()):
            dist = b_idx * bucket_size
            values = buckets[b_idx]
            avg = sum(values) / len(values)
            if mode == "gear":
                out.append((dist, float(round(avg))))
            else:
                out.append((dist, avg))
        return out

    def _fit_all_graphs_to_span(self, datasets: list[list[tuple[float, float]]]) -> None:
        dists = [d for dataset in datasets for d, _ in dataset]
        if self.ref_brake:
            dists.extend(d for d, _ in self.ref_brake)
        if self.ref_throttle:
            dists.extend(d for d, _ in self.ref_throttle)
        if self.ref_gear:
            dists.extend(d for d, _ in self.ref_gear)

        if not dists:
            return

        self._max_track_dist = max(dists)

        if getattr(self, "_is_full_view", True):
            self._current_zoom_m = self._max_track_dist
            self._zoom_full_view()
        else:
            self._sync_container_width()

    def _zoom_full_view(self) -> None:
        self._is_full_view = True
        self._current_zoom_m = self._max_track_dist
        self._sync_container_width()
        self.scroll_area.horizontalScrollBar().setValue(0)

    @staticmethod
    def _fit_graph_to_samples(graph: InputGraphWidget, samples: list[tuple[float, float]]) -> None:
        if not samples:
            return
        dists = [d for d, _ in samples]
        min_d = min(dists)
        max_d = max(dists)
        center = (min_d + max_d) / 2.0
        span = max(200.0, (max_d - min_d) + 100.0)
        # Allow complete zoom-out for long tracks in review mode.
        graph.MAX_WINDOW = max(graph.MAX_WINDOW, span + 200.0)
        graph.window_metres = span
        graph._current_distance = center

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Select Lap:"))

        self.lap_combo = QComboBox()
        self.lap_combo.currentIndexChanged.connect(self._on_lap_selected)
        toolbar.addWidget(self.lap_combo)

        toolbar.addSpacing(20)

        self.cb_brake = QCheckBox("Brake")
        self.cb_brake.setChecked(True)
        self.cb_brake.toggled.connect(self._update_visibility)
        toolbar.addWidget(self.cb_brake)

        self.cb_throttle = QCheckBox("Throttle")
        self.cb_throttle.setChecked(True)
        self.cb_throttle.toggled.connect(self._update_visibility)
        toolbar.addWidget(self.cb_throttle)

        self.cb_gear = QCheckBox("Gear")
        self.cb_gear.setChecked(True)
        self.cb_gear.toggled.connect(self._update_visibility)
        toolbar.addWidget(self.cb_gear)

        self.full_view_btn = QPushButton("Full View")
        self.full_view_btn.clicked.connect(self._zoom_full_view)
        toolbar.addWidget(self.full_view_btn)

        self.export_btn = QPushButton("Export Coaching")
        self.export_btn.clicked.connect(self._export_coaching)
        toolbar.addWidget(self.export_btn)

        toolbar.addStretch(1)
        root.addLayout(toolbar)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        root.addWidget(self.scroll_area, stretch=1)

        self.graphs_container = QWidget()
        self.graphs_layout = QVBoxLayout(self.graphs_container)
        self.graphs_layout.setContentsMargins(0, 0, 0, 0)
        self.graphs_layout.setSpacing(0)

        self.brake_graph = InputGraphWidget(
            color_base=(232, 42, 42),
            color_live=(232, 60, 60),
            parent=self.graphs_container,
        )
        self.brake_graph.setMinimumHeight(150)
        if self.ref_brake:
            self.brake_graph.set_reference(self.ref_brake, 0)
        self.graphs_layout.addWidget(self.brake_graph)

        self.throttle_graph = InputGraphWidget(
            color_base=(42, 232, 80),
            color_live=(60, 232, 100),
            parent=self.graphs_container,
        )
        self.throttle_graph.setMinimumHeight(150)
        if self.ref_throttle:
            self.throttle_graph.set_reference(self.ref_throttle, 0)
        self.graphs_layout.addWidget(self.throttle_graph)

        self.gear_graph = InputGraphWidget(
            color_base=(232, 180, 42),
            color_live=(255, 210, 60),
            value_mode="gear",
            parent=self.graphs_container,
        )
        self.gear_graph.setMinimumHeight(100)
        if self.ref_gear:
            self.gear_graph.set_reference(self.ref_gear, 0)
        self.graphs_layout.addWidget(self.gear_graph)

        self.scroll_area.setWidget(self.graphs_container)

        for graph in (self.brake_graph, self.throttle_graph, self.gear_graph):
            graph._lock_start_at_zero = True
            graph.MAX_WINDOW = float("inf")

        # Capture wheel events before QScrollArea consumes them.
        for graph in (self.brake_graph, self.throttle_graph, self.gear_graph):
            graph.installEventFilter(self)

    def _all_graphs(self) -> list[InputGraphWidget]:
        return [self.brake_graph, self.throttle_graph, self.gear_graph]

    def _apply_zoom_factor(self, factor: float) -> None:
        hbar = self.scroll_area.horizontalScrollBar()
        old_val = hbar.value()
        old_max = hbar.maximum()
        v_width = self.scroll_area.viewport().width()

        # Relative center position to preserve zoom point
        rel_center = (old_val + v_width / 2) / (old_max + v_width) if (old_max + v_width) > 0 else 0.5

        self._is_full_view = False
        self._current_zoom_m *= factor
        self._sync_container_width()

        new_max = hbar.maximum()
        new_val = int(rel_center * (new_max + v_width) - v_width / 2)
        hbar.setValue(new_val)

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return
        factor = 0.9 if delta > 0 else 1.1
        self._apply_zoom_factor(factor)
        event.accept()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Wheel and watched in self._all_graphs():
            delta = event.angleDelta().y()
            if delta != 0:
                factor = 0.9 if delta > 0 else 1.1
                self._apply_zoom_factor(factor)
            event.accept()
            return True
        if watched in self._all_graphs() and event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.MiddleButton:
                self._is_full_view = False
                self._middle_drag_active = True
                self._middle_drag_last_global = event.globalPosition().toPoint()
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                return True
        if watched in self._all_graphs() and event.type() == QEvent.Type.MouseMove:
            if (
                self._middle_drag_active
                and event.buttons() & Qt.MouseButton.MiddleButton
                and self._middle_drag_last_global is not None
            ):
                self._is_full_view = False
                current = event.globalPosition().toPoint()
                delta = current - self._middle_drag_last_global
                self._middle_drag_last_global = current
                hbar = self.scroll_area.horizontalScrollBar()
                vbar = self.scroll_area.verticalScrollBar()
                if hbar is not None:
                    hbar.setValue(hbar.value() - delta.x())
                if vbar is not None:
                    vbar.setValue(vbar.value() - delta.y())
                event.accept()
                return True
        if watched in self._all_graphs() and event.type() == QEvent.Type.MouseButtonRelease:
            if event.button() == Qt.MouseButton.MiddleButton:
                self._middle_drag_active = False
                self._middle_drag_last_global = None
                self.unsetCursor()
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def update_laps(self, historical_laps: list[dict]) -> None:
        self.historical_laps = historical_laps
        idx = self.lap_combo.currentIndex()
        self._lap_dbg(
            f"update_laps called: incoming={len(self.historical_laps)}, old_combo_idx={idx}, "
            f"old_combo_count={self.lap_combo.count()}"
        )
        self.lap_combo.blockSignals(True)
        self.lap_combo.clear()

        if not self.historical_laps:
            self.lap_combo.addItem("No laps recorded")
        else:
            for i in range(len(self.historical_laps)):
                self.lap_combo.addItem(f"Lap {i + 1}")
            if len(self.historical_laps) > 1:
                self.lap_combo.addItem("Average Lap")

        if idx >= 0 and idx < self.lap_combo.count():
            self.lap_combo.setCurrentIndex(idx)
        else:
            self.lap_combo.setCurrentIndex(0)

        self._lap_dbg(
            f"update_laps rebuilt combo_count={self.lap_combo.count()}, "
            f"new_idx={self.lap_combo.currentIndex()}, current_text='{self.lap_combo.currentText()}'"
        )

        self.lap_combo.blockSignals(False)
        self._on_lap_selected(self.lap_combo.currentIndex())

    def _update_visibility(self) -> None:
        self.brake_graph.setVisible(self.cb_brake.isChecked())
        self.throttle_graph.setVisible(self.cb_throttle.isChecked())
        self.gear_graph.setVisible(self.cb_gear.isChecked())

    def _on_lap_selected(self, index: int) -> None:
        if index < 0 or not self.historical_laps:
            self._lap_dbg(f"on_lap_selected ignored: index={index}, laps={len(self.historical_laps)}")
            return

        text = self.lap_combo.itemText(index)
        self._lap_dbg(f"on_lap_selected index={index}, text='{text}', laps={len(self.historical_laps)}")

        self.brake_graph.clear_historical()
        self.throttle_graph.clear_historical()
        self.gear_graph.clear_historical()
        self.brake_graph._live_samples.clear()
        self.throttle_graph._live_samples.clear()
        self.gear_graph._live_samples.clear()

        b_data, t_data, g_data = [], [], []

        if text.startswith("Lap "):
            lap_idx = int(text.split(" ")[1]) - 1
            if 0 <= lap_idx < len(self.historical_laps):
                lap = self.historical_laps[lap_idx]
                b_data = self._downsample_samples(
                    list(lap.get("brake", [])),
                    self.SINGLE_LAP_BUCKET_METRES,
                    mode="percent",
                )
                t_data = self._downsample_samples(
                    list(lap.get("throttle", [])),
                    self.SINGLE_LAP_BUCKET_METRES,
                    mode="percent",
                )
                g_data = self._downsample_samples(
                    list(lap.get("gear", [])),
                    self.SINGLE_LAP_BUCKET_METRES,
                    mode="gear",
                )

        elif text == "Average Lap":
            bucket_size = LAP_AVERAGING_BUCKET_METRES
            buckets: dict[int, dict[str, list[float]]] = {}
            for lap in self.historical_laps:
                for dist, brake in lap.get("brake", []):
                    b_idx = int(dist / bucket_size)
                    buckets.setdefault(b_idx, {"b": [], "t": [], "g": []})["b"].append(brake)
                for dist, throttle in lap.get("throttle", []):
                    b_idx = int(dist / bucket_size)
                    buckets.setdefault(b_idx, {"b": [], "t": [], "g": []})["t"].append(throttle)
                for dist, gear in lap.get("gear", []):
                    b_idx = int(dist / bucket_size)
                    buckets.setdefault(b_idx, {"b": [], "t": [], "g": []})["g"].append(gear)

            for b_idx in sorted(buckets.keys()):
                dist = b_idx * bucket_size
                b_list = buckets[b_idx]["b"]
                if b_list:
                    b_data.append((dist, sum(b_list) / len(b_list)))
                t_list = buckets[b_idx]["t"]
                if t_list:
                    t_data.append((dist, sum(t_list) / len(t_list)))
                g_list = buckets[b_idx]["g"]
                if g_list:
                    g_data.append((dist, sum(g_list) / len(g_list)))

        self.brake_graph._live_samples = b_data
        self.throttle_graph._live_samples = t_data
        self.gear_graph._live_samples = g_data

        self._lap_dbg(
            f"selected data sizes: brake={len(b_data)}, throttle={len(t_data)}, gear={len(g_data)}"
        )

        self._fit_all_graphs_to_span([b_data, t_data, g_data])

        self.brake_graph.update()
        self.throttle_graph.update()
        self.gear_graph.update()

    def _export_coaching(self) -> None:
        if not self.historical_laps:
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Coaching Data",
            "coaching_export.txt",
            "Text Files (*.txt)",
        )
        if not path:
            return

        # --- Build Average Lap ---
        bucket_size = LAP_AVERAGING_BUCKET_METRES
        buckets: dict[int, dict[str, list[float]]] = {}
        for lap in self.historical_laps:
            for dist, brake in lap.get("brake", []):
                b_idx = int(dist / bucket_size)
                buckets.setdefault(b_idx, {"b": [], "t": [], "g": []})[
                    "b"
                ].append(brake)
            for dist, throttle in lap.get("throttle", []):
                b_idx = int(dist / bucket_size)
                buckets.setdefault(b_idx, {"b": [], "t": [], "g": []})[
                    "t"
                ].append(throttle)
            for dist, gear in lap.get("gear", []):
                b_idx = int(dist / bucket_size)
                buckets.setdefault(b_idx, {"b": [], "t": [], "g": []})[
                    "g"
                ].append(gear)

        avg_rows: list[tuple[float, float, float, int]] = []
        for b_idx in sorted(buckets.keys()):
            dist = b_idx * bucket_size
            bl = buckets[b_idx]["b"]
            tl = buckets[b_idx]["t"]
            gl = buckets[b_idx]["g"]
            avg_b = sum(bl) / len(bl) if bl else 0.0
            avg_t = sum(tl) / len(tl) if tl else 0.0
            avg_g = round(sum(gl) / len(gl)) if gl else 1
            avg_rows.append((dist, avg_b, avg_t, avg_g))

        # --- Build Reference data ---
        ref_map_b = {d: v for d, v in self.ref_brake} if self.ref_brake else {}
        ref_map_t = {d: v for d, v in self.ref_throttle} if self.ref_throttle else {}
        ref_map_g = {d: v for d, v in self.ref_gear} if self.ref_gear else {}
        ref_dists = sorted(set(ref_map_b.keys()) | set(ref_map_t.keys()) | set(ref_map_g.keys()))

        lines: list[str] = []
        lines.append("=== F1 COACHING ANALYSIS ===")
        lines.append(f"Laps analyzed: {len(self.historical_laps)}")
        lines.append("")
        lines.append("--- PROMPT ---")
        lines.append(
            "You are a professional F1 driving coach. Analyze the following telemetry "
            "data and provide specific, actionable coaching tips. Compare the driver's "
            '"Average Lap" with the "Reference Lap" (ideal line). Focus on:'
        )
        lines.append("1. Brake points (too early/late, too hard/soft)")
        lines.append("2. Throttle application (traction, smoothness)")
        lines.append("3. Gear selection and shift points")
        lines.append("4. Sector-by-sector analysis")
        lines.append(
            "5. Overall lap time potential and where the biggest gains are"
        )
        lines.append("")
        lines.append(
            "Both datasets use the same format: Distance in meters, "
            "Brake 0-1, Throttle 0-1, Gear 1-8."
        )
        lines.append(
            "Higher brake values mean harder braking. A value of 1.0 is full brake/throttle."
        )
        lines.append("")

        # --- Reference Lap Section ---
        lines.append("--- REFERENCE LAP DATA ---")
        lines.append("Distance(m), Brake(0-1), Throttle(0-1), Gear")
        for d in ref_dists:
            b = ref_map_b.get(d, 0.0)
            t = ref_map_t.get(d, 0.0)
            g = int(ref_map_g.get(d, 1))
            lines.append(f"{d:.1f}, {b:.3f}, {t:.3f}, {g}")
        lines.append("")

        # --- Average Lap Section ---
        lines.append("--- AVERAGE LAP DATA ---")
        lines.append("Distance(m), Brake(0-1), Throttle(0-1), Gear")
        for dist, avg_b, avg_t, avg_g in avg_rows:
            lines.append(f"{dist:.1f}, {avg_b:.3f}, {avg_t:.3f}, {avg_g}")
        lines.append("")

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except OSError:
            return
