"""
core/theme_manager.py
Single source of truth for all application colours and trace palettes.

Themes are discovered from ./themes/*.json at startup.
No colour data lives in any other Python file.

Schema of a theme JSON:
  name          str   — display name in menu
  tooltip       str   — hover tooltip
  plotview      dict  — Qt stylesheet colours (bg, text, accent, ...)
  statusbar     dict  — status bar palette (bar_bg, info_bg, ...)
  trace_colors  list  — ordered list of hex colour strings for traces

Consumers call:
  theme_manager.pv(key)          -> plotview colour str
  theme_manager.sb(key)          -> statusbar colour str
  theme_manager.trace_color(idx) -> hex colour str (wraps with modulo)
  theme_manager.trace_colors     -> full list
  theme_manager.get_stylesheet() -> Qt CSS string
  theme_manager.plot_colors()    -> dict for pyqtgraph
  theme_manager.statusbar_palette() -> dict for ScopeStatusBar
"""

import json
import os
from typing import Dict, List
from PyQt6.QtCore import QObject, pyqtSignal

THEMES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "themes")

# Absolute fallback palette — used only if ALL theme files are missing.
# This is NOT intended to be edited; create/edit theme files instead.
_FALLBACK_PLOTVIEW = {
    "bg": "#0d0d0d", "bg_panel": "#141414", "bg_plot": "#050508",
    "grid_major": "#1a2a1a", "grid_minor": "#111811",
    "text": "#e0e0e0", "text_dim": "#666666", "accent": "#1e88e5",
    "border": "#2a2a2a", "cursor_a": "#ffcc00", "cursor_b": "#00ccff",
    "toolbar_bg": "#181818", "statusbar_bg": "#0d0d0d",
    "scope_bg": "#050508", "scope_grid": "#1a2a1a",
    # "All Sinc" / "All Cub" button text in the channel panel
    "interp_sinc_color": "#ff8888",
    "interp_cub_color":  "#cc88ff",
}
_FALLBACK_STATUSBAR = {
    "bar_bg": "#0a0a14", "info_bg": "#141428", "info_text": "#d0d0e8",
    "info_dim": "#555577", "trig_text": "#44ee66", "sep": "#1e1e38",
    "logo_bg": "#060610", "logo_text": "#F0C040", "logo_sub": "#555577",
    # Interpolation badge colours (SINC / CUB blocks in channel status bar)
    "badge_sinc_bg": "#cc2222", "badge_sinc_fg": "#ffffff",
    "badge_cub_bg":  "#8822cc", "badge_cub_fg":  "#ffffff",
}
_FALLBACK_TRACES = [
    "#F0C040", "#40C0F0", "#F04080", "#40F080", "#F08040",
    "#A040F0", "#40F0F0", "#F0F040", "#F04040", "#4080F0",
]


class ThemeData:
    """Loaded and validated theme from a JSON file."""

    def __init__(self, path: str):
        self.path         = path
        self.file_id      = os.path.splitext(os.path.basename(path))[0]
        self.name         = self.file_id
        self.tooltip      = ""
        self.force_labels = False
        self._plotview    = dict(_FALLBACK_PLOTVIEW)
        self._statusbar   = dict(_FALLBACK_STATUSBAR)
        self._traces      = list(_FALLBACK_TRACES)
        self._load(path)

    def _load(self, path: str):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self.name         = data.get("name",         self.file_id)
            self.tooltip      = data.get("tooltip",      "")
            self.force_labels = bool(data.get("force_labels", False))
            if "plotview" in data:
                self._plotview.update(data["plotview"])
            if "statusbar" in data:
                self._statusbar.update(data["statusbar"])
            if "trace_colors" in data and data["trace_colors"]:
                self._traces = [str(c) for c in data["trace_colors"]]
        except Exception as e:
            print(f"[ThemeManager] Warning: could not load {path}: {e}")

    def pv(self, key: str, default: str = "#ffffff") -> str:
        return self._plotview.get(key, default)

    def sb(self, key: str, default: str = "#888888") -> str:
        return self._statusbar.get(key, default)

    def trace_color(self, index: int) -> str:
        if not self._traces:
            return "#ffffff"
        return self._traces[index % len(self._traces)]

    @property
    def trace_colors(self) -> List[str]:
        return list(self._traces)

    def to_plot_theme(self):
        """Return a PlotTheme with the plotview colours for this theme.

        Imported lazily so that pytraceview is not required at module level
        (useful if theme_manager is ever loaded in a non-plotting context).
        """
        from pytraceview.plot_theme import PlotTheme
        return PlotTheme(
            background   = self.pv("scope_bg"),
            grid         = self.pv("scope_grid"),
            text         = self.pv("text"),
            cursor_a     = self.pv("cursor_a"),
            cursor_b     = self.pv("cursor_b"),
            accent       = self.pv("accent"),
            force_labels = self.force_labels,
            theme_id     = self.file_id,
            trace_colors = list(self._traces),
        )

    def to_json(self) -> dict:
        return {
            "name":         self.name,
            "tooltip":      self.tooltip,
            "plotview":     dict(self._plotview),
            "statusbar":    dict(self._statusbar),
            "trace_colors": list(self._traces),
        }

    def save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.to_json(), f, indent=2)


