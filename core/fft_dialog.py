"""
core/fft_dialog.py
FFT analysis dialog.
"""

import numpy as np
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QComboBox, QLabel,
    QCheckBox, QPushButton, QRadioButton, QButtonGroup, QGroupBox,
    QGridLayout
)
from PyQt6.QtCore import Qt
import pyqtgraph as pg
from typing import List
from core.trace_model import TraceModel


WINDOWS = {
    "Rectangular": np.ones,
    "Hanning": np.hanning,
    "Hamming": np.hamming,
    "Blackman": np.blackman,
    "Flat Top": lambda n: np.ones(n),  # placeholder
}


def compute_fft(y: np.ndarray, sample_rate: float, window_name: str = "Hanning"):
    """Compute FFT magnitude spectrum in dB."""
    n = len(y)
    if n < 4:
        return np.array([0.0]), np.array([0.0])

    win_fn = WINDOWS.get(window_name, np.hanning)
    win = win_fn(n)
    y_w = y * win

    fft_result = np.fft.rfft(y_w)
    freqs = np.fft.rfftfreq(n, d=1.0/sample_rate)
    mag = np.abs(fft_result) / (n / 2)
    mag[0] /= 2  # DC
    mag_db = 20 * np.log10(np.maximum(mag, 1e-12))
    return freqs, mag_db


class FFTDialog(QDialog):
    def __init__(self, traces: List[TraceModel],
                  view_range=None, parent=None):
        super().__init__(parent)
        self.traces = [t for t in traces if t.visible]
        self.view_range = view_range  # (t_start, t_end) or None
        self.setWindowTitle("FFT Analysis")
        self.resize(900, 500)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Controls
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

        btn_compute = QPushButton("Compute FFT")
        btn_compute.clicked.connect(self._compute)
        btn_compute.setStyleSheet(
            "background: #2060c0; color: white; padding: 4px 12px;")
        ctrl.addWidget(btn_compute)
        ctrl.addStretch()
        layout.addLayout(ctrl)

        # Plot
        self.plot = pg.PlotWidget(background="#050508")
        pi = self.plot.getPlotItem()
        pi.setLabel("bottom", "Frequency (Hz)")
        pi.setLabel("left", "Magnitude (dB)")
        pi.showGrid(x=True, y=True, alpha=0.3)
        pi.setLogMode(x=True, y=False)
        for ax in ("left", "bottom"):
            ax_obj = pi.getAxis(ax)
            ax_obj.setPen(pg.mkPen(color="#e0e0e0"))
            ax_obj.setTextPen(pg.mkPen(color="#e0e0e0"))
        self.plot.addLegend()
        layout.addWidget(self.plot)

        layout.addWidget(QLabel(
            "Tip: X axis is log-frequency. Zoom/pan normally."))

        # Auto-compute on open
        self._compute()

    def _compute(self):
        self.plot.getPlotItem().clear()
        self.plot.addLegend()

        trace_name = self.combo_trace.currentData()
        window_name = self.combo_window.currentText()
        use_window = self.radio_win.isChecked() and self.view_range is not None

        traces_to_plot = [t for t in self.traces
                          if t.name == trace_name or trace_name is None]

        # Actually just plot selected trace
        for trace in self.traces:
            if trace.name != trace_name:
                continue
            if use_window and self.view_range:
                t, y = trace.windowed_data(*self.view_range)
            else:
                t = trace.time_axis
                y = trace.processed_data

            if len(y) < 4:
                continue

            freqs, mag_db = compute_fft(y, trace.sample_rate, window_name)

            # Avoid log(0) issues
            freqs = np.maximum(freqs, 1e-10)

            pen = pg.mkPen(color=trace.color, width=1.5)
            self.plot.plot(freqs, mag_db, pen=pen, name=trace.label)
