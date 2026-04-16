"""
core/periodicity.py
Signal period estimation: pure functions, no Qt, no side-effects.

Public API
----------
estimate_period(samples, dt, method) -> (T_seconds, confidence)
    T_seconds  : estimated period in seconds (0.0 if unknown / failed / 'none')
    confidence : 0.0 – 1.0  (0.0 = not estimated or estimation failed)

Methods (in increasing compute cost)
-------------------------------------
'none'          — skip estimation; returns (0.0, 0.0) immediately
'fast'          — zero-crossing rate  (O(N), ~microseconds, rough)
'zero_crossing' — sub-sample rising-edge intervals (O(N), good for clean signals)
'standard'      — FFT autocorrelation, integer-lag peak (O(N log N))
'precise'       — FFT autocorrelation + parabolic sub-sample refinement (O(N log N))

All methods subsample long signals to at most _MAX_FFT_SAMPLES points before
FFT work so the compute cost stays bounded regardless of recording length.

Extension
---------
To add a new method, add a constant, add it to ALL_METHODS, and add an
elif branch in estimate_period() pointing to a private _estimate_* function.
"""

from __future__ import annotations

import numpy as np
from typing import Tuple


# ── Method identifiers ────────────────────────────────────────────────────────

METHOD_NONE          = "none"
METHOD_FAST          = "fast"
METHOD_ZERO_CROSSING = "zero_crossing"
METHOD_STANDARD      = "standard"
METHOD_PRECISE       = "precise"

ALL_METHODS = (
    METHOD_NONE,
    METHOD_FAST,
    METHOD_ZERO_CROSSING,
    METHOD_STANDARD,
    METHOD_PRECISE,
)

METHOD_LABELS = {
    METHOD_NONE:          "None (disabled)",
    METHOD_FAST:          "Fast (zero-crossing rate)",
    METHOD_ZERO_CROSSING: "Zero Crossing (sub-sample)",
    METHOD_STANDARD:      "Standard (FFT autocorrelation)",
    METHOD_PRECISE:       "Precise (FFT + parabolic refinement)",
}

# Maximum number of samples used for FFT work — keeps cost bounded
_MAX_FFT_SAMPLES = 65536


# ── Public entry point ────────────────────────────────────────────────────────

def estimate_period(
        samples: np.ndarray,
        dt: float,
        method: str = METHOD_PRECISE,
) -> Tuple[float, float]:
    """
    Estimate the dominant period of *samples* (uniformly sampled at interval
    *dt* seconds).

    Returns ``(T_seconds, confidence)`` where *confidence* is 0–1.
    Returns ``(0.0, 0.0)`` when *method* is 'none', when the signal is too
    short, or when estimation fails (e.g. no clear periodicity).
    """
    if method == METHOD_NONE or dt <= 0:
        return 0.0, 0.0

    y = np.asarray(samples, dtype=float)
    n = len(y)
    if n < 8:
        return 0.0, 0.0

    if method == METHOD_FAST:
        return _estimate_fast(y, dt)
    if method == METHOD_ZERO_CROSSING:
        return _estimate_zero_crossing(y, dt)
    if method in (METHOD_STANDARD, METHOD_PRECISE):
        return _estimate_autocorr(y, dt, refine=(method == METHOD_PRECISE))

    return 0.0, 0.0


# ── Method implementations ────────────────────────────────────────────────────

def _estimate_fast(y: np.ndarray, dt: float) -> Tuple[float, float]:
    """
    Count zero crossings around the signal mean.
    Period ≈ 2 * N * dt / n_crossings.

    Very fast (single pass, O(N)) but unreliable for signals with DC drift,
    harmonics, or noise.  Returns low confidence scores (≤ 0.40).
    """
    mean = float(np.mean(y))
    c = y - mean

    # Count every crossing (both rising and falling)
    crossings = int(np.sum(
        ((c[:-1] < 0) & (c[1:] >= 0)) |
        ((c[:-1] > 0) & (c[1:] <= 0))
    ))
    if crossings < 2:
        return 0.0, 0.0

    T = 2.0 * len(y) * dt / crossings

    # Confidence grows with the number of observed cycles, capped at 0.40
    n_cycles = crossings / 2.0
    conf = min(0.40, 0.08 + 0.06 * n_cycles)
    return T, conf


