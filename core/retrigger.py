"""
core/retrigger.py
Multi-trigger segment extraction: persistence, averaging, interpolation.

Data flow (no Qt — pure numpy):
  1. find_all_triggers()       -> List[int]   (sample indices)
  2. extract_segments()        -> List[RetriggerSegment]
  3. select_segments()         -> List[RetriggerSegment]  (count / selection)
  4. apply_mode()              -> RetriggerResult          (full pipeline)

Rendering is handled by the caller (scope_plot_widget).

Extension points
----------------
Future modes (heatmap density, eye-diagram, jitter) can extend
RetriggerResult with new fields and add an apply_* function routed
through apply_mode().  No magic numbers live here — all tunable
values come from the settings dicts whose defaults are exported.
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


# ── Mode identifiers ───────────────────────────────────────────────────────────

MODE_OFF            = "off"
MODE_PERSIST_FUTURE = "future"       # first trigger → hard line, history fades forward
MODE_PERSIST_PAST   = "past"         # last trigger  → hard line, history fades back
MODE_AVERAGING      = "averaging"
MODE_INTERPOLATION  = "interpolation"

PERSIST_MODES = frozenset({MODE_PERSIST_FUTURE, MODE_PERSIST_PAST})
ALL_MODES     = (MODE_OFF, MODE_PERSIST_FUTURE, MODE_PERSIST_PAST,
                 MODE_AVERAGING, MODE_INTERPOLATION)


# ── Default settings (exported so callers can reset to them) ───────────────────

PERSISTENCE_DEFAULTS: dict = {
    "count":         10,       # max segments to overlay
    "selection":     "first",  # "first" | "last"  — which N triggers to use
    "emphasis":      "first",  # "first" | "last"  — which one gets the hard line
    "opacity_decay": 0.7,      # 0 < x < 1 — opacity multiplier per step back
    "width_growth":  1.35,     # x >= 1   — line-width multiplier per step back
}

AVERAGING_DEFAULTS: dict = {
    "count": 20,
    "original_display": "dimmed",   # "dimmed" | "dashed" | "hide"
}

INTERPOLATION_DEFAULTS: dict = {
    "count": 20,
    "original_display": "dimmed",   # "dimmed" | "dashed" | "hide"
}


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class RetriggerSegment:
    """One trigger-aligned data window extracted from a trace."""
    time: np.ndarray        # time relative to trigger (trigger = 0 s)
    data: np.ndarray        # sample values
    trigger_idx: int        # sample index of crossing in original trace
    trigger_time: float     # absolute time of crossing (for re-shifting)


@dataclass
class PersistenceLayer:
    """
    One rendered sweep in a persistence display.
    Layers are sorted by z_order (lowest = drawn first = background).

    Callers multiply their trace's base pen width by width_multiplier and
    set alpha from opacity to produce the final pen for each layer.
    """
    time: np.ndarray
    data: np.ndarray
    opacity: float          # 0.0 – 1.0
    width_multiplier: float # relative to base trace width (1.0 = normal)
    z_order: int            # 0 = back; higher = closer to front
    is_emphasis: bool       # True = the hard, fully-opaque sweep


@dataclass
class RetriggerResult:
    """
    Output of apply_mode().
    Callers inspect .mode to determine which fields are populated.

    Future modes append new optional fields here and add an apply_*
    function; apply_mode() routes to them without changing its signature.
    """
    mode: str
    segments: List[RetriggerSegment] = field(default_factory=list)

    # Persistence
    layers: List[PersistenceLayer] = field(default_factory=list)

    # Averaging
    avg_time: Optional[np.ndarray] = None
    avg_data: Optional[np.ndarray] = None

    # Sub-sample interleaved interpolation
    interp_time: Optional[np.ndarray] = None
    interp_data: Optional[np.ndarray] = None

    # Diagnostics
    n_triggers_found: int = 0
    n_segments_used:  int = 0


# ── Trigger finding ────────────────────────────────────────────────────────────

def find_all_triggers(
        data: np.ndarray,
        time: np.ndarray,
        level: float,
        edge_idx: int,          # 0 = rising, 1 = falling, 2 = either
        holdoff_samples: int = 0,
) -> List[int]:
    """
    Find every threshold crossing using the same definition as TriggerPanel:
      Rising:  y[i] strictly < level  AND  y[i+1] >= level
      Falling: y[i] strictly > level  AND  y[i+1] <= level

    holdoff_samples prevents re-triggering within that many samples of the
    previous trigger (use 0 for no holdoff).

    Returns a list of sample indices i where the crossing occurs between
    sample i and sample i+1.
    """
    if len(data) < 2:
        return []

    y = np.asarray(data, dtype=float)
    indices: List[int] = []
    i = 0
    n = len(y) - 1

    while i < n:
        a, b = y[i], y[i + 1]
        rising  = (a < level) and (b >= level)
        falling = (a > level) and (b <= level)

        if edge_idx == 0:
            hit = rising
        elif edge_idx == 1:
            hit = falling
        else:
            hit = rising or falling

        if hit:
            indices.append(i)
            i += max(1, holdoff_samples)
        else:
            i += 1

    return indices


def _trigger_time_at(time: np.ndarray, data: np.ndarray,
                     idx: int, level: float) -> float:
    """Sub-sample accurate trigger time via linear interpolation."""
    if idx + 1 >= len(time):
        return float(time[idx])
    a, b = float(data[idx]), float(data[idx + 1])
    denom = b - a
    frac = (level - a) / denom if denom != 0 else 0.0
    return float(time[idx]) + frac * (float(time[idx + 1]) - float(time[idx]))


# ── Segment extraction ─────────────────────────────────────────────────────────

def extract_segments(
        time: np.ndarray,
        data: np.ndarray,
        trigger_indices: List[int],
        trigger_times: List[float],
        half_span: float,
) -> List[RetriggerSegment]:
    """
    Extract fixed-width windows centred on each trigger crossing.
    The time array in each segment is relative to the trigger (trigger = 0).
    Windows that do not fit fully within the data extents are discarded.
    """
    segments: List[RetriggerSegment] = []
    t_lo, t_hi = float(time[0]), float(time[-1])

    for idx, t_trig in zip(trigger_indices, trigger_times):
        win_lo = t_trig - half_span
        win_hi = t_trig + half_span
        if win_lo < t_lo or win_hi > t_hi:
            continue

        mask = (time >= win_lo) & (time <= win_hi)
        t_seg = time[mask] - t_trig
        d_seg = data[mask]

        if len(t_seg) < 2:
            continue

        segments.append(RetriggerSegment(
            time=t_seg,
            data=d_seg,
            trigger_idx=idx,
            trigger_time=t_trig,
        ))

    return segments


# ── Selection ─────────────────────────────────────────────────────────────────

def select_segments(
        segments: List[RetriggerSegment],
        count: int,
        selection: str,         # "first" | "last"
) -> List[RetriggerSegment]:
    """Return up to count segments from the front or back of the list."""
    if not segments:
        return []
    n = min(max(1, count), len(segments))
    return segments[:n] if selection == "first" else segments[-n:]


# ── Persistence layers ─────────────────────────────────────────────────────────

def build_persistence_layers(
        segments: List[RetriggerSegment],
        emphasis: str,          # "first" | "last"
        opacity_decay: float,
        width_growth: float,
) -> List[PersistenceLayer]:
    """
    Convert a selected segment list into persistence render layers.

    emphasis="first"  (Future Persist):
        segments[0]   → hard line, drawn on top  (z = n-1)
        segments[k>0] → increasingly faded, drawn below

    emphasis="last"   (Past Persist Normal):
        segments[-1]  → hard line, drawn on top  (z = n-1)
        segments[k<-1]→ increasingly faded, drawn below

    Returned list is sorted by z_order ascending (draw back-to-front).
    """
    if not segments:
        return []

    n = len(segments)
    layers: List[PersistenceLayer] = []

    for i, seg in enumerate(segments):
        if emphasis == "first":
            decay   = i
            z       = n - 1 - i     # segments[0] → z = n-1 (top)
            is_em   = (i == 0)
        else:                        # "last"
            decay   = n - 1 - i
            z       = i              # segments[-1] → z = n-1 (top)
            is_em   = (i == n - 1)

        layers.append(PersistenceLayer(
            time=seg.time,
            data=seg.data,
            opacity=float(opacity_decay ** decay),
            width_multiplier=float(width_growth ** decay),
            z_order=z,
            is_emphasis=is_em,
        ))

    layers.sort(key=lambda l: l.z_order)
    return layers


# ── Averaging ─────────────────────────────────────────────────────────────────

def compute_average(
        segments: List[RetriggerSegment],
) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """
    Average all segments onto the shortest segment's time grid.
    Returns (time, averaged_data) or (None, None) on failure.
    """
    if not segments:
        return None, None
    if len(segments) == 1:
        return segments[0].time.copy(), segments[0].data.copy()

    ref = min(segments, key=lambda s: len(s.time))
    t_ref = ref.time

    stacked = []
    for seg in segments:
        if len(seg.time) >= len(t_ref):
            stacked.append(np.interp(t_ref, seg.time, seg.data))

    if not stacked:
        return None, None

    return t_ref.copy(), np.mean(np.stack(stacked, axis=0), axis=0)


# ── Sub-sample interleaved interpolation ──────────────────────────────────────

def compute_interpolated(
        segments: List[RetriggerSegment],
) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """
    Sub-sample interleaving across trigger-aligned segments.

    Each segment has a slightly different fractional sample-phase offset
    because the trigger crossing falls between two samples at a unique
    fraction each time.  Merging all segments' time points gives a denser
    effective time grid; averaging overlapping points reduces noise.
    """
    if not segments:
        return None, None
    if len(segments) == 1:
        return segments[0].time.copy(), segments[0].data.copy()

    t_lo = max(float(s.time[0])  for s in segments)
    t_hi = min(float(s.time[-1]) for s in segments)
    if t_lo >= t_hi:
        return None, None

    all_t = np.unique(np.concatenate([s.time for s in segments]))
    mask  = (all_t >= t_lo) & (all_t <= t_hi)
    all_t = all_t[mask]
    if len(all_t) < 2:
        return None, None

    stacked = []
    for seg in segments:
        if seg.time[0] <= t_lo and seg.time[-1] >= t_hi:
            stacked.append(np.interp(all_t, seg.time, seg.data))

    if not stacked:
        return None, None

    return all_t.copy(), np.mean(np.stack(stacked, axis=0), axis=0)


# ── Top-level pipeline ────────────────────────────────────────────────────────

def apply_mode(
        mode: str,
        time: np.ndarray,
        data: np.ndarray,
        level: float,
        edge_idx: int,
        view_span: float,
        persistence_settings: dict,
        averaging_settings: dict,
        interpolation_settings: dict,
        holdoff_samples: int = 0,
) -> RetriggerResult:
    """
    Full pipeline: find triggers → extract → select → apply mode.
    Returns a RetriggerResult; caller handles rendering.
    view_span is the current plot window width in seconds; segments are
    extracted as ±½ view_span windows centred on each trigger.
    """
    result = RetriggerResult(mode=mode)

    if mode == MODE_OFF or len(time) < 2:
        return result

    idxs = find_all_triggers(data, time, level, edge_idx, holdoff_samples)
    result.n_triggers_found = len(idxs)
    if not idxs:
        return result

    t_trigs = [_trigger_time_at(time, data, i, level) for i in idxs]
    half    = max(view_span / 2.0, 0.0)
    if half <= 0:
        return result

    segs = extract_segments(time, data, idxs, t_trigs, half)
    result.segments = segs
    if not segs:
        return result

    if mode in PERSIST_MODES:
        p   = persistence_settings
        sel = select_segments(segs, p.get("count", 20), p.get("selection", "first"))
        result.n_segments_used = len(sel)
        result.layers = build_persistence_layers(
            sel,
            p.get("emphasis",      "first"),
            p.get("opacity_decay", 0.9),
            p.get("width_growth",  1.1),
        )

    elif mode == MODE_AVERAGING:
        a   = averaging_settings
        sel = select_segments(segs, a.get("count", 20), "first")
        result.n_segments_used = len(sel)
        result.avg_time, result.avg_data = compute_average(sel)

    elif mode == MODE_INTERPOLATION:
        ip  = interpolation_settings
        sel = select_segments(segs, ip.get("count", 20), "first")
        result.n_segments_used = len(sel)
        result.interp_time, result.interp_data = compute_interpolated(sel)

    return result


# ── Trigger-time helper (public) ──────────────────────────────────────────────

def find_all_triggers_with_times(
        data: np.ndarray,
        time: np.ndarray,
        level: float,
        edge_idx: int,
        holdoff_samples: int = 0,
) -> Tuple[List[int], List[float]]:
    """
    Find every trigger crossing and return both sample indices and sub-sample
    accurate times.  Convenience wrapper used when the caller needs trigger
    positions to apply to multiple data channels.
    """
    idxs    = find_all_triggers(data, time, level, edge_idx, holdoff_samples)
    t_trigs = [_trigger_time_at(time, data, i, level) for i in idxs]
    return idxs, t_trigs


# ── Cross-channel pipeline variant ───────────────────────────────────────────

def apply_mode_with_triggers(
        mode: str,
        time: np.ndarray,
        data: np.ndarray,
        trigger_indices: List[int],
        trigger_times: List[float],
        view_span: float,
        persistence_settings: dict,
        averaging_settings: dict,
        interpolation_settings: dict,
) -> RetriggerResult:
    """
    Pipeline variant that accepts pre-computed trigger positions instead of
    running the trigger-finder internally.

    Use this when triggers were detected on a dedicated trigger channel and
    the same events should be applied identically to every data channel —
    exactly how a real oscilloscope works.

    view_span is the actual viewport width; a 10 % margin is added internally
    so segments carry a little extra data on each side.
    """
    result = RetriggerResult(mode=mode)
    result.n_triggers_found = len(trigger_indices)

    if mode == MODE_OFF or len(time) < 2 or not trigger_indices:
        return result

    half = max(view_span / 2.0 * 1.1, 0.0)
    if half <= 0:
        return result

    segs = extract_segments(time, data, trigger_indices, trigger_times, half)
    result.segments = segs
    if not segs:
        return result

    if mode in PERSIST_MODES:
        p   = persistence_settings
        sel = select_segments(segs, p.get("count", 20), p.get("selection", "first"))
        result.n_segments_used = len(sel)
        result.layers = build_persistence_layers(
            sel,
            p.get("emphasis",      "first"),
            p.get("opacity_decay", 0.9),
            p.get("width_growth",  1.1),
        )

    elif mode == MODE_AVERAGING:
        a   = averaging_settings
        sel = select_segments(segs, a.get("count", 20), "first")
        result.n_segments_used = len(sel)
        result.avg_time, result.avg_data = compute_average(sel)

    elif mode == MODE_INTERPOLATION:
        ip  = interpolation_settings
        sel = select_segments(segs, ip.get("count", 20), "first")
        result.n_segments_used = len(sel)
        result.interp_time, result.interp_data = compute_interpolated(sel)

    return result
