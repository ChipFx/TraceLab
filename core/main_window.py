"""
core/main_window.py
Main application window.
"""

import os, sys, json, copy
import numpy as np
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QToolBar, QFileDialog, QMessageBox, QLabel, QPushButton,
    QCheckBox, QMenu, QInputDialog, QDialog
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QKeySequence, QIcon, QPixmap, QColor
from typing import List, Optional

from core.trace_model import TraceModel
from core.theme_manager import ThemeManager
from core.data_loader import load_csv
from core.import_dialog import ImportDialog
from core.scope_plot_widget import ScopePlotWidget, DEFAULT_LIMITS_CONFIG
from core.channel_panel import ChannelPanel
from core.cursor_panel import CursorPanel
from core.trigger_panel import TriggerPanel
from core.plugin_manager import PluginManager
from core.scope_status_bar import ScopeStatusBar
from core.draw_mode import (
    DEFAULT_DENSITY_PEN_MAPPING,
    DEFAULT_DRAW_MODE,
    DRAW_MODE_ADVANCED,
    DRAW_MODE_CLEAR,
    DRAW_MODE_FAST,
    DRAW_MODE_SIMPLE,
    DRAW_MODE_TOOLTIPS,
)
from core.periodicity import (
    estimate_period,
    ALL_TIERS as PERIODICITY_TIERS,
    TIER_DISABLED, TIER_ZERO_CROSS, TIER_STANDARD, TIER_PRECISE, TIER_EXTREME,
    TIER_LABELS as PERIODICITY_TIER_LABELS,
    TIER_TOOLTIPS as PERIODICITY_TIER_TOOLTIPS,
)
from core.retrigger import (
    MODE_OFF, MODE_PERSIST_FUTURE, MODE_PERSIST_PAST,
    MODE_AVERAGING, MODE_INTERPOLATION, PERSIST_MODES,
    PERSISTENCE_DEFAULTS, AVERAGING_DEFAULTS, INTERPOLATION_DEFAULTS,
    PersistenceLayer,
    apply_mode_with_triggers as retrigger_apply_with_triggers,
    find_all_triggers,
    find_all_triggers_with_times,
)

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "settings.json")


class _PeriodEstimateWorker(QThread):
    """Background thread for one period-estimation call.
    Emits result_ready(trace_name, T_seconds, confidence) when done."""
    result_ready = pyqtSignal(str, float, float)

    def __init__(self, trace_name: str, samples: np.ndarray,
                 dt: float, method: str, parent=None):
        super().__init__(parent)
        self._trace_name = trace_name
        self._samples = samples   # already a copy — safe to read from another thread
        self._dt = dt
        self._method = method

    def run(self):
        T, conf = estimate_period(self._samples, self._dt, self._method)
        self.result_ready.emit(self._trace_name, T, conf)


