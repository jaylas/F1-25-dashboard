import json
import math
import os
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QRect
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, 
    QFileDialog, QGroupBox, QSlider, QCheckBox, QMessageBox
)

from core.telemetry import TelemetryListener
from core.models import TelemetryFrame, UDP_PORT

class LauncherWindow(QWidget):
    """
    Zentrales Kontrollzentrum (Launcher). 
    Verwaltet die Telemetrie-Verbindung und das Referenzrunden-Management.
    Startet und steuert die reinen Daten-Fenster (Graph, Arc, Radar).
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("F1 25 Dashboard Launcher")
        self.setMinimumWidth(350)

        # Telemetry Background Task
        self.telemetry = TelemetryListener(port=UDP_PORT)
        
        # State
        self.recording = False
        self.record_start_lap_number: int | None = None
        self.reference_samples: list[dict[str, float]] = []
        self.reference_track_length = 0.0
        self._dbg_packet_counts: dict[int, int] = {0: 0, 2: 0, 6: 0, 10: 0}
        self._dbg_last_session_by_id: dict[int, float] = {}
        self._dbg_last_valid_tyre: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
        
        # Window Instances
        self._graph_window = None
        self._arc_overlay = None
        self._arc_settings = None
        self._radar_window = None

        self._build_ui()
        self._ensure_graph_window_exists()
        
        self.telemetry.telemetry_received.connect(self._on_telemetry)
        self.telemetry.connection_state_changed.connect(self._on_connection)
        self.telemetry.track_changed.connect(self._on_track_changed)
        self._load_global_settings()
        self.telemetry.start()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Status Sektion
        self.lbl_status = QLabel("Warte auf F1 25 Telemetrie...")
        self.lbl_status.setStyleSheet("font-weight: bold; color: #ff9900;")
        layout.addWidget(self.lbl_status)
        
        self.lbl_ref_status = QLabel("Keine Referenzrunde geladen.")
        self.lbl_ref_status.setStyleSheet("color: #999;")
        layout.addWidget(self.lbl_ref_status)

        # Fenster Sektion
        grp_windows = QGroupBox("Daten-Fenster")
        v_win = QVBoxLayout()
        
        # Graph Overlay Toggle
        row_g = QHBoxLayout()
        self.btn_graph = QPushButton("Graph Dashboard")
        self.btn_graph.setCheckable(True)
        self.btn_graph.toggled.connect(self._toggle_graph_window)
        row_g.addWidget(self.btn_graph)
        v_win.addLayout(row_g)

        # Graph Sub-Toggles (Throttle & Gear)
        row_sub = QHBoxLayout()
        row_sub.addSpacing(20) # Einrückung
        self.chk_throttle = QCheckBox("Throttle")
        self.chk_throttle.toggled.connect(self._on_throttle_toggled)
        self.chk_gear = QCheckBox("Gear")
        self.chk_gear.toggled.connect(self._on_gear_toggled)
        row_sub.addWidget(self.chk_throttle)
        row_sub.addWidget(self.chk_gear)
        row_sub.addStretch()
        v_win.addLayout(row_sub)

        # Arc Overlay Toggle
        row_a = QHBoxLayout()
        self.btn_arc = QPushButton("Halo/Arc HUD")
        self.btn_arc.setCheckable(True)
        self.btn_arc.toggled.connect(self._toggle_arc_hud)
        row_a.addWidget(self.btn_arc)
        
        self.btn_arc_settings = QPushButton("⚙")
        self.btn_arc_settings.setFixedWidth(40)
        self.btn_arc_settings.clicked.connect(self._open_arc_settings)
        row_a.addWidget(self.btn_arc_settings)
        v_win.addLayout(row_a)
        
        # Radar Toggle
        row_r = QHBoxLayout()
        self.btn_radar = QPushButton("Standalone Radar")
        self.btn_radar.setCheckable(True)
        self.btn_radar.toggled.connect(self._toggle_radar_window)
        row_r.addWidget(self.btn_radar)
        
        self.chk_radar_autohide = QCheckBox("Auto-Verstecken (Smart Radar)")
        self.chk_radar_autohide.setChecked(False)
        self.chk_radar_autohide.toggled.connect(self._on_autohide_toggled)
        row_r.addWidget(self.chk_radar_autohide)
        v_win.addLayout(row_r)
        
        # Radar Slider
        self.slider_radar = QSlider(Qt.Orientation.Horizontal)
        self.slider_radar.setRange(5, 100)
        self.slider_radar.setValue(20)
        self.slider_radar.setToolTip("Radar Radius (m)")
        self.slider_radar.valueChanged.connect(self._on_radar_scale_changed)
        
        row_rs = QHBoxLayout()
        row_rs.addWidget(QLabel("Radius:"))
        row_rs.addWidget(self.slider_radar)
        self.lbl_radius = QLabel("20m")
        row_rs.addWidget(self.lbl_radius)
        v_win.addLayout(row_rs)

        grp_windows.setLayout(v_win)
        layout.addWidget(grp_windows)

        # Opacity Sektion
        grp_op = QGroupBox("Sichtbarkeit (Opacity)")
        v_op = QVBoxLayout()
        
        def make_slider(name, slot):
            r = QHBoxLayout()
            r.addWidget(QLabel(name))
            s = QSlider(Qt.Orientation.Horizontal)
            s.setRange(25, 255) # 10% bis 100%
            s.setValue(255)
            s.valueChanged.connect(slot)
            r.addWidget(s)
            v_op.addLayout(r)
            return s
            
        self.slider_graph_op = make_slider("Graph:", self._on_graph_op_changed)
        self.slider_arc_op = make_slider("Arc HUD:", self._on_arc_op_changed)
        self.slider_radar_op = make_slider("Radar:", self._on_radar_op_changed)
        
        grp_op.setLayout(v_op)
        layout.addWidget(grp_op)

        # Reference Sektion
        grp_ref = QGroupBox("Referenz-Runden")
        v_ref = QVBoxLayout()
        
        btn_rec = QPushButton("Referenz aufnehmen")
        btn_rec.clicked.connect(self._toggle_recording)
        self.btn_rec = btn_rec
        v_ref.addWidget(btn_rec)
        
        row_io = QHBoxLayout()
        btn_load = QPushButton("Laden")
        btn_load.clicked.connect(self._load_reference)
        row_io.addWidget(btn_load)
        btn_save = QPushButton("Speichern")
        btn_save.clicked.connect(self._save_reference)
        row_io.addWidget(btn_save)
        v_ref.addLayout(row_io)

        btn_review = QPushButton("Runden Review")
        btn_review.clicked.connect(self._open_review_window)
        v_ref.addWidget(btn_review)
        
        grp_ref.setLayout(v_ref)
        layout.addWidget(grp_ref)

        # Live Debug Sektion
        grp_dbg = QGroupBox("Live Debug")
        v_dbg = QVBoxLayout()
        self.lbl_dbg_packets = QLabel("Packets 0/2/6/10: 0 / 0 / 0 / 0")
        self.lbl_dbg_packets.setStyleSheet("color: #b8c4d0; font-family: Consolas;")
        v_dbg.addWidget(self.lbl_dbg_packets)

        self.lbl_dbg_tyre = QLabel("Tyre selected FL/FR/RL/RR: 0.0 / 0.0 / 0.0 / 0.0")
        self.lbl_dbg_tyre.setStyleSheet("color: #b8c4d0; font-family: Consolas;")
        v_dbg.addWidget(self.lbl_dbg_tyre)
        self.lbl_dbg_tyre_raw = QLabel("Tyre raw wear: 0.0/0.0/0.0/0.0 | damage: 0.0/0.0/0.0/0.0")
        self.lbl_dbg_tyre_raw.setStyleSheet("color: #8fa0ad; font-family: Consolas;")
        v_dbg.addWidget(self.lbl_dbg_tyre_raw)

        self.lbl_dbg_motion = QLabel("Opponents: 0 | Ahead gap: n/a")
        self.lbl_dbg_motion.setStyleSheet("color: #b8c4d0; font-family: Consolas;")
        v_dbg.addWidget(self.lbl_dbg_motion)

        self.lbl_dbg_last_seen = QLabel("Last t[s] id0/id2/id6/id10: - / - / - / -")
        self.lbl_dbg_last_seen.setStyleSheet("color: #8fa0ad; font-family: Consolas;")
        v_dbg.addWidget(self.lbl_dbg_last_seen)

        grp_dbg.setLayout(v_dbg)
        layout.addWidget(grp_dbg)

    # ─── Telemetry Dispatching ──────────────────────────────────────────
    def _on_connection(self, active: bool):
        if active:
            self.lbl_status.setText(" verbunden. (UDP Empfang)")
            self.lbl_status.setStyleSheet("font-weight: bold; color: #00cc66;")
        else:
            self.lbl_status.setText("Warte auf F1 25 Telemetrie...")
            self.lbl_status.setStyleSheet("font-weight: bold; color: #ff9900;")

    def _on_track_changed(self, track_id: int, track_name: str) -> None:
        self.current_track_name = track_name
        self._try_load_track_ref(track_name)

    def _on_telemetry(self, frame: TelemetryFrame):
        self._update_live_debug(frame)
        # 1. Update Graph Window
        if self._graph_window is not None:
            self._graph_window.on_telemetry(frame)
        
        # 2. Update Arc Overlay
        if self._arc_overlay is not None and self._arc_overlay.isVisible():
            # Der GraphWindow hat aktuell die Logik für referenzen...
            # The Graph modules in Arc need the live queues. We can feed them!
            # We'll pass the frame to arc_overlay and let it push to modules
            self._update_arc_data(frame)

        # 3. Update Radar
        if self._radar_window is not None and self.btn_radar.isChecked():
            if self.chk_radar_autohide.isChecked():
                import math
                radius = getattr(self._radar_window, 'radius_m', 20.0)
                px, py, pz = frame.player_pos
                show_radar = False
                for (ox, oy, oz) in frame.opponents:
                    dist = math.hypot(ox - px, oz - pz)
                    if dist <= radius * 1.5:
                        show_radar = True
                        break
                
                if show_radar and not self._radar_window.isVisible():
                    self._radar_window.show()
                elif not show_radar and self._radar_window.isVisible():
                    self._radar_window.hide()
                    
            if self._radar_window.isVisible():
                self._radar_window.update_telemetry(frame)
            
        # 4. Recording logic
        if self.recording:
            self._record_sample(frame)

    def _update_live_debug(self, frame: TelemetryFrame) -> None:
        pid = int(frame.source_packet_id)
        if pid in self._dbg_packet_counts:
            self._dbg_packet_counts[pid] += 1
            self._dbg_last_session_by_id[pid] = frame.session_time

        c0 = self._dbg_packet_counts[0]
        c2 = self._dbg_packet_counts[2]
        c6 = self._dbg_packet_counts[6]
        c10 = self._dbg_packet_counts[10]
        self.lbl_dbg_packets.setText(f"Packets 0/2/6/10: {c0} / {c2} / {c6} / {c10}")

        fl, fr, rl, rr = frame.tyre_wear
        if any(v > 0.0 for v in (fl, fr, rl, rr)):
            self._dbg_last_valid_tyre = (fl, fr, rl, rr)
        self.lbl_dbg_tyre.setText(
            f"Tyre selected FL/FR/RL/RR: {fl:.1f} / {fr:.1f} / {rl:.1f} / {rr:.1f} "
            f"(last>0: {self._dbg_last_valid_tyre[0]:.1f}/{self._dbg_last_valid_tyre[1]:.1f}/{self._dbg_last_valid_tyre[2]:.1f}/{self._dbg_last_valid_tyre[3]:.1f})"
        )
        wfl, wfr, wrl, wrr = frame.tyre_wear_raw
        dfl, dfr, drl, drr = frame.tyre_damage_raw
        self.lbl_dbg_tyre_raw.setText(
            f"Tyre raw wear: {wfl:.1f}/{wfr:.1f}/{wrl:.1f}/{wrr:.1f} | "
            f"damage: {dfl:.1f}/{dfr:.1f}/{drl:.1f}/{drr:.1f}"
        )

        ahead = self._nearest_ahead_gap_m(frame)
        gap_txt = "n/a" if ahead is None else f"{ahead:.1f} m"
        self.lbl_dbg_motion.setText(
            f"Opponents: {len(frame.opponents)} | Ahead gap: {gap_txt}"
        )

        def _last(pid_key: int) -> str:
            v = self._dbg_last_session_by_id.get(pid_key)
            return "-" if v is None else f"{v:.1f}"

        self.lbl_dbg_last_seen.setText(
            f"Last t[s] id0/id2/id6/id10: {_last(0)} / {_last(2)} / {_last(6)} / {_last(10)}"
        )

    def _update_arc_data(self, frame: TelemetryFrame):
        current_dist = frame.lap_distance
        from ui.widgets.arc.modules import GraphModule, RadarModule
        # We need the reference data for Arc graphs!
        # Since graph window manages the live buffers, if we want arc graphs to work independently,
        # we have a structural dependency. For now, pull live samples from graph_window if it exists.
        # But this is a bit coupled. As a fix, we could let GraphWindow manage the live dequeues,
        # or just let GraphModule maintain its own queue!
        # In the interest of not breaking Arc graphs, if graph_window exists, we use it.
        # Otherwise Arc graphs won't update their live curves seamlessly.
        ahead_gap_m = self._nearest_ahead_gap_m(frame)
        
        if self._arc_overlay:
            from ui.widgets.arc.modules import GraphModule, RadarModule, BrakeIndicatorModule, BrakeOverlayModule, TyreWearModule, RelativeDeltaModule
            for mod in self._arc_overlay.modules:
                if isinstance(mod, RadarModule):
                    mod.update_telemetry(frame.player_pos, frame.player_forward, frame.player_right, frame.opponents)
                elif isinstance(mod, BrakeIndicatorModule):
                    mod.set_current_distance(current_dist)
                elif isinstance(mod, BrakeOverlayModule):
                    mod.set_current_distance(current_dist)
                    if self._graph_window is not None:
                        mod.set_live_samples(self._graph_window.brake_graph._live_samples)
                elif isinstance(mod, TyreWearModule):
                    mod.set_tyre_wear(frame.tyre_wear)
                elif isinstance(mod, RelativeDeltaModule):
                    if frame.source_packet_id == 0:
                        mod.update_gap(ahead_gap_m, frame.session_time)
                elif isinstance(mod, GraphModule) and self._graph_window is not None:
                    if mod.data_key == "throttle":
                        mod.set_live_samples(self._graph_window.throttle_graph._live_samples, current_dist)
                    elif mod.data_key == "brake":
                        mod.set_live_samples(self._graph_window.brake_graph._live_samples, current_dist)
                    elif mod.data_key == "gear":
                        mod.set_live_samples(self._graph_window.gear_graph._live_samples, current_dist)
            self._arc_overlay.update()

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
            # More tolerant so this also works on corner entries/exits.
            if abs(lateral) > 80.0:
                continue
            score = longitudinal + (abs(lateral) * 0.2)
            if score < best_score:
                best_score = score
                best_gap = longitudinal

        return best_gap

    # ─── Window Management ───────────────────────────────────────────────
    def _ensure_graph_window_exists(self):
        if self._graph_window is None:
            from ui.windows.graph_window import GraphWindow
            self._graph_window = GraphWindow()
            self._graph_window.offset_changed.connect(self._on_graph_offset_changed)
            
            # Initialer Sync der Checkboxen
            self._graph_window.throttle_graph.setVisible(self.chk_throttle.isChecked())
            self._graph_window.gear_graph.setVisible(self.chk_gear.isChecked())
            self._graph_window.set_opacity(self.slider_graph_op.value())

            # Push currently loaded references into it
            if self.reference_samples:
                self._push_references_to_graphs()

    def _toggle_graph_window(self, checked: bool):
        self._ensure_graph_window_exists()
        if checked:
            self._graph_window.show()
        else:
            self._graph_window.hide()

    def _on_throttle_toggled(self, checked: bool):
        if self._graph_window is not None:
            self._graph_window.throttle_graph.setVisible(checked)
            
    def _on_gear_toggled(self, checked: bool):
        if self._graph_window is not None:
            self._graph_window.gear_graph.setVisible(checked)

    def _toggle_arc_hud(self, checked: bool):
        if self._arc_overlay is None:
            from ui.widgets.arc.arc_overlay import ArcOverlayWidget
            self._arc_overlay = ArcOverlayWidget()
            try:
                self._arc_overlay.load_config("config/arc_config.json")
            except Exception:
                pass
            if not self._arc_overlay.modules:
                self._create_default_arc_modules()
                self._arc_overlay.rebuild()
            self._arc_overlay.setWindowOpacity(self.slider_arc_op.value() / 255.0)
        if checked:
            self._arc_overlay.show()
        else:
            self._arc_overlay.hide()
            
    def _open_arc_settings(self):
        if self._arc_overlay is None:
             self.btn_arc.setChecked(True) # Schaltet das ArcHUD ein
        if self._arc_settings is None:
            from ui.widgets.arc.settings import ArcSettingsWindow
            self._arc_settings = ArcSettingsWindow(self._arc_overlay)
        self._arc_settings.show()
        self._arc_settings.raise_()

    def _toggle_radar_window(self, checked: bool):
        if self._radar_window is None:
            from ui.windows.radar_window import RadarWindow
            self._radar_window = RadarWindow(self) # Owned by launcher so it closes with it
            self._radar_window.setWindowOpacity(self.slider_radar_op.value() / 255.0)
        if checked:
            self._radar_window.show()
        else:
            self._radar_window.hide()

    def _on_autohide_toggled(self, checked: bool):
        if not checked and self.btn_radar.isChecked() and self._radar_window is not None:
            self._radar_window.show()

    def _on_radar_scale_changed(self, value):
        self.lbl_radius.setText(f"{value}m")
        if self._radar_window is not None:
            self._radar_window.set_radius(float(value))

    def _on_graph_op_changed(self, value: int):
        if self._graph_window is not None:
            self._graph_window.set_opacity(value)

    def _on_arc_op_changed(self, value: int):
        if self._arc_overlay is not None:
            self._arc_overlay.setWindowOpacity(value / 255.0)

    def _on_radar_op_changed(self, value: int):
        if self._radar_window is not None:
            self._radar_window.setWindowOpacity(value / 255.0)

    # ─── Recording Logic ────────────────────────────────────────────────
    def _toggle_recording(self):
        if self.recording:
            self._finish_recording(auto_finished=False)
        else:
            self.recording = True
            self.record_start_lap_number = None # Wird im nächsten Frame gesetzt
            self.reference_samples = []
            self.btn_rec.setText("Aufnahme stoppen")
            self.lbl_ref_status.setText("Recording...")

    def _open_review_window(self):
        if self._graph_window is not None:
            self._graph_window._open_review_window()
        else:
            QMessageBox.information(self, "Review", "Das Graph-Dashboard muss einmal offen gewesen sein, um Runden mitzuschneiden!")

    def _record_sample(self, frame: TelemetryFrame):
        if self.record_start_lap_number is None:
            self.record_start_lap_number = frame.lap_number
        if (
            frame.lap_number != self.record_start_lap_number
            and len(self.reference_samples) > 100
            and frame.lap_distance < 60.0
        ):
            self._finish_recording(auto_finished=True)
            return
        self.reference_samples.append({
            "lap_distance": float(frame.lap_distance),
            "brake": float(frame.brake),
            "throttle": float(frame.throttle),
            "gear": float(max(1, min(8, frame.gear))),
        })

    def _finish_recording(self, auto_finished: bool):
        self.recording = False
        self.btn_rec.setText("Referenz aufnehmen")
        if not self.reference_samples:
            self.lbl_ref_status.setText("Aufnahme beendet - keine Daten.")
            return
        
        self.reference_track_length = max(s["lap_distance"] for s in self.reference_samples)
        self._push_references_to_graphs()
        
        tag = "Auto" if auto_finished else "Saved"
        self.lbl_ref_status.setText(
            f"{tag}: {len(self.reference_samples)} Punkte, {self.reference_track_length:.0f}m."
        )

    def _on_graph_offset_changed(self, offset: float):
        # We don't need to push everything if we just want to update arc offset, but push is cheap enough.
        # It's cleaner to just update the arc modules.
        if self._arc_overlay is not None:
            from ui.widgets.arc.modules import GraphModule, BrakeIndicatorModule, BrakeOverlayModule
            b_graph = [(s["lap_distance"], s.get("brake", 0.0)) for s in self.reference_samples]
            t_graph = [(s["lap_distance"], s.get("throttle", 0.0)) for s in self.reference_samples]
            g_graph = [(s["lap_distance"], s.get("gear", 1.0)) for s in self.reference_samples]
            for mod in self._arc_overlay.modules:
                if isinstance(mod, GraphModule):
                    if mod.data_key == "brake":
                        mod.set_ref_samples(b_graph, offset)
                    elif mod.data_key == "throttle":
                        mod.set_ref_samples(t_graph, offset)
                    elif mod.data_key == "gear":
                        mod.set_ref_samples(g_graph, offset)
                elif isinstance(mod, BrakeIndicatorModule):
                    mod.set_ref_samples(b_graph, offset)
                elif isinstance(mod, BrakeOverlayModule):
                    mod.set_ref_samples(b_graph, offset)

    def _push_references_to_graphs(self):
        b_graph = [(s["lap_distance"], s.get("brake", 0.0)) for s in self.reference_samples]
        t_graph = [(s["lap_distance"], s.get("throttle", 0.0)) for s in self.reference_samples]
        g_graph = [(s["lap_distance"], s.get("gear", 1.0)) for s in self.reference_samples]
        
        if self._graph_window is not None:
            self._graph_window.set_references(b_graph, t_graph, g_graph, self.reference_track_length)
            
        if self._arc_overlay is not None:
            # Der offset wird jetzt ueber _on_graph_offset_changed einzeln behandelt,
            # aber beim Neu-Laden setzen wir ihn initell hier.
            offset = 0.0
            if self._graph_window is not None:
                 offset = self._graph_window.brake_graph._ref_offset
            from ui.widgets.arc.modules import GraphModule, BrakeIndicatorModule, BrakeOverlayModule
            for mod in self._arc_overlay.modules:
                if isinstance(mod, GraphModule):
                    if mod.data_key == "brake":
                        mod.set_ref_samples(b_graph, offset)
                    elif mod.data_key == "throttle":
                        mod.set_ref_samples(t_graph, offset)
                    elif mod.data_key == "gear":
                        mod.set_ref_samples(g_graph, offset)
                elif isinstance(mod, BrakeIndicatorModule):
                    mod.set_ref_samples(b_graph, offset)
                elif isinstance(mod, BrakeOverlayModule):
                    mod.set_ref_samples(b_graph, offset)

    def _create_default_arc_modules(self) -> None:
        from ui.widgets.arc.modules import GraphModule, TyreWearModule, RelativeDeltaModule
        ov = self._arc_overlay
        if not ov:
            return
        ov.modules.append(GraphModule(
            name="Throttle", t_start=0.03, t_end=0.35,
            side="outside", height=0.9,
            color=(42, 232, 80, 220), fill_color=(42, 232, 80, 60),
            data_key="throttle", value_mode="percent"
        ))
        ov.modules.append(GraphModule(
            name="Brake", t_start=0.03, t_end=0.35,
            side="inside", height=0.9,
            color=(232, 42, 42, 220), fill_color=(232, 42, 42, 60),
            data_key="brake", value_mode="percent"
        ))
        ov.modules.append(GraphModule(
            name="Throttle 2", t_start=0.65, t_end=0.97,
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
        ov.modules.append(TyreWearModule(
            name="ReifenverschleiÃŸ", t_start=0.52, t_end=0.78,
            side="inside", height=1.0,
            color=(255, 255, 255, 220),
            show_values=True,
        ))

        ov.modules.append(RelativeDeltaModule(
            name="Delta Vordermann", t_start=0.40, t_end=0.60,
            side="inside", height=0.9,
            color=(255, 255, 255, 220),
            speed_scale=5.0,
            response_alpha=0.35,
            decay_factor=0.96,
        ))

    def _save_reference(self):
        if not self.reference_samples:
             self.lbl_ref_status.setText("Nichts zum Speichern da.")
             return
        path, _ = QFileDialog.getSaveFileName(self, "Save", str(Path.cwd() / "reference_lap.json"), "JSON (*.json)")
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
            self.lbl_ref_status.setText(f"Gespeichert: {Path(path).name}")
        except Exception as e:
            self.lbl_ref_status.setText(f"Fehler: {e}")

    def _try_load_track_ref(self, track_name: str) -> None:
        from core.models import REFS_DIR
        TRACK_REF_ALIASES = {
            "lusail": "losail",
        }
        refs_root = Path(__file__).resolve().parent.parent.parent / REFS_DIR
        p = refs_root / f"{track_name}.json"
        if not p.exists() and track_name in TRACK_REF_ALIASES:
            p = refs_root / f"{TRACK_REF_ALIASES[track_name]}.json"
        
        if p.exists():
            self._load_reference_file(str(p))
            self.lbl_status.setText(f"Auto-Geladen: {track_name}")
        else:
            self.reference_samples = []
            self._push_references_to_graphs()
            self.lbl_ref_status.setText(f"Kein File: refs/{track_name}.json gefunden")

    def _load_reference(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load", str(Path.cwd()), "JSON (*.json)")
        if not path:
            return
        self._load_reference_file(path)

    def _load_reference_file(self, path: str):
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
                
            pts, t_pts, g_pts = [], [], []
            tl = float(payload.get("track_length", payload.get("data", {}).get("maxDistance", 0.0)))
            
            samples = payload.get("samples", [])
            for s in samples:
                pts.append((float(s.get("lap_distance", 0)), float(s.get("brake", 0))))
                t_pts.append((float(s.get("lap_distance", 0)), float(s.get("throttle", 0))))
                g_pts.append((float(s.get("lap_distance", 0)), float(s.get("gear", 1.0))))
            
            if not samples and "data" in payload:
                # F1 alten struct
                 dists = payload["data"].get("distance", [])
                 brakes = payload["data"].get("brake", [])
                 throttles = payload["data"].get("throttle", [])
                 gears = payload["data"].get("gear", [])
                 for i in range(len(dists)):
                     pts.append((float(dists[i]), float(brakes[i])/100.0 if i < len(brakes) else 0.0))
                     t_pts.append((float(dists[i]), float(throttles[i])/100.0 if i < len(throttles) else 0.0))
                     g_pts.append((float(dists[i]), float(gears[i]) if i < len(gears) else 1.0))

            if pts:
                self.reference_samples = [{"lap_distance": d, "brake": b, "throttle": t, "gear": g} 
                                          for (d,b), (_,t), (_,g) in zip(pts, t_pts, g_pts)]
                self.reference_track_length = tl
                self._push_references_to_graphs()
                self.lbl_ref_status.setText(f"Geladen: {Path(path).name} ({tl:.0f}m)")
        except Exception as e:
            self.lbl_ref_status.setText(f"Fehler: {e}")

    def _save_global_settings(self):
        config = {
            "chk_throttle": self.chk_throttle.isChecked(),
            "chk_gear": self.chk_gear.isChecked(),
            "opacity_graph": self.slider_graph_op.value(),
            "opacity_arc": self.slider_arc_op.value(),
            "opacity_radar": self.slider_radar_op.value(),
            "radar_radius": self.slider_radar.value(),
            "radar_autohide": self.chk_radar_autohide.isChecked(),
            "open_graph": self.btn_graph.isChecked(),
            "open_arc": self.btn_arc.isChecked(),
            "open_radar": self.btn_radar.isChecked(),
            "geom_launcher": self.geometry().getRect()
        }
        if self._graph_window:
            config["geom_graph"] = self._graph_window.geometry().getRect()
        if self._arc_overlay:
            config["geom_arc"] = self._arc_overlay.geometry().getRect()
            self._arc_overlay.save_config("config/arc_config.json")
        if self._radar_window:
            config["geom_radar"] = self._radar_window.geometry().getRect()
            
        try:
            with open("config/dashboard_config.json", "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
        except Exception:
            pass

    def _load_global_settings(self):
        try:
            with open("config/dashboard_config.json", "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception:
            return

        self.chk_throttle.setChecked(config.get("chk_throttle", True))
        self.chk_gear.setChecked(config.get("chk_gear", True))
        self.slider_graph_op.setValue(config.get("opacity_graph", 255))
        self.slider_arc_op.setValue(config.get("opacity_arc", 255))
        self.slider_radar_op.setValue(config.get("opacity_radar", 255))
        self.slider_radar.setValue(config.get("radar_radius", 20))
        self.chk_radar_autohide.setChecked(config.get("radar_autohide", False))
        
        gl = config.get("geom_launcher")
        if gl:
            self.setGeometry(QRect(*gl))
            
        if config.get("open_graph", False):
            self.btn_graph.setChecked(True)
            if self._graph_window and "geom_graph" in config:
                self._graph_window.setGeometry(QRect(*config["geom_graph"]))
                
        if config.get("open_arc", False):
            self.btn_arc.setChecked(True)
            if self._arc_overlay and "geom_arc" in config:
                self._arc_overlay.setGeometry(QRect(*config["geom_arc"]))
                
        if config.get("open_radar", False):
            self.btn_radar.setChecked(True)
            if self._radar_window and "geom_radar" in config:
                self._radar_window.setGeometry(QRect(*config["geom_radar"]))

    def closeEvent(self, event):
        self._save_global_settings()
        self.telemetry.stop()
        if self._graph_window: self._graph_window.close()
        if self._arc_overlay: self._arc_overlay.close()
        if self._arc_settings: self._arc_settings.close()
        if self._radar_window: self._radar_window.close()
        super().closeEvent(event)
