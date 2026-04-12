"""
core/channel_status_block.py
Painted channel-info block for the scope status bar.

V/div is computed from the ACTUAL major tick spacing of the lane's Y axis,
not a simple view_range/10 approximation.

Text is drawn larger (font size × 1.3) with a thinner outline (0.8× previous)
so text dominates over its own outline.
"""

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import (
    QPainter, QColor, QFont, QPen, QBrush,
    QPainterPath, QFontMetrics, QCursor
)
from core.trace_model import TraceModel

BLOCK_W = 120
BLOCK_H = 110


def _eng(value: float, unit: str) -> str:
    if value == 0:
        return f"0 {unit}"
    abs_v = abs(value)
    for scale, prefix in [
        (1e12,'T'),(1e9,'G'),(1e6,'M'),(1e3,'k'),
        (1,''),(1e-3,'m'),(1e-6,'µ'),(1e-9,'n'),(1e-12,'p'),
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
                    outline_w: float = 1.2):
    """Draw text with contrasting stroke outline. Outline is thin so text wins."""
    path = QPainterPath()
    path.addText(x, y, font, text)
    pen = QPen(outline, outline_w * 2)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.strokePath(path, pen)
    painter.fillPath(path, QBrush(fill))


def _get_phosphor_colors(settings: dict) -> tuple:
    """Return (fg_hex, bg_hex) for phosphor theme from settings or defaults."""
    fg = settings.get("phosphor_fg", "#00ee44") if settings else "#00ee44"
    bg = settings.get("phosphor_bg", "#001800") if settings else "#001800"
    return fg, bg


class ChannelStatusBlock(QWidget):
    toggle_interp = pyqtSignal(str)

    def __init__(self, trace: TraceModel,
                 y_major_div: float = 0.0,   # actual major Y tick spacing
                 interp_mode: str = "linear",
                 theme_name: str = "dark",
                 settings: dict = None,
                 parent=None):
        super().__init__(parent)
        self._trace       = trace
        self._y_major_div = y_major_div   # volts per major division
        self._interp_mode = interp_mode
        self._theme_name  = theme_name
        self._settings    = settings or {}
        self.setFixedSize(BLOCK_W, BLOCK_H)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        mode_lbl = {"linear": "Linear", "sinc": "Sinc (sin(x)/x)",
                    "cubic": "Cubic spline"}.get(interp_mode, interp_mode)
        self.setToolTip(
            f"Channel: {trace.label}\n"
            f"Interpolation: {mode_lbl}\n"
            f"Click to toggle interpolation")

    def mousePressEvent(self, event):
        self.toggle_interp.emit(self._trace.name)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        w, h = self.width(), self.height()

        # ── Background ────────────────────────────────────────────────────
        is_phosphor = (self._theme_name == "rs_green")
        if is_phosphor:
            _, ph_bg = _get_phosphor_colors(self._settings)
            bg = QColor(ph_bg)
        elif self._theme_name == "print":
            bg = QColor("#f0f4ff")   # near-white, slight blue tint
        else:
            bg = QColor(self._trace.color)

        painter.fillRect(0, 0, w, h, bg)

        # ── Text colours ──────────────────────────────────────────────────
        if is_phosphor:
            ph_fg, _ = _get_phosphor_colors(self._settings)
            fill_c    = QColor(ph_fg)
            outline_c = QColor("#000800")
        elif self._theme_name == "print":
            fill_c    = QColor("#000044")
            outline_c = QColor("#ffffff")
        else:
            lum = (bg.red() * 299 + bg.green() * 587 + bg.blue() * 114) / 1000
            if lum > 128:
                fill_c    = QColor("#000000")
                outline_c = QColor("#ffffff")
            else:
                fill_c    = QColor("#ffffff")
                outline_c = QColor("#000000")

        # ── Row 1: channel name (larger, bolder) ──────────────────────────
        f_name = QFont("Courier New", 13)   # was 11
        f_name.setBold(True)
        _outlined_text(painter, 5, 28, self._trace.label,
                        f_name, fill_c, outline_c, 1.2)  # outline was 1.5

        # ── SINC/LIN/CUB badge (top-right corner) ────────────────────────
        badge_map = {"sinc": ("SINC", "#cc2222"), "cubic": ("CUB", "#8822cc")}
        badge_txt, badge_col = badge_map.get(
            self._interp_mode, ("LIN", None))
        badge_bg = QColor(badge_col) if badge_col else QColor(0, 0, 0, 70)
        badge_fg = QColor("#ffffff")
        f_badge  = QFont("Courier New", 7)
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

        # ── Row 2: V/div (actual major tick spacing) ─────────────────────
        unit = getattr(self._trace, 'unit', '') or ''
        if self._y_major_div > 0 and unit and unit != 'raw':
            vdiv_txt = _eng(self._y_major_div, unit) + "/div"
        else:
            vdiv_txt = "---/div"
        f_vdiv = QFont("Courier New", 11)   # was 9
        f_vdiv.setBold(True)
        _outlined_text(painter, 5, 58, vdiv_txt,
                        f_vdiv, fill_c, outline_c, 1.0)  # outline was 1.0

        # ── Row 3: filter / coupling info (larger, outlined) ─────────────
        filt     = getattr(self._trace, '_filter_desc', '') or ''
        coupling = getattr(self._trace, 'coupling', '') or ''
        imp      = getattr(self._trace, 'impedance', '') or ''
        extra    = "  ".join(p for p in [coupling, imp, filt] if p)
        if extra:
            if is_phosphor:
                ph_fg, _ = _get_phosphor_colors(self._settings)
                filt_c = QColor(ph_fg).lighter(120)
            elif self._theme_name in ("light", "print"):
                filt_c = QColor("#663300")
            else:
                filt_c = QColor("#ffcc66")
            f_extra = QFont("Courier New", 10)   # was 8, plain
            f_extra.setBold(True)
            _outlined_text(painter, 5, 84, extra,
                            f_extra, filt_c, outline_c, 0.8)

        # ── Left colour bar ───────────────────────────────────────────────
        if is_phosphor:
            ph_fg, _ = _get_phosphor_colors(self._settings)
            bar_c = QColor(ph_fg)
        elif self._theme_name == "print":
            bar_c = QColor("#0000cc")
        else:
            bar_c = QColor(self._trace.color)
        painter.setBrush(QBrush(bar_c))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(0, 0, 5, h)

        # ── Bottom border ─────────────────────────────────────────────────
        painter.setPen(QPen(bar_c, 3))
        painter.drawLine(0, h - 2, w, h - 2)

        painter.end()
