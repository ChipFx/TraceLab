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
    QSizePolicy, QRadioButton, QButtonGroup
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

    trigger_found              = pyqtSignal(float)   # time of trigger crossing
    set_time_zero              = pyqtSignal(float)   # request t=0 shift to this time
    place_cursor               = pyqtSignal(int, float)  # cursor_id, time
    retrigger_update_requested = pyqtSignal()        # manual "Update Retrigger" press

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
        gl.setSpacing(4)
        gl.setContentsMargins(6, 14, 6, 6)

        # Row 0: Channel (full width)
        gl.addWidget(QLabel("Channel:"), 0, 0)
        self.combo_ch = QComboBox()
        self.combo_ch.setToolTip("Trace to trigger on")
        gl.addWidget(self.combo_ch, 0, 1, 1, 3)

        # Row 1: Edge | Level on the same row
        gl.addWidget(QLabel("Edge:"), 1, 0)
        self.combo_edge = QComboBox()
        self.combo_edge.addItems(["Rising ↑", "Falling ↓", "Either ↕"])
        gl.addWidget(self.combo_edge, 1, 1)
        gl.addWidget(QLabel("Level:"), 1, 2)
        self.edit_level = SciLineEdit("0")
        self.edit_level.setToolTip(
            "Threshold value (in trace units after scaling).\n"
            "Fractions and metric suffixes supported: 1.5, 500m")
        gl.addWidget(self.edit_level, 1, 3)

        # Row 2: Direction × Origin — two independent radio pairs
        gl.addWidget(QLabel("Search:"), 2, 0)
        _radio_style = (
            "QRadioButton::indicator {"
            "  width: 11px; height: 11px; border-radius: 6px;"
            "  border: 2px solid #666; background: #222; }"
            "QRadioButton::indicator:checked {"
            "  background: #60e060; border-color: #40a040; }"
        )
        # Direction pair
        self.radio_dir_forward  = QRadioButton("Fwd")
        self.radio_dir_backward = QRadioButton("Bwd")
        self.radio_dir_forward.setChecked(True)
        self.radio_dir_forward.setToolTip("Search forward (→) from the origin point")
        self.radio_dir_backward.setToolTip("Search backward (←) from the origin point")
        self.radio_dir_forward.setStyleSheet(_radio_style)
        self.radio_dir_backward.setStyleSheet(_radio_style)
        _bg_dir = QButtonGroup(grp)
        _bg_dir.addButton(self.radio_dir_forward)
        _bg_dir.addButton(self.radio_dir_backward)
        self.radio_dir_forward.toggled.connect(self._on_search_direction_changed)

        # Origin pair
        self.radio_from_t0   = QRadioButton("t=0")
        self.radio_from_edge = QRadioButton("Edge")
        self.radio_from_t0.setChecked(True)
        self.radio_from_t0.setToolTip(
            "Start search from t=0\n"
            "Fwd→ searches forward from t=0; Bwd← searches backward from t=0")
        self.radio_from_edge.setToolTip(
            "Start search from the waveform edge\n"
            "Fwd→ searches from the start of data; Bwd← searches from the end of data")
        self.radio_from_t0.setStyleSheet(_radio_style)
        self.radio_from_edge.setStyleSheet(_radio_style)
        _bg_from = QButtonGroup(grp)
        _bg_from.addButton(self.radio_from_t0)
        _bg_from.addButton(self.radio_from_edge)

        search_row = QHBoxLayout()
        search_row.setSpacing(4)
        search_row.addWidget(self.radio_dir_forward)
        search_row.addWidget(self.radio_dir_backward)
        sep_s = QFrame()
        sep_s.setFrameShape(QFrame.Shape.VLine)
        sep_s.setStyleSheet("color: #444;")
        sep_s.setFixedWidth(1)
        search_row.addSpacing(4)
        search_row.addWidget(sep_s)
        search_row.addSpacing(4)
        search_row.addWidget(self.radio_from_t0)
        search_row.addWidget(self.radio_from_edge)
        search_row.addStretch()
        search_container = QWidget()
        search_container.setLayout(search_row)
        gl.addWidget(search_container, 2, 1, 1, 3)

        gl.setColumnStretch(1, 1)
        gl.setColumnStretch(3, 1)
        layout.addWidget(grp)

        # On Trigger options — 2×2 grid
        opt_grp = QGroupBox("On Trigger")
        og = QGridLayout(opt_grp)
        og.setSpacing(4)
        og.setContentsMargins(6, 14, 6, 6)
        self.chk_cursor_a = QCheckBox("Place Cur. A")
        self.chk_cursor_a.setChecked(True)
        self.chk_set_t0 = QCheckBox("Set t=0")
        self.chk_set_t0.setChecked(False)
        self.chk_set_t0.setToolTip(
            "Shifts the time axis so the trigger point becomes t=0.\n"
            "All earlier samples appear with negative time.")
        self.chk_zoom = QCheckBox("Zoom to trig")
        self.chk_zoom.setChecked(True)
        self.chk_auto_retrigger = QCheckBox("Auto-update")
        self.chk_auto_retrigger.setChecked(False)
        self.chk_auto_retrigger.setToolTip(
            "Automatically recalculate persistence / averaging / interpolation\n"
            "when you zoom or scroll.  Disable on large datasets to keep the\n"
            "app responsive.")
        og.addWidget(self.chk_cursor_a,    0, 0)
        og.addWidget(self.chk_set_t0,      0, 1)
        og.addWidget(self.chk_zoom,        1, 0)
        og.addWidget(self.chk_auto_retrigger, 1, 1)
        layout.addWidget(opt_grp)

        # Find Trigger + Next → side by side (60/40 split)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        self.btn_trigger = QPushButton("Find Trigger")
        self.btn_trigger.setStyleSheet(
            "background: #1a4a1a; color: #60e060; font-weight: bold; "
            "padding: 6px; border: 1px solid #40a040; border-radius: 3px;")
        self.btn_trigger.clicked.connect(lambda: self._find_trigger())
        btn_row.addWidget(self.btn_trigger, 3)

        self.btn_next = QPushButton("Next →")
        self.btn_next.setEnabled(False)
        self.btn_next.setToolTip("Find next trigger after the last one")
        self.btn_next.clicked.connect(self._find_next)
        btn_row.addWidget(self.btn_next, 2)
        layout.addLayout(btn_row)

        self.btn_retrigger_update = QPushButton("Update Retrigger")
        self.btn_retrigger_update.setEnabled(False)
        self.btn_retrigger_update.setToolTip(
            "Manually recalculate persistence / averaging / interpolation\n"
            "using the current view window and trigger settings.")
        self.btn_retrigger_update.setStyleSheet(
            "padding: 4px; border: 1px solid #555; border-radius: 3px;")
        self.btn_retrigger_update.clicked.connect(
            self.retrigger_update_requested)
        layout.addWidget(self.btn_retrigger_update)

        self.lbl_status = QLabel("No trigger set")
        self.lbl_status.setStyleSheet(
            "color: #888; font-size: 9px; padding: 2px;")
        self.lbl_status.setWordWrap(True)
        layout.addWidget(self.lbl_status)

        # ── Quick set-t=0 buttons ─────────────────────────────────────────
        t0_hdr = QLabel("SET  t=0")
        t0_hdr.setStyleSheet(
            "color: #888; font-size: 10px; font-weight: bold; "
            "letter-spacing: 1px; margin-top: 4px;")
        layout.addWidget(t0_hdr)

        _btn_style = (
            "QPushButton { padding: 3px 6px; border: 1px solid #555; "
            "border-radius: 3px; font-size: 11px; } "
            "QPushButton:hover { border-color: #888; } "
            "QPushButton:disabled { color: #444; border-color: #333; }")

        t0_row = QHBoxLayout()
        t0_row.setSpacing(3)

        self.btn_t0_first = QPushButton("⊣")
        self.btn_t0_first.setToolTip(
            "Set first dataset sample to t=0\n"
            "(full dataset, current zoom unchanged)")
        self.btn_t0_first.setStyleSheet(_btn_style)
        self.btn_t0_first.clicked.connect(self._set_t0_first)

        self.btn_t0_mid = QPushButton("⊙")
        self.btn_t0_mid.setToolTip(
            "Set dataset midpoint to t=0\n"
            "(full dataset, current zoom unchanged)")
        self.btn_t0_mid.setStyleSheet(_btn_style)
        self.btn_t0_mid.clicked.connect(self._set_t0_middle)

        self.btn_t0_last = QPushButton("⊢")
        self.btn_t0_last.setToolTip(
            "Set last dataset sample to t=0\n"
            "(full dataset, current zoom unchanged)")
        self.btn_t0_last.setStyleSheet(_btn_style)
        self.btn_t0_last.clicked.connect(self._set_t0_last)

        t0_row.addWidget(self.btn_t0_first)
        t0_row.addWidget(self.btn_t0_mid)
        t0_row.addWidget(self.btn_t0_last)
        layout.addLayout(t0_row)

        layout.addStretch()

    def _on_search_direction_changed(self, is_forward: bool):
        if is_forward:
            self.btn_next.setText("Next →")
            self.btn_next.setToolTip("Find next trigger after the last one")
        else:
            self.btn_next.setText("← Prev")
            self.btn_next.setToolTip("Find previous trigger before the last one")

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

        level    = self.edit_level.get_value(0.0)
        edge_idx = self.combo_edge.currentIndex()  # 0=rise, 1=fall, 2=either

        t = trace.time_axis
        y = trace.processed_data

        backward  = self.radio_dir_backward.isChecked()
        from_edge = self.radio_from_edge.isChecked()   # False = from t=0

        if backward:
            if search_after is not None:
                # ← Prev: continue backward from just before last found trigger
                i_end = int(np.searchsorted(t, search_after)) - 2
            elif from_edge:
                # Backward from end of waveform
                i_end = len(t) - 2
            else:
                # Backward from t=0
                i_end = int(np.searchsorted(t, 0.0)) - 1
            i_end = max(0, min(i_end, len(t) - 2))
            t_pos   = self._find_crossing_backward(t, y, level, edge_idx, i_end)
            bound_t = float(t[i_end])
            direction = "before"
        else:
            if search_after is not None:
                # Next →: continue forward from just after last found trigger
                i_start = int(np.searchsorted(t, search_after)) + 1
            elif from_edge:
                # Forward from start of waveform
                i_start = 0
            else:
                # Forward from t=0
                i_start = int(np.searchsorted(t, 0.0))
            i_start = max(0, min(i_start, len(t) - 2))
            t_pos   = self._find_crossing(t, y, level, edge_idx, i_start)
            bound_t = float(t[i_start])
            direction = "after"

        if t_pos is None:
            edge_name = ("rising" if edge_idx == 0
                         else "falling" if edge_idx == 1 else "")
            self.lbl_status.setText(
                f"No {edge_name} crossing at {level:.4g} "
                f"found {direction} t={bound_t:.6g}")
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

    @staticmethod
    def _find_crossing_backward(t: np.ndarray, y: np.ndarray,
                                level: float, edge_idx: int,
                                i_end: int) -> Optional[float]:
        """
        Find last threshold crossing at or before i_end, searching backward.
        Uses the same edge definitions as _find_crossing.
        """
        if len(t) < 2:
            return None

        for i in range(min(i_end, len(y) - 2), -1, -1):
            a, b = float(y[i]), float(y[i + 1])
            is_rising  = (a < level) and (b >= level)
            is_falling = (a > level) and (b <= level)

            if edge_idx == 0 and not is_rising:
                continue
            if edge_idx == 1 and not is_falling:
                continue
            if edge_idx == 2 and not (is_rising or is_falling):
                continue

            denom = b - a
            frac = 0.0 if denom == 0 else (level - a) / denom
            t0, t1 = float(t[i]), float(t[i + 1])
            return t0 + frac * (t1 - t0)

        return None


    # ── Quick set-t=0 helpers ─────────────────────────────────────────────

    def _dataset_bounds(self) -> Optional[tuple]:
        """Return (t_min, t_max) across all loaded traces, or None if no data."""
        t_min: Optional[float] = None
        t_max: Optional[float] = None
        for trace in self._traces:
            ta = trace.time_axis
            if ta is not None and len(ta) > 0:
                lo, hi = float(ta[0]), float(ta[-1])
                if t_min is None or lo < t_min:
                    t_min = lo
                if t_max is None or hi > t_max:
                    t_max = hi
        if t_min is None:
            return None
        return t_min, t_max

    def _set_t0_first(self):
        bounds = self._dataset_bounds()
        if bounds is not None:
            self.set_time_zero.emit(bounds[0])

    def _set_t0_last(self):
        bounds = self._dataset_bounds()
        if bounds is not None:
            self.set_time_zero.emit(bounds[1])

    def _set_t0_middle(self):
        bounds = self._dataset_bounds()
        if bounds is not None:
            self.set_time_zero.emit((bounds[0] + bounds[1]) / 2.0)


def _fmt_time(t: float) -> str:
    a = abs(t)
    if a == 0:   return "0 s"
    if a < 1e-9: return f"{t*1e12:.4g} ps"
    if a < 1e-6: return f"{t*1e9:.4g} ns"
    if a < 1e-3: return f"{t*1e6:.4g} µs"
    if a < 1:    return f"{t*1e3:.4g} ms"
    return f"{t:.6g} s"