class ThemeManager(QObject):
    """
    Single source of truth for application theming.
    Discovers themes from THEMES_DIR at construction.
    """

    themeChanged = pyqtSignal(object)

    def __init__(self, active_id: str = "dark"):
        super().__init__()
        self._themes: Dict[str, ThemeData] = {}
        self._active: ThemeData = ThemeData.__new__(ThemeData)
        # Bootstrap fallback theme (no file needed)
        self._active.file_id      = "dark"
        self._active.name         = "Dark"
        self._active.tooltip      = ""
        self._active.force_labels = False
        self._active._plotview    = dict(_FALLBACK_PLOTVIEW)
        self._active._statusbar   = dict(_FALLBACK_STATUSBAR)
        self._active._traces      = list(_FALLBACK_TRACES)

        self.discover()
        self.set_theme(active_id)

    # ── Discovery ─────────────────────────────────────────────────────────────

    def discover(self) -> List[str]:
        """Scan THEMES_DIR and load/reload all .json files."""
        self._themes.clear()
        os.makedirs(THEMES_DIR, exist_ok=True)

        # If no themes exist yet, seed the directory
        if not any(f.endswith(".json") for f in os.listdir(THEMES_DIR)):
            self._seed_defaults()

        for fname in sorted(os.listdir(THEMES_DIR)):
            if fname.endswith(".json"):
                path = os.path.join(THEMES_DIR, fname)
                td = ThemeData(path)
                self._themes[td.file_id] = td

        return list(self._themes.keys())

    def _seed_defaults(self):
        """Write built-in themes if themes dir is empty (first run)."""
        pass  # themes/ is already in the repo — nothing to seed

    # ── Theme switching ───────────────────────────────────────────────────────

    def set_theme(self, file_id: str) -> bool:
        changed = False
        if file_id in self._themes:
            self._active = self._themes[file_id]
            changed = True
        # Try by display name (for backwards compat with settings.json)
        if not changed:
            for td in self._themes.values():
                if td.name.lower() == file_id.lower():
                    self._active = td
                    changed = True
                    break
        # Fall back to first available theme
        if not changed and self._themes:
            self._active = next(iter(self._themes.values()))
        self.themeChanged.emit(self._active)
        return changed

    @property
    def theme_name(self) -> str:
        """File ID of the active theme (used as settings key)."""
        return self._active.file_id

    @property
    def display_name(self) -> str:
        return self._active.name

    @property
    def active_theme(self) -> ThemeData:
        return self._active

    @property
    def available_themes(self) -> Dict[str, ThemeData]:
        """Ordered dict of file_id -> ThemeData."""
        return dict(self._themes)

    # ── Colour accessors (forwarded from active theme) ────────────────────────

    def pv(self, key: str, default: str = "#ffffff") -> str:
        """Get a plotview colour."""
        return self._active.pv(key, default)

    def sb(self, key: str, default: str = "#888888") -> str:
        """Get a statusbar colour."""
        return self._active.sb(key, default)

    def trace_color(self, index: int) -> str:
        """Get the trace colour for a given index (wraps with modulo)."""
        return self._active.trace_color(index)

    @property
    def trace_colors(self) -> List[str]:
        return self._active.trace_colors

    # Legacy accessor for code that still calls theme_manager.get(key)
    def get(self, key: str, default: str = "#ffffff") -> str:
        return self.pv(key, default)

    # ── Derived compound objects ──────────────────────────────────────────────

    def plot_colors(self) -> dict:
        """Return colours for pyqtgraph plot widgets."""
        return {
            "background": self.pv("scope_bg"),
            "grid":       self.pv("scope_grid"),
            "text":       self.pv("text"),
            "cursor_a":   self.pv("cursor_a"),
            "cursor_b":   self.pv("cursor_b"),
        }

    def statusbar_palette(self) -> dict:
        """Return the full statusbar palette dict (all keys from theme file)."""
        return dict(self._active._statusbar)

    def plotview_palette(self) -> dict:
        """Return the full plotview palette dict (all keys from theme file)."""
        return dict(self._active._plotview)

    def get_stylesheet(self, font_scale: float = 1.0) -> str:
        c = self._active._plotview
        _fs = max(8, int(11 * font_scale))
        return f"""
        QMainWindow, QWidget {{
            background-color: {c.get('bg','#0d0d0d')};
            color: {c.get('text','#e0e0e0')};
            font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
            font-size: {_fs}px;
        }}
        QMenuBar {{
            background-color: {c.get('toolbar_bg','#181818')};
            color: {c.get('text','#e0e0e0')};
            border-bottom: 1px solid {c.get('border','#2a2a2a')};
            font-size: {_fs}px;
        }}
        QMenuBar::item:selected {{ background-color: {c.get('accent','#1e88e5')}; }}
        QMenu {{
            background-color: {c.get('bg_panel','#141414')};
            color: {c.get('text','#e0e0e0')};
            border: 1px solid {c.get('border','#2a2a2a')};
            font-size: {_fs}px;
        }}
        QMenu::item:selected {{ background-color: {c.get('accent','#1e88e5')}; }}
        QToolBar {{
            background-color: {c.get('toolbar_bg','#181818')};
            border: none;
            border-bottom: 1px solid {c.get('border','#2a2a2a')};
            spacing: 2px;
        }}
        QStatusBar {{
            background-color: {c.get('statusbar_bg','#0d0d0d')};
            color: {c.get('text_dim','#666666')};
            border-top: 1px solid {c.get('border','#2a2a2a')};
        }}
        QGroupBox {{
            color: {c.get('text','#e0e0e0')};
            border: 1px solid {c.get('border','#2a2a2a')};
            border-radius: 4px;
            margin-top: 16px;
            padding-top: 8px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 8px;
            color: {c.get('accent','#1e88e5')};
        }}
        QPushButton {{
            background-color: {c.get('bg_panel','#141414')};
            color: {c.get('text','#e0e0e0')};
            border: 1px solid {c.get('border','#2a2a2a')};
            border-radius: 3px;
            padding: 4px 8px;
        }}
        QPushButton:hover {{ background-color: {c.get('accent','#1e88e5')}; color: white; }}
        QPushButton:checked {{ background-color: {c.get('accent','#1e88e5')}; color: white; }}
        QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
            background-color: {c.get('bg','#0d0d0d')};
            color: {c.get('text','#e0e0e0')};
            border: 1px solid {c.get('border','#2a2a2a')};
            border-radius: 3px;
            padding: 2px 4px;
        }}
        QCheckBox {{ color: {c.get('text','#e0e0e0')}; }}
        QCheckBox::indicator {{
            border: 1px solid {c.get('border','#2a2a2a')};
            background: {c.get('bg','#0d0d0d')};
        }}
        QCheckBox::indicator:checked {{ background: {c.get('accent','#1e88e5')}; }}
        QTabWidget::pane {{ border: 1px solid {c.get('border','#2a2a2a')}; }}
        QTabBar::tab {{
            background: {c.get('bg_panel','#141414')};
            color: {c.get('text_dim','#666666')};
            padding: 5px 12px;
            border: 1px solid {c.get('border','#2a2a2a')};
        }}
        QTabBar::tab:selected {{
            background: {c.get('bg','#0d0d0d')};
            color: {c.get('text','#e0e0e0')};
            border-bottom: 2px solid {c.get('accent','#1e88e5')};
        }}
        QScrollBar:vertical {{ background: {c.get('bg_panel','#141414')}; width: 10px; }}
        QScrollBar::handle:vertical {{ background: {c.get('border','#2a2a2a')}; border-radius: 5px; }}
        QDialog {{ background-color: {c.get('bg','#0d0d0d')}; color: {c.get('text','#e0e0e0')}; }}
        QLabel {{ color: {c.get('text','#e0e0e0')}; }}
        QHeaderView::section {{
            background-color: {c.get('toolbar_bg','#181818')};
            color: {c.get('text','#e0e0e0')};
            border: 1px solid {c.get('border','#2a2a2a')};
            padding: 3px;
        }}
        QTableWidget {{
            background-color: {c.get('bg','#0d0d0d')};
            color: {c.get('text','#e0e0e0')};
            gridline-color: {c.get('border','#2a2a2a')};
        }}
        QSplitter::handle {{ background: {c.get('border','#2a2a2a')}; }}
        QRadioButton {{ color: {c.get('text','#e0e0e0')}; }}
        QRadioButton::indicator {{
            width: 12px; height: 12px;
            border: 1px solid {c.get('text_dim','#666666')};
            border-radius: 7px;
            background: {c.get('bg','#0d0d0d')};
        }}
        QRadioButton::indicator:checked {{
            background: {c.get('accent','#1e88e5')};
            border-color: {c.get('accent','#1e88e5')};
        }}
        QRadioButton::indicator:hover {{
            border-color: {c.get('accent','#1e88e5')};
        }}
        QDockWidget {{
            background-color: {c.get('bg_panel','#141414')};
            color: {c.get('text','#e0e0e0')};
        }}
        QDockWidget::title {{
            background: {c.get('toolbar_bg','#181818')};
            padding: 4px;
            border: 1px solid {c.get('border','#2a2a2a')};
        }}
        """

    # ── Theme editor ──────────────────────────────────────────────────────────

    def open_editor(self, parent=None, on_apply=None):
        """Open the theme editor window."""
        from core.theme_editor import ThemeEditorWindow
        win = ThemeEditorWindow(self, parent=parent, on_apply=on_apply)
        win.show()
        return win
