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
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QAction
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from core.trace_model import TraceModel
from core.draw_mode import (
    DEFAULT_DENSITY_PEN_MAPPING,
    DEFAULT_DRAW_MODE,
    RenderViewport,
    create_density_estimator,
    resolve_pen_width,
)

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


def _upsample_for_display(
        t: np.ndarray, y: np.ndarray,
        interp_mode: str, viewport_min_pts: int,
) -> Tuple[np.ndarray, np.ndarray]:
    """Apply sinc/cubic upsampling to a short data segment if needed.

    Used for retrigger result curves so they receive the same display
    interpolation as the raw trace lanes.  The segment is already scoped
    to the view window, so a simple point-count check is sufficient.
    """
    if interp_mode not in ("sinc", "cubic") or len(t) < 4:
        return t, y
    if len(t) >= viewport_min_pts:
        return t, y
    if interp_mode == "cubic":
        return cubic_interpolate_to_n(t, y, viewport_min_pts)
    return sinc_interpolate_to_n(t, y, viewport_min_pts)


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



def _theme_name(theme) -> str:
    return getattr(theme, "file_id", "dark")


def _plot_colors_from_theme(theme) -> dict:
    return {
        "background": theme.pv("scope_bg"),
        "grid":       theme.pv("scope_grid"),
        "text":       theme.pv("text"),
        "cursor_a":   theme.pv("cursor_a"),
        "cursor_b":   theme.pv("cursor_b"),
    }


@dataclass(frozen=True)
class TraceStyleContext:
    theme: object
    plot_colors: dict
    theme_name: str
    draw_mode: str
    density_pen_mapping: dict


