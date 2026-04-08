"""
core/import_dialog.py
Import dialog with locale-safe number inputs and working gain/offset scaling.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QLineEdit, QComboBox, QCheckBox, QPushButton, QScrollArea,
    QWidget, QGroupBox, QDoubleSpinBox, QTabWidget,
    QMessageBox, QRadioButton, QButtonGroup, QFrame
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
import numpy as np
from typing import Dict, List, Optional
from core.data_loader import LoadResult, is_numeric_column, CsvMetadata, parse_value
from core.trace_model import TraceModel, ScalingConfig, DEFAULT_TRACE_COLORS


# ── Locale-safe number input ──────────────────────────────────────────────────

class SciLineEdit(QLineEdit):
    """
    A QLineEdit for scientific/engineering numbers.
    - Accepts both '.' and ',' as decimal separator regardless of locale.
    - Supports fractions: 2.5/4096
    - Supports metric suffixes: 10k, 2.2M
    - On focus-in: selects all text (easy to replace with new value)
    """

    def __init__(self, default: str = "1", parent=None):
        super().__init__(default, parent)
        self.setToolTip(
            "Enter a number. Both '.' and ',' work as decimal separator.\n"
            "Fractions supported: 2.5/4096\n"
            "Metric suffixes: 10k, 2.2M, 100n")

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.selectAll()

    def get_value(self, default: float = 1.0) -> float:
        """Parse with locale tolerance: comma → dot."""
        text = self.text().strip()
        # Replace comma-as-decimal with dot, but only when it looks like
        # a decimal separator (i.e. not followed by 3 digits = thousands sep)
        import re
        # Normalise: if single comma present and not thousands-style, treat as decimal
        text = _normalise_decimal(text)
        try:
            return parse_value(text)
        except Exception:
            return default


def _normalise_decimal(s: str) -> str:
    """Convert locale decimal comma to dot for parse_value."""
    import re
    # Already has a dot → leave as-is (parse_value handles it)
    if '.' in s:
        return s
    # Replace comma that looks like decimal separator:
    # "1,25" → "1.25"  but "1,250,000" → leave (rare in our context)
    # Simple rule: replace the LAST comma if there's only one
    parts = s.split(',')
    if len(parts) == 2:
        return parts[0] + '.' + parts[1]
    return s


class SciSpinBox(QWidget):
    """Label + SciLineEdit combo that mimics a spinbox but locale-tolerant."""
    def __init__(self, default: float = 0.0, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._edit = SciLineEdit(str(default))
        layout.addWidget(self._edit)

    def value(self) -> float:
        return self._edit.get_value(0.0)

    def setValue(self, v: float):
        # Format sensibly
        if v == 0.0:
            self._edit.setText("0")
        elif abs(v) >= 1e4 or (abs(v) < 1e-3 and v != 0):
            self._edit.setText(f"{v:.6e}")
        else:
            self._edit.setText(f"{v:.8g}")

    def setFixedWidth(self, w):
        self._edit.setFixedWidth(w)
        super().setFixedWidth(w)

    @property
    def edit(self):
        return self._edit


# ── Column config row ─────────────────────────────────────────────────────────

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
        lbl.setMinimumWidth(110)
        lbl.setMaximumWidth(180)
        lbl.setFont(QFont("Courier New", 9))
        layout.addWidget(lbl)

        self.edit_label = SciLineEdit(col_name)
        self.edit_label.setToolTip("Display label for this trace")
        self.edit_label.setMinimumWidth(90)
        self.edit_label.setMaximumWidth(140)
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

        sl.addWidget(QLabel("Gain:"))
        self.edit_gain = SciLineEdit("1")
        self.edit_gain.setFixedWidth(75)
        self.edit_gain.setToolTip(
            "Multiplier: output = raw × gain + offset\n"
            "Fractions OK: 2.5/4096  Suffixes OK: 10k")
        sl.addWidget(self.edit_gain)

        sl.addWidget(QLabel("Offset:"))
        self.edit_offset = SciLineEdit("0")
        self.edit_offset.setFixedWidth(75)
        self.edit_offset.setToolTip(
            "Additive offset after gain (in output units)\n"
            "Decimal: use '.' or ',' — both accepted")
        sl.addWidget(self.edit_offset)

        self.edit_unit = SciLineEdit(meta.unit or "V")
        self.edit_unit.setFixedWidth(38)
        self.edit_unit.setToolTip("Physical unit label (V, A, °C, …)")
        sl.addWidget(self.edit_unit)

        self.scale_widget.setEnabled(False)
        layout.addWidget(self.scale_widget)

        layout.addStretch()

        # Color swatch
        self.color = color
        self.btn_color = QPushButton()
        self.btn_color.setFixedSize(22, 20)
        self.btn_color.setStyleSheet(
            f"background-color: {color}; border: 1px solid #555;")
        self.btn_color.clicked.connect(self._pick_color)
        layout.addWidget(self.btn_color)

        # Stats
        if self._is_numeric and len(data) > 0:
            try:
                d = data.astype(float)
                valid = d[np.isfinite(d)]
                if len(valid):
                    stats = (f"n={len(data)}"
                             f"  min={valid.min():.3g}"
                             f"  max={valid.max():.3g}")
                else:
                    stats = f"n={len(data)}"
            except Exception:
                stats = f"n={len(data)}"
            lbl_stats = QLabel(stats)
            lbl_stats.setStyleSheet("color: #888; font-size: 9px;")
            layout.addWidget(lbl_stats)

        if not self._is_numeric:
            self.chk_enable.setChecked(False)
            self.chk_enable.setEnabled(False)
            self.chk_scale.setEnabled(False)

        # Pre-fill from CSV metadata
        if meta.gain is not None and meta.gain != 1.0:
            self.chk_scale.setChecked(True)
            self.edit_gain.setText(str(meta.gain))
        if meta.offset is not None and meta.offset != 0.0:
            self.chk_scale.setChecked(True)
            self.edit_offset.setValue(meta.offset)

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
        gain = self.edit_gain.get_value(1.0)
        offset = self.edit_offset.get_value(0.0)
        enabled = self.chk_scale.isChecked()
        unit = self.edit_unit.edit.text().strip() or "V"
        return ScalingConfig(
            enabled=enabled,
            use_gain_offset=True,
            gain=gain,
            offset=offset,
            unit=unit,
        )

    def apply_scale_from(self, source: "ColumnConfigRow"):
        self.chk_scale.setChecked(source.chk_scale.isChecked())
        self.edit_gain.setText(source.edit_gain.text())
        self.edit_offset.setText(source.edit_offset.text())
        self.edit_unit.setText(source.edit_unit.edit.text())


# ── Import dialog ─────────────────────────────────────────────────────────────

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

        # ── Info bar ──────────────────────────────────────────────────
        info_parts = [
            f"File: <b>{self.load_result.filename}</b>",
            f"Rows: <b>{self.load_result.n_rows}</b>",
            f"Columns: <b>{len(self.load_result.columns)}</b>",
        ]
        meta_hints = []
        if meta.sample_rate:
            meta_hints.append(f"SPS={meta.sample_rate:.4g}")
        if meta.gain is not None:
            meta_hints.append(f"Gain={meta.gain:.6g}")
        if meta.offset is not None and meta.offset != 0:
            meta_hints.append(f"Offset={meta.offset:.6g}")
        if meta.unit:
            meta_hints.append(f"Unit={meta.unit}")
        if meta_hints:
            info_parts.append(
                f"<span style='color:#80c080'>📋 Metadata: "
                f"{', '.join(meta_hints)}</span>")

        info = QLabel("  |  ".join(info_parts))
        info.setStyleSheet(
            "padding: 6px; background: #1a1a2e; border-radius: 4px;")
        layout.addWidget(info)

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
        cl = QVBoxLayout(col_tab)

        tb = QHBoxLayout()
        for label, fn in [
            ("Select All",  lambda: self._select_all(True)),
            ("Select None", lambda: self._select_all(False)),
            ("Select Numeric", self._select_numeric),
            ("Apply Scale to All Selected", self._apply_scale_to_all),
        ]:
            b = QPushButton(label)
            b.clicked.connect(fn)
            tb.addWidget(b)
        tb.addStretch()
        cl.addLayout(tb)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        sw = QWidget()
        sl = QVBoxLayout(sw)
        sl.setSpacing(2)

        color_idx = 0
        for i, (col_name, data) in enumerate(self.load_result.columns.items()):
            is_time = col_name == self.load_result.suggested_time_col
            color = DEFAULT_TRACE_COLORS[color_idx % len(DEFAULT_TRACE_COLORS)]
            if is_numeric_column(data) and not is_time:
                color_idx += 1
            row = ColumnConfigRow(col_name, data, color,
                                   is_time_candidate=is_time, metadata=meta)
            self._col_rows[col_name] = row
            if i > 0 and i % 5 == 0:
                line = QFrame()
                line.setFrameShape(QFrame.Shape.HLine)
                line.setStyleSheet("color: #333;")
                sl.addWidget(line)
            sl.addWidget(row)

        sl.addStretch()
        scroll.setWidget(sw)
        cl.addWidget(scroll)
        tabs.addTab(col_tab, "Columns && Scaling")

        # ── Tab 2: Time Base ──────────────────────────────────────────
        time_tab = QWidget()
        tl = QVBoxLayout(time_tab)
        tl.setAlignment(Qt.AlignmentFlag.AlignTop)

        tg_box = QGroupBox("Time Base Configuration")
        tg = QGridLayout(tg_box)

        self.radio_sps = QRadioButton("Fixed Sample Rate")
        self.radio_dt  = QRadioButton("Fixed dt (period)")
        self.radio_time_col = QRadioButton("Use Time Column")
        bg = QButtonGroup(self)
        for r in (self.radio_sps, self.radio_dt, self.radio_time_col):
            bg.addButton(r)

        default_sps = meta.sample_rate or self._settings.get("default_sample_rate", 1000.0)
        default_dt  = meta.dt or (1.0 / default_sps if default_sps else 0.001)

        tg.addWidget(self.radio_sps, 0, 0)
        self.edit_sps = SciLineEdit(f"{default_sps:.6g}")
        self.edit_sps.setToolTip("Samples per second. Use suffix: 10k, 2.2M")
        self.edit_sps.editingFinished.connect(self._sps_changed)
        tg.addWidget(self.edit_sps, 0, 1)
        tg.addWidget(QLabel("Sa/s"), 0, 2)

        tg.addWidget(self.radio_dt, 1, 0)
        self.edit_dt = SciLineEdit(f"{default_dt:.9g}")
        self.edit_dt.setToolTip("Seconds per sample. Use suffix: 100n, 1u")
        self.edit_dt.editingFinished.connect(self._dt_changed)
        tg.addWidget(self.edit_dt, 1, 1)
        tg.addWidget(QLabel("s"), 1, 2)

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
        tl.addWidget(tg_box)

        # Time offset
        tz_box = QGroupBox("Time Zero Offset")
        tzl = QHBoxLayout(tz_box)
        tzl.addWidget(QLabel("t=0 at sample #:"))
        self.edit_t0_sample = SciLineEdit("0")
        self.edit_t0_sample.setFixedWidth(80)
        self.edit_t0_sample.setToolTip(
            "Set this sample index as t=0. Points before it get negative time.\n"
            "Also set by #zerotime=N in CSV headers.")
        tzl.addWidget(self.edit_t0_sample)
        tzl.addWidget(QLabel("  or time value:"))
        self.edit_t0_time = SciLineEdit("0")
        self.edit_t0_time.setFixedWidth(90)
        self.edit_t0_time.setToolTip(
            "Subtract this time value from all time points.\n"
            "E.g. enter 0.5 to make t=0.5 the new zero.")
        tzl.addWidget(self.edit_t0_time)
        tzl.addStretch()
        tl.addWidget(tz_box)

        # Pre-fill zerotime from metadata
        if hasattr(meta, 'zerotime') and meta.zerotime is not None:
            self.edit_t0_sample.setText(str(meta.zerotime))

        for r in (self.radio_sps, self.radio_dt, self.radio_time_col):
            r.toggled.connect(self._update_duration_label)
        self.combo_time_col.currentTextChanged.connect(self._update_duration_label)
        self._update_duration_label()
        tl.addStretch()
        tabs.addTab(time_tab, "Time Base")

        # ── Import options ────────────────────────────────────────────
        opt_box = QGroupBox("Import Options")
        og = QHBoxLayout(opt_box)
        self.chk_replace = QCheckBox("Replace existing data")
        self.chk_replace.setChecked(self._settings.get("import_replace", True))
        self.chk_replace.setToolTip(
            "Clear all current traces before importing.\n"
            "Uncheck to add alongside existing traces.")
        og.addWidget(self.chk_replace)
        self.chk_reset_view = QCheckBox("Reset view after import")
        self.chk_reset_view.setChecked(self._settings.get("import_reset_view", True))
        og.addWidget(self.chk_reset_view)
        og.addStretch()
        layout.addWidget(opt_box)

        # ── Buttons ───────────────────────────────────────────────────
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_ok = QPushButton("Import")
        btn_ok.setDefault(True)
        btn_ok.setStyleSheet(
            "background: #2060c0; color: white; padding: 6px 20px; "
            "font-weight: bold;")
        btn_cancel.clicked.connect(self.reject)
        btn_ok.clicked.connect(self._do_import)
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_ok)
        layout.addLayout(btn_layout)

    def _sps_changed(self):
        try:
            sps = self.edit_sps.get_value(0)
            if sps > 0:
                dt = 1.0 / sps
                self.edit_dt.setText(f"{dt:.9g}")
                self._update_duration_label()
        except Exception:
            pass

    def _dt_changed(self):
        try:
            dt = self.edit_dt.get_value(0)
            if dt > 0:
                sps = 1.0 / dt
                self.edit_sps.setText(f"{sps:.6g}")
                self._update_duration_label()
        except Exception:
            pass

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
            try:
                dt = self.edit_dt.get_value(0)
                dur = n * dt if dt > 0 else 0.0
            except Exception:
                dur = 0.0
        self.lbl_duration.setText(f"{_fmt_duration(dur)}  ({n} samples)")

    def _select_all(self, state):
        for row in self._col_rows.values():
            if row.chk_enable.isEnabled():
                row.chk_enable.setChecked(state)

    def _select_numeric(self):
        for row in self._col_rows.values():
            row.chk_enable.setChecked(
                row._is_numeric and row.chk_enable.isEnabled())

    def _apply_scale_to_all(self):
        source = next((r for r in self._col_rows.values()
                       if r.chk_enable.isChecked() and r.chk_scale.isChecked()),
                      None)
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

        sps = self.edit_sps.get_value(1000.0)
        dt  = self.edit_dt.get_value(0.001)
        if sps <= 0:
            sps = 1.0 / dt if dt > 0 else 1000.0
        if dt <= 0:
            dt = 1.0 / sps if sps > 0 else 0.001

        time_data = None
        if use_time_col and time_col_name:
            time_data = self.load_result.columns.get(time_col_name)

        # Time zero offset
        t0_sample = int(self.edit_t0_sample.get_value(0))
        t0_time   = self.edit_t0_time.get_value(0.0)

        traces = []
        for col_name, row in self._col_rows.items():
            if not row.chk_enable.isChecked():
                continue
            if col_name == time_col_name:
                continue

            raw = self.load_result.columns[col_name].copy()
            scaling = row.get_scaling()

            # Build time axis with zero offset applied
            td = None
            if time_data is not None:
                td = time_data.copy().astype(float)
                if t0_sample > 0:
                    if 0 < t0_sample < len(td):
                        td = td - td[t0_sample]
                    else:
                        td = td - td[0]
                elif t0_time != 0.0:
                    td = td - t0_time

            trace = TraceModel(
                name=col_name,
                raw_data=raw,
                time_data=td,
                sample_rate=sps,
                dt=dt,
                color=row.color,
                label=row.edit_label.text().strip() or col_name,
                unit=scaling.unit if scaling.enabled else "raw",
                scaling=scaling,
            )

            # For sample-based time, apply t0 offset via dt-based shift
            if time_data is None and t0_sample > 0:
                trace._t0_sample_offset = t0_sample  # store for time_axis calc

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
    if dur <= 0: return "0 s"
    if dur < 1e-6: return f"{dur*1e9:.3g} ns"
    if dur < 1e-3: return f"{dur*1e6:.3g} µs"
    if dur < 1:    return f"{dur*1e3:.3g} ms"
    return f"{dur:.4g} s"
