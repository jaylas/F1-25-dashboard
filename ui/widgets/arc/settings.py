"""
Einstellungs-Fenster für Arc Overlay – Bogen-Form + Modul-Editor.
"""
from __future__ import annotations
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QSlider,
    QPushButton, QCheckBox, QComboBox, QLineEdit, QScrollArea,
    QColorDialog, QListWidget, QListWidgetItem, QDoubleSpinBox,
    QSpinBox, QFileDialog, QMessageBox
)
from PyQt6.QtGui import QColor

from widgets.arc.arc_overlay import ArcOverlayWidget
from widgets.arc.modules import ArcModule, TextModule, GraphModule, BarModule


class ArcSettingsWindow(QWidget):
    CONFIG_FILE = "arc_config.json"

    def __init__(self, overlay: ArcOverlayWidget):
        super().__init__()
        self.ov = overlay
        self.setWindowTitle("⬡ Bügel Einstellungen")
        self.resize(420, 700)
        self.color = QColor(overlay.base_color)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        root = QVBoxLayout()

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
                           ("Entfernen", self._remove_module)]:
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

        # Typ-spezifische Felder
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Text:"))
        self.mod_text = QLineEdit()
        self.mod_text.textChanged.connect(self._push_module)
        row3.addWidget(self.mod_text)
        row3.addWidget(QLabel("Sub:"))
        self.mod_subtext = QLineEdit()
        self.mod_subtext.textChanged.connect(self._push_module)
        row3.addWidget(self.mod_subtext)
        dl.addLayout(row3)

        row4 = QHBoxLayout()
        row4.addWidget(QLabel("Font:"))
        self.mod_fontsize = QSpinBox()
        self.mod_fontsize.setRange(4, 40); self.mod_fontsize.setValue(14)
        self.mod_fontsize.valueChanged.connect(self._push_module)
        row4.addWidget(self.mod_fontsize)
        row4.addWidget(QLabel("Data-Key:"))
        self.mod_datakey = QLineEdit()
        self.mod_datakey.textChanged.connect(self._push_module)
        row4.addWidget(self.mod_datakey)
        dl.addLayout(row4)

        self.mod_visible = QCheckBox("Sichtbar")
        self.mod_visible.setChecked(True)
        self.mod_visible.toggled.connect(self._push_module)
        dl.addWidget(self.mod_visible)

        self.detail_frame.setLayout(dl)
        l4.addWidget(self.detail_frame)
        self.detail_frame.hide()

        g4.setLayout(l4)
        root.addWidget(g4)

        # ── Speichern/Laden ───────────────────────────────────────────
        save_row = QHBoxLayout()
        btn_save = QPushButton("💾 Speichern")
        btn_save.setMinimumHeight(36)
        btn_save.setStyleSheet("font-weight:bold; background:#238636; color:white;")
        btn_save.clicked.connect(self._save)
        save_row.addWidget(btn_save)

        btn_load = QPushButton("📂 Laden")
        btn_load.setMinimumHeight(36)
        btn_load.clicked.connect(self._load)
        save_row.addWidget(btn_load)
        root.addLayout(save_row)

        root.addStretch()
        inner.setLayout(root)
        scroll.setWidget(inner)

        outer = QVBoxLayout()
        outer.addWidget(scroll)
        self.setLayout(outer)

        self._refresh_module_list()

    # ── Slider-Helfer ─────────────────────────────────────────────────
    def _slider(self, layout, label, key, lo, hi, default):
        lbl = QLabel(f"{label}: {default}")
        s = QSlider(Qt.Orientation.Horizontal)
        s.setMinimum(lo); s.setMaximum(hi); s.setValue(default)
        s.valueChanged.connect(lambda v, l=lbl, t=label: l.setText(f"{t}: {v}"))
        s.valueChanged.connect(self._push_arc)
        layout.addWidget(lbl); layout.addWidget(s)
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

    def _on_module_selected(self, row):
        if row < 0 or row >= len(self.ov.modules):
            self.detail_frame.hide()
            return
        self.detail_frame.show()
        m = self.ov.modules[row]
        # Block signals to avoid feedback loop
        for w in (self.mod_name, self.mod_t_start, self.mod_t_end,
                  self.mod_side, self.mod_height, self.mod_text,
                  self.mod_subtext, self.mod_fontsize, self.mod_datakey,
                  self.mod_visible):
            w.blockSignals(True)

        self.mod_name.setText(m.name)
        self.mod_t_start.setValue(m.t_start)
        self.mod_t_end.setValue(m.t_end)
        self.mod_side.setCurrentText(m.side)
        self.mod_height.setValue(m.height)
        self.mod_visible.setChecked(m.visible)

        if isinstance(m, TextModule):
            self.mod_text.setText(m.text)
            self.mod_subtext.setText(m.sub_text)
            self.mod_fontsize.setValue(m.font_size)
        if isinstance(m, GraphModule):
            self.mod_datakey.setText(m.data_key)

        for w in (self.mod_name, self.mod_t_start, self.mod_t_end,
                  self.mod_side, self.mod_height, self.mod_text,
                  self.mod_subtext, self.mod_fontsize, self.mod_datakey,
                  self.mod_visible):
            w.blockSignals(False)

    def _push_module(self, *_):
        row = self.module_list.currentRow()
        if row < 0 or row >= len(self.ov.modules):
            return
        m = self.ov.modules[row]
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

        self._refresh_module_list()
        self.module_list.setCurrentRow(row)
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

    def _remove_module(self):
        row = self.module_list.currentRow()
        if row >= 0:
            self.ov.remove_module(row)
            self._refresh_module_list()
            self.detail_frame.hide()

    # ── Speichern/Laden ───────────────────────────────────────────────
    def _save(self):
        self.ov.save_config(self.CONFIG_FILE)
        self.setWindowTitle("⬡ Bügel Einstellungen (Gespeichert!)")

    def _load(self):
        self.ov.load_config(self.CONFIG_FILE)
        # Sliders updaten
        for key, s in self.sl.items():
            val = getattr(self.ov, key, s.value())
            s.blockSignals(True)
            s.setValue(val)
            s.blockSignals(False)
        self.cb_round.setChecked(self.ov.round_caps)
        self.color = QColor(self.ov.base_color)
        self._refresh_module_list()
        self.setWindowTitle("⬡ Bügel Einstellungen (Geladen!)")
