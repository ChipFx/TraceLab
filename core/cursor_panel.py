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


def _fmt_val(v: float, unit: str = "") -> str:
    """Format a measurement value with SI prefix if a unit is known."""
    if v is None:
        return "---"
    if not unit or unit == "raw":
        # No unit — use plain engineering notation
        a = abs(v)
        if a == 0:
            return "0"
        if a < 1e-9:
            return f"{v*1e12:.4g} p"
        if a < 1e-6:
            return f"{v*1e9:.4g} n"
        if a < 1e-3:
            return f"{v*1e6:.4g} µ"
        if a >= 1e6:
            return f"{v/1e6:.4g} M"
        if a >= 1e3:
            return f"{v/1e3:.4g} k"
        return f"{v:.5g}"
    # With unit — full SI prefix
    abs_v = abs(v)
    for scale, prefix in [(1e12,'T'),(1e9,'G'),(1e6,'M'),(1e3,'k'),
                           (1,''),(1e-3,'m'),(1e-6,'µ'),(1e-9,'n'),(1e-12,'p')]:
        if abs_v >= scale * 0.9999:
            s = v / scale
            if abs(s) >= 100:   txt = f"{s:.0f}"
            elif abs(s) >= 10:  txt = f"{s:.1f}".rstrip('0').rstrip('.')
            else:               txt = f"{s:.3f}".rstrip('0').rstrip('.')
            return f"{txt} {prefix}{unit}"
    return f"{v:.4e} {unit}"


class CursorPanel(QWidget):
    """Cursor readout panel."""

    place_cursor   = pyqtSignal(int)   # emits cursor_id
    set_t0_at_a    = pyqtSignal()      # request: set time-zero at cursor A
    jump_to_t0     = pyqtSignal()      # request: center t=0 in current viewport

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(200)
        self._cursor_times = {0: None, 1: None}
        self._trace_values: Dict[int, Dict] = {}  # cursor_id -> {name: value}
        self._trace_display_order: List[str] = []  # set by main window
        self._trace_units: Dict[str, str] = {}     # name -> unit string

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
        ga.addWidget(btn_place_a, 1, 0)
        btn_t0 = QPushButton("Set t=0 here")
        btn_t0.setToolTip(
            "Shift all traces so Cursor A position becomes t=0.\n"
            "Points before it get negative time.")
        btn_t0.setStyleSheet("font-size: 9px;")
        btn_t0.clicked.connect(self.set_t0_at_a)
        ga.addWidget(btn_t0, 1, 1)
        btn_jump_t0 = QPushButton("Jump to t=0")
        btn_jump_t0.setToolTip(
            "Keep the current zoom span and move t=0 to the middle of the viewport.")
        btn_jump_t0.setStyleSheet("font-size: 9px;")
        btn_jump_t0.clicked.connect(self.jump_to_t0)
        ga.addWidget(btn_jump_t0, 2, 0, 1, 2)
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
        all_names = set(vals_a.keys()) | set(vals_b.keys())
        # Use provided display order if available, else sort
        if self._trace_display_order:
            trace_names = [n for n in self._trace_display_order if n in all_names]
            trace_names += sorted(all_names - set(trace_names))
        else:
            trace_names = sorted(all_names)

        self.table.setRowCount(len(trace_names))
        for i, name in enumerate(trace_names):
            unit = self._trace_units.get(name, "")
            self.table.setItem(i, 0, QTableWidgetItem(name))
            va = vals_a.get(name)
            vb = vals_b.get(name)
            self.table.setItem(i, 1, QTableWidgetItem(
                _fmt_val(va, unit) if va is not None else "---"))
            self.table.setItem(i, 2, QTableWidgetItem(
                _fmt_val(vb, unit) if vb is not None else "---"))

    def set_trace_order(self, names: List[str]):
        """Update the display order for cursor value table."""
        self._trace_display_order = list(names)

    def set_trace_units(self, unit_map: Dict[str, str]):
        """Update unit strings per trace name for smart formatting."""
        self._trace_units = dict(unit_map)

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
