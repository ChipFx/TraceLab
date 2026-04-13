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

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "settings.json")


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
        s["import_replace"] = self._import_replace
        s["import_reset_view"] = self._import_reset_view
        s["fft_min_freq"] = self._fft_min_freq
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
        self._last_trigger_info = ""

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

        plot_colors = self.theme.plot_colors()
        self._interp_mode = self._settings.get("interp_mode", "linear")
        self._plot = ScopePlotWidget(
            plot_colors, self.theme.theme_name, self._y_lock_auto,
            self._interp_mode, self._viewport_min_pts)
        self._plot.cursor_values_changed.connect(self._on_cursor_values)
        self._plot.sinc_active_changed.connect(self._on_sinc_active_changed)
        self._plot.view_changed.connect(self._refresh_status_bar)

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
        right_splitter.addWidget(self._cursor_panel)

        self._trigger_panel = TriggerPanel()
        self._trigger_panel.trigger_found.connect(self._on_trigger_found)
        self._trigger_panel.set_time_zero.connect(self._on_trigger_set_t0)
        self._trigger_panel.place_cursor.connect(self._plot.set_cursor)
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
        # Dynamically populated from themes/ folder
        self._theme_submenu = view_menu.addMenu("Theme")
        self._rebuild_theme_menu()

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
            # Preserve color from old trace
            old_color = self._traces[idx].color
            trace.color = old_color
            self._traces[idx] = trace
            self._channel_panel.refresh_all()
            self._plot.add_trace(trace)  # add_trace handles overwrite
            return

        # Assign color from active theme via ThemeManager
        n = len(self._traces)
        trace.color = self.theme.trace_color(n)

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
        self._plot.clear_all()
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
        """Return path to branding SVG if it exists, else empty string."""
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        # Try theme-specific first, then generic
        theme = self.theme.theme_name
        for name in [f"branding_{theme}.svg", "branding.svg"]:
            p = os.path.join(base, "assets", name)
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
        new_colors = self.theme.plot_colors()
        self._plot.theme = new_colors
        self._plot.theme_name = file_id
        self._plot._rebuild()
        if hasattr(self, '_scope_status'):
            self._scope_status.set_palette(self.theme.statusbar_palette())
            self._scope_status.set_branding(self._get_branding_path())
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
        x0, x1 = self._plot.get_current_view_range()
        mid = (x0 + x1) / 2
        if cursor_id == 1 and self._plot._cursors.get(0) is not None:
            mid = self._plot._cursors[0] + (x1 - x0) * 0.1
        self._plot.set_cursor(cursor_id, mid)
        name = "A" if cursor_id == 0 else "B"
        self._status_lbl.setText(
            f"Cursor {name} placed at {mid:.6g} s — drag to move")

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
                         fft_min_freq=self._fft_min_freq, parent=self)
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

    def _on_trigger_set_t0(self, t_pos: float):
        """Shift all trace time axes so t_pos becomes 0."""
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
        self._plot.zoom_full()
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
        for trace in self._traces:
            ch_menu = self._per_trace_interp_menu.addMenu(trace.label)
            current = getattr(trace, '_interp_mode_override', self._interp_mode)
            a_lin = ch_menu.addAction("Linear")
            a_lin.setCheckable(True)
            a_lin.setChecked(current == "linear")
            a_sin = ch_menu.addAction("Sinc (sin(x)/x)")
            a_sin.setCheckable(True)
            a_sin.setChecked(current == "sinc")
            from PyQt6.QtGui import QActionGroup
            ag = QActionGroup(ch_menu)
            ag.setExclusive(True)
            ag.addAction(a_lin); ag.addAction(a_sin)
            name = trace.name
            a_lin.triggered.connect(
                lambda _, n=name: self._plot.set_interp_mode_for_trace(n, "linear"))
            a_sin.triggered.connect(
                lambda _, n=name: self._plot.set_interp_mode_for_trace(n, "sinc"))

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

    def _on_reset_trace_color(self, trace_name: str):
        """Reset a single trace to its theme-default colour."""
        idx = next((i for i, t in enumerate(self._traces)
                    if t.name == trace_name), 0)
        color = self.theme.trace_color(idx)
        for trace in self._traces:
            if trace.name == trace_name:
                trace.color = color
                break
        self._channel_panel.refresh_all()
        self._plot.refresh_all()
        self._refresh_status_bar()

    def _on_channel_interp_changed(self, name: str, mode: str):
        """Channel panel context menu or All Lin/Sinc triggered."""
        self._plot.set_interp_mode_for_trace(name, mode)
        self._refresh_status_bar()

    def _on_status_bar_toggle_interp(self, name: str):
        """Clicking the interp badge on a channel block in the status bar."""
        for trace in self._traces:
            if trace.name == name:
                cur = getattr(trace, '_interp_mode_override', self._interp_mode)
                new_mode = 'linear' if cur == 'sinc' else 'sinc'
                trace._interp_mode_override = new_mode
                self._plot.set_interp_mode_for_trace(name, new_mode)
                break
        self._refresh_status_bar()

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
