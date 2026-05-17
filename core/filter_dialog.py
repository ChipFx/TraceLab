"""
core/filter_dialog.py
UI for configuring a single filter recipe and pushing it onto one or more
traces' filter stacks.

This module is UI-only: signal-processing lives in core/filter_engine.py.
On Apply, the dialog emits filter_recipe_added(recipe, trace_names);
main_window receives, appends to each trace's stack, and triggers a
re-apply + downstream re-evaluation.

"Clear Filters on Selected" emits clear_requested(trace_names) so main_window
can pop the affected stacks and refresh.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QDoubleSpinBox, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QGroupBox, QGridLayout, QMessageBox,
)
from PyQt6.QtCore import Qt, pyqtSignal
from typing import List
from pytraceview.trace_model import TraceModel
from core.filter_engine import (
    FilterRecipe, parse_si_freq, format_si_freq, describe_recipe,
)


# Local thin aliases so the rest of the file (UI helpers) keeps the old names
_parse_si_freq  = parse_si_freq
_format_si_freq = format_si_freq


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
    # main_window appends `recipe` to each of `trace_names`' stacks then
    # re-applies and re-evaluates downstream maths.
    filter_recipe_added = pyqtSignal(object, list)   # (FilterRecipe, [trace_names])

    # main_window pops the entire stack for each name and refreshes.
    clear_requested     = pyqtSignal(list)            # [trace_names]

    # Used by EditFilterStackDialog when it asks for one filter targeted
    # at a single trace.  When set, "Apply" emits and closes immediately;
    # the trace-selection list is hidden.
    def __init__(self, traces: List[TraceModel], parent=None,
                 single_trace_name: str = ""):
        super().__init__(parent)
        self.traces = [t for t in traces if t.visible or t.name == single_trace_name]
        self._single_trace = single_trace_name
        self.setWindowTitle(self.tr("Signal Filters"))
        self.resize(560, 480)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # In single-trace mode (called from EditFilterStackDialog), narrow the
        # working list to just that trace and skip the multi-select UI noise.
        if self._single_trace:
            self.traces = [t for t in self.traces if t.name == self._single_trace]

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
        """Clear the filter stack on every selected trace.  Main_window
        handles the actual removal and re-evaluation."""
        names = list(self._selected_names())
        if names:
            self.clear_requested.emit(names)
            self.accept()

    def _build_recipe(self) -> "FilterRecipe | None":
        """Read the UI and return a FilterRecipe, or None on invalid input
        (in which case a message box was already shown)."""
        ftype = _FTYPE[self.combo_type.currentIndex()]
        is_iir_simple = ftype in ("notch", "peak", "comb")

        fc1 = _parse_si_freq(self.edit_fc1.text())
        fc2 = _parse_si_freq(self.edit_fc2.text())

        if fc1 is None or fc1 <= 0:
            QMessageBox.warning(self, self.tr("Invalid Input"),
                self.tr("Please enter a valid cutoff frequency."))
            return None
        if ftype in ("bandpass", "bandstop") and (fc2 is None or fc2 <= fc1):
            QMessageBox.warning(self, self.tr("Invalid Input"),
                self.tr("High cutoff must be greater than low cutoff."))
            return None

        if is_iir_simple:
            # notch / peak / comb: center freq + Q only
            params = {"center_hz": fc1, "q": float(self.spin_q.value())}
        elif ftype in ("bandpass", "bandstop"):
            params = {
                "low_hz":  fc1,
                "high_hz": fc2,
                "order":   int(self.spin_order.value()),
                "family":  _FAMILY[self.combo_family.currentIndex()],
            }
        else:   # lowpass, highpass
            params = {
                "cutoff_hz": fc1,
                "order":     int(self.spin_order.value()),
                "family":    _FAMILY[self.combo_family.currentIndex()],
            }
        recipe = FilterRecipe(filter_type=ftype, params=params)
        recipe.ensure_description()
        return recipe

    def _apply(self):
        names = list(self._selected_names())
        if not names:
            QMessageBox.warning(self, self.tr("No Traces Selected"),
                self.tr("Select one or more traces to filter."))
            return

        recipe = self._build_recipe()
        if recipe is None:
            return

        self.filter_recipe_added.emit(recipe, names)
        self.accept()