def _hex_perceived_luminance(hex_color: str) -> float:
    """Perceived luminance 0–255 for a #rrggbb color (ITU-R BT.601 coefficients)."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = h[0] * 2 + h[1] * 2 + h[2] * 2
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        return 0.0
    return 0.299 * r + 0.587 * g + 0.114 * b


# ── CSV export helpers ────────────────────────────────────────────────────────

def _meta_header_comments(traces) -> list:
    """
    Build TraceLab '#trace_meta=' comment lines for a set of exported traces.

    Everything is per-trace — different captures can have different sample rates,
    wall-clock anchors and channel metadata.

    Per-trace format (one line per trace that has any metadata):
      #trace_meta={"label","sps=10000","dt=0.0001","t0_wall_clock=...","unit=V",
                   "coupling=DC","impedance=1M","bwlimit=200M"}

    Rules:
      - Both sps= and dt= are written when set; neither is skipped because they
        are stored as separate floats and round-tripping through only one can
        accumulate error.
      - t0_wall_clock= is written per-trace; each capture has its own anchor.
      - unit=, coupling=, impedance=, bwlimit= written when non-empty.
      - gain= / offset= are intentionally omitted: the exported data is already
        processed_data (scaling already applied), so writing them would cause
        double-application on re-import.
      - The first token is the trace label (= column name in the CSV header),
        so the parser can match #trace_meta to the right column.
    """
    lines = []
    for trace in traces:
        attrs = []
        if trace.sample_rate and trace.sample_rate != 1.0:
            attrs.append(f"sps={trace.sample_rate:.10g}")
        if trace.dt and trace.dt != 1.0:
            attrs.append(f"dt={trace.dt:.10g}")
        if trace.t0_wall_clock:
            attrs.append(f"t0_wall_clock={trace.t0_wall_clock}")
        if trace.unit and trace.unit not in ("", "raw"):
            attrs.append(f"unit={trace.unit}")
        if trace.coupling:
            attrs.append(f"coupling={trace.coupling}")
        if trace.impedance:
            attrs.append(f"impedance={trace.impedance}")
        if trace.bwlimit:
            attrs.append(f"bwlimit={trace.bwlimit}")
        if attrs:
            quoted = ",".join(f'"{a}"' for a in attrs)
            lines.append(f'#trace_meta={{"{trace.label}",{quoted}}}')

    # Group membership — one #addgroup= line per unique group, listing all
    # member labels.  The first token in the braces is the group name;
    # subsequent tokens are column names (= trace labels in the exported CSV).
    groups: dict = {}
    for trace in traces:
        if trace.col_group:
            groups.setdefault(trace.col_group, []).append(trace.label)
    for group_name, members in groups.items():
        quoted = ",".join(f'"{m}"' for m in members)
        lines.append(f'#addgroup={{"{group_name}",{quoted}}}')

    return lines


def _build_flat_csv(traces, x0: float, x1: float, primary_only: bool = False):
    """
    Viewport-clipped flat CSV export (existing behaviour).
    When primary_only=True and a trace carries segments, only the primary
    segment's slice is exported (no segment headers written).
    """
    import numpy as np

    def _seg_slice(trace):
        """Return (time_array, data_array) for the relevant segment slice."""
        if primary_only and trace.segments:
            si = trace.primary_segment if trace.primary_segment is not None else 0
            si = max(0, min(si, len(trace.segments) - 1))
            s, e = trace.segments[si][0], trace.segments[si][1]
            return trace.time_axis[s:e], trace.processed_data[s:e]
        return trace.time_axis, trace.processed_data

    ref_ta, _ = _seg_slice(traces[0])
    mask  = (ref_ta >= x0) & (ref_ta <= x1)
    ref_t = ref_ta[mask]

    col_slices = [_seg_slice(t) for t in traces]

    # Per-trace valid time window: half-sample tolerance at each end so that
    # the exact first/last sample still matches despite floating-point jitter.
    trace_lo = []
    trace_hi = []
    for (ta, _), trace in zip(col_slices, traces):
        if len(ta):
            eps = trace.dt * 0.5 if trace.dt > 0 else 1e-12
            trace_lo.append(ta[0]  - eps)
            trace_hi.append(ta[-1] + eps)
        else:
            trace_lo.append(None)
            trace_hi.append(None)

    # Build data rows, recording the first and last row (1-based) that each
    # trace actually contributes a value — needed for #trace_data_range= headers.
    first_row = [None] * len(traces)
    last_row  = [None] * len(traces)
    data_lines = []

    for row_idx, t_val in enumerate(ref_t, start=1):
        row = [f"{t_val:.10g}"]
        for i, ((ta, ya), trace) in enumerate(zip(col_slices, traces)):
            lo, hi = trace_lo[i], trace_hi[i]
            if lo is None or t_val < lo or t_val > hi:
                row.append("")        # empty cell — outside this trace's time range
            else:
                if len(ta) < 2:
                    row.append(f"{ya[0]:.10g}" if len(ya) else "")
                else:
                    idx = int(round((t_val - ta[0]) / trace.dt)) if trace.dt > 0 else 0
                    idx = max(0, min(idx, len(ya) - 1))
                    row.append(f"{ya[idx]:.10g}")
                if first_row[i] is None:
                    first_row[i] = row_idx
                last_row[i] = row_idx
        data_lines.append(",".join(row))

    # Range headers for traces that don't cover the full exported window.
    # Omitted when the trace spans every row (no information gain).
    n_rows = len(ref_t)
    range_comments = []
    for i, trace in enumerate(traces):
        r0, r1 = first_row[i], last_row[i]
        if r0 is not None and r1 is not None and (r0 != 1 or r1 != n_rows):
            range_comments.append(
                f'#trace_data_range={{"{trace.label}",{r0},{r1}}}')

    lines = _meta_header_comments(traces)
    lines.extend(range_comments)
    lines.append("time," + ",".join(t.label for t in traces))
    lines.extend(data_lines)
    return lines


def _build_segmented_csv(traces):
    """
    Full-data segmented TraceLab native export.
    Segmented traces expand into .SEG0, .SEG1, … columns with segment
    headers; non-segmented traces export as a single column.
    All data is written without viewport clipping.
    """
    import numpy as np

    header_comments = []
    col_order  = ["Time"]
    col_arrays = {}

    # Reference time axis: first segment of the first segmented trace.
    # All LeCroy-style segments share an identical trigger-relative time axis,
    # so one copy is sufficient for the Time column.
    ref_time = None
    for trace in traces:
        if trace.segments is not None:
            s0, e0 = trace.segments[0][0], trace.segments[0][1]
            ref_time = trace.time_axis[s0:e0]
            break
    if ref_time is None:
        ref_time = traces[0].time_axis
    col_arrays["Time"] = ref_time
    n_rows = len(ref_time)

    for trace in traces:
        if trace.segments is not None:
            seg_col_names = [f"{trace.label}.SEG{i}"
                             for i in range(len(trace.segments))]

            # #segments= header (named-column form)
            header_comments.append(
                '#segments=("{}",{})'.format(
                    trace.label,
                    ",".join(f'"{n}"' for n in seg_col_names)))

            # #segment_meta= and per-segment column data
            for i, (s, e, t0_abs, t0_rel) in enumerate(trace.segments):
                seg_data = trace.processed_data[s:e]
                header_comments.append(
                    f'#segment_meta={{"{trace.label}",{i},1,'
                    f'{len(seg_data)},{t0_abs:.6f},{t0_rel:.10g}}}')
                col_order.append(seg_col_names[i])
                col_arrays[seg_col_names[i]] = seg_data

            # #trace_settings= header  (positional: name, primary_segment, viewmode)
            ps  = trace.primary_segment
            vm  = trace.non_primary_viewmode or ""
            header_comments.append(
                f'#trace_settings={{"{trace.label}",'
                f'{"null" if ps is None else ps},'
                f'"{vm}"}}')
        else:
            col_order.append(trace.label)
            col_arrays[trace.label] = trace.processed_data

    lines = _meta_header_comments(traces) + header_comments
    lines.append(",".join(col_order))
    for row_idx in range(n_rows):
        row = [f"{ref_time[row_idx]:.10g}"]
        for col_name in col_order[1:]:
            arr = col_arrays.get(col_name)
            if arr is not None and row_idx < len(arr):
                row.append(f"{arr[row_idx]:.10g}")
            else:
                row.append("")   # empty cell for shorter segments
        lines.append(",".join(row))
    return lines


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ChipFX TraceLab")
        self.resize(1400, 800)

        self.theme = ThemeManager()
        self._traces: List[TraceModel] = []
        self._plugins = PluginManager()
        self._plugins.discover()
        self._settings: dict = self._load_settings_dict()

        self._build_ui()
        self._build_menus()
        self._build_toolbar()
        self._build_statusbar()
        self._update_plugin_menu()
        self._restore_geometry()

    # ── Settings helpers ───────────────────────────────────────────────

    def _load_settings_dict(self) -> dict:
        if os.path.exists(SETTINGS_PATH):
            try:
                with open(SETTINGS_PATH) as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_settings(self):
        s = self._settings.copy()
        s["theme"] = self.theme.theme_name
        s["display_mode"] = self._display_mode
        s["geometry"] = self.saveGeometry().toHex().data().decode()
        s["y_lock_auto"] = self._plot.y_lock_auto
        s["interp_mode"] = self._interp_mode
        s["viewport_min_pts"] = self._viewport_min_pts
        s["draw_mode"] = self._draw_mode
        s["density_pen_mapping"] = dict(self._density_pen_mapping)
        s["import_replace"]         = self._import_replace
        s["import_reset_view"]      = self._import_reset_view
        s["import_reset_retrigger"] = self._import_reset_retrigger
        s["fft_min_freq"] = self._fft_min_freq
        s["retrigger_mode"] = self._retrigger_mode
        s["persistence"] = dict(self._persist_settings)
        s["averaging"] = dict(self._averaging_settings)
        s["interpolation"] = dict(self._interpolation_settings)
        s["original_dimmed_opacity"] = self._original_dimmed_opacity
        s["dashed_line_config"] = dict(self._dashed_line_config)
        s["auto_retrigger"] = self._trigger_panel.chk_auto_retrigger.isChecked()
        s["periodicity_estimation_method"] = self._periodicity_method
        s["periodicity_estimation_timeout"] = self._settings.get(
            "periodicity_estimation_timeout", 5)
        s["viewport_limits_mode"]    = self._limits_config.get("mode", "window")
        s["viewport_scale_min_px"]   = self._limits_config.get("scale_min_px", 2)
        s["viewport_scale_max_px"]   = self._limits_config.get("scale_max_px", 12)
        s["viewport_preset_min"]     = self._limits_config.get("preset_min", 2048)
        s["viewport_preset_max"]     = self._limits_config.get("preset_max", 50_000)
        s["retrigger_extrap_mode"] = self._retrigger_extrap_mode
        s["advanced_ui"] = dict(self._adv_ui)
        s["smart_scale"] = dict(self._smart_scale)
        s["process_segments"] = self._process_segments
        s["scroll_primaries"] = self._scroll_primaries
        s["lane_label_size"] = self._lane_label_size
        s["show_lane_labels"] = self._show_lane_labels
        s["allow_theme_force_labels"] = self._allow_theme_force_labels
        s["lane_label_spacing"] = self._lane_label_spacing
        try:
            with open(SETTINGS_PATH, "w") as f:
                json.dump(s, f, indent=2)
        except Exception:
            pass

    def _restore_geometry(self):
        geo = self._settings.get("geometry")
        if geo:
            try:
                self.restoreGeometry(bytes.fromhex(geo))
            except Exception:
                pass
        # Apply saved theme
        if "theme" in self._settings:
            self._set_theme(self._settings["theme"], save=False)
        # Apply saved font scale
        if "font_scale" in self._settings:
            self._apply_font_scale(self._settings["font_scale"])
        # Restore last view mode
        self._set_display_mode(self._display_mode, save=False)
        # Set branding on status bar
        self._scope_status.set_branding(self._get_branding_path())
        # Window icon — render a square version of the branding SVG
        self._set_window_icon()

    # ── UI Construction ────────────────────────────────────────────────

    def _build_ui(self):
        self._display_mode: str = self._settings.get("display_mode", "split")
        self._import_replace          = self._settings.get("import_replace", True)
        self._import_reset_view       = self._settings.get("import_reset_view", True)
        self._import_reset_retrigger  = self._settings.get("import_reset_retrigger", True)
        self._rejection_enabled       = self._settings.get("rejection_enabled", False)
        self._rejection_max_lines     = self._settings.get("rejection_max_lines", 10)
        self._export_segments_mode    = self._settings.get("export_segments_mode", "all")
        self._segments_dim_opacity    = self._settings.get("segments_dim_opacity", 30)
        self._segments_dash_size      = self._settings.get("segments_dash_size", 6)
        self._segments_gap_size       = self._settings.get("segments_gap_size", 6)
        self._y_lock_auto = self._settings.get("y_lock_auto", True)
        self._fft_min_freq = self._settings.get("fft_min_freq", 1.0)
        self._viewport_min_pts = self._settings.get("viewport_min_pts", 1024)
        self._draw_mode = self._settings.get("draw_mode", DEFAULT_DRAW_MODE)
        self._density_pen_mapping = dict(
            self._settings.get("density_pen_mapping",
                               DEFAULT_DENSITY_PEN_MAPPING))
        self._last_trigger_info = ""

        # ── Retrigger / persistence state ─────────────────────────────────────
        self._retrigger_mode: str = self._settings.get(
            "retrigger_mode", MODE_OFF)
        self._persist_settings: dict = dict(
            {**PERSISTENCE_DEFAULTS,
             **self._settings.get("persistence", {})})
        self._averaging_settings: dict = dict(
            {**AVERAGING_DEFAULTS,
             **self._settings.get("averaging", {})})
        self._interpolation_settings: dict = dict(
            {**INTERPOLATION_DEFAULTS,
             **self._settings.get("interpolation", {})})
        self._original_dimmed_opacity: float = max(0.1, min(0.9, float(
            self._settings.get("original_dimmed_opacity", 0.5))))
        self._dashed_line_config: dict = {
            "dash": 8, "space": 4,
            **self._settings.get("dashed_line_config", {}),
        }
        self._last_trigger_t_pos: Optional[float] = None
        self._last_retrigger_results: dict = {}
        self._last_retrigger_span: float = 0.0   # tracks view_span at last calc
        # "warn" = show EXTRAP badge; "clip" = mask curve to signal bounds
        self._retrigger_extrap_mode: str = self._settings.get(
            "retrigger_extrap_mode", "warn")

        # Epoch anchor for averaging / interpolation modes.
        # Set ONCE when the user places a trigger, then kept fixed across zoom.
        # Resets to None when trigger parameters (channel/level/edge) change.
        self._epoch_anchor_idx:   Optional[int] = None   # integer sample index
        self._epoch_anchor_ch:    str   = ""
        self._epoch_anchor_level: float = 0.0
        self._epoch_anchor_edge:  int   = -1

        # ── Advanced UI settings ──────────────────────────────────────────────
        _adv_defaults = {
            "statusbar_scroll":      True,
            "scroll_zoom_enabled":   True,
            "scroll_list_enabled":   True,
            "scroll_modifier_keys":  ["ctrl", "alt", "shift"],
            "scroll_default":        "zoom",
            "arrow_mode":            "pan_time",
            "split_min_lane_height": 80,
            "div_halves_px":         15,
            "div_fifths_px":         30,
            "div_tenths_px":         60,
            "div_subdiv_label":      False,
        }
        self._adv_ui: dict = {**_adv_defaults,
                              **self._settings.get("advanced_ui", {})}

        # ── Smart time-axis scale settings ────────────────────────────────────
        _smart_defaults = {
            "enabled":     False,
            "max_seconds": 300,
            "max_minutes": 120,
            "max_hours":   24,
        }
        self._smart_scale: dict = {**_smart_defaults,
                                   **self._settings.get("smart_scale", {})}

        # ── Segment view rendering ────────────────────────────────────────────
        self._process_segments: bool = bool(
            self._settings.get("process_segments", True))
        self._scroll_primaries: bool = bool(
            self._settings.get("scroll_primaries", True))

        # ── Periodicity estimation ────────────────────────────────────────────
        # Migrate any old method names that may live in settings.json
        _tier_migrate = {
            "none": TIER_DISABLED, "fast": TIER_ZERO_CROSS,
            "zero_crossing": TIER_ZERO_CROSS, "standard": TIER_STANDARD,
            "precise": TIER_PRECISE, "extreme": TIER_EXTREME,
        }
        _raw = self._settings.get("periodicity_estimation_method", TIER_STANDARD)
        self._periodicity_method: str = _tier_migrate.get(_raw, TIER_STANDARD)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)

        self._channel_panel = ChannelPanel()
        self._channel_panel.visibility_changed.connect(self._on_trace_visibility)
        self._channel_panel.color_changed.connect(self._on_trace_color)
        self._channel_panel.trace_removed.connect(self._remove_trace)
        self._channel_panel.order_changed.connect(self._on_channel_order_changed)
        self._channel_panel.interp_changed.connect(self._on_channel_interp_changed)
        self._channel_panel.reset_color_requested.connect(self._on_reset_trace_color)
        self._channel_panel.trace_renamed.connect(self._on_trace_renamed)
        self._channel_panel.segment_changed.connect(self._on_segment_changed)
        self._channel_panel.unit_changed.connect(self._on_unit_changed)
        self._channel_panel.set_scroll_primaries(self._scroll_primaries)
        self._splitter.addWidget(self._channel_panel)

        self._interp_mode = self._settings.get("interp_mode", "linear")
        self._lane_label_size: int = int(self._settings.get("lane_label_size", 8))
        self._show_lane_labels: bool = bool(self._settings.get("show_lane_labels", True))
        self._allow_theme_force_labels: bool = bool(
            self._settings.get("allow_theme_force_labels", False))
        self._lane_label_spacing: float = float(
            self._settings.get("lane_label_spacing", 0.3))
        # Viewport limits config — merged over defaults so unknown keys stay safe
        self._limits_config: dict = {
            **DEFAULT_LIMITS_CONFIG,
            **{k: v for k, v in {
                "mode":         self._settings.get("viewport_limits_mode",
                                                   DEFAULT_LIMITS_CONFIG["mode"]),
                "scale_min_px": int(self._settings.get("viewport_scale_min_px",
                                                        DEFAULT_LIMITS_CONFIG["scale_min_px"])),
                "scale_max_px": int(self._settings.get("viewport_scale_max_px",
                                                        DEFAULT_LIMITS_CONFIG["scale_max_px"])),
                "preset_min":   int(self._settings.get("viewport_preset_min",
                                                        DEFAULT_LIMITS_CONFIG["preset_min"])),
                "preset_max":   int(self._settings.get("viewport_preset_max",
                                                        DEFAULT_LIMITS_CONFIG["preset_max"])),
            }.items()},
        }
        self._plot = ScopePlotWidget(
            self.theme, self._y_lock_auto,
            self._interp_mode, self._viewport_min_pts,
            self._draw_mode, self._density_pen_mapping,
            lane_label_size=self._lane_label_size,
            show_lane_labels=self._show_lane_labels,
            allow_theme_force_labels=self._allow_theme_force_labels,
            lane_label_spacing=self._lane_label_spacing,
            limits_config=self._limits_config)
        self._plot.cursor_values_changed.connect(self._on_cursor_values)
        self._plot.sinc_active_changed.connect(self._on_sinc_active_changed)
        self._plot.view_changed.connect(self._refresh_status_bar)
        self._plot.view_changed.connect(self._on_view_changed_retrigger)

        # 0-ms single-shot timer coalesces rapid back-to-back _refresh_status_bar
        # calls (e.g. hiding all 32 channels) into one actual redraw.
        self._status_bar_refresh_timer = QTimer()
        self._status_bar_refresh_timer.setSingleShot(True)
        self._status_bar_refresh_timer.setInterval(0)
        self._status_bar_refresh_timer.timeout.connect(self._do_refresh_status_bar)

        # Wrap plot + status bar in a vertical container
        plot_container = QWidget()
        pcl = QVBoxLayout(plot_container)
        pcl.setContentsMargins(0, 0, 0, 0)
        pcl.setSpacing(0)

        # Move range bar OUT of scope_plot_widget into here, so the
        # status bar sits between plot and range bar (like a real scope).
        # The plot widget's internal layout: scroll/overlay is first, range
        # bar last. We grab the plot (which includes range bar internally).
        pcl.addWidget(self._plot)

        self._scope_status = ScopeStatusBar(self.theme.statusbar_palette())
        self._scope_status.toggle_trace_interp.connect(
            self._on_status_bar_toggle_interp)
        # Insert status bar BEFORE the range bar — achieved by the fact
        # that _plot already has its range_bar at its bottom. We just add
        # the status bar after _plot in the outer container so visually:
        # [plot+range_bar] [status_bar] -- but range_bar is inside _plot.
        # Better: hide range_bar from _plot and add it here after status bar.
        self._plot._range_bar.setParent(None)  # detach from plot's layout
        pcl.addWidget(self._scope_status)
        pcl.addWidget(self._plot._range_bar)

        # Propagate advanced-UI settings to plot and status bar
        self._plot.set_scroll_settings(self._adv_ui)
        self._plot.set_min_lane_height(self._adv_ui.get("split_min_lane_height", 80))
        self._scope_status.set_statusbar_scroll_enabled(
            self._adv_ui.get("statusbar_scroll", True))
        self._plot.set_div_settings(self._adv_ui)
        self._plot.set_smart_scale(self._smart_scale)
        self._plot.set_process_segments(self._process_segments)
        self._plot.set_segment_dim_opacity(self._segments_dim_opacity)
        self._plot.set_segment_dash_pattern(
            self._segments_dash_size, self._segments_gap_size)

        self._plot_container = plot_container
        self._splitter.addWidget(plot_container)

        # Right panel: cursor readout + trigger, stacked vertically
        right_splitter = QSplitter(Qt.Orientation.Vertical)

        self._cursor_panel = CursorPanel()
        self._cursor_panel.place_cursor.connect(self._start_cursor_placement)
        self._cursor_panel.set_t0_at_a.connect(self._cursor_set_t0_at_a)
        self._cursor_panel.jump_to_t0.connect(self._jump_to_t0)
        self._cursor_panel.remove_cursors.connect(self._plot.clear_cursors)
        self._cursor_panel.remove_cursors.connect(self._cursor_panel.clear_readout)
        right_splitter.addWidget(self._cursor_panel)

        self._trigger_panel = TriggerPanel()
        self._trigger_panel.trigger_found.connect(self._on_trigger_found)
        self._trigger_panel.set_time_zero.connect(self._on_trigger_set_t0)
        self._trigger_panel.place_cursor.connect(self._plot.set_cursor)
        self._trigger_panel.retrigger_update_requested.connect(self._reapply_retrigger)
        self._trigger_panel.chk_auto_retrigger.setChecked(
            self._settings.get("auto_retrigger", False))
        right_splitter.addWidget(self._trigger_panel)

        right_splitter.setStretchFactor(0, 2)
        right_splitter.setStretchFactor(1, 1)
        right_splitter.setSizes([420, 280])
        self._splitter.addWidget(right_splitter)

        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setStretchFactor(2, 0)
        self._splitter.setSizes([180, 820, 310])
        main_layout.addWidget(self._splitter)

        # Install app-level event filter so arrow keys work regardless of
        # which child widget (e.g. pyqtgraph ViewBox) currently holds focus.
        from PyQt6.QtWidgets import QApplication as _QApp
        _QApp.instance().installEventFilter(self)

    def _build_menus(self):
        mb = self.menuBar()

        # ── File ──────────────────────────────────────────────────────
        file_menu = mb.addMenu("File")
        a = file_menu.addAction("Open CSV...")
        a.setShortcut(QKeySequence("Ctrl+O"))
        a.triggered.connect(self._open_csv)

        file_menu.addSeparator()
        a = file_menu.addAction("Export Visible Data as CSV...")
        a.triggered.connect(self._export_csv)
        a = file_menu.addAction("Save Screenshot...")
        a.setShortcut(QKeySequence("Ctrl+P"))
        a.triggered.connect(self._save_screenshot)

        file_menu.addSeparator()
        a = file_menu.addAction("Clear All Traces")
        a.triggered.connect(self._clear_all)
        file_menu.addSeparator()
        a = file_menu.addAction("Quit")
        a.setShortcut(QKeySequence("Ctrl+Q"))
        a.triggered.connect(self.close)

        # ── View ──────────────────────────────────────────────────────
        view_menu = mb.addMenu("View")
        view_mode_group = QActionGroup(self)
        view_mode_group.setExclusive(True)
        self._act_view_split = view_menu.addAction("Split Lanes (LeCroy Style)")
        self._act_view_split.setCheckable(True)
        self._act_view_split.setChecked(self._display_mode == "split")
        self._act_view_split.triggered.connect(lambda: self._set_display_mode("split"))
        view_mode_group.addAction(self._act_view_split)
        self._act_view_overlay = view_menu.addAction("Overlay All Traces")
        self._act_view_overlay.setCheckable(True)
        self._act_view_overlay.setChecked(self._display_mode == "overlay")
        self._act_view_overlay.triggered.connect(lambda: self._set_display_mode("overlay"))
        view_mode_group.addAction(self._act_view_overlay)

        view_menu.addSeparator()
        a = view_menu.addAction("Zoom to Fit")
        a.setShortcut(QKeySequence("F"))
        a.triggered.connect(self._zoom_full_safe)
        a = view_menu.addAction("Fit Time (X only)")
        a.setShortcut(QKeySequence("T"))
        a.triggered.connect(self._plot.zoom_fit_x)
        a = view_menu.addAction("Fit Amplitude (Y only)")
        a.setShortcut(QKeySequence("A"))
        a.triggered.connect(self._plot.zoom_fit_y)

        view_menu.addSeparator()
        a = view_menu.addAction("Zoom In (X)")
        a.setShortcut(QKeySequence("+"))
        a.triggered.connect(lambda: self._plot.zoom_in(0.5))
        a = view_menu.addAction("Zoom Out (X)")
        a.setShortcut(QKeySequence("-"))
        a.triggered.connect(lambda: self._plot.zoom_out(2.0))

        view_menu.addSeparator()
        # Y-lock toggle (checkable)
        self._act_y_lock = view_menu.addAction("Lock Y to Auto-Scale")
        self._act_y_lock.setCheckable(True)
        self._act_y_lock.setChecked(self._y_lock_auto)
        self._act_y_lock.setShortcut(QKeySequence("L"))
        self._act_y_lock.triggered.connect(self._toggle_y_lock)

        view_menu.addSeparator()
        # Interpolation mode submenu
        interp_menu = view_menu.addMenu("Interpolation")
        self._act_interp_linear = interp_menu.addAction("Linear (default)")
        self._act_interp_linear.setCheckable(True)
        self._act_interp_sinc   = interp_menu.addAction("Sinc (sin(x)/x)")
        self._act_interp_sinc.setCheckable(True)
        self._act_interp_cubic  = interp_menu.addAction("Cubic Spline")
        self._act_interp_cubic.setCheckable(True)
        ag = QActionGroup(self)
        ag.setExclusive(True)
        ag.addAction(self._act_interp_linear)
        ag.addAction(self._act_interp_sinc)
        ag.addAction(self._act_interp_cubic)
        m = self._interp_mode
        (self._act_interp_sinc if m == "sinc"
         else self._act_interp_cubic if m == "cubic"
         else self._act_interp_linear).setChecked(True)
        self._act_interp_linear.triggered.connect(
            lambda: self._set_interp_mode("linear"))
        self._act_interp_sinc.triggered.connect(
            lambda: self._set_interp_mode("sinc"))
        self._act_interp_cubic.triggered.connect(
            lambda: self._set_interp_mode("cubic"))

        view_menu.addSeparator()
        draw_menu = view_menu.addMenu("Draw Mode")
        self._draw_mode_actions = {}
        draw_group = QActionGroup(self)
        draw_group.setExclusive(True)
        for mode in (
            DRAW_MODE_SIMPLE,
            DRAW_MODE_FAST,
            DRAW_MODE_CLEAR,
            DRAW_MODE_ADVANCED,
        ):
            act = draw_menu.addAction(mode)
            act.setCheckable(True)
            act.setToolTip(DRAW_MODE_TOOLTIPS[mode])
            act.setStatusTip(DRAW_MODE_TOOLTIPS[mode])
            act.setChecked(mode == self._draw_mode)
            act.triggered.connect(
                lambda checked, selected_mode=mode: self._set_draw_mode(selected_mode))
            draw_group.addAction(act)
            self._draw_mode_actions[mode] = act

        view_menu.addSeparator()
        # Dynamically populated from themes/ folder
        self._theme_submenu = view_menu.addMenu("Theme")
        self._rebuild_theme_menu()

        view_menu.addSeparator()
        seg_view_menu = view_menu.addMenu("Segments")
        seg_view_menu.setToolTipsVisible(True)
        self._act_process_segments = seg_view_menu.addAction("Process Segments")
        self._act_process_segments.setCheckable(True)
        self._act_process_segments.setChecked(self._process_segments)
        self._act_process_segments.setToolTip(
            "When enabled, only the primary segment is drawn as the main trace;\n"
            "other segments are shown according to their non-primary view mode.")
        self._act_process_segments.toggled.connect(self._toggle_process_segments)
        self._act_scroll_primaries = seg_view_menu.addAction("Scroll Primaries")
        self._act_scroll_primaries.setCheckable(True)
        self._act_scroll_primaries.setChecked(self._scroll_primaries)
        self._act_scroll_primaries.setToolTip(
            "When enabled, hovering the mouse over a channel in the panel\n"
            "and scrolling the wheel steps through its primary segment.")
        self._act_scroll_primaries.toggled.connect(self._toggle_scroll_primaries)
        seg_view_menu.addSeparator()
        seg_view_menu.addAction("Dim Opacity…").triggered.connect(
            self._dlg_segments_dim_opacity)
        seg_dash_menu = seg_view_menu.addMenu("Dash Settings")
        seg_dash_menu.addAction("Dash Size…").triggered.connect(
            self._dlg_segments_dash_size)
        seg_dash_menu.addAction("Gap Size…").triggered.connect(
            self._dlg_segments_gap_size)

        view_menu.addSeparator()
        time_scale_menu = view_menu.addMenu("Time Scale")
        self._act_smart_scale = time_scale_menu.addAction("Smart Scale  (MM:SS / HH:MM:SS…)")
        self._act_smart_scale.setCheckable(True)
        self._act_smart_scale.setChecked(self._smart_scale.get("enabled", False))
        self._act_smart_scale.setToolTip(
            "When enabled, long time axes display as MM:SS, HH:MM:SS, or DD:HH:MM:SS\n"
            "instead of kilo-seconds.")
        self._act_smart_scale.toggled.connect(self._toggle_smart_scale)
        time_scale_menu.addSeparator()
        time_scale_menu.addAction("Settings…").triggered.connect(
            self._dlg_smart_scale_settings)

        # ── Analysis ──────────────────────────────────────────────────
        analysis_menu = mb.addMenu("Analysis")
        a = analysis_menu.addAction("FFT...")
        a.setShortcut(QKeySequence("Ctrl+F"))
        a.triggered.connect(self._show_fft)
        a = analysis_menu.addAction("Filter...")
        a.triggered.connect(self._show_filter)
        a = analysis_menu.addAction("Clear All Filters")
        a.triggered.connect(self._clear_all_filters)
        analysis_menu.addSeparator()
        interp_trace_menu = analysis_menu.addMenu("Interpolation per Channel")
        self._per_trace_interp_menu = interp_trace_menu
        # Populated dynamically in _update_per_trace_interp_menu()

        analysis_menu.addSeparator()
        a = analysis_menu.addAction("Add Label at Cursor A...")
        a.triggered.connect(self._add_label_at_cursor)
        a = analysis_menu.addAction("Clear All Labels")
        a.triggered.connect(self._clear_all_labels)

        # ── Acquire ───────────────────────────────────────────────────
        acquire_menu = mb.addMenu("Acquire")

        persist_group = QActionGroup(self)
        persist_group.setExclusive(True)

        self._act_persist_off = acquire_menu.addAction("Off")
        self._act_persist_off.setCheckable(True)
        self._act_persist_off.setChecked(self._retrigger_mode == MODE_OFF)
        persist_group.addAction(self._act_persist_off)
        self._act_persist_off.triggered.connect(
            lambda: self._set_retrigger_mode(MODE_OFF))

        acquire_menu.addSeparator()

        self._act_persist_past = acquire_menu.addAction("Persistence (Normal)")
        self._act_persist_past.setCheckable(True)
        self._act_persist_past.setChecked(
            self._retrigger_mode == MODE_PERSIST_PAST)
        self._act_persist_past.setToolTip(
            "Last trigger shown as hard line; earlier triggers fade into "
            "history below it.  Classic oscilloscope persistence.")
        persist_group.addAction(self._act_persist_past)
        self._act_persist_past.triggered.connect(
            lambda: self._set_retrigger_mode(MODE_PERSIST_PAST))

        self._act_persist_future = acquire_menu.addAction("Persistence (Future)")
        self._act_persist_future.setCheckable(True)
        self._act_persist_future.setChecked(
            self._retrigger_mode == MODE_PERSIST_FUTURE)
        self._act_persist_future.setToolTip(
            "First trigger shown as hard line; later triggers fade into "
            "the future below it.")
        persist_group.addAction(self._act_persist_future)
        self._act_persist_future.triggered.connect(
            lambda: self._set_retrigger_mode(MODE_PERSIST_FUTURE))

        self._act_rt_averaging = acquire_menu.addAction("Averaging")
        self._act_rt_averaging.setCheckable(True)
        self._act_rt_averaging.setChecked(self._retrigger_mode == MODE_AVERAGING)
        self._act_rt_averaging.setToolTip(
            "Average multiple trigger-aligned segments to reduce noise.")
        self._act_rt_averaging.triggered.connect(
            self._toggle_retrigger_averaging)

        self._act_rt_interp = acquire_menu.addAction("Interpolate")
        self._act_rt_interp.setCheckable(True)
        self._act_rt_interp.setChecked(
            self._retrigger_mode == MODE_INTERPOLATION)
        self._act_rt_interp.setToolTip(
            "Interleave multiple trigger-aligned segments to increase "
            "effective sample resolution.")
        self._act_rt_interp.triggered.connect(
            self._toggle_retrigger_interpolation)

        acquire_menu.addSeparator()

        pm = acquire_menu.addMenu("Persistence Settings")
        pm.addAction("Count…").triggered.connect(self._dlg_persist_count)
        pm.addAction("Selection…").triggered.connect(self._dlg_persist_selection)
        pm.addAction("Emphasis…").triggered.connect(self._dlg_persist_emphasis)
        pm.addAction("Opacity Decay…").triggered.connect(
            self._dlg_persist_opacity)
        pm.addAction("Width Growth…").triggered.connect(self._dlg_persist_width)
        pm.addSeparator()
        pm.addAction("Restore Defaults").triggered.connect(
            self._reset_persist_defaults)

        am = acquire_menu.addMenu("Averaging Settings")
        am.addAction("Count…").triggered.connect(self._dlg_avg_count)
        am.addSeparator()
        avg_orig = am.addMenu("Original Data")
        self._avg_orig_actions = self._build_original_display_menu(
            avg_orig, self._averaging_settings,
            lambda: self._reapply_retrigger())
        am.addSeparator()
        am.addAction("Restore Defaults").triggered.connect(
            self._reset_avg_defaults)

        im = acquire_menu.addMenu("Interpolation Settings")
        im.addAction("Count…").triggered.connect(self._dlg_interp_count)
        im.addSeparator()
        interp_orig = im.addMenu("Original Data")
        self._interp_orig_actions = self._build_original_display_menu(
            interp_orig, self._interpolation_settings,
            lambda: self._reapply_retrigger())
        im.addSeparator()
        im.addAction("Restore Defaults").triggered.connect(
            self._reset_interp_defaults)

        acquire_menu.addSeparator()

        em = acquire_menu.addMenu("Extrapolation Behaviour")
        em.setToolTipsVisible(True)
        extrap_group = QActionGroup(self)
        extrap_group.setExclusive(True)
        self._extrap_warn_act = em.addAction("Warn (show EXTRAP badge)")
        self._extrap_warn_act.setCheckable(True)
        self._extrap_warn_act.setChecked(self._retrigger_extrap_mode == "warn")
        self._extrap_warn_act.setToolTip(
            "Show the full averaged/interpolated curve even when it extends\n"
            "beyond the original capture window.\n"
            "An EXTRAP badge appears on the channel status block.")
        self._extrap_warn_act.triggered.connect(
            lambda: self._set_extrap_mode("warn"))
        extrap_group.addAction(self._extrap_warn_act)
        self._extrap_clip_act = em.addAction("Clip to capture window")
        self._extrap_clip_act.setCheckable(True)
        self._extrap_clip_act.setChecked(self._retrigger_extrap_mode == "clip")
        self._extrap_clip_act.setToolTip(
            "Mask the averaged/interpolated curve so it never extends\n"
            "beyond the original capture's time bounds.\n"
            "No EXTRAP badge is shown; the curve may appear shorter at\n"
            "zoom levels where the trigger is near the edge of the data.")
        self._extrap_clip_act.triggered.connect(
            lambda: self._set_extrap_mode("clip"))
        extrap_group.addAction(self._extrap_clip_act)

        # ── Settings ──────────────────────────────────────────────────
        settings_menu = mb.addMenu("Settings")
        settings_menu.addAction("Font Scale...").triggered.connect(
            self._show_font_scale_dialog)
        settings_menu.addAction("Decimal Separator...").triggered.connect(
            self._show_decimal_sep_dialog)
        settings_menu.addSeparator()
        settings_menu.addAction("Edit Current Theme...").triggered.connect(
            self._open_theme_editor)
        settings_menu.addAction("Reload Themes from Disk").triggered.connect(
            self._reload_themes)
        settings_menu.addSeparator()
        self._act_remember_folder = settings_menu.addAction("Remember Last Folder")
        self._act_remember_folder.setCheckable(True)
        self._act_remember_folder.setChecked(
            self._settings.get("remember_folder", True))
        self._act_remember_folder.setToolTip(
            "When enabled, open/save dialogs start in the last used folder.")
        self._act_remember_folder.triggered.connect(self._toggle_remember_folder)

        settings_menu.addSeparator()
        settings_menu.addAction("Dimmed Opacity…").triggered.connect(
            self._dlg_dimmed_opacity)
        settings_menu.addAction("Dashed Line Config…").triggered.connect(
            self._dlg_dashed_line_config)

        settings_menu.addSeparator()
        per_menu = settings_menu.addMenu("Periodicity Estimation")
        per_menu.setToolTipsVisible(True)
        per_group = QActionGroup(self)
        per_group.setExclusive(True)
        self._periodicity_method_actions: dict = {}
        for tier in PERIODICITY_TIERS:
            act = per_menu.addAction(PERIODICITY_TIER_LABELS[tier])
            act.setCheckable(True)
            act.setChecked(tier == self._periodicity_method)
            act.setToolTip(PERIODICITY_TIER_TOOLTIPS[tier])
            act.triggered.connect(
                lambda *_, t=tier: self._set_periodicity_method(t))
            per_group.addAction(act)
            self._periodicity_method_actions[tier] = act
        per_menu.addSeparator()
        adv_per_menu = per_menu.addMenu("Advanced Settings")
        adv_per_menu.addAction("Set Time Out…").triggered.connect(
            self._dlg_period_timeout)

        settings_menu.addSeparator()
        vp_menu = settings_menu.addMenu("Viewport Limits")
        vp_menu.setToolTipsVisible(True)
        vp_group = QActionGroup(self)
        vp_group.setExclusive(True)
        self._act_vp_window = vp_menu.addAction("Use window size")
        self._act_vp_window.setCheckable(True)
        self._act_vp_window.setChecked(self._limits_config.get("mode") == "window")
        self._act_vp_window.setToolTip(
            "Scale the display point budget with the plot pixel width.\n"
            "Wider windows get more points; narrow windows are automatically lighter.")
        self._act_vp_preset = vp_menu.addAction("Use preset limits")
        self._act_vp_preset.setCheckable(True)
        self._act_vp_preset.setChecked(self._limits_config.get("mode") == "preset")
        self._act_vp_preset.setToolTip(
            "Use a fixed min/max point budget regardless of window size.\n"
            "Reproduces the classic fixed-limit behaviour.")
        vp_group.addAction(self._act_vp_window)
        vp_group.addAction(self._act_vp_preset)
        self._act_vp_window.triggered.connect(
            lambda: self._set_viewport_limits_mode("window"))
        self._act_vp_preset.triggered.connect(
            lambda: self._set_viewport_limits_mode("preset"))
        vp_menu.addSeparator()
        vp_menu.addAction("Scale min points/px…").triggered.connect(
            lambda: self._dlg_vp_int("scale_min_px", "Scale min points/px",
                                     "Minimum rendered points per display pixel\n"
                                     "(window-size mode):", 1, 200))
        vp_menu.addAction("Scale max points/px…").triggered.connect(
            lambda: self._dlg_vp_int("scale_max_px", "Scale max points/px",
                                     "Maximum rendered points per display pixel\n"
                                     "(window-size mode):", 2, 200))
        vp_menu.addSeparator()
        vp_menu.addAction("Preset min points…").triggered.connect(
            lambda: self._dlg_vp_int("preset_min", "Preset min points",
                                     "Absolute floor — never downsample below this\n"
                                     "many points (both modes):", 64, 10_000_000))
        vp_menu.addAction("Preset max points…").triggered.connect(
            lambda: self._dlg_vp_int("preset_max", "Preset max points",
                                     "Fixed point ceiling\n"
                                     "(preset mode):", 64, 10_000_000))

        settings_menu.addSeparator()
        lane_lbl_menu = settings_menu.addMenu("Lane Labels")
        lane_lbl_menu.setToolTipsVisible(True)
        lane_lbl_menu.addAction("Label Size…").triggered.connect(
            self._dlg_lane_label_size)
        lane_lbl_menu.addAction("Label Spacing…").triggered.connect(
            self._dlg_lane_label_spacing)
        self._act_show_lane_labels = lane_lbl_menu.addAction("Show Names")
        self._act_show_lane_labels.setCheckable(True)
        self._act_show_lane_labels.setChecked(self._show_lane_labels)
        self._act_show_lane_labels.setToolTip(
            "Show/hide the floating channel name in each split-lane panel.")
        self._act_show_lane_labels.triggered.connect(self._toggle_show_lane_labels)
        self._act_allow_force_labels = lane_lbl_menu.addAction("Allow Theme Override")
        self._act_allow_force_labels.setCheckable(True)
        self._act_allow_force_labels.setChecked(self._allow_theme_force_labels)
        self._act_allow_force_labels.setToolTip(
            "When enabled, a theme that has force_labels=true will always show\n"
            "lane labels regardless of the Show Names toggle.\n"
            "Useful for monochromatic themes where colour alone is ambiguous.")
        self._act_allow_force_labels.triggered.connect(
            self._toggle_allow_theme_force_labels)

        settings_menu.addSeparator()
        import_menu = settings_menu.addMenu("Import")
        import_menu.setToolTipsVisible(True)
        rejection_menu = import_menu.addMenu("Rejection")
        rejection_menu.setToolTipsVisible(True)
        rejection_menu.setToolTip(
            "Configure if and how many lines between header and data\n"
            "get rejected if they are malformed before an error occurs.")

        self._act_rejection_enabled = rejection_menu.addAction("Enabled")
        self._act_rejection_enabled.setCheckable(True)
        self._act_rejection_enabled.setChecked(self._rejection_enabled)
        self._act_rejection_enabled.setToolTip(
            "When enabled, lines between the column header and the first\n"
            "valid data row are silently skipped if they contain no\n"
            "recognisable numeric data (wrong column count, pure text, etc.).\n"
            "Useful for formats that embed extra comment or unit rows.")
        self._act_rejection_enabled.triggered.connect(
            self._toggle_rejection_enabled)

        rejection_menu.addAction("Max Lines…").triggered.connect(
            self._dlg_rejection_max_lines)

        settings_menu.addSeparator()
        export_menu = settings_menu.addMenu("Export")
        export_menu.setToolTipsVisible(True)
        seg_export_menu = export_menu.addMenu("Segments")
        seg_export_menu.setToolTipsVisible(True)
        seg_export_group = QActionGroup(self)
        seg_export_group.setExclusive(True)
        self._act_export_seg_all = seg_export_menu.addAction("Export All Always")
        self._act_export_seg_all.setCheckable(True)
        self._act_export_seg_all.setToolTip(
            "Export every segment of a segmented trace.\n"
            "Each segment becomes its own column (Trace.SEG0, Trace.SEG1, …).")
        self._act_export_seg_primary = seg_export_menu.addAction("Export Primary Only")
        self._act_export_seg_primary.setCheckable(True)
        self._act_export_seg_primary.setToolTip(
            "Export only the primary segment of each segmented trace.\n"
            "If no primary is set, segment 0 is used.\n"
            "The exported file is non-segmented (no #segments= headers).")
        seg_export_group.addAction(self._act_export_seg_all)
        seg_export_group.addAction(self._act_export_seg_primary)
        (self._act_export_seg_primary
         if self._export_segments_mode == "primary_only"
         else self._act_export_seg_all).setChecked(True)
        self._act_export_seg_all.triggered.connect(
            lambda: self._set_export_segments_mode("all"))
        self._act_export_seg_primary.triggered.connect(
            lambda: self._set_export_segments_mode("primary_only"))

        settings_menu.addSeparator()
        adv_ui_menu = settings_menu.addMenu("Advanced UI")
        adv_ui_menu.setToolTipsVisible(True)

        # ── Mouse → Scroll submenu ────────────────────────��────────────
        scroll_menu = adv_ui_menu.addMenu("Mouse → Scroll")
        scroll_menu.setToolTipsVisible(True)

        self._act_statusbar_scroll = scroll_menu.addAction("Enable Status Bar Scrolling")
        self._act_statusbar_scroll.setCheckable(True)
        self._act_statusbar_scroll.setChecked(self._adv_ui.get("statusbar_scroll", True))
        self._act_statusbar_scroll.setToolTip(
            "Mouse wheel scrolls the channel-block status bar horizontally.\n"
            "Wheel tilt (if present) snaps one block at a time.")
        self._act_statusbar_scroll.triggered.connect(self._toggle_statusbar_scroll)

        scroll_menu.addSeparator()

        self._act_scroll_zoom = scroll_menu.addAction("Zoom with Scroll Wheel")
        self._act_scroll_zoom.setCheckable(True)
        self._act_scroll_zoom.setChecked(self._adv_ui.get("scroll_zoom_enabled", True))
        self._act_scroll_zoom.setToolTip(
            "Mouse wheel zooms the trace view (pyqtgraph default).")
        self._act_scroll_zoom.triggered.connect(self._toggle_scroll_zoom)

        self._act_scroll_list = scroll_menu.addAction("Scroll Trace List with Scroll Wheel")
        self._act_scroll_list.setCheckable(True)
        self._act_scroll_list.setChecked(self._adv_ui.get("scroll_list_enabled", True))
        self._act_scroll_list.setToolTip(
            "Mouse wheel can scroll the split-view trace list vertically\n"
            "(controlled by modifier keys or default action).")
        self._act_scroll_list.triggered.connect(self._toggle_scroll_list)

        scroll_menu.addSeparator()
        scroll_default_menu = scroll_menu.addMenu("Default Scroll Action")
        scroll_default_menu.setToolTipsVisible(True)
        _sdg = QActionGroup(self)
        _sdg.setExclusive(True)
        self._act_scroll_default_zoom = scroll_default_menu.addAction("Zoom (no modifier)")
        self._act_scroll_default_zoom.setCheckable(True)
        self._act_scroll_default_zoom.setChecked(
            self._adv_ui.get("scroll_default", "zoom") == "zoom")
        self._act_scroll_default_zoom.setToolTip(
            "Without a modifier key → zoom; with modifier → scroll list.")
        _sdg.addAction(self._act_scroll_default_zoom)
        self._act_scroll_default_list = scroll_default_menu.addAction("Scroll List (no modifier)")
        self._act_scroll_default_list.setCheckable(True)
        self._act_scroll_default_list.setChecked(
            self._adv_ui.get("scroll_default", "zoom") == "scroll_list")
        self._act_scroll_default_list.setToolTip(
            "Without a modifier key → scroll list; with modifier → zoom.")
        _sdg.addAction(self._act_scroll_default_list)
        self._act_scroll_default_zoom.triggered.connect(
            lambda: self._set_scroll_default("zoom"))
        self._act_scroll_default_list.triggered.connect(
            lambda: self._set_scroll_default("scroll_list"))

        scroll_menu.addSeparator()
        scroll_menu.addAction("Set Modifier Keys…").triggered.connect(
            self._dlg_scroll_modifier_keys)

        # ── Keyboard → Arrows submenu ──────────────────────────────────
        arrows_menu = adv_ui_menu.addMenu("Keyboard → Arrows")
        arrows_menu.setToolTipsVisible(True)
        _akg = QActionGroup(self)
        _akg.setExclusive(True)
        arrow_mode = self._adv_ui.get("arrow_mode", "pan_time")
        self._act_arrow_off = arrows_menu.addAction("Off / do not use")
        self._act_arrow_off.setCheckable(True)
        self._act_arrow_off.setChecked(arrow_mode == "off")
        self._act_arrow_off.setToolTip("Arrow keys have no effect on the plot.")
        _akg.addAction(self._act_arrow_off)
        self._act_arrow_pan = arrows_menu.addAction("Pan time axis (Left / Right)")
        self._act_arrow_pan.setCheckable(True)
        self._act_arrow_pan.setChecked(arrow_mode == "pan_time")
        self._act_arrow_pan.setToolTip(
            "Left/Right arrows pan the time axis by 10% of the visible span.\n"
            "Up/Down arrows scroll the split-view trace list.")
        _akg.addAction(self._act_arrow_pan)
        self._act_arrow_statusbar = arrows_menu.addAction("Pan status bar by 1 block")
        self._act_arrow_statusbar.setCheckable(True)
        self._act_arrow_statusbar.setChecked(arrow_mode == "scroll_statusbar")
        self._act_arrow_statusbar.setToolTip(
            "Left/Right arrows snap the status bar one channel block at a time.")
        _akg.addAction(self._act_arrow_statusbar)
        self._act_arrow_off.triggered.connect(
            lambda: self._set_arrow_mode("off"))
        self._act_arrow_pan.triggered.connect(
            lambda: self._set_arrow_mode("pan_time"))
        self._act_arrow_statusbar.triggered.connect(
            lambda: self._set_arrow_mode("scroll_statusbar"))

        # ── Split View submenu ─────────────────────────────────────────
        split_view_menu = adv_ui_menu.addMenu("Split View")
        split_view_menu.addAction("Minimum Trace Height…").triggered.connect(
            self._dlg_min_lane_height)

        # ── Div Settings ───────────────────────────────────────────────
        adv_ui_menu.addAction("Div Settings…").triggered.connect(
            self._dlg_div_settings)

        # ── Plugins ───────────────────────────────────────────────────────
        self._plugins_menu = mb.addMenu("Plugins")
        self._plugins_menu.addAction("Reload Plugins").triggered.connect(
            self._reload_plugins)
        self._plugins_menu.addAction("Open Plugins Folder").triggered.connect(
            self._open_plugins_dir)
        self._plugins_menu.addSeparator()
        self._plugin_actions_start = len(self._plugins_menu.actions())

        # ── Help ──────────────────────────────────────────────────────
        help_menu = mb.addMenu("Help")
        help_menu.addAction("About TraceLab").triggered.connect(self._show_about)

    def _build_toolbar(self):
        tb = self.addToolBar("Main")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)

        def add(label, fn, tip=""):
            a = QAction(label, self)
            a.triggered.connect(fn)
            if tip:
                a.setToolTip(tip)
            tb.addAction(a)
            return a

        add("Open", self._open_csv)
        add("Clear", self._clear_all, "Clear all traces")
        tb.addSeparator()
        add("Split", lambda: self._set_display_mode("split"))
        add("Overlay", lambda: self._set_display_mode("overlay"))
        tb.addSeparator()
        add("Cursor A", lambda: self._start_cursor_placement(0))
        add("Cursor B", lambda: self._start_cursor_placement(1))
        tb.addSeparator()
        add("Fit (F)", self._zoom_full_safe)
        add("Fit X (T)", self._plot.zoom_fit_x)
        add("Fit Y (A)", self._plot.zoom_fit_y)
        tb.addSeparator()

        # Y-lock checkbox in toolbar
        self._tb_y_lock = QCheckBox("Y Auto")
        self._tb_y_lock.setChecked(self._y_lock_auto)
        self._tb_y_lock.setToolTip(
            "Lock Y axis to auto-scale. Mouse wheel zooms X only.")
        self._tb_y_lock.toggled.connect(self._toggle_y_lock)
        tb.addWidget(self._tb_y_lock)
        tb.addSeparator()

        add("FFT", self._show_fft)
        add("Filter", self._show_filter)
        tb.addSeparator()
        add("Screenshot", self._save_screenshot)

    def _build_statusbar(self):
        self._status_lbl = QLabel("Ready  |  No data loaded")
        self._status_lbl.setStyleSheet("padding: 2px 8px;")
        self.statusBar().addWidget(self._status_lbl)
        self._cursor_status = QLabel("")
        self._cursor_status.setStyleSheet("padding: 2px 8px; color: #aaa;")
        self.statusBar().addPermanentWidget(self._cursor_status)

    # ── File Operations ────────────────────────────────────────────────

    def _get_open_dir(self) -> str:
        """Return the starting directory for open dialogs."""
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if self._settings.get("remember_folder", True):
            d = self._settings.get("last_open_folder", base)
            if os.path.isdir(d):
                return d
        return base

    def _get_save_dir(self, filename: str = "") -> str:
        """Return the starting directory (+ optional filename) for save dialogs."""
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if self._settings.get("remember_folder", True):
            d = self._settings.get("last_save_folder", base)
            if os.path.isdir(d):
                return os.path.join(d, filename) if filename else d
        return os.path.join(base, filename) if filename else base

    def _remember_open(self, path: str):
        if self._settings.get("remember_folder", True):
            self._settings["last_open_folder"] = os.path.dirname(os.path.abspath(path))

    def _remember_save(self, path: str):
        if self._settings.get("remember_folder", True):
            self._settings["last_save_folder"] = os.path.dirname(os.path.abspath(path))

    def _open_csv(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Data File", self._get_open_dir(),
            "CSV Files (*.csv *.tsv *.txt);;All Files (*)")
        if not path:
            return

        result = load_csv(path,
                          rejection_enabled=self._rejection_enabled,
                          rejection_max_lines=self._rejection_max_lines,
                          honor_skip_rows=self._settings.get(
                              "import_honor_skip_rows", True))
        if result.error:
            QMessageBox.critical(self, "Load Error", result.error)
            return

        self._remember_open(path)

        # Pass persistent settings to dialog
        dlg = ImportDialog(result, persistent_settings=self._settings,
                           parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        # Save checkbox preferences
        self._import_replace       = dlg.replace_existing
        self._import_reset_view    = dlg.reset_view
        self._settings["import_replace"]          = self._import_replace
        self._settings["import_reset_view"]       = self._import_reset_view
        self._settings["import_reset_retrigger"]  = dlg.reset_retrigger
        self._settings["import_remove_cursors"]   = dlg.remove_cursors
        self._settings["import_honor_skip_rows"]  = dlg.honor_skip_rows

        if dlg.reset_retrigger:
            self._set_retrigger_mode(MODE_OFF)

        if dlg.remove_cursors:
            self._plot.clear_cursors()
            self._cursor_panel.clear_readout()

        if dlg.replace_existing:
            self._clear_all(confirm=False)

        self._batch_import_traces(dlg.result_traces,
                                   replace_existing=dlg.replace_existing)

        if dlg.reset_view:
            meta = result.metadata
            if (meta.view_time_start is not None or
                    meta.view_sample_start is not None):
                QTimer.singleShot(80, lambda: self._apply_viewport_from_metadata(meta))
            else:
                QTimer.singleShot(50, self._zoom_full_safe)

        self._update_status()

    def _unique_trace_name(self, name: str) -> str:
        """Return name, or name_001 / name_002 … if name is already taken."""
        existing = {t.name for t in self._traces}
        if name not in existing:
            return name
        i = 1
        while True:
            candidate = f"{name}_{i:03d}"
            if candidate not in existing:
                return candidate
            i += 1

    def _batch_import_traces(self, traces: List[TraceModel],
                             replace_existing: bool = True) -> None:
        """Add/replace multiple traces in a single plot rebuild.
        Used by _open_csv so 8 traces cause one rebuild, not eight.
        When replace_existing=False, name collisions get a _001/_002 suffix
        instead of overwriting the existing trace."""
        for trace in traces:
            existing_names = [t.name for t in self._traces]
            if trace.name in existing_names:
                if replace_existing:
                    idx = existing_names.index(trace.name)
                    old_trace = self._traces[idx]
                    trace.color = old_trace.color
                    trace.theme_color_index = old_trace.theme_color_index
                    trace.use_theme_color = old_trace.use_theme_color
                    self._traces[idx] = trace
                    self._channel_panel.refresh_all()
                else:
                    # Keep both — rename the incoming trace to avoid collision
                    unique = self._unique_trace_name(trace.name)
                    trace.name  = unique
                    trace.label = unique
                    n = len(self._traces)
                    trace.reset_color_to_theme(n)
                    trace.sync_theme_color(self.theme.active_theme)
                    self._traces.append(trace)
                    self._channel_panel.add_trace(trace)
            else:
                n = len(self._traces)
                trace.reset_color_to_theme(n)
                trace.sync_theme_color(self.theme.active_theme)
                self._traces.append(trace)
                self._channel_panel.add_trace(trace)
        # Single plot rebuild for the whole batch
        self._plot.batch_add_traces(traces)
        self._refresh_trigger_channels()
        self._refresh_status_bar()
        # Start async period estimation for every trace (after UI is live)
        for trace in traces:
            self._estimate_period_async(trace)

    def _add_trace(self, trace: TraceModel):
        """Add or replace a single trace (used by plugins/retrigger; not import)."""
        # If a trace with this name already exists, replace it
        existing_names = [t.name for t in self._traces]
        if trace.name in existing_names:
            idx = existing_names.index(trace.name)
            old_trace = self._traces[idx]
            trace.color = old_trace.color
            trace.theme_color_index = old_trace.theme_color_index
            trace.use_theme_color = old_trace.use_theme_color
            self._traces[idx] = trace
            self._channel_panel.refresh_all()
            self._plot.add_trace(trace)  # add_trace handles overwrite
            self._estimate_period_async(trace)
            return

        # Assign color from active theme via ThemeManager
        n = len(self._traces)
        trace.reset_color_to_theme(n)
        trace.sync_theme_color(self.theme.active_theme)

        self._traces.append(trace)
        self._channel_panel.add_trace(trace)
        self._plot.add_trace(trace)
        self._estimate_period_async(trace)
        self._refresh_trigger_channels()
        self._refresh_status_bar()

    def _remove_trace(self, trace_name: str):
        self._traces = [t for t in self._traces if t.name != trace_name]
        self._channel_panel.remove_trace(trace_name)
        self._plot.remove_trace(trace_name)
        self._refresh_trigger_channels()
        self._update_status()

    def _clear_all(self, confirm: bool = True):
        if confirm and self._traces:
            r = QMessageBox.question(
                self, "Clear All",
                "Remove all loaded traces?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
        # Remove from panels
        for t in list(self._traces):
            self._channel_panel.remove_trace(t.name)
        self._traces.clear()
        self._plot.clear_all()          # also clears persist/retrigger state in plot
        self._last_retrigger_results.clear()
        self._last_trigger_t_pos  = None
        self._last_retrigger_span = 0.0
        self._refresh_trigger_channels()
        self._update_status()

    def _export_csv(self):
        if not self._traces:
            QMessageBox.information(self, "Export", "No data loaded.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Visible Data", self._get_save_dir(),
            "CSV Files (*.csv)")
        if not path:
            return
        self._remember_save(path)

        x0, x1 = self._plot.get_current_view_range()
        visible = [t for t in self._traces if t.visible]
        if not visible:
            return

        any_segmented = any(t.segments is not None for t in visible)
        primary_only  = (self._export_segments_mode == "primary_only")

        if any_segmented and not primary_only:
            lines = _build_segmented_csv(visible)
        else:
            lines = _build_flat_csv(visible, x0, x1, primary_only=primary_only)

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        self._status_lbl.setText(f"Exported: {os.path.basename(path)}")

    def _save_screenshot(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Screenshot",
            self._get_save_dir("tracelab_capture.png"),
            "PNG Images (*.png);;All Files (*)")
        if not path:
            return
        self._remember_save(path)
        self._grab_screenshot(path, scale=2)
        self._status_lbl.setText(f"Screenshot: {os.path.basename(path)}")

    def _grab_screenshot(self, filepath: str, scale: int = 2):
        """
        Grab plot area + scope status bar together (excludes range-input bar).
        Composites branding directly from the status bar logo — no separate
        corner overlay needed.
        """
        from PyQt6.QtGui import QPixmap, QImage
        from PyQt6.QtCore import Qt as _Qt

        # Grab the plot widget (scroll/overlay + range bar excluded since
        # range_bar was re-parented out of _plot)
        if self._plot._mode == "split":
            plot_px = self._plot._scroll.grab()
        else:
            plot_px = self._plot._overlay_widget.grab()

        # Grab the scope status bar
        status_px = self._scope_status.grab()

        # Stack vertically: plot on top, status bar on bottom
        pw, ph = plot_px.width(), plot_px.height()
        sw, sh = status_px.width(), status_px.height()
        total_w = max(pw, sw)
        total_h = ph + sh

        combined = QPixmap(total_w, total_h)
        combined.fill(_Qt.GlobalColor.black)
        from PyQt6.QtGui import QPainter
        p = QPainter(combined)
        p.drawPixmap(0, 0, plot_px)
        p.drawPixmap(0, ph, status_px)
        p.end()

        if scale > 1:
            img = combined.toImage()
            img = img.scaled(
                img.width() * scale, img.height() * scale,
                _Qt.AspectRatioMode.KeepAspectRatio,
                _Qt.TransformationMode.SmoothTransformation)
            img.save(filepath)
        else:
            combined.save(filepath)

    def _get_branding_path(self) -> str:
        """Return path to branding SVG if it exists, else empty string.

        Resolution order:
          1. branding_{theme display name}.svg   e.g. "branding_Phosphor Green.svg"
          2. branding_Dark.svg / branding_Light.svg  chosen by bg_plot luminance
          3. empty string  (caller falls back to text)
        """
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        assets = os.path.join(base, "assets")

        # 1. Exact match on the theme's display name
        p = os.path.join(assets, f"branding_{self.theme.display_name}.svg")
        if os.path.exists(p):
            return p

        # 2. Brightness-based generic fallback
        bg_hex = self.theme.pv("bg_plot") or self.theme.pv("bg", "#0d0d0d")
        fallback = ("branding_Light.svg"
                    if _hex_perceived_luminance(bg_hex) >= 128
                    else "branding_Dark.svg")
        p = os.path.join(assets, fallback)
        if os.path.exists(p):
            return p

        return ""

    def _set_window_icon(self):
        """Load icon.svg (icon.svg → branding_Dark.svg → nothing) and apply
        to both the window title bar and the OS taskbar / program bar.

        On Windows, also sets the AppUserModelID so the OS assigns our icon
        to the taskbar button instead of grouping it under the Python icon.
        """
        base   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        assets = os.path.join(base, "assets")

        # Resolution order: dedicated icon first, branding fallback second
        icon_path = None
        for name in ("icon.svg", "branding_Dark.svg"):
            candidate = os.path.join(assets, name)
            if os.path.exists(candidate):
                icon_path = candidate
                break
        if icon_path is None:
            return

        try:
            from PyQt6.QtSvg import QSvgRenderer
            from PyQt6.QtCore import QRectF
            from PyQt6.QtGui import QPainter
            from PyQt6.QtWidgets import QApplication
            renderer = QSvgRenderer(icon_path)
            if not renderer.isValid():
                return
            # Build a multi-resolution QIcon so it looks sharp at every DPI
            icon = QIcon()
            bg = QColor("#060610")
            for size in (16, 32, 48, 64, 128, 256):
                px = QPixmap(size, size)
                px.fill(bg)
                p = QPainter(px)
                renderer.render(p, QRectF(0, 0, size, size))
                p.end()
                icon.addPixmap(px)
            # Title-bar icon
            self.setWindowIcon(icon)
            # Taskbar / program-bar icon (all platforms)
            QApplication.instance().setWindowIcon(icon)
            # Windows: set AppUserModelID so the OS replaces the Python
            # interpreter icon with ours in the taskbar
            if sys.platform == "win32":
                try:
                    import ctypes
                    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                        "ChipFX.TraceLab.1")
                except Exception:
                    pass
        except Exception:
            pass

    # ── Display / Theme ────────────────────────────────────────────────

    def _set_display_mode(self, mode: str, save: bool = True):
        self._display_mode = mode
        self._plot.set_mode(mode)
        # Sync menu checkmarks
        if hasattr(self, '_act_view_split'):
            self._act_view_split.setChecked(mode == "split")
            self._act_view_overlay.setChecked(mode == "overlay")
        if save:
            self._settings["display_mode"] = mode

    def _set_theme(self, file_id: str, save: bool = True):
        from PyQt6.QtWidgets import QApplication
        self.theme.set_theme(file_id)
        scale = self._settings.get("font_scale", 1.0)
        QApplication.instance().setStyleSheet(
            self.theme.get_stylesheet(font_scale=scale))
        self._channel_panel.refresh_all()
        if hasattr(self, '_scope_status'):
            self._scope_status.set_palette(self.theme.statusbar_palette())
            self._scope_status.set_branding(self._get_branding_path())
        self._refresh_status_bar()
        if hasattr(self, '_theme_submenu'):
            self._rebuild_theme_menu()
        # Re-evaluate force_labels in case the new theme has a different value
        if hasattr(self, '_plot'):
            self._plot.apply_lane_label_settings(
                self._lane_label_size,
                self._show_lane_labels,
                self._allow_theme_force_labels,
                self._lane_label_spacing)
        if save:
            self._settings["theme"] = file_id

    def _toggle_y_lock(self, checked: bool):
        self._y_lock_auto = checked
        self._plot.set_y_lock_auto(checked)
        # Sync both toolbar checkbox and menu action
        self._tb_y_lock.blockSignals(True)
        self._tb_y_lock.setChecked(checked)
        self._tb_y_lock.blockSignals(False)
        self._act_y_lock.blockSignals(True)
        self._act_y_lock.setChecked(checked)
        self._act_y_lock.blockSignals(False)

    # ── Lane label settings ───────────────────────────────────────────

    def _dlg_lane_label_size(self):
        val, ok = QInputDialog.getInt(
            self, "Label Size", "Font size (pt) for in-panel trace names:",
            self._lane_label_size, 4, 32, 1)
        if ok:
            self._lane_label_size = val
            self._plot.apply_lane_label_settings(
                val, self._show_lane_labels, self._allow_theme_force_labels,
                self._lane_label_spacing)
            self._save_settings()

    def _dlg_lane_label_spacing(self):
        val, ok = QInputDialog.getDouble(
            self, "Label Spacing",
            "Gap between overlay labels (fraction of text height, 0.1 – 1.5):",
            self._lane_label_spacing, 0.1, 1.5, 2)
        if ok:
            self._lane_label_spacing = val
            self._plot.apply_lane_label_settings(
                self._lane_label_size, self._show_lane_labels,
                self._allow_theme_force_labels, val)
            self._save_settings()

    def _toggle_show_lane_labels(self, checked: bool):
        self._show_lane_labels = checked
        self._plot.apply_lane_label_settings(
            self._lane_label_size, checked, self._allow_theme_force_labels,
            self._lane_label_spacing)
        self._save_settings()

    def _toggle_allow_theme_force_labels(self, checked: bool):
        self._allow_theme_force_labels = checked
        self._plot.apply_lane_label_settings(
            self._lane_label_size, self._show_lane_labels, checked,
            self._lane_label_spacing)
        self._save_settings()

    def _toggle_rejection_enabled(self, checked: bool):
        self._rejection_enabled = checked
        self._settings["rejection_enabled"] = checked
        self._save_settings()

    def _dlg_rejection_max_lines(self):
        val, ok = QInputDialog.getInt(
            self, "Rejection — Max Lines",
            "Maximum number of malformed lines to skip between the\n"
            "column header and the first valid data row.\n"
            "Lines beyond this limit are passed through normally.",
            self._rejection_max_lines, 1, 1000, 1)
        if ok:
            self._rejection_max_lines = val
            self._settings["rejection_max_lines"] = val
            self._save_settings()

    def _set_export_segments_mode(self, mode: str):
        self._export_segments_mode = mode
        self._settings["export_segments_mode"] = mode
        self._save_settings()

    def _toggle_process_segments(self, enabled: bool):
        self._process_segments = enabled
        self._plot.set_process_segments(enabled)
        self._save_settings()

    def _toggle_scroll_primaries(self, enabled: bool):
        self._scroll_primaries = enabled
        self._channel_panel.set_scroll_primaries(enabled)
        self._save_settings()

    def _toggle_smart_scale(self, enabled: bool):
        self._smart_scale["enabled"] = enabled
        self._plot.set_smart_scale(self._smart_scale)
        self._save_settings()

    def _dlg_smart_scale_settings(self):
        from PyQt6.QtWidgets import QDialog, QFormLayout, QDialogButtonBox, QLineEdit
        dlg = QDialog(self)
        dlg.setWindowTitle("Smart Scale Settings")
        fl = QFormLayout(dlg)
        fl.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        e_s = QLineEdit(str(self._smart_scale.get("max_seconds", 300)))
        e_m = QLineEdit(str(self._smart_scale.get("max_minutes", 120)))
        e_h = QLineEdit(str(self._smart_scale.get("max_hours",   24)))
        fl.addRow("Switch to MM:SS above (seconds):", e_s)
        fl.addRow("Switch to HH:MM:SS above (minutes):", e_m)
        fl.addRow("Switch to DD:HH:MM:SS above (hours):", e_h)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        fl.addRow(bb)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                self._smart_scale["max_seconds"] = float(e_s.text())
            except ValueError:
                pass
            try:
                self._smart_scale["max_minutes"] = float(e_m.text())
            except ValueError:
                pass
            try:
                self._smart_scale["max_hours"] = float(e_h.text())
            except ValueError:
                pass
            self._plot.set_smart_scale(self._smart_scale)
            self._save_settings()

    def _dlg_segments_dim_opacity(self):
        val, ok = QInputDialog.getInt(
            self, "Segments — Dim Opacity",
            "Opacity for non-primary segments when dimmed (10 – 90 %):",
            self._segments_dim_opacity, 10, 90, 5)
        if ok:
            self._segments_dim_opacity = val
            self._settings["segments_dim_opacity"] = val
            self._plot.set_segment_dim_opacity(val)
            self._save_settings()

    def _dlg_segments_dash_size(self):
        val, ok = QInputDialog.getInt(
            self, "Segments — Dash Size",
            "Drawn dash length in pixels for non-primary segments (1 – 50):",
            self._segments_dash_size, 1, 50, 1)
        if ok:
            self._segments_dash_size = val
            self._settings["segments_dash_size"] = val
            self._plot.set_segment_dash_pattern(
                self._segments_dash_size, self._segments_gap_size)
            self._save_settings()

    def _dlg_segments_gap_size(self):
        val, ok = QInputDialog.getInt(
            self, "Segments — Gap Size",
            "Gap length in pixels between dashes for non-primary segments (1 – 50):",
            self._segments_gap_size, 1, 50, 1)
        if ok:
            self._segments_gap_size = val
            self._settings["segments_gap_size"] = val
            self._plot.set_segment_dash_pattern(
                self._segments_dash_size, self._segments_gap_size)
            self._save_settings()

    # ── Cursors ────────────────────────────────────────────────────────

    def _start_cursor_placement(self, cursor_id: int):
        x_pos = self._plot.get_cursor_placement_x(cursor_id)
        self._plot.set_cursor(cursor_id, x_pos)
        name = "A" if cursor_id == 0 else "B"
        self._status_lbl.setText(
            f"Cursor {name} placed at {x_pos:.6g} s — drag to move")

    def _on_cursor_values(self, data: dict):
        self._cursor_panel.update_cursors(data)
        t_a = data.get(0, {}).get("time")
        t_b = data.get(1, {}).get("time")
        if t_a is not None and t_b is not None:
            self._cursor_status.setText(f"ΔT = {t_b - t_a:.6g} s")
        self._refresh_status_bar()

    def _refresh_status_bar(self):
        """Schedule a status-bar refresh on the next event loop tick.
        Multiple calls within the same synchronous call stack coalesce into one."""
        if hasattr(self, '_status_bar_refresh_timer'):
            self._status_bar_refresh_timer.start()

    def _do_refresh_status_bar(self):
        if not hasattr(self, '_scope_status'):
            return
        x0, x1 = self._plot.get_current_view_range()

        # Get actual major tick spacing for time axis and each Y lane
        x_major_div = self._get_x_major_tick()
        y_major_divs = {}
        for trace in self._traces:
            if trace.visible:
                lane = self._plot._lanes.get(trace.name)
                if lane:
                    y_major_divs[trace.name] = self._get_y_major_tick(lane)

        trig_info = getattr(self, '_last_trigger_info', "")
        sinc_active = self._plot.get_sinc_active()
        unit_map = {t.name: getattr(t, 'unit', '') for t in self._traces}
        self._cursor_panel.set_trace_units(unit_map)
        self._cursor_panel.set_y_spacings(y_major_divs)
        interp_map = {t.name: getattr(t, '_interp_mode_override',
                                       self._interp_mode)
                      for t in self._traces}
        self._scope_status.set_trace_interp_modes(interp_map)
        self._scope_status.update(
            self._traces, x_major_div, trig_info, y_major_divs,
            sinc_active, settings=self._settings)

    def _get_x_major_tick(self) -> float:
        """Return the X tick spacing to show as div in the status bar.
        Reads the cached result from the last axis render so the status bar
        always agrees with what was actually drawn (no size mismatch)."""
        subdiv_label = self._adv_ui.get("div_subdiv_label", False)
        try:
            if self._plot._lanes:
                ax = next(iter(self._plot._lanes.values())).getPlotItem().getAxis('bottom')
            elif self._plot._mode == "overlay":
                ax = self._plot._overlay_widget.getPlotItem().getAxis('bottom')
            else:
                ax = None
            if ax is not None:
                cached = getattr(ax, '_last_tick_result', None)
                if cached:
                    return float(cached[-1][0] if subdiv_label else cached[0][0])
        except Exception:
            pass
        x0, x1 = self._plot.get_current_view_range()
        return (x1 - x0) / 10.0

    def _get_y_major_tick(self, lane) -> float:
        """Return the Y tick spacing to show as div in the status bar.
        Reads the cached result from the last axis render so the status bar
        always agrees with what was actually drawn (no size mismatch).
        Falls back to span/10 before the first render — never calls tickSpacing
        directly so the cache is never poisoned by the wrong axis size."""
        subdiv_label = self._adv_ui.get("div_subdiv_label", False)
        try:
            ax = lane.getPlotItem().getAxis('left')
            cached = getattr(ax, '_last_tick_result', None)
            if cached:
                return float(cached[-1][0] if subdiv_label else cached[0][0])
            # Pre-render fallback: rough estimate, no tickSpacing call
            vr = lane.getPlotItem().viewRange()[1]
            span = abs(vr[1] - vr[0])
            return span / 10.0 if span > 0 else 0.0
        except Exception:
            pass
        return 0.0

    def _on_sinc_active_changed(self, active: bool):
        self._refresh_status_bar()

    def _zoom_full_safe(self):
        """Zoom to fit all data — forces a range reset even after manual zoom.
        Suppresses intermediate per-lane redraws and issues one clean refresh
        after all ranges are applied, avoiding N × M redundant draw calls."""
        if not self._traces:
            return
        # Find global data extents
        import numpy as np
        t_mins, t_maxs, y_mins, y_maxs = [], [], [], []
        for trace in self._traces:
            if not trace.visible:
                continue
            t = trace.time_axis
            y = trace.processed_data
            if len(t):
                t_finite = t[np.isfinite(t)]
                if len(t_finite):
                    t_mins.append(float(t_finite.min()))
                    t_maxs.append(float(t_finite.max()))
            if len(y):
                y_finite = y[np.isfinite(y)]
                if len(y_finite):
                    y_mins.append(float(y_finite.min()))
                    y_maxs.append(float(y_finite.max()))
        if not t_mins:
            self._plot.zoom_full()
            return
        t0, t1 = min(t_mins), max(t_maxs)
        # Suppress per-lane redraws while setting ranges — one clean refresh at end
        self._plot._set_lanes_suppress(True)
        try:
            if self._plot._mode == "split":
                for lane in self._plot._lanes.values():
                    pi = lane.getPlotItem()
                    pi.disableAutoRange()
                    pi.setXRange(t0, t1, padding=0.02)
                    # Per-lane Y range — avoids forcing mV-scale lanes to the
                    # global V-scale range, which would produce nonsense Y/div.
                    y = lane.trace.processed_data
                    y_finite = y[np.isfinite(y)] if len(y) else np.array([])
                    if len(y_finite):
                        y0 = float(y_finite.min())
                        y1 = float(y_finite.max())
                        pad_y = (y1 - y0) * 0.05 or 0.1
                        pi.setYRange(y0 - pad_y, y1 + pad_y, padding=0)
            else:
                pi = self._plot._overlay_widget.getPlotItem()
                pi.disableAutoRange()
                pi.setXRange(t0, t1, padding=0.02)
                if y_mins:
                    y0, y1 = min(y_mins), max(y_maxs)
                    pad_y = (y1 - y0) * 0.05 or 0.1
                    pi.setYRange(y0 - pad_y, y1 + pad_y, padding=0)
        finally:
            self._plot._set_lanes_suppress(False)
            self._plot.refresh_all()
        self._refresh_status_bar()

    # ── Trace Events ──────────────────────────────────────────────────

    def _on_trace_visibility(self, name: str, visible: bool):
        self._plot.set_trace_visible(name, visible)
        self._refresh_status_bar()

    def _on_trace_color(self, name: str, color: str):
        self._plot.refresh_all()
        self._refresh_status_bar()

    def _on_trace_renamed(self, _trace_name: str, _new_label: str):
        """Propagate a label change from the channel panel to plot and status bar."""
        self._plot.refresh_all()      # redraws overlay legend / lane labels
        self._refresh_status_bar()    # status bar block uses trace.label

    def _on_segment_changed(self, trace_name: str):
        """Refresh the lane for a trace whose primary_segment or viewmode changed."""
        lane = self._plot._lanes.get(trace_name)
        if lane:
            lane.refresh_curve()

    def _on_unit_changed(self, trace_name: str, *_):
        """Refresh the y-axis label when a channel's unit is changed by the user."""
        lane = self._plot._lanes.get(trace_name)
        if lane:
            lane.refresh_curve()   # _add_trace_curve already calls _y_axis.set_unit
        else:
            self._plot.refresh_all()   # overlay mode — _ov_y_axis picks up first visible unit
        self._refresh_status_bar()     # keeps cursor-panel unit map current

    # ── Analysis ──────────────────────────────────────────────────────

    def _show_fft(self):
        if not self._traces:
            return
        from core.fft_dialog import FFTDialog
        vr = self._plot.get_current_view_range()
        dlg = FFTDialog(self._traces, view_range=vr,
                         fft_min_freq=self._fft_min_freq,
                         settings=self._settings, parent=self)
        dlg.exec()
        self._fft_min_freq = dlg.spin_min_freq.value()
        self._settings["fft_min_freq"] = self._fft_min_freq

    def _show_filter(self):
        if not self._traces:
            return
        from core.filter_dialog import FilterDialog
        dlg = FilterDialog(self._traces, parent=self)
        dlg.filters_applied.connect(self._on_filters_applied)
        dlg.exec()

    def _clear_all_filters(self):
        for trace in self._traces:
            trace.clear_filter()
        self._plot.refresh_all()
        self._status_lbl.setText("All filters cleared.")

    def _on_filters_applied(self, names):
        self._plot.refresh_all()
        self._status_lbl.setText(f"Filter applied: {', '.join(names)}")

    # ── Plugins ───────────────────────────────────────────────────────

    def _update_plugin_menu(self):
        # Remove all dynamic entries (including any submenus we created before)
        actions = self._plugins_menu.actions()
        for act in actions[self._plugin_actions_start:]:
            if act.menu():          # submenu — clean up the child QMenu too
                act.menu().deleteLater()
            self._plugins_menu.removeAction(act)

        from core.plugin_manager import _group_canonical
        plugins = self._plugins.get_plugins()
        if not plugins:
            a = QAction("(No plugins found)", self)
            a.setEnabled(False)
            self._plugins_menu.addAction(a)
        else:
            # Separate grouped from ungrouped; preserve first-seen order per group
            groups_order = []                      # display names in first-seen order
            groups_map   = {}                      # canonical_key → (display, [plugins])
            ungrouped    = []
            for p in plugins:
                if not p.group:
                    ungrouped.append(p)
                else:
                    key = _group_canonical(p.group)
                    if key not in groups_map:
                        groups_map[key] = (p.group, [])
                        groups_order.append(key)
                    groups_map[key][1].append(p)

            def _plugin_action(parent, plug):
                a = QAction(f"{plug.name}  [{plug.plugin_type}]", parent)
                a.setStatusTip(plug.description)
                a.triggered.connect(lambda checked, _p=plug: self._run_plugin(_p))
                return a

            # Add one sub-menu per named group
            for key in groups_order:
                display, group_plugins = groups_map[key]
                sub = self._plugins_menu.addMenu(display)
                for gp in group_plugins:
                    sub.addAction(_plugin_action(sub, gp))

            # Ungrouped: always put in an "Ungrouped" sub-menu
            if ungrouped:
                if groups_order:
                    self._plugins_menu.addSeparator()
                sub = self._plugins_menu.addMenu("Ungrouped")
                for p in ungrouped:
                    sub.addAction(_plugin_action(sub, p))

        if self._plugins.load_errors:
            self._plugins_menu.addSeparator()
            a = QAction(f"⚠ {len(self._plugins.load_errors)} load error(s)", self)
            a.triggered.connect(self._show_plugin_errors)
            self._plugins_menu.addAction(a)

    def _run_plugin(self, plugin):
        traces_copy = copy.deepcopy(self._traces)
        x0, x1 = self._plot.get_current_view_range()
        context = {
            "view_range": (x0, x1),
            "sample_rate": self._traces[0].sample_rate if self._traces else 1.0,
            "parent_window": self,
        }
        try:
            result = self._plugins.run_plugin(plugin.name, traces_copy, context)
            if plugin.plugin_type == "processor" and result:
                name_map = {t.name: t for t in result}
                for trace in self._traces:
                    if trace.name in name_map:
                        r = name_map[trace.name]
                        trace.raw_data = r.raw_data
                        trace.scaling.enabled = False
                        trace._invalidate_cache()
                self._plot.refresh_all()
                self._status_lbl.setText(f"Plugin '{plugin.name}' applied.")
        except Exception as e:
            QMessageBox.critical(self, "Plugin Error", str(e))

    def _reload_plugins(self):
        self._plugins.reload()
        self._update_plugin_menu()
        self._status_lbl.setText(
            f"Plugins reloaded: {self._plugins.plugin_count} found.")

    def _open_plugins_dir(self):
        import subprocess, platform
        d = self._plugins.PLUGIN_DIR
        os.makedirs(d, exist_ok=True)
        if platform.system() == "Windows":
            os.startfile(d)
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", d])
        else:
            subprocess.Popen(["xdg-open", d])

    def _show_plugin_errors(self):
        QMessageBox.warning(self, "Plugin Errors",
                             "\n\n".join(self._plugins.load_errors))

    # ── Status ────────────────────────────────────────────────────────

    def _update_status(self):
        n = len(self._traces)
        vis = sum(1 for t in self._traces if t.visible)
        filt = sum(1 for t in self._traces if t.has_filter)
        if n == 0:
            self._status_lbl.setText("Ready  |  No data loaded")
        else:
            pts = sum(t.n_samples for t in self._traces)
            parts = [f"{n} traces", f"{vis} visible", f"{pts:,} samples"]
            if filt:
                parts.append(f"{filt} filtered")
            self._status_lbl.setText("  |  ".join(parts))

    # ── About ─────────────────────────────────────────────────────────

    def _show_about(self):
        import os
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        notice = ""
        for fname in ("NOTICE.md", "LICENSE.md"):
            p = os.path.join(base, fname)
            if os.path.exists(p):
                try:
                    notice += f"<hr><pre style='font-size:9px;'>{open(p).read()[:2000]}</pre>"
                except Exception:
                    pass

        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextBrowser, QPushButton
        dlg = QDialog(self)
        dlg.setWindowTitle("About ChipFX TraceLab")
        dlg.resize(600, 500)
        lay = QVBoxLayout(dlg)
        tb = QTextBrowser()
        tb.setOpenExternalLinks(True)
        tb.setHtml(
            "<h2>ChipFX TraceLab</h2>"
            "<p>Modular oscilloscope &amp; signal data viewer.</p>"
            "<p>A <b>ChipFX</b> instrument software project.</p>"
            "<p><b>Keyboard shortcuts:</b><br>"
            "F — Zoom to fit &nbsp; T — Fit X &nbsp; A — Fit Y &nbsp; "
            "L — Y auto-lock<br>"
            "+ / − — Zoom &nbsp; Ctrl+O — Open &nbsp; "
            "Ctrl+F — FFT &nbsp; Ctrl+P — Screenshot</p>"
            "<p>CSV metadata: <code>#samplerate=10k</code>, "
            "<code>#gain=2.5/4096</code>, <code>#offset=-1.25</code></p>"
            "<p>Plugins: drop <code>.py</code> files in "
            "<code>plugins/</code> folder.</p>"
            + notice)
        lay.addWidget(tb)
        btn = QPushButton("Close")
        btn.clicked.connect(dlg.accept)
        lay.addWidget(btn)
        dlg.exec()

    def _cursor_set_t0_at_a(self):
        """Called from cursor panel Set t=0 button — shift to cursor A."""
        t_pos = self._plot._cursors.get(0)
        if t_pos is None:
            return
        self._on_trigger_set_t0(t_pos)

    def _jump_to_t0(self):
        """Center t=0 in the current viewport without changing zoom span."""
        x0, x1 = self._plot.get_current_view_range()
        half_span = (x1 - x0) / 2.0
        self._plot.zoom_x_range(-half_span, half_span)
        self._refresh_status_bar()

    def _on_trigger_found(self, t_pos: float):
        """Trigger located — optionally zoom context window around it."""
        # User explicitly placed a new trigger: invalidate the epoch anchor so
        # the next _apply_retrigger re-anchors near this new position.
        self._epoch_anchor_idx = None
        trace = None
        for t in self._traces:
            if t.visible:
                trace = t
                break
        level = ""
        if hasattr(self, '_trigger_panel'):
            level = self._trigger_panel.edit_level.text()
            ch = self._trigger_panel.combo_ch.currentText()
            edge = self._trigger_panel.combo_edge.currentText()[0]
            self._last_trigger_info = f"{edge} {ch} {level}"
        if self._trigger_panel.chk_zoom.isChecked():
            x0, x1 = self._plot.get_current_view_range()
            half_win = (x1 - x0) / 2
            self._plot.zoom_x_range(t_pos - half_win, t_pos + half_win)
        self._refresh_status_bar()
        self._apply_retrigger(t_pos)

    def _on_trigger_set_t0(self, t_pos: float):
        """Shift all trace time axes so t_pos becomes 0."""
        x0, x1 = self._plot.get_current_view_range()
        shifted_x0 = x0 - t_pos
        shifted_x1 = x1 - t_pos
        cursor_positions = dict(self._plot._cursors)

        for trace in self._traces:
            # Shift the stored time data
            if trace.time_data is not None:
                trace.time_data = trace.time_data - t_pos
            else:
                # Convert to explicit time array shifted by t_pos
                import numpy as np
                trace.time_data = trace.time_axis - t_pos
            trace._computed_time = None  # invalidate cache

        self._plot.refresh_all()

        for cid, pos in cursor_positions.items():
            if pos is not None:
                self._plot.set_cursor(cid, pos - t_pos)

        self._plot.zoom_x_range(shifted_x0, shifted_x1)
        self._plot._update_range_bar()
        self._refresh_status_bar()
        self._status_lbl.setText(f"t=0 set to trigger at {t_pos:.6g} s")

    def _refresh_trigger_channels(self):
        self._trigger_panel.update_traces(self._traces)
        self._update_per_trace_interp_menu()

    def _update_per_trace_interp_menu(self):
        """Rebuild the per-channel interpolation submenu."""
        if not hasattr(self, '_per_trace_interp_menu'):
            return
        self._per_trace_interp_menu.clear()
        if not self._traces:
            self._per_trace_interp_menu.addAction("(no traces)").setEnabled(False)
            return
        from PyQt6.QtGui import QActionGroup
        for trace in self._traces:
            ch_menu = self._per_trace_interp_menu.addMenu(trace.label)
            current = getattr(trace, '_interp_mode_override', self._interp_mode)
            ag = QActionGroup(ch_menu)
            ag.setExclusive(True)
            name = trace.name
            for mode, lbl in [("linear", "Linear"),
                               ("cubic",  "Cubic Spline"),
                               ("sinc",   "Sinc (sin(x)/x)")]:
                a = ch_menu.addAction(lbl)
                a.setCheckable(True)
                a.setChecked(current == mode)
                ag.addAction(a)
                a.triggered.connect(
                    lambda _, n=name, m=mode:
                        self._on_channel_interp_changed(n, m))

    # ── Channel order ─────────────────────────────────────────────────

    def _on_channel_order_changed(self, name_order: list):
        """Channel panel drag-reorder → update plot, cursor table and status bar."""
        self._plot.reorder_traces(name_order)
        self._cursor_panel.set_trace_order(name_order)
        # Keep self._traces in channel-panel order so the status bar blocks
        # appear in the same sequence as the trace panel rows.
        name_to_trace = {t.name: t for t in self._traces}
        ordered = [name_to_trace[n] for n in name_order if n in name_to_trace]
        extras  = [t for t in self._traces if t.name not in set(name_order)]
        self._traces = ordered + extras
        self._refresh_status_bar()

    # ── Settings dialogs ───────────────────────────────────────────────

    def _show_font_scale_dialog(self):
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QPushButton, QDoubleSpinBox
        dlg = QDialog(self)
        dlg.setWindowTitle("Font Scale")
        dlg.setFixedWidth(340)
        layout = QVBoxLayout(dlg)

        current = self._settings.get("font_scale", 1.0)
        layout.addWidget(QLabel(
            "Scale all UI text. Affects menus, labels, panels.\n"
            "Restart not required — applied immediately."))

        hl = QHBoxLayout()
        hl.addWidget(QLabel("Scale:"))
        spin = QDoubleSpinBox()
        spin.setRange(0.6, 3.0)
        spin.setSingleStep(0.05)
        spin.setDecimals(2)
        spin.setValue(current)
        hl.addWidget(spin)
        hl.addWidget(QLabel("  (1.0 = normal, 1.25 = 125%)"))
        layout.addLayout(hl)

        def apply():
            scale = spin.value()
            self._settings["font_scale"] = scale
            self._apply_font_scale(scale)

        bh = QHBoxLayout()
        btn_apply = QPushButton("Apply")
        btn_apply.clicked.connect(apply)
        btn_ok = QPushButton("OK")
        btn_ok.clicked.connect(lambda: (apply(), dlg.accept()))
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(dlg.reject)
        bh.addWidget(btn_apply); bh.addStretch()
        bh.addWidget(btn_cancel); bh.addWidget(btn_ok)
        layout.addLayout(bh)
        dlg.exec()

    def _apply_font_scale(self, scale: float):
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtGui import QFont
        app = QApplication.instance()
        base_size = 10  # pt
        f = QFont()
        f.setPointSize(max(7, int(base_size * scale)))
        app.setFont(f)
        # Re-apply stylesheet with scaled font-size
        app.setStyleSheet(self.theme.get_stylesheet(font_scale=scale))

    def _show_decimal_sep_dialog(self):
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
            QLabel, QComboBox, QPushButton, QGroupBox, QGridLayout)
        dlg = QDialog(self)
        dlg.setWindowTitle("Number Input Settings")
        dlg.setFixedWidth(380)
        layout = QVBoxLayout(dlg)

        layout.addWidget(QLabel(
            "Configure how numbers are entered in input fields.\n"
            "Applies to all Gain/Offset/frequency fields."))

        grp = QGroupBox("Decimal Separator")
        gl = QGridLayout(grp)
        gl.addWidget(QLabel("Decimal point:"), 0, 0)
        self._combo_decimal = QComboBox()
        self._combo_decimal.addItems(["Both '.' and ',' accepted (default)",
                                       "Dot '.' only",
                                       "Comma ',' only"])
        cur = self._settings.get("decimal_sep", "both")
        idx = {"both": 0, "dot": 1, "comma": 2}.get(cur, 0)
        self._combo_decimal.setCurrentIndex(idx)
        gl.addWidget(self._combo_decimal, 0, 1)

        gl.addWidget(QLabel("Thousands separator:"), 1, 0)
        self._combo_thousands = QComboBox()
        self._combo_thousands.addItems(["None (default)", "Dot '.'", "Comma ','"])
        cur_t = self._settings.get("thousands_sep", "none")
        idx_t = {"none": 0, "dot": 1, "comma": 2}.get(cur_t, 0)
        self._combo_thousands.setCurrentIndex(idx_t)
        gl.addWidget(self._combo_thousands, 1, 1)
        layout.addWidget(grp)

        layout.addWidget(QLabel(
            "<i>Note: TraceLab input fields always accept both '.' and ','\n"
            "as decimal when 'Both' is selected, regardless of system locale.\n"
            "The numpad dot will always work.</i>"))

        bh = QHBoxLayout()
        btn_ok = QPushButton("OK")
        btn_ok.setStyleSheet("background:#2060c0;color:white;padding:4px 16px;")
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(dlg.reject)
        btn_ok.clicked.connect(lambda: (self._save_decimal_settings(), dlg.accept()))
        bh.addStretch(); bh.addWidget(btn_cancel); bh.addWidget(btn_ok)
        layout.addLayout(bh)
        dlg.exec()

    def _save_decimal_settings(self):
        dec_map = {0: "both", 1: "dot", 2: "comma"}
        tho_map = {0: "none", 1: "dot", 2: "comma"}
        self._settings["decimal_sep"] = dec_map[self._combo_decimal.currentIndex()]
        self._settings["thousands_sep"] = tho_map[self._combo_thousands.currentIndex()]

    # ── Viewport auto-zoom from CSV metadata ──────────────────────────

    def _apply_viewport_from_metadata(self, meta):
        """After import, zoom to viewport hints if present in CSV headers."""
        from PyQt6.QtCore import QTimer as _QTimer
        if meta.view_time_start is not None and meta.view_time_stop is not None:
            t0, t1 = meta.view_time_start, meta.view_time_stop
            _QTimer.singleShot(80, lambda: self._plot.zoom_x_range(t0, t1))
        elif (meta.view_sample_start is not None
              and meta.view_sample_stop is not None
              and self._traces):
            trace = self._traces[0]
            ta = trace.time_axis
            n = len(ta)
            i0 = max(0, min(meta.view_sample_start, n-1))
            i1 = max(0, min(meta.view_sample_stop, n-1))
            t0, t1 = float(ta[i0]), float(ta[i1])
            _QTimer.singleShot(80, lambda: self._plot.zoom_x_range(t0, t1))

    def _toggle_remember_folder(self, checked: bool):
        self._settings["remember_folder"] = checked

    # ── Interpolation ──────────────────────────────────────────────────

    def _set_interp_mode(self, mode: str):
        self._interp_mode = mode
        self._plot.set_interp_mode(mode)
        self._settings["interp_mode"] = mode
        self._reapply_retrigger()

    # ── Periodicity estimation ─────────────────────────────────────────────

    def _estimate_period_async(self, trace: TraceModel) -> None:
        """Start a background period estimation for *trace*.
        Returns immediately; result arrives via _on_period_result.
        If the worker exceeds the configured timeout the trace keeps its
        current (unknown) period and a status-bar notice is shown."""
        if self._periodicity_method == TIER_DISABLED:
            trace.period_estimate             = 0.0
            trace.period_confidence           = 0.0
            trace.period_estimation_attempted = False
            return

        timeout_ms = int(
            self._settings.get("periodicity_estimation_timeout", 5) * 1000)

        # Mark as in-progress (unknown until the worker returns)
        trace.period_estimate             = 0.0
        trace.period_confidence           = 0.0
        trace.period_estimation_attempted = True
        trace._period_timed_out           = False

        worker = _PeriodEstimateWorker(
            trace.name,
            trace.processed_data.copy(),   # copy: numpy arrays are not thread-safe for writing
            trace.dt,
            self._periodicity_method,
            parent=self,
        )

        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(timeout_ms)

        trace_name = trace.name   # capture for lambdas

        worker.result_ready.connect(self._on_period_result)
        worker.result_ready.connect(lambda *_: timer.stop())
        timer.timeout.connect(lambda: self._on_period_timeout(trace_name))
        worker.finished.connect(timer.stop)
        worker.finished.connect(worker.deleteLater)

        timer.start()
        worker.start()

    def _on_period_result(self, trace_name: str, T: float, conf: float) -> None:
        """Receive a completed period estimate from the background worker."""
        trace = next((t for t in self._traces if t.name == trace_name), None)
        if trace is None:
            return   # trace removed while estimating
        if getattr(trace, '_period_timed_out', False):
            return   # timeout already handled this trace; discard late result
        trace.period_estimate             = T
        trace.period_confidence           = conf
        trace.period_estimation_attempted = True
        self._refresh_status_bar()
        if hasattr(self, '_scope_status'):
            self._scope_status.repaint_channel_blocks()

    def _on_period_timeout(self, trace_name: str) -> None:
        """Called when period estimation for *trace_name* exceeded the timeout."""
        trace = next((t for t in self._traces if t.name == trace_name), None)
        if trace is not None:
            trace._period_timed_out   = True
            trace.period_estimate     = 0.0   # remains unknown
            trace.period_confidence   = 0.0
        timeout_s = self._settings.get("periodicity_estimation_timeout", 5)
        self._status_lbl.setText(
            f"Periodicity estimation timed out for '{trace_name}' "
            f"({timeout_s} s) — use a faster tier or increase the timeout.")

    def _set_periodicity_method(self, method: str) -> None:
        """Change the active method, re-estimate all loaded traces, save."""
        self._periodicity_method = method
        self._settings["periodicity_estimation_method"] = method
        if hasattr(self, "_periodicity_method_actions"):
            act = self._periodicity_method_actions.get(method)
            if act:
                act.setChecked(True)
        for trace in self._traces:
            self._estimate_period_async(trace)

    # ── Viewport limits ────────────────────────────────────────────────

    def _set_viewport_limits_mode(self, mode: str) -> None:
        self._limits_config["mode"] = mode
        self._plot.set_limits_config(self._limits_config)

    def _dlg_vp_int(self, key: str, title: str, prompt: str,
                    lo: int, hi: int) -> None:
        current = int(self._limits_config.get(key, DEFAULT_LIMITS_CONFIG[key]))
        val, ok = QInputDialog.getInt(self, title, prompt, current, lo, hi, 1)
        if ok:
            self._limits_config[key] = val
            self._plot.set_limits_config(self._limits_config)

    def _dlg_period_timeout(self) -> None:
        """Settings > Periodicity Estimation > Advanced Settings > Set Time Out."""
        current = float(self._settings.get("periodicity_estimation_timeout", 5))
        val, ok = QInputDialog.getDouble(
            self, "Periodicity Estimation — Time Out",
            "Maximum time allowed per trace (seconds).\n"
            "If estimation takes longer the period is left unknown.\n"
            "Increase for Precise/Extreme tiers on very large files.\n\n"
            "Time out (1 – 300 s):",
            current, 1.0, 300.0, 1)
        if ok:
            self._settings["periodicity_estimation_timeout"] = val

    # ── Status bar navigation helper ───────────────────────────────────

    def _statusbar_snap(self, direction: int):
        """Scroll the status bar to the next whole block edge.
        direction: +1 = towards higher scroll value (right), -1 = towards left.
        Always moves at least one edge even if already on a boundary."""
        sb   = self._scope_status._ch_scroll.horizontalScrollBar()
        step = 122   # BLOCK_W(120) + SEP_W(2)
        pos  = sb.value()
        if direction > 0:
            new_pos = (pos // step + 1) * step
        else:
            if pos % step == 0:
                new_pos = max(0, pos - step)
            else:
                new_pos = (pos // step) * step
        sb.setValue(new_pos)

    # ── Advanced UI helpers ────────────────────────────────────────────

    def _adv_ui_save(self):
        self._adv_ui_apply()
        self._settings["advanced_ui"] = dict(self._adv_ui)

    def _adv_ui_apply(self):
        self._plot.set_scroll_settings(self._adv_ui)
        self._plot.set_min_lane_height(
            self._adv_ui.get("split_min_lane_height", 80))
        self._scope_status.set_statusbar_scroll_enabled(
            self._adv_ui.get("statusbar_scroll", True))
        self._plot.set_div_settings(self._adv_ui)

    def _toggle_statusbar_scroll(self, checked: bool):
        self._adv_ui["statusbar_scroll"] = checked
        self._adv_ui_save()

    def _toggle_scroll_zoom(self, checked: bool):
        self._adv_ui["scroll_zoom_enabled"] = checked
        self._adv_ui_save()

    def _toggle_scroll_list(self, checked: bool):
        self._adv_ui["scroll_list_enabled"] = checked
        self._adv_ui_save()

    def _set_scroll_default(self, action: str):
        self._adv_ui["scroll_default"] = action
        self._adv_ui_save()

    def _set_arrow_mode(self, mode: str):
        self._adv_ui["arrow_mode"] = mode
        self._adv_ui_save()

    def _dlg_scroll_modifier_keys(self):
        current = ", ".join(self._adv_ui.get("scroll_modifier_keys",
                                             ["ctrl", "alt", "shift"]))
        text, ok = QInputDialog.getText(
            self, "Scroll Modifier Keys",
            "Comma-separated list of modifier keys that switch the scroll action.\n"
            "Valid values: ctrl, alt, shift  (e.g. 'ctrl, alt')\n\n"
            "Any one of the listed keys triggers the alternate scroll mode:",
            text=current)
        if ok:
            keys = [k.strip().lower() for k in text.split(",")]
            keys = [k for k in keys if k in ("ctrl", "alt", "shift")]
            self._adv_ui["scroll_modifier_keys"] = keys or ["ctrl"]
            self._adv_ui_save()

    def _dlg_min_lane_height(self):
        current = int(self._adv_ui.get("split_min_lane_height", 80))
        val, ok = QInputDialog.getInt(
            self, "Minimum Trace Height",
            "Minimum height of each split-view trace lane (pixels).\n"
            "Smaller values let you view more traces at once on a small screen.",
            current, 40, 800, 10)
        if ok:
            self._adv_ui["split_min_lane_height"] = val
            self._adv_ui_save()

    def _dlg_div_settings(self):
        from PyQt6.QtWidgets import (QDialog, QFormLayout, QSpinBox,
                                     QCheckBox, QDialogButtonBox)
        dlg = QDialog(self)
        dlg.setWindowTitle("Div Settings")
        form = QFormLayout(dlg)

        sb_halves = QSpinBox()
        sb_halves.setRange(1, 500)
        sb_halves.setSuffix(" px")
        sb_halves.setToolTip(
            "Minimum major-tick pixel height/width to draw a midpoint (÷2) sub-division line.")
        sb_halves.setValue(int(self._adv_ui.get("div_halves_px", 15)))
        form.addRow("Show ÷2 sub-div above:", sb_halves)

        sb_fifths = QSpinBox()
        sb_fifths.setRange(1, 500)
        sb_fifths.setSuffix(" px")
        sb_fifths.setToolTip(
            "Minimum major-tick pixel size to draw four (÷5) sub-division lines.")
        sb_fifths.setValue(int(self._adv_ui.get("div_fifths_px", 30)))
        form.addRow("Show ÷5 sub-div above:", sb_fifths)

        sb_tenths = QSpinBox()
        sb_tenths.setRange(1, 500)
        sb_tenths.setSuffix(" px")
        sb_tenths.setToolTip(
            "Minimum major-tick pixel size to draw nine (÷10) sub-division lines.")
        sb_tenths.setValue(int(self._adv_ui.get("div_tenths_px", 60)))
        form.addRow("Show ÷10 sub-div above:", sb_tenths)

        chk_subdiv = QCheckBox("Show /DIV status on sub-divisions")
        chk_subdiv.setToolTip(
            "When enabled, the status bar reports the finest visible sub-division spacing\n"
            "instead of the major tick spacing.\n"
            "Default off: the status bar always shows the major division.")
        chk_subdiv.setChecked(bool(self._adv_ui.get("div_subdiv_label", False)))
        form.addRow("", chk_subdiv)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._adv_ui["div_halves_px"]    = sb_halves.value()
            self._adv_ui["div_fifths_px"]    = sb_fifths.value()
            self._adv_ui["div_tenths_px"]    = sb_tenths.value()
            self._adv_ui["div_subdiv_label"] = chk_subdiv.isChecked()
            self._adv_ui_save()

    def _set_extrap_mode(self, mode: str) -> None:
        """Switch extrapolation behaviour and re-render."""
        self._retrigger_extrap_mode = mode
        self._settings["retrigger_extrap_mode"] = mode
        self._reapply_retrigger()

    def _clear_extrap_flags(self):
        """Clear extrapolation badges on all traces and repaint status blocks."""
        for t in self._traces:
            t.retrigger_extrapolating = False
        if hasattr(self, '_scope_status'):
            self._scope_status.repaint_channel_blocks()

    def _set_draw_mode(self, mode: str):
        self._draw_mode = mode
        if hasattr(self, "_draw_mode_actions") and mode in self._draw_mode_actions:
            self._draw_mode_actions[mode].setChecked(True)
        self._plot.set_draw_mode(mode)
        self._settings["draw_mode"] = mode

    def _on_reset_trace_color(self, trace_name: str):
        """Reset a single trace to its theme-default colour."""
        idx = next((i for i, t in enumerate(self._traces)
                    if t.name == trace_name), 0)
        for trace in self._traces:
            if trace.name == trace_name:
                trace.reset_color_to_theme(idx)
                trace.sync_theme_color(self.theme.active_theme)
                break
        self._channel_panel.refresh_all()
        self._plot.refresh_all()
        self._refresh_status_bar()

    def _on_channel_interp_changed(self, name: str, mode: str):
        """Channel panel context menu or All Lin/Sinc triggered."""
        self._plot.set_interp_mode_for_trace(name, mode)
        self._refresh_status_bar()

    def _on_status_bar_toggle_interp(self, name: str):
        """Clicking the interp badge on a channel block cycles linear→cubic→sinc→…"""
        _CYCLE = ("linear", "cubic", "sinc")
        for trace in self._traces:
            if trace.name == name:
                cur = getattr(trace, '_interp_mode_override', self._interp_mode)
                idx = _CYCLE.index(cur) if cur in _CYCLE else 0
                new_mode = _CYCLE[(idx + 1) % len(_CYCLE)]
                trace._interp_mode_override = new_mode
                self._plot.set_interp_mode_for_trace(name, new_mode)
                break
        self._refresh_status_bar()

    # ── Retrigger pipeline ─────────────────────────────────────────────

    def _set_retrigger_mode(self, mode: str):
        """Select a persistence mode; deselects Averaging and Interpolate."""
        self._retrigger_mode = mode
        # Auto-wire selection & emphasis to match the mode's semantic meaning.
        # Future Persist: first trigger is the "hard" line; history grows forward.
        # Past Persist Normal: last trigger is the "hard" line; history grows back.
        if mode == MODE_PERSIST_FUTURE:
            self._persist_settings["selection"] = "first"
            self._persist_settings["emphasis"]  = "first"
        elif mode == MODE_PERSIST_PAST:
            self._persist_settings["selection"] = "last"
            self._persist_settings["emphasis"]  = "last"
        self._act_rt_averaging.blockSignals(True)
        self._act_rt_averaging.setChecked(False)
        self._act_rt_averaging.blockSignals(False)
        self._act_rt_interp.blockSignals(True)
        self._act_rt_interp.setChecked(False)
        self._act_rt_interp.blockSignals(False)
        self._plot.clear_persistence_layers()
        self._plot.clear_retrigger_curve()
        self._last_retrigger_results.clear()
        self._clear_extrap_flags()
        self._update_retrigger_controls()
        if mode != MODE_OFF:
            self._reapply_retrigger()

    def _toggle_retrigger_averaging(self, checked: bool):
        """Toggle trigger-aligned averaging; disables persistence on enable."""
        if checked:
            self._retrigger_mode = MODE_AVERAGING
            self._epoch_anchor_idx = None   # re-anchor at the new trigger pos
            self._act_persist_off.blockSignals(True)
            self._act_persist_off.setChecked(True)
            self._act_persist_off.blockSignals(False)
            self._act_rt_interp.blockSignals(True)
            self._act_rt_interp.setChecked(False)
            self._act_rt_interp.blockSignals(False)
        else:
            self._retrigger_mode = MODE_OFF
        self._plot.clear_persistence_layers()
        self._plot.clear_retrigger_curve()
        self._last_retrigger_results.clear()
        self._clear_extrap_flags()
        self._update_retrigger_controls()
        if checked:
            self._reapply_retrigger()

    def _toggle_retrigger_interpolation(self, checked: bool):
        """Toggle sub-sample interpolation; disables persistence on enable."""
        if checked:
            self._retrigger_mode = MODE_INTERPOLATION
            self._epoch_anchor_idx = None   # re-anchor at the new trigger pos
            self._act_persist_off.blockSignals(True)
            self._act_persist_off.setChecked(True)
            self._act_persist_off.blockSignals(False)
            self._act_rt_averaging.blockSignals(True)
            self._act_rt_averaging.setChecked(False)
            self._act_rt_averaging.blockSignals(False)
        else:
            self._retrigger_mode = MODE_OFF
        self._plot.clear_persistence_layers()
        self._plot.clear_retrigger_curve()
        self._last_retrigger_results.clear()
        self._clear_extrap_flags()
        self._update_retrigger_controls()
        if checked:
            self._reapply_retrigger()

    def _apply_retrigger(self, t_pos: float):
        """
        Full retrigger pipeline.

        Triggers are found ONCE on the selected trigger channel, then the
        same trigger times are applied to ALL visible traces — exactly how a
        real oscilloscope works: the trigger channel fires, every channel
        captures in sync.
        """
        if self._retrigger_mode == MODE_OFF:
            return

        level    = self._trigger_panel.edit_level.get_value(0.0)
        edge_idx = self._trigger_panel.combo_edge.currentIndex()
        x0, x1  = self._plot.get_current_view_range()
        view_span = x1 - x0
        if view_span <= 0:
            return

        # Segments must cover from the trigger point to BOTH view edges, not
        # just half the view width.  When the trigger is near the left edge
        # (the common case), view_span/2 leaves the right portion undrawn.
        # Compute a symmetric half-span that reaches the farther edge.
        left_span  = max(0.0, t_pos - x0)
        right_span = max(0.0, x1 - t_pos)
        # seg_span is passed as view_span; apply_mode_with_triggers halves it
        # internally and adds 10 % margin, so effective half = largest_edge * 1.1
        seg_span = 2.0 * max(left_span, right_span, view_span / 2.0)

        # ── Step 1: find triggers on the selected trigger channel only ─────────
        trig_trace = self._trigger_panel._get_selected_trace()
        if trig_trace is None or len(trig_trace.time_axis) < 2:
            return

        trig_t = trig_trace.time_axis
        trig_y = trig_trace.processed_data
        dt_est = float(trig_t[1] - trig_t[0])

        # ── Epoch selection ───────────────────────────────────────────────────
        #
        # Persistence modes use a view-span holdoff so consecutive windows
        # don't overlap — this is intentional and view-dependent.
        #
        # Averaging / interpolation modes MUST use a fixed, view-independent
        # epoch set.  The holdoff approach causes different zoom levels to
        # select different trigger crossings (e.g. T/2-spaced on "either edge"
        # once the holdoff shrinks below half a period), which flips the phase
        # and depresses amplitude via anti-phase cancellation when averaging.
        #
        # Fix: anchor to ONE trigger crossing near t_pos (stored in
        # _epoch_anchor_idx), then step forward and backward in integer
        # multiples of T_samples.  The anchor survives zoom/pan; it only
        # resets when the user explicitly places a new trigger or changes the
        # trigger channel / level / edge.

        trig_ch = self._trigger_panel.combo_ch.currentText()

        if self._retrigger_mode in (MODE_AVERAGING, MODE_INTERPOLATION):
            T_est = trig_trace.period_estimate
            conf  = trig_trace.period_confidence

            if T_est > 0 and conf >= 0.3 and dt_est > 0:
                # Re-anchor when trigger parameters changed or never set
                anchor_stale = (
                    self._epoch_anchor_idx is None
                    or self._epoch_anchor_ch    != trig_ch
                    or abs(self._epoch_anchor_level - level) > 1e-9
                    or self._epoch_anchor_edge  != edge_idx
                )
                if anchor_stale:
                    # Find all raw crossings (no holdoff) and pick the one
                    # closest to the user-placed trigger position
                    raw_idxs = find_all_triggers(
                        trig_y, trig_t, level, edge_idx, holdoff_samples=0)
                    if raw_idxs:
                        t_arr   = np.array([float(trig_t[i]) for i in raw_idxs])
                        closest = int(np.argmin(np.abs(t_arr - t_pos)))
                        self._epoch_anchor_idx   = raw_idxs[closest]
                        self._epoch_anchor_ch    = trig_ch
                        self._epoch_anchor_level = level
                        self._epoch_anchor_edge  = edge_idx

                if self._epoch_anchor_idx is not None:
                    # Walk forward and backward from the anchor using
                    # floating-point period steps so each epoch is independently
                    # rounded to the nearest sample.  This bounds the phase error
                    # at any epoch k to ±dt/2 regardless of how far from the
                    # anchor we walk — integer accumulation (ep += T_samples)
                    # compounds the fractional error and causes phase drift that
                    # grows linearly with the number of periods.
                    n_total  = len(trig_y)
                    anchor   = self._epoch_anchor_idx
                    T_float  = T_est / dt_est   # fractional samples per period
                    epoch_idxs: list = []
                    k = 0
                    while True:
                        ep = int(round(anchor + k * T_float))
                        if ep >= n_total:
                            break
                        epoch_idxs.append(ep)
                        k += 1
                    k = -1
                    while True:
                        ep = int(round(anchor + k * T_float))
                        if ep < 0:
                            break
                        epoch_idxs.append(ep)
                        k -= 1
                    epoch_idxs.sort()

                    idxs    = epoch_idxs
                    t_trigs = [float(trig_t[i]) for i in idxs]

                    if not idxs:
                        self._plot.clear_persistence_layers()
                        self._plot.clear_retrigger_curve()
                        self._last_retrigger_results.clear()
                        return

                    # Snap the display reference to the nearest epoch time.
                    # _auto_find_trigger returns a noisy sub-sample crossing
                    # that can drift by arbitrary fractions of T between zoom
                    # steps, making avg_time + t_display appear to phase-rotate.
                    # Snapping to the nearest integer-sample epoch time ensures
                    # the displayed result stays phase-locked across zoom.
                    t_arr     = np.fromiter(t_trigs, dtype=float)
                    t_display = float(t_arr[np.argmin(np.abs(t_arr - t_pos))])

                    # Epoch drift: integer T_samples accumulates error vs. the
                    # true period, so t_display can lag t_pos by up to T/2.
                    # Recompute seg_span from t_display so the segment half-span
                    # actually reaches both view edges from the snapped anchor.
                    left_d  = max(0.0, t_display - x0)
                    right_d = max(0.0, x1 - t_display)
                    seg_span = 2.0 * max(left_d, right_d, view_span / 2.0)

                    return self._apply_retrigger_render(
                        t_display, idxs, t_trigs, trig_t, seg_span, x0, x1)

        # ── Persistence (and avg/interp fallback when no period) ─────────────
        # One view-span holdoff: no two consecutive trigger windows may overlap
        holdoff = max(1, int(view_span / dt_est)) if dt_est > 0 else 1

        idxs, t_trigs = find_all_triggers_with_times(
            trig_y, trig_t, level, edge_idx, holdoff)

        if not idxs:
            # No triggers found — clear any existing display and bail
            self._plot.clear_persistence_layers()
            self._plot.clear_retrigger_curve()
            self._last_retrigger_results.clear()
            return

        self._apply_retrigger_render(t_pos, idxs, t_trigs, trig_t,
                                     seg_span, x0, x1)

    def _compute_trace_epochs(
            self,
            trace,
            t_arr: np.ndarray,
            y_arr: np.ndarray,
            t_pos: float,
    ):
        """
        Compute independent period-based epoch indices for *trace* in
        interpolation mode.

        Each non-trigger trace finds its own anchor crossing near *t_pos*
        (using the trace's mean as the level, rising edge) and steps in
        floating-point multiples of its own period so that each epoch index
        is independently rounded to ±dt/2.  This lets each channel achieve
        maximum phase accuracy independently of the trigger channel's phase.

        Returns (epoch_idxs, epoch_times, t_display) or (None, None, t_pos)
        on failure (falls back to trigger-channel epochs in the caller).
        """
        if len(t_arr) < 2:
            return None, None, t_pos
        dt = float(t_arr[1] - t_arr[0])
        T  = trace.period_estimate
        if T <= 0 or dt <= 0 or trace.period_confidence < 0.3:
            return None, None, t_pos

        T_float    = T / dt   # fractional samples per period
        mean_level = float(np.mean(y_arr))

        # Find all rising zero-crossings of this trace, pick the one closest
        # to the trigger position as the per-trace anchor
        raw_idxs = find_all_triggers(y_arr, t_arr, mean_level, 0,
                                     holdoff_samples=0)
        if not raw_idxs:
            return None, None, t_pos

        t_cross = np.array([float(t_arr[i]) for i in raw_idxs])
        closest = int(np.argmin(np.abs(t_cross - t_pos)))
        anchor  = raw_idxs[closest]

        # Walk using floating-point steps so each epoch index is independently
        # rounded — bounds phase error to ±dt/2 regardless of distance from anchor.
        n_total = len(y_arr)
        epoch_idxs: list = []
        k = 0
        while True:
            ep = int(round(anchor + k * T_float))
            if ep >= n_total:
                break
            epoch_idxs.append(ep)
            k += 1
        k = -1
        while True:
            ep = int(round(anchor + k * T_float))
            if ep < 0:
                break
            epoch_idxs.append(ep)
            k -= 1
        epoch_idxs.sort()

        if not epoch_idxs:
            return None, None, t_pos

        epoch_times = [float(t_arr[i]) for i in epoch_idxs]
        t_arr_e = np.fromiter(epoch_times, dtype=float)
        t_display = float(t_arr_e[np.argmin(np.abs(t_arr_e - t_pos))])
        return epoch_idxs, epoch_times, t_display

    def _apply_retrigger_render(
            self,
            t_pos: float,
            idxs: list,
            t_trigs: list,
            trig_t,
            seg_span: float,
            x0: float,
            x1: float,
    ):
        """
        Shared rendering path for both epoch-based and holdoff-based epochs.

        Averaging   — all traces use the trigger channel's epoch list (idxs /
                      t_trigs).  Non-coherent signals average away.
        Interpolation — the trigger trace uses idxs / t_trigs; every other
                      trace independently computes its own period-based epochs
                      so each channel achieves maximum sub-sample resolution
                      without being constrained by the trigger channel's phase.
        """
        self._last_trigger_t_pos  = t_pos
        # Store the actual view span (not the inflated seg_span) so the 20 %
        # change-detection threshold in _on_view_changed_retrigger is compared
        # against a consistent baseline.  Storing seg_span here caused false
        # positives because new_span (= view_span) was always < old_span (= seg_span).
        self._last_retrigger_span = x1 - x0
        self._plot.clear_persistence_layers()
        self._plot.clear_retrigger_curve()
        self._last_retrigger_results.clear()
        # Clear extrapolation flags before re-rendering so stale badges
        # don't persist if a trace produces no output this cycle.
        for _t in self._traces:
            _t.retrigger_extrapolating = False

        trig_ch = self._trigger_panel.combo_ch.currentText()

        # Adaptive count cap: limit ghost count when zoomed far out.
        mask_v = (trig_t >= x0) & (trig_t <= x1)
        visible_s = max(1, int(mask_v.sum()))
        zoom_cap = max(1, len(trig_t) // visible_s)
        effective_persist = dict(self._persist_settings)
        effective_persist["count"] = min(
            self._persist_settings.get("count", 10), zoom_cap)

        for trace in self._traces:
            if not trace.visible:
                continue
            t = trace.time_axis
            y = trace.processed_data
            if len(t) < 2:
                continue

            # In interpolation mode, non-trigger traces find their own
            # period-based epochs so each channel phase-matches independently.
            # In averaging mode, all traces share the trigger channel's epochs
            # so that non-coherent signals average away (the desired behaviour).
            use_idxs    = idxs
            use_trigs   = t_trigs
            use_display = t_pos
            if (self._retrigger_mode == MODE_INTERPOLATION
                    and trace.name != trig_ch):
                tr_i, tr_t, tr_d = self._compute_trace_epochs(
                    trace, t, y, t_pos)
                if tr_i is not None:
                    use_idxs    = tr_i
                    use_trigs   = tr_t
                    use_display = tr_d

            result = retrigger_apply_with_triggers(
                mode=self._retrigger_mode,
                time=t,
                data=y,
                trigger_indices=use_idxs,
                trigger_times=use_trigs,
                view_span=seg_span,
                persistence_settings=effective_persist,
                averaging_settings=self._averaging_settings,
                interpolation_settings=self._interpolation_settings,
            )
            self._last_retrigger_results[trace.name] = result
            self._render_retrigger(trace.name, result, use_display)

        # Repaint channel status blocks so EXTRAP badges reflect current state.
        if hasattr(self, '_scope_status'):
            self._scope_status.repaint_channel_blocks()

    def _render_retrigger(self, trace_name: str, result, t_ref: float):
        """Dispatch a RetriggerResult to the appropriate plot calls."""
        mode = result.mode
        trace_obj = next((t for t in self._traces if t.name == trace_name), None)

        def _apply_extrap(abs_time: np.ndarray, data: np.ndarray):
            """Detect extrapolation; clip or warn per _retrigger_extrap_mode.
            Returns (abs_time, data, is_extrapolating)."""
            if trace_obj is None or len(abs_time) == 0:
                return abs_time, data, False
            t_lo = float(trace_obj.time_axis[0])
            t_hi = float(trace_obj.time_axis[-1])
            is_extrap = (abs_time[0] < t_lo - 1e-12) or (abs_time[-1] > t_hi + 1e-12)
            if is_extrap and self._retrigger_extrap_mode == "clip":
                mask = (abs_time >= t_lo) & (abs_time <= t_hi)
                if mask.any():
                    abs_time, data = abs_time[mask], data[mask]
                return abs_time, data, False   # clipped → no longer extrapolating
            return abs_time, data, is_extrap

        if mode in PERSIST_MODES:
            if result.layers and trace_obj is not None:
                t_lo = float(trace_obj.time_axis[0])
                t_hi = float(trace_obj.time_axis[-1])
                is_extrap = any(
                    len(lyr.time) > 0 and (
                        (lyr.time[0]  + t_ref) < t_lo - 1e-12 or
                        (lyr.time[-1] + t_ref) > t_hi + 1e-12
                    )
                    for lyr in result.layers
                )
                if is_extrap and self._retrigger_extrap_mode == "clip":
                    clipped = []
                    for lyr in result.layers:
                        abs_t = lyr.time + t_ref
                        mask  = (abs_t >= t_lo) & (abs_t <= t_hi)
                        if mask.any():
                            clipped.append(PersistenceLayer(
                                time=lyr.time[mask],
                                data=lyr.data[mask],
                                opacity=lyr.opacity,
                                width_multiplier=lyr.width_multiplier,
                                z_order=lyr.z_order,
                                is_emphasis=lyr.is_emphasis,
                            ))
                    self._plot.set_persistence_layers(
                        trace_name, clipped, t_ref)
                    is_extrap = False
                else:
                    self._plot.set_persistence_layers(
                        trace_name, result.layers, t_ref)
                trace_obj.retrigger_extrapolating = is_extrap
            elif result.layers:
                self._plot.set_persistence_layers(trace_name, result.layers, t_ref)
            else:
                self._plot.clear_persistence_layers(trace_name)
                if trace_obj:
                    trace_obj.retrigger_extrapolating = False
            self._plot.clear_retrigger_curve(trace_name)
        elif mode == MODE_AVERAGING:
            self._plot.clear_persistence_layers(trace_name)
            if result.avg_time is not None and result.avg_data is not None:
                abs_t, abs_d, is_extrap = _apply_extrap(
                    result.avg_time + t_ref, result.avg_data)
                if trace_obj:
                    trace_obj.retrigger_extrapolating = is_extrap
                self._plot.set_retrigger_curve(
                    trace_name, abs_t, abs_d,
                    **self._retrigger_display_kwargs(self._averaging_settings))
            else:
                self._plot.clear_retrigger_curve(trace_name)
                if trace_obj:
                    trace_obj.retrigger_extrapolating = False
        elif mode == MODE_INTERPOLATION:
            self._plot.clear_persistence_layers(trace_name)
            if result.interp_time is not None and result.interp_data is not None:
                abs_t, abs_d, is_extrap = _apply_extrap(
                    result.interp_time + t_ref, result.interp_data)
                if trace_obj:
                    trace_obj.retrigger_extrapolating = is_extrap
                self._plot.set_retrigger_curve(
                    trace_name, abs_t, abs_d,
                    **self._retrigger_display_kwargs(self._interpolation_settings))
            else:
                self._plot.clear_retrigger_curve(trace_name)
                if trace_obj:
                    trace_obj.retrigger_extrapolating = False
        else:
            self._plot.clear_persistence_layers(trace_name)
            self._plot.clear_retrigger_curve(trace_name)
            if trace_obj:
                trace_obj.retrigger_extrapolating = False

    def _retrigger_display_kwargs(self, mode_settings: dict) -> dict:
        """Build keyword args for set_retrigger_curve from current display settings."""
        cfg = self._dashed_line_config
        dash_pattern = [float(cfg.get("dash", 8)), float(cfg.get("space", 4))]
        return dict(
            original_display=mode_settings.get("original_display", "dimmed"),
            dimmed_opacity=self._original_dimmed_opacity,
            dash_pattern=dash_pattern,
        )

    def _reapply_retrigger(self):
        """Re-run the pipeline with the last known trigger position.
        If no trigger has been found yet, auto-detects the first one."""
        if self._retrigger_mode == MODE_OFF:
            return
        t_pos = self._last_trigger_t_pos
        if t_pos is None:
            t_pos = self._auto_find_trigger()
            if t_pos is None:
                return
            self._last_trigger_t_pos = t_pos
        self._apply_retrigger(t_pos)

    def _auto_find_trigger(self) -> Optional[float]:
        """
        Find the first trigger crossing in the current view window using the
        trigger panel's current level/edge/channel settings.
        Falls back to searching the full trace if no crossing is visible.
        Returns the sub-sample accurate trigger time, or None.
        """
        trig_trace = self._trigger_panel._get_selected_trace()
        if trig_trace is None:
            return None
        level    = self._trigger_panel.edit_level.get_value(0.0)
        edge_idx = self._trigger_panel.combo_edge.currentIndex()
        t_full = trig_trace.time_axis
        y_full = trig_trace.processed_data
        if len(t_full) < 2:
            return None

        # Try the visible window first — gives the most relevant trigger
        x0, x1 = self._plot.get_current_view_range()
        mask = (t_full >= x0) & (t_full <= x1)
        t_v, y_v = t_full[mask], y_full[mask]
        if len(t_v) >= 2:
            _, t_trigs = find_all_triggers_with_times(y_v, t_v, level, edge_idx, 0)
            if t_trigs:
                return t_trigs[0]

        # Fall back to the full trace
        _, t_trigs = find_all_triggers_with_times(y_full, t_full, level, edge_idx, 0)
        return t_trigs[0] if t_trigs else None

    def _update_retrigger_controls(self):
        """Enable/disable the manual Update Retrigger button."""
        active = self._retrigger_mode != MODE_OFF
        self._trigger_panel.btn_retrigger_update.setEnabled(active)

    def _on_view_changed_retrigger(self):
        """
        Called (throttled, ~100 ms) whenever the user pans or zooms.
        Recalculates persistence when:
          - the view span changed by more than 20 % (zoom in or out), OR
          - the current trigger reference has scrolled out of the visible window.
        Finding a new trigger near the centre of the view keeps the display
        consistent as the user scrolls through the data.
        """
        if self._retrigger_mode == MODE_OFF or not self._traces:
            return
        if not self._trigger_panel.chk_auto_retrigger.isChecked():
            return
        x0, x1 = self._plot.get_current_view_range()
        new_span = x1 - x0

        old_span = self._last_retrigger_span
        span_changed = (
            old_span <= 0
            or abs(new_span - old_span) / max(old_span, 1e-12) > 0.20
        )
        t_pos = self._last_trigger_t_pos
        pos_offscreen = (t_pos is None) or not (x0 <= t_pos <= x1)

        if span_changed or pos_offscreen:
            new_t = self._auto_find_trigger()
            if new_t is not None:
                self._last_trigger_t_pos  = new_t
                self._last_retrigger_span = new_span
                self._apply_retrigger(new_t)

    # ── Retrigger settings dialogs ─────────────────────────────────────

    def _dlg_persist_count(self):
        val, ok = QInputDialog.getInt(
            self, "Persistence Count",
            "Maximum number of trigger-aligned sweeps to overlay:",
            self._persist_settings.get("count", 20), 2, 1000, 1)
        if ok:
            self._persist_settings["count"] = val
            self._reapply_retrigger()

    def _dlg_persist_selection(self):
        items = ["first", "last"]
        cur = self._persist_settings.get("selection", "first")
        idx = items.index(cur) if cur in items else 0
        val, ok = QInputDialog.getItem(
            self, "Persistence Selection",
            "Which triggers to keep (first N or last N):",
            items, idx, False)
        if ok:
            self._persist_settings["selection"] = val
            self._reapply_retrigger()

    def _dlg_persist_emphasis(self):
        items = ["first", "last"]
        cur = self._persist_settings.get("emphasis", "first")
        idx = items.index(cur) if cur in items else 0
        val, ok = QInputDialog.getItem(
            self, "Persistence Emphasis",
            "Which sweep is drawn as the hard line on top:",
            items, idx, False)
        if ok:
            self._persist_settings["emphasis"] = val
            self._reapply_retrigger()

    def _dlg_persist_opacity(self):
        val, ok = QInputDialog.getDouble(
            self, "Opacity Decay",
            "Opacity multiplier per step back from emphasis (0.01 – 0.99):\n"
            "Lower = faster fade.   0.9 = gentle fade.",
            self._persist_settings.get("opacity_decay", 0.9), 0.01, 0.99, 2)
        if ok:
            self._persist_settings["opacity_decay"] = val
            self._reapply_retrigger()

    def _dlg_persist_width(self):
        val, ok = QInputDialog.getDouble(
            self, "Width Growth",
            "Line-width multiplier per step back from emphasis (1.0 – 5.0):\n"
            "Higher = older sweeps drawn thicker.   1.0 = all same width.",
            self._persist_settings.get("width_growth", 1.1), 1.0, 5.0, 2)
        if ok:
            self._persist_settings["width_growth"] = val
            self._reapply_retrigger()

    def _reset_persist_defaults(self):
        self._persist_settings = dict(PERSISTENCE_DEFAULTS)
        self._reapply_retrigger()

    def _build_original_display_menu(self, menu, settings_dict: dict,
                                      on_change) -> dict:
        """Add Show Dimmed / Show Dashed / Don't Show radio actions to *menu*.
        Returns a dict of the three QActions so callers can update check state."""
        from PyQt6.QtGui import QActionGroup
        grp = QActionGroup(menu)
        grp.setExclusive(True)
        current = settings_dict.get("original_display", "dimmed")
        actions = {}
        for key, label in [("dimmed", "Show Dimmed"),
                            ("dashed", "Show Dashed"),
                            ("hide",   "Don't Show")]:
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(key == current)
            grp.addAction(act)
            def _make_cb(k, d, cb):
                def _cb(checked):
                    if checked:
                        d["original_display"] = k
                        cb()
                return _cb
            act.toggled.connect(_make_cb(key, settings_dict, on_change))
            actions[key] = act
        return actions

    def _dlg_dimmed_opacity(self):
        from PyQt6.QtWidgets import QInputDialog
        val, ok = QInputDialog.getDouble(
            self, "Dimmed Opacity",
            "Opacity of the original trace when 'Show Dimmed' is active\n"
            "(10 % – 90 %):",
            self._original_dimmed_opacity * 100, 10.0, 90.0, 0)
        if ok:
            self._original_dimmed_opacity = max(0.1, min(0.9, val / 100.0))
            self._reapply_retrigger()

    def _dlg_dashed_line_config(self):
        from PyQt6.QtWidgets import QDialog, QFormLayout, QSpinBox, QDialogButtonBox
        dlg = QDialog(self)
        dlg.setWindowTitle("Dashed Line Config")
        form = QFormLayout(dlg)
        sb_dash = QSpinBox(); sb_dash.setRange(1, 64)
        sb_dash.setValue(int(self._dashed_line_config.get("dash", 8)))
        sb_dash.setSuffix(" px")
        sb_space = QSpinBox(); sb_space.setRange(1, 64)
        sb_space.setValue(int(self._dashed_line_config.get("space", 4)))
        sb_space.setSuffix(" px")
        form.addRow("Dash length:", sb_dash)
        form.addRow("Space length:", sb_space)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec():
            self._dashed_line_config = {"dash": sb_dash.value(),
                                         "space": sb_space.value()}
            self._reapply_retrigger()

    def _dlg_avg_count(self):
        val, ok = QInputDialog.getInt(
            self, "Averaging Count",
            "Number of trigger-aligned segments to average:",
            self._averaging_settings.get("count", 20), 2, 1000, 1)
        if ok:
            self._averaging_settings["count"] = val
            self._reapply_retrigger()

    def _reset_avg_defaults(self):
        self._averaging_settings = dict(AVERAGING_DEFAULTS)
        self._reapply_retrigger()

    def _dlg_interp_count(self):
        val, ok = QInputDialog.getInt(
            self, "Interpolation Count",
            "Number of trigger-aligned segments to interleave:",
            self._interpolation_settings.get("count", 20), 2, 1000, 1)
        if ok:
            self._interpolation_settings["count"] = val
            self._reapply_retrigger()

    def _reset_interp_defaults(self):
        self._interpolation_settings = dict(INTERPOLATION_DEFAULTS)
        self._reapply_retrigger()

    # ── Trace labels ───────────────────────────────────────────────────

    def _add_label_at_cursor(self):
        """Add a text label to one trace at the current Cursor A position."""
        if not self._traces:
            return
        t_pos = self._plot._cursors.get(0)
        if t_pos is None:
            QMessageBox.information(self, "Add Label",
                "Place Cursor A first, then add a label.")
            return

        # Pick trace
        from PyQt6.QtWidgets import QInputDialog
        names = [t.label for t in self._traces if t.visible]
        if not names:
            return
        trace_label, ok = QInputDialog.getItem(
            self, "Add Label", "Trace:", names, 0, False)
        if not ok:
            return
        text, ok2 = QInputDialog.getText(
            self, "Add Label", f"Label text at t={t_pos:.6g} s:")
        if not ok2 or not text.strip():
            return

        # Find trace and add label
        for trace in self._traces:
            if trace.label == trace_label:
                trace.trace_labels.append((t_pos, text.strip()))
                break
        self._plot.refresh_all()

    def _clear_all_labels(self):
        for trace in self._traces:
            trace.trace_labels.clear()
        self._plot.refresh_all()
        self._status_lbl.setText("All trace labels cleared.")

    def _rebuild_theme_menu(self):
        """Populate Theme submenu from discovered theme files."""
        self._theme_submenu.clear()
        active_id = self.theme.theme_name
        ag = QActionGroup(self)
        ag.setExclusive(True)
        for file_id, td in self.theme.available_themes.items():
            a = QAction(td.name, self)
            a.setCheckable(True)
            a.setChecked(file_id == active_id)
            if td.tooltip:
                a.setToolTip(td.tooltip)
            a.triggered.connect(
                lambda checked, fid=file_id: self._set_theme(fid))
            ag.addAction(a)
            self._theme_submenu.addAction(a)

    def _open_theme_editor(self):
        def on_apply(file_id):
            self._set_theme(file_id)
        self.theme.open_editor(parent=self, on_apply=on_apply)

    def _reload_themes(self):
        self.theme.discover()
        self._rebuild_theme_menu()
        self._status_lbl.setText(
            f"Themes reloaded: {len(self.theme.available_themes)} found.")

    def eventFilter(self, _obj, event):
        """App-level event filter — intercepts arrow keys globally so they work
        regardless of which child widget (pyqtgraph ViewBox etc.) has focus.

        Only fires when this window is the active top-level window and no text-
        entry widget has focus (so typing in QLineEdit / QSpinBox is unaffected)."""
        from PyQt6.QtCore import QEvent as _QEvent
        from PyQt6.QtWidgets import (QApplication as _QApp,
                                      QLineEdit, QTextEdit,
                                      QAbstractSpinBox, QAbstractItemView)
        if event.type() != _QEvent.Type.KeyPress:
            return False
        # Only handle when this window is the active window
        if not self.isActiveWindow():
            return False
        # Don't steal keys from text-entry or list widgets
        fw = _QApp.instance().focusWidget()
        if isinstance(fw, (QLineEdit, QTextEdit, QAbstractSpinBox,
                           QAbstractItemView)):
            return False

        key  = event.key()
        mode = self._adv_ui.get("arrow_mode", "pan_time")

        if mode == "pan_time":
            if key == Qt.Key.Key_Left:
                self._plot.pan_x(-0.1); return True
            if key == Qt.Key.Key_Right:
                self._plot.pan_x(0.1);  return True
            if key == Qt.Key.Key_Up:
                sb = self._plot._scroll.verticalScrollBar()
                sb.setValue(sb.value() - 80); return True
            if key == Qt.Key.Key_Down:
                sb = self._plot._scroll.verticalScrollBar()
                sb.setValue(sb.value() + 80); return True

        elif mode == "scroll_statusbar":
            if key == Qt.Key.Key_Left:
                self._statusbar_snap(-1); return True
            if key == Qt.Key.Key_Right:
                self._statusbar_snap(+1); return True

        return False

    def closeEvent(self, event):
        self._save_settings()
        event.accept()
