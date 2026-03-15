import sys

from PyQt6.QtWidgets import QApplication

from windows.overlay import OverlayWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    overlay = OverlayWindow()
    overlay.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
