"""
core/scope_plot_widget.py
Main oscilloscope plot widget.
Supports split-lane (LeCroy MAUI style) and overlay mode.
Includes cursors, pan/zoom, etc.
"""

import numpy as np
import pyqtgraph as pg
from pyqtgraph import InfiniteLine, LinearRegionItem
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QSplitter, QScrollArea, QSizePolicy, QMenu,
                               QColorDialog, QInputDialog, QApplication)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QPen, QFont, QAction
from typing import List, Dict, Optional, Tuple
from core.trace_model import TraceModel


# Sub-samples for performance: max points to plot per trace
MAX_DISPLAY_POINTS = 50_000


def downsample_for_display(t: np.ndarray, y: np.ndarray,
                            max_pts: int = MAX_DISPLAY_POINTS
                            ) -> Tuple[np.ndarray, np.ndarray]:
    """Min-max decimation to preserve envelope shape."""
    n = len(t)
    if n <= max_pts:
        return t, y
    # Decimate while preserving min/max in each window
    window = n // (max_pts // 2)
    n_windows = n // window
    t_out = np.empty(n_windows * 2)
    y_out = np.empty(n_windows * 2)
    for i in range(n_windows):
        sl = slice(i * window, (i + 1) * window)
        yw = y[sl]
        tw = t[sl]
        imin = np.argmin(yw)
        imax = np.argmax(yw)
        if imin <= imax:
            t_out[i*2]   = tw[imin]; y_out[i*2]   = yw[imin]
            t_out[i*2+1] = tw[imax]; y_out[i*2+1] = yw[imax]
        else:
            t_out[i*2]   = tw[imax]; y_out[i*2]   = yw[imax]
            t_out[i*2+1] = tw[imin]; y_out[i*2+1] = yw[imin]
    return t_out, y_out


class TraceLane(pg.PlotWidget):
    """A single trace lane (one PlotWidget per trace in split view)."""
    cursor_moved = pyqtSignal(float, int)  # time, cursor_id (0=A, 1=B)

    def __init__(self, trace: TraceModel, theme_colors: dict,
                 split_mode: bool = True, parent=None):
        super().__init__(parent=parent, background=theme_colors["background"])
        self.trace = trace
        self.theme = theme_colors
        self.split_mode = split_mode
        self._curve = None
        self._cursors: Dict[int, InfiniteLine] = {}

        self._setup_plot()
        self._add_trace_curve()

    def _setup_plot(self):
        pi = self.getPlotItem()
        pi.showGrid(x=True, y=True,
                    alpha=0.3)
        pi.setMenuEnabled(False)

        # Label on left side
        pi.setLabel("left",
                     f"<span style='color:{self.trace.color}'>{self.trace.label}</span>",
                     color=self.theme["text"])
        pi.getAxis("left").setWidth(60)

        # Style axes
        for ax_name in ("left", "bottom", "top", "right"):
            ax = pi.getAxis(ax_name)
            ax.setPen(pg.mkPen(color=self.theme["text"], width=1))
            ax.setTextPen(pg.mkPen(color=self.theme["text"]))

        pi.getAxis("top").setStyle(showValues=False)
        pi.getAxis("right").setStyle(showValues=False)

        # Enable mouse tracking
        self.setMouseTracking(True)

    def _add_trace_curve(self):
        if self._curve is not None:
            self.removeItem(self._curve)

        t, y = downsample_for_display(
            self.trace.time_axis, self.trace.processed_data)

        pen = pg.mkPen(color=self.trace.color, width=1.5)
        self._curve = self.plot(t, y, pen=pen, antialias=False)
        self._curve.setDownsampling(auto=True, method="peak")
        self._curve.setClipToView(True)

    def refresh_curve(self):
        self._add_trace_curve()

    def add_cursor(self, cursor_id: int, x_pos: float,
                    color: str, label: str = ""):
        if cursor_id in self._cursors:
            self.removeItem(self._cursors[cursor_id])
        pen = pg.mkPen(color=color, width=1.5, style=Qt.PenStyle.DashLine)
        line = InfiniteLine(pos=x_pos, angle=90, pen=pen,
                             movable=True, label=label,
                             labelOpts={"color": color, "position": 0.95})
        line.sigPositionChanged.connect(
            lambda l, cid=cursor_id: self.cursor_moved.emit(l.value(), cid))
        self.addItem(line)
        self._cursors[cursor_id] = line

    def update_cursor(self, cursor_id: int, x_pos: float):
        if cursor_id in self._cursors:
            self._cursors[cursor_id].blockSignals(True)
            self._cursors[cursor_id].setValue(x_pos)
            self._cursors[cursor_id].blockSignals(False)

    def get_value_at(self, t_pos: float) -> Optional[float]:
        """Interpolate trace value at given time."""
        t = self.trace.time_axis
        y = self.trace.processed_data
        if len(t) < 2:
            return None
        idx = np.searchsorted(t, t_pos)
        if idx <= 0:
            return float(y[0])
        if idx >= len(t):
            return float(y[-1])
        # Linear interpolation
        t0, t1 = t[idx-1], t[idx]
        y0, y1 = y[idx-1], y[idx]
        if t1 == t0:
            return float(y0)
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
        act_reset = QAction("Auto Scale Y", self)
        act_reset.triggered.connect(lambda: self.getPlotItem().enableAutoRange(axis="y"))
        menu.addAction(act_reset)

        menu.exec(event.globalPos())

    def _change_color(self):
        c = QColorDialog.getColor(QColor(self.trace.color), self)
        if c.isValid():
            self.trace.color = c.name()
            self.refresh_curve()
            self.getPlotItem().setLabel(
                "left",
                f"<span style='color:{self.trace.color}'>{self.trace.label}</span>")

    def _rename(self):
        text, ok = QInputDialog.getText(
            self, "Rename", "New label:", text=self.trace.label)
        if ok and text:
            self.trace.label = text
            self.getPlotItem().setLabel(
                "left",
                f"<span style='color:{self.trace.color}'>{self.trace.label}</span>")


