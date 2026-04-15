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
    QPushButton, QRadioButton, QDoubleSpinBox,
    QFrame, QCheckBox, QSizePolicy
)
from PyQt6.QtCore import Qt
import pyqtgraph as pg
from pyqtgraph import InfiniteLine
from typing import List, Optional, Tuple
from core.trace_model import TraceModel

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


# ── Dialog ────────────────────────────────────────────────────────────────────

class FFTDialog(QDialog):
    def __init__(self, traces: List[TraceModel],
                 view_range: Optional[Tuple] = None,
                 fft_min_freq: float = 1.0,
                 parent=None):
        super().__init__(parent)
        self.traces = [t for t in traces if t.visible]
        self.view_range = view_range
        self.fft_min_freq = fft_min_freq
        self.setWindowTitle("FFT Analysis")
        self.resize(1140, 680)

        # FFT result (current compute)
        self._freqs:  Optional[np.ndarray] = None
        self._mag_db: Optional[np.ndarray] = None

        # Cursor state — actual frequencies (not log)
        self._cursor_freq: dict = {0: None, 1: None}
        self._cursor_lines: dict = {}   # cursor_id -> InfiniteLine

        # Peak markers (list of plot items)
        self._peak_markers: list = []

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

        ctrl.addWidget(QLabel("Min freq (Hz):"))
        self.spin_min_freq = QDoubleSpinBox()
        self.spin_min_freq.setRange(0.0, 1e12)
        self.spin_min_freq.setDecimals(3)
        self.spin_min_freq.setValue(self.fft_min_freq)
        self.spin_min_freq.setFixedWidth(90)
        ctrl.addWidget(self.spin_min_freq)
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

        btn_mark = QPushButton(f"Mark Top {_N_MARK_PEAKS} Peaks")
        btn_mark.setToolTip("Mark the highest amplitude peaks on the plot")
        btn_mark.clicked.connect(self._mark_peaks)
        tool.addWidget(btn_mark)
        btn_clear = QPushButton("Clear Markers")
        btn_clear.clicked.connect(self._clear_markers)
        tool.addWidget(btn_clear)

        tool.addWidget(self._vsep())

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

        tool.addStretch()
        root.addLayout(tool)

        # ── Plot ──────────────────────────────────────────────────────────────
        self.plot = pg.PlotWidget(background="#050508")
        pi = self.plot.getPlotItem()
        pi.setMenuEnabled(False)   # suppress pyqtgraph's built-in export menu
        pi.setLabel("bottom", "Frequency (Hz)")
        pi.setLabel("left", "Magnitude (dBFS)")
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

    @staticmethod
    def _vsep() -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        return sep

    # ── FFT computation ───────────────────────────────────────────────────────

    def _compute(self):
        pi = self.plot.getPlotItem()
        # pi.clear() removes all items — clear stale references before it
        self._cursor_lines.clear()
        self._peak_markers.clear()
        pi.clear()
        self.plot.addLegend()

        trace_name = self.combo_trace.currentData()
        window_name = self.combo_window.currentText()
        use_window = self.radio_win.isChecked() and self.view_range is not None
        min_freq = max(self.spin_min_freq.value(), 1e-10)

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
        return _find_fft_peaks(self._mag_db)

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
        if self._freqs is None or self._mag_db is None:
            return
        peaks = self._get_peaks()
        if not len(peaks):
            return
        # Take top-N by amplitude
        n = min(_N_MARK_PEAKS, len(peaks))
        top_idx = np.argsort(self._mag_db[peaks])[-n:][::-1]
        for idx in peaks[top_idx]:
            f = float(self._freqs[idx])
            a = float(self._mag_db[idx])
            log_f = np.log10(f)
            line = pg.InfiniteLine(
                pos=log_f, angle=90,
                pen=pg.mkPen(color=_PEAK_COLOR, width=1,
                              style=Qt.PenStyle.DotLine))
            self.plot.addItem(line)
            txt = pg.TextItem(
                text=_fmt_freq(f), color=_PEAK_COLOR, anchor=(0.5, 1.15))
            txt.setPos(log_f, a)
            self.plot.addItem(txt)
            self._peak_markers.extend([line, txt])

    def _clear_markers(self):
        for item in self._peak_markers:
            try:
                self.plot.removeItem(item)
            except Exception:
                pass
        self._peak_markers.clear()

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
                pi.setYRange(vis.min() - pad, vis.max() + pad, padding=0)
                return
        except Exception:
            pass
        pi.enableAutoRange(axis="y")