def _style_context_from_theme(theme,
                              draw_mode: str = DEFAULT_DRAW_MODE,
                              density_pen_mapping: Optional[dict] = None
                              ) -> TraceStyleContext:
    return TraceStyleContext(
        theme=theme,
        plot_colors=_plot_colors_from_theme(theme),
        theme_name=_theme_name(theme),
        draw_mode=draw_mode,
        density_pen_mapping=dict(
            DEFAULT_DENSITY_PEN_MAPPING if density_pen_mapping is None
            else density_pen_mapping),
    )


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

    def __init__(self, trace: TraceModel, style_context: TraceStyleContext,
                 y_lock_auto: bool = True,
                 interp_mode: str = "linear",
                 lane_label_size: int = 8,
                 show_lane_labels: bool = True,
                 allow_theme_force_labels: bool = False,
                 parent=None):
        self._y_axis = EngineeringAxisItem(orientation="left")
        self._x_axis = EngineeringTimeAxisItem(orientation="bottom")
        unit = getattr(trace, 'unit', '') or ''
        self._y_axis.set_unit(unit)
        super().__init__(parent=parent,
                         background=style_context.plot_colors["background"],
                         axisItems={"left": self._y_axis,
                                    "bottom": self._x_axis})
        self.trace = trace
        self._style_context = style_context
        self.y_lock_auto = y_lock_auto
        self.interp_mode = interp_mode   # "linear" or "sinc"
        self.viewport_min_pts = 1024      # minimum display points; set from settings
        self._lane_label_size: int = lane_label_size
        self._show_lane_labels: bool = show_lane_labels
        self._allow_theme_force_labels: bool = allow_theme_force_labels
        self._curve = None
        self._persist_curves: list = []   # ghost curves for persistence layers
        self._retrigger_curve = None      # averaged / interpolated override curve
        self._original_display_mode: Optional[str] = None  # "dimmed"|"dashed"|"hide"
        self._original_dimmed_opacity: float = 0.5
        self._original_dash_pattern: Optional[list] = None
        self._cursors: Dict[int, InfiniteLine] = {}
        self._labels: list = []          # TextItem labels anchored to time positions
        self._sinc_active = False         # True when sinc was actually used this draw
        self._render_t = np.array([])
        self._render_y = np.array([])
        self._visible_samples = 0
        self._density_estimator = create_density_estimator(
            self._style_context.draw_mode)
        self._last_style_key = None

        self._setup_plot()
        self._add_trace_curve()

        # Re-render when view range changes (viewport-aware interp)
        self.getPlotItem().sigRangeChanged.connect(self._on_view_changed)
        self.getPlotItem().sigRangeChanged.connect(
            lambda: self.view_range_changed.emit(self))

        # Floating trace name label pinned to top-right of canvas
        self._setup_trace_label_item()

    def _on_view_changed(self):
        """Re-draw curve when zoomed in enough that sinc/cubic would kick in."""
        if self.interp_mode in ("sinc", "cubic"):
            self._add_trace_curve()
        else:
            self._apply_resolved_style()
            self._reapply_original_style()
        self._reposition_trace_label()

    def _setup_plot(self):
        pi = self.getPlotItem()
        pi.showGrid(x=True, y=True, alpha=0.3)
        pi.setMenuEnabled(False)
        pi.getAxis("left").setWidth(60)
        pi.getAxis("top").setStyle(showValues=False)
        pi.getAxis("right").setStyle(showValues=False)
        self.apply_style(self._style_context)
        self.setMouseTracking(True)
        if self.y_lock_auto:
            pi.setMouseEnabled(x=True, y=False)

    def _display_color(self) -> str:
        color = self.trace.sync_theme_color(self._style_context.theme)
        return _effective_color(color, self._style_context.theme_name)

    def apply_style(self, style_context: TraceStyleContext):
        self._style_context = style_context
        self._density_estimator = create_density_estimator(style_context.draw_mode)
        plot_colors = style_context.plot_colors
        pi = self.getPlotItem()
        self.setBackground(plot_colors["background"])
        pi.setLabel("left", "")                    # trace name lives in overlay, not axis
        try:
            pi.getAxis("left").showLabel(False)     # suppress label area and (x0.001) clutter
        except Exception:
            pass
        for ax_name in ("left", "bottom", "top", "right"):
            ax = pi.getAxis(ax_name)
            pen = pg.mkPen(color=plot_colors["text"], width=1)
            ax.setPen(pen)
            ax.setTextPen(pen)
        self._last_style_key = None
        self._apply_resolved_style()
        self._redraw_labels()
        self._update_trace_label_item()
        self.update()
        self.repaint()

    def apply_theme(self, theme):
        self.apply_style(_style_context_from_theme(
            theme,
            self._style_context.draw_mode,
            self._style_context.density_pen_mapping))

    # ── Floating trace label overlay ──────────────────────────────────

    def _label_visible(self) -> bool:
        """True when the overlay label should be shown."""
        if self._show_lane_labels:
            return True
        if self._allow_theme_force_labels:
            return bool(getattr(self._style_context.theme, 'force_labels', False))
        return False

    def _setup_trace_label_item(self):
        """Create a TextItem pinned to the top-right corner of the plot canvas."""
        disp_color = self._display_color()
        bg_color = QColor(
            self._style_context.plot_colors.get("background", "#0d0d0d"))
        bg_color.setAlpha(210)
        self._trace_label_item = pg.TextItem(
            text=self.trace.label,
            color=disp_color,
            fill=pg.mkBrush(bg_color),
            anchor=(1.0, 0.0),   # top-right corner of text box at setPos point
        )
        font = QFont()
        font.setPointSize(self._lane_label_size)
        font.setBold(True)
        self._trace_label_item.setFont(font)
        self.getPlotItem().addItem(self._trace_label_item, ignoreBounds=True)
        self._trace_label_item.setVisible(self._label_visible())
        self._reposition_trace_label()

    def _update_trace_label_item(self):
        """Update text, colour and visibility of the floating label; no-op until item exists."""
        if not hasattr(self, '_trace_label_item'):
            return
        disp_color = self._display_color()
        bg_color = QColor(
            self._style_context.plot_colors.get("background", "#0d0d0d"))
        bg_color.setAlpha(210)
        self._trace_label_item.fill = pg.mkBrush(bg_color)
        self._trace_label_item.setColor(disp_color)
        self._trace_label_item.setVisible(self._label_visible())
        self._trace_label_item.setText(self.trace.label)  # triggers repaint
        self._reposition_trace_label()

    def _reposition_trace_label(self):
        """Move the label to the top-right of the current viewport."""
        if not hasattr(self, '_trace_label_item'):
            return
        try:
            vr = self.getPlotItem().viewRange()
            self._trace_label_item.setPos(vr[0][1], vr[1][1])
        except Exception:
            pass

    def set_lane_label_settings(self, size: int, show: bool, allow_force: bool):
        """Update label size and visibility; safe to call at any time."""
        self._lane_label_size = size
        self._show_lane_labels = show
        self._allow_theme_force_labels = allow_force
        if not hasattr(self, '_trace_label_item'):
            return
        font = QFont()
        font.setPointSize(size)
        font.setBold(True)
        self._trace_label_item.setFont(font)
        self._trace_label_item.setVisible(self._label_visible())
        self._trace_label_item.setText(self.trace.label)  # trigger repaint

    def _current_viewport(self) -> RenderViewport:
        x_range, y_range = self.getPlotItem().viewRange()
        vb = self.getPlotItem().vb
        width_px = max(1.0, float(vb.width()))
        height_px = max(1.0, float(vb.height()))
        return RenderViewport(
            width_px=width_px,
            height_px=height_px,
            x_range=(float(x_range[0]), float(x_range[1])),
            y_range=(float(y_range[0]), float(y_range[1])),
            visible_samples=int(self._visible_samples),
        )

    def _update_visible_samples(self, viewport: Optional[RenderViewport] = None):
        viewport = viewport or self._current_viewport()
        x0, x1 = viewport.x_range
        visible_mask = (self.trace.time_axis >= x0) & (self.trace.time_axis <= x1)
        self._visible_samples = int(visible_mask.sum()) or len(self.trace.time_axis)

    def _density_source_points(self, viewport: RenderViewport) -> Tuple[np.ndarray, np.ndarray]:
        x0, x1 = viewport.x_range
        if self.interp_mode in ("sinc", "cubic") and self._sinc_active and len(self._render_t):
            t_points = self._render_t
            y_points = self._render_y
        else:
            t_points, y_points = self.trace.windowed_data(x0, x1)
            if len(t_points) < 2:
                t_points = self.trace.time_axis
                y_points = self.trace.processed_data

        max_points = self._density_estimator.max_segments + 1
        if len(t_points) > max_points:
            idx = np.linspace(0, len(t_points) - 1, max_points, dtype=int)
            t_points = t_points[idx]
            y_points = y_points[idx]
        return t_points, y_points

    def _screen_points(self, viewport: RenderViewport) -> np.ndarray:
        t_points, y_points = self._density_source_points(viewport)
        if len(t_points) == 0:
            return np.empty((0, 2), dtype=float)
        x0, x1 = viewport.x_range
        y0, y1 = viewport.y_range
        dx = max(1e-12, x1 - x0)
        dy = max(1e-12, y1 - y0)
        x_px = (t_points - x0) / dx * viewport.width_px
        y_px = (y_points - y0) / dy * viewport.height_px
        return np.column_stack((x_px, y_px))

    def _resolved_pen_width(self) -> float:
        viewport = self._current_viewport()
        self._update_visible_samples(viewport)
        style_key = (
            round(viewport.width_px, 2),
            round(viewport.height_px, 2),
            round(viewport.x_range[0], 9),
            round(viewport.x_range[1], 9),
            round(viewport.y_range[0], 9),
            round(viewport.y_range[1], 9),
            viewport.visible_samples,
            len(self._render_t),
            self._style_context.draw_mode,
            tuple(sorted(self._style_context.density_pen_mapping.items())),
        )
        if style_key == self._last_style_key and self._curve is not None:
            return float(self._curve.opts["pen"].widthF())
        density = self._density_estimator.compute(
            self.trace,
            self._screen_points(viewport),
            viewport,
        )
        self._last_style_key = style_key
        return resolve_pen_width(density, self._style_context.density_pen_mapping)

    def _apply_resolved_style(self):
        if self._curve is None:
            return
        color = self._display_color()
        width = self._resolved_pen_width()
        self._curve.setPen(pg.mkPen(color=color, width=width))
        self._curve.update()

    def update_render_style(self):
        self._apply_resolved_style()

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

        self._update_visible_samples()

        t, y = downsample_for_display(t_full, y_full)
        self._render_t = t
        self._render_y = y
        self._last_style_key = None
        color = self._display_color()
        pen = pg.mkPen(color=color, width=resolve_pen_width(
            0.0, self._style_context.density_pen_mapping))
        self._curve = self.plot(t, y, pen=pen, antialias=False)
        self._curve.setDownsampling(auto=True, method="peak")
        self._curve.setClipToView(True)
        self._apply_resolved_style()
        self._reapply_original_style()   # restore dimmed/dashed/hidden if active
        if self._persist_curves:
            # Always keep main curve above all persistence ghost layers
            self._curve.setZValue(len(self._persist_curves) + 1)
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
            color = self._display_color()
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

    # ── Persistence / retrigger overlay ───────────────────────────────────────

    def set_persistence_layers(self, layers: list, t_ref: float = 0.0):
        """Overlay ghost traces for persistence mode."""
        self.clear_persistence_layers()
        color_hex = self._display_color()
        for layer in layers:
            t_plot = layer.time + t_ref
            d_plot = layer.data
            # Apply sinc/cubic interpolation to ghost layers when active
            if (self.interp_mode in ("sinc", "cubic")
                    and len(t_plot) >= 4
                    and len(t_plot) < self.viewport_min_pts):
                if self.interp_mode == "cubic":
                    t_plot, d_plot = cubic_interpolate_to_n(
                        t_plot, d_plot, self.viewport_min_pts)
                else:
                    t_plot, d_plot = sinc_interpolate_to_n(
                        t_plot, d_plot, self.viewport_min_pts)
            c = QColor(color_hex)
            c.setAlphaF(max(0.0, min(1.0, layer.opacity)))
            pen = pg.mkPen(color=c, width=max(0.5, 1.5 * layer.width_multiplier))
            curve = self.plot(t_plot, d_plot, pen=pen, antialias=False)
            curve.setZValue(layer.z_order)
            self._persist_curves.append(curve)
        if self._curve is not None:
            # Keep main curve above all ghost layers regardless of count
            self._curve.setZValue(len(self._persist_curves) + 1)

    def clear_persistence_layers(self):
        for c in self._persist_curves:
            try:
                self.removeItem(c)
            except Exception:
                pass
        self._persist_curves.clear()
        if self._curve is not None:
            self._curve.setZValue(0)

    def _reapply_original_style(self):
        """Apply dimmed/dashed/hidden styling to the raw trace curve when a
        result curve is active.  No-op when no result curve is set."""
        if self._curve is None or self._original_display_mode is None:
            return
        mode = self._original_display_mode
        color = self._display_color()
        width = float(self._curve.opts["pen"].widthF()) or 1.5
        if mode == "hide":
            self._curve.setVisible(False)
        elif mode == "dimmed":
            c = QColor(color)
            c.setAlphaF(self._original_dimmed_opacity)
            self._curve.setPen(pg.mkPen(color=c, width=width))
            self._curve.setVisible(True)
        elif mode == "dashed":
            pen = pg.mkPen(color=color, width=width)
            if self._original_dash_pattern:
                pen.setStyle(Qt.PenStyle.CustomDashLine)
                pen.setDashPattern(self._original_dash_pattern)
            else:
                pen.setStyle(Qt.PenStyle.DashLine)
            self._curve.setPen(pen)
            self._curve.setVisible(True)

    def set_retrigger_curve(self, time_abs: np.ndarray, data: np.ndarray,
                             original_display: str = "dimmed",
                             dimmed_opacity: float = 0.5,
                             dash_pattern: Optional[list] = None):
        """Show averaged/interpolated result as the solid hard line;
        style the raw trace according to original_display."""
        self.clear_retrigger_curve()
        self._original_display_mode = original_display
        self._original_dimmed_opacity = max(0.1, min(0.9, dimmed_opacity))
        self._original_dash_pattern = dash_pattern
        # Apply sinc/cubic upsampling if the mode is active
        t_plot, d_plot = _upsample_for_display(
            time_abs, data, self.interp_mode, self.viewport_min_pts)
        # Result curve — solid, full opacity, slightly wider
        color = self._display_color()
        pen = pg.mkPen(color=color, width=2.0)
        self._retrigger_curve = self.plot(t_plot, d_plot, pen=pen, antialias=False)
        self._retrigger_curve.setZValue(15)
        self._reapply_original_style()

    def clear_retrigger_curve(self):
        if self._retrigger_curve is not None:
            try:
                self.removeItem(self._retrigger_curve)
            except Exception:
                pass
            self._retrigger_curve = None
        # Restore raw trace to normal solid appearance
        self._original_display_mode = None
        if self._curve is not None:
            self._curve.setVisible(True)
            self._apply_resolved_style()

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
            self.trace.set_user_color(c.name())
            self.refresh_curve()
            self._update_trace_label_item()

    def _rename(self):
        text, ok = QInputDialog.getText(
            self, "Rename", "New label:", text=self.trace.label)
        if ok and text:
            self.trace.label = text
            self._update_trace_label_item()


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


