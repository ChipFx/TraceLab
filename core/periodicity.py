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
'fast'          — zero-crossing rate  (O(N), fast, rough)
'zero_crossing' — sub-sample rising-edge intervals (O(N), good for clean signals)
'standard'      — FFT autocorrelation on windowed block, integer-lag peak (O(W log W))
'precise'       — FFT autocorrelation + parabolic sub-sample refinement (O(W log W))

For 'standard' and 'precise', long signals are WINDOWED (a centred contiguous
block of _MAX_FFT_SAMPLES points at the original sample rate), NOT subsampled.
Windowing preserves dt so short periods (high-frequency signals) stay detectable.
The tradeoff: periods longer than _MAX_FFT_SAMPLES/2 * dt are not seen by the
FFT methods — use 'fast' or 'zero_crossing' for those (they scan the full signal).

Scalability tiers (future work — currently all FFT methods share one window size):
  pretty-good  — _MAX_FFT_SAMPLES = 65536  (~10 Msample captures, ≤ 1 GSPS, ≤ 150 MHz)
  quality      — _MAX_FFT_SAMPLES = 262144 (~50 Msample captures, ≤ 5 GSPS, ≤ 500 MHz)
  elite        — _MAX_FFT_SAMPLES = 1M+    (40 GSPS / 250 Msample-class instruments)

