"""
core/maths_status_block.py
Painted status block for Maths traces in the scope status bar.

Layout mirrors ChannelStatusBlock so the two types sit together cleanly:
  Row 1 (y=28)  : trace label  ("Maths 000")  — bold 13, outline 1.2
  Row 2 (y=58)  : unit/div     ("{unit}/div")  — bold 11, outline 1.0
  Row 3 (y=84)  : expression   (truncated)     — bold 10, outline 0.8

  Badge top-right  : "MATHS"
  Badge bottom-left: "FILT"  when any input uses post-filter data

Left colour bar and bottom border follow the trace colour.
All colours come from the theme palette — no hardcoded hex values.
"""

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import (
    QPainter, QColor, QFont, QPen, QBrush, QFontMetrics, QCursor,
)

from pytraceview.trace_model import TraceModel
from core.channel_status_block import (
    BLOCK_W, BLOCK_H, _outlined_text, _contrast_colors, _eng,
)


class MathsStatusBlock(QWidget):
    """Status block for a computed (maths) trace."""

    context_menu_requested = pyqtSignal(str, object)   # (trace_name, QPoint)
    edit_requested         = pyqtSignal(str)            # trace_name

    def __init__(
        self,
        trace:       TraceModel,
        recipe,                        # MathsRecipe
        y_major_div: float = 0.0,
        palette:     dict  = None,
        parent=None,
    ):
        super().__init__(parent)
        self._trace       = trace
        self._recipe      = recipe
        self._y_major_div = y_major_div
        self._pal         = palette or {}
        self._scale       = 1.0
        self.setFixedSize(BLOCK_W, BLOCK_H)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._build_tooltip()
        self._apply_tooltip_style()

    # ── Public API ─────────────────────────────────────────────────────────────

    def set_palette(self, palette: dict):
        self._pal = dict(palette)
        self._apply_tooltip_style()
        self.update()

    def set_scale(self, scale: float):
        self._scale = max(0.5, float(scale))
        self.setFixedSize(
            max(60, int(BLOCK_W * self._scale)),
            max(55, int(BLOCK_H * self._scale)),
        )
        self.update()

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _build_tooltip(self):
        r = self._recipe
        sources = ", ".join(f"{a}={n}" for a, n in r.source_map.items())
        fmodes  = ", ".join(f"{a}:{m}" for a, m in r.filter_mode.items())
        self.setToolTip(
            f"Maths: {self._trace.label}\n"
            f"Expression: {r.expression}\n"
            f"Sources: {sources}\n"
            f"Data mode: {fmodes}\n"
            f"Left-click to edit"
        )

    def _apply_tooltip_style(self):
        bg  = self._pal.get("info_bg",   "#141428")
        fg  = self._pal.get("info_text", "#d0d0e8")
        bdr = self._pal.get("sep",       "#1e1e38")
        self.setStyleSheet(
            f"QToolTip {{ color: {fg}; background-color: {bg}; "
            f"border: 1px solid {bdr}; padding: 3px 6px; }}")

    def _unit_div_text(self) -> str:
        unit = getattr(self._trace, "unit", "") or ""
        if self._y_major_div > 0 and unit and unit != "raw":
            return _eng(self._y_major_div, unit) + "/div"
        return "---/div"

    def _truncate(self, text: str, font: QFont, max_px: int) -> str:
        from PyQt6.QtGui import QFontMetrics
        fm = QFontMetrics(font)
        if fm.horizontalAdvance(text) <= max_px:
            return text
        ellipsis = "..."
        while text and fm.horizontalAdvance(text + ellipsis) > max_px:
            text = text[:-1]
        return text + ellipsis

    # ── Events ─────────────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.context_menu_requested.emit(
                self._trace.name, event.globalPosition().toPoint())
        else:
            self.edit_requested.emit(self._trace.name)

    # ── Painting ───────────────────────────────────────────────────────────────

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        w, h = self.width(), self.height()
        s = self._scale

        # Background
        if "ch_bg" in self._pal:
            bg = QColor(self._pal["ch_bg"])
        else:
            bg = QColor(self._trace.color)
        p.fillRect(0, 0, w, h, bg)

        fill_c, outline_c = _contrast_colors(bg, self._pal)

        # ── Row 1: label ──────────────────────────────────────────────────
        f_name = QFont("Courier New", max(6, int(13 * s)))
        f_name.setBold(True)
        _outlined_text(p, int(5 * s), int(28 * s), self._trace.label,
                       f_name, fill_c, outline_c, 1.2)

        # ── MATHS badge (top-right) ───────────────────────────────────────
        badge_txt = "MATHS"
        bg_b = QColor(self._pal.get("badge_maths_bg", "#886600"))
        fg_b = QColor(self._pal.get("badge_maths_fg", "#ffffff"))
        f_b  = QFont("Courier New", max(5, int(7 * s)))
        f_b.setBold(True)
        fm_b = QFontMetrics(f_b)
        bw = fm_b.horizontalAdvance(badge_txt) + int(8 * s)
        bh = int(14 * s)
        bx = w - bw - int(4 * s)
        by = int(4 * s)
        p.setBrush(QBrush(bg_b))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(bx, by, bw, bh, 2, 2)
        p.setFont(f_b)
        p.setPen(QPen(fg_b))
        p.drawText(bx + int(4 * s), by + bh - 2, badge_txt)

        # ── Row 2: unit/div — same style as ChannelStatusBlock row 2 ─────
        f_vdiv = QFont("Courier New", max(6, int(11 * s)))
        f_vdiv.setBold(True)
        _outlined_text(p, int(5 * s), int(58 * s), self._unit_div_text(),
                       f_vdiv, fill_c, outline_c, 1.0)

        # ── Row 3: expression — same style as ChannelStatusBlock row 3 ───
        f_expr  = QFont("Courier New", max(6, int(10 * s)))
        f_expr.setBold(True)
        max_px  = w - int(10 * s)
        expr    = self._truncate(self._recipe.expression, f_expr, max_px)
        _outlined_text(p, int(5 * s), int(84 * s), expr,
                       f_expr, fill_c, outline_c, 0.8)

        # ── FILT badge (bottom-left): any input uses post-filter data ─────
        aliases  = list(self._recipe.source_map.keys())
        any_filt = any(
            self._recipe.filter_mode.get(a, "filtered") == "filtered"
            for a in aliases
        )
        if any_filt:
            ft_txt = "FILT"
            f_ft   = QFont("Courier New", max(5, int(7 * s)))
            f_ft.setBold(True)
            fm_ft  = QFontMetrics(f_ft)
            fw     = fm_ft.horizontalAdvance(ft_txt) + int(8 * s)
            fh_    = int(14 * s)
            fx     = int(4 * s)
            fy     = h - fh_ - int(4 * s)
            p.setBrush(QBrush(QColor(self._pal.get("badge_filt_bg", "#1a6622"))))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(fx, fy, fw, fh_, 2, 2)
            p.setFont(f_ft)
            p.setPen(QPen(QColor(self._pal.get("badge_filt_fg", "#aaffaa"))))
            p.drawText(fx + int(4 * s), fy + fh_ - 2, ft_txt)

        # ── Left colour bar ───────────────────────────────────────────────
        bar_c = QColor(self._pal.get("ch_bar", self._trace.color))
        p.setBrush(QBrush(bar_c))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRect(0, 0, int(5 * s), h)

        # ── Bottom border ─────────────────────────────────────────────────
        p.setPen(QPen(bar_c, max(1, int(3 * s))))
        p.drawLine(0, h - 2, w, h - 2)

        p.end()