class OverlayTraceVisual:
    def __init__(self, plot_item, trace: TraceModel,
                 style_context: TraceStyleContext,
                 interp_mode: str = "linear",
                 viewport_min_pts: int = 1024):
        self.plot_item = plot_item
        self.trace = trace
        self._style_context = style_context
        self.interp_mode = interp_mode
        self.viewport_min_pts = viewport_min_pts
        self._density_estimator = create_density_estimator(style_context.draw_mode)
        self._render_t = np.array([])
        self._render_y = np.array([])
        self._visible_samples = 0
        self._interpolated_view = False
        self._last_style_key = None
        self._persist_curves: list = []
        self._retrigger_curve = None
        self._original_display_mode: Optional[str] = None
        self._original_dimmed_opacity: float = 0.5
        self._original_dash_pattern: Optional[list] = None
        self.curve = self.plot_item.plot([], [], pen=pg.mkPen(width=1.5),
                                         name=trace.label, antialias=False)
        self.curve.setDownsampling(auto=True, method="peak")
        self.curve.setClipToView(True)
        self.apply_style(style_context)

    def _display_color(self) -> str:
        color = self.trace.sync_theme_color(self._style_context.theme)
        return _effective_color(color, self._style_context.theme_name)

    def apply_style(self, style_context: TraceStyleContext):
        self._style_context = style_context
        self._density_estimator = create_density_estimator(style_context.draw_mode)
        self._last_style_key = None
        self._apply_resolved_style()

    def apply_theme(self, theme):
        self.apply_style(_style_context_from_theme(
            theme,
            self._style_context.draw_mode,
            self._style_context.density_pen_mapping))

    def _current_viewport(self) -> RenderViewport:
        x_range, y_range = self.plot_item.viewRange()
        vb = self.plot_item.vb
        width_px = max(1.0, float(vb.width()))
        height_px = max(1.0, float(vb.height()))
        return RenderViewport(
            width_px=width_px,
            height_px=height_px,
            x_range=(float(x_range[0]), float(x_range[1])),
            y_range=(float(y_range[0]), float(y_range[1])),
            visible_samples=int(self._visible_samples),
        )

    def _update_visible_samples(self, viewport: Optional[RenderViewport] = None):
        viewport = viewport or self._current_viewport()
        x0, x1 = viewport.x_range
        visible_mask = (self.trace.time_axis >= x0) & (self.trace.time_axis <= x1)
        self._visible_samples = int(visible_mask.sum()) or len(self.trace.time_axis)

    def _density_source_points(self, viewport: RenderViewport) -> Tuple[np.ndarray, np.ndarray]:
        x0, x1 = viewport.x_range
        if self.interp_mode in ("sinc", "cubic") and self._interpolated_view and len(self._render_t):
            t_points = self._render_t
            y_points = self._render_y
        else:
            t_points, y_points = self.trace.windowed_data(x0, x1)
            if len(t_points) < 2:
                t_points = self.trace.time_axis
                y_points = self.trace.processed_data

        max_points = self._density_estimator.max_segments + 1
        if len(t_points) > max_points:
            idx = np.linspace(0, len(t_points) - 1, max_points, dtype=int)
            t_points = t_points[idx]
            y_points = y_points[idx]
        return t_points, y_points

    def _screen_points(self, viewport: RenderViewport) -> np.ndarray:
        t_points, y_points = self._density_source_points(viewport)
        if len(t_points) == 0:
            return np.empty((0, 2), dtype=float)
        x0, x1 = viewport.x_range
        y0, y1 = viewport.y_range
        dx = max(1e-12, x1 - x0)
        dy = max(1e-12, y1 - y0)
        x_px = (t_points - x0) / dx * viewport.width_px
        y_px = (y_points - y0) / dy * viewport.height_px
        return np.column_stack((x_px, y_px))

    def _resolved_pen_width(self) -> float:
        viewport = self._current_viewport()
        self._update_visible_samples(viewport)
        style_key = (
            round(viewport.width_px, 2),
            round(viewport.height_px, 2),
            round(viewport.x_range[0], 9),
            round(viewport.x_range[1], 9),
            round(viewport.y_range[0], 9),
            round(viewport.y_range[1], 9),
            viewport.visible_samples,
            len(self._render_t),
            self._style_context.draw_mode,
            tuple(sorted(self._style_context.density_pen_mapping.items())),
        )
        if style_key == self._last_style_key:
            return float(self.curve.opts["pen"].widthF())
        density = self._density_estimator.compute(
            self.trace,
            self._screen_points(viewport),
            viewport,
        )
        self._last_style_key = style_key
        return resolve_pen_width(density, self._style_context.density_pen_mapping)

    def _apply_resolved_style(self):
        width = self._resolved_pen_width()
        self.curve.setPen(pg.mkPen(color=self._display_color(), width=width))
        self.curve.update()

    def update_render_style(self):
        self._apply_resolved_style()
        self._reapply_original_style()

    def refresh_curve(self, view_range: Tuple[float, float]):
        t_full = self.trace.time_axis
        y_full = self.trace.processed_data
        x0, x1 = view_range
        self._interpolated_view = False
        self._update_visible_samples(RenderViewport(
            width_px=max(1.0, float(self.plot_item.vb.width())),
            height_px=max(1.0, float(self.plot_item.vb.height())),
            x_range=(x0, x1),
            y_range=tuple(self.plot_item.viewRange()[1]),
            visible_samples=self._visible_samples,
        ))

        if self.interp_mode in ("sinc", "cubic"):
            mask = (t_full >= x0) & (t_full <= x1)
            n_vis = int(mask.sum()) or len(t_full)
            if n_vis < self.viewport_min_pts and n_vis >= 4:
                t_s = t_full[mask] if n_vis < len(t_full) else t_full
                y_s = y_full[mask] if n_vis < len(y_full) else y_full
                if self.interp_mode == "cubic":
                    t_s, y_s = cubic_interpolate_to_n(
                        t_s, y_s, self.viewport_min_pts)
                else:
                    t_s, y_s = sinc_interpolate_to_n(
                        t_s, y_s, self.viewport_min_pts)
                t_full, y_full = t_s, y_s
                self._interpolated_view = True

        t_data, y_data = downsample_for_display(t_full, y_full)
        self._render_t = t_data
        self._render_y = y_data
        self._last_style_key = None
        self.curve.setData(t_data, y_data)
        self.curve.opts["name"] = self.trace.label
        self._apply_resolved_style()
        self._reapply_original_style()
        if self._persist_curves:
            self.curve.setZValue(len(self._persist_curves) + 1)

    # ── Persistence / retrigger overlay ───────────────────────────────────────

    def set_persistence_layers(self, layers: list, t_ref: float = 0.0):
        self.clear_persistence_layers()
        color_hex = self._display_color()
        for layer in layers:
            t_plot = layer.time + t_ref
            d_plot = layer.data
            # Apply sinc/cubic interpolation to ghost layers when active
            if (self.interp_mode in ("sinc", "cubic")
                    and len(t_plot) >= 4
                    and len(t_plot) < self.viewport_min_pts):
                if self.interp_mode == "cubic":
                    t_plot, d_plot = cubic_interpolate_to_n(
                        t_plot, d_plot, self.viewport_min_pts)
                else:
                    t_plot, d_plot = sinc_interpolate_to_n(
                        t_plot, d_plot, self.viewport_min_pts)
            c = QColor(color_hex)
            c.setAlphaF(max(0.0, min(1.0, layer.opacity)))
            pen = pg.mkPen(color=c, width=max(0.5, 1.5 * layer.width_multiplier))
            curve = self.plot_item.plot(t_plot, d_plot, pen=pen, antialias=False)
            curve.setZValue(layer.z_order)
            self._persist_curves.append(curve)
        self.curve.setZValue(len(self._persist_curves) + 1)

    def clear_persistence_layers(self):
        for c in self._persist_curves:
            try:
                self.plot_item.removeItem(c)
            except Exception:
                pass
        self._persist_curves.clear()
        self.curve.setZValue(0)

    def _reapply_original_style(self):
        if self._original_display_mode is None:
            return
        mode = self._original_display_mode
        color = self._display_color()
        width = float(self.curve.opts["pen"].widthF()) or 1.5
        if mode == "hide":
            self.curve.setVisible(False)
        elif mode == "dimmed":
            c = QColor(color)
            c.setAlphaF(self._original_dimmed_opacity)
            self.curve.setPen(pg.mkPen(color=c, width=width))
            self.curve.setVisible(True)
        elif mode == "dashed":
            pen = pg.mkPen(color=color, width=width)
            if self._original_dash_pattern:
                pen.setStyle(Qt.PenStyle.CustomDashLine)
                pen.setDashPattern(self._original_dash_pattern)
            else:
                pen.setStyle(Qt.PenStyle.DashLine)
            self.curve.setPen(pen)
            self.curve.setVisible(True)

    def set_retrigger_curve(self, time_abs: np.ndarray, data: np.ndarray,
                             original_display: str = "dimmed",
                             dimmed_opacity: float = 0.5,
                             dash_pattern: Optional[list] = None):
        self.clear_retrigger_curve()
        self._original_display_mode = original_display
        self._original_dimmed_opacity = max(0.1, min(0.9, dimmed_opacity))
        self._original_dash_pattern = dash_pattern
        t_plot, d_plot = _upsample_for_display(
            time_abs, data, self.interp_mode, self.viewport_min_pts)
        color = self._display_color()
        pen = pg.mkPen(color=color, width=2.0)
        self._retrigger_curve = self.plot_item.plot(
            t_plot, d_plot, pen=pen, antialias=False)
        self._retrigger_curve.setZValue(15)
        self._reapply_original_style()

    def clear_retrigger_curve(self):
        if self._retrigger_curve is not None:
            try:
                self.plot_item.removeItem(self._retrigger_curve)
            except Exception:
                pass
            self._retrigger_curve = None
        self._original_display_mode = None
        self.curve.setVisible(True)
        self._apply_resolved_style()

    def remove(self):
        self.clear_persistence_layers()
        self.clear_retrigger_curve()
        self.plot_item.removeItem(self.curve)


