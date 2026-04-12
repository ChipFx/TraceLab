"""
core/scope_plot_widget.py
Main oscilloscope plot widget — split lanes and overlay modes.
"""

import numpy as np
import pyqtgraph as pg
from pyqtgraph import InfiniteLine
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QScrollArea, QMenu, QColorDialog, QInputDialog,
                               QLineEdit, QPushButton, QFrame, QSizePolicy)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QFont, QAction
from typing import List, Dict, Optional, Tuple
from core.trace_model import TraceModel

MAX_DISPLAY_POINTS = 50_000


def downsample_for_display(t, y, max_pts=MAX_DISPLAY_POINTS):
    n = len(t)
    if n <= max_pts:
        return t, y
    window = max(1, n // (max_pts // 2))
    n_windows = n // window
    t_out = np.empty(n_windows * 2)
    y_out = np.empty(n_windows * 2)
    for i in range(n_windows):
        sl = slice(i * window, (i + 1) * window)
        yw = y[sl]; tw = t[sl]
        imin = np.argmin(yw); imax = np.argmax(yw)
        if imin <= imax:
            t_out[i*2] = tw[imin]; y_out[i*2] = yw[imin]
            t_out[i*2+1] = tw[imax]; y_out[i*2+1] = yw[imax]
        else:
            t_out[i*2] = tw[imax]; y_out[i*2] = yw[imax]
            t_out[i*2+1] = tw[imin]; y_out[i*2+1] = yw[imin]
    return t_out, y_out


def sinc_interpolate_to_n(t: np.ndarray, y: np.ndarray,
                           target_n: int) -> tuple:
    """
    Bandlimited sinc interpolation via FFT zero-padding.
    Upsamples y to exactly target_n points spread evenly over [t[0], t[-1]].
    Only upsamples (target_n > len(y)); pass-through if not needed.
    """
    n = len(y)
    if n < 4 or target_n <= n:
        return t, y
    # Ceiling division so n_new >= target_n
    upsample = max(2, (target_n + n - 1) // n)
    n_new = n * upsample
    Y = np.fft.rfft(y)
    Y_pad = np.zeros(n_new // 2 + 1, dtype=complex)
    copy_len = min(len(Y), len(Y_pad))
    Y_pad[:copy_len] = Y[:copy_len] * upsample
    y_new = np.fft.irfft(Y_pad, n_new)
    t_new = np.linspace(t[0], t[-1], n_new, endpoint=False)
    return t_new, y_new


# Keep old name for backward compat (used in tests)
def sinc_interpolate(t, y, upsample=8):
    return sinc_interpolate_to_n(t, y, len(y) * upsample)


def cubic_interpolate_to_n(t: np.ndarray, y: np.ndarray,
                            target_n: int) -> tuple:
    """
    Cubic spline interpolation via scipy CubicSpline (not-a-knot boundary).
    Pass-through if target_n <= len(y) or len(y) < 4.
    Falls back to sinc if scipy fails.
    """
    n = len(y)
    if n < 4 or target_n <= n:
        return t, y
    try:
        from scipy.interpolate import CubicSpline
        cs = CubicSpline(t, y, bc_type='not-a-knot')
        t_new = np.linspace(t[0], t[-1], target_n)
        return t_new, cs(t_new)
    except Exception:
        return sinc_interpolate_to_n(t, y, target_n)


def _eng_format(value: float, unit: str) -> str:
    """
    Format a float with engineering-style SI prefix and unit.
    Examples:  0.001 V  ->  '1 mV'
               0.000099 V -> '99 µV'
               1500 Hz    -> '1.5 kHz'
               0.1 V      -> '100 mV'   (100 mV shorter than 0.1 V)
    """
    if value == 0:
        return f"0 {unit}"
    abs_v = abs(value)
    prefixes = [
        (1e12, 'T'), (1e9, 'G'), (1e6, 'M'), (1e3, 'k'),
        (1,    ''),  (1e-3, 'm'), (1e-6, 'µ'), (1e-9, 'n'), (1e-12, 'p'),
    ]
    for scale, prefix in prefixes:
        if abs_v >= scale * 0.9999:
            scaled = value / scale
            # Choose decimal places to keep it short
            if abs(scaled) >= 100:
                s = f"{scaled:.0f}"
            elif abs(scaled) >= 10:
                s = f"{scaled:.1f}".rstrip('0').rstrip('.')
            else:
                s = f"{scaled:.2f}".rstrip('0').rstrip('.')
            return f"{s} {prefix}{unit}"
    # Fallback for very small values
    return f"{value:.3e} {unit}"


class EngineeringTimeAxisItem(pg.AxisItem):
    """X-axis that labels ticks with SI time prefixes: ns, µs, ms, s, ks."""
    def tickStrings(self, values, scale, spacing):
        results = []
        for v in values:
            t = float(v)
            a = abs(t)
            if a == 0:
                results.append("0 s")
            elif a < 1e-9:
                results.append(f"{t*1e12:.4g} ps")
            elif a < 1e-6:
                results.append(f"{t*1e9:.4g} ns")
            elif a < 1e-3:
                results.append(f"{t*1e6:.4g} µs")
            elif a < 1.0:
                results.append(f"{t*1e3:.4g} ms")
            elif a < 1e3:
                results.append(f"{t:.4g} s")
            else:
                results.append(f"{t/1e3:.4g} ks")
        return results


class EngineeringAxisItem(pg.AxisItem):
    """
    Y-axis that labels ticks as  '1 mV', '-500 µV', '1.5 V' etc.
    Set unit via .set_unit(str). Empty/None unit falls back to plain numbers.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._unit = ""

    def set_unit(self, unit: str):
        self._unit = unit or ""

    def tickStrings(self, values, scale, spacing):
        if not self._unit or self._unit in ("raw", ""):
            return super().tickStrings(values, scale, spacing)
        # pyqtgraph passes scale for its own unit conversion; we handle SI
        # prefixes ourselves in _eng_format, so use values directly (scale=1.0)
        return [_eng_format(float(v), self._unit) for v in values]



def _effective_color(color: str, theme_name: str) -> str:
    """Override trace color for special themes."""
    if theme_name == "rs_green":
        return "#00ee44"
    return color


class RangeBar(QWidget):
    """Compact X/Y range input bar shown below the plot area."""
    range_changed = pyqtSignal(float, float, float, float)  # x0,x1,y0,y1

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(6)

        layout.addWidget(QLabel("X:"))
        self.x0 = QLineEdit(); self.x0.setFixedWidth(90)
        self.x1 = QLineEdit(); self.x1.setFixedWidth(90)
        layout.addWidget(self.x0)
        layout.addWidget(QLabel("→"))
        layout.addWidget(self.x1)

        layout.addWidget(QLabel("  Y:"))
        self.y0 = QLineEdit(); self.y0.setFixedWidth(80)
        self.y1 = QLineEdit(); self.y1.setFixedWidth(80)
        layout.addWidget(self.y0)
        layout.addWidget(QLabel("→"))
        layout.addWidget(self.y1)

        btn = QPushButton("Apply")
        btn.setFixedWidth(55)
        btn.clicked.connect(self._apply)
        layout.addWidget(btn)
        layout.addStretch()

        self.setMaximumHeight(30)

    def update_display(self, x0, x1, y0, y1):
        def fmt(v):
            if abs(v) < 1e-3 or abs(v) >= 1e6:
                return f"{v:.4e}"
            return f"{v:.6g}"
        for edit, val in [(self.x0, x0), (self.x1, x1),
                           (self.y0, y0), (self.y1, y1)]:
            edit.blockSignals(True)
            edit.setText(fmt(val))
            edit.blockSignals(False)

    def _apply(self):
        try:
            x0 = float(self.x0.text())
            x1 = float(self.x1.text())
            y0 = float(self.y0.text())
            y1 = float(self.y1.text())
            if x0 < x1 and y0 < y1:
                self.range_changed.emit(x0, x1, y0, y1)
        except ValueError:
            pass


class TraceLane(pg.PlotWidget):
    cursor_moved = pyqtSignal(float, int)
    view_range_changed = pyqtSignal(object)  # passes self

    def __init__(self, trace: TraceModel, theme_colors: dict,
                 theme_name: str = "dark", y_lock_auto: bool = True,
                 interp_mode: str = "linear", parent=None):
        self._y_axis = EngineeringAxisItem(orientation="left")
        self._x_axis = EngineeringTimeAxisItem(orientation="bottom")
        unit = getattr(trace, 'unit', '') or ''
        self._y_axis.set_unit(unit)
        super().__init__(parent=parent, background=theme_colors["background"],
                         axisItems={"left": self._y_axis,
                                    "bottom": self._x_axis})
        self.trace = trace
        self.theme = theme_colors
        self.theme_name = theme_name
        self.y_lock_auto = y_lock_auto
        self.interp_mode = interp_mode   # "linear" or "sinc"
        self.viewport_min_pts = 1024      # minimum display points; set from settings
        self._curve = None
        self._cursors: Dict[int, InfiniteLine] = {}
        self._labels: list = []          # TextItem labels anchored to time positions
        self._sinc_active = False         # True when sinc was actually used this draw

        self._setup_plot()
        self._add_trace_curve()

        # Re-render when view range changes (viewport-aware interp)
        self.getPlotItem().sigRangeChanged.connect(self._on_view_changed)
        self.getPlotItem().sigRangeChanged.connect(
            lambda: self.view_range_changed.emit(self))

    def _on_view_changed(self):
        """Re-draw curve when zoomed in enough that sinc/cubic would kick in."""
        if self.interp_mode in ("sinc", "cubic"):
            self._add_trace_curve()

    def _setup_plot(self):
        pi = self.getPlotItem()
        pi.showGrid(x=True, y=True, alpha=0.3)
        pi.setMenuEnabled(False)
        disp_color = _effective_color(self.trace.color, self.theme_name)
        # Label shows trace name coloured; unit appears in tick strings
        ylabel = f"<span style='color:{disp_color}'>{self.trace.label}</span>"
        pi.setLabel("left", ylabel, color=self.theme["text"])
        pi.getAxis("left").setWidth(60)
        for ax_name in ("left", "bottom", "top", "right"):
            ax = pi.getAxis(ax_name)
            ax.setPen(pg.mkPen(color=self.theme["text"], width=1))
            ax.setTextPen(pg.mkPen(color=self.theme["text"]))
        pi.getAxis("top").setStyle(showValues=False)
        pi.getAxis("right").setStyle(showValues=False)
        self.setMouseTracking(True)
        if self.y_lock_auto:
            pi.setMouseEnabled(x=True, y=False)

    def _add_trace_curve(self):
        if self._curve is not None:
            self.removeItem(self._curve)
        t_full = self.trace.time_axis
        y_full = self.trace.processed_data
        self._sinc_active = False

        if self.interp_mode in ("sinc", "cubic"):
            # Get current view window to know how many raw samples are visible
            vr = self.getPlotItem().viewRange()
            x0, x1 = vr[0]
            mask = (t_full >= x0) & (t_full <= x1)
            n_visible = int(mask.sum())
            if n_visible < 2:
                n_visible = len(t_full)   # not yet zoomed, use full length

            if n_visible < self.viewport_min_pts:
                # Interpolate just the visible slice to viewport_min_pts
                t_vis = t_full[mask] if n_visible < len(t_full) else t_full
                y_vis = y_full[mask] if n_visible < len(y_full) else y_full
                if len(t_vis) >= 4:
                    if self.interp_mode == "cubic":
                        t_vis, y_vis = cubic_interpolate_to_n(
                            t_vis, y_vis, self.viewport_min_pts)
                    else:
                        t_vis, y_vis = sinc_interpolate_to_n(
                            t_vis, y_vis, self.viewport_min_pts)
                    self._sinc_active = True
                # Replace visible window portion
                t_full = t_vis
                y_full = y_vis

        t, y = downsample_for_display(t_full, y_full)
        color = _effective_color(self.trace.color, self.theme_name)
        pen = pg.mkPen(color=color, width=1.5)
        self._curve = self.plot(t, y, pen=pen, antialias=False)
        self._curve.setDownsampling(auto=True, method="peak")
        self._curve.setClipToView(True)
        if self.y_lock_auto:
            self.getPlotItem().enableAutoRange(axis="y")
        self._redraw_labels()

    def _redraw_labels(self):
        """Draw per-trace text labels anchored to time positions."""
        for item in self._labels:
            self.removeItem(item)
        self._labels.clear()
        labels = getattr(self.trace, 'trace_labels', [])
        for t_pos, text in labels:
            y_pos = self.get_value_at(t_pos)
            if y_pos is None:
                continue
            color = _effective_color(self.trace.color, self.theme_name)
            item = pg.TextItem(text=text, color=color, anchor=(0.5, 1.0))
            item.setPos(t_pos, y_pos)
            self.addItem(item)
            self._labels.append(item)

    def refresh_curve(self):
        self._add_trace_curve()
        # Refresh unit on axis in case it changed (e.g. after filter applied)
        unit = getattr(self.trace, 'unit', '') or ''
        if hasattr(self, '_y_axis'):
            self._y_axis.set_unit(unit)

    def set_y_lock_auto(self, locked: bool):
        self.y_lock_auto = locked
        pi = self.getPlotItem()
        pi.setMouseEnabled(x=True, y=not locked)
        if locked:
            pi.enableAutoRange(axis="y")

    def add_cursor(self, cursor_id, x_pos, color, label=""):
        if cursor_id in self._cursors:
            self.removeItem(self._cursors[cursor_id])
        pen = pg.mkPen(color=color, width=1.5, style=Qt.PenStyle.DashLine)
        line = InfiniteLine(pos=x_pos, angle=90, pen=pen, movable=True,
                             label=label,
                             labelOpts={"color": color, "position": 0.95})
        line.sigPositionChanged.connect(
            lambda l, cid=cursor_id: self.cursor_moved.emit(l.value(), cid))
        self.addItem(line)
        self._cursors[cursor_id] = line

    def update_cursor(self, cursor_id, x_pos):
        if cursor_id in self._cursors:
            self._cursors[cursor_id].blockSignals(True)
            self._cursors[cursor_id].setValue(x_pos)
            self._cursors[cursor_id].blockSignals(False)

    def get_value_at(self, t_pos):
        t = self.trace.time_axis
        y = self.trace.processed_data
        if len(t) < 2:
            return None
        idx = np.searchsorted(t, t_pos)
        if idx <= 0: return float(y[0])
        if idx >= len(t): return float(y[-1])
        t0, t1 = t[idx-1], t[idx]
        y0, y1 = y[idx-1], y[idx]
        if t1 == t0: return float(y0)
        return float(y0 + (y1 - y0) * (t_pos - t0) / (t1 - t0))

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        act_color = QAction(f"Change Color: {self.trace.label}", self)
        act_color.triggered.connect(self._change_color)
        menu.addAction(act_color)
        act_label = QAction("Rename Trace", self)
        act_label.triggered.connect(self._rename)
        menu.addAction(act_label)
        menu.addSeparator()
        act_y_auto = QAction("Auto Scale Y", self)
        act_y_auto.triggered.connect(
            lambda: self.getPlotItem().enableAutoRange(axis="y"))
        menu.addAction(act_y_auto)
        act_xy = QAction("Auto Scale X+Y", self)
        act_xy.triggered.connect(
            lambda: self.getPlotItem().enableAutoRange())
        menu.addAction(act_xy)
        menu.exec(event.globalPos())

    def _change_color(self):
        c = QColorDialog.getColor(QColor(self.trace.color), self)
        if c.isValid():
            self.trace.color = c.name()
            self.refresh_curve()
            color = _effective_color(self.trace.color, self.theme_name)
            self.getPlotItem().setLabel(
                "left",
                f"<span style='color:{color}'>{self.trace.label}</span>")

    def _rename(self):
        text, ok = QInputDialog.getText(
            self, "Rename", "New label:", text=self.trace.label)
        if ok and text:
            self.trace.label = text
            color = _effective_color(self.trace.color, self.theme_name)
            self.getPlotItem().setLabel(
                "left",
                f"<span style='color:{color}'>{self.trace.label}</span>")


def _composite_branding(img: "QImage", svg_path: str) -> "QImage":
    """
    Render an SVG logo into the bottom-left corner of img.
    The logo is constrained to a max of 120px wide / 60px tall (before scale
    is applied at caller side) with 6px padding from the corner.
    Returns the composited QImage (may be a copy).
    """
    try:
        from PyQt6.QtSvg import QSvgRenderer
        from PyQt6.QtGui import QPainter, QImage
        from PyQt6.QtCore import QRectF, Qt

        renderer = QSvgRenderer(svg_path)
        if not renderer.isValid():
            return img

        # Target logo size: up to 160×80 px on the output image
        w_img, h_img = img.width(), img.height()
        logo_w = min(160, w_img // 6)
        logo_h = min(80,  h_img // 8)
        # Maintain SVG aspect ratio
        vp = renderer.viewBox()
        if vp.width() > 0 and vp.height() > 0:
            aspect = vp.width() / vp.height()
            if logo_w / aspect > logo_h:
                logo_w = int(logo_h * aspect)
            else:
                logo_h = int(logo_w / aspect)

        pad = 8
        x = pad
        y = h_img - logo_h - pad

        # Convert to ARGB for compositing if needed
        if img.format() != QImage.Format.Format_ARGB32_Premultiplied:
            img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        renderer.render(painter, QRectF(x, y, logo_w, logo_h))
        painter.end()
    except Exception:
        pass  # branding failure must never crash a screenshot
    return img


class ScopePlotWidget(QWidget):
    cursor_values_changed = pyqtSignal(dict)

    sinc_active_changed = pyqtSignal(bool)  # emitted when sinc kicks in/out
    view_changed        = pyqtSignal()       # emitted (throttled) on pan/zoom

    def __init__(self, theme_colors: dict, theme_name: str = "dark",
                 y_lock_auto: bool = True, interp_mode: str = "linear",
                 viewport_min_pts: int = 1024, parent=None):
        super().__init__(parent)
        self.theme = theme_colors
        self.theme_name = theme_name
        self.y_lock_auto = y_lock_auto
        self.interp_mode = interp_mode
        self.viewport_min_pts = viewport_min_pts
        self._last_sinc_active = False
        self.traces: List[TraceModel] = []
        self._lanes: Dict[str, TraceLane] = {}
        self._mode = "split"
        self._cursors = {0: None, 1: None}
        self._cursor_colors = {
            0: theme_colors["cursor_a"],
            1: theme_colors["cursor_b"],
        }
        self._overlay_curves: Dict[str, pg.PlotDataItem] = {}
        self._overlay_z_order: List[str] = []  # for bring-to-front

        layout = QVBoxLayout(self)
        layout.setSpacing(1)
        layout.setContentsMargins(0, 0, 0, 0)

        # Split-lane scroll area
        self._lanes_container = QWidget()
        self._lanes_layout = QVBoxLayout(self._lanes_container)
        self._lanes_layout.setSpacing(1)
        self._lanes_layout.setContentsMargins(0, 0, 0, 0)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(self._lanes_container)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self._scroll)

        # Overlay widget
        self._overlay_widget = pg.PlotWidget(
            background=theme_colors["background"])
        self._overlay_widget.hide()
        layout.addWidget(self._overlay_widget)
        self._setup_overlay()

        # Range bar
        self._range_bar = RangeBar()
        self._range_bar.range_changed.connect(self._on_range_bar_changed)
        layout.addWidget(self._range_bar)

        # Timer to update range bar AND status bar (throttled to 100ms)
        self._range_timer = QTimer()
        self._range_timer.setSingleShot(True)
        self._range_timer.setInterval(100)
        self._range_timer.timeout.connect(self._update_range_bar)
        self._range_timer.timeout.connect(self.view_changed)

    def _setup_overlay(self):
        # Replace default axes with engineering-unit axes
        pi = self._overlay_widget.getPlotItem()
        self._ov_y_axis = EngineeringAxisItem(orientation="left")
        self._ov_x_axis = EngineeringTimeAxisItem(orientation="bottom")
        pi.setAxisItems({"left": self._ov_y_axis, "bottom": self._ov_x_axis})

        pi.showGrid(x=True, y=True, alpha=0.3)
        pi.setMenuEnabled(False)
        for ax_name in ("left", "bottom"):
            ax = pi.getAxis(ax_name)
            ax.setPen(pg.mkPen(color=self.theme["text"], width=1))
            ax.setTextPen(pg.mkPen(color=self.theme["text"]))
        pi.addLegend(offset=(10, 10))
        pi.sigRangeChanged.connect(lambda: self._range_timer.start())
        pi.sigRangeChanged.connect(self._on_overlay_range_changed)
        self._overlay_widget.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self._overlay_widget.customContextMenuRequested.connect(
            self._overlay_context_menu)
        if self.y_lock_auto:
            pi.setMouseEnabled(x=True, y=False)

    def _on_overlay_range_changed(self):
        """Re-render overlay curves when zoomed in (viewport sinc)."""
        if self.interp_mode == "sinc" or any(
                getattr(t, '_interp_mode_override', '') == 'sinc'
                for t in self.traces):
            self._rebuild_overlay()

    def set_mode(self, mode: str):
        self._mode = mode
        if mode == "overlay":
            self._scroll.hide()
            self._overlay_widget.show()
            self._rebuild_overlay()
        else:
            self._overlay_widget.hide()
            self._scroll.show()
            self._rebuild_split()

    def set_y_lock_auto(self, locked: bool):
        self.y_lock_auto = locked
        for lane in self._lanes.values():
            lane.set_y_lock_auto(locked)
        pi = self._overlay_widget.getPlotItem()
        pi.setMouseEnabled(x=True, y=not locked)
        if locked:
            pi.enableAutoRange(axis="y")

    def set_interp_mode(self, mode: str):
        """Switch global interpolation mode. Clears per-trace overrides."""
        self.interp_mode = mode
        # Clear per-trace overrides so all traces follow global mode
        for trace in self.traces:
            if hasattr(trace, '_interp_mode_override'):
                del trace._interp_mode_override
        self._rebuild()

    def add_trace(self, trace: TraceModel):
        # Allow overwrite if name already exists
        if trace.name in [t.name for t in self.traces]:
            for i, t in enumerate(self.traces):
                if t.name == trace.name:
                    self.traces[i] = trace
                    break
        else:
            self.traces.append(trace)
        self._rebuild()

    def reorder_traces(self, name_order: list):
        """Reorder traces list to match channel panel order."""
        name_idx = {n: i for i, n in enumerate(name_order)}
        self.traces.sort(key=lambda t: name_idx.get(t.name, 999))
        self._rebuild()

    def clear_all(self):
        self.traces.clear()
        self._rebuild()

    def remove_trace(self, trace_name: str):
        self.traces = [t for t in self.traces if t.name != trace_name]
        self._rebuild()

    def set_trace_visible(self, trace_name: str, visible: bool):
        for t in self.traces:
            if t.name == trace_name:
                t.visible = visible
        self._rebuild()

    def set_interp_mode_for_trace(self, trace_name: str, mode: str):
        """Set per-trace interpolation override."""
        for t in self.traces:
            if t.name == trace_name:
                t._interp_mode_override = mode
        lane = self._lanes.get(trace_name)
        if lane:
            lane.interp_mode = mode
            lane.refresh_curve()

    def get_sinc_active(self) -> bool:
        """True if any visible lane is currently sinc-interpolating."""
        return any(getattr(l, '_sinc_active', False)
                   for l in self._lanes.values())

    def refresh_all(self):
        if self._mode == "split":
            for lane in self._lanes.values():
                lane.refresh_curve()
                if self.y_lock_auto:
                    lane.getPlotItem().enableAutoRange(axis="y")
            # Signal sinc status change
            sinc_now = self.get_sinc_active()
            if sinc_now != self._last_sinc_active:
                self._last_sinc_active = sinc_now
                self.sinc_active_changed.emit(sinc_now)
        else:
            self._rebuild_overlay()

    def _rebuild(self):
        if self._mode == "split":
            self._rebuild_split()
        else:
            self._rebuild_overlay()

    def _rebuild_split(self):
        for lane in self._lanes.values():
            lane.setParent(None)
            lane.deleteLater()
        self._lanes.clear()

        visible = [t for t in self.traces if t.visible]
        if not visible:
            return

        first_lane = None
        for trace in visible:
            lane = TraceLane(trace, self.theme, self.theme_name,
                              self.y_lock_auto,
                              getattr(trace, '_interp_mode_override',
                                      self.interp_mode))
            lane.viewport_min_pts = self.viewport_min_pts
            lane.setMinimumHeight(80)
            if first_lane is None:
                first_lane = lane
            else:
                lane.setXLink(first_lane)
            lane.cursor_moved.connect(self._on_cursor_moved)
            lane.view_range_changed.connect(
                lambda _: self._range_timer.start())
            self._lanes[trace.name] = lane
            self._lanes_layout.addWidget(lane)

        for cid, t_pos in self._cursors.items():
            if t_pos is not None:
                self._place_cursors(cid, t_pos)

        self._range_timer.start()

    def _rebuild_overlay(self):
        pi = self._overlay_widget.getPlotItem()
        for curve in self._overlay_curves.values():
            pi.removeItem(curve)
        self._overlay_curves.clear()

        visible = [t for t in self.traces if t.visible]
        self._overlay_z_order = [t.name for t in visible]

        vr = pi.viewRange()
        x0, x1 = vr[0]

        # Update Y-axis unit from first visible trace with a real unit
        unit = next((t.unit for t in visible
                     if t.unit and t.unit != 'raw'), '')
        self._ov_y_axis.set_unit(unit)

        for trace in visible:
            t_full = trace.time_axis
            y_full = trace.processed_data
            mode = getattr(trace, '_interp_mode_override',
                           self.interp_mode)

            if mode in ("sinc", "cubic"):
                mask = (t_full >= x0) & (t_full <= x1)
                n_vis = int(mask.sum()) or len(t_full)
                if n_vis < self.viewport_min_pts and n_vis >= 4:
                    t_s = t_full[mask] if n_vis < len(t_full) else t_full
                    y_s = y_full[mask] if n_vis < len(y_full) else y_full
                    if mode == "cubic":
                        t_s, y_s = cubic_interpolate_to_n(
                            t_s, y_s, self.viewport_min_pts)
                    else:
                        t_s, y_s = sinc_interpolate_to_n(
                            t_s, y_s, self.viewport_min_pts)
                    t_full, y_full = t_s, y_s
            t_data, y_data = downsample_for_display(t_full, y_full)
            color = _effective_color(trace.color, self.theme_name)
            pen = pg.mkPen(color=color, width=1.5)
            curve = pi.plot(t_data, y_data, pen=pen,
                            name=trace.label, antialias=False)
            curve.setDownsampling(auto=True, method="peak")
            curve.setClipToView(True)
            self._overlay_curves[trace.name] = curve

        if self.y_lock_auto:
            pi.enableAutoRange(axis="y")

        # Re-place cursors
        for cid, t_pos in self._cursors.items():
            if t_pos is not None:
                self._place_cursors(cid, t_pos)

        self._range_timer.start()

    def bring_trace_to_front(self, trace_name: str):
        """Move a trace curve to the top of the overlay z-order."""
        if self._mode != "overlay":
            return
        curve = self._overlay_curves.get(trace_name)
        if curve:
            pi = self._overlay_widget.getPlotItem()
            pi.removeItem(curve)
            pi.addItem(curve)

    def _overlay_context_menu(self, pos):
        menu = QMenu(self._overlay_widget)

        # Bring-to-front submenu
        if self._overlay_z_order:
            front_menu = menu.addMenu("Bring to Front")
            for name in self._overlay_z_order:
                trace = next((t for t in self.traces if t.name == name), None)
                if trace:
                    color = _effective_color(trace.color, self.theme_name)
                    act = QAction(
                        f"● {trace.label}", self._overlay_widget)
                    act.setData(name)
                    act.triggered.connect(
                        lambda checked, n=name: self.bring_trace_to_front(n))
                    front_menu.addAction(act)

        menu.addSeparator()
        act_y = QAction("Auto Scale Y", self._overlay_widget)
        act_y.triggered.connect(
            lambda: self._overlay_widget.getPlotItem().enableAutoRange(axis="y"))
        menu.addAction(act_y)
        act_xy = QAction("Auto Scale X+Y", self._overlay_widget)
        act_xy.triggered.connect(
            lambda: self._overlay_widget.getPlotItem().enableAutoRange())
        menu.addAction(act_xy)
        menu.exec(self._overlay_widget.mapToGlobal(pos))

    # ── Cursors ───────────────────────────────────────────────────────

    def set_cursor(self, cursor_id: int, x_pos: Optional[float]):
        self._cursors[cursor_id] = x_pos
        if x_pos is not None:
            self._place_cursors(cursor_id, x_pos)
        self._emit_cursor_values()

    def _place_cursors(self, cursor_id, x_pos):
        color = self._cursor_colors[cursor_id]
        label = "A" if cursor_id == 0 else "B"
        if self._mode == "split":
            for lane in self._lanes.values():
                lane.add_cursor(cursor_id, x_pos, color, label)
        else:
            pi = self._overlay_widget.getPlotItem()
            attr = f"_overlay_cursor_{cursor_id}"
            if hasattr(self, attr):
                pi.removeItem(getattr(self, attr))
            pen = pg.mkPen(color=color, width=1.5,
                            style=Qt.PenStyle.DashLine)
            line = InfiniteLine(pos=x_pos, angle=90, pen=pen, movable=True,
                                 label=label,
                                 labelOpts={"color": color, "position": 0.95})
            line.sigPositionChanged.connect(
                lambda l, cid=cursor_id: self._on_cursor_moved(l.value(), cid))
            pi.addItem(line)
            setattr(self, attr, line)

    def _on_cursor_moved(self, x_pos, cursor_id):
        self._cursors[cursor_id] = x_pos
        if self._mode == "split":
            for lane in self._lanes.values():
                lane.update_cursor(cursor_id, x_pos)
        self._emit_cursor_values()

    def _emit_cursor_values(self):
        result = {}
        for cid, t_pos in self._cursors.items():
            if t_pos is None:
                continue
            vals = {"time": t_pos}
            for trace in self.traces:
                if not trace.visible:
                    continue
                lane = self._lanes.get(trace.name)
                if lane:
                    v = lane.get_value_at(t_pos)
                    if v is not None:
                        vals[trace.name] = v
                else:
                    t = trace.time_axis; y = trace.processed_data
                    if len(t) > 0:
                        idx = np.searchsorted(t, t_pos)
                        if 0 < idx < len(t):
                            t0, t1 = t[idx-1], t[idx]
                            y0, y1 = y[idx-1], y[idx]
                            if t1 != t0:
                                vals[trace.name] = float(
                                    y0 + (y1-y0)*(t_pos-t0)/(t1-t0))
            result[cid] = vals
        self.cursor_values_changed.emit(result)

    # ── View range ────────────────────────────────────────────────────

    def get_current_view_range(self) -> Tuple[float, float]:
        if self._lanes:
            first = next(iter(self._lanes.values()))
            vr = first.getPlotItem().viewRange()
            return vr[0][0], vr[0][1]
        if self._mode == "overlay":
            vr = self._overlay_widget.getPlotItem().viewRange()
            return vr[0][0], vr[0][1]
        return 0.0, 1.0

    def get_current_y_range(self) -> Tuple[float, float]:
        if self._lanes:
            first = next(iter(self._lanes.values()))
            vr = first.getPlotItem().viewRange()
            return vr[1][0], vr[1][1]
        if self._mode == "overlay":
            vr = self._overlay_widget.getPlotItem().viewRange()
            return vr[1][0], vr[1][1]
        return -1.0, 1.0

    def _update_range_bar(self):
        x0, x1 = self.get_current_view_range()
        y0, y1 = self.get_current_y_range()
        self._range_bar.update_display(x0, x1, y0, y1)

    def _on_range_bar_changed(self, x0, x1, y0, y1):
        self.zoom_x_range(x0, x1)
        self.zoom_y_range(y0, y1)

    def zoom_full(self):
        if self._mode == "split":
            for lane in self._lanes.values():
                lane.getPlotItem().enableAutoRange()
        else:
            self._overlay_widget.getPlotItem().enableAutoRange()

    def zoom_fit_x(self):
        if self._mode == "split":
            for lane in self._lanes.values():
                lane.getPlotItem().enableAutoRange(axis="x")
        else:
            self._overlay_widget.getPlotItem().enableAutoRange(axis="x")

    def zoom_fit_y(self):
        if self._mode == "split":
            for lane in self._lanes.values():
                lane.getPlotItem().enableAutoRange(axis="y")
        else:
            self._overlay_widget.getPlotItem().enableAutoRange(axis="y")

    def zoom_x_range(self, x_start, x_end):
        if self._mode == "split":
            for lane in self._lanes.values():
                lane.getPlotItem().setXRange(x_start, x_end, padding=0)
        else:
            self._overlay_widget.getPlotItem().setXRange(
                x_start, x_end, padding=0)

    def zoom_y_range(self, y_start, y_end):
        if self._mode == "split":
            for lane in self._lanes.values():
                lane.getPlotItem().setYRange(y_start, y_end, padding=0)
        else:
            self._overlay_widget.getPlotItem().setYRange(
                y_start, y_end, padding=0)

    def zoom_in(self, factor=0.5):
        x0, x1 = self.get_current_view_range()
        mid = (x0 + x1) / 2
        half = (x1 - x0) * factor / 2
        self.zoom_x_range(mid - half, mid + half)

    def zoom_out(self, factor=2.0):
        self.zoom_in(factor)

    def pan_x(self, fraction):
        x0, x1 = self.get_current_view_range()
        dx = (x1 - x0) * fraction
        self.zoom_x_range(x0 + dx, x1 + dx)

    def take_screenshot(self, filepath, scale=2, branding_path=""):
        """
        Grab plot + status bar together (no range-edit bar in screenshot).
        The scope status bar is expected to be a sibling widget managed
        by the parent; we grab only the plot scroll/overlay area.
        """
        from PyQt6.QtCore import Qt as _Qt
        from PyQt6.QtGui import QPainter, QImage

        # Only grab the plot area (scroll or overlay), not the range bar
        if self._mode == "split":
            grab_widget = self._scroll
        else:
            grab_widget = self._overlay_widget
        pixmap = grab_widget.grab()

        if scale > 1:
            img = pixmap.toImage()
            img = img.scaled(
                img.width() * scale, img.height() * scale,
                _Qt.AspectRatioMode.KeepAspectRatio,
                _Qt.TransformationMode.SmoothTransformation)
        else:
            img = pixmap.toImage()

        # Composite branding SVG into bottom-left corner
        if branding_path:
            img = _composite_branding(img, branding_path)

        img.save(filepath)
        return True
