from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPainter, QMouseEvent
from PyQt6.QtWidgets import QWidget

from ui.widgets.arc.radar_renderer import draw_radar
from core.models import TelemetryFrame

class RadarWindow(QWidget):
    """
    Eigenständiges, frei platzierbares Radar/Sonar-Fenster.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint | 
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(300, 300)
        
        self.radius_m = 20.0
        self._player_pos = (0.0, 0.0, 0.0)
        self._player_fwd = (0.0, 0.0, 1.0)
        self._player_right = (1.0, 0.0, 0.0)
        self._opponents = []
        
        self._drag_pos = None

    def update_telemetry(self, frame: TelemetryFrame):
        if not self.isVisible():
            return
        self._player_pos = frame.player_pos
        self._player_fwd = frame.player_forward
        self._player_right = frame.player_right
        self._opponents = frame.opponents
        self.update()

    def set_radius(self, radius_m: float):
        self.radius_m = radius_m
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        size = min(self.width(), self.height()) - 10
        cx = self.width() / 2
        cy = self.height() / 2
        
        draw_radar(
            p, cx, cy, size,
            self._player_pos, self._player_fwd, self._player_right, self._opponents,
            radius_m=self.radius_m,
            bg_color=(10, 14, 20, 255), # Etwas dunklerer Hintergrund fürs Standalone Fenster
            border_color=(80, 200, 255, 180)
        )
        p.end()

    # Drag & Drop Support
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drag_pos is not None and event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_pos = None
        event.accept()
