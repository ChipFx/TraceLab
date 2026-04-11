"""
core/scope_status_bar.py
LeCroy/R&S-style scope status bar for ChipFX TraceLab.

Layout (left to right):
  [Logo 110×110] | [Time+Trig block] | [Ch1 block] [Ch2 block] ... (scrollable)

Each block is ~110px wide × 110px tall with 3 rows of info.
Channel blocks contain: name + interp badge | V/div | filter info
Clicking the interp badge on a channel block toggles linear↔sinc for that trace.
"""

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QScrollArea,
    QFrame, QSizePolicy, QPushButton
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap, QColor, QPainter, QFont, QCursor
from typing import List, Optional
from core.trace_model import TraceModel

# ── Sizing constants ──────────────────────────────────────────────────────────
BAR_H    = 110   # total bar height
BLOCK_W  = 115   # width of each block (logo, info, channel)
SEP_W    = 2     # separator width
BG       = "#0a0a14"
BG_BLOCK = "#0e0e1c"
BG_LOGO  = "#06060f"
COL_DIM  = "#666677"
COL_TEXT = "#ccccdd"
COL_TRIG = "#44ee66"
COL_FILT = "#ff8844"
COL_SINC = "#ee2222"
COL_LIN  = "#334455"


def _eng(value: float, unit: str) -> str:
    if value == 0:
        return f"0 {unit}"
    abs_v = abs(value)
    for scale, prefix in [(1e12,'T'),(1e9,'G'),(1e6,'M'),(1e3,'k'),
                           (1,''),(1e-3,'m'),(1e-6,'µ'),(1e-9,'n'),(1e-12,'p')]:
        if abs_v >= scale * 0.9999:
            s = value / scale
            txt = (f"{s:.0f}" if abs(s) >= 100
                   else f"{s:.1f}".rstrip('0').rstrip('.')
                   if abs(s) >= 10
                   else f"{s:.2f}".rstrip('0').rstrip('.'))
            return f"{txt} {prefix}{unit}"
    return f"{value:.3e} {unit}"


def _tdiv(span: float) -> str:
    if span <= 0:
        return "---"
    return _eng(span / 10.0, "s") + "/div"


def _mono(size=9, bold=False):
    f = QFont("Courier New", size)
    if bold:
        f.setWeight(QFont.Weight.Bold)
    return f


def _label(text, size=9, bold=False, color=COL_TEXT,
           align=Qt.AlignmentFlag.AlignLeft) -> QLabel:
    lbl = QLabel(text)
    lbl.setFont(_mono(size, bold))
    lbl.setStyleSheet(f"color: {color}; background: transparent;")
    lbl.setAlignment(align)
    return lbl


def _sep_v() -> QFrame:
    s = QFrame()
    s.setFrameShape(QFrame.Shape.VLine)
    s.setFixedWidth(SEP_W)
    s.setStyleSheet(f"color: #1e1e30;")
    return s


# ── Channel block ─────────────────────────────────────────────────────────────

