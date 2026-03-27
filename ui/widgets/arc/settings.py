"""
Einstellungs-Fenster für Arc Overlay – Bogen-Form + Modul-Editor.
"""
from __future__ import annotations
import math
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QSlider,
    QPushButton, QCheckBox, QComboBox, QLineEdit, QScrollArea,
    QColorDialog, QListWidget, QListWidgetItem, QDoubleSpinBox,
    QSpinBox, QFileDialog, QMessageBox
)
from PyQt6.QtGui import QColor

from ui.widgets.arc.arc_overlay import ArcOverlayWidget
from ui.widgets.arc.modules import ArcModule, TextModule, GraphModule, BarModule, RadarModule, BrakeIndicatorModule, BrakeOverlayModule, TyreWearModule, RelativeDeltaModule


class ArcSettingsWindow(QWidget):
    CONFIG_FILE = "config/arc_config.json"

    def __init__(self, overlay: ArcOverlayWidget):
        super().__init__()
        self.ov = overlay
        self.setWindowTitle("⬡ Bügel Einstellungen")
        self.resize(420, 700)
        self.setMinimumSize(320, 520)
        self.color = QColor(overlay.base_color)
        self._base_w = 420
        self._last_scale = -1.0

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        root = QVBoxLayout()
        self._scroll = scroll
        self._inner = inner
        self._root_layout = root
        self._base_content_width = 0

        self.sl: dict[str, QSlider] = {}

        # ── Bogen-Form ────────────────────────────────────────────────
        g1 = QGroupBox("Bogen-Form")
        l1 = QVBoxLayout()
        self._slider(l1, "Bügel Breite",       "bezier_w",      50, 3000, overlay.bezier_w)
        self._slider(l1, "Bügel Höhe",          "bezier_h",      10, 2000, overlay.bezier_h)
        self._slider(l1, "Kontrollpunkt X",     "ctrl_x",      -800, 1500, overlay.ctrl_x)
        self._slider(l1, "Scheitel-Abflachung", "ctrl_y_extra", -500, 1000, overlay.ctrl_y_extra)
        self._slider(l1, "Balken Dicke",        "base_thick",     8,  300, overlay.base_thick)
        g1.setLayout(l1)
        root.addWidget(g1)

        # ── Fenster ───────────────────────────────────────────────────
        g2 = QGroupBox("Fenster")
        l2 = QVBoxLayout()
        self._slider(l2, "Fenster Breite", "win_w", 200, 4000, overlay.win_w)
        self._slider(l2, "Fenster Höhe",   "win_h", 100, 3000, overlay.win_h)
        g2.setLayout(l2)
        root.addWidget(g2)

        # ── Aussehen ──────────────────────────────────────────────────
        g3 = QGroupBox("Aussehen")
        l3 = QVBoxLayout()
        self.cb_round = QCheckBox("Abgerundete Enden")
        self.cb_round.setChecked(overlay.round_caps)
        self.cb_round.toggled.connect(self._push_arc)
        l3.addWidget(self.cb_round)
        btn_col = QPushButton("Basis-Farbe ändern …")
        btn_col.clicked.connect(self._pick_color)
        l3.addWidget(btn_col)
        g3.setLayout(l3)
        root.addWidget(g3)

        # ── Modul-Editor ──────────────────────────────────────────────
        g4 = QGroupBox("Module")
        l4 = QVBoxLayout()

        self.module_list = QListWidget()
        self.module_list.currentRowChanged.connect(self._on_module_selected)
        l4.addWidget(self.module_list)

        btn_row = QHBoxLayout()
        for text, slot in [("+ Text", self._add_text),
                           ("+ Graph", self._add_graph),
                           ("+ Bar", self._add_bar),
                           ("+ Radar", self._add_radar),
                           ("+ Reifen", self._add_tyre_wear),
                           ("+ Delta", self._add_relative_delta),
                           ("+ Brems-Overlay", self._add_brake_overlay),
                           ("+ Brems-Balken", self._add_brake),
                           ("X", self._remove_module)]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            btn_row.addWidget(b)
        l4.addLayout(btn_row)

        # Modul-Detail-Felder
        self.detail_frame = QWidget()
        dl = QVBoxLayout()
        dl.setContentsMargins(0, 8, 0, 0)

        self.mod_name = QLineEdit()
        self.mod_name.setPlaceholderText("Modul-Name")
        self.mod_name.textChanged.connect(self._push_module)
        dl.addWidget(self.mod_name)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Start %:"))
        self.mod_t_start = QDoubleSpinBox()
        self.mod_t_start.setRange(0.0, 1.0); self.mod_t_start.setSingleStep(0.01); self.mod_t_start.setDecimals(2)
        self.mod_t_start.valueChanged.connect(self._push_module)
        row1.addWidget(self.mod_t_start)

        row1.addWidget(QLabel("Ende %:"))
        self.mod_t_end = QDoubleSpinBox()
        self.mod_t_end.setRange(0.0, 1.0); self.mod_t_end.setSingleStep(0.01); self.mod_t_end.setDecimals(2)
        self.mod_t_end.valueChanged.connect(self._push_module)
        row1.addWidget(self.mod_t_end)
        dl.addLayout(row1)

        row_shift = QHBoxLayout()
        row_shift.addWidget(QLabel("Verschieben (%):"))
        self.mod_shift = QDoubleSpinBox()
        self.mod_shift.setRange(-100.0, 100.0)
        self.mod_shift.setSingleStep(1.0)
        self.mod_shift.setDecimals(1)
        self.mod_shift.setToolTip("Verschiebt Start und Ende gemeinsam. Positiv = nach rechts, negativ = nach links.")
        row_shift.addWidget(self.mod_shift)
        self.btn_shift_apply = QPushButton("Verschieben")
        self.btn_shift_apply.clicked.connect(self._shift_module_range)
        row_shift.addWidget(self.btn_shift_apply)
        row_shift.addStretch(1)
        dl.addLayout(row_shift)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Seite:"))
        self.mod_side = QComboBox()
        self.mod_side.addItems(["outside", "inside", "center"])
        self.mod_side.currentTextChanged.connect(self._push_module)
        row2.addWidget(self.mod_side)
        row2.addWidget(QLabel("Höhe:"))
        self.mod_height = QDoubleSpinBox()
        self.mod_height.setRange(0.1, 3.0); self.mod_height.setSingleStep(0.1); self.mod_height.setDecimals(1)
        self.mod_height.valueChanged.connect(self._push_module)
        row2.addWidget(self.mod_height)
        dl.addLayout(row2)

        # Legacy-Felder bleiben aus Kompatibilitätsgründen im Objekt,
        # werden aber im UI nicht mehr angezeigt.
        self.mod_text = QLineEdit()
        self.mod_text.textChanged.connect(self._push_module)
        self.mod_subtext = QLineEdit()
        self.mod_subtext.textChanged.connect(self._push_module)
        self.mod_fontsize = QSpinBox()
        self.mod_fontsize.setRange(4, 40); self.mod_fontsize.setValue(14)
        self.mod_fontsize.valueChanged.connect(self._push_module)
        self.mod_datakey = QLineEdit()
        self.mod_datakey.textChanged.connect(self._push_module)

        self.row_radius = QWidget()
        row5 = QHBoxLayout(self.row_radius)
        row5.setContentsMargins(0, 0, 0, 0)
        row5.addWidget(QLabel("Radar Radius (m):"))
        self.mod_radius = QDoubleSpinBox()
        self.mod_radius.setRange(5.0, 200.0); self.mod_radius.setSingleStep(5.0); self.mod_radius.setDecimals(1)
        self.mod_radius.valueChanged.connect(self._push_module)
        row5.addWidget(self.mod_radius)
        
        self.mod_visible = QCheckBox("Sichtbar")
        self.mod_visible.setChecked(True)
        self.mod_visible.toggled.connect(self._push_module)
        row5.addWidget(self.mod_visible)
        dl.addWidget(self.row_radius)

        self.row_dist = QWidget()
        row6 = QHBoxLayout(self.row_dist)
        row6.setContentsMargins(0, 0, 0, 0)
        row6.addWidget(QLabel("Bremsindikator Distanz (m):"))
        self.mod_dist_threshold = QDoubleSpinBox()
        self.mod_dist_threshold.setRange(1.0, 3000.0); self.mod_dist_threshold.setSingleStep(5.0); self.mod_dist_threshold.setDecimals(1)
        self.mod_dist_threshold.valueChanged.connect(self._push_module)
        row6.addWidget(self.mod_dist_threshold)
        dl.addWidget(self.row_dist)

        self.row_color = QWidget()
        row_color = QHBoxLayout(self.row_color)
        row_color.setContentsMargins(0, 0, 0, 0)
        row_color.addWidget(QLabel("Modul-Farbe:"))
        self.btn_mod_color = QPushButton("Modul-Farbe …")
        self.btn_mod_color.clicked.connect(self._pick_mod_color)
        row_color.addWidget(self.btn_mod_color)
        row_color.addStretch(1)
        dl.addWidget(self.row_color)

        self.row_brake_overlay_1 = QWidget()
        row_bo1 = QHBoxLayout(self.row_brake_overlay_1)
        row_bo1.setContentsMargins(0, 0, 0, 0)
        row_bo1.addWidget(QLabel("Ref-Farbe:"))
        self.btn_brake_overlay_ref_color = QPushButton("Ref-Farbe …")
        self.btn_brake_overlay_ref_color.clicked.connect(self._pick_brake_overlay_ref_color)
        row_bo1.addWidget(self.btn_brake_overlay_ref_color)
        row_bo1.addWidget(QLabel("Ref-Opacity (%):"))
        self.mod_ref_opacity = QSpinBox()
        self.mod_ref_opacity.setRange(0, 100)
        self.mod_ref_opacity.valueChanged.connect(self._push_module)
        row_bo1.addWidget(self.mod_ref_opacity)
        self.mod_ref_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.mod_ref_opacity_slider.setRange(0, 100)
        self.mod_ref_opacity_slider.valueChanged.connect(self.mod_ref_opacity.setValue)
        self.mod_ref_opacity.valueChanged.connect(self.mod_ref_opacity_slider.setValue)
        row_bo1.addWidget(self.mod_ref_opacity_slider, 1)
        dl.addWidget(self.row_brake_overlay_1)

        self.row_brake_overlay_2 = QWidget()
        row_bo2 = QHBoxLayout(self.row_brake_overlay_2)
        row_bo2.setContentsMargins(0, 0, 0, 0)
        row_bo2.addWidget(QLabel("Live-Farbe:"))
        self.btn_brake_overlay_live_color = QPushButton("Live-Farbe …")
        self.btn_brake_overlay_live_color.clicked.connect(self._pick_brake_overlay_live_color)
        row_bo2.addWidget(self.btn_brake_overlay_live_color)
        row_bo2.addWidget(QLabel("Live-Opacity (%):"))
        self.mod_live_opacity = QSpinBox()
        self.mod_live_opacity.setRange(0, 100)
        self.mod_live_opacity.valueChanged.connect(self._push_module)
        row_bo2.addWidget(self.mod_live_opacity)
        self.mod_live_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.mod_live_opacity_slider.setRange(0, 100)
        self.mod_live_opacity_slider.valueChanged.connect(self.mod_live_opacity.setValue)
        self.mod_live_opacity.valueChanged.connect(self.mod_live_opacity_slider.setValue)
        row_bo2.addWidget(self.mod_live_opacity_slider, 1)
        dl.addWidget(self.row_brake_overlay_2)

        self.row_brake_overlay_3 = QWidget()
        row_bo3 = QHBoxLayout(self.row_brake_overlay_3)
        row_bo3.setContentsMargins(0, 0, 0, 0)
        row_bo3.addWidget(QLabel("Füllrichtung:"))
        self.mod_fill_direction = QComboBox()
        self.mod_fill_direction.addItem("Start -> Ende", "start_to_end")
        self.mod_fill_direction.addItem("Ende -> Start", "end_to_start")
        self.mod_fill_direction.currentIndexChanged.connect(self._push_module)
        row_bo3.addWidget(self.mod_fill_direction)
        row_bo3.addStretch(1)
        dl.addWidget(self.row_brake_overlay_3)

        self.row_tyre = QWidget()
        row_tyre = QHBoxLayout(self.row_tyre)
        row_tyre.setContentsMargins(0, 0, 0, 0)
        row_tyre.addWidget(QLabel("Glättung:"))
        self.mod_tyre_alpha = QDoubleSpinBox()
        self.mod_tyre_alpha.setRange(0.01, 1.00); self.mod_tyre_alpha.setSingleStep(0.01); self.mod_tyre_alpha.setDecimals(2)
        self.mod_tyre_alpha.setToolTip("Höher = reagiert schneller, niedriger = ruhiger.")
        self.mod_tyre_alpha.valueChanged.connect(self._push_module)
        row_tyre.addWidget(self.mod_tyre_alpha)
        row_tyre.addStretch(1)
        dl.addWidget(self.row_tyre)

        self.row_delta_1 = QWidget()
        row7 = QHBoxLayout(self.row_delta_1)
        row7.setContentsMargins(0, 0, 0, 0)
        lbl_scale = QLabel("Vollausschlag bei Δv (m/s):")
        lbl_scale.setToolTip("Bei dieser Relativgeschwindigkeit erreicht der Balken den maximalen Ausschlag.")
        row7.addWidget(lbl_scale)
        self.mod_delta_scale = QDoubleSpinBox()
        self.mod_delta_scale.setRange(0.5, 30.0); self.mod_delta_scale.setSingleStep(0.5); self.mod_delta_scale.setDecimals(1)
        self.mod_delta_scale.valueChanged.connect(self._push_module)
        self.mod_delta_scale.setToolTip("Kleiner = empfindlicher, größer = ruhiger.")
        row7.addWidget(self.mod_delta_scale)
        lbl_alpha = QLabel("Ansprechgeschwindigkeit:")
        lbl_alpha.setToolTip("Wie schnell der Balken auf neue Werte reagiert.")
        row7.addWidget(lbl_alpha)
        self.mod_delta_alpha = QDoubleSpinBox()
        self.mod_delta_alpha.setRange(0.01, 1.00); self.mod_delta_alpha.setSingleStep(0.01); self.mod_delta_alpha.setDecimals(2)
        self.mod_delta_alpha.valueChanged.connect(self._push_module)
        self.mod_delta_alpha.setToolTip("Höher = direkter, niedriger = weicher.")
        row7.addWidget(self.mod_delta_alpha)
        dl.addWidget(self.row_delta_1)

        self.row_delta_2 = QWidget()
        row8 = QHBoxLayout(self.row_delta_2)
        row8.setContentsMargins(0, 0, 0, 0)
        lbl_decay = QLabel("Rücklauf zur Mitte:")
        lbl_decay.setToolTip("Wie schnell der Balken ohne klares Signal wieder Richtung Mitte fällt.")
        row8.addWidget(lbl_decay)
        self.mod_delta_decay = QDoubleSpinBox()
        self.mod_delta_decay.setRange(0.50, 0.999); self.mod_delta_decay.setSingleStep(0.01); self.mod_delta_decay.setDecimals(3)
        self.mod_delta_decay.valueChanged.connect(self._push_module)
        self.mod_delta_decay.setToolTip("Höher = hält länger, niedriger = fällt schneller zurück.")
        row8.addWidget(self.mod_delta_decay)
        row8.addStretch(1)
        dl.addWidget(self.row_delta_2)

        self.detail_frame.setLayout(dl)
        l4.addWidget(self.detail_frame)
        self.detail_frame.hide()

        g4.setLayout(l4)
        root.addWidget(g4)


        root.addStretch()
        inner.setLayout(root)
        scroll.setWidget(inner)

        outer = QVBoxLayout()
        outer.addWidget(scroll)
        self.setLayout(outer)

        self._refresh_module_list()
        self._set_scale_styles(1.0)
        self._inner.adjustSize()
        self._base_content_width = max(1, self._inner.sizeHint().width())
        self._apply_ui_scale()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._apply_ui_scale()

    def _apply_ui_scale(self) -> None:
        viewport_w = max(1, self._scroll.viewport().width())
        base_w = max(1, self._base_content_width)
        scale = viewport_w / base_w
        scale = max(0.30, min(1.60, scale))

        if math.isclose(scale, self._last_scale, rel_tol=0.0, abs_tol=0.01):
            return
        self._last_scale = scale
        self._set_scale_styles(scale)

    def _set_scale_styles(self, scale: float) -> None:
        # Width-only scaling:
        # Keep vertical readability (font/control heights) constant and
        # only adapt horizontal footprint.
        font_px = 12
        ctrl_h = 26
        list_h = 180
        btn_min_w = int(round(64 * scale))
        spacing = 6
        margin = 8
        group_top_margin = 10

        self.setStyleSheet(
            f"""
            QWidget {{ font-size: {font_px}px; }}
            QPushButton {{ min-height: {ctrl_h}px; min-width: {btn_min_w}px; }}
            QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{ min-height: {ctrl_h}px; }}
            QListWidget {{ min-height: {list_h}px; }}
            QGroupBox {{ margin-top: {group_top_margin}px; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 3px; }}
            """
        )

        if self._root_layout is not None:
            self._root_layout.setSpacing(spacing)
            self._root_layout.setContentsMargins(margin, margin, margin, margin)
        top = self.layout()
        if top is not None:
            top.setSpacing(spacing)
            top.setContentsMargins(margin, margin, margin, margin)

    # ── Slider-Helfer ─────────────────────────────────────────────────
    def _slider(self, layout, label, key, lo, hi, default):
        lbl = QLabel(f"{label}: {default}")
        row = QVBoxLayout()

        s = QSlider(Qt.Orientation.Horizontal)
        s.setMinimum(lo); s.setMaximum(hi); s.setValue(default)
        s.valueChanged.connect(lambda v, l=lbl, t=label: l.setText(f"{t}: {v}"))
        s.valueChanged.connect(self._push_arc)

        layout.addWidget(lbl)
        row.addWidget(s)
        layout.addLayout(row)
        self.sl[key] = s

    def _pick_color(self):
        c = QColorDialog.getColor(self.color, self, "Basis-Farbe",
                                  QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if c.isValid():
            self.color = c
            self._push_arc()

    # ── Arc-Parameter an Overlay pushen ───────────────────────────────
    def _push_arc(self, *_):
        self.ov.set_arc_params(
            bezier_w=self.sl["bezier_w"].value(),
            bezier_h=self.sl["bezier_h"].value(),
            ctrl_x=self.sl["ctrl_x"].value(),
            ctrl_y_extra=self.sl["ctrl_y_extra"].value(),
            base_thick=self.sl["base_thick"].value(),
            win_w=self.sl["win_w"].value(),
            win_h=self.sl["win_h"].value(),
            base_color=self.color,
            round_caps=self.cb_round.isChecked(),
        )

    # ── Modul-Editor ──────────────────────────────────────────────────
    def _refresh_module_list(self):
        self.module_list.clear()
        for m in self.ov.modules:
            self.module_list.addItem(f"[{type(m).__name__[:-6]}] {m.name}")

    def _apply_type_visibility(self, m: ArcModule) -> None:
        is_radar = isinstance(m, RadarModule)
        is_brake = isinstance(m, BrakeIndicatorModule)
        is_brake_overlay = isinstance(m, BrakeOverlayModule)
        is_tyre = isinstance(m, TyreWearModule)
        is_delta = isinstance(m, RelativeDeltaModule)
        self.row_radius.setVisible(is_radar)
        self.row_dist.setVisible(is_brake)
        self.row_color.setVisible(not is_brake_overlay)
        self.row_brake_overlay_1.setVisible(is_brake_overlay)
        self.row_brake_overlay_2.setVisible(is_brake_overlay)
        self.row_brake_overlay_3.setVisible(is_brake_overlay)
        self.row_tyre.setVisible(is_tyre)
        self.row_delta_1.setVisible(is_delta)
        self.row_delta_2.setVisible(is_delta)

    def _on_module_selected(self, row):
        if row < 0 or row >= len(self.ov.modules):
            self.detail_frame.hide()
            return
        self.detail_frame.show()
        m = self.ov.modules[row]
        self._apply_type_visibility(m)
        # Block signals to avoid feedback loop
        for w in (self.mod_name, self.mod_t_start, self.mod_t_end,
                  self.mod_shift,
                  self.mod_side, self.mod_height, self.mod_text,
                  self.mod_subtext, self.mod_fontsize, self.mod_datakey,
                  self.mod_visible, self.mod_radius, self.mod_dist_threshold,
                  self.mod_tyre_alpha,
                  self.mod_delta_scale, self.mod_delta_alpha, self.mod_delta_decay,
                  self.mod_ref_opacity, self.mod_ref_opacity_slider,
                  self.mod_live_opacity, self.mod_live_opacity_slider,
                  self.mod_fill_direction):
            w.blockSignals(True)

        self.mod_name.setText(m.name)
        self.mod_t_start.setValue(m.t_start)
        self.mod_t_end.setValue(m.t_end)
        self.mod_shift.setValue(0.0)
        self.mod_side.setCurrentText(m.side)
        self.mod_height.setValue(m.height)
        self.mod_visible.setChecked(m.visible)

        if isinstance(m, TextModule):
            self.mod_text.setText(m.text)
            self.mod_subtext.setText(m.sub_text)
            self.mod_fontsize.setValue(m.font_size)
        if isinstance(m, GraphModule):
            self.mod_datakey.setText(m.data_key)
        if hasattr(m, "radius_m"):
            self.mod_radius.setValue(m.radius_m)
        if hasattr(m, "dist_threshold"):
            self.mod_dist_threshold.setValue(m.dist_threshold)
        if hasattr(m, "smoothing_alpha"):
            self.mod_tyre_alpha.setValue(m.smoothing_alpha)
        if hasattr(m, "speed_scale"):
            self.mod_delta_scale.setValue(m.speed_scale)
        if hasattr(m, "response_alpha"):
            self.mod_delta_alpha.setValue(m.response_alpha)
        if hasattr(m, "decay_factor"):
            self.mod_delta_decay.setValue(m.decay_factor)
        if isinstance(m, BrakeOverlayModule):
            self.mod_ref_opacity.setValue(int(m.ref_opacity))
            self.mod_live_opacity.setValue(int(m.live_opacity))
            idx = self.mod_fill_direction.findData(m.fill_direction)
            if idx < 0:
                idx = 0
            self.mod_fill_direction.setCurrentIndex(idx)
            ref_hex = QColor(*m.ref_color_rgb).name()
            self.btn_brake_overlay_ref_color.setStyleSheet(
                f"background-color: {ref_hex}; font-weight: bold; color: {'white' if m.ref_color_rgb[0] < 150 else 'black'};"
            )
            live_hex = QColor(*m.live_color_rgb).name()
            self.btn_brake_overlay_live_color.setStyleSheet(
                f"background-color: {live_hex}; font-weight: bold; color: {'white' if m.live_color_rgb[0] < 150 else 'black'};"
            )
            
        color = getattr(m, "fill_color", getattr(m, "color", (255,255,255,255)))
        color_hex = QColor(*color).name()
        self.btn_mod_color.setStyleSheet(f"background-color: {color_hex}; font-weight: bold; color: {'white' if color[0]<150 else 'black'};")

        for w in (self.mod_name, self.mod_t_start, self.mod_t_end,
                  self.mod_shift,
                  self.mod_side, self.mod_height, self.mod_text,
                  self.mod_subtext, self.mod_fontsize, self.mod_datakey,
                  self.mod_visible, self.mod_radius, self.mod_dist_threshold,
                  self.mod_tyre_alpha,
                  self.mod_delta_scale, self.mod_delta_alpha, self.mod_delta_decay,
                  self.mod_ref_opacity, self.mod_ref_opacity_slider,
                  self.mod_live_opacity, self.mod_live_opacity_slider,
                  self.mod_fill_direction):
            w.blockSignals(False)


    def _pick_mod_color(self):
        row = self.module_list.currentRow()
        if row < 0 or row >= len(self.ov.modules):
            return
        m = self.ov.modules[row]
        if isinstance(m, BrakeOverlayModule):
            return
        current_rgba = getattr(m, "fill_color", getattr(m, "color", (255,255,255,255)))
        c = QColorDialog.getColor(QColor(*current_rgba), self, "Modul-Farbe",
                                  QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if c.isValid():
            c_tuple = (c.red(), c.green(), c.blue(), c.alpha())
            if hasattr(m, "fill_color"):
                m.fill_color = c_tuple
            else:
                m.color = c_tuple
            self._on_module_selected(row)  # Refresh button color
            self.ov.rebuild()

    def _pick_brake_overlay_ref_color(self):
        row = self.module_list.currentRow()
        if row < 0 or row >= len(self.ov.modules):
            return
        m = self.ov.modules[row]
        if not isinstance(m, BrakeOverlayModule):
            return
        c = QColorDialog.getColor(QColor(*m.ref_color_rgb), self, "Ref-Farbe wählen")
        if c.isValid():
            m.ref_color_rgb = (c.red(), c.green(), c.blue())
            self._on_module_selected(row)
            self.ov.rebuild()

    def _pick_brake_overlay_live_color(self):
        row = self.module_list.currentRow()
        if row < 0 or row >= len(self.ov.modules):
            return
        m = self.ov.modules[row]
        if not isinstance(m, BrakeOverlayModule):
            return
        c = QColorDialog.getColor(QColor(*m.live_color_rgb), self, "Live-Farbe wählen")
        if c.isValid():
            m.live_color_rgb = (c.red(), c.green(), c.blue())
            self._on_module_selected(row)
            self.ov.rebuild()

    def _push_module(self, *_):
        row = self.module_list.currentRow()
        if row < 0 or row >= len(self.ov.modules):
            return
        m = self.ov.modules[row]
        self._apply_type_visibility(m)
        m.name = self.mod_name.text()
        m.t_start = self.mod_t_start.value()
        m.t_end = self.mod_t_end.value()
        m.side = self.mod_side.currentText()
        m.height = self.mod_height.value()
        m.visible = self.mod_visible.isChecked()

        if isinstance(m, TextModule):
            m.text = self.mod_text.text()
            m.sub_text = self.mod_subtext.text()
            m.font_size = self.mod_fontsize.value()
        if isinstance(m, GraphModule):
            m.data_key = self.mod_datakey.text()
        if hasattr(m, "radius_m"):
            m.radius_m = self.mod_radius.value()
        if hasattr(m, "dist_threshold"):
            m.dist_threshold = self.mod_dist_threshold.value()
        if hasattr(m, "smoothing_alpha"):
            m.smoothing_alpha = self.mod_tyre_alpha.value()
        if hasattr(m, "speed_scale"):
            m.speed_scale = self.mod_delta_scale.value()
        if hasattr(m, "response_alpha"):
            m.response_alpha = self.mod_delta_alpha.value()
        if hasattr(m, "decay_factor"):
            m.decay_factor = self.mod_delta_decay.value()
        if isinstance(m, BrakeOverlayModule):
            m.ref_opacity = self.mod_ref_opacity.value()
            m.live_opacity = self.mod_live_opacity.value()
            m.fill_direction = str(self.mod_fill_direction.currentData() or "start_to_end")

        item = self.module_list.item(row)
        if item:
            item.setText(f"[{type(m).__name__[:-6]}] {m.name}")

        self.ov.rebuild()

    def _shift_module_range(self):
        row = self.module_list.currentRow()
        if row < 0 or row >= len(self.ov.modules):
            return
        m = self.ov.modules[row]

        delta = self.mod_shift.value() / 100.0
        if abs(delta) < 1e-9:
            return

        width = max(0.0, float(m.t_end) - float(m.t_start))
        new_start = float(m.t_start) + delta
        new_end = float(m.t_end) + delta

        if new_start < 0.0:
            new_start = 0.0
            new_end = min(1.0, new_start + width)
        if new_end > 1.0:
            new_end = 1.0
            new_start = max(0.0, new_end - width)

        m.t_start = max(0.0, min(1.0, new_start))
        m.t_end = max(0.0, min(1.0, new_end))

        self.mod_t_start.blockSignals(True)
        self.mod_t_end.blockSignals(True)
        self.mod_t_start.setValue(m.t_start)
        self.mod_t_end.setValue(m.t_end)
        self.mod_t_start.blockSignals(False)
        self.mod_t_end.blockSignals(False)

        self.mod_shift.blockSignals(True)
        self.mod_shift.setValue(0.0)
        self.mod_shift.blockSignals(False)
        self.ov.rebuild()

    def _add_text(self):
        m = TextModule(name="Neuer Text", t_start=0.4, t_end=0.6,
                       text="296", sub_text="KM/H", font_size=16)
        self.ov.add_module(m)
        self._refresh_module_list()
        self.module_list.setCurrentRow(len(self.ov.modules) - 1)

    def _add_graph(self):
        m = GraphModule(name="Throttle", t_start=0.05, t_end=0.35,
                        data_key="throttle", color=(0, 255, 100, 220),
                        fill_color=(0, 255, 100, 60))
        self.ov.add_module(m)
        self._refresh_module_list()
        self.module_list.setCurrentRow(len(self.ov.modules) - 1)

    def _add_bar(self):
        m = BarModule(name="RPM", t_start=0.2, t_end=0.8,
                      side="inside", value=0.72,
                      bar_color=(0, 255, 100, 200))
        self.ov.add_module(m)
        self._refresh_module_list()
        self.module_list.setCurrentRow(len(self.ov.modules) - 1)

    def _add_radar(self):
        m = RadarModule(name="Sonar Radar", t_start=0.45, t_end=0.55,
                        radius_m=20.0, color=(80, 200, 255, 180),
                        side="center", height=2.0)
        self.ov.add_module(m)
        self._refresh_module_list()
        self.module_list.setCurrentRow(len(self.ov.modules) - 1)

    def _add_brake(self):
        m = BrakeIndicatorModule(name="Bremspunkt", t_start=0.3, t_end=0.7,
                                 dist_threshold=30.0, fill_color=(255, 50, 50, 200),
                                 side="outside", height=1.0)
        self.ov.add_module(m)
        self._refresh_module_list()
        self.module_list.setCurrentRow(len(self.ov.modules) - 1)

    def _add_brake_overlay(self):
        m = BrakeOverlayModule(
            name="Brems-Overlay",
            t_start=0.25,
            t_end=0.75,
            side="outside",
            height=1.0,
            color=(255, 255, 255, 220),
            ref_color_rgb=(255, 50, 50),
            live_color_rgb=(235, 235, 235),
            ref_opacity=70,
            live_opacity=45,
            fill_direction="start_to_end",
        )
        self.ov.add_module(m)
        self._refresh_module_list()
        self.module_list.setCurrentRow(len(self.ov.modules) - 1)

    def _add_tyre_wear(self):
        m = TyreWearModule(
            name="ReifenverschleiÃŸ",
            t_start=0.52,
            t_end=0.78,
            side="inside",
            height=1.0,
            color=(255, 255, 255, 220),
        )
        self.ov.add_module(m)
        self._refresh_module_list()
        self.module_list.setCurrentRow(len(self.ov.modules) - 1)

    def _add_relative_delta(self):
        m = RelativeDeltaModule(
            name="Delta Vordermann",
            t_start=0.40,
            t_end=0.60,
            side="inside",
            height=0.9,
            color=(255, 255, 255, 220),
            speed_scale=5.0,
            response_alpha=0.35,
            decay_factor=0.96,
        )
        self.ov.add_module(m)
        self._refresh_module_list()
        self.module_list.setCurrentRow(len(self.ov.modules) - 1)

    def _remove_module(self):
        row = self.module_list.currentRow()
        if row >= 0:
            self.ov.remove_module(row)
            self._refresh_module_list()
            self.detail_frame.hide()

