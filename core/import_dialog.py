"""
core/import_dialog.py
Import dialog: column selection, scaling, sample rate, time column config.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QLineEdit, QComboBox, QCheckBox, QPushButton, QScrollArea,
    QWidget, QGroupBox, QDoubleSpinBox, QSpinBox, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QRadioButton, QButtonGroup, QSizePolicy, QFrame
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
import numpy as np
from typing import Dict, List, Optional
from core.data_loader import LoadResult, is_numeric_column
from core.trace_model import TraceModel, ScalingConfig, DEFAULT_TRACE_COLORS


class ColumnConfigRow(QWidget):
    """One row in the column configuration table."""

    def __init__(self, col_name: str, data: np.ndarray, color: str,
                 is_time_candidate: bool = False, parent=None):
        super().__init__(parent)
        self.col_name = col_name
        self.data = data
        self._is_numeric = is_numeric_column(data)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(8)

        # Enable checkbox
        self.chk_enable = QCheckBox()
        self.chk_enable.setChecked(self._is_numeric and not is_time_candidate)
        self.chk_enable.setToolTip("Import this column as a trace")
        layout.addWidget(self.chk_enable)

        # Column name label
        lbl = QLabel(col_name)
        lbl.setMinimumWidth(120)
        lbl.setMaximumWidth(200)
        lbl.setFont(QFont("Courier New", 9))
        layout.addWidget(lbl)

        # Display name edit
        self.edit_label = QLineEdit(col_name)
        self.edit_label.setMinimumWidth(100)
        self.edit_label.setMaximumWidth(150)
        layout.addWidget(self.edit_label)

        # Scaling enable
        self.chk_scale = QCheckBox("Scale")
        self.chk_scale.setChecked(False)
        self.chk_scale.toggled.connect(self._toggle_scaling)
        layout.addWidget(self.chk_scale)

        # Scaling inputs (compact)
        self.scale_widget = QWidget()
        sl = QHBoxLayout(self.scale_widget)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(4)

        sl.addWidget(QLabel("In:"))
        self.spin_in_min = QDoubleSpinBox()
        self.spin_in_min.setRange(-1e12, 1e12)
        self.spin_in_min.setDecimals(4)
        self.spin_in_min.setValue(0)
        self.spin_in_min.setFixedWidth(80)
        sl.addWidget(self.spin_in_min)
        sl.addWidget(QLabel("→"))
        self.spin_in_max = QDoubleSpinBox()
        self.spin_in_max.setRange(-1e12, 1e12)
        self.spin_in_max.setDecimals(4)
        self.spin_in_max.setValue(4095)
        self.spin_in_max.setFixedWidth(80)
        sl.addWidget(self.spin_in_max)

        sl.addWidget(QLabel("Out:"))
        self.spin_out_min = QDoubleSpinBox()
        self.spin_out_min.setRange(-1e12, 1e12)
        self.spin_out_min.setDecimals(6)
        self.spin_out_min.setValue(-1.25)
        self.spin_out_min.setFixedWidth(80)
        sl.addWidget(self.spin_out_min)
        sl.addWidget(QLabel("→"))
        self.spin_out_max = QDoubleSpinBox()
        self.spin_out_max.setRange(-1e12, 1e12)
        self.spin_out_max.setDecimals(6)
        self.spin_out_max.setValue(1.25)
        self.spin_out_max.setFixedWidth(80)
        sl.addWidget(self.spin_out_max)

        sl.addWidget(QLabel("×"))
        self.spin_post = QDoubleSpinBox()
        self.spin_post.setRange(-1e12, 1e12)
        self.spin_post.setDecimals(6)
        self.spin_post.setValue(1.0)
        self.spin_post.setFixedWidth(70)
        self.spin_post.setToolTip("Post-scale multiplier (e.g. 0.25 A/V shunt)")
        sl.addWidget(self.spin_post)

        self.edit_unit = QLineEdit("V")
        self.edit_unit.setFixedWidth(40)
        sl.addWidget(self.edit_unit)

        self.scale_widget.setEnabled(False)
        layout.addWidget(self.scale_widget)

        layout.addStretch()

        # Color button
        self.color = color
        self.btn_color = QPushButton()
        self.btn_color.setFixedSize(24, 20)
        self.btn_color.setStyleSheet(f"background-color: {color}; border: 1px solid #555;")
        self.btn_color.clicked.connect(self._pick_color)
        layout.addWidget(self.btn_color)

        # Stats label
        if self._is_numeric and len(data) > 0:
            valid = data[~np.isnan(data)]
            if len(valid) > 0:
                stats = f"n={len(data)}  min={valid.min():.3g}  max={valid.max():.3g}"
            else:
                stats = f"n={len(data)}"
            lbl_stats = QLabel(stats)
            lbl_stats.setStyleSheet("color: #888; font-size: 9px;")
            layout.addWidget(lbl_stats)

        if not self._is_numeric:
            self.chk_enable.setChecked(False)
            self.chk_enable.setEnabled(False)
            self.chk_scale.setEnabled(False)

    def _toggle_scaling(self, enabled: bool):
        self.scale_widget.setEnabled(enabled)

    def _pick_color(self):
        from PyQt6.QtWidgets import QColorDialog
        c = QColorDialog.getColor(QColor(self.color), self, "Pick Trace Color")
        if c.isValid():
            self.color = c.name()
            self.btn_color.setStyleSheet(
                f"background-color: {self.color}; border: 1px solid #555;")

    def get_scaling(self) -> ScalingConfig:
        return ScalingConfig(
            enabled=self.chk_scale.isChecked(),
            input_min=self.spin_in_min.value(),
            input_max=self.spin_in_max.value(),
            output_min=self.spin_out_min.value(),
            output_max=self.spin_out_max.value(),
            unit=self.edit_unit.text(),
            post_scale=self.spin_post.value(),
        )


class ImportDialog(QDialog):
    """Full import configuration dialog."""

    def __init__(self, load_result: LoadResult, parent=None):
        super().__init__(parent)
        self.load_result = load_result
        self.result_traces: List[TraceModel] = []
        self._col_rows: Dict[str, ColumnConfigRow] = {}

        self.setWindowTitle(f"Import: {load_result.filename}")
        self.setMinimumSize(1000, 600)
        self.resize(1200, 700)

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Header info
        info = QLabel(
            f"File: <b>{self.load_result.filename}</b>  |  "
            f"Rows: <b>{self.load_result.n_rows}</b>  |  "
            f"Columns: <b>{len(self.load_result.columns)}</b>"
        )
        info.setStyleSheet("padding: 6px; background: #1a1a2e; border-radius: 4px;")
        layout.addWidget(info)

        tabs = QTabWidget()
        layout.addWidget(tabs)

        # ── Tab 1: Column Selection ──────────────────────────────────────
        col_tab = QWidget()
        col_layout = QVBoxLayout(col_tab)

        # Toolbar
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

        # Scroll area for column rows
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setSpacing(2)

        numeric_cols = [n for n, d in self.load_result.columns.items()
                        if is_numeric_column(d)]
        color_idx = 0
        for i, (col_name, data) in enumerate(self.load_result.columns.items()):
            is_time = col_name == self.load_result.suggested_time_col
            color = DEFAULT_TRACE_COLORS[color_idx % len(DEFAULT_TRACE_COLORS)]
            if is_numeric_column(data) and not is_time:
                color_idx += 1
            row = ColumnConfigRow(col_name, data, color, is_time_candidate=is_time)
            self._col_rows[col_name] = row

            # Separator every 5 rows
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

        # ── Tab 2: Time / Sample Rate ────────────────────────────────────
        time_tab = QWidget()
        tl = QVBoxLayout(time_tab)
        tl.setAlignment(Qt.AlignmentFlag.AlignTop)

        time_group = QGroupBox("Time Base Configuration")
        tg = QGridLayout(time_group)

        self.radio_sps = QRadioButton("Fixed Sample Rate")
        self.radio_dt = QRadioButton("Fixed dt (period)")
        self.radio_time_col = QRadioButton("Use Time Column")
        self.radio_sps.setChecked(True)

        bg = QButtonGroup(self)
        bg.addButton(self.radio_sps)
        bg.addButton(self.radio_dt)
        bg.addButton(self.radio_time_col)

        tg.addWidget(self.radio_sps, 0, 0)
        self.spin_sps = QDoubleSpinBox()
        self.spin_sps.setRange(1e-9, 1e15)
        self.spin_sps.setDecimals(3)
        self.spin_sps.setValue(1000.0)
        self.spin_sps.setSuffix(" Sa/s")
        self.spin_sps.valueChanged.connect(self._sps_changed)
        tg.addWidget(self.spin_sps, 0, 1)

        tg.addWidget(self.radio_dt, 1, 0)
        self.spin_dt = QDoubleSpinBox()
        self.spin_dt.setRange(1e-15, 1e9)
        self.spin_dt.setDecimals(9)
        self.spin_dt.setValue(0.001)
        self.spin_dt.setSuffix(" s")
        self.spin_dt.valueChanged.connect(self._dt_changed)
        tg.addWidget(self.spin_dt, 1, 1)

        tg.addWidget(self.radio_time_col, 2, 0)
        self.combo_time_col = QComboBox()
        numeric_names = [n for n, d in self.load_result.columns.items()
                         if is_numeric_column(d)]
        self.combo_time_col.addItems(numeric_names)
        if self.load_result.suggested_time_col in numeric_names:
            self.combo_time_col.setCurrentText(self.load_result.suggested_time_col)
        tg.addWidget(self.combo_time_col, 2, 1)

        self.lbl_duration = QLabel()
        tg.addWidget(QLabel("Estimated duration:"), 3, 0)
        tg.addWidget(self.lbl_duration, 3, 1)

        tl.addWidget(time_group)
        self._update_duration_label()

        self.spin_sps.valueChanged.connect(self._update_duration_label)
        self.spin_dt.valueChanged.connect(self._update_duration_label)

        tl.addStretch()
        tabs.addTab(time_tab, "Time Base")

        # ── Buttons ──────────────────────────────────────────────────────
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_ok = QPushButton("Import")
        btn_ok.setDefault(True)
        btn_ok.setStyleSheet("background: #2060c0; color: white; padding: 6px 20px; font-weight: bold;")
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
        dt = self.spin_dt.value()
        dur = n * dt
        if dur < 1e-6:
            s = f"{dur*1e9:.3g} ns"
        elif dur < 1e-3:
            s = f"{dur*1e6:.3g} µs"
        elif dur < 1:
            s = f"{dur*1e3:.3g} ms"
        else:
            s = f"{dur:.4g} s"
        self.lbl_duration.setText(f"{s}  ({n} samples)")

    def _select_all(self, state: bool):
        for row in self._col_rows.values():
            if row.chk_enable.isEnabled():
                row.chk_enable.setChecked(state)

    def _select_numeric(self):
        for row in self._col_rows.values():
            row.chk_enable.setChecked(
                row._is_numeric and row.chk_enable.isEnabled())

    def _apply_scale_to_all(self):
        # Find first selected row with scaling enabled, copy to all selected
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
                row.chk_scale.setChecked(True)
                row.spin_in_min.setValue(source.spin_in_min.value())
                row.spin_in_max.setValue(source.spin_in_max.value())
                row.spin_out_min.setValue(source.spin_out_min.value())
                row.spin_out_max.setValue(source.spin_out_max.value())
                row.spin_post.setValue(source.spin_post.value())
                row.edit_unit.setText(source.edit_unit.text())

    def _do_import(self):
        # Determine time settings
        use_time_col = self.radio_time_col.isChecked()
        time_col_name = self.combo_time_col.currentText() if use_time_col else None
        sps = self.spin_sps.value()
        dt = self.spin_dt.value()

        time_data = None
        if use_time_col and time_col_name:
            time_data = self.load_result.columns.get(time_col_name)

        traces = []
        color_idx = 0
        for col_name, row in self._col_rows.items():
            if not row.chk_enable.isChecked():
                continue
            if col_name == time_col_name:
                continue

            data = self.load_result.columns[col_name].copy()
            color = row.color
            scaling = row.get_scaling()

            trace = TraceModel(
                name=col_name,
                raw_data=data,
                time_data=time_data.copy() if time_data is not None else None,
                sample_rate=sps,
                dt=dt,
                color=color,
                label=row.edit_label.text() or col_name,
                unit=scaling.unit if scaling.enabled else "raw",
                scaling=scaling,
            )
            traces.append(trace)

        if not traces:
            QMessageBox.warning(self, "No Traces", "No columns selected for import.")
            return

        self.result_traces = traces
        self.accept()
