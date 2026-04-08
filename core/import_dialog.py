"""
core/import_dialog.py
Import dialog: column selection, scaling, sample rate, time column config.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QLineEdit, QComboBox, QCheckBox, QPushButton, QScrollArea,
    QWidget, QGroupBox, QDoubleSpinBox, QTabWidget,
    QMessageBox, QRadioButton, QButtonGroup, QFrame, QSizePolicy
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
import numpy as np
from typing import Dict, List, Optional
from core.data_loader import LoadResult, is_numeric_column, CsvMetadata
from core.trace_model import TraceModel, ScalingConfig, DEFAULT_TRACE_COLORS


class ColumnConfigRow(QWidget):
    def __init__(self, col_name: str, data: np.ndarray, color: str,
                 is_time_candidate: bool = False,
                 metadata: CsvMetadata = None, parent=None):
        super().__init__(parent)
        self.col_name = col_name
        self.data = data
        self._is_numeric = is_numeric_column(data)
        meta = metadata or CsvMetadata()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(8)

        self.chk_enable = QCheckBox()
        self.chk_enable.setChecked(self._is_numeric and not is_time_candidate)
        self.chk_enable.setToolTip("Import this column as a trace")
        layout.addWidget(self.chk_enable)

        lbl = QLabel(col_name)
        lbl.setMinimumWidth(120)
        lbl.setMaximumWidth(200)
        lbl.setFont(QFont("Courier New", 9))
        layout.addWidget(lbl)

        self.edit_label = QLineEdit(col_name)
        self.edit_label.setMinimumWidth(100)
        self.edit_label.setMaximumWidth(150)
        layout.addWidget(self.edit_label)

        # ── Scaling ──────────────────────────────────────────────────
        self.chk_scale = QCheckBox("Scale")
        self.chk_scale.setChecked(False)
        self.chk_scale.toggled.connect(self._toggle_scaling)
        layout.addWidget(self.chk_scale)

        self.scale_widget = QWidget()
        sl = QHBoxLayout(self.scale_widget)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(4)

        # Gain
        sl.addWidget(QLabel("Gain:"))
        self.edit_gain = QLineEdit("1")
        self.edit_gain.setFixedWidth(70)
        self.edit_gain.setToolTip(
            "Gain multiplier. Supports fractions: 2.5/4096\n"
            "Applied as: output = raw * gain + offset")
        sl.addWidget(self.edit_gain)

        sl.addWidget(QLabel("Offset:"))
        self.spin_offset = QDoubleSpinBox()
        self.spin_offset.setRange(-1e12, 1e12)
        self.spin_offset.setDecimals(6)
        self.spin_offset.setValue(0.0)
        self.spin_offset.setFixedWidth(80)
        self.spin_offset.setToolTip("Additive offset after gain (in output units)")
        sl.addWidget(self.spin_offset)

        self.edit_unit = QLineEdit(meta.unit or "V")
        self.edit_unit.setFixedWidth(40)
        sl.addWidget(self.edit_unit)

        self.scale_widget.setEnabled(False)
        layout.addWidget(self.scale_widget)

        layout.addStretch()

        # Color swatch
        self.color = color
        self.btn_color = QPushButton()
        self.btn_color.setFixedSize(24, 20)
        self.btn_color.setStyleSheet(
            f"background-color: {color}; border: 1px solid #555;")
        self.btn_color.clicked.connect(self._pick_color)
        layout.addWidget(self.btn_color)

        # Stats
        if self._is_numeric and len(data) > 0:
            valid = data[~np.isnan(data.astype(float))] if data.dtype.kind == 'f' else data
            try:
                stats = f"n={len(data)}  min={float(valid.min()):.3g}  max={float(valid.max()):.3g}"
            except Exception:
                stats = f"n={len(data)}"
            lbl_stats = QLabel(stats)
            lbl_stats.setStyleSheet("color: #888; font-size: 9px;")
            layout.addWidget(lbl_stats)

        if not self._is_numeric:
            self.chk_enable.setChecked(False)
            self.chk_enable.setEnabled(False)
            self.chk_scale.setEnabled(False)

        # Pre-fill from metadata
        if meta.gain is not None and meta.gain != 1.0:
            self.chk_scale.setChecked(True)
            self.edit_gain.setText(str(meta.gain))
        if meta.offset is not None and meta.offset != 0.0:
            self.chk_scale.setChecked(True)
            self.spin_offset.setValue(meta.offset)

    def _toggle_scaling(self, enabled: bool):
        self.scale_widget.setEnabled(enabled)

    def _pick_color(self):
        from PyQt6.QtWidgets import QColorDialog
        c = QColorDialog.getColor(QColor(self.color), self, "Pick Trace Color")
        if c.isValid():
            self.color = c.name()
            self.btn_color.setStyleSheet(
                f"background-color: {self.color}; border: 1px solid #555;")

    def _parse_gain(self) -> float:
        """Parse the gain field, supporting fractions like 2.5/4096."""
        from core.data_loader import parse_value
        try:
            return parse_value(self.edit_gain.text())
        except Exception:
            return 1.0

    def get_scaling(self) -> ScalingConfig:
        gain = self._parse_gain()
        offset = self.spin_offset.value()
        enabled = self.chk_scale.isChecked()
        return ScalingConfig(
            enabled=enabled,
            use_gain_offset=True,
            gain=gain,
            offset=offset,
            unit=self.edit_unit.text(),
            # Keep range fields at defaults (unused when use_gain_offset=True)
        )

    def apply_scale_from(self, source: "ColumnConfigRow"):
        self.chk_scale.setChecked(source.chk_scale.isChecked())
        self.edit_gain.setText(source.edit_gain.text())
        self.spin_offset.setValue(source.spin_offset.value())
        self.edit_unit.setText(source.edit_unit.text())


class ImportDialog(QDialog):
    def __init__(self, load_result: LoadResult,
                 persistent_settings: dict = None, parent=None):
        super().__init__(parent)
        self.load_result = load_result
        self.result_traces: List[TraceModel] = []
        self._col_rows: Dict[str, ColumnConfigRow] = {}
        self._settings = persistent_settings or {}

        self.setWindowTitle(f"Import: {load_result.filename}")
        self.setMinimumSize(900, 580)
        self.resize(1100, 660)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        meta = self.load_result.metadata

        # ── Info bar ─────────────────────────────────────────────────
        info_parts = [
            f"File: <b>{self.load_result.filename}</b>",
            f"Rows: <b>{self.load_result.n_rows}</b>",
            f"Columns: <b>{len(self.load_result.columns)}</b>",
        ]
        meta_hints = []
        if meta.sample_rate:
            meta_hints.append(f"SPS={meta.sample_rate:.4g}")
        if meta.gain is not None:
            meta_hints.append(f"Gain={meta.gain:.4g}")
        if meta.offset is not None:
            meta_hints.append(f"Offset={meta.offset:.4g}")
        if meta_hints:
            info_parts.append(f"<span style='color:#80c080'>Metadata: {', '.join(meta_hints)}</span>")

        info = QLabel("  |  ".join(info_parts))
        info.setStyleSheet("padding: 6px; background: #1a1a2e; border-radius: 4px;")
        layout.addWidget(info)

        # ── Time-column detection banner ──────────────────────────────
        if self.load_result.suggested_time_col:
            banner = QLabel(
                f"⏱  Time column auto-detected: "
                f"<b>{self.load_result.suggested_time_col}</b>"
                f"  — verify on the Time Base tab.")
            banner.setStyleSheet(
                "padding: 5px 10px; background: #1a3020; color: #60e090; "
                "border-left: 3px solid #40c060; border-radius: 2px;")
            layout.addWidget(banner)

        tabs = QTabWidget()
        layout.addWidget(tabs)

        # ── Tab 1: Columns ────────────────────────────────────────────
        col_tab = QWidget()
        col_layout = QVBoxLayout(col_tab)

        tb = QHBoxLayout()
        btn_all = QPushButton("Select All")
        btn_none = QPushButton("Select None")
        btn_numeric = QPushButton("Select Numeric")
        btn_apply_scale = QPushButton("Apply Scale to All Selected")
        btn_all.clicked.connect(lambda: self._select_all(True))
        btn_none.clicked.connect(lambda: self._select_all(False))
        btn_numeric.clicked.connect(self._select_numeric)
        btn_apply_scale.clicked.connect(self._apply_scale_to_all)
        for b in [btn_all, btn_none, btn_numeric, btn_apply_scale]:
            tb.addWidget(b)
        tb.addStretch()
        col_layout.addLayout(tb)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setSpacing(2)

        color_idx = 0
        for i, (col_name, data) in enumerate(self.load_result.columns.items()):
            is_time = col_name == self.load_result.suggested_time_col
            color = DEFAULT_TRACE_COLORS[color_idx % len(DEFAULT_TRACE_COLORS)]
            if is_numeric_column(data) and not is_time:
                color_idx += 1
            row = ColumnConfigRow(col_name, data, color,
                                   is_time_candidate=is_time,
                                   metadata=meta)
            self._col_rows[col_name] = row
            if i > 0 and i % 5 == 0:
                line = QFrame()
                line.setFrameShape(QFrame.Shape.HLine)
                line.setStyleSheet("color: #333;")
                scroll_layout.addWidget(line)
            scroll_layout.addWidget(row)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_widget)
        col_layout.addWidget(scroll)
        tabs.addTab(col_tab, "Columns & Scaling")

        # ── Tab 2: Time Base ──────────────────────────────────────────
        time_tab = QWidget()
        tl = QVBoxLayout(time_tab)
        tl.setAlignment(Qt.AlignmentFlag.AlignTop)

        time_group = QGroupBox("Time Base Configuration")
        tg = QGridLayout(time_group)

        self.radio_sps = QRadioButton("Fixed Sample Rate")
        self.radio_dt = QRadioButton("Fixed dt (period)")
        self.radio_time_col = QRadioButton("Use Time Column")

        bg = QButtonGroup(self)
        bg.addButton(self.radio_sps)
        bg.addButton(self.radio_dt)
        bg.addButton(self.radio_time_col)

        default_sps = meta.sample_rate or self._settings.get("default_sample_rate", 1000.0)
        default_dt = meta.dt or (1.0 / default_sps if default_sps else 0.001)

        tg.addWidget(self.radio_sps, 0, 0)
        self.spin_sps = QDoubleSpinBox()
        self.spin_sps.setRange(1e-9, 1e15)
        self.spin_sps.setDecimals(3)
        self.spin_sps.setValue(default_sps)
        self.spin_sps.setSuffix(" Sa/s")
        self.spin_sps.valueChanged.connect(self._sps_changed)
        tg.addWidget(self.spin_sps, 0, 1)

        tg.addWidget(self.radio_dt, 1, 0)
        self.spin_dt = QDoubleSpinBox()
        self.spin_dt.setRange(1e-15, 1e9)
        self.spin_dt.setDecimals(9)
        self.spin_dt.setValue(default_dt)
        self.spin_dt.setSuffix(" s")
        self.spin_dt.valueChanged.connect(self._dt_changed)
        tg.addWidget(self.spin_dt, 1, 1)

        tg.addWidget(self.radio_time_col, 2, 0)
        self.combo_time_col = QComboBox()
        numeric_names = [n for n, d in self.load_result.columns.items()
                         if is_numeric_column(d)]
        self.combo_time_col.addItems(numeric_names)
        suggested = self.load_result.suggested_time_col
        if suggested and suggested in numeric_names:
            self.combo_time_col.setCurrentText(suggested)
            self.radio_time_col.setChecked(True)
        else:
            self.radio_sps.setChecked(True)
        tg.addWidget(self.combo_time_col, 2, 1)

        self.lbl_duration = QLabel()
        tg.addWidget(QLabel("Estimated duration:"), 3, 0)
        tg.addWidget(self.lbl_duration, 3, 1)
        tl.addWidget(time_group)

        self.spin_sps.valueChanged.connect(self._update_duration_label)
        self.spin_dt.valueChanged.connect(self._update_duration_label)
        self.radio_time_col.toggled.connect(self._update_duration_label)
        self._update_duration_label()
        tl.addStretch()
        tabs.addTab(time_tab, "Time Base")

        # ── Import options ────────────────────────────────────────────
        opt_group = QGroupBox("Import Options")
        og = QHBoxLayout(opt_group)

        self.chk_replace = QCheckBox("Replace existing data (clear all before import)")
        self.chk_replace.setChecked(self._settings.get("import_replace", True))
        self.chk_replace.setToolTip(
            "When checked, all currently loaded traces are removed before importing.\n"
            "When unchecked, new traces are added alongside existing ones.")
        og.addWidget(self.chk_replace)

        self.chk_reset_view = QCheckBox("Reset view after import")
        self.chk_reset_view.setChecked(self._settings.get("import_reset_view", True))
        og.addWidget(self.chk_reset_view)

        og.addStretch()
        layout.addWidget(opt_group)

        # ── Buttons ───────────────────────────────────────────────────
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_ok = QPushButton("Import")
        btn_ok.setDefault(True)
        btn_ok.setStyleSheet(
            "background: #2060c0; color: white; padding: 6px 20px; font-weight: bold;")
        btn_cancel.clicked.connect(self.reject)
        btn_ok.clicked.connect(self._do_import)
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_ok)
        layout.addLayout(btn_layout)

    def _sps_changed(self, val):
        if val > 0:
            self.spin_dt.blockSignals(True)
            self.spin_dt.setValue(1.0 / val)
            self.spin_dt.blockSignals(False)

    def _dt_changed(self, val):
        if val > 0:
            self.spin_sps.blockSignals(True)
            self.spin_sps.setValue(1.0 / val)
            self.spin_sps.blockSignals(False)

    def _update_duration_label(self):
        n = self.load_result.n_rows
        if self.radio_time_col.isChecked():
            col = self.combo_time_col.currentText()
            arr = self.load_result.columns.get(col)
            if arr is not None and is_numeric_column(arr) and len(arr) > 1:
                dur = float(arr[-1]) - float(arr[0])
            else:
                dur = 0.0
        else:
            dt = self.spin_dt.value()
            dur = n * dt
        s = _fmt_duration(dur)
        self.lbl_duration.setText(f"{s}  ({n} samples)")

    def _select_all(self, state):
        for row in self._col_rows.values():
            if row.chk_enable.isEnabled():
                row.chk_enable.setChecked(state)

    def _select_numeric(self):
        for row in self._col_rows.values():
            row.chk_enable.setChecked(
                row._is_numeric and row.chk_enable.isEnabled())

    def _apply_scale_to_all(self):
        source = None
        for row in self._col_rows.values():
            if row.chk_enable.isChecked() and row.chk_scale.isChecked():
                source = row
                break
        if not source:
            QMessageBox.information(self, "Apply Scale",
                "Enable scaling on at least one selected column first.")
            return
        for row in self._col_rows.values():
            if row.chk_enable.isChecked() and row is not source:
                row.apply_scale_from(source)

    def _do_import(self):
        use_time_col = self.radio_time_col.isChecked()
        time_col_name = self.combo_time_col.currentText() if use_time_col else None
        sps = self.spin_sps.value()
        dt = self.spin_dt.value()

        time_data = None
        if use_time_col and time_col_name:
            time_data = self.load_result.columns.get(time_col_name)

        traces = []
        for col_name, row in self._col_rows.items():
            if not row.chk_enable.isChecked():
                continue
            if col_name == time_col_name:
                continue

            data = self.load_result.columns[col_name].copy()
            scaling = row.get_scaling()

            trace = TraceModel(
                name=col_name,
                raw_data=data,
                time_data=time_data.copy() if time_data is not None else None,
                sample_rate=sps,
                dt=dt,
                color=row.color,
                label=row.edit_label.text() or col_name,
                unit=scaling.unit if scaling.enabled else "raw",
                scaling=scaling,
            )
            traces.append(trace)

        if not traces:
            QMessageBox.warning(self, "No Traces",
                "No columns selected for import.")
            return

        self.result_traces = traces
        self.replace_existing = self.chk_replace.isChecked()
        self.reset_view = self.chk_reset_view.isChecked()
        self.accept()


def _fmt_duration(dur: float) -> str:
    if dur <= 0:
        return "0 s"
    if dur < 1e-6:
        return f"{dur*1e9:.3g} ns"
    if dur < 1e-3:
        return f"{dur*1e6:.3g} µs"
    if dur < 1:
        return f"{dur*1e3:.3g} ms"
    return f"{dur:.4g} s"
