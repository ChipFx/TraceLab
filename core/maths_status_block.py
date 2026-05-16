"""
core/maths_status_block.py
Painted status block for Maths traces in the scope status bar.

Shows maths-specific information instead of instrument metadata:
  Row 1  : trace label ("Maths 000")
  Row 2  : expression (truncated to fit)
  Row 3  : source aliases and filter-mode summary

  Badge top-right  : "MATHS"  — fixed identifier colour
  Badge bottom-left: "FILT"   — shown when any input uses post-filter data

Left colour bar and bottom border follow the trace colour, same as
ChannelStatusBlock.  All colours come from the theme palette.

Right-click emits context_menu_requested (same API as ChannelStatusBlock).
Left-click emits edit_requested so the main window can re-open the dialog.
"""

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import (
    QPainter, QColor, QFont, QPen, QBrush, QFontMetrics, QCursor,
)

from pytraceview.trace_model import TraceModel
from core.channel_status_block import (
    BLOCK_W, BLOCK_H, _outlined_text, _contrast_colors,
)


class MathsStatusBlock(QWidget):
    """Status block for a computed (maths) trace."""

    context_menu_requested = pyqtSignal(str, object)   # (trace_name, QPoint)
    edit_requested         = pyqtSignal(str)            # trace_name

    def __init__(
        self,
        trace:   TraceModel,
        recipe,                   # MathsRecipe — imported lazily to avoid cycles
        palette: dict = None,
        parent=None,
    ):
        super().__init__(parent)
        self._trace   = trace
        self._recipe  = recipe
        self._pal     = palette or {}
        self._scale   = 1.0
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
        sources = ", ".join(
            f"{a}={n}" for a, n in r.source_map.items()
        )
        fmodes = ", ".join(
            f"{a}:{m}" for a, m in r.filter_mode.items()
        )
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
        fm = QFontMetrics(f_b)
        bw = fm.horizontalAdvance(badge_txt) + int(8 * s)
        bh = int(14 * s)
        bx = w - bw - int(4 * s)
        by = int(4 * s)
        p.setBrush(QBrush(bg_b))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(bx, by, bw, bh, 2, 2)
        p.setFont(f_b)
        p.setPen(QPen(fg_b))
        p.drawText(bx + int(4 * s), by + bh - 2, badge_txt)

        # ── Row 2: expression (truncated) ─────────────────────────────────
        expr     = self._recipe.expression
        f_expr   = QFont("Courier New", max(6, int(10 * s)))
        f_expr.setBold(False)
        fm_e     = QFontMetrics(f_expr)
        max_w    = w - int(10 * s)
        ellipsis = "…"
        if fm_e.horizontalAdvance(expr) > max_w:
            while expr and fm_e.horizontalAdvance(expr + ellipsis) > max_w:
                expr = expr[:-1]
            expr += ellipsis
        _outlined_text(p, int(5 * s), int(56 * s), expr,
                       f_expr, fill_c, outline_c, 1.0)

        # ── Row 3: sources + filter summary ──────────────────────────────
        aliases     = list(self._recipe.source_map.keys())
        any_filt    = any(
            self._recipe.filter_mode.get(a, "filtered") == "filtered"
            for a in aliases
        )
        row3 = "+".join(aliases) if aliases else "?"
        if self._trace.has_filter:
            row3 += "  ⊛"             # filtered result
        f_r3 = QFont("Courier New", max(6, int(10 * s)))
        f_r3.setBold(True)
        _outlined_text(p, int(5 * s), int(82 * s), row3,
                       f_r3, fill_c, outline_c, 0.8)

        # ── FILT badge (bottom-left): input data was post-filter ──────────
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
