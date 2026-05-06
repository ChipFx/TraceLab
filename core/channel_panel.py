"""
core/channel_panel.py
TraceLab-specific channel panel — extends pytraceview's ChannelPanel with
oscilloscope interpolation mode controls (All Lin / All Cub / All Sinc).

Everything else (groups, drag-to-reorder, palette, grouping dialog) lives in
pytraceview/channel_panel.py.
"""

from PyQt6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout
from PyQt6.QtCore import pyqtSignal

from pytraceview.channel_panel import ChannelPanel as _ChannelPanelBase

# Re-export ChannelRow and _ChannelGroupHeader so any TraceLab code that
# imported them from this module continues to work without changes.
from pytraceview.channel_panel import ChannelRow, _ChannelGroupHeader  # noqa: F401


class ChannelPanel(_ChannelPanelBase):
    """TraceLab ChannelPanel: base panel + "All Lin / All Cub / All Sinc" row."""

    def _setup_extra_button_rows(self, layout: QVBoxLayout):
        """Add the interpolation button row above the group controls."""
        ctrl2 = QHBoxLayout()
        ctrl2.setContentsMargins(4, 0, 4, 4)
        ctrl2.setSpacing(3)
        self._btn_lin  = QPushButton("All Lin")
        self._btn_cub  = QPushButton("All Cub")
        self._btn_sinc = QPushButton("All Sinc")
        self._btn_lin.setToolTip("Set all channels to Linear interpolation")
        self._btn_cub.setToolTip("Set all channels to Cubic Spline interpolation")
        self._btn_sinc.setToolTip("Set all channels to Sinc (sin(x)/x) interpolation")
        self._btn_lin.clicked.connect(lambda: self._set_all_interp("linear"))
        self._btn_cub.clicked.connect(lambda: self._set_all_interp("cubic"))
        self._btn_sinc.clicked.connect(lambda: self._set_all_interp("sinc"))
        ctrl2.addWidget(self._btn_lin)
        ctrl2.addWidget(self._btn_cub)
        ctrl2.addWidget(self._btn_sinc)
        layout.addLayout(ctrl2)

    def _set_all_interp(self, mode: str):
        for row in self._rows.values():
            row.trace._interp_mode_override = mode
            self.interp_changed.emit(row.trace.name, mode)

    def _apply_button_styles(self):
        """Style base buttons, then the interp buttons if they exist yet."""
        super()._apply_button_styles()
        if not hasattr(self, '_btn_lin'):
            return  # called from base __init__ before our hook runs
        fs         = max(8, int(round(11 * self._font_scale * 0.9)))
        sinc_color = self._pv.get("interp_sinc_color", "#ff8888")
        cub_color  = self._pv.get("interp_cub_color",  "#cc88ff")
        self._btn_lin.setStyleSheet(
            f"font-size: {fs}px; font-weight: bold;")
        self._btn_cub.setStyleSheet(
            f"font-size: {fs}px; font-weight: bold; color: {cub_color};")
        self._btn_sinc.setStyleSheet(
            f"font-size: {fs}px; font-weight: bold; color: {sinc_color};")

    def _update_minimum_width(self):
        """Minimum width is constrained by the wider interp button row."""
        if not hasattr(self, '_btn_lin'):
            super()._update_minimum_width()
            return
        for btn in (self._btn_lin, self._btn_cub, self._btn_sinc):
            btn.ensurePolished()
        # ctrl2 layout: 4px left margin + 3px spacing × 2 + 4px right margin = 14
        needed = (self._btn_lin.sizeHint().width()
                  + self._btn_cub.sizeHint().width()
                  + self._btn_sinc.sizeHint().width()
                  + 14)
        self.setMinimumWidth(needed)
