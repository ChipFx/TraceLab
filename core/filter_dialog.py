"""
core/filter_dialog.py
Non-destructive filters: raw_data is never modified.
Filtered result is stored in trace._filter_data; clearing restores original.
"""

import numpy as np
from scipy import signal as sp_signal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QDoubleSpinBox, QPushButton, QListWidget, QListWidgetItem,
    QGroupBox, QGridLayout, QMessageBox, QCheckBox
)
from PyQt6.QtCore import Qt, pyqtSignal
from typing import List
from core.trace_model import TraceModel


class FilterDialog(QDialog):
    filters_applied = pyqtSignal(list)

    def __init__(self, traces: List[TraceModel], parent=None):
        super().__init__(parent)
        self.traces = [t for t in traces if t.visible]
        self.setWindowTitle("Signal Filters")
        self.resize(520, 420)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Filter status overview
        active = [t for t in self.traces if t.has_filter]
        if active:
            info = QLabel(
                f"Active filters: {', '.join(t.label + ' (' + t.filter_description + ')' for t in active)}")
            info.setStyleSheet(
                "color: #80e0a0; padding: 4px; background: #102010; border-radius:3px;")
            info.setWordWrap(True)
            layout.addWidget(info)

        grp_trace = QGroupBox("Select Traces")
        tl = QVBoxLayout(grp_trace)
        self.trace_list = QListWidget()
        self.trace_list.setSelectionMode(
            QListWidget.SelectionMode.MultiSelection)
        for t in self.traces:
            label = t.label
            if t.has_filter:
                label += f"  [filtered: {t.filter_description}]"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, t.name)
            self.trace_list.addItem(item)
            item.setSelected(True)
        tl.addWidget(self.trace_list)
        layout.addWidget(grp_trace)

        grp_filt = QGroupBox("Filter Settings")
        fl = QGridLayout(grp_filt)

        fl.addWidget(QLabel("Type:"), 0, 0)
        self.combo_type = QComboBox()
        self.combo_type.addItems(["Lowpass", "Highpass", "Bandpass"])
        self.combo_type.currentTextChanged.connect(self._update_ui)
        fl.addWidget(self.combo_type, 0, 1)

        fl.addWidget(QLabel("Order:"), 1, 0)
        self.spin_order = QDoubleSpinBox()
        self.spin_order.setRange(1, 10)
        self.spin_order.setDecimals(0)
        self.spin_order.setValue(4)
        fl.addWidget(self.spin_order, 1, 1)

        self.lbl_fc1 = QLabel("Cutoff freq (Hz):")
        self.spin_fc1 = QDoubleSpinBox()
        self.spin_fc1.setRange(0.001, 1e12)
        self.spin_fc1.setDecimals(3)
        self.spin_fc1.setValue(1000.0)
        fl.addWidget(self.lbl_fc1, 2, 0)
        fl.addWidget(self.spin_fc1, 2, 1)

        self.lbl_fc2 = QLabel("High cutoff (Hz):")
        self.spin_fc2 = QDoubleSpinBox()
        self.spin_fc2.setRange(0.001, 1e12)
        self.spin_fc2.setDecimals(3)
        self.spin_fc2.setValue(5000.0)
        fl.addWidget(self.lbl_fc2, 3, 0)
        fl.addWidget(self.spin_fc2, 3, 1)

        layout.addWidget(grp_filt)
        self._update_ui()

        btn_layout = QHBoxLayout()
        btn_clear = QPushButton("Clear Filters on Selected")
        btn_clear.clicked.connect(self._clear_filters)
        btn_layout.addWidget(btn_clear)
        btn_layout.addStretch()
        btn_apply = QPushButton("Apply Filter")
        btn_apply.setStyleSheet(
            "background: #2060c0; color: white; padding: 6px 16px;")
        btn_apply.clicked.connect(self._apply)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_apply)
        layout.addLayout(btn_layout)

    def _update_ui(self):
        is_band = self.combo_type.currentText() == "Bandpass"
        self.lbl_fc2.setVisible(is_band)
        self.spin_fc2.setVisible(is_band)

    def _selected_names(self):
        return {item.data(Qt.ItemDataRole.UserRole)
                for item in self.trace_list.selectedItems()}

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
        ftype = self.combo_type.currentText().lower()
        order = int(self.spin_order.value())
        fc1 = self.spin_fc1.value()
        fc2 = self.spin_fc2.value()

        modified = []
        for trace in self.traces:
            if trace.name not in names:
                continue
            sps = trace.sample_rate
            nyq = sps / 2.0
            try:
                if ftype == "lowpass":
                    wn = min(fc1 / nyq, 0.9999)
                    b, a = sp_signal.butter(order, wn, btype="low")
                    desc = f"LP {fc1:.4g}Hz"
                elif ftype == "highpass":
                    wn = min(fc1 / nyq, 0.9999)
                    b, a = sp_signal.butter(order, wn, btype="high")
                    desc = f"HP {fc1:.4g}Hz"
                else:
                    wn = [min(fc1/nyq, 0.499), min(fc2/nyq, 0.9999)]
                    b, a = sp_signal.butter(order, wn, btype="band")
                    desc = f"BP {fc1:.4g}-{fc2:.4g}Hz"

                # Filter the SCALED data (after gain/offset), non-destructively
                filtered = sp_signal.filtfilt(b, a, trace.processed_data)
                trace.set_filter(filtered, desc)
                modified.append(trace.name)

            except Exception as e:
                QMessageBox.warning(self, "Filter Error",
                    f"Failed to filter {trace.label}: {e}")

        if modified:
            self.filters_applied.emit(modified)
            self.accept()
