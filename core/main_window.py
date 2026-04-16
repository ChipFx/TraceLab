"""
core/main_window.py
Main application window.
"""

import os, sys, json, copy
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QToolBar, QFileDialog, QMessageBox, QLabel, QPushButton,
    QCheckBox, QMenu, QInputDialog, QDialog
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QActionGroup, QKeySequence, QIcon, QPixmap, QColor
from typing import List, Optional

from core.trace_model import TraceModel
from core.theme_manager import ThemeManager
from core.data_loader import load_csv
from core.import_dialog import ImportDialog
from core.scope_plot_widget import ScopePlotWidget
from core.channel_panel import ChannelPanel
from core.cursor_panel import CursorPanel
from core.trigger_panel import TriggerPanel
from core.plugin_manager import PluginManager
from core.scope_status_bar import ScopeStatusBar
from core.draw_mode import (
    DEFAULT_DENSITY_PEN_MAPPING,
    DEFAULT_DRAW_MODE,
    DRAW_MODE_ADVANCED,
    DRAW_MODE_CLEAR,
    DRAW_MODE_FAST,
    DRAW_MODE_SIMPLE,
    DRAW_MODE_TOOLTIPS,
)
from core.retrigger import (
    MODE_OFF, MODE_PERSIST_FUTURE, MODE_PERSIST_PAST,
    MODE_AVERAGING, MODE_INTERPOLATION, PERSIST_MODES,
    PERSISTENCE_DEFAULTS, AVERAGING_DEFAULTS, INTERPOLATION_DEFAULTS,
    apply_mode_with_triggers as retrigger_apply_with_triggers,
    find_all_triggers_with_times,
)

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "settings.json")


