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
from PyQt6.QtGui import QKeySequence, QIcon, QPixmap, QColor, QAction
from typing import List, Optional

from core.trace_model import TraceModel, DEFAULT_TRACE_COLORS, DEFAULT_TRACE_COLORS_LIGHT
from core.theme_manager import ThemeManager
from core.data_loader import load_csv
from core.import_dialog import ImportDialog
from core.scope_plot_widget import ScopePlotWidget
from core.channel_panel import ChannelPanel
from core.cursor_panel import CursorPanel
from core.plugin_manager import PluginManager

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "settings.json")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PyScope")
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

    # ── UI Construction ────────────────────────────────────────────────

    def _build_ui(self):
        self._import_replace = self._settings.get("import_replace", True)
        self._import_reset_view = self._settings.get("import_reset_view", True)
        self._y_lock_auto = self._settings.get("y_lock_auto", True)
        self._fft_min_freq = self._settings.get("fft_min_freq", 1.0)

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
        self._splitter.addWidget(self._channel_panel)

        plot_colors = self.theme.plot_colors()
        self._plot = ScopePlotWidget(
            plot_colors, self.theme.theme_name, self._y_lock_auto)
        self._plot.cursor_values_changed.connect(self._on_cursor_values)
        self._splitter.addWidget(self._plot)

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
        a.triggered.connect(self._plot.zoom_full)
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
        a = view_menu.addAction("Theme: Dark")
        a.triggered.connect(lambda: self._set_theme("dark"))
        a = view_menu.addAction("Theme: Light")
        a.triggered.connect(lambda: self._set_theme("light"))
        a = view_menu.addAction("Theme: Green Phosphor")
        a.triggered.connect(lambda: self._set_theme("rs_green"))

        # ── Analysis ──────────────────────────────────────────────────
        analysis_menu = mb.addMenu("Analysis")
        a = analysis_menu.addAction("FFT...")
        a.setShortcut(QKeySequence("Ctrl+F"))
        a.triggered.connect(self._show_fft)
        a = analysis_menu.addAction("Filter...")
        a.triggered.connect(self._show_filter)
        a = analysis_menu.addAction("Clear All Filters")
        a.triggered.connect(self._clear_all_filters)

        # ── Plugins ───────────────────────────────────────────────────
        self._plugins_menu = mb.addMenu("Plugins")
        self._plugins_menu.addAction("Reload Plugins").triggered.connect(
            self._reload_plugins)
        self._plugins_menu.addAction("Open Plugins Folder").triggered.connect(
            self._open_plugins_dir)
        self._plugins_menu.addSeparator()
        self._plugin_actions_start = len(self._plugins_menu.actions())

        # ── Help ──────────────────────────────────────────────────────
        help_menu = mb.addMenu("Help")
        help_menu.addAction("About PyScope").triggered.connect(self._show_about)

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
        add("Fit (F)", self._plot.zoom_full)
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

        # Assign color from appropriate palette
        color_palette = (DEFAULT_TRACE_COLORS_LIGHT
                         if self.theme.theme_name == "light"
                         else DEFAULT_TRACE_COLORS)
        n = len(self._traces)
        trace.color = color_palette[n % len(color_palette)]

        self._traces.append(trace)
        self._channel_panel.add_trace(trace)
        self._plot.add_trace(trace)

    def _remove_trace(self, trace_name: str):
        self._traces = [t for t in self._traces if t.name != trace_name]
        self._channel_panel.remove_trace(trace_name)
        self._plot.remove_trace(trace_name)
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
            self, "Save Screenshot", "pyscope_capture.png",
            "PNG Images (*.png);;All Files (*)")
        if not path:
            return
        self._plot.take_screenshot(path, scale=2)
        self._status_lbl.setText(f"Screenshot: {os.path.basename(path)}")

    # ── Display / Theme ────────────────────────────────────────────────

    def _set_display_mode(self, mode: str):
        self._plot.set_mode(mode)

    def _set_theme(self, name: str, save: bool = True):
        from PyQt6.QtWidgets import QApplication
        self.theme.set_theme(name)
        QApplication.instance().setStyleSheet(self.theme.get_stylesheet())
        new_colors = self.theme.plot_colors()
        self._plot.theme = new_colors
        self._plot.theme_name = name
        self._plot._rebuild()
        if save:
            self._settings["theme"] = name

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

    # ── Trace Events ──────────────────────────────────────────────────

    def _on_trace_visibility(self, name: str, visible: bool):
        self._plot.set_trace_visible(name, visible)

    def _on_trace_color(self, name: str, color: str):
        self._plot.refresh_all()

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
        QMessageBox.about(self, "About PyScope",
            "<h2>PyScope</h2>"
            "<p>Modular oscilloscope data viewer.</p>"
            "<p><b>Keyboard shortcuts:</b><br>"
            "F — Zoom to fit &nbsp; T — Fit X &nbsp; A — Fit Y<br>"
            "L — Toggle Y auto-lock<br>"
            "+ / − — Zoom in/out<br>"
            "Ctrl+O — Open &nbsp; Ctrl+F — FFT<br>"
            "Ctrl+P — Screenshot &nbsp; Ctrl+Q — Quit</p>"
            "<p>CSV metadata: lines starting with <code>#</code> before the "
            "header are parsed.<br>"
            "Example: <code>#samplerate=10k</code>, "
            "<code>#gain=2.5/4096</code>, <code>#offset=-1.25</code></p>"
            "<p>Plugins: drop <code>.py</code> files in the "
            "<code>plugins/</code> folder.</p>")

    def closeEvent(self, event):
        self._save_settings()
        event.accept()