class ScopePlotWidget(QWidget):
    """
    Main oscilloscope display widget.
    Manages multiple TraceLanes with linked X axes.
    Supports split and overlay modes.
    """

    cursor_values_changed = pyqtSignal(dict)  # {cursor_id: {trace_name: value, 'time': t}}

    def __init__(self, theme_colors: dict, parent=None):
        super().__init__(parent)
        self.theme = theme_colors
        self.traces: List[TraceModel] = []
        self._lanes: Dict[str, TraceLane] = {}  # trace.name -> lane
        self._mode = "split"  # "split" or "overlay"
        self._cursors = {0: None, 1: None}  # cursor time positions
        self._cursor_colors = {
            0: theme_colors["cursor_a"],
            1: theme_colors["cursor_b"],
        }

        self._layout = QVBoxLayout(self)
        self._layout.setSpacing(1)
        self._layout.setContentsMargins(0, 0, 0, 0)

        # Container for lanes
        self._lanes_container = QWidget()
        self._lanes_layout = QVBoxLayout(self._lanes_container)
        self._lanes_layout.setSpacing(1)
        self._lanes_layout.setContentsMargins(0, 0, 0, 0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(self._lanes_container)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._layout.addWidget(self._scroll)

        # Overlay PlotWidget (hidden initially)
        self._overlay_widget = pg.PlotWidget(
            background=theme_colors["background"])
        self._overlay_widget.hide()
        self._overlay_curves: Dict[str, pg.PlotDataItem] = {}
        self._layout.addWidget(self._overlay_widget)

        self._setup_overlay()

    def _setup_overlay(self):
        pi = self._overlay_widget.getPlotItem()
        pi.showGrid(x=True, y=True, alpha=0.3)
        pi.setMenuEnabled(False)
        for ax_name in ("left", "bottom"):
            ax = pi.getAxis(ax_name)
            ax.setPen(pg.mkPen(color=self.theme["text"], width=1))
            ax.setTextPen(pg.mkPen(color=self.theme["text"]))
        pi.addLegend(offset=(10, 10))

    def set_mode(self, mode: str):
        """Switch between 'split' and 'overlay'."""
        self._mode = mode
        if mode == "overlay":
            self._scroll.hide()
            self._overlay_widget.show()
            self._rebuild_overlay()
        else:
            self._overlay_widget.hide()
            self._scroll.show()
            self._rebuild_split()

    def add_trace(self, trace: TraceModel):
        if trace.name in self._lanes:
            return
        self.traces.append(trace)
        self._rebuild()

    def remove_trace(self, trace_name: str):
        self.traces = [t for t in self.traces if t.name != trace_name]
        self._rebuild()

    def set_trace_visible(self, trace_name: str, visible: bool):
        for t in self.traces:
            if t.name == trace_name:
                t.visible = visible
        self._rebuild()

    def refresh_all(self):
        """Refresh all curve data (after processing changes)."""
        for lane in self._lanes.values():
            lane.refresh_curve()

    def _rebuild(self):
        if self._mode == "split":
            self._rebuild_split()
        else:
            self._rebuild_overlay()

    def _rebuild_split(self):
        # Clear existing lanes
        for lane in self._lanes.values():
            lane.setParent(None)
        self._lanes.clear()

        visible = [t for t in self.traces if t.visible]
        if not visible:
            return

        # Link x axes
        first_lane = None
        for trace in visible:
            lane = TraceLane(trace, self.theme, split_mode=True)
            lane.setMinimumHeight(80)

            if first_lane is None:
                first_lane = lane
            else:
                lane.setXLink(first_lane)

            # Connect cursor events
            lane.cursor_moved.connect(self._on_cursor_moved)

            self._lanes[trace.name] = lane
            self._lanes_layout.addWidget(lane)

        # Re-place cursors
        for cid, t_pos in self._cursors.items():
            if t_pos is not None:
                self._place_cursors(cid, t_pos)

    def _rebuild_overlay(self):
        pi = self._overlay_widget.getPlotItem()
        # Remove old curves
        for curve in self._overlay_curves.values():
            pi.removeItem(curve)
        self._overlay_curves.clear()

        visible = [t for t in self.traces if t.visible]
        for trace in visible:
            t_data, y_data = downsample_for_display(
                trace.time_axis, trace.processed_data)
            pen = pg.mkPen(color=trace.color, width=1.5)
            curve = pi.plot(t_data, y_data, pen=pen,
                            name=trace.label, antialias=False)
            curve.setDownsampling(auto=True, method="peak")
            curve.setClipToView(True)
            self._overlay_curves[trace.name] = curve

    def set_cursor(self, cursor_id: int, x_pos: Optional[float]):
        self._cursors[cursor_id] = x_pos
        if x_pos is not None:
            self._place_cursors(cursor_id, x_pos)
        self._emit_cursor_values()

    def _place_cursors(self, cursor_id: int, x_pos: float):
        color = self._cursor_colors[cursor_id]
        label = f"{'A' if cursor_id == 0 else 'B'}"
        if self._mode == "split":
            for lane in self._lanes.values():
                lane.add_cursor(cursor_id, x_pos, color, label)
        else:
            # Overlay mode cursor
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

    def _on_cursor_moved(self, x_pos: float, cursor_id: int):
        self._cursors[cursor_id] = x_pos
        # Sync all lanes
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
                    # overlay mode: interpolate directly
                    t = trace.time_axis
                    y = trace.processed_data
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

    def get_current_view_range(self) -> Tuple[float, float]:
        """Return current x-axis view range."""
        if self._lanes:
            first = next(iter(self._lanes.values()))
            vr = first.getPlotItem().viewRange()
            return vr[0][0], vr[0][1]
        if self._mode == "overlay":
            vr = self._overlay_widget.getPlotItem().viewRange()
            return vr[0][0], vr[0][1]
        return 0.0, 1.0

    def zoom_full(self):
        """Auto-range all axes."""
        if self._mode == "split":
            for lane in self._lanes.values():
                lane.getPlotItem().enableAutoRange()
        else:
            self._overlay_widget.getPlotItem().enableAutoRange()

    def zoom_x_range(self, x_start: float, x_end: float):
        """Set X range on all lanes."""
        if self._mode == "split":
            for lane in self._lanes.values():
                lane.getPlotItem().setXRange(x_start, x_end, padding=0)
        else:
            self._overlay_widget.getPlotItem().setXRange(
                x_start, x_end, padding=0)

    def pan_x(self, fraction: float):
        """Pan by a fraction of the current view width."""
        x0, x1 = self.get_current_view_range()
        dx = (x1 - x0) * fraction
        self.zoom_x_range(x0 + dx, x1 + dx)

    def zoom_in(self, factor: float = 0.5):
        x0, x1 = self.get_current_view_range()
        mid = (x0 + x1) / 2
        half = (x1 - x0) * factor / 2
        self.zoom_x_range(mid - half, mid + half)

    def zoom_out(self, factor: float = 2.0):
        self.zoom_in(factor)

    def take_screenshot(self, filepath: str, scale: int = 2):
        """Export plot as high-res PNG."""
        from PyQt6.QtGui import QPixmap, QPainter, QImage
        # Grab the widget
        pixmap = self.grab()
        if scale > 1:
            img = pixmap.toImage()
            img = img.scaled(img.width() * scale, img.height() * scale,
                             Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
            img.save(filepath)
        else:
            pixmap.save(filepath)
        return True
