"""
core/filter_dialog.py
Non-destructive filters: raw_data is never modified.
Filtered result is stored in trace._filter_data; clearing restores original.
"""

import re
import numpy as np
from scipy import signal as sp_signal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QDoubleSpinBox, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QGroupBox, QGridLayout, QMessageBox,
)
from PyQt6.QtCore import Qt, pyqtSignal
from typing import List, Optional
from core.trace_model import TraceModel


# ── SI frequency helpers ──────────────────────────────────────────────────────

_SI_PREFIXES = {
    'T': 1e12, 'G': 1e9, 'M': 1e6,
    'k': 1e3,  'K': 1e3,
    '':  1.0,
    'm': 1e-3,
    'u': 1e-6, 'µ': 1e-6, 'μ': 1e-6,
    'n': 1e-9, 'p': 1e-12, 'f': 1e-15,
}

_SI_PARSE_RE = re.compile(
    r'^\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)'
    r'\s*(T|G|M|k|K|m|u|µ|μ|n|p|f)?'
    r'\s*(?:[Hh][Zz])?\s*$'
)


def _parse_si_freq(text: str) -> Optional[float]:
    """Parse a frequency string with optional SI prefix / Hz suffix.
    Returns Hz as float, or None if unparseable.
    Accepts: '200u'  '200uHz'  '1.5kHz'  '0.0002'  '2M'  '500nHz'  '1.2THz'
    """
    text = text.strip()
    if not text:
        return None
    m = _SI_PARSE_RE.match(text)
    if m:
        value = float(m.group(1))
        prefix = m.group(2) or ''
        return value * _SI_PREFIXES.get(prefix, 1.0)
    try:
        return float(text)
    except ValueError:
        return None


def _format_si_freq(hz: float) -> str:
    """Format a frequency in Hz using the most readable SI prefix."""
    if hz <= 0:
        return f"{hz:g} Hz"
    for scale, prefix in [
        (1e12, 'T'), (1e9, 'G'), (1e6, 'M'), (1e3, 'k'),
        (1.0,  ''),  (1e-3, 'm'), (1e-6, 'µ'), (1e-9, 'n'),
        (1e-12, 'p'), (1e-15, 'f'),
    ]:
        if hz >= scale * 0.9995:
            val = hz / scale
            unit = f"{prefix}Hz" if prefix else "Hz"
            return f"{val:.4g} {unit}"
    return f"{hz:.4g} Hz"


def _format_duration(seconds: float) -> str:
    if seconds >= 3600:
        return f"{seconds / 3600:.3g}h"
    if seconds >= 60:
        return f"{seconds / 60:.3g}min"
    if seconds >= 1:
        return f"{seconds:.3g}s"
    if seconds >= 1e-3:
        return f"{seconds * 1e3:.3g}ms"
    return f"{seconds * 1e6:.3g}µs"


# ── Dialog ────────────────────────────────────────────────────────────────────

# Internal filter type keys (index matches combo box order)
_FTYPE = ["lowpass", "highpass", "bandpass", "bandstop"]


