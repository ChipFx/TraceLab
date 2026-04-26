"""
core/scope_status_bar.py
LeCroy-style scope status bar for ChipFX TraceLab.

ALL colour data comes from ThemeManager via set_theme(palette_dict).
No colour constants live here.

Layout: [Logo] | [Time+Trig] | [Ch1][Ch2]...(scrollable)
"""

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QScrollArea, QFrame
)
from PyQt6.QtCore import Qt, pyqtSignal, QRectF, QEvent
from PyQt6.QtGui import QPainter, QColor, QFont, QPen, QPixmap, QCursor
from typing import List

from core.trace_model import TraceModel
from core.channel_status_block import ChannelStatusBlock, BLOCK_H, BLOCK_W, _eng

BAR_H  = BLOCK_H   # 110 px
SEP_W  = 2
INFO_W = 120


def _tdiv(span: float) -> str:
    return "---" if span <= 0 else _eng(span, "s") + "/div"


def _sep_widget(color: str = "#1e1e38") -> QFrame:
    s = QFrame()
    s.setFrameShape(QFrame.Shape.VLine)
    s.setFixedWidth(SEP_W)
    s.setStyleSheet(f"color: {color};")
    return s


# ── Logo block ─────────────────────────────────────────────────────────────────
class LogoBlock(QWidget):
    def __init__(self, palette: dict, parent=None):
        super().__init__(parent)
        self._pal     = palette
        self._pixmap  = None
        self._logo_w  = 200
        self.setFixedSize(self._logo_w, BAR_H)

    def set_svg(self, svg_path: str):
        try:
            from PyQt6.QtSvg import QSvgRenderer
            renderer = QSvgRenderer(svg_path)
            if not renderer.isValid():
                self._pixmap = None; self.update(); return
            vb = renderer.viewBox()
            if vb.width() > 0 and vb.height() > 0:
                self._logo_w = max(80, min(400, int(BAR_H * vb.width() / vb.height())))
            self.setFixedWidth(self._logo_w)
            px = QPixmap(self._logo_w, BAR_H)
            px.fill(QColor(self._pal.get("logo_bg", "#060610")))
            p = QPainter(px)
            renderer.render(p, QRectF(0, 0, self._logo_w, BAR_H))
            p.end()
            self._pixmap = px
        except Exception:
            self._pixmap = None
        self.update()

    def set_palette(self, palette: dict):
        self._pal = palette
        self._pixmap = None   # will redraw as text
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(self._pal.get("logo_bg", "#060610")))
        if self._pixmap:
            p.drawPixmap(0, 0, self._pixmap)
        else:
            f1 = QFont("Courier New", 14); f1.setBold(True)
            p.setFont(f1)
            p.setPen(QPen(QColor(self._pal.get("logo_text", "#F0C040"))))
            p.drawText(8, 38, "TraceLab")
            f2 = QFont("Courier New", 9)
            p.setFont(f2)
            p.setPen(QPen(QColor(self._pal.get("logo_sub", "#555577"))))
            p.drawText(10, 58, "by ChipFX")
        p.end()


