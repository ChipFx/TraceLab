"""
core/filter_engine.py
Filter computation engine — pure numpy/scipy, no Qt.

A FilterRecipe is a serializable description of one filter operation:
  - filter_type : "lowpass" | "highpass" | "bandpass" | "bandstop" |
                  "notch"   | "peak"     | "comb"
  - params      : type-specific dict
                    polynomial : cutoff_hz | (low_hz, high_hz), order, family
                    iir simple : center_hz, q
  - description : cached human-readable summary (auto-filled when empty)

The dispatch table maps each filter_type to a handler that returns SOS
coefficients for the given sample rate; the engine then applies them with
sosfiltfilt.  Add a new filter type = add an entry to _DISPATCH and a
describe_recipe branch.

The engine knows nothing about TraceModel, segments, or Qt.  Callers pass
plain 1-D float arrays and a sample rate and receive a filtered 1-D array.
"""

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional
import re

import numpy as np
from scipy import signal as sp_signal


# ── SI frequency helpers (shared by engine and dialog) ────────────────────────

_SI_PREFIXES = {
    'T': 1e12, 'G': 1e9, 'M': 1e6,
    'k': 1e3,  'K': 1e3,
    '':  1.0,
    'm': 1e-3,
    'u': 1e-6, 'µ': 1e-6, 'μ': 1e-6,
    'n': 1e-9, 'p': 1e-12, 'f': 1e-15,
}

_SI_PARSE_RE = re.compile(
    r'^\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)'
    r'\s*(T|G|M|k|K|m|u|µ|μ|n|p|f)?'
    r'\s*(?:[Hh][Zz])?\s*$'
)


def parse_si_freq(text: str) -> Optional[float]:
    """Parse a frequency string with optional SI prefix and Hz suffix.

    Accepts forms like '200u', '200uHz', '1.5kHz', '0.0002', '2M', '500nHz'.
    Returns Hz as float, or None if unparseable.
    """
    text = text.strip()
    if not text:
        return None
    m = _SI_PARSE_RE.match(text)
    if m:
        value = float(m.group(1))
        prefix = m.group(2) or ''
        return value * _SI_PREFIXES.get(prefix, 1.0)
    try:
        return float(text)
    except ValueError:
        return None


def format_si_freq(hz: float) -> str:
    """Format a frequency in Hz using the most readable SI prefix."""
    if hz <= 0:
        return f"{hz:g} Hz"
    for scale, prefix in [
        (1e12, 'T'), (1e9, 'G'), (1e6, 'M'), (1e3, 'k'),
        (1.0,  ''),  (1e-3, 'm'), (1e-6, 'µ'), (1e-9, 'n'),
        (1e-12, 'p'), (1e-15, 'f'),
    ]:
        if hz >= scale * 0.9995:
            val = hz / scale
            unit = f"{prefix}Hz" if prefix else "Hz"
            return f"{val:.4g} {unit}"
    return f"{hz:.4g} Hz"


# ── Recipe ────────────────────────────────────────────────────────────────────

@dataclass
class FilterRecipe:
    """One filter to be applied to a 1-D signal."""
    filter_type: str
    params:      dict = field(default_factory=dict)
    description: str  = ""    # auto-filled by describe_recipe() when blank

    def ensure_description(self):
        if not self.description:
            self.description = describe_recipe(self)
        return self.description


class FilterEngineError(ValueError):
    """Raised when a recipe cannot be applied (Nyquist violation, unknown
    filter type, malformed params, etc.).  Callers may catch and notify."""


# ── Description ───────────────────────────────────────────────────────────────