class FilterDialog(QDialog):
    filters_applied = pyqtSignal(list)

    def __init__(self, traces: List[TraceModel], parent=None):
        super().__init__(parent)
        self.traces = [t for t in traces if t.visible]
        self.setWindowTitle(self.tr("Signal Filters"))
        self.resize(560, 480)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Active filter overview
        active = [t for t in self.traces if t.has_filter]
        if active:
            info = QLabel(
                "Active filters: " +
                ", ".join(f"{t.label} ({t.filter_description})" for t in active))
            info.setStyleSheet(
                "color: #80e0a0; padding: 4px; background: #102010; border-radius:3px;")
            info.setWordWrap(True)
            layout.addWidget(info)

        grp_trace = QGroupBox(self.tr("Select Traces"))
        tl = QVBoxLayout(grp_trace)
        self.trace_list = QTableWidget()
        self.trace_list.setColumnCount(3)
        self.trace_list.setHorizontalHeaderLabels([
            self.tr("Trace"), self.tr("Nyquist"), self.tr("Min freq (data duration)")])
        self.trace_list.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self.trace_list.setSelectionMode(
            QTableWidget.SelectionMode.MultiSelection)
        self.trace_list.verticalHeader().setVisible(False)
        self.trace_list.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.trace_list.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents)
        self.trace_list.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents)
        self.trace_list.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        for t in self.traces:
            row = self.trace_list.rowCount()
            self.trace_list.insertRow(row)
            label = t.label
            if t.has_filter:
                label += f"  [filtered: {t.filter_description}]"
            name_item = QTableWidgetItem(label)
            name_item.setData(Qt.ItemDataRole.UserRole, t.name)
            self.trace_list.setItem(row, 0, name_item)
            sps = getattr(t, 'sample_rate', None)
            data = getattr(t, 'processed_data', None)
            n = len(data) if data is not None else 0
            if sps and sps > 0 and n > 0:
                nyq = sps / 2.0
                duration = n / sps
                min_f = 1.0 / duration
                nyq_str = _format_si_freq(nyq)
                min_str = f"{_format_si_freq(min_f)}  ({_format_duration(duration)})"
            else:
                nyq_str = "—"
                min_str = "—"
            self.trace_list.setItem(row, 1, QTableWidgetItem(nyq_str))
            self.trace_list.setItem(row, 2, QTableWidgetItem(min_str))
            self.trace_list.selectRow(row)
        tl.addWidget(self.trace_list)
        layout.addWidget(grp_trace)

        grp_filt = QGroupBox(self.tr("Filter Settings"))
        fl = QGridLayout(grp_filt)

        fl.addWidget(QLabel(self.tr("Type:")), 0, 0)
        self.combo_type = QComboBox()
        self.combo_type.addItems([
            self.tr("Lowpass"), self.tr("Highpass"),
            self.tr("Bandpass"), self.tr("Bandstop")])
        self.combo_type.currentIndexChanged.connect(self._update_ui)
        fl.addWidget(self.combo_type, 0, 1, 1, 2)

        fl.addWidget(QLabel(self.tr("Order:")), 1, 0)
        self.spin_order = QDoubleSpinBox()
        self.spin_order.setRange(1, 10)
        self.spin_order.setDecimals(0)
        self.spin_order.setValue(4)
        fl.addWidget(self.spin_order, 1, 1, 1, 2)

        self.lbl_fc1 = QLabel(self.tr("Cutoff freq:"))
        self.edit_fc1 = QLineEdit("1 kHz")
        self.edit_fc1.setPlaceholderText(
            self.tr("e.g. 1kHz  200uHz  1.5MHz  0.0002"))
        self.lbl_fc1_fb = QLabel()
        self.lbl_fc1_fb.setMinimumWidth(110)
        fl.addWidget(self.lbl_fc1, 2, 0)
        fl.addWidget(self.edit_fc1, 2, 1)
        fl.addWidget(self.lbl_fc1_fb, 2, 2)

        self.lbl_fc2 = QLabel(self.tr("High cutoff:"))
        self.edit_fc2 = QLineEdit("5 kHz")
        self.edit_fc2.setPlaceholderText(self.tr("e.g. 5kHz  10MHz"))
        self.lbl_fc2_fb = QLabel()
        self.lbl_fc2_fb.setMinimumWidth(110)
        fl.addWidget(self.lbl_fc2, 3, 0)
        fl.addWidget(self.edit_fc2, 3, 1)
        fl.addWidget(self.lbl_fc2_fb, 3, 2)

        layout.addWidget(grp_filt)

        self.edit_fc1.textChanged.connect(
            lambda: self._update_fb(self.edit_fc1, self.lbl_fc1_fb))
        self.edit_fc2.textChanged.connect(
            lambda: self._update_fb(self.edit_fc2, self.lbl_fc2_fb))

        self._update_ui()
        self._update_fb(self.edit_fc1, self.lbl_fc1_fb)
        self._update_fb(self.edit_fc2, self.lbl_fc2_fb)

        btn_layout = QHBoxLayout()
        btn_clear = QPushButton(self.tr("Clear Filters on Selected"))
        btn_clear.clicked.connect(self._clear_filters)
        btn_layout.addWidget(btn_clear)
        btn_layout.addStretch()
        btn_cancel = QPushButton(self.tr("Cancel"))
        btn_cancel.clicked.connect(self.reject)
        btn_apply = QPushButton(self.tr("Apply Filter"))
        btn_apply.setStyleSheet(
            "background: #2060c0; color: white; padding: 6px 16px;")
        btn_apply.clicked.connect(self._apply)
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_apply)
        layout.addLayout(btn_layout)

    # ── UI helpers ────────────────────────────────────────────────────────────

    def _update_fb(self, edit: QLineEdit, label: QLabel):
        """Update the parsed-value feedback label next to a frequency input."""
        hz = _parse_si_freq(edit.text())
        if hz is None:
            label.setText(self.tr("invalid"))
            label.setStyleSheet("color: #e05050;")
            edit.setStyleSheet("border: 1px solid #e05050;")
        elif hz <= 0:
            label.setText(self.tr("must be > 0"))
            label.setStyleSheet("color: #e05050;")
            edit.setStyleSheet("border: 1px solid #e05050;")
        else:
            label.setText(f"= {_format_si_freq(hz)}")
            label.setStyleSheet("color: #60c060;")
            edit.setStyleSheet("")

    def _update_ui(self):
        two_freqs = self.combo_type.currentIndex() in (2, 3)  # bandpass or bandstop
        self.lbl_fc2.setVisible(two_freqs)
        self.edit_fc2.setVisible(two_freqs)
        self.lbl_fc2_fb.setVisible(two_freqs)

    # ── Data helpers ──────────────────────────────────────────────────────────

    def _selected_names(self):
        rows = {idx.row() for idx in self.trace_list.selectedIndexes()}
        result = set()
        for row in rows:
            item = self.trace_list.item(row, 0)
            if item:
                result.add(item.data(Qt.ItemDataRole.UserRole))
        return result

    # ── Actions ───────────────────────────────────────────────────────────────

    def _clear_filters(self):
        names = self._selected_names()
        cleared = []
        for trace in self.traces:
            if trace.name in names and trace.has_filter:
                trace.clear_filter()
                cleared.append(trace.name)
        if cleared:
            self.filters_applied.emit(cleared)
            self.accept()

    def _apply(self):
        names = self._selected_names()
        ftype = _FTYPE[self.combo_type.currentIndex()]
        order = int(self.spin_order.value())

        fc1 = _parse_si_freq(self.edit_fc1.text())
        fc2 = _parse_si_freq(self.edit_fc2.text())

        if fc1 is None or fc1 <= 0:
            QMessageBox.warning(self, self.tr("Invalid Input"),
                self.tr("Please enter a valid cutoff frequency."))
            return
        if ftype in ("bandpass", "bandstop") and (fc2 is None or fc2 <= fc1):
            QMessageBox.warning(self, self.tr("Invalid Input"),
                self.tr("High cutoff must be greater than low cutoff."))
            return

        modified = []
        for trace in self.traces:
            if trace.name not in names:
                continue
            sps = trace.sample_rate
            nyq = sps / 2.0
            try:
                if ftype == "lowpass":
                    wn = min(fc1 / nyq, 0.9999)
                    sos = sp_signal.butter(order, wn, btype="low", output='sos')
                    desc = f"LP {_format_si_freq(fc1)}"
                elif ftype == "highpass":
                    wn = min(fc1 / nyq, 0.9999)
                    sos = sp_signal.butter(order, wn, btype="high", output='sos')
                    desc = f"HP {_format_si_freq(fc1)}"
                elif ftype == "bandpass":
                    wn = [min(fc1 / nyq, 0.499), min(fc2 / nyq, 0.9999)]
                    sos = sp_signal.butter(order, wn, btype="band", output='sos')
                    desc = f"BP {_format_si_freq(fc1)}–{_format_si_freq(fc2)}"
                else:  # bandstop
                    wn = [min(fc1 / nyq, 0.499), min(fc2 / nyq, 0.9999)]
                    sos = sp_signal.butter(order, wn, btype="bandstop", output='sos')
                    desc = f"BS {_format_si_freq(fc1)}–{_format_si_freq(fc2)}"

                # sosfiltfilt is numerically stable at extreme frequency ratios.
                # The old b,a form loses precision when wn << 1 (e.g. µHz-range
                # cutoffs on a slow logger) and silently produces garbage output.
                filtered = sp_signal.sosfiltfilt(sos, trace.processed_data)
                trace.set_filter(filtered, desc)
                modified.append(trace.name)

            except Exception as e:
                QMessageBox.warning(self, self.tr("Filter Error"),
                    f"Failed to filter {trace.label}: {e}")

        if modified:
            self.filters_applied.emit(modified)
            self.accept()
