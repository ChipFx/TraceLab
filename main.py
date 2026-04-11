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

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