# ── Time+Trigger block ──────────────────────────────────────────────────────────
class TimeTrigBlock(QWidget):
    def __init__(self, palette: dict, parent=None):
        super().__init__(parent)
        self._pal      = palette
        self._tdiv_txt = "---"
        self._trig_txt = "---"
        self.setFixedSize(INFO_W, BAR_H)

    def set_tdiv(self, t: str):  self._tdiv_txt = t; self.update()
    def set_trig(self, t: str):  self._trig_txt = t or "---"; self.update()
    def set_palette(self, p: dict): self._pal = p; self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(self._pal.get("info_bg", "#141428")))

        f_dim  = QFont("Courier New", 7)
        f_val  = QFont("Courier New", 11); f_val.setBold(True)
        f_trig = QFont("Courier New", 9);  f_trig.setBold(True)

        p.setFont(f_dim)
        p.setPen(QPen(QColor(self._pal.get("info_dim", "#555577"))))
        p.drawText(6, 16, "TIME BASE")

        p.setFont(f_val)
        p.setPen(QPen(QColor(self._pal.get("info_text", "#d0d0e8"))))
        p.drawText(6, 38, self._tdiv_txt)

        p.setPen(QPen(QColor(self._pal.get("sep", "#1e1e38")), 1))
        p.drawLine(6, 50, w - 6, 50)

        p.setFont(f_dim)
        p.setPen(QPen(QColor(self._pal.get("info_dim", "#555577"))))
        p.drawText(6, 64, "TRIGGER")

        p.setFont(f_trig)
        p.setPen(QPen(QColor(self._pal.get("trig_text", "#44ee66"))))
        txt = self._trig_txt
        if len(txt) > 15:
            mid = txt.rfind(' ', 0, 15) or 15
            p.drawText(6, 82, txt[:mid])
            p.drawText(6, 98, txt[mid:].strip())
        else:
            p.drawText(6, 82, txt)
        p.end()


