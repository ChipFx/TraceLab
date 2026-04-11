"""
core/trigger_panel.py
Trigger panel — find and mark trigger events on traces.

Supports:
  - Rising edge at threshold X on channel N
  - Falling edge at threshold X on channel N  
  - Either edge (rising or falling) at threshold X on channel N

When triggered:
  - Sets t=0 to the first trigger point found
  - Places Cursor A at the trigger point
  - Optionally zooms the view to show context around the trigger
  - Emits trigger_found(time_position) for the main window to act on
"""

import numpy as np
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QComboBox, QPushButton, QGroupBox, QCheckBox, QFrame,
    QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from typing import List, Optional
from core.trace_model import TraceModel
from core.import_dialog import SciLineEdit


class TriggerPanel(QWidget):
    """
    Right-panel trigger control widget.
    Emits trigger_found(t_pos) when a trigger is located.
    Emits set_time_zero(t_pos) to request a time-zero shift.
    """

    trigger_found   = pyqtSignal(float)   # time of trigger crossing
    set_time_zero   = pyqtSignal(float)   # request t=0 shift to this time
    place_cursor    = pyqtSignal(int, float)  # cursor_id, time

    def __init__(self, parent=None):
        super().__init__(parent)
        self._traces: List[TraceModel] = []
        self._last_trigger_t: Optional[float] = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # Header
        hdr = QLabel("TRIGGER")
        hdr.setStyleSheet(
            "color: #888; font-size: 10px; font-weight: bold; "
            "letter-spacing: 1px;")
        layout.addWidget(hdr)

        grp = QGroupBox("Trigger Settings")
        gl = QGridLayout(grp)
        gl.setSpacing(6)

        # Channel selector
        gl.addWidget(QLabel("Channel:"), 0, 0)
        self.combo_ch = QComboBox()
        self.combo_ch.setToolTip("Trace to trigger on")
        gl.addWidget(self.combo_ch, 0, 1)

        # Edge type
        gl.addWidget(QLabel("Edge:"), 1, 0)
        self.combo_edge = QComboBox()
        self.combo_edge.addItems(["Rising ↑", "Falling ↓", "Either ↕"])
        gl.addWidget(self.combo_edge, 1, 1)

        # Threshold
        gl.addWidget(QLabel("Level:"), 2, 0)
        self.edit_level = SciLineEdit("0")
        self.edit_level.setToolTip(
            "Threshold value (in trace units after scaling).\n"
            "Fractions and metric suffixes supported: 1.5, 500m")
        gl.addWidget(self.edit_level, 2, 1)

        # Search start
        gl.addWidget(QLabel("Search from:"), 3, 0)
        # "Find Trigger" always starts from the beginning of data.
        # "Next ->" continues from after the last trigger.
        # No combo needed — kept as label for clarity.
        lbl_search = QLabel("Find: always from start\nNext →: from last")
        lbl_search.setStyleSheet("color: #888; font-size: 9px;")
        gl.addWidget(lbl_search, 3, 1)

        layout.addWidget(grp)

        # Options
        opt_grp = QGroupBox("On Trigger")
        ol = QVBoxLayout(opt_grp)
        self.chk_cursor_a = QCheckBox("Place Cursor A at trigger")
        self.chk_cursor_a.setChecked(True)
        self.chk_set_t0   = QCheckBox("Set t=0 to trigger point")
        self.chk_set_t0.setChecked(False)
        self.chk_set_t0.setToolTip(
            "Shifts the time axis so the trigger point becomes t=0.\n"
            "All earlier samples appear with negative time.")
        self.chk_zoom     = QCheckBox("Zoom to show trigger context")
        self.chk_zoom.setChecked(True)
        ol.addWidget(self.chk_cursor_a)
        ol.addWidget(self.chk_set_t0)
        ol.addWidget(self.chk_zoom)
        layout.addWidget(opt_grp)

        # Trigger button + status
        self.btn_trigger = QPushButton("Find Trigger")
        self.btn_trigger.setStyleSheet(
            "background: #1a4a1a; color: #60e060; font-weight: bold; "
            "padding: 6px; border: 1px solid #40a040; border-radius: 3px;")
        self.btn_trigger.clicked.connect(self._find_trigger)
        layout.addWidget(self.btn_trigger)

        self.btn_next = QPushButton("Next →")
        self.btn_next.setEnabled(False)
        self.btn_next.setToolTip("Find next trigger after the last one")
        self.btn_next.clicked.connect(self._find_next)
        layout.addWidget(self.btn_next)

        self.lbl_status = QLabel("No trigger set")
        self.lbl_status.setStyleSheet(
            "color: #888; font-size: 9px; padding: 2px;")
        self.lbl_status.setWordWrap(True)
        layout.addWidget(self.lbl_status)

        layout.addStretch()

    def update_traces(self, traces: List[TraceModel]):
        """Refresh the channel selector when traces change."""
        current = self.combo_ch.currentText()
        self.combo_ch.clear()
        for t in traces:
            if t.visible:
                self.combo_ch.addItem(t.label, t.name)
        # Try to restore selection
        idx = self.combo_ch.findText(current)
        if idx >= 0:
            self.combo_ch.setCurrentIndex(idx)
        self._traces = traces

    def _get_selected_trace(self) -> Optional[TraceModel]:
        name = self.combo_ch.currentData()
        for t in self._traces:
            if t.name == name:
                return t
        return None

    def _find_trigger(self, search_after: Optional[float] = None):
        trace = self._get_selected_trace()
        if trace is None:
            self.lbl_status.setText("No channel selected.")
            return

        level = self.edit_level.get_value(0.0)
        edge_idx = self.combo_edge.currentIndex()  # 0=rise, 1=fall, 2=either

        t = trace.time_axis
        y = trace.processed_data

        # "Find Trigger" always starts from the beginning of data (i_start=0).
        # "Next ->" (search_after set) starts from just after the last trigger.
        if search_after is not None:
            i_start = int(np.searchsorted(t, search_after)) + 1
        else:
            i_start = 0

        i_start = max(0, min(i_start, len(t) - 2))

        # Find crossing
        t_pos = self._find_crossing(t, y, level, edge_idx, i_start)

        if t_pos is None:
            self.lbl_status.setText(
                f"No {'rising' if edge_idx==0 else 'falling' if edge_idx==1 else ''} "
                f"crossing at {level:.4g} found after t={t[i_start]:.6g}")
            self.btn_next.setEnabled(False)
            return

        self._last_trigger_t = t_pos
        self.lbl_status.setText(
            f"Trigger at t = {_fmt_time(t_pos)}\n"
            f"Level = {level:.4g}")
        self.btn_next.setEnabled(True)
        self.trigger_found.emit(t_pos)

        if self.chk_cursor_a.isChecked():
            self.place_cursor.emit(0, t_pos)

        if self.chk_set_t0.isChecked():
            self.set_time_zero.emit(t_pos)

    def _find_next(self):
        if self._last_trigger_t is not None:
            self._find_trigger(search_after=self._last_trigger_t)

    @staticmethod
    def _find_crossing(t: np.ndarray, y: np.ndarray,
                        level: float, edge_idx: int,
                        i_start: int) -> Optional[float]:
        """
        Find first threshold crossing with linear interpolation.

        Rising  edge: previous sample STRICTLY below level,
                      current sample STRICTLY above (or equal) level.
        Falling edge: previous sample STRICTLY above level,
                      current sample STRICTLY below (or equal) level.

        This means a flat run AT the level is not itself a crossing —
        only an actual transition through the level counts.
        """
        if len(t) < 2:
            return None

        for i in range(i_start, len(y) - 1):
            a, b = float(y[i]), float(y[i + 1])
            is_rising  = (a < level) and (b >= level)
            is_falling = (a > level) and (b <= level)

            if edge_idx == 0 and not is_rising:
                continue
            if edge_idx == 1 and not is_falling:
                continue
            if edge_idx == 2 and not (is_rising or is_falling):
                continue

            # Linear interpolation for sub-sample accuracy
            denom = b - a
            if denom == 0:
                frac = 0.0
            else:
                frac = (level - a) / denom

            t0, t1 = float(t[i]), float(t[i + 1])
            return t0 + frac * (t1 - t0)

        return None


def _fmt_time(t: float) -> str:
    a = abs(t)
    if a == 0:   return "0 s"
    if a < 1e-9: return f"{t*1e12:.4g} ps"
    if a < 1e-6: return f"{t*1e9:.4g} ns"
    if a < 1e-3: return f"{t*1e6:.4g} µs"
    if a < 1:    return f"{t*1e3:.4g} ms"
    return f"{t:.6g} s"