class ChannelBlock(QWidget):
    """
    One channel info block — 3 rows:
      Row 1: [color bar] [label] [interp badge — clickable]
      Row 2: [V/div value]
      Row 3: [filter desc or blank]
    Clicking the interp badge emits toggle_interp(trace_name).
    """
    toggle_interp = pyqtSignal(str)   # trace name

    def __init__(self, trace: TraceModel, y_span: float = 0.0,
                 interp_mode: str = "linear", parent=None):
        super().__init__(parent)
        self._trace = trace
        self._y_span = y_span
        self._interp_mode = interp_mode
        self.setFixedSize(BLOCK_W, BAR_H)
        self.setStyleSheet(f"background: {BG_BLOCK};")
        self._build()

    def _build(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Left colour bar
        bar = QFrame()
        bar.setFixedWidth(5)
        bar.setStyleSheet(f"background: {self._trace.color}; border: none;")
        outer.addWidget(bar)

        # Content column
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(5, 5, 4, 5)
        cl.setSpacing(4)

        # Row 1: label + interp badge
        row1 = QWidget()
        row1.setStyleSheet("background: transparent;")
        r1l = QHBoxLayout(row1)
        r1l.setContentsMargins(0,0,0,0)
        r1l.setSpacing(4)

        lbl_name = _label(self._trace.label, size=9, bold=True,
                          color=self._trace.color)
        lbl_name.setMaximumWidth(70)
        r1l.addWidget(lbl_name)
        r1l.addStretch()

        # Interp badge button
        sinc = (self._interp_mode == "sinc")
        badge_txt  = "SINC" if sinc else "LIN"
        badge_bg   = COL_SINC if sinc else COL_LIN
        badge_fg   = "#ffffff" if sinc else "#889aaa"
        self._badge = QPushButton(badge_txt)
        self._badge.setFont(_mono(7, bold=sinc))
        self._badge.setFixedSize(34, 16)
        self._badge.setStyleSheet(
            f"QPushButton {{ background: {badge_bg}; color: {badge_fg}; "
            f"border-radius: 2px; padding: 0px; border: none; }}"
            f"QPushButton:hover {{ border: 1px solid #aaa; }}")
        self._badge.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._badge.setToolTip(
            "Click to toggle interpolation for this channel\n"
            f"Current: {'Sinc (sin(x)/x)' if sinc else 'Linear'}")
        self._badge.clicked.connect(
            lambda: self.toggle_interp.emit(self._trace.name))
        r1l.addWidget(self._badge)
        cl.addWidget(row1)

        # Row 2: V/div
        unit = getattr(self._trace, 'unit', '') or ''
        if self._y_span > 0 and unit and unit != 'raw':
            vdiv_txt = _eng(self._y_span / 10.0, unit) + "/div"
        else:
            vdiv_txt = "---/div"
        lbl_vdiv = _label(vdiv_txt, size=9, bold=True, color=COL_TEXT)
        cl.addWidget(lbl_vdiv)

        # Row 3: filter or coupling info
        filt = getattr(self._trace, '_filter_desc', '') or ''
        coupling = getattr(self._trace, 'coupling', '') or ''
        imp = getattr(self._trace, 'impedance', '') or ''
        extra_parts = [p for p in [coupling, imp, filt] if p]
        extra_txt = "  ".join(extra_parts) if extra_parts else ""
        if extra_txt:
            lbl_extra = _label(extra_txt, size=7, color=COL_FILT)
        else:
            lbl_extra = _label("", size=7, color=COL_DIM)
        cl.addWidget(lbl_extra)

        outer.addWidget(content)


# ── Combined Time+Trigger block ───────────────────────────────────────────────

class TimeTrigBlock(QWidget):
    """Combined timebase + trigger info block, 3 rows."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(BLOCK_W, BAR_H)
        self.setStyleSheet(f"background: {BG_BLOCK};")

        cl = QVBoxLayout(self)
        cl.setContentsMargins(8, 6, 8, 6)
        cl.setSpacing(4)

        # Row 1: dim label "TIME BASE"
        cl.addWidget(_label("TIME BASE", size=7, color=COL_DIM))

        # Row 2: T/div value
        self._lbl_tdiv = _label("---", size=11, bold=True, color=COL_TEXT)
        cl.addWidget(self._lbl_tdiv)

        # Separator line
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"color: #1e1e30;")
        line.setFixedHeight(1)
        cl.addWidget(line)

        # Row 3a: dim label "TRIGGER"
        cl.addWidget(_label("TRIGGER", size=7, color=COL_DIM))

        # Row 3b: trigger value
        self._lbl_trig = _label("---", size=9, bold=True, color=COL_TRIG)
        self._lbl_trig.setWordWrap(False)
        cl.addWidget(self._lbl_trig)

    def set_tdiv(self, text: str):
        self._lbl_tdiv.setText(text)

    def set_trig(self, text: str):
        self._lbl_trig.setText(text or "---")


# ── Logo block ────────────────────────────────────────────────────────────────

class LogoBlock(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(BLOCK_W, BAR_H)
        self.setStyleSheet(f"background: {BG_LOGO};")
        self._lbl = QLabel("TraceLab")
        self._lbl.setFont(_mono(12, bold=True))
        self._lbl.setStyleSheet("color: #F0C040; background: transparent;")
        self._lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self._lbl)

    def set_svg(self, svg_path: str):
        try:
            from PyQt6.QtSvg import QSvgRenderer
            from PyQt6.QtCore import QRectF
            renderer = QSvgRenderer(svg_path)
            if not renderer.isValid():
                return
            px = QPixmap(BLOCK_W - 8, BAR_H - 8)
            px.fill(QColor(BG_LOGO))
            p = QPainter(px)
            renderer.render(p, QRectF(0, 0, px.width(), px.height()))
            p.end()
            self._lbl.setPixmap(px)
            self._lbl.setText("")
        except Exception:
            pass


# ── Main status bar ───────────────────────────────────────────────────────────

class ScopeStatusBar(QWidget):
    """Full-width LeCroy-style status bar, BAR_H px tall."""

    toggle_trace_interp = pyqtSignal(str)   # trace name — connect to main window

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(BAR_H)
        self.setStyleSheet(f"background: {BG}; border-top: 2px solid #1e1e30;")

        self._outer = QHBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)

        # Logo
        self._logo = LogoBlock()
        self._outer.addWidget(self._logo)
        self._outer.addWidget(_sep_v())

        # Time + Trigger
        self._time_trig = TimeTrigBlock()
        self._outer.addWidget(self._time_trig)
        self._outer.addWidget(_sep_v())

        # Scrollable channel area
        self._ch_scroll = QScrollArea()
        self._ch_scroll.setWidgetResizable(False)
        self._ch_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._ch_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._ch_scroll.setFixedHeight(BAR_H)
        self._ch_scroll.setStyleSheet(
            f"QScrollArea {{ border: none; background: {BG}; }}"
            "QScrollBar:horizontal { height: 5px; background: #111; }"
            "QScrollBar::handle:horizontal { background: #333; border-radius: 2px; }")
        self._ch_container = QWidget()
        self._ch_container.setStyleSheet(f"background: {BG};")
        self._ch_layout = QHBoxLayout(self._ch_container)
        self._ch_layout.setContentsMargins(2, 0, 2, 0)
        self._ch_layout.setSpacing(SEP_W)
        self._ch_layout.addStretch()
        self._ch_scroll.setWidget(self._ch_container)
        self._outer.addWidget(self._ch_scroll, stretch=1)

        self._trace_interp_modes: dict = {}   # name -> "linear"/"sinc"

    def set_branding(self, svg_path: str):
        self._logo.set_svg(svg_path)

    def set_trace_interp_modes(self, modes: dict):
        """Update {name: mode} map used when building channel blocks."""
        self._trace_interp_modes = dict(modes)

    def update(self, traces: List[TraceModel],
               x_span: float,
               trigger_info: str = "",
               y_ranges: dict = None,
               interp_active: bool = False):
        """Refresh all info. Call whenever view or traces change."""
        self._time_trig.set_tdiv(_tdiv(x_span))
        self._time_trig.set_trig(trigger_info)

        # Rebuild channel blocks
        while self._ch_layout.count():
            item = self._ch_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        y_ranges = y_ranges or {}
        visible = [t for t in traces if t.visible]
        for trace in visible:
            y_min, y_max = y_ranges.get(trace.name, (0.0, 0.0))
            y_span = abs(y_max - y_min)
            mode = self._trace_interp_modes.get(trace.name, "linear")
            block = ChannelBlock(trace, y_span, mode)
            block.toggle_interp.connect(self.toggle_trace_interp)
            self._ch_layout.addWidget(block)

        self._ch_layout.addStretch()
        n = len(visible)
        self._ch_container.setFixedWidth(max(n * (BLOCK_W + SEP_W) + 8, 10))