Extension
---------
To add a new method, add a constant, add it to ALL_METHODS, and add an
elif branch in estimate_period() pointing to a private _estimate_* function.
"""

from __future__ import annotations

import numpy as np
from typing import Tuple


# ── Tier identifiers (user-facing estimation modes) ──────────────────────────

TIER_DISABLED   = "disabled"
TIER_ZERO_CROSS = "zero_crossing"
TIER_STANDARD   = "standard"
TIER_PRECISE    = "precise"
TIER_EXTREME    = "extreme"

ALL_TIERS = (
    TIER_DISABLED,
    TIER_ZERO_CROSS,
    TIER_STANDARD,
    TIER_PRECISE,
    TIER_EXTREME,
)

# FFT window sizes (contiguous samples at original dt, NOT subsampled).
# 10× steps so each tier detects 10× longer periods than the one below.
_WIN_STANDARD =    65_536   # ≤ 1 GSPS / ~150 MHz typical bench signals
_WIN_PRECISE  =   655_360   # ≤ 5 GSPS / ~500 MHz
_WIN_EXTREME  = 6_553_600   # 40 GSPS / 250 Msample-class, or n//4

TIER_LABELS = {
    TIER_DISABLED:   "Disabled",
    TIER_ZERO_CROSS: "Fast — Zero-crossing scan",
    TIER_STANDARD:   "Standard — FFT 64 k window",
    TIER_PRECISE:    "Precise — FFT 640 k window",
    TIER_EXTREME:    "Extreme — FFT up to 6.5 M samples",
}

TIER_TOOLTIPS = {
    TIER_DISABLED: (
        "Periodicity estimation is disabled.\n"
        "ERES / averaging tools cannot build epoch grids.\n"
        "No 'APERIODIC' badge will appear."
    ),
    TIER_ZERO_CROSS: (
        "Scans the entire signal for sub-sample rising zero-crossings\n"
        "and averages the intervals — O(N) time, works on any file size.\n"
        "Works well on clean signals with a clear zero crossing.\n"
        "Less reliable with heavy noise, strong DC offsets, or waveforms\n"
        "that rarely cross the signal mean.\n"
        "Detection range: 2 dt  …  full capture length."
    ),
    TIER_STANDARD: (
        "FFT autocorrelation on a 64 k-sample window at the original\n"
        "sample rate, with parabolic sub-sample refinement.\n"
        "Reliable for typical bench signals; completes in well under a\n"
        "second on any modern CPU.\n"
        "Recommended for: ≤ 1 GSPS captures, signals up to ~150 MHz.\n"
        "Detection range: 8 dt  …  32 k × dt  (half the window)."
    ),
    TIER_PRECISE: (
        "FFT autocorrelation on a 640 k-sample window — 10× Standard.\n"
        "Detects periods up to 10× longer at the same sample rate.\n"
        "Recommended for: ≤ 5 GSPS captures, up to ~500 MHz signals,\n"
        "or whenever Standard returns 'APERIODIC' unexpectedly.\n"
        "Compute cost: roughly 10× Standard (~1–3 s on large files)."
    ),
    TIER_EXTREME: (
        "FFT autocorrelation on max(6.5 M samples, ¼ of the full signal).\n"
        "For 40 GSPS / 250 Msample-class instruments, very low-frequency\n"
        "signals in long captures, or when Precise still shows 'APERIODIC'.\n"
        "Can take many seconds on large files — not suitable for live\n"
        "retrigger workflows.\n"
        "Detection range: 8 dt  …  window/2 × dt."
    ),
}


# ── Public entry point ────────────────────────────────────────────────────────

def estimate_period(
        samples: np.ndarray,
        dt: float,
        tier: str = TIER_STANDARD,
) -> Tuple[float, float]:
    """
    Estimate the dominant period of *samples* (uniformly sampled at interval
    *dt* seconds).

    Returns ``(T_seconds, confidence)`` where *confidence* is 0–1.
    Returns ``(0.0, 0.0)`` when *tier* is 'disabled', when the signal is too
    short, or when estimation fails (e.g. no clear periodicity).
    """
    if tier == TIER_DISABLED or dt <= 0:
        return 0.0, 0.0

    y = np.asarray(samples, dtype=float)
    n = len(y)
    if n < 8:
        return 0.0, 0.0

    if tier == TIER_ZERO_CROSS:
        return _estimate_zero_crossing(y, dt)

    if tier == TIER_PRECISE:
        max_fft = _WIN_PRECISE
    elif tier == TIER_EXTREME:
        max_fft = max(_WIN_EXTREME, n // 4)
    else:  # TIER_STANDARD or unrecognised → safe default
        max_fft = _WIN_STANDARD

    return _estimate_autocorr(y, dt, max_fft=max_fft)


# ── Method implementations ────────────────────────────────────────────────────


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
        max_fft: int = _WIN_STANDARD,
) -> Tuple[float, float]:
    """
    FFT-based autocorrelation → locate the first strong secondary peak,
    with parabolic sub-sample refinement.

    *max_fft* sets the window size (contiguous samples at the original
    sample rate).  Larger windows detect longer periods at the cost of
    proportionally more compute.

    Confidence is based on normalised peak prominence over the median of
    the search region: (peak − noise_floor) / (1 − noise_floor).
    """
    n = len(y)

    # ── Window to max_fft (centred block, original sample rate) ───────────
    # Windowing preserves dt so T_samples stays physically meaningful.
    # Subsampling would alias away short periods: a 150 MHz signal at
    # 1 GSPS has T_samples ≈ 6.7; after ×153 subsampling that becomes
    # < 1 — completely undetectable.
    # Tradeoff: periods longer than max_fft/2 × dt are not visible;
    # use TIER_ZERO_CROSS for those (O(N), scans the full signal).
    if n > max_fft:
        mid  = n // 2
        half = max_fft // 2
        y    = y[mid - half : mid + half]
        n    = len(y)
        # dt is unchanged

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
    # min_lag = 4: skip only the trivially-high lag-0 main lobe.
    # A constant floor is safe here because the ramp / monotone-autocorr
    # case is handled below by requiring a genuine local maximum (no local
    # maxima → aperiodic), so we don't need a large min_lag as a guard.
    # 4 samples covers any signal with T_samples > 8, which is roughly the
    # Nyquist limit anyway.
    # max_lag = n // 2: need at least two visible periods in the window.
    min_lag = 4
    max_lag = n // 2
    if max_lag <= min_lag:
        return 0.0, 0.0

    search = R[min_lag:max_lag]
    if len(search) < 3:
        return 0.0, 0.0

    # ── Find peak ──────────────────────────────────────────────────────────
    # Detect genuine local maxima using vectorised numpy comparisons.
    # This avoids the adjacent-sample floating-point hazard (FFT arithmetic
    # can make R[k±1] appear microscopically above R[k]) and also correctly
    # rejects monotonically-decreasing autocorrelations (ramp signals) which
    # have NO local maxima at all.
    k_all    = np.arange(min_lag, max_lag)
    is_local = (R[k_all - 1] < R[k_all]) & (R[k_all] >= R[k_all + 1])
    peak_lags = k_all[is_local]
    if len(peak_lags) == 0:
        return 0.0, 0.0

    # Select the strongest local maximum.  For a biased autocorrelation the
    # envelope decreases with lag, so the first-period peak wins.
    peak_lag   = int(peak_lags[np.argmax(R[peak_lags])])
    peak_local = peak_lag - min_lag
    peak_val   = float(R[peak_lag])

    noise = float(np.median(np.abs(search)))
    if peak_val <= 0 or peak_val <= noise:
        return 0.0, 0.0

    # ── Confidence ────────────────────────────────────────────────────────
    conf = float(np.clip(
        (peak_val - noise) / (1.0 - noise + 1e-9), 0.0, 1.0))

    # ── Sub-sample refinement (parabolic fit, always applied) ─────────────
    if 1 <= peak_local < len(search) - 1:
        y0 = R[peak_lag - 1]
        y1 = R[peak_lag]
        y2 = R[peak_lag + 1]
        denom = 2.0 * (y0 + y2 - 2.0 * y1)
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
