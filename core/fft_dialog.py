"""
core/fft_dialog.py
FFT analysis dialog.

Features:
  - Two draggable cursors (click = A, Shift+click = B) with frequency/amplitude readout
  - Snap cursor to next higher-frequency peak
  - Mark top-N peaks with dotted lines and frequency labels
  - Fit Frequency / Fit Amplitude view controls
  - Auto-Y option
"""

import numpy as np
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QComboBox, QLabel,
    QPushButton, QRadioButton, QDoubleSpinBox, QLineEdit,
    QFrame, QCheckBox, QSizePolicy
)
from core.filter_dialog import _parse_si_freq, _format_si_freq
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
import pyqtgraph as pg
from pyqtgraph import InfiniteLine
from typing import List, Optional, Tuple
from pytraceview.trace_model import TraceModel

# ── Scipy optional ────────────────────────────────────────────────────────────
try:
    from scipy.signal import find_peaks as _scipy_find_peaks
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False

# ── Constants ─────────────────────────────────────────────────────────────────
WINDOWS = {
    "Hanning":     np.hanning,
    "Hamming":     np.hamming,
    "Blackman":    np.blackman,
    "Rectangular": np.ones,
    "Flat Top":    lambda n: np.ones(n),
}

_CURSOR_COLORS = {0: "#40c0ff", 1: "#ff8040"}
_PEAK_COLOR    = "#ffcc00"
_N_MARK_PEAKS  = 10


# ── Helper functions ──────────────────────────────────────────────────────────

def compute_fft(y: np.ndarray, sample_rate: float,
                window_name: str = "Hanning") -> Tuple[np.ndarray, np.ndarray]:
    n = len(y)
    if n < 4:
        return np.array([1e-10]), np.array([-120.0])
    win = WINDOWS.get(window_name, np.hanning)(n)
    y_w = (y - np.mean(y)) * win
    fft_result = np.fft.rfft(y_w)
    freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate)
    mag = np.abs(fft_result) / (n / 2)
    mag[0] /= 2
    mag_db = 20 * np.log10(np.maximum(mag, 1e-12))
    freqs = np.maximum(freqs, 1e-10)
    return freqs, mag_db


def _find_fft_peaks(mag_db: np.ndarray,
                    min_prominence: float = 3.0) -> np.ndarray:
    """Return sorted indices of local maxima with minimum prominence in dB."""
    if len(mag_db) < 3:
        return np.array([], dtype=int)
    if _HAS_SCIPY:
        peaks, _ = _scipy_find_peaks(mag_db, prominence=min_prominence)
        return peaks
    # Fallback: local maxima above (median + min_prominence)
    candidates = np.where(
        (mag_db[1:-1] >= mag_db[:-2]) & (mag_db[1:-1] >= mag_db[2:])
    )[0] + 1
    if not len(candidates):
        return np.array([], dtype=int)
    threshold = np.median(mag_db) + min_prominence
    return candidates[mag_db[candidates] >= threshold]


def _fmt_freq(f: float) -> str:
    a = abs(f)
    if a >= 1e9: return f"{f/1e9:.4g} GHz"
    if a >= 1e6: return f"{f/1e6:.4g} MHz"
    if a >= 1e3: return f"{f/1e3:.4g} kHz"
    return f"{f:.4g} Hz"


def _fmt_db(v: float) -> str:
    return f"{v:.2f} dBFS"


# ── Clickable TextItem for per-marker delete ───────────────────────────────────

