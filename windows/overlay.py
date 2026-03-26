import json
from pathlib import Path

from PyQt6.QtCore import QEvent, QPoint, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QCursor, QPainter
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from models import REFS_DIR, TelemetryFrame, UDP_PORT
from telemetry import TelemetryListener
from widgets.graph import InputGraphWidget
from widgets.arc import ArcOverlayWidget, ArcSettingsWindow
from widgets.arc.modules import GraphModule
from windows.review import ReviewWindow

ARC_CONFIG_FILE = "arc_config.json"


_RESIZE_MARGIN = 8
TRACK_REF_ALIASES = {
    "lusail": "losail",
}
LAP_DEBUG = True


class OverlayWindow(QWidget):
    @staticmethod
    def _lap_dbg(message: str) -> None:
        if LAP_DEBUG:
            print(f"[LAP-DEBUG][Overlay] {message}")

    @staticmethod
    def _normalize_gear_value(raw: float) -> float:
        # Accept common gear encodings from external telemetry exports.
        if raw <= 0.0:
            return 1.0
        if raw <= 1.0:
            return max(1.0, min(8.0, round(raw * 8.0)))
        if raw <= 8.0:
            return max(1.0, min(8.0, round(raw)))
        return max(1.0, min(8.0, round(raw / 12.5)))

    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("F1 25 Brake Overlay")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMinimumSize(320, 200)
        self.resize(520, 320)
        self.setMouseTracking(True)

        screen = QApplication.primaryScreen()
        if screen:
            sg = screen.availableGeometry()
            self.move(sg.right() - self.width() - 20, sg.top() + 20)

        self.telemetry = TelemetryListener(port=UDP_PORT)
        self.telemetry.telemetry_received.connect(self.on_telemetry)
        self.telemetry.connection_state_changed.connect(self.on_connection_state)
        self.telemetry.track_changed.connect(self.on_track_changed)

        self.drag_origin: QPoint | None = None
        self._resize_edge: str = ""
        self._resize_origin: QPoint | None = None
        self._resize_geom: QRectF | None = None

        self._ui_visible = True
        self._bg_opacity = 255
        self.setWindowOpacity(self._bg_opacity / 255.0)

        self.current_brake = 0.0
        self.current_throttle = 0.0
        self.current_lap_distance = 0.0
        self.current_lap_number = 0
        self.current_track_name = ""

        self.recording = False
        self.record_start_lap_number: int | None = None
        self.reference_samples: list[dict[str, float]] = []
        self.reference_track_length = 0.0

        self.historical_laps: list[dict[str, list[tuple[float, float]]]] = []
        self._current_lap: int = -1
        self._current_distance: float = 0.0
        self._wrap_archive_latched: bool = False

        # Arc overlay
        self._arc_overlay: ArcOverlayWidget | None = None
        self._arc_settings: ArcSettingsWindow | None = None
        self._arc_active: bool = False

        self.ui_timer = QTimer(self)
        self.ui_timer.setInterval(16)
        self.ui_timer.timeout.connect(self.update)
        self.ui_timer.start()

        self._build_ui()
        self.telemetry.start()

        self._try_load_default()

    def _try_load_default(self) -> None:
        self.status_label.setText("No reference lap loaded. Tracking live braking only.")

    def _try_load_track_ref(self, track_name: str) -> None:
        refs_root = Path(__file__).resolve().parent.parent / REFS_DIR
        p = refs_root / f"{track_name}.json"
        if not p.exists() and track_name in TRACK_REF_ALIASES:
            p = refs_root / f"{TRACK_REF_ALIASES[track_name]}.json"
        if p.exists():
            self._load_reference_file(str(p))
        else:
            self.brake_graph.clear_reference()
            self.throttle_graph.clear_reference()
            self.status_label.setText(
                f"Track: {track_name} - no ref file found (refs/{track_name}.json). "
                f"Tracking live inputs only."
            )

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(8)

        self.connection_label = QLabel("UDP: waiting")
        self.connection_label.setStyleSheet("color: #d7d7d7; font-size: 13px;")
        top.addWidget(self.connection_label)

        top.addStretch(1)

        self.record_button = QPushButton("Record Ref Lap")
        self.record_button.clicked.connect(self.toggle_recording)
        top.addWidget(self.record_button)

        self.load_button = QPushButton("Load")
        self.load_button.clicked.connect(self.load_reference)
        top.addWidget(self.load_button)

        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self.save_reference)
        top.addWidget(self.save_button)

        self.top_widget = QWidget()
        self.top_widget.setLayout(top)
        root.addWidget(self.top_widget)

        self.mid_widget = QWidget()
        mid_row = QHBoxLayout(self.mid_widget)
        mid_row.setContentsMargins(0, 0, 0, 0)

        labels_vbox = QVBoxLayout()
        self.status_label = QLabel("No reference lap loaded.")
        self.status_label.setStyleSheet("color: #b5b5b5; font-size: 12px;")
        labels_vbox.addWidget(self.status_label)

        mid_row.addLayout(labels_vbox)
        mid_row.addStretch(1)

        self.review_btn = QPushButton("Review Laps")
        self.review_btn.clicked.connect(self._open_review_window)
        mid_row.addWidget(self.review_btn)

        mid_row.addStretch(1)

        self.toggles_widget = QWidget()
        toggles_layout = QVBoxLayout(self.toggles_widget)
        toggles_layout.setContentsMargins(0, 0, 0, 0)

        self.toggle_throttle_btn = QPushButton("Toggle Throttle")
        self.toggle_throttle_btn.clicked.connect(self._toggle_throttle_graph)
        toggles_layout.addWidget(self.toggle_throttle_btn)

        self.toggle_gear_btn = QPushButton("Toggle Gear")
        self.toggle_gear_btn.clicked.connect(self._toggle_gear_graph)
        toggles_layout.addWidget(self.toggle_gear_btn)

        self.toggle_arc_btn = QPushButton("Arc HUD")
        self.toggle_arc_btn.clicked.connect(self._toggle_arc_mode)
        toggles_layout.addWidget(self.toggle_arc_btn)

        self.arc_settings_btn = QPushButton("Arc Settings")
        self.arc_settings_btn.clicked.connect(self._open_arc_settings)
        toggles_layout.addWidget(self.arc_settings_btn)

        mid_row.addWidget(self.toggles_widget)
        root.addWidget(self.mid_widget)

        self.brake_graph = InputGraphWidget(
            color_base=(232, 42, 42),
            color_live=(232, 60, 60),
        )
        root.addWidget(self.brake_graph, stretch=1)

        self.throttle_graph = InputGraphWidget(
            color_base=(42, 232, 80),
            color_live=(60, 232, 100),
        )
        self.throttle_graph.hide()
        root.addWidget(self.throttle_graph, stretch=1)

        self.gear_graph = InputGraphWidget(
            color_base=(232, 180, 42),
            color_live=(255, 210, 60),
            value_mode="gear",
        )
        self.gear_graph.hide()
        root.addWidget(self.gear_graph, stretch=1)

        # Capture wheel events at the overlay level so all graphs zoom together.
        for graph in (self.brake_graph, self.throttle_graph, self.gear_graph):
            graph.installEventFilter(self)

        self.offset_widget = QWidget()
        offset_row = QHBoxLayout(self.offset_widget)
        offset_row.setContentsMargins(0, 0, 0, 0)
        offset_row.setSpacing(4)

        self.offset_label = QLabel("Ref offset: 0m")
        self.offset_label.setStyleSheet("color: rgba(255,255,255,120); font-size: 11px;")
        offset_row.addWidget(self.offset_label)

        offset_row.addStretch(1)

        op_label = QLabel("Opacity:")
        op_label.setStyleSheet("color: rgba(255,255,255,120); font-size: 11px;")
        offset_row.addWidget(op_label)

        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setMinimum(0)
        self.opacity_slider.setMaximum(255)
        self.opacity_slider.setValue(self._bg_opacity)
        self.opacity_slider.setFixedWidth(80)
        self.opacity_slider.valueChanged.connect(self._update_opacity)
        offset_row.addWidget(self.opacity_slider)

        offset_row.addSpacing(10)

        btn_minus = QPushButton("< -1m")
        btn_minus.setFixedWidth(60)
        btn_minus.setAutoRepeat(True)
        btn_minus.setAutoRepeatDelay(400)
        btn_minus.setAutoRepeatInterval(50)
        btn_minus.clicked.connect(lambda: self._adjust_offset(-1))
        offset_row.addWidget(btn_minus)

        btn_plus = QPushButton("+1m >")
        btn_plus.setFixedWidth(60)
        btn_plus.setAutoRepeat(True)
        btn_plus.setAutoRepeatDelay(400)
        btn_plus.setAutoRepeatInterval(50)
        btn_plus.clicked.connect(lambda: self._adjust_offset(1))
        offset_row.addWidget(btn_plus)

        hint = QLabel("Scroll to zoom")
        hint.setStyleSheet("color: rgba(255,255,255,60); font-size: 10px;")
        offset_row.addWidget(hint)

        root.addWidget(self.offset_widget)

        self.setStyleSheet(
            """
            QWidget { background: transparent; }
            QPushButton {
                background: rgba(22,22,22,200);
                color: #ededed;
                border: 1px solid rgba(255,255,255,60);
                border-radius: 6px;
                padding: 5px 8px;
                font-size: 12px;
            }
            QPushButton:hover { border: 1px solid rgba(255,255,255,110); }
            """
        )

    def _toggle_throttle_graph(self) -> None:
        if self.throttle_graph.isVisible():
            self.throttle_graph.hide()
        else:
            self.throttle_graph.show()

    def _adjust_offset(self, delta: float) -> None:
        self.brake_graph._ref_offset += delta
        self.throttle_graph._ref_offset += delta
        off = self.brake_graph._ref_offset
        self.offset_label.setText(f"Ref offset: {off:+.0f}m")

    def _update_opacity(self, value: int) -> None:
        self._bg_opacity = value
        self.setWindowOpacity(value / 255.0)

    def _toggle_gear_graph(self) -> None:
        if self.gear_graph.isVisible():
            self.gear_graph.hide()
        else:
            self.gear_graph.show()

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

    def _open_review_window(self) -> None:
        self._lap_dbg(f"Open review requested, historical_laps={len(self.historical_laps)}")
        current_offset = self.brake_graph._ref_offset
        if not hasattr(self, "review_window") or self.review_window is None:
            self.review_window = ReviewWindow(
                self.historical_laps,
                self.brake_graph._ref_samples,
                self.throttle_graph._ref_samples,
                self.gear_graph._ref_samples,
            )
            self.review_window.update_references(
                self.brake_graph._ref_samples,
                self.throttle_graph._ref_samples,
                self.gear_graph._ref_samples,
                offset=current_offset
            )
            self._lap_dbg(f"Created new ReviewWindow instance with offset {current_offset}")
        else:
            self.review_window.update_references(
                self.brake_graph._ref_samples,
                self.throttle_graph._ref_samples,
                self.gear_graph._ref_samples,
                offset=current_offset
            )
            self.review_window.update_laps(self.historical_laps)
            self._lap_dbg(f"Updated existing ReviewWindow references and laps with offset {current_offset}")
        if hasattr(self.review_window, "lap_combo"):
            self._lap_dbg(
                f"Review combo items={self.review_window.lap_combo.count()}, "
                f"current_index={self.review_window.lap_combo.currentIndex()}"
            )
        self.review_window.show()
        self.review_window.raise_()

    def on_connection_state(self, connected: bool) -> None:
        self.connection_label.setText("UDP: connected" if connected else "UDP: waiting")
        self.connection_label.setStyleSheet(
            "color: #78ff8a; font-size: 13px;"
            if connected
            else "color: #d7d7d7; font-size: 13px;"
        )

    def on_track_changed(self, track_id: int, track_name: str) -> None:
        self.current_track_name = track_name
        self._try_load_track_ref(track_name)

    def on_telemetry(self, frame: TelemetryFrame) -> None:
        prev_lap = self._current_lap
        prev_dist = self._current_distance
        dist_drop = prev_dist - frame.lap_distance
        lap_changed = prev_lap >= 0 and frame.lap_number != prev_lap
        if frame.lap_distance > 300.0:
            self._wrap_archive_latched = False
        # Handle both normal lap number increment and wrapped lap distance packets.
        wrapped_distance = (
            dist_drop > 200.0
            and frame.lap_distance < 200.0
            and not self._wrap_archive_latched
        )

        if lap_changed or wrapped_distance:
            self._lap_dbg(
                "boundary detected: "
                f"prev_lap={prev_lap}, frame_lap={frame.lap_number}, "
                f"prev_dist={prev_dist:.1f}, new_dist={frame.lap_distance:.1f}, "
                f"drop={dist_drop:.1f}, lap_changed={lap_changed}, wrapped={wrapped_distance}, "
                f"live_samples={len(self.brake_graph._live_samples)}, archived={len(self.historical_laps)}"
            )

        if (lap_changed or wrapped_distance) and len(self.brake_graph._live_samples) > 50:
            b_live = list(self.brake_graph._live_samples)
            t_live = list(self.throttle_graph._live_samples)
            g_live = list(self.gear_graph._live_samples)
            self.historical_laps.append(
                {
                    "brake": b_live,
                    "throttle": t_live,
                    "gear": g_live,
                }
            )
            if wrapped_distance:
                self._wrap_archive_latched = True
            self.status_label.setText(
                f"Archived lap {len(self.historical_laps)} (Live tracking lap {frame.lap_number})"
            )
            b_first = b_live[0][0] if b_live else -1.0
            b_last = b_live[-1][0] if b_live else -1.0
            self._lap_dbg(
                f"archived lap_idx={len(self.historical_laps) - 1}, "
                f"brake_samples={len(b_live)}, dist_first={b_first:.1f}, dist_last={b_last:.1f}, "
                f"latch={self._wrap_archive_latched}"
            )
            if wrapped_distance and not lap_changed:
                self.brake_graph._live_samples.clear()
                self.throttle_graph._live_samples.clear()
                self.gear_graph._live_samples.clear()
                self._lap_dbg("cleared live samples after wrapped archive (lap counter unchanged)")
            if hasattr(self, "review_window") and self.review_window is not None:
                self.review_window.update_laps(self.historical_laps)
                self._lap_dbg(
                    f"review_window refreshed after archive, combo_items={self.review_window.lap_combo.count()}"
                )
        elif lap_changed or wrapped_distance:
            self._lap_dbg(
                f"archive skipped due to low sample count: {len(self.brake_graph._live_samples)}"
            )

        self._current_lap = frame.lap_number
        self._current_distance = frame.lap_distance

        self.current_brake = frame.brake
        self.current_throttle = frame.throttle
        self.current_lap_distance = frame.lap_distance
        self.current_lap_number = frame.lap_number

        self.brake_graph.add_sample(frame.lap_distance, frame.brake, frame.lap_number)
        self.throttle_graph.add_sample(frame.lap_distance, frame.throttle, frame.lap_number)
        gear_value = float(max(1, min(8, frame.gear)))
        self.gear_graph.add_sample(frame.lap_distance, gear_value, frame.lap_number)

        target_window = self.brake_graph.window_metres
        self.throttle_graph.window_metres = target_window
        self.gear_graph.window_metres = target_window

        # Feed arc overlay
        if self._arc_active and self._arc_overlay is not None:
            self._update_arc_data()

        if self.recording:
            self._record_sample(frame)

    def _record_sample(self, frame: TelemetryFrame) -> None:
        if self.record_start_lap_number is None:
            self.record_start_lap_number = frame.lap_number
        if (
            frame.lap_number != self.record_start_lap_number
            and len(self.reference_samples) > 100
            and frame.lap_distance < 60.0
        ):
            self.finish_recording(auto_finished=True)
            return
        self.reference_samples.append(
            {
                "lap_distance": float(frame.lap_distance),
                "brake": float(frame.brake),
                "throttle": float(frame.throttle),
                "gear": float(max(1, min(8, frame.gear))),
            }
        )

    def toggle_recording(self) -> None:
        if self.recording:
            self.finish_recording(auto_finished=False)
            return
        self.recording = True
        self.record_start_lap_number = self.current_lap_number
        self.reference_samples = []
        self.record_button.setText("Stop Recording")
        self.status_label.setText("Recording reference lap...")

    def finish_recording(self, auto_finished: bool) -> None:
        self.recording = False
        self.record_button.setText("Record Ref Lap")
        if not self.reference_samples:
            self.status_label.setText("Recording finished - no samples.")
            return
        self.reference_track_length = max(s["lap_distance"] for s in self.reference_samples)
        b_graph = [(s["lap_distance"], s.get("brake", 0.0)) for s in self.reference_samples]
        t_graph = [(s["lap_distance"], s.get("throttle", 0.0)) for s in self.reference_samples]
        g_graph = [(s["lap_distance"], s.get("gear", 1.0)) for s in self.reference_samples]

        self.brake_graph.set_reference(b_graph, self.reference_track_length)
        self.throttle_graph.set_reference(t_graph, self.reference_track_length)
        self.gear_graph.set_reference(g_graph, self.reference_track_length)
        tag = "Auto" if auto_finished else "Saved"
        self.status_label.setText(
            f"{tag} ref lap: {len(self.reference_samples)} pts, {self.reference_track_length:.0f}m."
        )

    def save_reference(self) -> None:
        if not self.reference_samples:
            self.status_label.setText("Nothing to save.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save",
            str(Path.cwd() / "reference_lap.json"),
            "JSON (*.json)",
        )
        if not path:
            return
        payload = {
            "version": 2,
            "track_length": self.reference_track_length,
            "samples": self.reference_samples,
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except OSError as e:
            self.status_label.setText(f"Save failed: {e}")
            return
        self.status_label.setText(f"Saved: {Path(path).name}")

    def load_reference(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load", str(Path.cwd()), "JSON (*.json)")
        if path:
            self._load_reference_file(path)

    def _load_reference_file(self, file_path: str) -> None:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            self.status_label.setText(f"Load failed: {e}")
            return

        pts: list[tuple[float, float]] = []
        t_pts: list[tuple[float, float]] = []
        g_pts: list[tuple[float, float]] = []
        tl = 0.0

        if "data" in payload and isinstance(payload["data"], dict):
            data = payload["data"]
            dists = data.get("distance", [])
            brakes = data.get("brake", [])
            throttles = data.get("throttle", [])
            gears = data.get("gear", [])
            tl = float(data.get("maxDistance", 0.0))

            nb = min(len(dists), len(brakes))
            for i in range(nb):
                pts.append((float(dists[i]), max(0.0, min(1.0, float(brakes[i]) / 100.0))))

            nt = min(len(dists), len(throttles))
            for i in range(nt):
                t_pts.append((float(dists[i]), max(0.0, min(1.0, float(throttles[i]) / 100.0))))

            ng = min(len(dists), len(gears))
            for i in range(ng):
                g_pts.append((float(dists[i]), self._normalize_gear_value(float(gears[i]))))

            if tl <= 0 and dists:
                tl = float(dists[-1])

        elif "samples" in payload:
            tl = float(payload.get("track_length", 0.0))
            for s in payload.get("samples", []):
                if isinstance(s, dict):
                    pts.append((float(s.get("lap_distance", 0)), float(s.get("brake", 0))))
                    t_pts.append((float(s.get("lap_distance", 0)), float(s.get("throttle", 0))))
                    if "gear" in s:
                        g_pts.append(
                            (
                                float(s.get("lap_distance", 0)),
                                self._normalize_gear_value(float(s.get("gear", 1.0))),
                            )
                        )
        else:
            self.status_label.setText("Unknown file format.")
            return

        if not pts:
            self.status_label.setText("File has no data.")
            return

        self.brake_graph.set_reference(pts, tl)
        if t_pts:
            self.throttle_graph.set_reference(t_pts, tl)
        else:
            self.throttle_graph.clear_reference()
        if g_pts:
            self.gear_graph.set_reference(g_pts, tl)
        else:
            self.gear_graph.clear_reference()

        self.status_label.setText(
            f"Loaded {len(pts)} pts / {len(g_pts)} gear pts from {Path(file_path).name} ({tl:.0f}m)."
        )

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(10, 10, 12, 170))
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 16, 16)

    def _update_ui_visibility(self, visible: bool) -> None:
        if self._ui_visible == visible:
            return
        self._ui_visible = visible
        if visible:
            self.top_widget.show()
            self.offset_widget.show()
            self.mid_widget.show()
        else:
            self.top_widget.hide()
            self.offset_widget.hide()
            self.mid_widget.hide()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        if event.type() == QEvent.Type.ActivationChange:
            self._update_ui_visibility(self.isActiveWindow())
        super().changeEvent(event)

    def _edge_at(self, pos: QPoint) -> str:
        m = _RESIZE_MARGIN
        x, y, w, h = pos.x(), pos.y(), self.width(), self.height()
        edge = ""
        if y < m:
            edge += "t"
        elif y > h - m:
            edge += "b"
        if x < m:
            edge += "l"
        elif x > w - m:
            edge += "r"
        return edge

    def _cursor_for_edge(self, edge: str) -> Qt.CursorShape:
        mapping = {
            "tl": Qt.CursorShape.SizeFDiagCursor,
            "br": Qt.CursorShape.SizeFDiagCursor,
            "tr": Qt.CursorShape.SizeBDiagCursor,
            "bl": Qt.CursorShape.SizeBDiagCursor,
            "t": Qt.CursorShape.SizeVerCursor,
            "b": Qt.CursorShape.SizeVerCursor,
            "l": Qt.CursorShape.SizeHorCursor,
            "r": Qt.CursorShape.SizeHorCursor,
        }
        return mapping.get(edge, Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position().toPoint()
        edge = self._edge_at(pos)
        if edge:
            self._resize_edge = edge
            self._resize_origin = event.globalPosition().toPoint()
            self._resize_geom = QRectF(self.geometry())
        else:
            self.drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._resize_origin is not None and self._resize_geom is not None:
            delta = event.globalPosition().toPoint() - self._resize_origin
            g = QRectF(self._resize_geom)
            if "r" in self._resize_edge:
                g.setRight(g.right() + delta.x())
            if "b" in self._resize_edge:
                g.setBottom(g.bottom() + delta.y())
            if "l" in self._resize_edge:
                g.setLeft(g.left() + delta.x())
            if "t" in self._resize_edge:
                g.setTop(g.top() + delta.y())
            r = g.toRect()
            if r.width() >= self.minimumWidth() and r.height() >= self.minimumHeight():
                self.setGeometry(r)
            event.accept()
        elif self.drag_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_origin)
            event.accept()
        else:
            edge = self._edge_at(event.position().toPoint())
            if edge:
                self.setCursor(QCursor(self._cursor_for_edge(edge)))
            else:
                self.unsetCursor()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_origin = None
            self._resize_origin = None
            self._resize_edge = ""
            self._resize_geom = None
            event.accept()

    # ── Arc Overlay ─────────────────────────────────────────────────
    def _toggle_arc_mode(self) -> None:
        self._arc_active = not self._arc_active
        if self._arc_active:
            if self._arc_overlay is None:
                self._arc_overlay = ArcOverlayWidget()
                import os
                if os.path.exists(ARC_CONFIG_FILE):
                    self._arc_overlay.load_config(ARC_CONFIG_FILE)
                else:
                    self._create_default_arc_modules()
                    self._arc_overlay.rebuild()
            self._arc_overlay.show()
            self.toggle_arc_btn.setText("Arc HUD (AN)")
        else:
            if self._arc_overlay is not None:
                self._arc_overlay.hide()
            self.toggle_arc_btn.setText("Arc HUD")

    def _create_default_arc_modules(self) -> None:
        """Erstellt Standard-Module für Throttle, Brake, Gear."""
        ov = self._arc_overlay
        if ov is None:
            return
        ov.modules.clear()
        ov.modules.append(GraphModule(
            name="Brake", t_start=0.03, t_end=0.35,
            side="outside", height=0.9,
            color=(255, 50, 50, 220), fill_color=(255, 50, 50, 60),
            data_key="brake"
        ))
        ov.modules.append(GraphModule(
            name="Throttle", t_start=0.03, t_end=0.35,
            side="outside", height=0.9,
            color=(0, 255, 100, 220), fill_color=(0, 255, 100, 60),
            data_key="throttle"
        ))
        ov.modules.append(GraphModule(
            name="Gear", t_start=0.65, t_end=0.97,
            side="outside", height=0.9,
            color=(255, 210, 60, 220), fill_color=(255, 210, 60, 60),
            data_key="gear"
        ))

    def _update_arc_data(self) -> None:
        """Schickt die letzten Live-Samples an die Arc-Module."""
        ov = self._arc_overlay
        if ov is None:
            return
        N = 100
        for mod in ov.modules:
            if not isinstance(mod, GraphModule):
                continue
            if mod.data_key == "throttle":
                samples = self.throttle_graph._live_samples[-N:]
                mod.set_values([v for _, v in samples])
            elif mod.data_key == "brake":
                samples = self.brake_graph._live_samples[-N:]
                mod.set_values([v for _, v in samples])
            elif mod.data_key == "gear":
                samples = self.gear_graph._live_samples[-N:]
                mod.set_values([max(0, (v - 1) / 7.0) for _, v in samples])
        ov.update()

    def _open_arc_settings(self) -> None:
        if self._arc_overlay is None:
            # Erstelle Arc-Overlay falls noch nicht da
            self._arc_overlay = ArcOverlayWidget()
            self._create_default_arc_modules()
            self._arc_overlay.rebuild()
        if self._arc_settings is None:
            self._arc_settings = ArcSettingsWindow(self._arc_overlay)
        self._arc_settings.show()
        self._arc_settings.raise_()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.telemetry.stop()
        if self._arc_overlay is not None:
            self._arc_overlay.close()
        if self._arc_settings is not None:
            self._arc_settings.close()
        app = QApplication.instance()
        if app is not None:
            app.quit()
        super().closeEvent(event)