class ScopePlotWidget(QWidget):
    cursor_values_changed = pyqtSignal(dict)

    sinc_active_changed = pyqtSignal(bool)  # emitted when sinc kicks in/out
    view_changed        = pyqtSignal()       # emitted (throttled) on pan/zoom

    def __init__(self, theme_manager, y_lock_auto: bool = True,
                 interp_mode: str = "linear",
                 viewport_min_pts: int = 1024,
                 draw_mode: str = DEFAULT_DRAW_MODE,
                 density_pen_mapping: Optional[dict] = None,
                 lane_label_size: int = 8,
                 show_lane_labels: bool = True,
                 allow_theme_force_labels: bool = False,
                 lane_label_spacing: float = 0.3,
                 parent=None):
        super().__init__(parent)
        self._theme_manager = theme_manager
        self._active_theme = theme_manager.active_theme
        self._draw_mode = draw_mode or DEFAULT_DRAW_MODE
        self._density_pen_mapping = dict(
            DEFAULT_DENSITY_PEN_MAPPING if density_pen_mapping is None
            else density_pen_mapping)
        self._style_context = _style_context_from_theme(
            self._active_theme, self._draw_mode, self._density_pen_mapping)
        self.y_lock_auto = y_lock_auto
        self.interp_mode = interp_mode
        self.viewport_min_pts = viewport_min_pts
        self._lane_label_size: int = lane_label_size
        self._show_lane_labels: bool = show_lane_labels
        self._allow_theme_force_labels: bool = allow_theme_force_labels
        self._lane_label_spacing: float = lane_label_spacing
        self._overlay_legend_items: list = []
        self._last_sinc_active = False
        self.traces: List[TraceModel] = []
        self._lanes: Dict[str, TraceLane] = {}
        self._mode = "split"
        self._cursors = {0: None, 1: None}
        self._cursor_colors = {
            0: self._style_context.plot_colors["cursor_a"],
            1: self._style_context.plot_colors["cursor_b"],
        }
        self._overlay_visuals: Dict[str, OverlayTraceVisual] = {}
        self._overlay_z_order: List[str] = []  # for bring-to-front
        # Persistence / retrigger state — reapplied after every rebuild
        self._persist_state: Dict[str, tuple] = {}       # name->(layers, t_ref)
        self._retrigger_curve_state: Dict[str, tuple] = {}  # name->(t_abs, data)

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
            background=self._style_context.plot_colors["background"])
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
        self._theme_manager.themeChanged.connect(self._on_theme_changed)

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
            pen = pg.mkPen(color=self._style_context.plot_colors["text"], width=1)
            ax.setPen(pen)
            ax.setTextPen(pen)
        pi.sigRangeChanged.connect(lambda: self._range_timer.start())
        pi.sigRangeChanged.connect(self._on_overlay_range_changed)
        pi.sigRangeChanged.connect(self._reposition_overlay_legend)
        self._overlay_widget.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self._overlay_widget.customContextMenuRequested.connect(
            self._overlay_context_menu)
        if self.y_lock_auto:
            pi.setMouseEnabled(x=True, y=False)

    def _apply_plot_theme(self, theme):
        self._active_theme = theme
        self._style_context = _style_context_from_theme(
            theme, self._draw_mode, self._density_pen_mapping)
        plot_colors = self._style_context.plot_colors
        self._overlay_widget.setBackground(plot_colors["background"])
        pi = self._overlay_widget.getPlotItem()
        for ax_name in ("left", "bottom"):
            ax = pi.getAxis(ax_name)
            pen = pg.mkPen(color=plot_colors["text"], width=1)
            ax.setPen(pen)
            ax.setTextPen(pen)
        self._cursor_colors = {
            0: plot_colors["cursor_a"],
            1: plot_colors["cursor_b"],
        }

    def _refresh_cursor_styles(self):
        for cid, x_pos in self._cursors.items():
            if x_pos is not None:
                self._place_cursors(cid, x_pos)

    def _on_theme_changed(self, theme):
        self._apply_plot_theme(theme)
        for lane in self._lanes.values():
            lane.apply_theme(theme)
        for visual in self._overlay_visuals.values():
            visual.apply_theme(theme)
        self._rebuild_overlay_legend()
        self._refresh_cursor_styles()
        self._overlay_widget.update()
        self.update()
        self.repaint()

    def set_draw_mode(self, draw_mode: str):
        self._draw_mode = draw_mode or DEFAULT_DRAW_MODE
        self._style_context = _style_context_from_theme(
            self._active_theme, self._draw_mode, self._density_pen_mapping)
        for lane in self._lanes.values():
            lane.apply_style(self._style_context)
            lane.refresh_curve()
        for trace_name, visual in self._overlay_visuals.items():
            visual.apply_style(self._style_context)
            visual.interp_mode = getattr(
                next((t for t in self.traces if t.name == trace_name), None),
                '_interp_mode_override',
                self.interp_mode)
            visual.refresh_curve(self.get_current_view_range())
        self.update()
        self.repaint()

    def set_density_pen_mapping(self, density_pen_mapping: dict):
        self._density_pen_mapping = dict(density_pen_mapping or DEFAULT_DENSITY_PEN_MAPPING)
        self.set_draw_mode(self._draw_mode)

    def _on_overlay_range_changed(self):
        """Re-render overlay curves when zoomed in (viewport sinc)."""
        if self.interp_mode == "sinc" or any(
                getattr(t, '_interp_mode_override', '') == 'sinc'
                for t in self.traces):
            self._refresh_overlay_visuals()
        else:
            for visual in self._overlay_visuals.values():
                visual.update_render_style()

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

    def get_cursor_placement_x(self, cursor_id: int) -> float:
        x0, x1 = self.get_current_view_range()
        if self._mode == "overlay":
            fraction = 0.5 if cursor_id == 0 else 0.75
            return x0 + (x1 - x0) * fraction
        mid = (x0 + x1) / 2.0
        if cursor_id == 1 and self._cursors.get(0) is not None:
            return self._cursors[0] + (x1 - x0) * 0.1
        return mid

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
        self._persist_state.clear()
        self._retrigger_curve_state.clear()
        self._rebuild()

    def remove_trace(self, trace_name: str):
        self.traces = [t for t in self.traces if t.name != trace_name]
        self._persist_state.pop(trace_name, None)
        self._retrigger_curve_state.pop(trace_name, None)
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
        visual = self._overlay_visuals.get(trace_name)
        if visual:
            visual.interp_mode = mode
            visual.refresh_curve(self.get_current_view_range())

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
            self._refresh_overlay_visuals()

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
            lane = TraceLane(trace, self._style_context,
                              self.y_lock_auto,
                              getattr(trace, '_interp_mode_override',
                                      self.interp_mode),
                              lane_label_size=self._lane_label_size,
                              show_lane_labels=self._show_lane_labels,
                              allow_theme_force_labels=self._allow_theme_force_labels)
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

        for name, (layers, t_ref) in self._persist_state.items():
            lane = self._lanes.get(name)
            if lane:
                lane.set_persistence_layers(layers, t_ref)
        for name, (t_abs, data) in self._retrigger_curve_state.items():
            lane = self._lanes.get(name)
            if lane:
                lane.set_retrigger_curve(t_abs, data)

        self._range_timer.start()

    def apply_lane_label_settings(self, size: int, show: bool, allow_force: bool,
                                   spacing: float = None):
        """Update lane label settings and propagate to all existing lanes/legend."""
        self._lane_label_size = size
        self._show_lane_labels = show
        self._allow_theme_force_labels = allow_force
        if spacing is not None:
            self._lane_label_spacing = spacing
        for lane in self._lanes.values():
            lane.set_lane_label_settings(size, show, allow_force)
        self._rebuild_overlay_legend()

    # ── Overlay legend ────────────────────────────────────────────────

    def _overlay_label_visible(self) -> bool:
        """True when the overlay legend should be shown."""
        if self._show_lane_labels:
            return True
        if self._allow_theme_force_labels:
            return bool(getattr(self._active_theme, 'force_labels', False))
        return False

    def _clear_overlay_legend(self):
        """Remove all legend TextItems from the overlay PlotItem."""
        pi = self._overlay_widget.getPlotItem()
        for item in self._overlay_legend_items:
            try:
                pi.removeItem(item)
            except Exception:
                pass
        self._overlay_legend_items.clear()

    def _rebuild_overlay_legend(self):
        """Create fresh legend TextItems for all visible traces (top-right)."""
        self._clear_overlay_legend()
        if not self._overlay_z_order or not self._overlay_label_visible():
            return
        pi = self._overlay_widget.getPlotItem()
        bg_color = QColor(
            self._style_context.plot_colors.get("background", "#0d0d0d"))
        bg_color.setAlpha(210)
        font = QFont()
        font.setPointSize(self._lane_label_size)
        font.setBold(True)
        for name in self._overlay_z_order:
            trace = next((t for t in self.traces if t.name == name), None)
            if trace is None:
                continue
            color = trace.sync_theme_color(self._active_theme)
            color = _effective_color(color, self._style_context.theme_name)
            item = pg.TextItem(
                text=trace.label,
                color=color,
                fill=pg.mkBrush(bg_color),
                anchor=(1.0, 0.0),  # top-right corner of box at setPos point
            )
            item.setFont(font)
            pi.addItem(item, ignoreBounds=True)
            self._overlay_legend_items.append(item)
        self._reposition_overlay_legend()

    def _reposition_overlay_legend(self):
        """Restack legend items in the top-right corner using screen-pixel spacing."""
        if not self._overlay_legend_items:
            return
        try:
            pi = self._overlay_widget.getPlotItem()
            vr = pi.viewRange()
            x_max = vr[0][1]
            y_max = vr[1][1]
            y_min = vr[1][0]
            h_px = max(1.0, float(pi.vb.height()))
            y_span = max(1e-12, y_max - y_min)
            # Measure actual line height from font metrics
            font = QFont()
            font.setPointSize(self._lane_label_size)
            font.setBold(True)
            font_h_px = float(QFontMetrics(font).height())
            # line slot = text height + gap (spacing_factor × text height)
            line_slot_px = font_h_px * (1.0 + self._lane_label_spacing)
            line_slot_data = line_slot_px * y_span / h_px
            for i, item in enumerate(self._overlay_legend_items):
                item.setPos(x_max, y_max - i * line_slot_data)
        except Exception:
            pass

    def _update_overlay_legend_items(self):
        """Refresh text/colors of existing items; rebuilds if count changed."""
        if len(self._overlay_legend_items) != len(self._overlay_z_order):
            self._rebuild_overlay_legend()
            return
        if not self._overlay_label_visible():
            for item in self._overlay_legend_items:
                item.setVisible(False)
            return
        bg_color = QColor(
            self._style_context.plot_colors.get("background", "#0d0d0d"))
        bg_color.setAlpha(210)
        font = QFont()
        font.setPointSize(self._lane_label_size)
        font.setBold(True)
        for item, name in zip(self._overlay_legend_items, self._overlay_z_order):
            trace = next((t for t in self.traces if t.name == name), None)
            if trace is None:
                continue
            color = trace.sync_theme_color(self._active_theme)
            color = _effective_color(color, self._style_context.theme_name)
            item.setColor(color)
            item.fill = pg.mkBrush(bg_color)
            item.setText(trace.label)
            item.setFont(font)
            item.setVisible(True)
        self._reposition_overlay_legend()

    def _refresh_overlay_visuals(self):
        visible = [t for t in self.traces if t.visible]
        visible_names = [t.name for t in visible]
        if set(visible_names) != set(self._overlay_visuals.keys()):
            self._rebuild_overlay()
            return

        view_range = self.get_current_view_range()
        self._overlay_z_order = visible_names
        unit = next((t.unit for t in visible
                     if t.unit and t.unit != 'raw'), '')
        self._ov_y_axis.set_unit(unit)

        for trace in visible:
            visual = self._overlay_visuals.get(trace.name)
            if visual is None:
                self._rebuild_overlay()
                return
            visual.trace = trace
            visual.interp_mode = getattr(trace, '_interp_mode_override',
                                         self.interp_mode)
            visual.refresh_curve(view_range)

        if self.y_lock_auto:
            self._overlay_widget.getPlotItem().enableAutoRange(axis="y")
        self._update_overlay_legend_items()
        self._range_timer.start()

    def _rebuild_overlay(self):
        pi = self._overlay_widget.getPlotItem()
        saved_view = pi.viewRange()
        had_items = bool(self._overlay_visuals)
        for visual in self._overlay_visuals.values():
            visual.remove()
        self._overlay_visuals.clear()

        visible = [t for t in self.traces if t.visible]
        self._overlay_z_order = [t.name for t in visible]

        vr = pi.viewRange()
        x0, x1 = vr[0]

        # Update Y-axis unit from first visible trace with a real unit
        unit = next((t.unit for t in visible
                     if t.unit and t.unit != 'raw'), '')
        self._ov_y_axis.set_unit(unit)

        for trace in visible:
            mode = getattr(trace, '_interp_mode_override',
                           self.interp_mode)
            visual = OverlayTraceVisual(
                pi, trace, self._style_context, mode, self.viewport_min_pts)
            visual.refresh_curve((x0, x1))
            self._overlay_visuals[trace.name] = visual

        if had_items:
            pi.setXRange(saved_view[0][0], saved_view[0][1], padding=0)
            if self.y_lock_auto:
                pi.enableAutoRange(axis="y")
            else:
                pi.setYRange(saved_view[1][0], saved_view[1][1], padding=0)
        elif self.y_lock_auto:
            pi.enableAutoRange(axis="y")

        # Re-place cursors
        for cid, t_pos in self._cursors.items():
            if t_pos is not None:
                self._place_cursors(cid, t_pos)

        for name, (layers, t_ref) in self._persist_state.items():
            visual = self._overlay_visuals.get(name)
            if visual:
                visual.set_persistence_layers(layers, t_ref)
        for name, (t_abs, data) in self._retrigger_curve_state.items():
            visual = self._overlay_visuals.get(name)
            if visual:
                visual.set_retrigger_curve(t_abs, data)

        self._rebuild_overlay_legend()
        self._range_timer.start()

    # ── Persistence / retrigger public API ────────────────────────────────────

    def set_persistence_layers(self, trace_name: str,
                               layers: list, t_ref: float = 0.0):
        """Store and display persistence ghost curves for one trace."""
        self._persist_state[trace_name] = (layers, t_ref)
        if self._mode == "split":
            lane = self._lanes.get(trace_name)
            if lane:
                lane.set_persistence_layers(layers, t_ref)
        else:
            visual = self._overlay_visuals.get(trace_name)
            if visual:
                visual.set_persistence_layers(layers, t_ref)

    def clear_persistence_layers(self, trace_name: str = None):
        """Remove persistence ghost curves for one trace, or all if None."""
        if trace_name is None:
            self._persist_state.clear()
            for lane in self._lanes.values():
                lane.clear_persistence_layers()
            for visual in self._overlay_visuals.values():
                visual.clear_persistence_layers()
        else:
            self._persist_state.pop(trace_name, None)
            lane = self._lanes.get(trace_name)
            if lane:
                lane.clear_persistence_layers()
            visual = self._overlay_visuals.get(trace_name)
            if visual:
                visual.clear_persistence_layers()

    def set_retrigger_curve(self, trace_name: str,
                            time_abs: np.ndarray, data: np.ndarray,
                            original_display: str = "dimmed",
                            dimmed_opacity: float = 0.5,
                            dash_pattern: Optional[list] = None):
        """Store and display an averaged / interpolated result curve."""
        self._retrigger_curve_state[trace_name] = (time_abs, data)
        kw = dict(original_display=original_display,
                  dimmed_opacity=dimmed_opacity,
                  dash_pattern=dash_pattern)
        if self._mode == "split":
            lane = self._lanes.get(trace_name)
            if lane:
                lane.set_retrigger_curve(time_abs, data, **kw)
        else:
            visual = self._overlay_visuals.get(trace_name)
            if visual:
                visual.set_retrigger_curve(time_abs, data, **kw)

    def clear_retrigger_curve(self, trace_name: str = None):
        """Remove retrigger result curve(s)."""
        if trace_name is None:
            self._retrigger_curve_state.clear()
            for lane in self._lanes.values():
                lane.clear_retrigger_curve()
            for visual in self._overlay_visuals.values():
                visual.clear_retrigger_curve()
        else:
            self._retrigger_curve_state.pop(trace_name, None)
            lane = self._lanes.get(trace_name)
            if lane:
                lane.clear_retrigger_curve()
            visual = self._overlay_visuals.get(trace_name)
            if visual:
                visual.clear_retrigger_curve()

    def bring_trace_to_front(self, trace_name: str):
        """Move a trace curve to the top of the overlay z-order."""
        if self._mode != "overlay":
            return
        if trace_name in self._overlay_visuals:
            curve = self._overlay_visuals[trace_name].curve
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

    def clear_cursors(self):
        """Remove both cursors from the plot and reset their positions."""
        for cid in (0, 1):
            self._cursors[cid] = None
            if self._mode == "split":
                for lane in self._lanes.values():
                    if cid in lane._cursors:
                        try:
                            lane.removeItem(lane._cursors[cid])
                        except Exception:
                            pass
                        lane._cursors.pop(cid, None)
            else:
                attr = f"_overlay_cursor_{cid}"
                if hasattr(self, attr):
                    try:
                        self._overlay_widget.getPlotItem().removeItem(
                            getattr(self, attr))
                    except Exception:
                        pass
                    delattr(self, attr)
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
        if self._mode == "overlay":
            vr = self._overlay_widget.getPlotItem().viewRange()
            return vr[0][0], vr[0][1]
        if self._lanes:
            first = next(iter(self._lanes.values()))
            vr = first.getPlotItem().viewRange()
            return vr[0][0], vr[0][1]
        return 0.0, 1.0

    def get_current_y_range(self) -> Tuple[float, float]:
        if self._mode == "overlay":
            vr = self._overlay_widget.getPlotItem().viewRange()
            return vr[1][0], vr[1][1]
        if self._lanes:
            first = next(iter(self._lanes.values()))
            vr = first.getPlotItem().viewRange()
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
