# main.py
"""Application entry point for HunStat desktop GUI."""

from __future__ import annotations

from pathlib import Path
import sys

_ROOT = str(Path(__file__).resolve().parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtWidgets import QApplication

from hunstat.gui import HunStatMainWindow
from hunstat.settings import AppSettings


def main() -> int:
    app = QApplication(sys.argv)
    settings = AppSettings.load()
    window = HunStatMainWindow(settings=settings)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())