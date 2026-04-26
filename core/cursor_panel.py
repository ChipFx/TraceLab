"""
core/cursor_panel.py
Shows cursor time positions and delta measurements.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QGridLayout, QTableWidget,
    QTableWidgetItem, QHeaderView, QFileDialog
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from typing import Dict, List, Optional


def _fmt_time(t: float) -> str:
    """Format a time value with SI time prefixes (standard mode)."""
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


def _fmt_smart_time(t: float, smart_settings: dict) -> str:
    """Format a time value the same way the Smart Scale axis would label it.
    Each value is evaluated independently so it never jumps based on zoom state.
    """
    if t is None:
        return "---"
    ss = smart_settings or {}
    max_s = float(ss.get("max_seconds", 300))
    max_m = float(ss.get("max_minutes", 120))
    max_h = float(ss.get("max_hours", 24))
    a = abs(t)
    if a < max_s:
        return _fmt_time(t)   # SI range
    max_m_thr = max_m * 60.0
    max_h_thr = max_h * 3600.0
    sign = "\u2212" if t < 0 else ""
    show_ms = (a % 1) != 0
    ms_str = f".{int(round((a % 1.0) * 1000)):03d}" if show_ms else ".0"
    secs  = int(a) % 60
    mins  = int(a) // 60 % 60
    hours = int(a) // 3600 % 24
    days  = int(a) // 86400
    if a < max_m_thr:
        total_mins = int(a) // 60
        return f"{sign}{total_mins}:{secs:02d}{ms_str}"
    elif a < max_h_thr:
        return f"{sign}{hours}:{mins:02d}:{secs:02d}{ms_str}"
    else:
        return f"{sign}{days}d {hours:02d}:{mins:02d}:{secs:02d}"


def _fmt_real_time_cursor(t: float, t0_dt) -> str:
    """Format a cursor time as an absolute wall-clock datetime (ms precision)."""
    if t is None or t0_dt is None:
        return "---"
    from datetime import timedelta
    dt = t0_dt + timedelta(seconds=float(t))
    ms = dt.microsecond // 1000
    return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{ms:03d}"


def _fmt_freq_si(freq: float) -> str:
    """Format a frequency with SI prefixes across the full range (nHz … GHz)."""
    if freq <= 0:
        return "---"
    a = abs(freq)
    if a >= 1e9:
        return f"{freq/1e9:.4g} GHz"
    if a >= 1e6:
        return f"{freq/1e6:.4g} MHz"
    if a >= 1e3:
        return f"{freq/1e3:.4g} kHz"
    if a >= 1.0:
        return f"{freq:.4g} Hz"
    if a >= 1e-3:
        return f"{freq*1e3:.4g} mHz"
    if a >= 1e-6:
        return f"{freq*1e6:.4g} \u00b5Hz"
    if a >= 1e-9:
        return f"{freq*1e9:.4g} nHz"
    return f"{freq:.4g} Hz"


def _fmt_val(v: float, unit: str = "", spacing: float = None) -> str:
    """Format a measurement value with SI prefix if a unit is known.

    When ``spacing`` is supplied (the current Y-axis tick interval in the same
    units as v), the number of decimal places is computed so the displayed value
    is never rounded coarser than the tick grid.  Without it a short heuristic
    is used, which can round 22.461 to "22.5".
    """
    if v is None:
        return "---"
    try:
        import math
        if math.isnan(v) or math.isinf(v):
            return "---"
    except (TypeError, ValueError):
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
    import math as _math
    abs_v = abs(v)
    for scale, prefix in [(1e12,'T'),(1e9,'G'),(1e6,'M'),(1e3,'k'),
                           (1,''),(1e-3,'m'),(1e-6,'µ'),(1e-9,'n'),(1e-12,'p')]:
        if abs_v >= scale * 0.9999:
            s = v / scale
            if spacing is not None and spacing > 0:
                # Display at 1/10th of a tick division so the readout toggles
                # with fine cursor movement, not only at full-div steps.
                scaled_sp = abs(spacing / scale) / 10.0
                dp = max(0, -int(_math.floor(_math.log10(scaled_sp)))) if scaled_sp < 1 else 0
                dp = min(dp, 9)
                txt = f"{s:.{dp}f}"
            elif abs(s) >= 100:
                txt = f"{s:.0f}"
            elif abs(s) >= 10:
                txt = f"{s:.1f}".rstrip('0').rstrip('.')
            else:
                txt = f"{s:.3f}".rstrip('0').rstrip('.')
            return f"{txt} {prefix}{unit}"
    return f"{v:.4e} {unit}"


class CursorPanel(QWidget):
    """Cursor readout panel."""

    place_cursor   = pyqtSignal(int)   # emits cursor_id
    set_t0_at_a    = pyqtSignal()      # request: set time-zero at cursor A
    jump_to_t0     = pyqtSignal()      # request: center t=0 in current viewport
    remove_cursors = pyqtSignal()      # request: clear both cursors from plot

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(160)
        self._cursor_times = {0: None, 1: None}
        self._trace_values: Dict[int, Dict] = {}  # cursor_id -> {name: value}
        self._trace_display_order: List[str] = []  # set by main window
        self._trace_units: Dict[str, str] = {}     # name -> unit string
        self._y_spacings: Dict[str, float] = {}    # name -> current Y tick spacing
        # Time-scale mode: "standard" | "smart" | "real_time"
        self._time_scale_mode: str = "standard"
        self._smart_settings: dict = {}
        self._t0_wall_clock_dt = None              # datetime | None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ── Row 1: placement + remove ──────────────────────────────────
        row1 = QHBoxLayout()
        row1.setSpacing(3)
        btn_place_a = QPushButton("Place A")
        btn_place_a.clicked.connect(lambda: self.place_cursor.emit(0))
        row1.addWidget(btn_place_a)
        btn_place_b = QPushButton("Place B")
        btn_place_b.clicked.connect(lambda: self.place_cursor.emit(1))
        row1.addWidget(btn_place_b)
        btn_remove = QPushButton("Remove")
        btn_remove.setToolTip("Remove both cursors from the plot")
        btn_remove.clicked.connect(self.remove_cursors)
        row1.addWidget(btn_remove)
        layout.addLayout(row1)

        # ── Row 2: t=0 controls ────────────────────────────────────────
        row2 = QHBoxLayout()
        row2.setSpacing(3)
        btn_t0 = QPushButton("Set t=0 @ A")
        btn_t0.setToolTip(
            "Shift all traces so Cursor A position becomes t=0.\n"
            "Points before it get negative time.")
        btn_t0.clicked.connect(self.set_t0_at_a)
        row2.addWidget(btn_t0)
        btn_jump_t0 = QPushButton("Jump to t=0")
        btn_jump_t0.setToolTip(
            "Keep the current zoom span and move t=0 to the middle of the viewport.")
        btn_jump_t0.clicked.connect(self.jump_to_t0)
        row2.addWidget(btn_jump_t0)
        layout.addLayout(row2)

        # ── Separator ──────────────────────────────────────────────────
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setStyleSheet("color: #444;")
        layout.addWidget(sep1)

        # ── Time + delta readout (2×2: A|B on row 0, Δt|1/Δt on row 1) ──
        grid = QGridLayout()
        grid.setSpacing(2)
        grid.setContentsMargins(2, 2, 2, 2)

        lbl_a_key = QLabel("A:")
        lbl_a_key.setStyleSheet("color: #ffcc00; font-weight: bold;")
        grid.addWidget(lbl_a_key, 0, 0)
        self.lbl_a_time = QLabel("---")
        self.lbl_a_time.setFont(QFont("Courier New", 9))
        grid.addWidget(self.lbl_a_time, 0, 1)

        lbl_b_key = QLabel("B:")
        lbl_b_key.setStyleSheet("color: #00ccff; font-weight: bold;")
        grid.addWidget(lbl_b_key, 0, 2)
        self.lbl_b_time = QLabel("---")
        self.lbl_b_time.setFont(QFont("Courier New", 9))
        grid.addWidget(self.lbl_b_time, 0, 3)

        grid.addWidget(QLabel("Δt:"), 1, 0)
        self.lbl_dt = QLabel("---")
        self.lbl_dt.setFont(QFont("Courier New", 9))
        grid.addWidget(self.lbl_dt, 1, 1)

        grid.addWidget(QLabel("1/Δt:"), 1, 2)
        self.lbl_freq = QLabel("---")
        self.lbl_freq.setFont(QFont("Courier New", 9))
        grid.addWidget(self.lbl_freq, 1, 3)

        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        layout.addLayout(grid)

        # ── Separator ──────────────────────────────────────────────────
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("color: #444;")
        layout.addWidget(sep2)

        # ── Values table ───────────────────────────────────────────────
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Trace", "@ A", "@ B"])
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, stretch=1)

        btn_export = QPushButton("Export CSV")
        btn_export.clicked.connect(self._export_csv)
        layout.addWidget(btn_export)

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
        self.lbl_a_time.setText(self._fmt_cursor_time(t_a))
        self.lbl_b_time.setText(self._fmt_cursor_time(t_b))

        if t_a is not None and t_b is not None:
            dt = t_b - t_a
            # Δt: use smart/standard time format (always relative, even in real_time mode)
            if self._time_scale_mode == "smart":
                self.lbl_dt.setText(_fmt_smart_time(dt, self._smart_settings))
            else:
                self.lbl_dt.setText(_fmt_time(dt))
            if dt != 0:
                self.lbl_freq.setText(_fmt_freq_si(1.0 / abs(dt)))
            else:
                self.lbl_freq.setText("∞")
        else:
            self.lbl_dt.setText("---")
            self.lbl_freq.setText("---")

        # Update per-trace table
        self._rebuild_table()

    def _rebuild_table(self):
        """Re-render the trace value table from current internal state."""
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
            spacing = self._y_spacings.get(name)
            self.table.setItem(i, 0, QTableWidgetItem(name))
            va = vals_a.get(name)
            vb = vals_b.get(name)
            self.table.setItem(i, 1, QTableWidgetItem(
                _fmt_val(va, unit, spacing) if va is not None else "---"))
            self.table.setItem(i, 2, QTableWidgetItem(
                _fmt_val(vb, unit, spacing) if vb is not None else "---"))

    def clear_readout(self):
        """Clear all cursor time/value readouts (called when cursors are removed)."""
        self._cursor_times = {0: None, 1: None}
        self._trace_values = {}
        self.lbl_a_time.setText("---")
        self.lbl_b_time.setText("---")
        self.lbl_dt.setText("---")
        self.lbl_freq.setText("---")
        self.table.setRowCount(0)

    def set_trace_order(self, names: List[str]):
        """Update the display order for cursor value table and re-render immediately."""
        self._trace_display_order = list(names)
        self._rebuild_table()

    def set_trace_units(self, unit_map: Dict[str, str]):
        """Update unit strings per trace name for smart formatting."""
        self._trace_units = dict(unit_map)

    def set_y_spacings(self, spacings: Dict[str, float]):
        """Update per-trace Y tick spacing so cursor values display at matching precision."""
        self._y_spacings = dict(spacings)

    def set_time_scale_mode(self, mode: str, smart_settings: dict, t0_dt=None):
        """Notify the cursor panel of the active time-scale mode.

        mode: "standard" | "smart" | "real_time"
        smart_settings: the smart_scale settings dict (thresholds)
        t0_dt: datetime object for t=0 wall-clock anchor (real_time mode only)
        """
        self._time_scale_mode = mode
        self._smart_settings = dict(smart_settings) if smart_settings else {}
        self._t0_wall_clock_dt = t0_dt
        # Refresh displayed values if cursors are placed
        if any(t is not None for t in self._cursor_times.values()):
            t_a = self._cursor_times.get(0)
            t_b = self._cursor_times.get(1)
            self.lbl_a_time.setText(self._fmt_cursor_time(t_a))
            self.lbl_b_time.setText(self._fmt_cursor_time(t_b))
            if t_a is not None and t_b is not None:
                dt = t_b - t_a
                if self._time_scale_mode == "smart":
                    self.lbl_dt.setText(_fmt_smart_time(dt, self._smart_settings))
                else:
                    self.lbl_dt.setText(_fmt_time(dt))
                if dt != 0:
                    self.lbl_freq.setText(_fmt_freq_si(1.0 / abs(dt)))

    def _fmt_cursor_time(self, t: Optional[float]) -> str:
        """Format a cursor time position according to the active time-scale mode."""
        if t is None:
            return "---"
        if self._time_scale_mode == "smart":
            return _fmt_smart_time(t, self._smart_settings)
        if self._time_scale_mode == "real_time":
            return _fmt_real_time_cursor(t, self._t0_wall_clock_dt)
        return _fmt_time(t)

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