def _ord_suffix(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def describe_recipe(recipe: FilterRecipe) -> str:
    """Compact human-readable summary.  Goes on the trace and shows up
    in the status block's row-3 and the Edit Stack dialog."""
    t = recipe.filter_type
    p = recipe.params
    family = p.get("family", "butterworth")
    fam_tag = "" if family == "butterworth" else "Bs"
    order   = int(p.get("order", 0) or 0)
    order_s = f" {order}{_ord_suffix(order)}" if order else ""

    if t == "lowpass":
        return f"{fam_tag}LP {format_si_freq(p.get('cutoff_hz', 0))}{order_s}"
    if t == "highpass":
        return f"{fam_tag}HP {format_si_freq(p.get('cutoff_hz', 0))}{order_s}"
    if t == "bandpass":
        return (f"{fam_tag}BP {format_si_freq(p.get('low_hz', 0))}-"
                f"{format_si_freq(p.get('high_hz', 0))}{order_s}")
    if t == "bandstop":
        return (f"{fam_tag}BS {format_si_freq(p.get('low_hz', 0))}-"
                f"{format_si_freq(p.get('high_hz', 0))}{order_s}")
    if t == "notch":
        return f"Notch {format_si_freq(p.get('center_hz', 0))} Q{p.get('q', 0):.3g}"
    if t == "peak":
        return f"Peak {format_si_freq(p.get('center_hz', 0))} Q{p.get('q', 0):.3g}"
    if t == "comb":
        return f"Comb {format_si_freq(p.get('center_hz', 0))} Q{p.get('q', 0):.3g}"
    return t


# ── SOS builders (one per filter type) ────────────────────────────────────────

def _butter_or_bessel_lp_hp(p: dict, sps: float, btype: str) -> np.ndarray:
    nyq    = sps / 2.0
    cutoff = float(p["cutoff_hz"])
    order  = int(p.get("order", 4))
    family = p.get("family", "butterworth")
    if cutoff >= nyq:
        raise FilterEngineError(
            f"{btype.title()} {format_si_freq(cutoff)} exceeds Nyquist "
            f"{format_si_freq(nyq)} for this sample rate")
    wn = min(cutoff / nyq, 0.9999)
    if family == "bessel":
        return sp_signal.bessel(order, wn, btype=btype, output='sos', norm='mag')
    return sp_signal.butter(order, wn, btype=btype, output='sos')


def _butter_or_bessel_band(p: dict, sps: float, btype: str) -> np.ndarray:
    nyq    = sps / 2.0
    low    = float(p["low_hz"])
    high   = float(p["high_hz"])
    order  = int(p.get("order", 4))
    family = p.get("family", "butterworth")
    if low <= 0 or high <= low:
        raise FilterEngineError(
            f"Band edges invalid: low={format_si_freq(low)}, "
            f"high={format_si_freq(high)}")
    if high >= nyq:
        raise FilterEngineError(
            f"High edge {format_si_freq(high)} exceeds Nyquist "
            f"{format_si_freq(nyq)} for this sample rate")
    wn = [min(low / nyq, 0.499), min(high / nyq, 0.9999)]
    if family == "bessel":
        return sp_signal.bessel(order, wn, btype=btype, output='sos', norm='mag')
    return sp_signal.butter(order, wn, btype=btype, output='sos')


def _notch_or_peak(p: dict, sps: float, kind: str) -> np.ndarray:
    nyq    = sps / 2.0
    center = float(p["center_hz"])
    q      = float(p.get("q", 30.0))
    if center <= 0 or center >= nyq:
        raise FilterEngineError(
            f"Center {format_si_freq(center)} outside (0, Nyquist={format_si_freq(nyq)})")
    fn = sp_signal.iirnotch if kind == "notch" else sp_signal.iirpeak
    b, a = fn(center, q, fs=sps)
    return sp_signal.tf2sos(b, a)


def _comb(p: dict, sps: float) -> np.ndarray:
    nyq    = sps / 2.0
    center = float(p["center_hz"])
    q      = float(p.get("q", 30.0))
    if center <= 0 or center >= nyq:
        raise FilterEngineError(
            f"Comb center {format_si_freq(center)} outside (0, Nyquist={format_si_freq(nyq)})")
    # iircomb produces a high-order tf; zpk route avoids tf2sos precision loss.
    b, a = sp_signal.iircomb(center, q, ftype='notch', fs=sps)
    z, p_, k = sp_signal.tf2zpk(b, a)
    return sp_signal.zpk2sos(z, p_, k)


_DISPATCH: Dict[str, Callable[[dict, float], np.ndarray]] = {
    "lowpass":  lambda p, sps: _butter_or_bessel_lp_hp(p, sps, "low"),
    "highpass": lambda p, sps: _butter_or_bessel_lp_hp(p, sps, "high"),
    "bandpass": lambda p, sps: _butter_or_bessel_band(p, sps, "band"),
    "bandstop": lambda p, sps: _butter_or_bessel_band(p, sps, "bandstop"),
    "notch":    lambda p, sps: _notch_or_peak(p, sps, "notch"),
    "peak":     lambda p, sps: _notch_or_peak(p, sps, "peak"),
    "comb":     lambda p, sps: _comb(p, sps),
}


# ── Apply ─────────────────────────────────────────────────────────────────────

def apply_filter_recipe(
    recipe: FilterRecipe, data: np.ndarray, sample_rate: float,
) -> np.ndarray:
    """Apply one recipe to a 1-D array.  Raises FilterEngineError on Nyquist
    violation or unknown filter type.  Returns a new array (does not modify
    the input).  Sample rate must be > 0; data must have at least 4 samples.
    """
    if sample_rate <= 0:
        raise FilterEngineError(f"Invalid sample rate {sample_rate}")
    if data is None or len(data) < 4:
        raise FilterEngineError("Data too short for sosfiltfilt (need >=4 samples)")
    handler = _DISPATCH.get(recipe.filter_type)
    if handler is None:
        raise FilterEngineError(f"Unknown filter type {recipe.filter_type!r}")
    sos = handler(recipe.params, sample_rate)
    return sp_signal.sosfiltfilt(sos, data)


def apply_filter_stack(
    stack: List[FilterRecipe], data: np.ndarray, sample_rate: float,
    on_error: Optional[Callable[[FilterRecipe, FilterEngineError], None]] = None,
) -> np.ndarray:
    """Apply each recipe in order against the running result.

    A recipe that raises FilterEngineError is skipped; the previous stage's
    output is carried forward.  Pass *on_error* to receive notice of skipped
    recipes (e.g. for surfacing in the application's notice bar).
    """
    result = np.asarray(data, dtype=float)
    for recipe in stack:
        try:
            result = apply_filter_recipe(recipe, result, sample_rate)
        except FilterEngineError as exc:
            if on_error is not None:
                on_error(recipe, exc)
            continue
    return result


def stack_summary(stack: List[FilterRecipe]) -> str:
    """Render a full stack as 'first > second > third'.  Empty stack -> ''."""
    return " > ".join(r.ensure_description() for r in stack)
