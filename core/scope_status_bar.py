"""
core/scope_status_bar.py
LeCroy-style scope status bar for ChipFX TraceLab.

Layout (left → right):
  [Logo — width from SVG aspect ratio × BAR_H] |
  [Time+Trig block — fixed BLOCK_W] |
  [Ch1 block][Ch2 block]... (scrollable QScrollArea)

Channel blocks are ChannelStatusBlock instances from channel_status_block.py.
The scroll area is the only stretch element.
"""

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QScrollArea, QFrame, QLabel, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, QRectF
from PyQt6.QtGui import QPainter, QColor, QFont, QPen, QPixmap, QCursor
from typing import List

from core.trace_model import TraceModel
from core.channel_status_block import ChannelStatusBlock, BLOCK_H, BLOCK_W, _eng

BAR_H   = BLOCK_H          # 110 px
SEP_W   = 2
INFO_W  = 120               # Time+Trig block width
DEFAULT_LOGO_W = 200        # fallback if no SVG loaded


# ── Theme palettes ─────────────────────────────────────────────────────────────
_THEME = {
    "dark": {
        "bar_bg":    "#0a0a14",
        "info_bg":   "#141428",
        "info_text": "#d0d0e8",
        "info_dim":  "#555577",
        "trig_text": "#44ee66",
        "sep":       "#1e1e38",
        "logo_bg":   "#060610",
        "logo_text": "#F0C040",
        "logo_sub":  "#555577",
    },
    "light": {
        "bar_bg":    "#d8d8ec",
        "info_bg":   "#c4c4dc",
        "info_text": "#181828",
        "info_dim":  "#8888aa",
        "trig_text": "#006622",
        "sep":       "#aaaacc",
        "logo_bg":   "#d0d0ec",
        "logo_text": "#2244aa",
        "logo_sub":  "#8888aa",
    },
    "rs_green": {
        "bar_bg":    "#000800",
        "info_bg":   "#001800",
        "info_text": "#00dd44",
        "info_dim":  "#004422",
        "trig_text": "#00ff66",
        "sep":       "#003322",
        "logo_bg":   "#000600",
        "logo_text": "#00ee44",
        "logo_sub":  "#004422",
    },
}

def _pal(name: str) -> dict:
    return _THEME.get(name, _THEME["dark"])

def _tdiv(span: float) -> str:
    return "---" if span <= 0 else _eng(span / 10.0, "s") + "/div"


# ── Logo block ─────────────────────────────────────────────────────────────────
class LogoBlock(QWidget):
    """
    Renders an SVG logo at the prescribed BAR_H height, width derived from
    the SVG viewBox aspect ratio so the logo is never squished.
    Falls back to text if no SVG.
    """
    def __init__(self, theme_name: str = "dark", parent=None):
        super().__init__(parent)
        self._theme_name = theme_name
        self._pixmap: QPixmap = None
        self._logo_w = DEFAULT_LOGO_W
        self.setFixedSize(self._logo_w, BAR_H)

    def set_svg(self, svg_path: str):
        try:
            from PyQt6.QtSvg import QSvgRenderer
            renderer = QSvgRenderer(svg_path)
            if not renderer.isValid():
                self._pixmap = None
                self._logo_w = DEFAULT_LOGO_W
                self.setFixedWidth(self._logo_w)
                self.update()
                return
            # Derive width from SVG aspect ratio
            vb = renderer.viewBox()
            if vb.width() > 0 and vb.height() > 0:
                aspect = vb.width() / vb.height()
                self._logo_w = max(80, min(400, int(BAR_H * aspect)))
            else:
                self._logo_w = DEFAULT_LOGO_W
            self.setFixedWidth(self._logo_w)

            pal = _pal(self._theme_name)
            px = QPixmap(self._logo_w, BAR_H)
            px.fill(QColor(pal["logo_bg"]))
            p = QPainter(px)
            renderer.render(p, QRectF(0, 0, self._logo_w, BAR_H))
            p.end()
            self._pixmap = px
        except Exception:
            self._pixmap = None
            self._logo_w = DEFAULT_LOGO_W
            self.setFixedWidth(self._logo_w)
        self.update()

    def set_theme(self, name: str):
        self._theme_name = name
        self._pixmap = None   # will redraw as text; caller should re-set SVG
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        pal = _pal(self._theme_name)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(pal["logo_bg"]))
        if self._pixmap:
            p.drawPixmap(0, 0, self._pixmap)
        else:
            f1 = QFont("Courier New", 14)
            f1.setBold(True)
            p.setFont(f1)
            p.setPen(QPen(QColor(pal["logo_text"])))
            p.drawText(8, 38, "TraceLab")
            f2 = QFont("Courier New", 9)
            p.setFont(f2)
            p.setPen(QPen(QColor(pal["logo_sub"])))
            p.drawText(10, 58, "by ChipFX")
        p.end()


