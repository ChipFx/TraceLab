"""
core/main_window.py
Main application window.
"""

import os
import sys
import json
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QToolBar, QStatusBar, QFileDialog, QMessageBox, QLabel,
    QPushButton, QComboBox, QDockWidget, QMenu, QInputDialog,
    QDialog
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence, QIcon, QPixmap, QColor
from typing import List, Dict, Optional

from core.trace_model import TraceModel, DEFAULT_TRACE_COLORS
from core.theme_manager import ThemeManager
from core.data_loader import load_csv
from core.import_dialog import ImportDialog
from core.scope_plot_widget import ScopePlotWidget
from core.channel_panel import ChannelPanel
from core.cursor_panel import CursorPanel
from core.plugin_manager import PluginManager


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PyScope")
        self.resize(1400, 800)

        self.theme = ThemeManager()
        self._traces: List[TraceModel] = []
        self._plugins = PluginManager()
        self._plugins.discover()
        self._placing_cursor: Optional[int] = None  # 0 or 1 when placing

        self._build_ui()
        self._build_menus()
        self._build_toolbar()
        self._build_statusbar()
        self._update_plugin_menu()

        # Load settings
        self._load_settings()

    # ── UI Construction ────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Main splitter
        self._splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: channel panel
        self._channel_panel = ChannelPanel()
        self._channel_panel.visibility_changed.connect(self._on_trace_visibility)
        self._channel_panel.color_changed.connect(self._on_trace_color)
        self._channel_panel.trace_removed.connect(self._remove_trace)
        self._splitter.addWidget(self._channel_panel)

        # Center: plot
        plot_colors = self.theme.plot_colors()
        self._plot = ScopePlotWidget(plot_colors)
        self._plot.cursor_values_changed.connect(self._on_cursor_values)
        self._splitter.addWidget(self._plot)

        # Right: cursor panel
        self._cursor_panel = CursorPanel()
        self._cursor_panel.place_cursor.connect(self._start_cursor_placement)
        self._splitter.addWidget(self._cursor_panel)

        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setStretchFactor(2, 0)
        self._splitter.setSizes([180, 900, 220])

        main_layout.addWidget(self._splitter)

    def _build_menus(self):
        mb = self.menuBar()

        # File
        file_menu = mb.addMenu("File")
        act_open = QAction("Open CSV...", self)
        act_open.setShortcut(QKeySequence("Ctrl+O"))
        act_open.triggered.connect(self._open_csv)
        file_menu.addAction(act_open)

        file_menu.addSeparator()

        act_export = QAction("Export Visible Data as CSV...", self)
        act_export.triggered.connect(self._export_csv)
        file_menu.addAction(act_export)

        act_screenshot = QAction("Save Screenshot...", self)
        act_screenshot.setShortcut(QKeySequence("Ctrl+P"))
        act_screenshot.triggered.connect(self._save_screenshot)
        file_menu.addAction(act_screenshot)

        file_menu.addSeparator()
        act_quit = QAction("Quit", self)
        act_quit.setShortcut(QKeySequence("Ctrl+Q"))
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        # View
        view_menu = mb.addMenu("View")
        act_split = QAction("Split Lanes (LeCroy Style)", self)
        act_split.triggered.connect(lambda: self._set_display_mode("split"))
        view_menu.addAction(act_split)

        act_overlay = QAction("Overlay All Traces", self)
        act_overlay.triggered.connect(lambda: self._set_display_mode("overlay"))
        view_menu.addAction(act_overlay)

        view_menu.addSeparator()

        act_zoom_full = QAction("Zoom to Fit", self)
        act_zoom_full.setShortcut(QKeySequence("F"))
        act_zoom_full.triggered.connect(self._plot.zoom_full)
        view_menu.addAction(act_zoom_full)

        act_zoom_in = QAction("Zoom In (X)", self)
        act_zoom_in.setShortcut(QKeySequence("+"))
        act_zoom_in.triggered.connect(lambda: self._plot.zoom_in(0.5))
        view_menu.addAction(act_zoom_in)

        act_zoom_out = QAction("Zoom Out (X)", self)
        act_zoom_out.setShortcut(QKeySequence("-"))
        act_zoom_out.triggered.connect(lambda: self._plot.zoom_out(2.0))
        view_menu.addAction(act_zoom_out)

        view_menu.addSeparator()

        act_theme_dark = QAction("Theme: Dark", self)
        act_theme_dark.triggered.connect(lambda: self._set_theme("dark"))
        view_menu.addAction(act_theme_dark)

        act_theme_light = QAction("Theme: Light", self)
        act_theme_light.triggered.connect(lambda: self._set_theme("light"))
        view_menu.addAction(act_theme_light)

        act_theme_green = QAction("Theme: Green Phosphor", self)
        act_theme_green.triggered.connect(lambda: self._set_theme("rs_green"))
        view_menu.addAction(act_theme_green)

        # Analysis
        analysis_menu = mb.addMenu("Analysis")
        act_fft = QAction("FFT...", self)
        act_fft.setShortcut(QKeySequence("Ctrl+F"))
        act_fft.triggered.connect(self._show_fft)
        analysis_menu.addAction(act_fft)

        act_filter = QAction("Apply Filter...", self)
        act_filter.triggered.connect(self._show_filter)
        analysis_menu.addAction(act_filter)

        # Plugins
        self._plugins_menu = mb.addMenu("Plugins")
        act_reload = QAction("Reload Plugins", self)
        act_reload.triggered.connect(self._reload_plugins)
        self._plugins_menu.addAction(act_reload)
        act_open_dir = QAction("Open Plugins Folder", self)
        act_open_dir.triggered.connect(self._open_plugins_dir)
        self._plugins_menu.addAction(act_open_dir)
        self._plugins_menu.addSeparator()
        self._plugin_actions_start = len(self._plugins_menu.actions())

        # Help
        help_menu = mb.addMenu("Help")
        act_about = QAction("About PyScope", self)
        act_about.triggered.connect(self._show_about)
        help_menu.addAction(act_about)

    def _build_toolbar(self):
        tb = self.addToolBar("Main")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)

        # Open
        act_open = QAction("Open", self)
        act_open.triggered.connect(self._open_csv)
        tb.addAction(act_open)

        tb.addSeparator()

        # View mode
        act_split = QAction("Split", self)
        act_split.triggered.connect(lambda: self._set_display_mode("split"))
        tb.addAction(act_split)

        act_overlay = QAction("Overlay", self)
        act_overlay.triggered.connect(lambda: self._set_display_mode("overlay"))
        tb.addAction(act_overlay)

        tb.addSeparator()

        # Cursor buttons
        act_cur_a = QAction("Cursor A", self)
        act_cur_a.triggered.connect(lambda: self._start_cursor_placement(0))
        tb.addAction(act_cur_a)

        act_cur_b = QAction("Cursor B", self)
        act_cur_b.triggered.connect(lambda: self._start_cursor_placement(1))
        tb.addAction(act_cur_b)

        tb.addSeparator()

        # Zoom
        act_zoom_fit = QAction("Fit", self)
        act_zoom_fit.triggered.connect(self._plot.zoom_full)
        tb.addAction(act_zoom_fit)

        tb.addSeparator()

        # Analysis
        act_fft = QAction("FFT", self)
        act_fft.triggered.connect(self._show_fft)
        tb.addAction(act_fft)

        act_filter = QAction("Filter", self)
        act_filter.triggered.connect(self._show_filter)
        tb.addAction(act_filter)

        tb.addSeparator()

        act_screenshot = QAction("Screenshot", self)
        act_screenshot.triggered.connect(self._save_screenshot)
        tb.addAction(act_screenshot)

    def _build_statusbar(self):
        sb = self.statusBar()
        self._status_lbl = QLabel("Ready  |  No data loaded")
        self._status_lbl.setStyleSheet("padding: 2px 8px;")
        sb.addWidget(self._status_lbl)

        self._cursor_status = QLabel("")
        self._cursor_status.setStyleSheet(
            "padding: 2px 8px; color: #aaa;")
        sb.addPermanentWidget(self._cursor_status)

    # ── File Operations ────────────────────────────────────────────────

    def _open_csv(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Data File", "",
            "CSV Files (*.csv *.tsv *.txt);;All Files (*)")
        if not path:
            return

        result = load_csv(path)
        if result.error:
            QMessageBox.critical(self, "Load Error", result.error)
            return

        dlg = ImportDialog(result, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        for trace in dlg.result_traces:
            self._add_trace(trace)

        self._update_status()

    def _add_trace(self, trace: TraceModel):
        # Assign default color if needed
        existing = len(self._traces)
        if trace.color == DEFAULT_TRACE_COLORS[0] and existing > 0:
            trace.color = DEFAULT_TRACE_COLORS[existing % len(DEFAULT_TRACE_COLORS)]

        self._traces.append(trace)
        self._channel_panel.add_trace(trace)
        self._plot.add_trace(trace)

    def _remove_trace(self, trace_name: str):
        self._traces = [t for t in self._traces if t.name != trace_name]
        self._channel_panel.remove_trace(trace_name)
        self._plot.remove_trace(trace_name)
        self._update_status()

    def _export_csv(self):
        if not self._traces:
            QMessageBox.information(self, "Export", "No data loaded.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Visible Data", "", "CSV Files (*.csv)")
        if not path:
            return

        x0, x1 = self._plot.get_current_view_range()
        visible = [t for t in self._traces if t.visible]

        if not visible:
            return

        # Use first trace's time as reference
        import numpy as np
        lines = []
        header = ["time"] + [t.label for t in visible]
        lines.append(",".join(header))

        ref_t = visible[0].time_axis
        mask = (ref_t >= x0) & (ref_t <= x1)
        ref_t = ref_t[mask]

        for i, t_val in enumerate(ref_t):
            row = [f"{t_val:.10g}"]
            for trace in visible:
                ta = trace.time_axis
                ya = trace.processed_data
                # Quick nearest sample
                idx = int(round((t_val - ta[0]) / trace.dt)) if trace.dt > 0 else 0
                idx = max(0, min(idx, len(ya)-1))
                row.append(f"{ya[idx]:.10g}")
            lines.append(",".join(row))

        with open(path, "w") as f:
            f.write("\n".join(lines))
        self._status_lbl.setText(f"Exported to {os.path.basename(path)}")

    def _save_screenshot(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Screenshot", "pyscope_capture.png",
            "PNG Images (*.png);;All Files (*)")
        if not path:
            return
        self._plot.take_screenshot(path, scale=2)
        self._status_lbl.setText(f"Screenshot saved: {os.path.basename(path)}")

    # ── Display / Theme ────────────────────────────────────────────────

    def _set_display_mode(self, mode: str):
        self._plot.set_mode(mode)

    def _set_theme(self, name: str):
        self.theme.set_theme(name)
        from PyQt6.QtWidgets import QApplication
        QApplication.instance().setStyleSheet(self.theme.get_stylesheet())
        # Rebuild plot with new colors
        new_colors = self.theme.plot_colors()
        self._plot.theme = new_colors
        self._plot._rebuild()

    # ── Cursors ────────────────────────────────────────────────────────

    def _start_cursor_placement(self, cursor_id: int):
        """Place cursor at center of current view."""
        x0, x1 = self._plot.get_current_view_range()
        mid = (x0 + x1) / 2
        # Offset B slightly from A if already placed
        if cursor_id == 1 and self._plot._cursors.get(0) is not None:
            mid = self._plot._cursors[0] + (x1 - x0) * 0.1
        self._plot.set_cursor(cursor_id, mid)
        name = "A" if cursor_id == 0 else "B"
        self._status_lbl.setText(
            f"Cursor {name} placed at {mid:.6g} s — drag to move")

    def _on_cursor_values(self, data: dict):
        self._cursor_panel.update_cursors(data)
        # Update status bar with delta if both cursors set
        t_a = data.get(0, {}).get("time")
        t_b = data.get(1, {}).get("time")
        if t_a is not None and t_b is not None:
            dt = t_b - t_a
            self._cursor_status.setText(f"ΔT = {dt:.6g} s")

    # ── Trace Events ──────────────────────────────────────────────────

    def _on_trace_visibility(self, trace_name: str, visible: bool):
        self._plot.set_trace_visible(trace_name, visible)

    def _on_trace_color(self, trace_name: str, color: str):
        self._plot.refresh_all()

    # ── Analysis ──────────────────────────────────────────────────────

    def _show_fft(self):
        if not self._traces:
            QMessageBox.information(self, "FFT", "No data loaded.")
            return
        from core.fft_dialog import FFTDialog
        vr = self._plot.get_current_view_range()
        dlg = FFTDialog(self._traces, view_range=vr, parent=self)
        dlg.exec()

    def _show_filter(self):
        if not self._traces:
            QMessageBox.information(self, "Filter", "No data loaded.")
            return
        from core.filter_dialog import FilterDialog
        dlg = FilterDialog(self._traces, parent=self)
        dlg.filters_applied.connect(self._on_filters_applied)
        dlg.exec()

    def _on_filters_applied(self, trace_names):
        self._plot.refresh_all()
        self._status_lbl.setText(
            f"Filter applied to: {', '.join(trace_names)}")

    # ── Plugins ───────────────────────────────────────────────────────

    def _update_plugin_menu(self):
        # Remove old plugin actions
        actions = self._plugins_menu.actions()
        for act in actions[self._plugin_actions_start:]:
            self._plugins_menu.removeAction(act)

        plugins = self._plugins.get_plugins()
        if not plugins:
            no_act = QAction("(No plugins found)", self)
            no_act.setEnabled(False)
            self._plugins_menu.addAction(no_act)
        else:
            for plugin in plugins:
                act = QAction(
                    f"{plugin.name} [{plugin.plugin_type}]", self)
                act.setStatusTip(plugin.description)
                act.triggered.connect(
                    lambda checked, p=plugin: self._run_plugin(p))
                self._plugins_menu.addAction(act)

        if self._plugins.load_errors:
            self._plugins_menu.addSeparator()
            err_act = QAction(
                f"⚠ {len(self._plugins.load_errors)} load error(s)", self)
            err_act.triggered.connect(self._show_plugin_errors)
            self._plugins_menu.addAction(err_act)

    def _run_plugin(self, plugin):
        import copy
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
                # Update traces with results
                name_map = {t.name: t for t in result}
                for trace in self._traces:
                    if trace.name in name_map:
                        r = name_map[trace.name]
                        trace.raw_data = r.raw_data
                        trace.scaling.enabled = False
                        trace._invalidate_cache()
                self._plot.refresh_all()
                self._status_lbl.setText(
                    f"Plugin '{plugin.name}' applied.")
        except Exception as e:
            QMessageBox.critical(self, "Plugin Error", str(e))

    def _reload_plugins(self):
        self._plugins.reload()
        self._update_plugin_menu()
        n = self._plugins.plugin_count
        self._status_lbl.setText(f"Plugins reloaded: {n} found.")

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
        errors = "\n\n".join(self._plugins.load_errors)
        QMessageBox.warning(self, "Plugin Load Errors", errors)

    # ── Settings ──────────────────────────────────────────────────────

    def _load_settings(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                             "settings.json")
        if os.path.exists(path):
            try:
                with open(path) as f:
                    s = json.load(f)
                if "geometry" in s:
                    self.restoreGeometry(
                        bytes.fromhex(s["geometry"]))
            except Exception:
                pass

    def _save_settings(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                             "settings.json")
        s = {
            "theme": self.theme.theme_name,
            "geometry": self.saveGeometry().toHex().data().decode(),
        }
        try:
            with open(path, "w") as f:
                json.dump(s, f, indent=2)
        except Exception:
            pass

    def closeEvent(self, event):
        self._save_settings()
        event.accept()

    # ── Status ────────────────────────────────────────────────────────

    def _update_status(self):
        n = len(self._traces)
        vis = sum(1 for t in self._traces if t.visible)
        if n == 0:
            self._status_lbl.setText("Ready  |  No data loaded")
        else:
            total_pts = sum(t.n_samples for t in self._traces)
            self._status_lbl.setText(
                f"{n} traces loaded  |  {vis} visible  |  "
                f"{total_pts:,} total samples")

    # ── About ─────────────────────────────────────────────────────────

    def _show_about(self):
        QMessageBox.about(self, "About PyScope",
            "<h2>PyScope</h2>"
            "<p>A modular oscilloscope data viewer.</p>"
            "<p>Built with Python, PyQt6, and PyQtGraph.</p>"
            "<p><b>Keyboard shortcuts:</b><br>"
            "F — Zoom to fit<br>"
            "+ / − — Zoom in/out<br>"
            "Ctrl+O — Open file<br>"
            "Ctrl+F — FFT<br>"
            "Ctrl+P — Screenshot<br>"
            "</p>"
            "<p>Add plugins to the <code>plugins/</code> folder.</p>")
