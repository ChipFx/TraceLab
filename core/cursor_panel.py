"""
core/cursor_panel.py
Shows cursor time positions and delta measurements.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QGridLayout, QSizePolicy, QScrollArea, QTableWidget,
    QTableWidgetItem, QHeaderView, QFileDialog
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from typing import Dict, List, Optional


def _fmt_time(t: float) -> str:
    """Format a time value with appropriate units."""
    if t is None:
        return "---"
    a = abs(t)
    if a == 0:
        return "0 s"
    if a < 1e-9:
        return f"{t*1e12:.4g} ps"
    if a < 1e-6:
        return f"{t*1e9:.4g} ns"
    if a < 1e-3:
        return f"{t*1e6:.4g} µs"
    if a < 1:
        return f"{t*1e3:.4g} ms"
    return f"{t:.6g} s"


def _fmt_val(v: float) -> str:
    if v is None:
        return "---"
    return f"{v:.6g}"


class CursorPanel(QWidget):
    """Cursor readout panel."""

    place_cursor = pyqtSignal(int)  # emits cursor_id to request placement mode

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(200)
        self._cursor_times = {0: None, 1: None}
        self._trace_values: Dict[int, Dict] = {}  # cursor_id -> {name: value}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # Cursor A
        grp_a = QGroupBox("Cursor A")
        grp_a.setStyleSheet("QGroupBox::title { color: #ffcc00; }")
        ga = QGridLayout(grp_a)
        self.lbl_a_time = QLabel("---")
        self.lbl_a_time.setFont(QFont("Courier New", 10))
        ga.addWidget(QLabel("Time:"), 0, 0)
        ga.addWidget(self.lbl_a_time, 0, 1)
        btn_place_a = QPushButton("Place A")
        btn_place_a.clicked.connect(lambda: self.place_cursor.emit(0))
        ga.addWidget(btn_place_a, 1, 0, 1, 2)
        layout.addWidget(grp_a)

        # Cursor B
        grp_b = QGroupBox("Cursor B")
        grp_b.setStyleSheet("QGroupBox::title { color: #00ccff; }")
        gb = QGridLayout(grp_b)
        self.lbl_b_time = QLabel("---")
        self.lbl_b_time.setFont(QFont("Courier New", 10))
        gb.addWidget(QLabel("Time:"), 0, 0)
        gb.addWidget(self.lbl_b_time, 0, 1)
        btn_place_b = QPushButton("Place B")
        btn_place_b.clicked.connect(lambda: self.place_cursor.emit(1))
        gb.addWidget(btn_place_b, 1, 0, 1, 2)
        layout.addWidget(grp_b)

        # Delta
        grp_d = QGroupBox("ΔT  (B − A)")
        gd = QGridLayout(grp_d)
        self.lbl_dt = QLabel("---")
        self.lbl_dt.setFont(QFont("Courier New", 10))
        self.lbl_freq = QLabel("---")
        self.lbl_freq.setFont(QFont("Courier New", 9))
        gd.addWidget(QLabel("Δt:"), 0, 0)
        gd.addWidget(self.lbl_dt, 0, 1)
        gd.addWidget(QLabel("1/Δt:"), 1, 0)
        gd.addWidget(self.lbl_freq, 1, 1)
        layout.addWidget(grp_d)

        # Per-trace values
        grp_vals = QGroupBox("Values at Cursors")
        gv = QVBoxLayout(grp_vals)

        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Trace", "@ A", "@ B"])
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setMaximumHeight(200)
        gv.addWidget(self.table)

        btn_export = QPushButton("Export CSV")
        btn_export.clicked.connect(self._export_csv)
        gv.addWidget(btn_export)
        layout.addWidget(grp_vals)

        layout.addStretch()

    def update_cursors(self, cursor_data: dict):
        """Called when cursor positions/values change."""
        for cid, data in cursor_data.items():
            t = data.get("time")
            self._cursor_times[cid] = t
            vals = {k: v for k, v in data.items() if k != "time"}
            self._trace_values[cid] = vals

        # Update time labels
        t_a = self._cursor_times.get(0)
        t_b = self._cursor_times.get(1)
        self.lbl_a_time.setText(_fmt_time(t_a))
        self.lbl_b_time.setText(_fmt_time(t_b))

        if t_a is not None and t_b is not None:
            dt = t_b - t_a
            self.lbl_dt.setText(_fmt_time(dt))
            if dt != 0:
                freq = 1.0 / abs(dt)
                if freq >= 1e9:
                    self.lbl_freq.setText(f"{freq/1e9:.4g} GHz")
                elif freq >= 1e6:
                    self.lbl_freq.setText(f"{freq/1e6:.4g} MHz")
                elif freq >= 1e3:
                    self.lbl_freq.setText(f"{freq/1e3:.4g} kHz")
                else:
                    self.lbl_freq.setText(f"{freq:.4g} Hz")
            else:
                self.lbl_freq.setText("∞")
        else:
            self.lbl_dt.setText("---")
            self.lbl_freq.setText("---")

        # Update per-trace table
        vals_a = self._trace_values.get(0, {})
        vals_b = self._trace_values.get(1, {})
        trace_names = sorted(set(vals_a.keys()) | set(vals_b.keys()))

        self.table.setRowCount(len(trace_names))
        for i, name in enumerate(trace_names):
            self.table.setItem(i, 0, QTableWidgetItem(name))
            va = vals_a.get(name)
            vb = vals_b.get(name)
            self.table.setItem(i, 1, QTableWidgetItem(
                _fmt_val(va) if va is not None else "---"))
            self.table.setItem(i, 2, QTableWidgetItem(
                _fmt_val(vb) if vb is not None else "---"))

    def _export_csv(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Measurements", "", "CSV Files (*.csv)")
        if not path:
            return
        vals_a = self._trace_values.get(0, {})
        vals_b = self._trace_values.get(1, {})
        t_a = self._cursor_times.get(0)
        t_b = self._cursor_times.get(1)
        lines = ["Trace,Time_A,Value_A,Time_B,Value_B"]
        trace_names = sorted(set(vals_a.keys()) | set(vals_b.keys()))
        for name in trace_names:
            va = vals_a.get(name, "")
            vb = vals_b.get(name, "")
            lines.append(f"{name},{t_a or ''},{va},{t_b or ''},{vb}")
        with open(path, "w") as f:
            f.write("\n".join(lines))
