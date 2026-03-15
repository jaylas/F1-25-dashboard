from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from models import LAP_AVERAGING_BUCKET_METRES
from widgets.graph import InputGraphWidget


class ReviewWindow(QWidget):
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

        self._build_ui()
        self.update_laps(historical_laps)

    @staticmethod
    def _fit_graph_to_samples(graph: InputGraphWidget, samples: list[tuple[float, float]]) -> None:
        if not samples:
            return
        dists = [d for d, _ in samples]
        min_d = min(dists)
        max_d = max(dists)
        center = (min_d + max_d) / 2.0
        span = max(200.0, (max_d - min_d) + 100.0)
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

        toolbar.addStretch(1)
        root.addLayout(toolbar)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        root.addWidget(self.scroll_area, stretch=1)

        self.graphs_container = QWidget()
        self.graphs_layout = QVBoxLayout(self.graphs_container)

        self.graphs_container.setMinimumWidth(3000)

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

        # Capture wheel events before QScrollArea consumes them.
        for graph in (self.brake_graph, self.throttle_graph, self.gear_graph):
            graph.installEventFilter(self)

    def _all_graphs(self) -> list[InputGraphWidget]:
        return [self.brake_graph, self.throttle_graph, self.gear_graph]

    def _apply_zoom_factor(self, factor: float) -> None:
        target = self.brake_graph.window_metres * factor
        for graph in self._all_graphs():
            graph.window_metres = target
            graph.update()

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
        return super().eventFilter(watched, event)

    def update_laps(self, historical_laps: list[dict]) -> None:
        self.historical_laps = historical_laps
        idx = self.lap_combo.currentIndex()
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

        self.lap_combo.blockSignals(False)
        self._on_lap_selected(self.lap_combo.currentIndex())

    def _update_visibility(self) -> None:
        self.brake_graph.setVisible(self.cb_brake.isChecked())
        self.throttle_graph.setVisible(self.cb_throttle.isChecked())
        self.gear_graph.setVisible(self.cb_gear.isChecked())

    def _on_lap_selected(self, index: int) -> None:
        if index < 0 or not self.historical_laps:
            return

        text = self.lap_combo.itemText(index)

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
                b_data = list(lap.get("brake", []))
                t_data = list(lap.get("throttle", []))
                g_data = list(lap.get("gear", []))

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

        self._fit_graph_to_samples(self.brake_graph, b_data)
        self._fit_graph_to_samples(self.throttle_graph, t_data)
        self._fit_graph_to_samples(self.gear_graph, g_data)

        self.brake_graph.update()
        self.throttle_graph.update()
        self.gear_graph.update()
