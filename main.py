import sys

from PyQt6.QtWidgets import QApplication

from ui.windows.launcher import LauncherWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    launcher = LauncherWindow()
    launcher.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
