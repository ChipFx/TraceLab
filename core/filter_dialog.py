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
_FTYPE = ["lowpass", "highpass", "bandpass", "bandstop", "notch", "peak", "comb"]

# Filter family keys (index matches combo_family order)
_FAMILY = ["butterworth", "bessel"]


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
            self.tr("Bandpass"), self.tr("Bandstop"),
            self.tr("Notch"), self.tr("Peak"), self.tr("Comb")])
        self.combo_type.currentIndexChanged.connect(self._update_ui)
        fl.addWidget(self.combo_type, 0, 1, 1, 2)

        self.lbl_family = QLabel(self.tr("Family:"))
        self.combo_family = QComboBox()
        self.combo_family.addItems([self.tr("Butterworth"), self.tr("Bessel")])
        fl.addWidget(self.lbl_family, 1, 0)
        fl.addWidget(self.combo_family, 1, 1, 1, 2)

        self.lbl_order = QLabel(self.tr("Order:"))
        self.spin_order = QDoubleSpinBox()
        self.spin_order.setRange(1, 10)
        self.spin_order.setDecimals(0)
        self.spin_order.setValue(4)
        fl.addWidget(self.lbl_order, 2, 0)
        fl.addWidget(self.spin_order, 2, 1, 1, 2)

        self.lbl_q = QLabel(self.tr("Q factor:"))
        self.spin_q = QDoubleSpinBox()
        self.spin_q.setRange(0.1, 1000.0)
        self.spin_q.setDecimals(2)
        self.spin_q.setValue(30.0)
        self.spin_q.setToolTip(self.tr(
            "Quality factor — higher Q = narrower bandwidth.\n"
            "Typical: Notch/Peak 10–100, Comb 30–300."))
        fl.addWidget(self.lbl_q, 3, 0)
        fl.addWidget(self.spin_q, 3, 1, 1, 2)

        self.lbl_fc1 = QLabel(self.tr("Cutoff freq:"))
        self.edit_fc1 = QLineEdit("1 kHz")
        self.edit_fc1.setPlaceholderText(
            self.tr("e.g. 1kHz  200uHz  1.5MHz  0.0002"))
        self.lbl_fc1_fb = QLabel()
        self.lbl_fc1_fb.setMinimumWidth(110)
        fl.addWidget(self.lbl_fc1, 4, 0)
        fl.addWidget(self.edit_fc1, 4, 1)
        fl.addWidget(self.lbl_fc1_fb, 4, 2)

        self.lbl_fc2 = QLabel(self.tr("High cutoff:"))
        self.edit_fc2 = QLineEdit("5 kHz")
        self.edit_fc2.setPlaceholderText(self.tr("e.g. 5kHz  10MHz"))
        self.lbl_fc2_fb = QLabel()
        self.lbl_fc2_fb.setMinimumWidth(110)
        fl.addWidget(self.lbl_fc2, 5, 0)
        fl.addWidget(self.edit_fc2, 5, 1)
        fl.addWidget(self.lbl_fc2_fb, 5, 2)

        layout.addWidget(grp_filt)

        # Comb filter cost warning — shown when filter order × data length is large
        self._lbl_comb_warn = QLabel()
        self._lbl_comb_warn.setStyleSheet(
            "color: #ffcc44; background: #2a1e00; font-size: 9pt; "
            "padding: 5px 8px; border: 1px solid #665500; border-radius: 3px;")
        self._lbl_comb_warn.setWordWrap(True)
        self._lbl_comb_warn.setVisible(False)
        layout.addWidget(self._lbl_comb_warn)

        self.edit_fc1.textChanged.connect(
            lambda: self._update_fb(self.edit_fc1, self.lbl_fc1_fb))
        self.edit_fc2.textChanged.connect(
            lambda: self._update_fb(self.edit_fc2, self.lbl_fc2_fb))
        self.combo_type.currentIndexChanged.connect(self._update_comb_warn)
        self.edit_fc1.textChanged.connect(lambda: self._update_comb_warn())
        self.trace_list.itemSelectionChanged.connect(self._update_comb_warn)

        self._update_ui()
        self._update_fb(self.edit_fc1, self.lbl_fc1_fb)
        self._update_fb(self.edit_fc2, self.lbl_fc2_fb)
        self._update_comb_warn()

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
        idx = self.combo_type.currentIndex()
        is_iir_simple = idx in (4, 5, 6)   # notch / peak / comb
        two_freqs = idx in (2, 3)           # bandpass / bandstop

        # Family and Order only for polynomial filter types (0-3)
        self.lbl_family.setVisible(not is_iir_simple)
        self.combo_family.setVisible(not is_iir_simple)
        self.lbl_order.setVisible(not is_iir_simple)
        self.spin_order.setVisible(not is_iir_simple)

        # Q only for notch / peak / comb
        self.lbl_q.setVisible(is_iir_simple)
        self.spin_q.setVisible(is_iir_simple)

        # Second frequency only for band-type filters
        self.lbl_fc2.setVisible(two_freqs)
        self.edit_fc2.setVisible(two_freqs)
        self.lbl_fc2_fb.setVisible(two_freqs)

        # Relabel fc1 for single-frequency IIR types
        if is_iir_simple:
            self.lbl_fc1.setText(self.tr("Center freq:"))
        elif two_freqs:
            self.lbl_fc1.setText(self.tr("Low cutoff:"))
        else:
            self.lbl_fc1.setText(self.tr("Cutoff freq:"))

    def _update_comb_warn(self):
        """Show a warning when the comb filter order × data length will be expensive."""
        if _FTYPE[self.combo_type.currentIndex()] != "comb":
            self._lbl_comb_warn.setVisible(False)
            return
        fc1 = _parse_si_freq(self.edit_fc1.text())
        if fc1 is None or fc1 <= 0:
            self._lbl_comb_warn.setVisible(False)
            return

        names = self._selected_names()
        worst_cost = 0
        worst_order = 0
        worst_n = 0
        for t in self.traces:
            if names and t.name not in names:
                continue
            sps = getattr(t, 'sample_rate', None)
            if not sps or sps <= 0:
                continue
            data = getattr(t, 'processed_data', None)
            n = len(data) if data is not None else 0
            order = round(sps / fc1)
            cost = n * order
            if cost > worst_cost:
                worst_cost = cost
                worst_order = order
                worst_n = n

        # Warn when convolution work is large enough to feel slow (empirically ~1 s+)
        if worst_order > 100 and worst_cost > 10_000_000:
            if worst_cost > 200_000_000:
                severity = self.tr("may take 10+ seconds")
            else:
                severity = self.tr("may take a few seconds")
            self._lbl_comb_warn.setText(
                self.tr(
                    "\u26a0\u2002 Comb at {fc}: filter order \u223c{order:,}"
                    " \u00d7 {n:,} samples \u2014 {sev}."
                ).format(
                    fc=_format_si_freq(fc1),
                    order=worst_order,
                    n=worst_n,
                    sev=severity,
                ))
            self._lbl_comb_warn.setVisible(True)
        else:
            self._lbl_comb_warn.setVisible(False)

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
        is_iir_simple = ftype in ("notch", "peak", "comb")
        order = int(self.spin_order.value())
        family = _FAMILY[self.combo_family.currentIndex()]
        Q = self.spin_q.value()

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
                sos = None

                if is_iir_simple:
                    # iirnotch / iirpeak / iircomb return b, a directly.
                    # tf2sos / zpk2sos give the numerically stable SOS form.
                    if ftype == "notch":
                        b, a = sp_signal.iirnotch(fc1, Q, fs=sps)
                        sos = sp_signal.tf2sos(b, a)
                        desc = f"Notch {_format_si_freq(fc1)} Q{Q:.3g}"
                    elif ftype == "peak":
                        b, a = sp_signal.iirpeak(fc1, Q, fs=sps)
                        sos = sp_signal.tf2sos(b, a)
                        desc = f"Peak {_format_si_freq(fc1)} Q{Q:.3g}"
                    else:  # comb
                        # iircomb is higher-order; zpk route avoids tf2sos precision loss.
                        b, a = sp_signal.iircomb(fc1, Q, ftype='notch', fs=sps)
                        z, p, k = sp_signal.tf2zpk(b, a)
                        sos = sp_signal.zpk2sos(z, p, k)
                        desc = f"Comb {_format_si_freq(fc1)} Q{Q:.3g}"

                elif family == "bessel":
                    # Bessel with norm='mag' gives a consistent −3 dB cutoff
                    # at the specified frequency, matching Butterworth convention.
                    if ftype == "lowpass":
                        wn = min(fc1 / nyq, 0.9999)
                        sos = sp_signal.bessel(order, wn, btype="low",
                                               output='sos', norm='mag')
                        desc = f"BsLP {_format_si_freq(fc1)}"
                    elif ftype == "highpass":
                        wn = min(fc1 / nyq, 0.9999)
                        sos = sp_signal.bessel(order, wn, btype="high",
                                               output='sos', norm='mag')
                        desc = f"BsHP {_format_si_freq(fc1)}"
                    elif ftype == "bandpass":
                        wn = [min(fc1 / nyq, 0.499), min(fc2 / nyq, 0.9999)]
                        sos = sp_signal.bessel(order, wn, btype="band",
                                               output='sos', norm='mag')
                        desc = f"BsBP {_format_si_freq(fc1)}–{_format_si_freq(fc2)}"
                    else:  # bandstop
                        wn = [min(fc1 / nyq, 0.499), min(fc2 / nyq, 0.9999)]
                        sos = sp_signal.bessel(order, wn, btype="bandstop",
                                               output='sos', norm='mag')
                        desc = f"BsBS {_format_si_freq(fc1)}–{_format_si_freq(fc2)}"

                else:  # butterworth
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
