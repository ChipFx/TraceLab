"""
core/channel_status_block.py
Standalone painted channel-info block for the scope status bar.

Each block shows:
  Row 1: channel name (bold, coloured) + SINC/LIN badge (clickable)
  Row 2: V/div in smart SI units
  Row 3: filter description / coupling / impedance (if set)

Background = trace colour (muted in phosphor theme).
Text uses outlined painting for legibility over any background colour.

Emits toggle_interp(trace_name) when clicked.
"""

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import (
    QPainter, QColor, QFont, QPen, QBrush,
    QPainterPath, QFontMetrics, QCursor
)
from core.trace_model import TraceModel

BLOCK_W = 120
BLOCK_H = 110   # must match BAR_H in scope_status_bar


def _eng(value: float, unit: str) -> str:
    """Compact SI-prefix format."""
    if value == 0:
        return f"0 {unit}"
    abs_v = abs(value)
    for scale, prefix in [
        (1e12, 'T'), (1e9, 'G'), (1e6, 'M'), (1e3, 'k'),
        (1, ''), (1e-3, 'm'), (1e-6, 'µ'), (1e-9, 'n'), (1e-12, 'p'),
    ]:
        if abs_v >= scale * 0.9999:
            s = value / scale
            txt = (f"{s:.0f}" if abs(s) >= 100
                   else f"{s:.1f}".rstrip('0').rstrip('.')
                   if abs(s) >= 10
                   else f"{s:.2f}".rstrip('0').rstrip('.'))
            return f"{txt} {prefix}{unit}"
    return f"{value:.3e} {unit}"


def _outlined_text(painter: QPainter, x: int, y: int, text: str,
                    font: QFont, fill: QColor, outline: QColor,
                    outline_w: float = 1.5):
    """Draw text with contrasting stroke outline for legibility."""
    path = QPainterPath()
    path.addText(x, y, font, text)
    pen = QPen(outline, outline_w * 2)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.strokePath(path, pen)
    painter.fillPath(path, QBrush(fill))


class ChannelStatusBlock(QWidget):
    """
    Painted channel block widget.
    Instantiate one per visible trace; add to a QHBoxLayout.
    """
    toggle_interp = pyqtSignal(str)   # emits trace.name

    def __init__(self, trace: TraceModel,
                 y_span: float = 0.0,
                 interp_mode: str = "linear",
                 theme_name: str = "dark",
                 parent=None):
        super().__init__(parent)
        self._trace = trace
        self._y_span = y_span
        self._interp_mode = interp_mode
        self._theme_name = theme_name
        self.setFixedSize(BLOCK_W, BLOCK_H)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setToolTip(
            f"Channel: {trace.label}\n"
            f"Interpolation: {'Sinc (sin(x)/x)' if interp_mode == 'sinc' else 'Linear'}\n"
            f"Click to toggle interpolation")

    def mousePressEvent(self, event):
        self.toggle_interp.emit(self._trace.name)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        w, h = self.width(), self.height()

        # ── Background ───────────────────────────────────────────────────
        if self._theme_name == "rs_green":
            bg = QColor("#001800")
        else:
            bg = QColor(self._trace.color)

        painter.fillRect(0, 0, w, h, bg)

        # ── Text colours: adapt to background luminance ───────────────────
        if self._theme_name == "rs_green":
            fill_c    = QColor("#00ee44")
            outline_c = QColor("#000800")
        else:
            lum = (bg.red() * 299 + bg.green() * 587 + bg.blue() * 114) / 1000
            if lum > 128:
                fill_c    = QColor("#000000")
                outline_c = QColor("#ffffff")
            else:
                fill_c    = QColor("#ffffff")
                outline_c = QColor("#000000")

        # ── Row 1: channel name ───────────────────────────────────────────
        f_name = QFont("Courier New", 11)
        f_name.setBold(True)
        _outlined_text(painter, 6, 26, self._trace.label,
                        f_name, fill_c, outline_c, 1.5)

        # ── SINC/LIN badge (top-right corner) ────────────────────────────
        sinc = (self._interp_mode == "sinc")
        badge_txt = "SINC" if sinc else "LIN"
        badge_bg  = QColor("#cc2222") if sinc else QColor(0, 0, 0, 70)
        badge_fg  = QColor("#ffffff")
        f_badge = QFont("Courier New", 7)
        f_badge.setBold(True)
        fm = QFontMetrics(f_badge)
        bw = fm.horizontalAdvance(badge_txt) + 8
        bh = 14
        bx = w - bw - 4
        by = 4
        painter.setBrush(QBrush(badge_bg))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(bx, by, bw, bh, 2, 2)
        painter.setFont(f_badge)
        painter.setPen(QPen(badge_fg))
        painter.drawText(bx + 4, by + bh - 2, badge_txt)

        # ── Row 2: V/div ─────────────────────────────────────────────────
        unit = getattr(self._trace, 'unit', '') or ''
        if self._y_span > 0 and unit and unit != 'raw':
            vdiv_txt = _eng(self._y_span / 10.0, unit) + "/div"
        else:
            vdiv_txt = "---/div"
        f_vdiv = QFont("Courier New", 9)
        f_vdiv.setBold(True)
        _outlined_text(painter, 6, 52, vdiv_txt,
                        f_vdiv, fill_c, outline_c, 1.0)

        # ── Row 3: filter / coupling info ────────────────────────────────
        filt     = getattr(self._trace, '_filter_desc', '') or ''
        coupling = getattr(self._trace, 'coupling', '') or ''
        imp      = getattr(self._trace, 'impedance', '') or ''
        extra    = "  ".join(p for p in [coupling, imp, filt] if p)
        if extra:
            if self._theme_name == "rs_green":
                filt_c = QColor("#aaee00")
            elif self._theme_name == "light":
                filt_c = QColor("#884400")
            else:
                filt_c = QColor("#ffaa44")
            f_extra = QFont("Courier New", 8)
            painter.setFont(f_extra)
            painter.setPen(QPen(filt_c))
            painter.drawText(6, 74, extra)

        # ── Bottom border in trace colour (or phosphor green) ─────────────
        if self._theme_name == "rs_green":
            border_c = QColor("#00ee44")
        else:
            border_c = QColor(self._trace.color)
            border_c = border_c.lighter(130) if border_c.value() < 80 else border_c
        painter.setPen(QPen(border_c, 3))
        painter.drawLine(0, h - 2, w, h - 2)

        painter.end()
