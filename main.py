#!/usr/bin/env python3
"""
ChipFX TraceLab - Modular oscilloscope data viewer
Entry point
"""

import sys
import os

# Add the project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from core.main_window import MainWindow


def main():
    # Enable high-DPI support
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    app = QApplication(sys.argv)
    app.setApplicationName("ChipFX TraceLab")
    app.setApplicationVersion("1.0.0")
    app.setOrganizationName("ChipFX")

    # Load application stylesheet
    from core.theme_manager import ThemeManager
    theme = ThemeManager()
    app.setStyleSheet(theme.get_stylesheet())

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    # sys.exit() raises SystemExit, whose traceback captures the calling
    # frame.  Calling it *inside* main() would pin window/app/theme in that
    # frame; Python then GC's them during SystemExit cleanup while Qt's own
    # teardown (QMenuBar event filters etc.) is partially done → segfault.
    # Instead, let main() return normally so its locals are refcount-dropped
    # cleanly before SystemExit is ever raised.
    import gc
    _exit_code = main()
    gc.collect()          # flush any lingering Python refs to Qt objects
    sys.exit(_exit_code)