def _hex_perceived_luminance(hex_color: str) -> float:
    """Perceived luminance 0–255 for a #rrggbb color (ITU-R BT.601 coefficients)."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = h[0] * 2 + h[1] * 2 + h[2] * 2
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        return 0.0
    return 0.299 * r + 0.587 * g + 0.114 * b


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ChipFX TraceLab")
        self.resize(1400, 800)

        self.theme = ThemeManager()
        self._traces: List[TraceModel] = []
        self._plugins = PluginManager()
        self._plugins.discover()
        self._settings: dict = self._load_settings_dict()

        self._build_ui()
        self._build_menus()
        self._build_toolbar()
        self._build_statusbar()
        self._update_plugin_menu()
        self._restore_geometry()

    # ── Settings helpers ───────────────────────────────────────────────

    def _load_settings_dict(self) -> dict:
        if os.path.exists(SETTINGS_PATH):
            try:
                with open(SETTINGS_PATH) as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_settings(self):
        s = self._settings.copy()
        s["theme"] = self.theme.theme_name
        s["geometry"] = self.saveGeometry().toHex().data().decode()
        s["y_lock_auto"] = self._plot.y_lock_auto
        s["interp_mode"] = self._interp_mode
        s["viewport_min_pts"] = self._viewport_min_pts
        s["draw_mode"] = self._draw_mode
        s["density_pen_mapping"] = dict(self._density_pen_mapping)
        s["import_replace"] = self._import_replace
        s["import_reset_view"] = self._import_reset_view
        s["fft_min_freq"] = self._fft_min_freq
        s["retrigger_mode"] = self._retrigger_mode
        s["persistence"] = dict(self._persist_settings)
        s["averaging"] = dict(self._averaging_settings)
        s["interpolation"] = dict(self._interpolation_settings)
        s["original_dimmed_opacity"] = self._original_dimmed_opacity
        s["dashed_line_config"] = dict(self._dashed_line_config)
        s["auto_retrigger"] = self._trigger_panel.chk_auto_retrigger.isChecked()
        try:
            with open(SETTINGS_PATH, "w") as f:
                json.dump(s, f, indent=2)
        except Exception:
            pass

    def _restore_geometry(self):
        geo = self._settings.get("geometry")
        if geo:
            try:
                self.restoreGeometry(bytes.fromhex(geo))
            except Exception:
                pass
        # Apply saved theme
        if "theme" in self._settings:
            self._set_theme(self._settings["theme"], save=False)
        # Apply saved font scale
        if "font_scale" in self._settings:
            self._apply_font_scale(self._settings["font_scale"])
        # Set branding on status bar
        self._scope_status.set_branding(self._get_branding_path())

    # ── UI Construction ────────────────────────────────────────────────

    def _build_ui(self):
        self._import_replace = self._settings.get("import_replace", True)
        self._import_reset_view = self._settings.get("import_reset_view", True)
        self._y_lock_auto = self._settings.get("y_lock_auto", True)
        self._fft_min_freq = self._settings.get("fft_min_freq", 1.0)
        self._viewport_min_pts = self._settings.get("viewport_min_pts", 1024)
        self._draw_mode = self._settings.get("draw_mode", DEFAULT_DRAW_MODE)
        self._density_pen_mapping = dict(
            self._settings.get("density_pen_mapping",
                               DEFAULT_DENSITY_PEN_MAPPING))
        self._last_trigger_info = ""

        # ── Retrigger / persistence state ─────────────────────────────────────
        self._retrigger_mode: str = self._settings.get(
            "retrigger_mode", MODE_OFF)
        self._persist_settings: dict = dict(
            {**PERSISTENCE_DEFAULTS,
             **self._settings.get("persistence", {})})
        self._averaging_settings: dict = dict(
            {**AVERAGING_DEFAULTS,
             **self._settings.get("averaging", {})})
        self._interpolation_settings: dict = dict(
            {**INTERPOLATION_DEFAULTS,
             **self._settings.get("interpolation", {})})
        self._original_dimmed_opacity: float = max(0.1, min(0.9, float(
            self._settings.get("original_dimmed_opacity", 0.5))))
        self._dashed_line_config: dict = {
            "dash": 8, "space": 4,
            **self._settings.get("dashed_line_config", {}),
        }
        self._last_trigger_t_pos: Optional[float] = None
        self._last_retrigger_results: dict = {}
        self._last_retrigger_span: float = 0.0   # tracks view_span at last calc

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)

        self._channel_panel = ChannelPanel()
        self._channel_panel.visibility_changed.connect(self._on_trace_visibility)
        self._channel_panel.color_changed.connect(self._on_trace_color)
        self._channel_panel.trace_removed.connect(self._remove_trace)
        self._channel_panel.order_changed.connect(self._on_channel_order_changed)
        self._channel_panel.interp_changed.connect(self._on_channel_interp_changed)
        self._channel_panel.reset_color_requested.connect(self._on_reset_trace_color)
        self._splitter.addWidget(self._channel_panel)

        self._interp_mode = self._settings.get("interp_mode", "linear")
        self._plot = ScopePlotWidget(
            self.theme, self._y_lock_auto,
            self._interp_mode, self._viewport_min_pts,
            self._draw_mode, self._density_pen_mapping)
        self._plot.cursor_values_changed.connect(self._on_cursor_values)
        self._plot.sinc_active_changed.connect(self._on_sinc_active_changed)
        self._plot.view_changed.connect(self._refresh_status_bar)
        self._plot.view_changed.connect(self._on_view_changed_retrigger)

        # Wrap plot + status bar in a vertical container
        plot_container = QWidget()
        pcl = QVBoxLayout(plot_container)
        pcl.setContentsMargins(0, 0, 0, 0)
        pcl.setSpacing(0)

        # Move range bar OUT of scope_plot_widget into here, so the
        # status bar sits between plot and range bar (like a real scope).
        # The plot widget's internal layout: scroll/overlay is first, range
        # bar last. We grab the plot (which includes range bar internally).
        pcl.addWidget(self._plot)

        self._scope_status = ScopeStatusBar(self.theme.statusbar_palette())
        self._scope_status.toggle_trace_interp.connect(
            self._on_status_bar_toggle_interp)
        # Insert status bar BEFORE the range bar — achieved by the fact
        # that _plot already has its range_bar at its bottom. We just add
        # the status bar after _plot in the outer container so visually:
        # [plot+range_bar] [status_bar] -- but range_bar is inside _plot.
        # Better: hide range_bar from _plot and add it here after status bar.
        self._plot._range_bar.setParent(None)  # detach from plot's layout
        pcl.addWidget(self._scope_status)
        pcl.addWidget(self._plot._range_bar)

        self._plot_container = plot_container
        self._splitter.addWidget(plot_container)

        # Right panel: cursor readout + trigger, stacked vertically
        right_splitter = QSplitter(Qt.Orientation.Vertical)

        self._cursor_panel = CursorPanel()
        self._cursor_panel.place_cursor.connect(self._start_cursor_placement)
        self._cursor_panel.set_t0_at_a.connect(self._cursor_set_t0_at_a)
        self._cursor_panel.jump_to_t0.connect(self._jump_to_t0)
        right_splitter.addWidget(self._cursor_panel)

        self._trigger_panel = TriggerPanel()
        self._trigger_panel.trigger_found.connect(self._on_trigger_found)
        self._trigger_panel.set_time_zero.connect(self._on_trigger_set_t0)
        self._trigger_panel.place_cursor.connect(self._plot.set_cursor)
        self._trigger_panel.retrigger_update_requested.connect(self._reapply_retrigger)
        self._trigger_panel.chk_auto_retrigger.setChecked(
            self._settings.get("auto_retrigger", False))
        right_splitter.addWidget(self._trigger_panel)

        right_splitter.setStretchFactor(0, 2)
        right_splitter.setStretchFactor(1, 1)
        right_splitter.setSizes([420, 280])
        self._splitter.addWidget(right_splitter)

        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setStretchFactor(2, 0)
        self._splitter.setSizes([180, 820, 310])
        main_layout.addWidget(self._splitter)

    def _build_menus(self):
        mb = self.menuBar()

        # ── File ──────────────────────────────────────────────────────
        file_menu = mb.addMenu("File")
        a = file_menu.addAction("Open CSV...")
        a.setShortcut(QKeySequence("Ctrl+O"))
        a.triggered.connect(self._open_csv)

        file_menu.addSeparator()
        a = file_menu.addAction("Export Visible Data as CSV...")
        a.triggered.connect(self._export_csv)
        a = file_menu.addAction("Save Screenshot...")
        a.setShortcut(QKeySequence("Ctrl+P"))
        a.triggered.connect(self._save_screenshot)

        file_menu.addSeparator()
        a = file_menu.addAction("Clear All Traces")
        a.triggered.connect(self._clear_all)
        file_menu.addSeparator()
        a = file_menu.addAction("Quit")
        a.setShortcut(QKeySequence("Ctrl+Q"))
        a.triggered.connect(self.close)

        # ── View ──────────────────────────────────────────────────────
        view_menu = mb.addMenu("View")
        a = view_menu.addAction("Split Lanes (LeCroy Style)")
        a.triggered.connect(lambda: self._set_display_mode("split"))
        a = view_menu.addAction("Overlay All Traces")
        a.triggered.connect(lambda: self._set_display_mode("overlay"))

        view_menu.addSeparator()
        a = view_menu.addAction("Zoom to Fit")
        a.setShortcut(QKeySequence("F"))
        a.triggered.connect(self._zoom_full_safe)
        a = view_menu.addAction("Fit Time (X only)")
        a.setShortcut(QKeySequence("T"))
        a.triggered.connect(self._plot.zoom_fit_x)
        a = view_menu.addAction("Fit Amplitude (Y only)")
        a.setShortcut(QKeySequence("A"))
        a.triggered.connect(self._plot.zoom_fit_y)

        view_menu.addSeparator()
        a = view_menu.addAction("Zoom In (X)")
        a.setShortcut(QKeySequence("+"))
        a.triggered.connect(lambda: self._plot.zoom_in(0.5))
        a = view_menu.addAction("Zoom Out (X)")
        a.setShortcut(QKeySequence("-"))
        a.triggered.connect(lambda: self._plot.zoom_out(2.0))

        view_menu.addSeparator()
        # Y-lock toggle (checkable)
        self._act_y_lock = view_menu.addAction("Lock Y to Auto-Scale")
        self._act_y_lock.setCheckable(True)
        self._act_y_lock.setChecked(self._y_lock_auto)
        self._act_y_lock.setShortcut(QKeySequence("L"))
        self._act_y_lock.triggered.connect(self._toggle_y_lock)

        view_menu.addSeparator()
        # Interpolation mode submenu
        interp_menu = view_menu.addMenu("Interpolation")
        self._act_interp_linear = interp_menu.addAction("Linear (default)")
        self._act_interp_linear.setCheckable(True)
        self._act_interp_sinc   = interp_menu.addAction("Sinc (sin(x)/x)")
        self._act_interp_sinc.setCheckable(True)
        self._act_interp_cubic  = interp_menu.addAction("Cubic Spline")
        self._act_interp_cubic.setCheckable(True)
        ag = QActionGroup(self)
        ag.setExclusive(True)
        ag.addAction(self._act_interp_linear)
        ag.addAction(self._act_interp_sinc)
        ag.addAction(self._act_interp_cubic)
        m = self._interp_mode
        (self._act_interp_sinc if m == "sinc"
         else self._act_interp_cubic if m == "cubic"
         else self._act_interp_linear).setChecked(True)
        self._act_interp_linear.triggered.connect(
            lambda: self._set_interp_mode("linear"))
        self._act_interp_sinc.triggered.connect(
            lambda: self._set_interp_mode("sinc"))
        self._act_interp_cubic.triggered.connect(
            lambda: self._set_interp_mode("cubic"))

        view_menu.addSeparator()
        draw_menu = view_menu.addMenu("Draw Mode")
        self._draw_mode_actions = {}
        draw_group = QActionGroup(self)
        draw_group.setExclusive(True)
        for mode in (
            DRAW_MODE_SIMPLE,
            DRAW_MODE_FAST,
            DRAW_MODE_CLEAR,
            DRAW_MODE_ADVANCED,
        ):
            act = draw_menu.addAction(mode)
            act.setCheckable(True)
            act.setToolTip(DRAW_MODE_TOOLTIPS[mode])
            act.setStatusTip(DRAW_MODE_TOOLTIPS[mode])
            act.setChecked(mode == self._draw_mode)
            act.triggered.connect(
                lambda checked, selected_mode=mode: self._set_draw_mode(selected_mode))
            draw_group.addAction(act)
            self._draw_mode_actions[mode] = act

        view_menu.addSeparator()
        # Dynamically populated from themes/ folder
        self._theme_submenu = view_menu.addMenu("Theme")
        self._rebuild_theme_menu()

        # ── Retrigger ──────────────────────────────────────────────────
        view_menu.addSeparator()
        rt_menu = view_menu.addMenu("Retrigger")

        persist_menu = rt_menu.addMenu("Persistence")
        persist_group = QActionGroup(self)
        persist_group.setExclusive(True)

        self._act_persist_off = persist_menu.addAction("Off")
        self._act_persist_off.setCheckable(True)
        self._act_persist_off.setChecked(self._retrigger_mode == MODE_OFF)
        persist_group.addAction(self._act_persist_off)
        self._act_persist_off.triggered.connect(
            lambda: self._set_retrigger_mode(MODE_OFF))

        self._act_persist_future = persist_menu.addAction("Future Persist")
        self._act_persist_future.setCheckable(True)
        self._act_persist_future.setChecked(
            self._retrigger_mode == MODE_PERSIST_FUTURE)
        self._act_persist_future.setToolTip(
            "First trigger shown as hard line; later triggers fade into "
            "the future below it.")
        persist_group.addAction(self._act_persist_future)
        self._act_persist_future.triggered.connect(
            lambda: self._set_retrigger_mode(MODE_PERSIST_FUTURE))

        self._act_persist_past = persist_menu.addAction("Past Persist (Normal)")
        self._act_persist_past.setCheckable(True)
        self._act_persist_past.setChecked(
            self._retrigger_mode == MODE_PERSIST_PAST)
        self._act_persist_past.setToolTip(
            "Last trigger shown as hard line; earlier triggers fade into "
            "history below it.  Classic oscilloscope persistence.")
        persist_group.addAction(self._act_persist_past)
        self._act_persist_past.triggered.connect(
            lambda: self._set_retrigger_mode(MODE_PERSIST_PAST))

        rt_menu.addSeparator()

        self._act_rt_averaging = rt_menu.addAction("Averaging")
        self._act_rt_averaging.setCheckable(True)
        self._act_rt_averaging.setChecked(self._retrigger_mode == MODE_AVERAGING)
        self._act_rt_averaging.setToolTip(
            "Average multiple trigger-aligned segments to reduce noise.")
        self._act_rt_averaging.triggered.connect(
            self._toggle_retrigger_averaging)

        self._act_rt_interp = rt_menu.addAction("Interpolate")
        self._act_rt_interp.setCheckable(True)
        self._act_rt_interp.setChecked(
            self._retrigger_mode == MODE_INTERPOLATION)
        self._act_rt_interp.setToolTip(
            "Interleave multiple trigger-aligned segments to increase "
            "effective sample resolution.")
        self._act_rt_interp.triggered.connect(
            self._toggle_retrigger_interpolation)

        # ── Analysis ──────────────────────────────────────────────────
        analysis_menu = mb.addMenu("Analysis")
        a = analysis_menu.addAction("FFT...")
        a.setShortcut(QKeySequence("Ctrl+F"))
        a.triggered.connect(self._show_fft)
        a = analysis_menu.addAction("Filter...")
        a.triggered.connect(self._show_filter)
        a = analysis_menu.addAction("Clear All Filters")
        a.triggered.connect(self._clear_all_filters)
        analysis_menu.addSeparator()
        interp_trace_menu = analysis_menu.addMenu("Interpolation per Channel")
        self._per_trace_interp_menu = interp_trace_menu
        # Populated dynamically in _update_per_trace_interp_menu()

        analysis_menu.addSeparator()
        a = analysis_menu.addAction("Add Label at Cursor A...")
        a.triggered.connect(self._add_label_at_cursor)
        a = analysis_menu.addAction("Clear All Labels")
        a.triggered.connect(self._clear_all_labels)

        # ── Settings ──────────────────────────────────────────────────
        settings_menu = mb.addMenu("Settings")
        settings_menu.addAction("Font Scale...").triggered.connect(
            self._show_font_scale_dialog)
        settings_menu.addAction("Decimal Separator...").triggered.connect(
            self._show_decimal_sep_dialog)
        settings_menu.addSeparator()
        settings_menu.addAction("Edit Current Theme...").triggered.connect(
            self._open_theme_editor)
        settings_menu.addAction("Reload Themes from Disk").triggered.connect(
            self._reload_themes)
        settings_menu.addSeparator()
        self._act_remember_folder = settings_menu.addAction("Remember Last Folder")
        self._act_remember_folder.setCheckable(True)
        self._act_remember_folder.setChecked(
            self._settings.get("remember_folder", True))
        self._act_remember_folder.setToolTip(
            "When enabled, open/save dialogs start in the last used folder.")
        self._act_remember_folder.triggered.connect(self._toggle_remember_folder)

        settings_menu.addSeparator()

        # ── Persistence settings ──────────────────────────────────────
        pm = settings_menu.addMenu("Persistence Settings")
        pm.addAction("Count…").triggered.connect(self._dlg_persist_count)
        pm.addAction("Selection…").triggered.connect(self._dlg_persist_selection)
        pm.addAction("Emphasis…").triggered.connect(self._dlg_persist_emphasis)
        pm.addAction("Opacity Decay…").triggered.connect(
            self._dlg_persist_opacity)
        pm.addAction("Width Growth…").triggered.connect(self._dlg_persist_width)
        pm.addSeparator()
        pm.addAction("Restore Defaults").triggered.connect(
            self._reset_persist_defaults)

        am = settings_menu.addMenu("Averaging Settings")
        am.addAction("Count…").triggered.connect(self._dlg_avg_count)
        am.addSeparator()
        avg_orig = am.addMenu("Original Data")
        self._avg_orig_actions = self._build_original_display_menu(
            avg_orig, self._averaging_settings,
            lambda: self._reapply_retrigger())
        am.addSeparator()
        am.addAction("Restore Defaults").triggered.connect(
            self._reset_avg_defaults)

        im = settings_menu.addMenu("Interpolation Settings")
        im.addAction("Count…").triggered.connect(self._dlg_interp_count)
        im.addSeparator()
        interp_orig = im.addMenu("Original Data")
        self._interp_orig_actions = self._build_original_display_menu(
            interp_orig, self._interpolation_settings,
            lambda: self._reapply_retrigger())
        im.addSeparator()
        im.addAction("Restore Defaults").triggered.connect(
            self._reset_interp_defaults)

        settings_menu.addSeparator()
        settings_menu.addAction("Dimmed Opacity…").triggered.connect(
            self._dlg_dimmed_opacity)
        settings_menu.addAction("Dashed Line Config…").triggered.connect(
            self._dlg_dashed_line_config)

        # ── Plugins ───────────────────────────────────────────────────────
        self._plugins_menu = mb.addMenu("Plugins")
        self._plugins_menu.addAction("Reload Plugins").triggered.connect(
            self._reload_plugins)
        self._plugins_menu.addAction("Open Plugins Folder").triggered.connect(
            self._open_plugins_dir)
        self._plugins_menu.addSeparator()
        self._plugin_actions_start = len(self._plugins_menu.actions())

        # ── Help ──────────────────────────────────────────────────────
        help_menu = mb.addMenu("Help")
        help_menu.addAction("About TraceLab").triggered.connect(self._show_about)

    def _build_toolbar(self):
        tb = self.addToolBar("Main")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)

        def add(label, fn, tip=""):
            a = QAction(label, self)
            a.triggered.connect(fn)
            if tip:
                a.setToolTip(tip)
            tb.addAction(a)
            return a

        add("Open", self._open_csv)
        add("Clear", self._clear_all, "Clear all traces")
        tb.addSeparator()
        add("Split", lambda: self._set_display_mode("split"))
        add("Overlay", lambda: self._set_display_mode("overlay"))
        tb.addSeparator()
        add("Cursor A", lambda: self._start_cursor_placement(0))
        add("Cursor B", lambda: self._start_cursor_placement(1))
        tb.addSeparator()
        add("Fit (F)", self._zoom_full_safe)
        add("Fit X (T)", self._plot.zoom_fit_x)
        add("Fit Y (A)", self._plot.zoom_fit_y)
        tb.addSeparator()

        # Y-lock checkbox in toolbar
        self._tb_y_lock = QCheckBox("Y Auto")
        self._tb_y_lock.setChecked(self._y_lock_auto)
        self._tb_y_lock.setToolTip(
            "Lock Y axis to auto-scale. Mouse wheel zooms X only.")
        self._tb_y_lock.toggled.connect(self._toggle_y_lock)
        tb.addWidget(self._tb_y_lock)
        tb.addSeparator()

        add("FFT", self._show_fft)
        add("Filter", self._show_filter)
        tb.addSeparator()
        add("Screenshot", self._save_screenshot)

    def _build_statusbar(self):
        self._status_lbl = QLabel("Ready  |  No data loaded")
        self._status_lbl.setStyleSheet("padding: 2px 8px;")
        self.statusBar().addWidget(self._status_lbl)
        self._cursor_status = QLabel("")
        self._cursor_status.setStyleSheet("padding: 2px 8px; color: #aaa;")
        self.statusBar().addPermanentWidget(self._cursor_status)

    # ── File Operations ────────────────────────────────────────────────

    def _get_open_dir(self) -> str:
        """Return the starting directory for open dialogs."""
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if self._settings.get("remember_folder", True):
            d = self._settings.get("last_open_folder", base)
            if os.path.isdir(d):
                return d
        return base

    def _get_save_dir(self, filename: str = "") -> str:
        """Return the starting directory (+ optional filename) for save dialogs."""
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if self._settings.get("remember_folder", True):
            d = self._settings.get("last_save_folder", base)
            if os.path.isdir(d):
                return os.path.join(d, filename) if filename else d
        return os.path.join(base, filename) if filename else base

    def _remember_open(self, path: str):
        if self._settings.get("remember_folder", True):
            self._settings["last_open_folder"] = os.path.dirname(os.path.abspath(path))

    def _remember_save(self, path: str):
        if self._settings.get("remember_folder", True):
            self._settings["last_save_folder"] = os.path.dirname(os.path.abspath(path))

    def _open_csv(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Data File", self._get_open_dir(),
            "CSV Files (*.csv *.tsv *.txt);;All Files (*)")
        if not path:
            return

        result = load_csv(path)
        if result.error:
            QMessageBox.critical(self, "Load Error", result.error)
            return

        self._remember_open(path)

        # Pass persistent settings to dialog
        dlg = ImportDialog(result, persistent_settings=self._settings,
                           parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        # Save checkbox preferences
        self._import_replace = dlg.replace_existing
        self._import_reset_view = dlg.reset_view
        self._settings["import_replace"] = self._import_replace
        self._settings["import_reset_view"] = self._import_reset_view

        if dlg.replace_existing:
            self._clear_all(confirm=False)

        for trace in dlg.result_traces:
            self._add_trace(trace)

        if dlg.reset_view:
            meta = result.metadata
            if (meta.view_time_start is not None or
                    meta.view_sample_start is not None):
                QTimer.singleShot(80, lambda: self._apply_viewport_from_metadata(meta))
            else:
                QTimer.singleShot(50, self._plot.zoom_full)

        self._update_status()

    def _add_trace(self, trace: TraceModel):
        # If a trace with this name already exists, replace it
        existing_names = [t.name for t in self._traces]
        if trace.name in existing_names:
            idx = existing_names.index(trace.name)
            old_trace = self._traces[idx]
            trace.color = old_trace.color
            trace.theme_color_index = old_trace.theme_color_index
            trace.use_theme_color = old_trace.use_theme_color
            self._traces[idx] = trace
            self._channel_panel.refresh_all()
            self._plot.add_trace(trace)  # add_trace handles overwrite
            return

        # Assign color from active theme via ThemeManager
        n = len(self._traces)
        trace.reset_color_to_theme(n)
        trace.sync_theme_color(self.theme.active_theme)

        self._traces.append(trace)
        self._channel_panel.add_trace(trace)
        self._plot.add_trace(trace)
        self._refresh_trigger_channels()
        self._refresh_status_bar()

    def _remove_trace(self, trace_name: str):
        self._traces = [t for t in self._traces if t.name != trace_name]
        self._channel_panel.remove_trace(trace_name)
        self._plot.remove_trace(trace_name)
        self._refresh_trigger_channels()
        self._update_status()

    def _clear_all(self, confirm: bool = True):
        if confirm and self._traces:
            r = QMessageBox.question(
                self, "Clear All",
                "Remove all loaded traces?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
        # Remove from panels
        for t in list(self._traces):
            self._channel_panel.remove_trace(t.name)
        self._traces.clear()
        self._plot.clear_all()          # also clears persist/retrigger state in plot
        self._last_retrigger_results.clear()
        self._last_trigger_t_pos  = None
        self._last_retrigger_span = 0.0
        self._refresh_trigger_channels()
        self._update_status()

    def _export_csv(self):
        if not self._traces:
            QMessageBox.information(self, "Export", "No data loaded.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Visible Data", self._get_save_dir(),
            "CSV Files (*.csv)")
        if not path:
            return
        self._remember_save(path)

        x0, x1 = self._plot.get_current_view_range()
        visible = [t for t in self._traces if t.visible]
        if not visible:
            return

        import numpy as np
        ref_t = visible[0].time_axis
        mask = (ref_t >= x0) & (ref_t <= x1)
        ref_t = ref_t[mask]

        lines = ["time," + ",".join(t.label for t in visible)]
        for t_val in ref_t:
            row = [f"{t_val:.10g}"]
            for trace in visible:
                ta = trace.time_axis
                ya = trace.processed_data
                idx = int(round((t_val - ta[0]) / trace.dt)) if trace.dt > 0 else 0
                idx = max(0, min(idx, len(ya) - 1))
                row.append(f"{ya[idx]:.10g}")
            lines.append(",".join(row))

        with open(path, "w") as f:
            f.write("\n".join(lines))
        self._status_lbl.setText(f"Exported: {os.path.basename(path)}")

    def _save_screenshot(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Screenshot",
            self._get_save_dir("tracelab_capture.png"),
            "PNG Images (*.png);;All Files (*)")
        if not path:
            return
        self._remember_save(path)
        self._grab_screenshot(path, scale=2)
        self._status_lbl.setText(f"Screenshot: {os.path.basename(path)}")

    def _grab_screenshot(self, filepath: str, scale: int = 2):
        """
        Grab plot area + scope status bar together (excludes range-input bar).
        Composites branding directly from the status bar logo — no separate
        corner overlay needed.
        """
        from PyQt6.QtGui import QPixmap, QImage
        from PyQt6.QtCore import Qt as _Qt

        # Grab the plot widget (scroll/overlay + range bar excluded since
        # range_bar was re-parented out of _plot)
        if self._plot._mode == "split":
            plot_px = self._plot._scroll.grab()
        else:
            plot_px = self._plot._overlay_widget.grab()

        # Grab the scope status bar
        status_px = self._scope_status.grab()

        # Stack vertically: plot on top, status bar on bottom
        pw, ph = plot_px.width(), plot_px.height()
        sw, sh = status_px.width(), status_px.height()
        total_w = max(pw, sw)
        total_h = ph + sh

        combined = QPixmap(total_w, total_h)
        combined.fill(_Qt.GlobalColor.black)
        from PyQt6.QtGui import QPainter
        p = QPainter(combined)
        p.drawPixmap(0, 0, plot_px)
        p.drawPixmap(0, ph, status_px)
        p.end()

        if scale > 1:
            img = combined.toImage()
            img = img.scaled(
                img.width() * scale, img.height() * scale,
                _Qt.AspectRatioMode.KeepAspectRatio,
                _Qt.TransformationMode.SmoothTransformation)
            img.save(filepath)
        else:
            combined.save(filepath)

    def _get_branding_path(self) -> str:
        """Return path to branding SVG if it exists, else empty string.

        Resolution order:
          1. branding_{theme display name}.svg   e.g. "branding_Phosphor Green.svg"
          2. branding_Dark.svg / branding_Light.svg  chosen by bg_plot luminance
          3. empty string  (caller falls back to text)
        """
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        assets = os.path.join(base, "assets")

        # 1. Exact match on the theme's display name
        p = os.path.join(assets, f"branding_{self.theme.display_name}.svg")
        if os.path.exists(p):
            return p

        # 2. Brightness-based generic fallback
        bg_hex = self.theme.pv("bg_plot") or self.theme.pv("bg", "#0d0d0d")
        fallback = ("branding_Light.svg"
                    if _hex_perceived_luminance(bg_hex) >= 128
                    else "branding_Dark.svg")
        p = os.path.join(assets, fallback)
        if os.path.exists(p):
            return p

        return ""

    # ── Display / Theme ────────────────────────────────────────────────

    def _set_display_mode(self, mode: str):
        self._plot.set_mode(mode)

    def _set_theme(self, file_id: str, save: bool = True):
        from PyQt6.QtWidgets import QApplication
        self.theme.set_theme(file_id)
        scale = self._settings.get("font_scale", 1.0)
        QApplication.instance().setStyleSheet(
            self.theme.get_stylesheet(font_scale=scale))
        self._channel_panel.refresh_all()
        if hasattr(self, '_scope_status'):
            self._scope_status.set_palette(self.theme.statusbar_palette())
            self._scope_status.set_branding(self._get_branding_path())
        self._refresh_status_bar()
        if save:
            self._settings["theme"] = file_id

    def _toggle_y_lock(self, checked: bool):
        self._y_lock_auto = checked
        self._plot.set_y_lock_auto(checked)
        # Sync both toolbar checkbox and menu action
        self._tb_y_lock.blockSignals(True)
        self._tb_y_lock.setChecked(checked)
        self._tb_y_lock.blockSignals(False)
        self._act_y_lock.blockSignals(True)
        self._act_y_lock.setChecked(checked)
        self._act_y_lock.blockSignals(False)

    # ── Cursors ────────────────────────────────────────────────────────

    def _start_cursor_placement(self, cursor_id: int):
        x_pos = self._plot.get_cursor_placement_x(cursor_id)
        self._plot.set_cursor(cursor_id, x_pos)
        name = "A" if cursor_id == 0 else "B"
        self._status_lbl.setText(
            f"Cursor {name} placed at {x_pos:.6g} s — drag to move")

    def _on_cursor_values(self, data: dict):
        self._cursor_panel.update_cursors(data)
        t_a = data.get(0, {}).get("time")
        t_b = data.get(1, {}).get("time")
        if t_a is not None and t_b is not None:
            self._cursor_status.setText(f"ΔT = {t_b - t_a:.6g} s")
        self._refresh_status_bar()

    def _refresh_status_bar(self):
        if not hasattr(self, '_scope_status'):
            return
        x0, x1 = self._plot.get_current_view_range()

        # Get actual major tick spacing for time axis and each Y lane
        x_major_div = self._get_x_major_tick()
        y_major_divs = {}
        for trace in self._traces:
            if trace.visible:
                lane = self._plot._lanes.get(trace.name)
                if lane:
                    y_major_divs[trace.name] = self._get_y_major_tick(lane)

        trig_info = getattr(self, '_last_trigger_info', "")
        sinc_active = self._plot.get_sinc_active()
        unit_map = {t.name: getattr(t, 'unit', '') for t in self._traces}
        self._cursor_panel.set_trace_units(unit_map)
        interp_map = {t.name: getattr(t, '_interp_mode_override',
                                       self._interp_mode)
                      for t in self._traces}
        self._scope_status.set_trace_interp_modes(interp_map)
        self._scope_status.update(
            self._traces, x_major_div, trig_info, y_major_divs,
            sinc_active, settings=self._settings)

    def _get_x_major_tick(self) -> float:
        """Return the actual major X tick spacing in data units (seconds)."""
        try:
            if self._plot._lanes:
                lane = next(iter(self._plot._lanes.values()))
                ax = lane.getPlotItem().getAxis('bottom')
                vr = lane.getPlotItem().viewRange()[0]
                w = lane.width() or 800
                ticks = ax.tickSpacing(vr[0], vr[1], w)
                if ticks:
                    return float(ticks[0][0])
            elif self._plot._mode == "overlay":
                pi = self._plot._overlay_widget.getPlotItem()
                ax = pi.getAxis('bottom')
                vr = pi.viewRange()[0]
                w = self._plot._overlay_widget.width() or 800
                ticks = ax.tickSpacing(vr[0], vr[1], w)
                if ticks:
                    return float(ticks[0][0])
        except Exception:
            pass
        x0, x1 = self._plot.get_current_view_range()
        return (x1 - x0) / 10.0

    def _get_y_major_tick(self, lane) -> float:
        """Return the actual major Y tick spacing for a lane."""
        try:
            ax = lane.getPlotItem().getAxis('left')
            vr = lane.getPlotItem().viewRange()[1]
            h = lane.height() or 300
            ticks = ax.tickSpacing(vr[0], vr[1], h)
            if ticks:
                return float(ticks[0][0])
        except Exception:
            pass
        return 0.0

    def _on_sinc_active_changed(self, active: bool):
        self._refresh_status_bar()

    def _zoom_full_safe(self):
        """Zoom to fit all data — forces a range reset even after manual zoom."""
        if not self._traces:
            return
        # Find global data extents
        import numpy as np
        t_mins, t_maxs, y_mins, y_maxs = [], [], [], []
        for trace in self._traces:
            if not trace.visible:
                continue
            t = trace.time_axis
            y = trace.processed_data
            if len(t):
                t_mins.append(float(t.min()))
                t_maxs.append(float(t.max()))
            if len(y):
                y_mins.append(float(y.min()))
                y_maxs.append(float(y.max()))
        if not t_mins:
            self._plot.zoom_full()
            return
        t0, t1 = min(t_mins), max(t_maxs)
        y0, y1 = min(y_mins), max(y_maxs)
        pad_y = (y1 - y0) * 0.05 or 0.1
        # Disable then re-enable to clear any stale range lock
        if self._plot._mode == "split":
            for lane in self._plot._lanes.values():
                pi = lane.getPlotItem()
                pi.disableAutoRange()
                pi.setXRange(t0, t1, padding=0.02)
                pi.setYRange(y0 - pad_y, y1 + pad_y, padding=0)
        else:
            pi = self._plot._overlay_widget.getPlotItem()
            pi.disableAutoRange()
            pi.setXRange(t0, t1, padding=0.02)
            pi.setYRange(y0 - pad_y, y1 + pad_y, padding=0)
        self._refresh_status_bar()

    # ── Trace Events ──────────────────────────────────────────────────

    def _on_trace_visibility(self, name: str, visible: bool):
        self._plot.set_trace_visible(name, visible)

    def _on_trace_color(self, name: str, color: str):
        self._plot.refresh_all()
        self._refresh_status_bar()

    # ── Analysis ──────────────────────────────────────────────────────

    def _show_fft(self):
        if not self._traces:
            return
        from core.fft_dialog import FFTDialog
        vr = self._plot.get_current_view_range()
        dlg = FFTDialog(self._traces, view_range=vr,
                         fft_min_freq=self._fft_min_freq,
                         settings=self._settings, parent=self)
        dlg.exec()
        self._fft_min_freq = dlg.spin_min_freq.value()
        self._settings["fft_min_freq"] = self._fft_min_freq

    def _show_filter(self):
        if not self._traces:
            return
        from core.filter_dialog import FilterDialog
        dlg = FilterDialog(self._traces, parent=self)
        dlg.filters_applied.connect(self._on_filters_applied)
        dlg.exec()

    def _clear_all_filters(self):
        for trace in self._traces:
            trace.clear_filter()
        self._plot.refresh_all()
        self._status_lbl.setText("All filters cleared.")

    def _on_filters_applied(self, names):
        self._plot.refresh_all()
        self._status_lbl.setText(f"Filter applied: {', '.join(names)}")

    # ── Plugins ───────────────────────────────────────────────────────

    def _update_plugin_menu(self):
        actions = self._plugins_menu.actions()
        for act in actions[self._plugin_actions_start:]:
            self._plugins_menu.removeAction(act)

        plugins = self._plugins.get_plugins()
        if not plugins:
            a = QAction("(No plugins found)", self)
            a.setEnabled(False)
            self._plugins_menu.addAction(a)
        else:
            for p in plugins:
                a = QAction(f"{p.name} [{p.plugin_type}]", self)
                a.setStatusTip(p.description)
                a.triggered.connect(lambda checked, _p=p: self._run_plugin(_p))
                self._plugins_menu.addAction(a)

        if self._plugins.load_errors:
            self._plugins_menu.addSeparator()
            a = QAction(f"⚠ {len(self._plugins.load_errors)} load error(s)", self)
            a.triggered.connect(self._show_plugin_errors)
            self._plugins_menu.addAction(a)

    def _run_plugin(self, plugin):
        traces_copy = copy.deepcopy(self._traces)
        x0, x1 = self._plot.get_current_view_range()
        context = {
            "view_range": (x0, x1),
            "sample_rate": self._traces[0].sample_rate if self._traces else 1.0,
            "parent_window": self,
        }
        try:
            result = self._plugins.run_plugin(plugin.name, traces_copy, context)
            if plugin.plugin_type == "processor" and result:
                name_map = {t.name: t for t in result}
                for trace in self._traces:
                    if trace.name in name_map:
                        r = name_map[trace.name]
                        trace.raw_data = r.raw_data
                        trace.scaling.enabled = False
                        trace._invalidate_cache()
                self._plot.refresh_all()
                self._status_lbl.setText(f"Plugin '{plugin.name}' applied.")
        except Exception as e:
            QMessageBox.critical(self, "Plugin Error", str(e))

    def _reload_plugins(self):
        self._plugins.reload()
        self._update_plugin_menu()
        self._status_lbl.setText(
            f"Plugins reloaded: {self._plugins.plugin_count} found.")

    def _open_plugins_dir(self):
        import subprocess, platform
        d = self._plugins.PLUGIN_DIR
        os.makedirs(d, exist_ok=True)
        if platform.system() == "Windows":
            os.startfile(d)
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", d])
        else:
            subprocess.Popen(["xdg-open", d])

    def _show_plugin_errors(self):
        QMessageBox.warning(self, "Plugin Errors",
                             "\n\n".join(self._plugins.load_errors))

    # ── Status ────────────────────────────────────────────────────────

    def _update_status(self):
        n = len(self._traces)
        vis = sum(1 for t in self._traces if t.visible)
        filt = sum(1 for t in self._traces if t.has_filter)
        if n == 0:
            self._status_lbl.setText("Ready  |  No data loaded")
        else:
            pts = sum(t.n_samples for t in self._traces)
            parts = [f"{n} traces", f"{vis} visible", f"{pts:,} samples"]
            if filt:
                parts.append(f"{filt} filtered")
            self._status_lbl.setText("  |  ".join(parts))

    # ── About ─────────────────────────────────────────────────────────

    def _show_about(self):
        import os
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        notice = ""
        for fname in ("NOTICE.md", "LICENSE.md"):
            p = os.path.join(base, fname)
            if os.path.exists(p):
                try:
                    notice += f"<hr><pre style='font-size:9px;'>{open(p).read()[:2000]}</pre>"
                except Exception:
                    pass

        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextBrowser, QPushButton
        dlg = QDialog(self)
        dlg.setWindowTitle("About ChipFX TraceLab")
        dlg.resize(600, 500)
        lay = QVBoxLayout(dlg)
        tb = QTextBrowser()
        tb.setOpenExternalLinks(True)
        tb.setHtml(
            "<h2>ChipFX TraceLab</h2>"
            "<p>Modular oscilloscope &amp; signal data viewer.</p>"
            "<p>A <b>ChipFX</b> instrument software project.</p>"
            "<p><b>Keyboard shortcuts:</b><br>"
            "F — Zoom to fit &nbsp; T — Fit X &nbsp; A — Fit Y &nbsp; "
            "L — Y auto-lock<br>"
            "+ / − — Zoom &nbsp; Ctrl+O — Open &nbsp; "
            "Ctrl+F — FFT &nbsp; Ctrl+P — Screenshot</p>"
            "<p>CSV metadata: <code>#samplerate=10k</code>, "
            "<code>#gain=2.5/4096</code>, <code>#offset=-1.25</code></p>"
            "<p>Plugins: drop <code>.py</code> files in "
            "<code>plugins/</code> folder.</p>"
            + notice)
        lay.addWidget(tb)
        btn = QPushButton("Close")
        btn.clicked.connect(dlg.accept)
        lay.addWidget(btn)
        dlg.exec()

    def _cursor_set_t0_at_a(self):
        """Called from cursor panel Set t=0 button — shift to cursor A."""
        t_pos = self._plot._cursors.get(0)
        if t_pos is None:
            return
        self._on_trigger_set_t0(t_pos)

    def _jump_to_t0(self):
        """Center t=0 in the current viewport without changing zoom span."""
        x0, x1 = self._plot.get_current_view_range()
        half_span = (x1 - x0) / 2.0
        self._plot.zoom_x_range(-half_span, half_span)
        self._refresh_status_bar()

    def _on_trigger_found(self, t_pos: float):
        """Trigger located — optionally zoom context window around it."""
        trace = None
        for t in self._traces:
            if t.visible:
                trace = t
                break
        level = ""
        if hasattr(self, '_trigger_panel'):
            level = self._trigger_panel.edit_level.text()
            ch = self._trigger_panel.combo_ch.currentText()
            edge = self._trigger_panel.combo_edge.currentText()[0]
            self._last_trigger_info = f"{edge} {ch} {level}"
        if self._trigger_panel.chk_zoom.isChecked():
            x0, x1 = self._plot.get_current_view_range()
            half_win = (x1 - x0) / 2
            self._plot.zoom_x_range(t_pos - half_win, t_pos + half_win)
        self._refresh_status_bar()
        self._apply_retrigger(t_pos)

    def _on_trigger_set_t0(self, t_pos: float):
        """Shift all trace time axes so t_pos becomes 0."""
        x0, x1 = self._plot.get_current_view_range()
        shifted_x0 = x0 - t_pos
        shifted_x1 = x1 - t_pos
        cursor_positions = dict(self._plot._cursors)

        for trace in self._traces:
            # Shift the stored time data
            if trace.time_data is not None:
                trace.time_data = trace.time_data - t_pos
            else:
                # Convert to explicit time array shifted by t_pos
                import numpy as np
                trace.time_data = trace.time_axis - t_pos
            trace._computed_time = None  # invalidate cache

        self._plot.refresh_all()

        for cid, pos in cursor_positions.items():
            if pos is not None:
                self._plot.set_cursor(cid, pos - t_pos)

        self._plot.zoom_x_range(shifted_x0, shifted_x1)
        self._plot._update_range_bar()
        self._refresh_status_bar()
        self._status_lbl.setText(f"t=0 set to trigger at {t_pos:.6g} s")

    def _refresh_trigger_channels(self):
        self._trigger_panel.update_traces(self._traces)
        self._update_per_trace_interp_menu()

    def _update_per_trace_interp_menu(self):
        """Rebuild the per-channel interpolation submenu."""
        if not hasattr(self, '_per_trace_interp_menu'):
            return
        self._per_trace_interp_menu.clear()
        if not self._traces:
            self._per_trace_interp_menu.addAction("(no traces)").setEnabled(False)
            return
        from PyQt6.QtGui import QActionGroup
        for trace in self._traces:
            ch_menu = self._per_trace_interp_menu.addMenu(trace.label)
            current = getattr(trace, '_interp_mode_override', self._interp_mode)
            ag = QActionGroup(ch_menu)
            ag.setExclusive(True)
            name = trace.name
            for mode, lbl in [("linear", "Linear"),
                               ("cubic",  "Cubic Spline"),
                               ("sinc",   "Sinc (sin(x)/x)")]:
                a = ch_menu.addAction(lbl)
                a.setCheckable(True)
                a.setChecked(current == mode)
                ag.addAction(a)
                a.triggered.connect(
                    lambda _, n=name, m=mode:
                        self._plot.set_interp_mode_for_trace(n, m))

    # ── Channel order ─────────────────────────────────────────────────

    def _on_channel_order_changed(self, name_order: list):
        """Channel panel drag-reorder → update plot and cursor table."""
        self._plot.reorder_traces(name_order)
        self._cursor_panel.set_trace_order(name_order)

    # ── Settings dialogs ───────────────────────────────────────────────

    def _show_font_scale_dialog(self):
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QPushButton, QDoubleSpinBox
        dlg = QDialog(self)
        dlg.setWindowTitle("Font Scale")
        dlg.setFixedWidth(340)
        layout = QVBoxLayout(dlg)

        current = self._settings.get("font_scale", 1.0)
        layout.addWidget(QLabel(
            "Scale all UI text. Affects menus, labels, panels.\n"
            "Restart not required — applied immediately."))

        hl = QHBoxLayout()
        hl.addWidget(QLabel("Scale:"))
        spin = QDoubleSpinBox()
        spin.setRange(0.6, 3.0)
        spin.setSingleStep(0.05)
        spin.setDecimals(2)
        spin.setValue(current)
        hl.addWidget(spin)
        hl.addWidget(QLabel("  (1.0 = normal, 1.25 = 125%)"))
        layout.addLayout(hl)

        def apply():
            scale = spin.value()
            self._settings["font_scale"] = scale
            self._apply_font_scale(scale)

        bh = QHBoxLayout()
        btn_apply = QPushButton("Apply")
        btn_apply.clicked.connect(apply)
        btn_ok = QPushButton("OK")
        btn_ok.clicked.connect(lambda: (apply(), dlg.accept()))
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(dlg.reject)
        bh.addWidget(btn_apply); bh.addStretch()
        bh.addWidget(btn_cancel); bh.addWidget(btn_ok)
        layout.addLayout(bh)
        dlg.exec()

    def _apply_font_scale(self, scale: float):
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtGui import QFont
        app = QApplication.instance()
        base_size = 10  # pt
        f = QFont()
        f.setPointSize(max(7, int(base_size * scale)))
        app.setFont(f)
        # Re-apply stylesheet with scaled font-size
        app.setStyleSheet(self.theme.get_stylesheet(font_scale=scale))

    def _show_decimal_sep_dialog(self):
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
            QLabel, QComboBox, QPushButton, QGroupBox, QGridLayout)
        dlg = QDialog(self)
        dlg.setWindowTitle("Number Input Settings")
        dlg.setFixedWidth(380)
        layout = QVBoxLayout(dlg)

        layout.addWidget(QLabel(
            "Configure how numbers are entered in input fields.\n"
            "Applies to all Gain/Offset/frequency fields."))

        grp = QGroupBox("Decimal Separator")
        gl = QGridLayout(grp)
        gl.addWidget(QLabel("Decimal point:"), 0, 0)
        self._combo_decimal = QComboBox()
        self._combo_decimal.addItems(["Both '.' and ',' accepted (default)",
                                       "Dot '.' only",
                                       "Comma ',' only"])
        cur = self._settings.get("decimal_sep", "both")
        idx = {"both": 0, "dot": 1, "comma": 2}.get(cur, 0)
        self._combo_decimal.setCurrentIndex(idx)
        gl.addWidget(self._combo_decimal, 0, 1)

        gl.addWidget(QLabel("Thousands separator:"), 1, 0)
        self._combo_thousands = QComboBox()
        self._combo_thousands.addItems(["None (default)", "Dot '.'", "Comma ','"])
        cur_t = self._settings.get("thousands_sep", "none")
        idx_t = {"none": 0, "dot": 1, "comma": 2}.get(cur_t, 0)
        self._combo_thousands.setCurrentIndex(idx_t)
        gl.addWidget(self._combo_thousands, 1, 1)
        layout.addWidget(grp)

        layout.addWidget(QLabel(
            "<i>Note: PyScope input fields always accept both '.' and ','\n"
            "as decimal when 'Both' is selected, regardless of system locale.\n"
            "The numpad dot will always work.</i>"))

        bh = QHBoxLayout()
        btn_ok = QPushButton("OK")
        btn_ok.setStyleSheet("background:#2060c0;color:white;padding:4px 16px;")
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(dlg.reject)
        btn_ok.clicked.connect(lambda: (self._save_decimal_settings(), dlg.accept()))
        bh.addStretch(); bh.addWidget(btn_cancel); bh.addWidget(btn_ok)
        layout.addLayout(bh)
        dlg.exec()

    def _save_decimal_settings(self):
        dec_map = {0: "both", 1: "dot", 2: "comma"}
        tho_map = {0: "none", 1: "dot", 2: "comma"}
        self._settings["decimal_sep"] = dec_map[self._combo_decimal.currentIndex()]
        self._settings["thousands_sep"] = tho_map[self._combo_thousands.currentIndex()]

    # ── Viewport auto-zoom from CSV metadata ──────────────────────────

    def _apply_viewport_from_metadata(self, meta):
        """After import, zoom to viewport hints if present in CSV headers."""
        from PyQt6.QtCore import QTimer as _QTimer
        if meta.view_time_start is not None and meta.view_time_stop is not None:
            t0, t1 = meta.view_time_start, meta.view_time_stop
            _QTimer.singleShot(80, lambda: self._plot.zoom_x_range(t0, t1))
        elif (meta.view_sample_start is not None
              and meta.view_sample_stop is not None
              and self._traces):
            trace = self._traces[0]
            ta = trace.time_axis
            n = len(ta)
            i0 = max(0, min(meta.view_sample_start, n-1))
            i1 = max(0, min(meta.view_sample_stop, n-1))
            t0, t1 = float(ta[i0]), float(ta[i1])
            _QTimer.singleShot(80, lambda: self._plot.zoom_x_range(t0, t1))

    def _toggle_remember_folder(self, checked: bool):
        self._settings["remember_folder"] = checked

    # ── Interpolation ──────────────────────────────────────────────────

    def _set_interp_mode(self, mode: str):
        self._interp_mode = mode
        self._plot.set_interp_mode(mode)
        self._settings["interp_mode"] = mode

    def _set_draw_mode(self, mode: str):
        self._draw_mode = mode
        if hasattr(self, "_draw_mode_actions") and mode in self._draw_mode_actions:
            self._draw_mode_actions[mode].setChecked(True)
        self._plot.set_draw_mode(mode)
        self._settings["draw_mode"] = mode

    def _on_reset_trace_color(self, trace_name: str):
        """Reset a single trace to its theme-default colour."""
        idx = next((i for i, t in enumerate(self._traces)
                    if t.name == trace_name), 0)
        for trace in self._traces:
            if trace.name == trace_name:
                trace.reset_color_to_theme(idx)
                trace.sync_theme_color(self.theme.active_theme)
                break
        self._channel_panel.refresh_all()
        self._plot.refresh_all()
        self._refresh_status_bar()

    def _on_channel_interp_changed(self, name: str, mode: str):
        """Channel panel context menu or All Lin/Sinc triggered."""
        self._plot.set_interp_mode_for_trace(name, mode)
        self._refresh_status_bar()

    def _on_status_bar_toggle_interp(self, name: str):
        """Clicking the interp badge on a channel block cycles linear→cubic→sinc→…"""
        _CYCLE = ("linear", "cubic", "sinc")
        for trace in self._traces:
            if trace.name == name:
                cur = getattr(trace, '_interp_mode_override', self._interp_mode)
                idx = _CYCLE.index(cur) if cur in _CYCLE else 0
                new_mode = _CYCLE[(idx + 1) % len(_CYCLE)]
                trace._interp_mode_override = new_mode
                self._plot.set_interp_mode_for_trace(name, new_mode)
                break
        self._refresh_status_bar()

    # ── Retrigger pipeline ─────────────────────────────────────────────

    def _set_retrigger_mode(self, mode: str):
        """Select a persistence mode; deselects Averaging and Interpolate."""
        self._retrigger_mode = mode
        # Auto-wire selection & emphasis to match the mode's semantic meaning.
        # Future Persist: first trigger is the "hard" line; history grows forward.
        # Past Persist Normal: last trigger is the "hard" line; history grows back.
        if mode == MODE_PERSIST_FUTURE:
            self._persist_settings["selection"] = "first"
            self._persist_settings["emphasis"]  = "first"
        elif mode == MODE_PERSIST_PAST:
            self._persist_settings["selection"] = "last"
            self._persist_settings["emphasis"]  = "last"
        self._act_rt_averaging.blockSignals(True)
        self._act_rt_averaging.setChecked(False)
        self._act_rt_averaging.blockSignals(False)
        self._act_rt_interp.blockSignals(True)
        self._act_rt_interp.setChecked(False)
        self._act_rt_interp.blockSignals(False)
        self._plot.clear_persistence_layers()
        self._plot.clear_retrigger_curve()
        self._last_retrigger_results.clear()
        self._update_retrigger_controls()
        if mode != MODE_OFF:
            self._reapply_retrigger()

    def _toggle_retrigger_averaging(self, checked: bool):
        """Toggle trigger-aligned averaging; disables persistence on enable."""
        if checked:
            self._retrigger_mode = MODE_AVERAGING
            self._act_persist_off.blockSignals(True)
            self._act_persist_off.setChecked(True)
            self._act_persist_off.blockSignals(False)
            self._act_rt_interp.blockSignals(True)
            self._act_rt_interp.setChecked(False)
            self._act_rt_interp.blockSignals(False)
        else:
            self._retrigger_mode = MODE_OFF
        self._plot.clear_persistence_layers()
        self._plot.clear_retrigger_curve()
        self._last_retrigger_results.clear()
        self._update_retrigger_controls()
        if checked:
            self._reapply_retrigger()

    def _toggle_retrigger_interpolation(self, checked: bool):
        """Toggle sub-sample interpolation; disables persistence on enable."""
        if checked:
            self._retrigger_mode = MODE_INTERPOLATION
            self._act_persist_off.blockSignals(True)
            self._act_persist_off.setChecked(True)
            self._act_persist_off.blockSignals(False)
            self._act_rt_averaging.blockSignals(True)
            self._act_rt_averaging.setChecked(False)
            self._act_rt_averaging.blockSignals(False)
        else:
            self._retrigger_mode = MODE_OFF
        self._plot.clear_persistence_layers()
        self._plot.clear_retrigger_curve()
        self._last_retrigger_results.clear()
        self._update_retrigger_controls()
        if checked:
            self._reapply_retrigger()

    def _apply_retrigger(self, t_pos: float):
        """
        Full retrigger pipeline.

        Triggers are found ONCE on the selected trigger channel, then the
        same trigger times are applied to ALL visible traces — exactly how a
        real oscilloscope works: the trigger channel fires, every channel
        captures in sync.
        """
        if self._retrigger_mode == MODE_OFF:
            return

        level    = self._trigger_panel.edit_level.get_value(0.0)
        edge_idx = self._trigger_panel.combo_edge.currentIndex()
        x0, x1  = self._plot.get_current_view_range()
        view_span = x1 - x0
        if view_span <= 0:
            return

        # ── Step 1: find triggers on the selected trigger channel only ─────────
        trig_trace = self._trigger_panel._get_selected_trace()
        if trig_trace is None or len(trig_trace.time_axis) < 2:
            return

        trig_t = trig_trace.time_axis
        trig_y = trig_trace.processed_data
        dt_est  = float(trig_t[1] - trig_t[0])
        # One view-span holdoff: no two consecutive trigger windows may overlap
        holdoff = max(1, int(view_span / dt_est)) if dt_est > 0 else 1

        idxs, t_trigs = find_all_triggers_with_times(
            trig_y, trig_t, level, edge_idx, holdoff)

        if not idxs:
            # No triggers found — clear any existing display and bail
            self._plot.clear_persistence_layers()
            self._plot.clear_retrigger_curve()
            self._last_retrigger_results.clear()
            return

        # ── Step 2: apply the same trigger times to every visible channel ──────
        self._last_trigger_t_pos  = t_pos
        self._last_retrigger_span = view_span
        self._plot.clear_persistence_layers()
        self._plot.clear_retrigger_curve()
        self._last_retrigger_results.clear()

        # Adaptive count cap: limit ghost count when zoomed far out.
        # If the full trace contains 10× more samples than are visible,
        # the max useful sweeps is at most total/visible (each sweep
        # would be below-viewport resolution otherwise).
        mask_v = (trig_t >= x0) & (trig_t <= x1)
        visible_s = max(1, int(mask_v.sum()))
        zoom_cap = max(1, len(trig_t) // visible_s)
        effective_persist = dict(self._persist_settings)
        effective_persist["count"] = min(
            self._persist_settings.get("count", 10), zoom_cap)

        for trace in self._traces:
            if not trace.visible:
                continue
            t = trace.time_axis
            y = trace.processed_data
            if len(t) < 2:
                continue

            result = retrigger_apply_with_triggers(
                mode=self._retrigger_mode,
                time=t,
                data=y,
                trigger_indices=idxs,
                trigger_times=t_trigs,
                view_span=view_span,
                persistence_settings=effective_persist,
                averaging_settings=self._averaging_settings,
                interpolation_settings=self._interpolation_settings,
            )
            self._last_retrigger_results[trace.name] = result
            self._render_retrigger(trace.name, result, t_pos)

    def _render_retrigger(self, trace_name: str, result, t_ref: float):
        """Dispatch a RetriggerResult to the appropriate plot calls."""
        mode = result.mode
        if mode in PERSIST_MODES:
            if result.layers:
                self._plot.set_persistence_layers(trace_name, result.layers, t_ref)
            else:
                self._plot.clear_persistence_layers(trace_name)
            self._plot.clear_retrigger_curve(trace_name)
        elif mode == MODE_AVERAGING:
            self._plot.clear_persistence_layers(trace_name)
            if result.avg_time is not None and result.avg_data is not None:
                self._plot.set_retrigger_curve(
                    trace_name, result.avg_time + t_ref, result.avg_data,
                    **self._retrigger_display_kwargs(self._averaging_settings))
            else:
                self._plot.clear_retrigger_curve(trace_name)
        elif mode == MODE_INTERPOLATION:
            self._plot.clear_persistence_layers(trace_name)
            if result.interp_time is not None and result.interp_data is not None:
                self._plot.set_retrigger_curve(
                    trace_name, result.interp_time + t_ref, result.interp_data,
                    **self._retrigger_display_kwargs(self._interpolation_settings))
            else:
                self._plot.clear_retrigger_curve(trace_name)
        else:
            self._plot.clear_persistence_layers(trace_name)
            self._plot.clear_retrigger_curve(trace_name)

    def _retrigger_display_kwargs(self, mode_settings: dict) -> dict:
        """Build keyword args for set_retrigger_curve from current display settings."""
        cfg = self._dashed_line_config
        dash_pattern = [float(cfg.get("dash", 8)), float(cfg.get("space", 4))]
        return dict(
            original_display=mode_settings.get("original_display", "dimmed"),
            dimmed_opacity=self._original_dimmed_opacity,
            dash_pattern=dash_pattern,
        )

    def _reapply_retrigger(self):
        """Re-run the pipeline with the last known trigger position.
        If no trigger has been found yet, auto-detects the first one."""
        if self._retrigger_mode == MODE_OFF:
            return
        t_pos = self._last_trigger_t_pos
        if t_pos is None:
            t_pos = self._auto_find_trigger()
            if t_pos is None:
                return
            self._last_trigger_t_pos = t_pos
        self._apply_retrigger(t_pos)

    def _auto_find_trigger(self) -> Optional[float]:
        """
        Find the first trigger crossing in the current view window using the
        trigger panel's current level/edge/channel settings.
        Falls back to searching the full trace if no crossing is visible.
        Returns the sub-sample accurate trigger time, or None.
        """
        trig_trace = self._trigger_panel._get_selected_trace()
        if trig_trace is None:
            return None
        level    = self._trigger_panel.edit_level.get_value(0.0)
        edge_idx = self._trigger_panel.combo_edge.currentIndex()
        t_full = trig_trace.time_axis
        y_full = trig_trace.processed_data
        if len(t_full) < 2:
            return None

        # Try the visible window first — gives the most relevant trigger
        x0, x1 = self._plot.get_current_view_range()
        mask = (t_full >= x0) & (t_full <= x1)
        t_v, y_v = t_full[mask], y_full[mask]
        if len(t_v) >= 2:
            _, t_trigs = find_all_triggers_with_times(y_v, t_v, level, edge_idx, 0)
            if t_trigs:
                return t_trigs[0]

        # Fall back to the full trace
        _, t_trigs = find_all_triggers_with_times(y_full, t_full, level, edge_idx, 0)
        return t_trigs[0] if t_trigs else None

    def _update_retrigger_controls(self):
        """Enable/disable the manual Update Retrigger button."""
        active = self._retrigger_mode != MODE_OFF
        self._trigger_panel.btn_retrigger_update.setEnabled(active)

    def _on_view_changed_retrigger(self):
        """
        Called (throttled, ~100 ms) whenever the user pans or zooms.
        Recalculates persistence when:
          - the view span changed by more than 20 % (zoom in or out), OR
          - the current trigger reference has scrolled out of the visible window.
        Finding a new trigger near the centre of the view keeps the display
        consistent as the user scrolls through the data.
        """
        if self._retrigger_mode == MODE_OFF or not self._traces:
            return
        if not self._trigger_panel.chk_auto_retrigger.isChecked():
            return
        x0, x1 = self._plot.get_current_view_range()
        new_span = x1 - x0

        old_span = self._last_retrigger_span
        span_changed = (
            old_span <= 0
            or abs(new_span - old_span) / max(old_span, 1e-12) > 0.20
        )
        t_pos = self._last_trigger_t_pos
        pos_offscreen = (t_pos is None) or not (x0 <= t_pos <= x1)

        if span_changed or pos_offscreen:
            new_t = self._auto_find_trigger()
            if new_t is not None:
                self._last_trigger_t_pos  = new_t
                self._last_retrigger_span = new_span
                self._apply_retrigger(new_t)

    # ── Retrigger settings dialogs ─────────────────────────────────────

    def _dlg_persist_count(self):
        val, ok = QInputDialog.getInt(
            self, "Persistence Count",
            "Maximum number of trigger-aligned sweeps to overlay:",
            self._persist_settings.get("count", 20), 2, 1000, 1)
        if ok:
            self._persist_settings["count"] = val
            self._reapply_retrigger()

    def _dlg_persist_selection(self):
        items = ["first", "last"]
        cur = self._persist_settings.get("selection", "first")
        idx = items.index(cur) if cur in items else 0
        val, ok = QInputDialog.getItem(
            self, "Persistence Selection",
            "Which triggers to keep (first N or last N):",
            items, idx, False)
        if ok:
            self._persist_settings["selection"] = val
            self._reapply_retrigger()

    def _dlg_persist_emphasis(self):
        items = ["first", "last"]
        cur = self._persist_settings.get("emphasis", "first")
        idx = items.index(cur) if cur in items else 0
        val, ok = QInputDialog.getItem(
            self, "Persistence Emphasis",
            "Which sweep is drawn as the hard line on top:",
            items, idx, False)
        if ok:
            self._persist_settings["emphasis"] = val
            self._reapply_retrigger()

    def _dlg_persist_opacity(self):
        val, ok = QInputDialog.getDouble(
            self, "Opacity Decay",
            "Opacity multiplier per step back from emphasis (0.01 – 0.99):\n"
            "Lower = faster fade.   0.9 = gentle fade.",
            self._persist_settings.get("opacity_decay", 0.9), 0.01, 0.99, 2)
        if ok:
            self._persist_settings["opacity_decay"] = val
            self._reapply_retrigger()

    def _dlg_persist_width(self):
        val, ok = QInputDialog.getDouble(
            self, "Width Growth",
            "Line-width multiplier per step back from emphasis (1.0 – 5.0):\n"
            "Higher = older sweeps drawn thicker.   1.0 = all same width.",
            self._persist_settings.get("width_growth", 1.1), 1.0, 5.0, 2)
        if ok:
            self._persist_settings["width_growth"] = val
            self._reapply_retrigger()

    def _reset_persist_defaults(self):
        self._persist_settings = dict(PERSISTENCE_DEFAULTS)
        self._reapply_retrigger()

    def _build_original_display_menu(self, menu, settings_dict: dict,
                                      on_change) -> dict:
        """Add Show Dimmed / Show Dashed / Don't Show radio actions to *menu*.
        Returns a dict of the three QActions so callers can update check state."""
        from PyQt6.QtGui import QActionGroup
        grp = QActionGroup(menu)
        grp.setExclusive(True)
        current = settings_dict.get("original_display", "dimmed")
        actions = {}
        for key, label in [("dimmed", "Show Dimmed"),
                            ("dashed", "Show Dashed"),
                            ("hide",   "Don't Show")]:
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(key == current)
            grp.addAction(act)
            def _make_cb(k, d, cb):
                def _cb(checked):
                    if checked:
                        d["original_display"] = k
                        cb()
                return _cb
            act.toggled.connect(_make_cb(key, settings_dict, on_change))
            actions[key] = act
        return actions

    def _dlg_dimmed_opacity(self):
        from PyQt6.QtWidgets import QInputDialog
        val, ok = QInputDialog.getDouble(
            self, "Dimmed Opacity",
            "Opacity of the original trace when 'Show Dimmed' is active\n"
            "(10 % – 90 %):",
            self._original_dimmed_opacity * 100, 10.0, 90.0, 0)
        if ok:
            self._original_dimmed_opacity = max(0.1, min(0.9, val / 100.0))
            self._reapply_retrigger()

    def _dlg_dashed_line_config(self):
        from PyQt6.QtWidgets import QDialog, QFormLayout, QSpinBox, QDialogButtonBox
        dlg = QDialog(self)
        dlg.setWindowTitle("Dashed Line Config")
        form = QFormLayout(dlg)
        sb_dash = QSpinBox(); sb_dash.setRange(1, 64)
        sb_dash.setValue(int(self._dashed_line_config.get("dash", 8)))
        sb_dash.setSuffix(" px")
        sb_space = QSpinBox(); sb_space.setRange(1, 64)
        sb_space.setValue(int(self._dashed_line_config.get("space", 4)))
        sb_space.setSuffix(" px")
        form.addRow("Dash length:", sb_dash)
        form.addRow("Space length:", sb_space)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec():
            self._dashed_line_config = {"dash": sb_dash.value(),
                                         "space": sb_space.value()}
            self._reapply_retrigger()

    def _dlg_avg_count(self):
        val, ok = QInputDialog.getInt(
            self, "Averaging Count",
            "Number of trigger-aligned segments to average:",
            self._averaging_settings.get("count", 20), 2, 1000, 1)
        if ok:
            self._averaging_settings["count"] = val
            self._reapply_retrigger()

    def _reset_avg_defaults(self):
        self._averaging_settings = dict(AVERAGING_DEFAULTS)
        self._reapply_retrigger()

    def _dlg_interp_count(self):
        val, ok = QInputDialog.getInt(
            self, "Interpolation Count",
            "Number of trigger-aligned segments to interleave:",
            self._interpolation_settings.get("count", 20), 2, 1000, 1)
        if ok:
            self._interpolation_settings["count"] = val
            self._reapply_retrigger()

    def _reset_interp_defaults(self):
        self._interpolation_settings = dict(INTERPOLATION_DEFAULTS)
        self._reapply_retrigger()

    # ── Trace labels ───────────────────────────────────────────────────

    def _add_label_at_cursor(self):
        """Add a text label to one trace at the current Cursor A position."""
        if not self._traces:
            return
        t_pos = self._plot._cursors.get(0)
        if t_pos is None:
            QMessageBox.information(self, "Add Label",
                "Place Cursor A first, then add a label.")
            return

        # Pick trace
        from PyQt6.QtWidgets import QInputDialog
        names = [t.label for t in self._traces if t.visible]
        if not names:
            return
        trace_label, ok = QInputDialog.getItem(
            self, "Add Label", "Trace:", names, 0, False)
        if not ok:
            return
        text, ok2 = QInputDialog.getText(
            self, "Add Label", f"Label text at t={t_pos:.6g} s:")
        if not ok2 or not text.strip():
            return

        # Find trace and add label
        for trace in self._traces:
            if trace.label == trace_label:
                trace.trace_labels.append((t_pos, text.strip()))
                break
        self._plot.refresh_all()

    def _clear_all_labels(self):
        for trace in self._traces:
            trace.trace_labels.clear()
        self._plot.refresh_all()
        self._status_lbl.setText("All trace labels cleared.")

    def _rebuild_theme_menu(self):
        """Populate Theme submenu from discovered theme files."""
        self._theme_submenu.clear()
        active_id = self.theme.theme_name
        ag = QActionGroup(self)
        ag.setExclusive(True)
        for file_id, td in self.theme.available_themes.items():
            a = QAction(td.name, self)
            a.setCheckable(True)
            a.setChecked(file_id == active_id)
            if td.tooltip:
                a.setToolTip(td.tooltip)
            a.triggered.connect(
                lambda checked, fid=file_id: self._set_theme(fid))
            ag.addAction(a)
            self._theme_submenu.addAction(a)

    def _open_theme_editor(self):
        def on_apply(file_id):
            self._set_theme(file_id)
        self.theme.open_editor(parent=self, on_apply=on_apply)

    def _reload_themes(self):
        self.theme.discover()
        self._rebuild_theme_menu()
        self._status_lbl.setText(
            f"Themes reloaded: {len(self.theme.available_themes)} found.")

    def closeEvent(self, event):
        self._save_settings()
        event.accept()
