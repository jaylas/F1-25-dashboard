import json
import math
from pathlib import Path

from PyQt6.QtCore import QEvent, QPoint, QRectF, Qt, QTimer, pyqtSignal
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

from core.models import REFS_DIR, TelemetryFrame, UDP_PORT
from core.telemetry import TelemetryListener
from ui.widgets.graph import InputGraphWidget
from ui.widgets.arc import ArcOverlayWidget, ArcSettingsWindow
from ui.widgets.arc.modules import GraphModule
from ui.windows.review import ReviewWindow

ARC_CONFIG_FILE = "config/arc_config.json"


_RESIZE_MARGIN = 8
TRACK_REF_ALIASES = {
    "lusail": "losail",
}
LAP_DEBUG = True


class GraphWindow(QWidget):
    # This signal notifies parents when the user adjusted graph offsets manually
    offset_changed = pyqtSignal(float)

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

        # Wir übernehmen keine eigene Telemetrie mehr, der Launcher füttert uns via update_telemetry
        
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

        # Historical laps (für Review Window) bleibt hier
        self.historical_laps: list[dict[str, list[tuple[float, float]]]] = []
        self._current_lap: int = -1
        self._current_distance: float = 0.0
        self._wrap_archive_latched: bool = False

        self.ui_timer = QTimer(self)
        self.ui_timer.setInterval(10)
        self.ui_timer.timeout.connect(self.update)
        self.ui_timer.start()

        self._build_ui()

        pass

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(8)

        # Top und Mid Widget (Buttons) fliegen komplett raus.

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

    def _adjust_offset(self, delta: float) -> None:
        self.brake_graph._ref_offset += delta
        self.throttle_graph._ref_offset += delta
        self.gear_graph._ref_offset += delta
        off = self.brake_graph._ref_offset
        self.offset_label.setText(f"Ref offset: {off:+.0f}m")
        self.offset_changed.emit(off)

    def set_opacity(self, value: int) -> None:
        self._bg_opacity = value
        self.setWindowOpacity(value / 255.0)

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
            self._lap_dbg(
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



    def set_references(self, b_graph, t_graph, g_graph, tl):
        """Called by LauncherWindow when loading/recording is done."""
        self.brake_graph.set_reference(b_graph, tl)
        self.throttle_graph.set_reference(t_graph, tl)
        self.gear_graph.set_reference(g_graph, tl)

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
            self.offset_widget.show()
        else:
            self.offset_widget.hide()

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
            self._update_arc_references()
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
            data_key="brake", value_mode="percent"
        ))
        ov.modules.append(GraphModule(
            name="Throttle", t_start=0.03, t_end=0.35,
            side="outside", height=0.9,
            color=(0, 255, 100, 220), fill_color=(0, 255, 100, 60),
            data_key="throttle", value_mode="percent"
        ))
        ov.modules.append(GraphModule(
            name="Gear", t_start=0.65, t_end=0.97,
            side="outside", height=0.9,
            color=(255, 210, 60, 220), fill_color=(255, 210, 60, 60),
            data_key="gear", value_mode="gear"
        ))

    def _update_arc_data(self, frame: TelemetryFrame = None) -> None:
        """Schickt Live-Samples + Reference-Daten an die Arc-Module."""
        ov = self._arc_overlay
        if ov is None:
            return
        current_dist = self.current_lap_distance

        from ui.widgets.arc.modules import GraphModule, RadarModule, BrakeOverlayModule, TyreWearModule, RelativeDeltaModule
        ahead_gap_m = self._nearest_ahead_gap_m(frame) if frame is not None else None

        for mod in ov.modules:
            if isinstance(mod, GraphModule):
                # Referenz teilen statt kopieren — GraphModule filtert selbst via bisect
                if mod.data_key == "throttle":
                    mod.set_live_samples(self.throttle_graph._live_samples, current_dist)
                elif mod.data_key == "brake":
                    mod.set_live_samples(self.brake_graph._live_samples, current_dist)
                elif mod.data_key == "gear":
                    mod.set_live_samples(self.gear_graph._live_samples, current_dist)
            elif isinstance(mod, BrakeOverlayModule):
                mod.set_current_distance(current_dist)
                mod.set_live_samples(self.brake_graph._live_samples)
            elif isinstance(mod, RadarModule) and frame is not None:
                mod.update_telemetry(frame.player_pos, frame.player_forward, frame.player_right, frame.opponents)
            elif isinstance(mod, TyreWearModule) and frame is not None:
                mod.set_tyre_wear(frame.tyre_wear)
            elif isinstance(mod, RelativeDeltaModule) and frame is not None:
                if frame.source_packet_id == 0:
                    mod.update_gap(ahead_gap_m, frame.session_time)
                
        ov.update()

    @staticmethod
    def _nearest_ahead_gap_m(frame: TelemetryFrame) -> float | None:
        px, _py, pz = frame.player_pos
        fx, _fy, fz = frame.player_forward
        rx, _ry, rz = frame.player_right

        f_len = math.hypot(fx, fz)
        r_len = math.hypot(rx, rz)
        if f_len < 1e-6 or r_len < 1e-6:
            return None

        fx, fz = fx / f_len, fz / f_len
        rx, rz = rx / r_len, rz / r_len

        best_gap: float | None = None
        best_score = float("inf")
        for ox, _oy, oz in frame.opponents:
            dx = ox - px
            dz = oz - pz
            longitudinal = dx * fx + dz * fz
            lateral = dx * rx + dz * rz

            if longitudinal <= 2.0:
                continue
            if abs(lateral) > 80.0:
                continue
            score = longitudinal + (abs(lateral) * 0.2)
            if score < best_score:
                best_score = score
                best_gap = longitudinal

        return best_gap


    def closeEvent(self, event) -> None:  # noqa: N802
        super().closeEvent(event)
