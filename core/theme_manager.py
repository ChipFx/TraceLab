"""
core/theme_manager.py
Manages dark/light themes and trace color schemes.
"""

import json
import os
from dataclasses import dataclass
from typing import Dict


THEMES = {
    "dark": {
        "bg": "#0d0d0d",
        "bg_panel": "#141414",
        "bg_plot": "#050508",
        "grid_major": "#1a2a1a",
        "grid_minor": "#111811",
        "text": "#e0e0e0",
        "text_dim": "#666666",
        "accent": "#1e88e5",
        "border": "#2a2a2a",
        "cursor_a": "#ffcc00",
        "cursor_b": "#00ccff",
        "toolbar_bg": "#181818",
        "statusbar_bg": "#0d0d0d",
        "scope_bg": "#050508",
        "scope_grid": "#1a2a1a",
    },
    "light": {
        "bg": "#f0f0f0",
        "bg_panel": "#e8e8e8",
        "bg_plot": "#ffffff",
        "grid_major": "#ccddcc",
        "grid_minor": "#e8ece8",
        "text": "#101010",
        "text_dim": "#888888",
        "accent": "#1565c0",
        "border": "#cccccc",
        "cursor_a": "#cc8800",
        "cursor_b": "#0066aa",
        "toolbar_bg": "#e0e0e0",
        "statusbar_bg": "#d8d8d8",
        "scope_bg": "#ffffff",
        "scope_grid": "#ccddcc",
    },
    "rs_green": {  # R&S style green phosphor
        "bg": "#001200",
        "bg_panel": "#001800",
        "bg_plot": "#000800",
        "grid_major": "#003300",
        "grid_minor": "#001a00",
        "text": "#00ee44",
        "text_dim": "#006622",
        "accent": "#00cc33",
        "border": "#003300",
        "cursor_a": "#ffff00",
        "cursor_b": "#00ffff",
        "toolbar_bg": "#001200",
        "statusbar_bg": "#001000",
        "scope_bg": "#000800",
        "scope_grid": "#003300",
    },
}


class ThemeManager:
    def __init__(self, theme_name: str = "dark"):
        self.theme_name = theme_name
        self._colors = THEMES.get(theme_name, THEMES["dark"]).copy()

        # Load user overrides from settings file if present
        settings_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "settings.json")
        if os.path.exists(settings_path):
            try:
                with open(settings_path) as f:
                    s = json.load(f)
                if "theme" in s:
                    self.theme_name = s["theme"]
                    self._colors = THEMES.get(self.theme_name, THEMES["dark"]).copy()
                if "color_overrides" in s:
                    self._colors.update(s["color_overrides"])
            except Exception:
                pass

    def get(self, key: str, default: str = "#ffffff") -> str:
        return self._colors.get(key, default)

    def set_theme(self, name: str):
        if name in THEMES:
            self.theme_name = name
            self._colors = THEMES[name].copy()

    def available_themes(self):
        return list(THEMES.keys())

    def get_stylesheet(self, font_scale: float = 1.0) -> str:
        c = self._colors
        _fs = max(8, int(11 * font_scale))
        return f"""
        QMainWindow, QWidget {{
            background-color: {c['bg']};
            color: {c['text']};
            font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
            font-size: {_fs}px;
        }}
        QMenuBar {{
            background-color: {c['toolbar_bg']};
            color: {c['text']};
            border-bottom: 1px solid {c['border']};
        }}
        QMenuBar::item:selected {{
            background-color: {c['accent']};
        }}
        QMenu {{
            background-color: {c['bg_panel']};
            color: {c['text']};
            border: 1px solid {c['border']};
        }}
        QMenu::item:selected {{
            background-color: {c['accent']};
        }}
        QToolBar {{
            background-color: {c['toolbar_bg']};
            border: none;
            border-bottom: 1px solid {c['border']};
            spacing: 2px;
        }}
        QStatusBar {{
            background-color: {c['statusbar_bg']};
            color: {c['text_dim']};
            border-top: 1px solid {c['border']};
        }}
        QDockWidget {{
            background-color: {c['bg_panel']};
            color: {c['text']};
            titlebar-close-icon: none;
        }}
        QDockWidget::title {{
            background: {c['toolbar_bg']};
            padding: 4px;
            border: 1px solid {c['border']};
        }}
        QGroupBox {{
            color: {c['text']};
            border: 1px solid {c['border']};
            border-radius: 4px;
            margin-top: 16px;
            padding-top: 8px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 8px;
            color: {c['accent']};
        }}
        QPushButton {{
            background-color: {c['bg_panel']};
            color: {c['text']};
            border: 1px solid {c['border']};
            border-radius: 3px;
            padding: 4px 8px;
        }}
        QPushButton:hover {{
            background-color: {c['accent']};
            color: white;
        }}
        QPushButton:checked {{
            background-color: {c['accent']};
            color: white;
        }}
        QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
            background-color: {c['bg']};
            color: {c['text']};
            border: 1px solid {c['border']};
            border-radius: 3px;
            padding: 2px 4px;
        }}
        QCheckBox {{
            color: {c['text']};
        }}
        QCheckBox::indicator {{
            border: 1px solid {c['border']};
            background: {c['bg']};
        }}
        QCheckBox::indicator:checked {{
            background: {c['accent']};
        }}
        QTabWidget::pane {{
            border: 1px solid {c['border']};
        }}
        QTabBar::tab {{
            background: {c['bg_panel']};
            color: {c['text_dim']};
            padding: 5px 12px;
            border: 1px solid {c['border']};
        }}
        QTabBar::tab:selected {{
            background: {c['bg']};
            color: {c['text']};
            border-bottom: 2px solid {c['accent']};
        }}
        QScrollBar:vertical {{
            background: {c['bg_panel']};
            width: 10px;
        }}
        QScrollBar::handle:vertical {{
            background: {c['border']};
            border-radius: 5px;
        }}
        QDialog {{
            background-color: {c['bg']};
            color: {c['text']};
        }}
        QLabel {{
            color: {c['text']};
        }}
        QHeaderView::section {{
            background-color: {c['toolbar_bg']};
            color: {c['text']};
            border: 1px solid {c['border']};
            padding: 3px;
        }}
        QTableWidget {{
            background-color: {c['bg']};
            color: {c['text']};
            gridline-color: {c['border']};
        }}
        QSplitter::handle {{
            background: {c['border']};
        }}
        QRadioButton {{
            color: {c['text']};
        }}
        """

    def plot_colors(self) -> dict:
        """Return colors for use in pyqtgraph plots."""
        return {
            "background": self._colors["scope_bg"],
            "grid": self._colors["scope_grid"],
            "text": self._colors["text"],
            "cursor_a": self._colors["cursor_a"],
            "cursor_b": self._colors["cursor_b"],
        }