class ClickableMarkerText(pg.TextItem):
    """TextItem that fires a callback when left-clicked — used for marker delete."""
    def __init__(self, *args, on_click=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._on_click = on_click

    def mouseClickEvent(self, ev):
        if (self._on_click is not None
                and ev.button() == Qt.MouseButton.LeftButton):
            self._on_click()
            ev.accept()
        else:
            ev.ignore()


# ── Dialog ────────────────────────────────────────────────────────────────────

_FFT_PLOT_BG = "#050508"   # background of the FFT plot widget


class FFTDialog(QDialog):
    def __init__(self, traces: List[TraceModel],
                 view_range: Optional[Tuple] = None,
                 fft_min_freq: float = 1.0,
                 settings: dict = None,
                 parent=None):
        super().__init__(parent)
        self.traces = [t for t in traces if t.visible]
        self.view_range = view_range
        self.fft_min_freq = fft_min_freq
        self._settings = settings or {}
        self.setWindowTitle("FFT Analysis")
        self.resize(1140, 680)

        # FFT result (current compute)
        self._freqs:  Optional[np.ndarray] = None
        self._mag_db: Optional[np.ndarray] = None

        # Axis units — not hardcoded; updated by _compute so export stays correct
        # if future modes change what the axes represent (e.g. dBm, linear V …)
        self._freq_unit: str = "Hz"
        self._ampl_unit: str = "dBFS"

        # Marker label background opacity (settable via settings.json)
        self._marker_bg_opacity: float = float(
            self._settings.get("fft_marker_bg_opacity", 0.60))

        # Cursor state — actual frequencies (not log)
        self._cursor_freq: dict = {0: None, 1: None}
        self._cursor_lines: dict = {}   # cursor_id -> InfiniteLine

        # Marker groups: each entry is {"items": [dot, txt], "box": (…)}
        # _marker_boxes mirrors the index of _marker_groups for overlap checks.
        self._marker_groups: list = []
        self._marker_boxes:  list = []   # [(log_f, y_bot, w_data, h_data), …]

        # "Add Marker" interactive placement mode
        self._marker_placement_mode: bool = False

        self._build_ui()
        self._compute()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(4)
        root.setContentsMargins(6, 6, 6, 6)

        # ── Row 1: compute controls ───────────────────────────────────────────
        ctrl = QHBoxLayout()

        ctrl.addWidget(QLabel("Trace:"))
        self.combo_trace = QComboBox()
        for t in self.traces:
            self.combo_trace.addItem(t.label, t.name)
        ctrl.addWidget(self.combo_trace)
        ctrl.addSpacing(8)

        ctrl.addWidget(QLabel("Window:"))
        self.combo_window = QComboBox()
        self.combo_window.addItems(list(WINDOWS.keys()))
        self.combo_window.setCurrentText("Hanning")
        ctrl.addWidget(self.combo_window)
        ctrl.addSpacing(8)

        self.radio_all = QRadioButton("All data")
        self.radio_win = QRadioButton("Windowed view")
        self.radio_all.setChecked(True)
        if self.view_range is None:
            self.radio_win.setEnabled(False)
        ctrl.addWidget(self.radio_all)
        ctrl.addWidget(self.radio_win)
        ctrl.addSpacing(8)

        ctrl.addWidget(QLabel("Min:"))
        self.edit_min_freq = QLineEdit()
        self.edit_min_freq.setFixedWidth(72)
        self.edit_min_freq.setPlaceholderText("e.g. 1Hz")
        self.edit_min_freq.setText(
            _format_si_freq(self.fft_min_freq) if self.fft_min_freq > 0 else "1 Hz")
        self._lbl_min_freq_fb = QLabel()
        self._lbl_min_freq_fb.setFixedWidth(80)
        ctrl.addWidget(self.edit_min_freq)
        ctrl.addWidget(self._lbl_min_freq_fb)
        ctrl.addSpacing(4)

        ctrl.addWidget(QLabel("Max:"))
        self.edit_max_freq = QLineEdit()
        self.edit_max_freq.setFixedWidth(72)
        self.edit_max_freq.setPlaceholderText("Nyquist")
        _max_init = float(self._settings.get("fft_max_freq", 0.0))
        self.edit_max_freq.setText(
            _format_si_freq(_max_init) if _max_init > 0 else "")
        self._lbl_max_freq_fb = QLabel()
        self._lbl_max_freq_fb.setFixedWidth(80)
        ctrl.addWidget(self.edit_max_freq)
        ctrl.addWidget(self._lbl_max_freq_fb)
        ctrl.addSpacing(4)

        btn_suggest = QPushButton("Suggest")
        btn_suggest.setToolTip(
            "Fill Min/Max from the selected trace's data limits\n"
            "(1 / total_duration and Nyquist), based on the current compute setting.")
        btn_suggest.clicked.connect(self._suggest_freq_limits)
        ctrl.addWidget(btn_suggest)
        ctrl.addSpacing(8)

        ctrl.addWidget(QLabel("Min prominence (dB):"))
        self.spin_prominence = QDoubleSpinBox()
        self.spin_prominence.setRange(0.5, 60.0)
        self.spin_prominence.setDecimals(1)
        self.spin_prominence.setSingleStep(1.0)
        self.spin_prominence.setValue(
            float(self._settings.get("fft_peak_prominence", 3.0)))
        self.spin_prominence.setFixedWidth(70)
        self.spin_prominence.setToolTip(
            "Minimum peak prominence in dB for 'Mark Top N' and cursor snap.\n"
            "Lower = more peaks found; raise to suppress noise peaks.")
        ctrl.addWidget(self.spin_prominence)
        ctrl.addSpacing(8)

        btn_compute = QPushButton("Compute FFT")
        btn_compute.clicked.connect(self._compute)
        btn_compute.setStyleSheet(
            "background: #2060c0; color: white; padding: 4px 12px;")
        ctrl.addWidget(btn_compute)
        ctrl.addStretch()
        root.addLayout(ctrl)

        # ── Row 2: tool controls ──────────────────────────────────────────────
        tool = QHBoxLayout()
        tool.setSpacing(6)

        # ── Cursors section ───────────────────────────────────────────
        lbl_sec_cur = QLabel("Cursors")
        lbl_sec_cur.setStyleSheet(
            "color: #888; font-size: 8pt; font-weight: bold; padding: 0 4px;")
        tool.addWidget(lbl_sec_cur)

        # Cursor A readout + snap
        self._lbl_cur_a = QLabel("A: —")
        self._lbl_cur_a.setStyleSheet(
            f"color: {_CURSOR_COLORS[0]}; font-weight: bold; min-width: 200px;")
        tool.addWidget(self._lbl_cur_a)
        btn_snap_a = QPushButton("Snap A →")
        btn_snap_a.setToolTip(
            "Jump Cursor A to the next peak at a higher frequency")
        btn_snap_a.clicked.connect(lambda: self._snap_to_next_peak(0))
        tool.addWidget(btn_snap_a)

        tool.addSpacing(10)

        # Cursor B readout + snap
        self._lbl_cur_b = QLabel("B: —")
        self._lbl_cur_b.setStyleSheet(
            f"color: {_CURSOR_COLORS[1]}; font-weight: bold; min-width: 200px;")
        tool.addWidget(self._lbl_cur_b)
        btn_snap_b = QPushButton("Snap B →")
        btn_snap_b.setToolTip(
            "Jump Cursor B to the next peak at a higher frequency")
        btn_snap_b.clicked.connect(lambda: self._snap_to_next_peak(1))
        tool.addWidget(btn_snap_b)

        btn_remove = QPushButton("Remove Cursors")
        btn_remove.setToolTip("Clear both cursors from the plot")
        btn_remove.clicked.connect(self._remove_cursors)
        tool.addWidget(btn_remove)

        tool.addWidget(self._vsep())

        # ── Markers section ───────────────────────────────────────────
        lbl_sec_mk = QLabel("Markers")
        lbl_sec_mk.setStyleSheet(
            "color: #888; font-size: 8pt; font-weight: bold; padding: 0 4px;")
        tool.addWidget(lbl_sec_mk)

        btn_mark = QPushButton(f"Mark Top {_N_MARK_PEAKS} Peaks")
        btn_mark.setToolTip("Mark the highest amplitude peaks on the plot")
        btn_mark.clicked.connect(self._mark_peaks)
        tool.addWidget(btn_mark)
        self.btn_add_marker = QPushButton("Add Marker")
        self.btn_add_marker.setCheckable(True)
        self.btn_add_marker.setToolTip(
            "Click anywhere on the FFT trace to place a manual marker.\n"
            "Click this button again to stop adding markers.")
        self.btn_add_marker.toggled.connect(self._on_add_marker_toggled)
        tool.addWidget(self.btn_add_marker)
        self.chk_snap_peak = QCheckBox("Snap to peak")
        self.chk_snap_peak.setChecked(True)
        self.chk_snap_peak.setToolTip(
            "When placing a manual marker, snap to the nearest FFT peak\n"
            "if one exists within ~5% of the view width in log space.")
        tool.addWidget(self.chk_snap_peak)
        btn_clear = QPushButton("Clear Markers")
        btn_clear.clicked.connect(self._clear_markers)
        tool.addWidget(btn_clear)

        tool.addWidget(self._vsep())

        # ── View section ──────────────────────────────────────────────
        lbl_sec_vw = QLabel("View")
        lbl_sec_vw.setStyleSheet(
            "color: #888; font-size: 8pt; font-weight: bold; padding: 0 4px;")
        tool.addWidget(lbl_sec_vw)

        btn_fit_x = QPushButton("Fit Frequency")
        btn_fit_x.setToolTip("Auto-range X axis to the full FFT frequency span")
        btn_fit_x.clicked.connect(self._fit_frequency)
        tool.addWidget(btn_fit_x)
        btn_fit_y = QPushButton("Fit Amplitude")
        btn_fit_y.setToolTip("Auto-range Y axis to the visible amplitude data")
        btn_fit_y.clicked.connect(self._fit_amplitude)
        tool.addWidget(btn_fit_y)
        self.chk_auto_y = QCheckBox("Auto Y")
        self.chk_auto_y.setChecked(True)
        self.chk_auto_y.setToolTip(
            "Automatically fit the Y axis after each Compute")
        tool.addWidget(self.chk_auto_y)

        tool.addWidget(self._vsep())

        btn_export = QPushButton("Export ▾")
        btn_export.setToolTip("Export FFT data as CSV or save a screenshot")
        btn_export.clicked.connect(self._show_export_menu)
        tool.addWidget(btn_export)
        self._btn_export = btn_export

        tool.addStretch()
        root.addLayout(tool)

        # ── Plot ──────────────────────────────────────────────────────────────
        self.plot = pg.PlotWidget(background="#050508")
        pi = self.plot.getPlotItem()
        pi.setMenuEnabled(False)   # suppress pyqtgraph's built-in export menu
        pi.setLabel("bottom", f"Frequency ({self._freq_unit})")
        pi.setLabel("left",   f"Magnitude ({self._ampl_unit})")
        pi.showGrid(x=True, y=True, alpha=0.3)
        pi.setLogMode(x=True, y=False)
        for ax_name in ("left", "bottom"):
            ax_obj = pi.getAxis(ax_name)
            ax_obj.setPen(pg.mkPen(color="#e0e0e0"))
            ax_obj.setTextPen(pg.mkPen(color="#e0e0e0"))
        self.plot.addLegend()
        self.plot.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(self.plot, stretch=1)

        # Mouse click to place cursors
        self.plot.scene().sigMouseClicked.connect(self._on_plot_clicked)

        # Auto-Y: refit amplitude whenever the frequency (X) range changes
        pi.sigRangeChanged.connect(self._on_range_changed)

        # ── Cursor readout bar ────────────────────────────────────────────────
        self._lbl_readout = QLabel(
            "Click on plot to place Cursor A  |  Shift+click to place Cursor B")
        self._lbl_readout.setStyleSheet(
            "color: #aaa; font-size: 9pt; padding: 2px 6px; "
            "border-top: 1px solid #333;")
        self._lbl_readout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._lbl_readout)

        # ── Out-of-view peaks warning ─────────────────────────────────────────
        self._lbl_hidden_peaks = QLabel()
        self._lbl_hidden_peaks.setStyleSheet(
            "color: #ff6666; background: #3a0000; font-size: 10pt; "
            "font-weight: bold; padding: 6px 8px; "
            "border-top: 2px solid #993333;")
        self._lbl_hidden_peaks.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_hidden_peaks.setVisible(False)
        root.addWidget(self._lbl_hidden_peaks)

        # Wire up SI freq input feedback
        self.edit_min_freq.textChanged.connect(
            lambda: self._update_freq_fb(
                self.edit_min_freq, self._lbl_min_freq_fb, allow_empty=False))
        self.edit_max_freq.textChanged.connect(
            lambda: self._update_freq_fb(
                self.edit_max_freq, self._lbl_max_freq_fb, allow_empty=True))
        self._update_freq_fb(self.edit_min_freq, self._lbl_min_freq_fb, allow_empty=False)
        self._update_freq_fb(self.edit_max_freq, self._lbl_max_freq_fb, allow_empty=True)

    @staticmethod
    def _vsep() -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        return sep

    # ── Freq input helpers ────────────────────────────────────────────────────

    def _update_freq_fb(self, edit: QLineEdit, label: QLabel,
                        allow_empty: bool = False):
        """Colour the feedback label next to a SI frequency input."""
        text = edit.text().strip()
        if not text:
            if allow_empty:
                label.setText("= Nyquist")
                label.setStyleSheet("color: #888888;")
                edit.setStyleSheet("")
            else:
                label.setText("invalid")
                label.setStyleSheet("color: #e05050;")
                edit.setStyleSheet("border: 1px solid #e05050;")
            return
        hz = _parse_si_freq(text)
        if hz is None or hz < 0:
            label.setText("invalid")
            label.setStyleSheet("color: #e05050;")
            edit.setStyleSheet("border: 1px solid #e05050;")
        else:
            label.setText(f"= {_format_si_freq(hz)}")
            label.setStyleSheet("color: #60c060;")
            edit.setStyleSheet("")

    def _suggest_freq_limits(self):
        """Fill Min/Max from trace data limits matching the current compute setting."""
        trace_name = self.combo_trace.currentData()
        use_window = self.radio_win.isChecked() and self.view_range is not None
        for trace in self.traces:
            if trace.name != trace_name:
                continue
            sps = trace.sample_rate
            if not sps or sps <= 0:
                return
            if use_window and self.view_range:
                _, y = trace.windowed_data(*self.view_range)
            else:
                y = trace.processed_data
            n = len(y) if y is not None else 0
            if n < 2:
                return
            nyq = sps / 2.0
            min_f = sps / n          # = 1 / duration
            self.edit_min_freq.setText(_format_si_freq(min_f))
            self.edit_max_freq.setText(_format_si_freq(nyq))
            break

    @property
    def min_freq_hz(self) -> float:
        v = _parse_si_freq(self.edit_min_freq.text())
        return max(v, 1e-10) if (v is not None and v > 0) else 1e-10

    @property
    def max_freq_hz(self) -> float:
        """Returns 0.0 when the field is empty (meaning 'up to Nyquist')."""
        v = _parse_si_freq(self.edit_max_freq.text())
        return v if (v is not None and v > 0) else 0.0

    # ── FFT computation ───────────────────────────────────────────────────────

    def _compute(self):
        pi = self.plot.getPlotItem()
        # pi.clear() removes all items — clear stale references before it
        self._cursor_lines.clear()
        self._marker_groups.clear()
        self._marker_boxes.clear()
        pi.clear()
        self.plot.addLegend()
        # Keep axis labels in sync with current units
        pi.setLabel("bottom", f"Frequency ({self._freq_unit})")
        pi.setLabel("left",   f"Magnitude ({self._ampl_unit})")

        trace_name = self.combo_trace.currentData()
        window_name = self.combo_window.currentText()
        use_window = self.radio_win.isChecked() and self.view_range is not None
        _min_v = _parse_si_freq(self.edit_min_freq.text())
        min_freq = max(_min_v, 1e-10) if (_min_v is not None and _min_v > 0) else 1e-10
        _max_v = _parse_si_freq(self.edit_max_freq.text())
        max_freq = _max_v if (_max_v is not None and _max_v > 0) else None

        self._freqs = None
        self._mag_db = None

        for trace in self.traces:
            if trace.name != trace_name:
                continue
            if use_window and self.view_range:
                _, y = trace.windowed_data(*self.view_range)
            else:
                y = trace.processed_data
            if len(y) < 4:
                continue
            freqs, mag_db = compute_fft(y, trace.sample_rate, window_name)
            mask = freqs >= min_freq
            if max_freq is not None:
                mask &= (freqs <= max_freq)
            freqs, mag_db = freqs[mask], mag_db[mask]
            if not len(freqs):
                continue
            self._freqs = freqs
            self._mag_db = mag_db
            pen = pg.mkPen(color=trace.color, width=1.5)
            self.plot.plot(freqs, mag_db, pen=pen, name=trace.label)
            break

        # Restore cursors that are still within the new frequency range
        for cid, freq in list(self._cursor_freq.items()):
            if freq is not None and self._freqs is not None:
                if self._freqs[0] <= freq <= self._freqs[-1]:
                    self._place_cursor(cid, freq, _store=False)

        if self.chk_auto_y.isChecked():
            self._fit_amplitude()
        else:
            pi.enableAutoRange(axis="x")

    # ── Cursor placement ──────────────────────────────────────────────────────

    def _on_range_changed(self, _view, ranges):
        """Called whenever the plot view range changes (pan / zoom)."""
        # Only refit Y when the X range changed and Auto Y is active.
        # ranges is [[x0,x1],[y0,y1]]; skip if only Y moved to avoid loops.
        if self.chk_auto_y.isChecked() and self._mag_db is not None:
            self._fit_amplitude()

    def _on_add_marker_toggled(self, active: bool):
        self._marker_placement_mode = active
        self.plot.setCursor(Qt.CursorShape.CrossCursor if active
                            else Qt.CursorShape.ArrowCursor)

    def _on_plot_clicked(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.scenePos()
        vb = self.plot.getPlotItem().vb
        if not vb.sceneBoundingRect().contains(pos):
            return
        mouse_pt = vb.mapSceneToView(pos)
        # In log-x mode the view-box X coordinate is log10(freq)
        freq = 10.0 ** mouse_pt.x()
        if self._freqs is None or not (self._freqs[0] <= freq <= self._freqs[-1]):
            return
        # Marker placement mode
        if self._marker_placement_mode:
            if self.chk_snap_peak.isChecked():
                # Snap to nearest peak within ~5% of log view width
                peaks = self._get_peaks()
                snap_f, snap_a = self._snap_to_nearest_peak(
                    freq, peaks, snap_frac=0.05)
            else:
                snap_f = snap_a = None
            if snap_f is not None:
                self._place_single_marker(snap_f, snap_a)
            else:
                idx = int(np.clip(np.searchsorted(self._freqs, freq),
                                  0, len(self._freqs) - 1))
                self._place_single_marker(
                    float(self._freqs[idx]), float(self._mag_db[idx]))
            event.accept()
            return
        # Shift+click → cursor B; plain click → cursor A
        cid = 1 if (event.modifiers() & Qt.KeyboardModifier.ShiftModifier) else 0
        self._place_cursor(cid, freq)
        event.accept()

    def _place_cursor(self, cid: int, freq: float, _store: bool = True):
        """Place (or move) cursor cid to the given frequency."""
        if _store:
            self._cursor_freq[cid] = freq
        # Remove stale line
        if cid in self._cursor_lines:
            try:
                self.plot.removeItem(self._cursor_lines[cid])
            except Exception:
                pass
        color = _CURSOR_COLORS[cid]
        label = "A" if cid == 0 else "B"
        pen = pg.mkPen(color=color, width=1.5, style=Qt.PenStyle.DashLine)
        # InfiniteLine position is in view (log10) coordinates when log mode is on
        line = InfiniteLine(
            pos=np.log10(freq), angle=90, pen=pen, movable=True,
            label=label,
            labelOpts={"color": color, "position": 0.93, "fill": "#00000080"})
        line.sigPositionChanged.connect(
            lambda l, c=cid: self._on_cursor_dragged(c, l.value()))
        self.plot.addItem(line)
        self._cursor_lines[cid] = line
        self._update_readout()

    def _on_cursor_dragged(self, cid: int, log_f: float):
        self._cursor_freq[cid] = 10.0 ** log_f
        self._update_readout()

    def _remove_cursors(self):
        for cid in list(self._cursor_lines):
            try:
                self.plot.removeItem(self._cursor_lines[cid])
            except Exception:
                pass
        self._cursor_lines.clear()
        self._cursor_freq = {0: None, 1: None}
        self._update_readout()

    def _update_readout(self):
        fa = self._cursor_freq.get(0)
        fb = self._cursor_freq.get(1)
        ma = self._mag_at(fa)
        mb = self._mag_at(fb)

        a_str = (f"A: {_fmt_freq(fa)}  {_fmt_db(ma)}"
                 if fa is not None and ma is not None else "A: —")
        b_str = (f"B: {_fmt_freq(fb)}  {_fmt_db(mb)}"
                 if fb is not None and mb is not None else "B: —")
        self._lbl_cur_a.setText(a_str)
        self._lbl_cur_b.setText(b_str)

        parts = []
        if fa is not None and ma is not None:
            parts.append(f"Cursor A: {_fmt_freq(fa)}  {_fmt_db(ma)}")
        if fb is not None and mb is not None:
            parts.append(f"Cursor B: {_fmt_freq(fb)}  {_fmt_db(mb)}")
        if (fa is not None and fb is not None
                and ma is not None and mb is not None):
            df  = abs(fb - fa)
            ddB = mb - ma
            parts.append(f"ΔF: {_fmt_freq(df)}  ΔdB: {ddB:+.2f} dB")
        if parts:
            self._lbl_readout.setText("   |   ".join(parts))
        else:
            self._lbl_readout.setText(
                "Click on plot to place Cursor A  |  "
                "Shift+click to place Cursor B")

    def _mag_at(self, freq: Optional[float]) -> Optional[float]:
        if freq is None or self._freqs is None or self._mag_db is None:
            return None
        idx = int(np.clip(np.searchsorted(self._freqs, freq),
                          0, len(self._freqs) - 1))
        return float(self._mag_db[idx])

    # ── Peak operations ───────────────────────────────────────────────────────

    def _get_peaks(self) -> np.ndarray:
        if self._mag_db is None:
            return np.array([], dtype=int)
        return _find_fft_peaks(self._mag_db,
                               min_prominence=self.spin_prominence.value())

    def _snap_to_nearest_peak(self, freq: float, peaks: np.ndarray,
                               snap_frac: float = 0.05
                               ) -> Tuple[Optional[float], Optional[float]]:
        """
        Return (peak_freq, peak_amp) for the nearest peak to `freq` in log space,
        if it is within snap_frac * (log view width) of the click.
        Returns (None, None) when no peak is close enough.
        """
        if not len(peaks) or self._freqs is None:
            return None, None
        pi = self.plot.getPlotItem()
        xr = pi.viewRange()[0]          # [log10_lo, log10_hi]
        view_width_log = abs(xr[1] - xr[0])
        threshold_log  = snap_frac * view_width_log

        peak_freqs = self._freqs[peaks]
        peak_amps  = self._mag_db[peaks]
        log_dists  = np.abs(np.log10(peak_freqs) - np.log10(freq))
        nearest    = int(np.argmin(log_dists))
        if log_dists[nearest] <= threshold_log:
            return float(peak_freqs[nearest]), float(peak_amps[nearest])
        return None, None

    def _snap_to_next_peak(self, cid: int):
        peaks = self._get_peaks()
        if not len(peaks):
            return
        peak_freqs = self._freqs[peaks]
        current = self._cursor_freq.get(cid)
        if current is None:
            self._place_cursor(cid, float(peak_freqs[0]))
        else:
            above = peak_freqs[peak_freqs > current * 1.001]
            if len(above):
                self._place_cursor(cid, float(above[0]))
            else:
                self._place_cursor(cid, float(peak_freqs[0]))   # wrap around

    def _mark_peaks(self):
        self._clear_markers()
        self._lbl_hidden_peaks.setVisible(False)
        if self._freqs is None or self._mag_db is None:
            return
        peaks = self._get_peaks()
        if not len(peaks):
            return

        # Split all peaks into visible vs. outside current view (log10 space)
        xr = self.plot.getPlotItem().viewRange()[0]   # [log10_lo, log10_hi]
        log_freqs = np.log10(self._freqs[peaks])
        in_view  = (log_freqs >= xr[0]) & (log_freqs <= xr[1])

        vis_peaks = peaks[in_view]
        out_peaks = peaks[~in_view]

        # Select top-N from the visible peaks and mark them all
        if not len(vis_peaks):
            return
        n = min(_N_MARK_PEAKS, len(vis_peaks))
        top_idx   = np.argsort(self._mag_db[vis_peaks])[-n:]
        top_peaks = vis_peaks[top_idx]

        # Banner: warn if any hidden peak is louder than the quietest marked peak
        if len(out_peaks):
            min_marked_db = float(self._mag_db[top_peaks].min())
            max_hidden_db = float(self._mag_db[out_peaks].max())
            if max_hidden_db > min_marked_db:
                n_louder = int((self._mag_db[out_peaks] > min_marked_db).sum())
                self._lbl_hidden_peaks.setText(
                    f"⚠  {n_louder} peak{'s' if n_louder != 1 else ''} outside "
                    f"the current view "
                    f"{'are' if n_louder != 1 else 'is'} louder than the marked peaks "
                    f"— zoom out to see {'them' if n_louder != 1 else 'it'}")
                self._lbl_hidden_peaks.setVisible(True)

        # Place markers sorted left-to-right for layout
        top_peaks = top_peaks[np.argsort(self._freqs[top_peaks])]
        for idx in top_peaks:
            self._place_single_marker(
                float(self._freqs[idx]), float(self._mag_db[idx]))

        # Expand Y range to include all label tops
        self._fit_amplitude()

    def _clear_markers(self):
        self._lbl_hidden_peaks.setVisible(False)
        for group in self._marker_groups:
            for item in group.get("items", []):
                try:
                    self.plot.removeItem(item)
                except Exception:
                    pass
        self._marker_groups.clear()
        self._marker_boxes.clear()

    # ── Smart marker placement ────────────────────────────────────────────────

    def _label_size_data(self) -> tuple:
        """Estimate label width and height in current data (log-f, dB) units."""
        pi = self.plot.getPlotItem()
        xr = pi.viewRange()[0]
        yr = pi.viewRange()[1]
        # Subtract rough axis margins so estimate matches the actual plot area
        w_px = max(1, self.plot.width()  - 80)
        h_px = max(1, self.plot.height() - 40)
        x_per_px = (xr[1] - xr[0]) / w_px
        y_per_px = (yr[1] - yr[0]) / h_px
        # "−23.4 dBFS @ 1.23 kHz" → ~160 px wide, ~16 px tall
        return 160 * x_per_px, 16 * y_per_px

    def _label_overlaps(self, log_f: float, y_bot: float,
                        lw: float, lh: float) -> bool:
        """True if a proposed label box overlaps any already-placed box."""
        y_top = y_bot + lh
        for (px, py_bot, pw, ph) in self._marker_boxes:
            py_top = py_bot + ph
            if (abs(log_f - px) < (lw + pw) / 2 and
                    y_bot < py_top and y_top > py_bot):
                return True
        return False

    def _find_label_y(self, log_f: float, peak_amp: float,
                      lw: float, lh: float) -> float:
        """Return lowest Y (bottom of label) above peak_amp that doesn't overlap."""
        gap  = lh * 0.4        # breathing room between peak dot and label bottom
        y    = peak_amp + gap
        step = lh * 1.15
        for _ in range(40):    # up to 40 slots before giving up
            if not self._label_overlaps(log_f, y, lw, lh):
                return y
            y += step
        return y

    def _place_single_marker(self, f: float, a: float):
        """Place one smart marker (dot + label with opaque background) for freq f, amp a."""
        log_f = np.log10(f)
        text  = f"{_fmt_db(a)} @ {_fmt_freq(f)}"
        lw, lh = self._label_size_data()
        y_bot  = self._find_label_y(log_f, a, lw, lh)

        group: dict = {"items": []}

        # ── Label with filled background ───────────────────────────────────────
        # anchor=(0.5, 1.0): bottom-centre of text sits at (log_f, y_bot)
        bg_color = QColor(_FFT_PLOT_BG)
        bg_color.setAlphaF(self._marker_bg_opacity)
        txt = ClickableMarkerText(
            text=text, color=_PEAK_COLOR, anchor=(0.5, 1.0),
            fill=pg.mkBrush(bg_color),
            on_click=lambda g=group: self._delete_marker_group(g))
        txt.setPos(log_f, y_bot)
        self.plot.addItem(txt)
        group["items"].append(txt)

        self._marker_groups.append(group)
        # Record bounding box so the next marker avoids this one
        self._marker_boxes.append((log_f, y_bot, lw, lh))

    def _delete_marker_group(self, group: dict):
        """Remove a single marker group from the plot and tracking lists."""
        for item in group.get("items", []):
            try:
                self.plot.removeItem(item)
            except Exception:
                pass
        if group in self._marker_groups:
            idx = self._marker_groups.index(group)
            self._marker_groups.pop(idx)
            self._marker_boxes.pop(idx)

    # ── Export ────────────────────────────────────────────────────────────────

    def _show_export_menu(self):
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QAction
        menu = QMenu(self)
        act_csv = QAction("Export as CSV…", self)
        act_csv.triggered.connect(self._export_csv)
        menu.addAction(act_csv)
        act_png = QAction("Save Screenshot…", self)
        act_png.triggered.connect(self._export_screenshot)
        menu.addAction(act_png)
        menu.exec(self._btn_export.mapToGlobal(
            self._btn_export.rect().bottomLeft()))

    def _export_csv(self):
        if self._freqs is None or self._mag_db is None:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Export", "No FFT data to export.")
            return
        from PyQt6.QtWidgets import QFileDialog
        import os
        path, _ = QFileDialog.getSaveFileName(
            self, "Export FFT as CSV", "",
            "CSV Files (*.csv);;All Files (*)")
        if not path:
            return
        freq_hdr = f"Freq ({self._freq_unit})"
        ampl_hdr = f"Ampl ({self._ampl_unit})"
        lines = [f"{freq_hdr},{ampl_hdr}"]
        for f, a in zip(self._freqs, self._mag_db):
            lines.append(f"{f:.10g},{a:.10g}")
        try:
            with open(path, "w") as fh:
                fh.write("\n".join(lines))
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Export Error", str(e))

    def _export_screenshot(self):
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, "Save FFT Screenshot", "",
            "PNG Images (*.png);;All Files (*)")
        if not path:
            return
        px = self.grab()   # includes control bars + plot + cursor readout
        if not px.save(path):
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Screenshot Error",
                                 f"Could not save image to:\n{path}")

    # ── View controls ─────────────────────────────────────────────────────────

    def _fit_frequency(self):
        pi = self.plot.getPlotItem()
        if self._freqs is not None and len(self._freqs):
            lo = np.log10(float(self._freqs[0]))
            hi = np.log10(float(self._freqs[-1]))
            pad = (hi - lo) * 0.02
            pi.setXRange(lo - pad, hi + pad, padding=0)
        else:
            pi.enableAutoRange(axis="x")

    def _fit_amplitude(self):
        pi = self.plot.getPlotItem()
        if self._freqs is None or self._mag_db is None:
            pi.enableAutoRange(axis="y")
            return
        # Fit to data visible in the current X window
        try:
            vr = pi.viewRange()
            lo_f = 10.0 ** vr[0][0]
            hi_f = 10.0 ** vr[0][1]
            mask = (self._freqs >= lo_f) & (self._freqs <= hi_f)
            if mask.sum() > 1:
                vis = self._mag_db[mask]
                rng = vis.max() - vis.min()
                pad = max(rng * 0.06, 3.0)
                y_min = vis.min() - pad
                y_max = vis.max() + pad
                # Expand upward to include any marker labels that stack above the data
                if self._marker_boxes:
                    label_tops = [y_bot + lh
                                  for (_, y_bot, _, lh) in self._marker_boxes]
                    y_max = max(y_max, max(label_tops) + pad * 0.5)
                pi.setYRange(y_min, y_max, padding=0)
                return
        except Exception:
            pass
        pi.enableAutoRange(axis="y")
