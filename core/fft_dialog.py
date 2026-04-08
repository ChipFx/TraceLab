"""
core/fft_dialog.py
FFT analysis dialog.
- Y auto-range to visible data by default
- X starts at fft_min_freq (default 1 Hz), configurable
"""

import numpy as np
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QComboBox, QLabel,
    QPushButton, QRadioButton, QButtonGroup, QDoubleSpinBox,
    QGroupBox, QGridLayout
)
from PyQt6.QtCore import Qt
import pyqtgraph as pg
from typing import List, Optional, Tuple
from core.trace_model import TraceModel


WINDOWS = {
    "Hanning": np.hanning,
    "Hamming": np.hamming,
    "Blackman": np.blackman,
    "Rectangular": np.ones,
    "Flat Top": lambda n: np.ones(n),
}


def compute_fft(y: np.ndarray, sample_rate: float,
                window_name: str = "Hanning") -> Tuple[np.ndarray, np.ndarray]:
    n = len(y)
    if n < 4:
        return np.array([1e-10]), np.array([-120.0])
    win_fn = WINDOWS.get(window_name, np.hanning)
    win = win_fn(n)
    y_w = (y - np.mean(y)) * win
    fft_result = np.fft.rfft(y_w)
    freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate)
    mag = np.abs(fft_result) / (n / 2)
    mag[0] /= 2
    mag_db = 20 * np.log10(np.maximum(mag, 1e-12))
    freqs = np.maximum(freqs, 1e-10)
    return freqs, mag_db


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
        self.resize(920, 520)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        ctrl = QHBoxLayout()

        ctrl.addWidget(QLabel("Trace:"))
        self.combo_trace = QComboBox()
        for t in self.traces:
            self.combo_trace.addItem(t.label, t.name)
        ctrl.addWidget(self.combo_trace)

        ctrl.addWidget(QLabel("Window:"))
        self.combo_window = QComboBox()
        self.combo_window.addItems(list(WINDOWS.keys()))
        self.combo_window.setCurrentText("Hanning")
        ctrl.addWidget(self.combo_window)

        self.radio_all = QRadioButton("All data")
        self.radio_win = QRadioButton("Windowed view")
        self.radio_all.setChecked(True)
        if self.view_range is None:
            self.radio_win.setEnabled(False)
        ctrl.addWidget(self.radio_all)
        ctrl.addWidget(self.radio_win)

        ctrl.addWidget(QLabel("Min freq (Hz):"))
        self.spin_min_freq = QDoubleSpinBox()
        self.spin_min_freq.setRange(0.0, 1e12)
        self.spin_min_freq.setDecimals(3)
        self.spin_min_freq.setValue(self.fft_min_freq)
        self.spin_min_freq.setFixedWidth(80)
        ctrl.addWidget(self.spin_min_freq)

        btn_compute = QPushButton("Compute FFT")
        btn_compute.clicked.connect(self._compute)
        btn_compute.setStyleSheet(
            "background: #2060c0; color: white; padding: 4px 12px;")
        ctrl.addWidget(btn_compute)
        ctrl.addStretch()
        layout.addLayout(ctrl)

        self.plot = pg.PlotWidget(background="#050508")
        pi = self.plot.getPlotItem()
        pi.setLabel("bottom", "Frequency (Hz)")
        pi.setLabel("left", "Magnitude (dBFS)")
        pi.showGrid(x=True, y=True, alpha=0.3)
        pi.setLogMode(x=True, y=False)
        for ax in ("left", "bottom"):
            ax_obj = pi.getAxis(ax)
            ax_obj.setPen(pg.mkPen(color="#e0e0e0"))
            ax_obj.setTextPen(pg.mkPen(color="#e0e0e0"))
        self.plot.addLegend()
        layout.addWidget(self.plot)

        layout.addWidget(QLabel(
            "X axis is log-frequency. Zoom and pan work normally."))

        self._compute()

    def _compute(self):
        pi = self.plot.getPlotItem()
        pi.clear()
        self.plot.addLegend()

        trace_name = self.combo_trace.currentData()
        window_name = self.combo_window.currentText()
        use_window = self.radio_win.isChecked() and self.view_range is not None
        min_freq = max(self.spin_min_freq.value(), 1e-10)

        for trace in self.traces:
            if trace.name != trace_name:
                continue
            if use_window and self.view_range:
                t, y = trace.windowed_data(*self.view_range)
            else:
                y = trace.processed_data

            if len(y) < 4:
                continue

            freqs, mag_db = compute_fft(y, trace.sample_rate, window_name)

            # Clip to min frequency
            mask = freqs >= min_freq
            freqs = freqs[mask]
            mag_db = mag_db[mask]

            if len(freqs) == 0:
                continue

            pen = pg.mkPen(color=trace.color, width=1.5)
            self.plot.plot(freqs, mag_db, pen=pen, name=trace.label)

        # Auto-range to visible data only
        pi.enableAutoRange()