def _estimate_zero_crossing(y: np.ndarray, dt: float) -> Tuple[float, float]:
    """
    Find sub-sample rising zero-crossing times via linear interpolation.
    Period = mean interval between consecutive rising crossings.
    Confidence falls as interval variance grows (high CV → low conf).

    Returns confidence up to 0.70.
    """
    mean = float(np.mean(y))
    c = y - mean

    # Rising crossings: c[i] < 0 and c[i+1] >= 0
    idx = np.where((c[:-1] < 0) & (c[1:] >= 0))[0]
    if len(idx) < 2:
        return 0.0, 0.0

    # Sub-sample crossing times
    t_cross = np.empty(len(idx), dtype=float)
    for k, i in enumerate(idx):
        a, b = c[i], c[i + 1]
        frac = (-a / (b - a)) if (b - a) != 0 else 0.0
        t_cross[k] = (i + frac) * dt

    intervals = np.diff(t_cross)
    if len(intervals) == 0:
        return 0.0, 0.0

    T = float(np.mean(intervals))
    if T <= 0:
        return 0.0, 0.0

    # Coefficient of variation: low = consistent = high confidence
    cv = float(np.std(intervals)) / T
    conf = float(np.clip(0.70 * np.exp(-3.0 * cv), 0.0, 0.70))
    # Scale down if we have very few complete cycles
    conf *= min(1.0, len(intervals) / 5.0)
    return T, conf


def _estimate_autocorr(
        y: np.ndarray,
        dt: float,
        refine: bool,
) -> Tuple[float, float]:
    """
    FFT-based autocorrelation → locate the first strong secondary peak.

    If *refine* is True (precise mode), the peak lag is refined to sub-sample
    accuracy with a three-point parabolic fit.

    Confidence is based on the normalised peak prominence over the median
    of the search region: (peak − noise_floor) / (1 − noise_floor).
    Precise mode caps at 1.0; standard mode caps at 0.85.
    """
    n = len(y)

    # ── Subsample to _MAX_FFT_SAMPLES ──────────────────────────────────────
    if n > _MAX_FFT_SAMPLES:
        step = int(np.ceil(n / _MAX_FFT_SAMPLES))
        y = y[::step]
        dt = dt * step
        n = len(y)

    # ── Pre-process: remove DC, normalise ──────────────────────────────────
    y = y - float(np.mean(y))
    std = float(np.std(y))
    if std < 1e-12:
        return 0.0, 0.0
    y = y / std

    # ── FFT autocorrelation (zero-padded for linear, not circular) ─────────
    Y = np.fft.rfft(y, n=2 * n)
    R = np.fft.irfft(Y * np.conj(Y))[:n]
    R /= R[0] + 1e-12   # normalise: R[0] = 1.0

    # ── Search window ──────────────────────────────────────────────────────
    # Minimum: skip the main lobe (~1/20 of length, at least 2 samples)
    # Maximum: half the record length (less than one full period in the window
    #          means we can't see its repetition)
    min_lag = max(2, n // 20)
    max_lag = n // 2
    if max_lag <= min_lag:
        return 0.0, 0.0

    search = R[min_lag:max_lag]
    if len(search) < 3:
        return 0.0, 0.0

    # ── Find peak ──────────────────────────────────────────────────────────
    peak_local = int(np.argmax(search))
    peak_lag   = peak_local + min_lag
    peak_val   = float(R[peak_lag])

    noise = float(np.median(np.abs(search)))
    if peak_val <= 0 or peak_val <= noise:
        return 0.0, 0.0

    # ── T/2 disambiguation ─────────────────────────────────────────────────
    # For signals whose autocorrelation has a spurious peak at half the true
    # period (common with asymmetric noise or distorted waveforms), argmax
    # may land at T/2.  Check if doubling the candidate lag also yields a
    # significant positive peak — if so, 2*peak_lag is a better estimate.
    # We only promote if the doubled peak is clearly present (> 60 % of the
    # T/2 peak) so that we don't accidentally double a true short period.
    doubled_lag = peak_lag * 2
    if doubled_lag < max_lag:
        doubled_val = float(R[doubled_lag])
        if doubled_val > 0 and doubled_val >= peak_val * 0.60:
            peak_lag = doubled_lag
            peak_val = doubled_val
            peak_local = peak_lag - min_lag

    # ── Confidence ────────────────────────────────────────────────────────
    raw_conf = float(np.clip(
        (peak_val - noise) / (1.0 - noise + 1e-9), 0.0, 1.0))
    conf_cap = 1.0 if refine else 0.85
    conf = raw_conf * conf_cap

    # ── Sub-sample refinement (precise only) ──────────────────────────────
    if refine and 1 <= peak_local < len(search) - 1:
        y0 = R[peak_lag - 1]
        y1 = R[peak_lag]
        y2 = R[peak_lag + 1]
        denom = 2.0 * (2.0 * y1 - y0 - y2)
        if abs(denom) > 1e-12:
            frac = (y0 - y2) / denom
            peak_lag_f = float(peak_lag) + frac
        else:
            peak_lag_f = float(peak_lag)
    else:
        peak_lag_f = float(peak_lag)

    T = peak_lag_f * dt
    if T <= 0:
        return 0.0, 0.0

    return T, conf