# ── Main status bar ─────────────────────────────────────────────────────────────
class ScopeStatusBar(QWidget):
    toggle_trace_interp          = pyqtSignal(str)
    trace_context_menu_requested = pyqtSignal(str, object)  # (trace_name, QPoint global)

    def __init__(self, palette: dict, parent=None):
        super().__init__(parent)
        self._pal = dict(palette)
        self._trace_interp_modes: dict = {}
        self._svg_path: str = ""
        self._ch_blocks: list = []

        self.setFixedHeight(BAR_H)
        self._apply_style()

        self._outer = QHBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)

        self._logo = LogoBlock(self._pal)
        self._outer.addWidget(self._logo)
        self._outer.addWidget(_sep_widget(self._pal.get("sep", "#1e1e38")))

        self._timetrig = TimeTrigBlock(self._pal)
        self._outer.addWidget(self._timetrig)
        self._outer.addWidget(_sep_widget(self._pal.get("sep", "#1e1e38")))

        self._ch_scroll = QScrollArea()
        self._ch_scroll.setWidgetResizable(False)
        self._ch_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._ch_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._ch_scroll.setFixedHeight(BAR_H)
        self._ch_container = QWidget()
        self._ch_layout = QHBoxLayout(self._ch_container)
        self._ch_layout.setContentsMargins(0, 0, 0, 0)
        self._ch_layout.setSpacing(SEP_W)
        self._ch_scroll.setWidget(self._ch_container)
        self._outer.addWidget(self._ch_scroll, stretch=1)
        self._update_scroll_style()

        self._statusbar_scroll_enabled = True
        # Intercept wheel events on the scroll area's viewport
        self._ch_scroll.viewport().installEventFilter(self)

    def _apply_style(self):
        bg  = self._pal.get("bar_bg",  "#0a0a14")
        sep = self._pal.get("sep",     "#1e1e38")
        self.setStyleSheet(f"background:{bg}; border-top:2px solid {sep};")

    def _update_scroll_style(self):
        bg  = self._pal.get("bar_bg", "#0a0a14")
        sep = self._pal.get("sep",    "#1e1e38")
        self._ch_scroll.setStyleSheet(
            f"QScrollArea{{border:none;background:{bg};}}"
            f"QScrollBar:horizontal{{height:5px;background:{bg};}}"
            f"QScrollBar::handle:horizontal{{background:{sep};border-radius:2px;}}")
        self._ch_container.setStyleSheet(f"background:{bg};")

    def set_palette(self, palette: dict):
        """Called by main_window when theme changes."""
        self._pal = dict(palette)
        self._apply_style()
        self._update_scroll_style()
        self._logo.set_palette(self._pal)
        if self._svg_path:
            self._logo.set_svg(self._svg_path)
        self._timetrig.set_palette(self._pal)

    # Keep old name for compatibility
    def set_theme(self, theme_name_ignored: str):
        pass   # palette is set via set_palette(); theme_name no longer used here

    def set_statusbar_scroll_enabled(self, enabled: bool):
        self._statusbar_scroll_enabled = enabled

    def eventFilter(self, obj, event):
        if (self._statusbar_scroll_enabled and
                event.type() == QEvent.Type.Wheel):
            self._handle_statusbar_scroll(event)
            return True   # consume; don't let QScrollArea process it
        return False

    def wheelEvent(self, event):
        """Wheel over logo / time-trig area — same handling."""
        if self._statusbar_scroll_enabled:
            self._handle_statusbar_scroll(event)
            event.accept()
        else:
            super().wheelEvent(event)

    def _handle_statusbar_scroll(self, event):
        """Scroll the channel-block area.
        Vertical wheel → smooth horizontal scroll.
        Horizontal wheel (tilt) → snap to next whole block edge."""
        sb  = self._ch_scroll.horizontalScrollBar()
        dx  = event.angleDelta().x()
        dy  = event.angleDelta().y()
        if dx == 0 and dy == 0:
            return
        if abs(dx) > abs(dy) and dx != 0:
            # Tilt left/right: snap to nearest block edge in that direction.
            # dx < 0 → tilt right → scroll right (higher value)
            self._snap_to_block(sb, +1 if dx < 0 else -1)
        elif dy != 0:
            # Vertical wheel: smooth horizontal scroll
            # angleDelta().y() is positive for scroll-up; scroll left for up
            sb.setValue(sb.value() - dy * 3 // 8)

    def _snap_to_block(self, sb, direction: int):
        """Snap scrollbar to the next whole-block boundary.
        direction: +1 = higher value (right), -1 = lower value (left)."""
        step = BLOCK_W + SEP_W
        pos  = sb.value()
        if direction > 0:
            new_pos = (pos // step + 1) * step
        else:
            if pos % step == 0:
                new_pos = max(0, pos - step)
            else:
                new_pos = (pos // step) * step
        sb.setValue(new_pos)

    def set_branding(self, svg_path: str):
        self._svg_path = svg_path
        self._logo.set_svg(svg_path) if svg_path else self._logo.set_palette(self._pal)

    def set_trace_interp_modes(self, modes: dict):
        self._trace_interp_modes = dict(modes)

    def update(self, traces: List[TraceModel],
               x_span: float,
               trigger_info: str = "",
               y_major_divs: dict = None,
               interp_active: bool = False,
               settings: dict = None):
        self._timetrig.set_tdiv(_tdiv(x_span))
        self._timetrig.set_trig(trigger_info)

        # Rebuild channel blocks
        while self._ch_layout.count():
            item = self._ch_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self._ch_blocks.clear()

        y_major_divs = y_major_divs or {}
        visible = [t for t in traces if t.visible]

        for trace in visible:
            y_div = y_major_divs.get(trace.name, 0.0)
            mode  = self._trace_interp_modes.get(trace.name, "linear")
            block = ChannelStatusBlock(
                trace, y_div, mode, self._pal,
                parent=self._ch_container)
            block.toggle_interp.connect(self.toggle_trace_interp)
            block.context_menu_requested.connect(self.trace_context_menu_requested)
            self._ch_blocks.append(block)
            self._ch_layout.addWidget(block)
            block.show()

        n = len(visible)
        w = n * (BLOCK_W + SEP_W) + 4 if n > 0 else 0
        self._ch_container.setFixedWidth(w)
        self._ch_container.setFixedHeight(BAR_H)

    def repaint_channel_blocks(self):
        """Repaint all channel status blocks without rebuilding them.
        Used to reflect model state changes (e.g. extrapolation flags)
        that don't require a full status bar rebuild."""
        for block in self._ch_blocks:
            block.update()