# ── Time+Trigger block ──────────────────────────────────────────────────────────
class TimeTrigBlock(QWidget):
    def __init__(self, theme_name: str = "dark", parent=None):
        super().__init__(parent)
        self._theme_name = theme_name
        self._tdiv_txt = "---"
        self._trig_txt = "---"
        self.setFixedSize(INFO_W, BAR_H)

    def set_tdiv(self, t: str):
        self._tdiv_txt = t
        self.update()

    def set_trig(self, t: str):
        self._trig_txt = t or "---"
        self.update()

    def set_theme(self, name: str):
        self._theme_name = name
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        pal = _pal(self._theme_name)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(pal["info_bg"]))

        f_dim = QFont("Courier New", 7)
        f_val = QFont("Courier New", 11)
        f_val.setBold(True)
        f_trig = QFont("Courier New", 9)
        f_trig.setBold(True)

        p.setFont(f_dim)
        p.setPen(QPen(QColor(pal["info_dim"])))
        p.drawText(6, 16, "TIME BASE")

        p.setFont(f_val)
        p.setPen(QPen(QColor(pal["info_text"])))
        p.drawText(6, 38, self._tdiv_txt)

        p.setPen(QPen(QColor(pal["sep"]), 1))
        p.drawLine(6, 50, w - 6, 50)

        p.setFont(f_dim)
        p.setPen(QPen(QColor(pal["info_dim"])))
        p.drawText(6, 64, "TRIGGER")

        p.setFont(f_trig)
        p.setPen(QPen(QColor(pal["trig_text"])))
        txt = self._trig_txt
        if len(txt) > 15:
            mid = txt.rfind(' ', 0, 15) or 15
            p.drawText(6, 82, txt[:mid])
            p.drawText(6, 98, txt[mid:].strip())
        else:
            p.drawText(6, 82, txt)
        p.end()


# ── Separator ──────────────────────────────────────────────────────────────────
def _sep(theme_name: str = "dark") -> QFrame:
    pal = _pal(theme_name)
    s = QFrame()
    s.setFrameShape(QFrame.Shape.VLine)
    s.setFixedWidth(SEP_W)
    s.setStyleSheet(f"color: {pal['sep']};")
    return s


# ── Main status bar ─────────────────────────────────────────────────────────────
class ScopeStatusBar(QWidget):
    toggle_trace_interp = pyqtSignal(str)   # trace name

    def __init__(self, theme_name: str = "dark", parent=None):
        super().__init__(parent)
        self._theme_name = theme_name
        self._trace_interp_modes: dict = {}
        self._svg_path: str = ""
        self._ch_blocks: list = []   # keep strong refs to avoid GC race

        self.setFixedHeight(BAR_H)
        self._apply_style()

        self._outer = QHBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)

        # Logo
        self._logo = LogoBlock(theme_name)
        self._outer.addWidget(self._logo)
        self._outer.addWidget(_sep(theme_name))

        # Time+Trigger
        self._timetrig = TimeTrigBlock(theme_name)
        self._outer.addWidget(self._timetrig)
        self._outer.addWidget(_sep(theme_name))

        # Scrollable channel area
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

    def _apply_style(self):
        pal = _pal(self._theme_name)
        self.setStyleSheet(
            f"background: {pal['bar_bg']}; "
            f"border-top: 2px solid {pal['sep']};")

    def _update_scroll_style(self):
        pal = _pal(self._theme_name)
        self._ch_scroll.setStyleSheet(
            f"QScrollArea {{ border: none; background: {pal['bar_bg']}; }}"
            f"QScrollBar:horizontal {{ height: 5px; background: {pal['bar_bg']}; }}"
            f"QScrollBar::handle:horizontal {{ background: {pal['sep']}; "
            f"border-radius: 2px; }}")
        self._ch_container.setStyleSheet(
            f"background: {pal['bar_bg']};")

    def set_branding(self, svg_path: str):
        self._svg_path = svg_path
        if svg_path:
            self._logo.set_svg(svg_path)
        else:
            self._logo.set_theme(self._theme_name)  # text fallback

    def set_theme(self, theme_name: str):
        self._theme_name = theme_name
        self._apply_style()
        self._update_scroll_style()
        self._logo.set_theme(theme_name)
        if self._svg_path:
            self._logo.set_svg(self._svg_path)
        self._timetrig.set_theme(theme_name)

    def set_trace_interp_modes(self, modes: dict):
        self._trace_interp_modes = dict(modes)

    def update(self, traces: List[TraceModel],
               x_span: float,
               trigger_info: str = "",
               y_ranges: dict = None,
               interp_active: bool = False):
        """Rebuild the status bar. Call whenever view or traces change."""
        self._timetrig.set_tdiv(_tdiv(x_span))
        self._timetrig.set_trig(trigger_info)

        # ── Rebuild channel blocks ─────────────────────────────────────────
        # IMPORTANT: remove widgets from layout FIRST, then clear strong-ref
        # list, then allow Qt to clean up. Using takeAt avoids the deleteLater
        # race that caused blocks to never appear.
        while self._ch_layout.count():
            item = self._ch_layout.takeAt(0)
            if item:
                w = item.widget()
                if w:
                    w.setParent(None)   # synchronous removal, no deleteLater race
        self._ch_blocks.clear()

        y_ranges = y_ranges or {}
        visible = [t for t in traces if t.visible]

        for trace in visible:
            y_min, y_max = y_ranges.get(trace.name, (0.0, 0.0))
            y_span = abs(y_max - y_min)
            mode = self._trace_interp_modes.get(trace.name, "linear")
            block = ChannelStatusBlock(
                trace, y_span, mode, self._theme_name, parent=self._ch_container)
            block.toggle_interp.connect(self.toggle_trace_interp)
            self._ch_blocks.append(block)   # strong ref
            self._ch_layout.addWidget(block)
            block.show()

        # Set container width so scroll works correctly
        n = len(visible)
        total_w = n * (BLOCK_W + SEP_W) + 4
        self._ch_container.setFixedWidth(max(total_w, BLOCK_W))
        self._ch_container.setFixedHeight(BAR_H)
