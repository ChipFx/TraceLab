"""
core/filter_dialog.py
Signal filtering: lowpass, highpass, bandpass using scipy.
"""

import numpy as np
from scipy import signal as sp_signal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QDoubleSpinBox, QPushButton, QListWidget, QListWidgetItem,
    QGroupBox, QGridLayout, QMessageBox
)
from PyQt6.QtCore import Qt, pyqtSignal
from typing import List
from core.trace_model import TraceModel


class FilterDialog(QDialog):
    """Apply filters to selected traces."""
    filters_applied = pyqtSignal(list)  # list of modified TraceModel names

    def __init__(self, traces: List[TraceModel], parent=None):
        super().__init__(parent)
        self.traces = [t for t in traces if t.visible]
        self.setWindowTitle("Signal Filters")
        self.resize(500, 400)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Trace selector
        grp_trace = QGroupBox("Select Traces")
        tl = QVBoxLayout(grp_trace)
        self.trace_list = QListWidget()
        self.trace_list.setSelectionMode(
            QListWidget.SelectionMode.MultiSelection)
        for t in self.traces:
            item = QListWidgetItem(t.label)
            item.setData(Qt.ItemDataRole.UserRole, t.name)
            self.trace_list.addItem(item)
            item.setSelected(True)
        tl.addWidget(self.trace_list)
        layout.addWidget(grp_trace)

        # Filter config
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

        # Buttons
        btn_layout = QHBoxLayout()
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

    def _apply(self):
        selected_names = set()
        for item in self.trace_list.selectedItems():
            selected_names.add(item.data(Qt.ItemDataRole.UserRole))

        ftype = self.combo_type.currentText().lower()
        order = int(self.spin_order.value())
        fc1 = self.spin_fc1.value()
        fc2 = self.spin_fc2.value()

        modified = []
        for trace in self.traces:
            if trace.name not in selected_names:
                continue

            sps = trace.sample_rate
            nyq = sps / 2.0

            try:
                if ftype == "lowpass":
                    wn = fc1 / nyq
                    b, a = sp_signal.butter(order, wn, btype="low")
                elif ftype == "highpass":
                    wn = fc1 / nyq
                    b, a = sp_signal.butter(order, wn, btype="high")
                else:  # bandpass
                    wn = [fc1 / nyq, fc2 / nyq]
                    b, a = sp_signal.butter(order, wn, btype="band")

                # Apply filter to processed data
                filtered = sp_signal.filtfilt(b, a, trace.processed_data)
                # Store as a new "raw" data with scaling disabled
                trace.raw_data = filtered
                trace.scaling.enabled = False
                trace._invalidate_cache()
                modified.append(trace.name)

            except Exception as e:
                QMessageBox.warning(self, "Filter Error",
                    f"Failed to filter {trace.label}: {e}")

        if modified:
            self.filters_applied.emit(modified)
            self.accept()
