"""
core/trace_model.py
Data model for a single oscilloscope trace/channel.

Scaling pipeline (applied in order):
  raw_data  -> gain -> offset -> display as processed_data

Filters are non-destructive: raw_data is never touched by filters.
filter_data holds the filtered result; processed_data returns
filter_data if a filter is active, otherwise the gain+offset result.
"""

import numpy as np
from dataclasses import dataclass, field
from typing import Optional


DEFAULT_TRACE_COLORS = [
    "#F0C040",  # C1 - Yellow
    "#40C0F0",  # C2 - Cyan
    "#F04080",  # C3 - Magenta
    "#40F080",  # C4 - Green
    "#F08040",  # C5 - Orange
    "#A040F0",  # C6 - Purple
    "#40F0F0",  # C7 - Light Cyan
    "#F0F040",  # C8 - Light Yellow
    "#F04040",  # C9 - Red
    "#4080F0",  # C10 - Blue
]

# Light-theme alternates (more saturated/darker so visible on white)
DEFAULT_TRACE_COLORS_LIGHT = [
    "#C08000",  # C1 - Dark Yellow/Gold
    "#0070B0",  # C2 - Blue
    "#C0004080", # C3 - Crimson (use hex without alpha for Qt)
    "#006030",  # C4 - Dark Green
    "#C05000",  # C5 - Dark Orange
    "#6000B0",  # C6 - Deep Purple
    "#007070",  # C7 - Teal
    "#808000",  # C8 - Olive
    "#B00000",  # C9 - Dark Red
    "#0040C0",  # C10 - Dark Blue
]
DEFAULT_TRACE_COLORS_LIGHT = [
    "#B08000", "#0068A0", "#A00040", "#006030", "#A04800",
    "#5800A0", "#007060", "#707000", "#980000", "#003CB0",
]


@dataclass
class ScalingConfig:
    """ADC-to-physical-unit scaling: output = (raw * gain) + offset."""
    enabled: bool = False
    # Linear map mode: input_min..input_max -> output_min..output_max
    input_min: float = 0.0
    input_max: float = 4095.0
    output_min: float = -1.25
    output_max: float = 1.25
    unit: str = "V"
    # Direct gain/offset mode (takes precedence when use_gain_offset=True)
    use_gain_offset: bool = False
    gain: float = 1.0
    offset: float = 0.0

    def apply(self, data: np.ndarray) -> np.ndarray:
        if not self.enabled:
            return data
        if self.use_gain_offset:
            return data * self.gain + self.offset
        in_range = self.input_max - self.input_min
        out_range = self.output_max - self.output_min
        if in_range == 0:
            return data
        return (data - self.input_min) / in_range * out_range + self.output_min

    @property
    def display_unit(self):
        return self.unit


@dataclass
class TraceModel:
    name: str
    raw_data: np.ndarray
    time_data: Optional[np.ndarray] = None
    sample_rate: float = 1.0
    dt: float = 1.0

    color: str = "#F0C040"
    visible: bool = True
    label: str = ""
    unit: str = "V"

    scaling: ScalingConfig = field(default_factory=ScalingConfig)

    # Display state
    y_offset: float = 0.0
    y_scale: float = 1.0
    display_row: int = 0

    # Instrument metadata (from CSV headers or binary import)
    coupling: str = ""
    impedance: str = ""
    bwlimit: str = ""

    # Non-destructive filter result (None = no filter active)
    _filter_data: Optional[np.ndarray] = field(default=None, repr=False)
    _filter_desc: str = field(default="", repr=False)  # e.g. "LP 1kHz"

    # Cache
    _processed_data: Optional[np.ndarray] = field(default=None, repr=False)
    _computed_time: Optional[np.ndarray] = field(default=None, repr=False)

    def __post_init__(self):
        if not self.label:
            self.label = self.name
        self._invalidate_cache()

    def _invalidate_cache(self):
        self._processed_data = None
        self._computed_time = None

    @property
    def processed_data(self) -> np.ndarray:
        """Returns filtered data if a filter is active, else scaled raw data."""
        if self._processed_data is None:
            if self._filter_data is not None:
                self._processed_data = self._filter_data
            else:
                self._processed_data = self.scaling.apply(self.raw_data)
        return self._processed_data

    @property
    def time_axis(self) -> np.ndarray:
        if self._computed_time is None:
            if self.time_data is not None:
                self._computed_time = self.time_data
            else:
                n = len(self.raw_data)
                self._computed_time = np.arange(n) * self.dt
        return self._computed_time

    def set_sample_rate(self, sps: float):
        self.sample_rate = sps
        self.dt = 1.0 / sps if sps != 0 else 1.0
        self._computed_time = None

    def set_dt(self, dt: float):
        self.dt = dt
        self.sample_rate = 1.0 / dt if dt != 0 else 1.0
        self._computed_time = None

    def update_scaling(self, scaling: ScalingConfig):
        self.scaling = scaling
        self._invalidate_cache()

    def set_filter(self, filtered_data: Optional[np.ndarray], description: str = ""):
        """Apply a non-destructive filter. Pass None to clear."""
        self._filter_data = filtered_data
        self._filter_desc = description
        self._processed_data = None  # invalidate display cache only

    def clear_filter(self):
        self.set_filter(None)

    @property
    def has_filter(self) -> bool:
        return self._filter_data is not None

    @property
    def filter_description(self) -> str:
        return self._filter_desc

    @property
    def duration(self) -> float:
        t = self.time_axis
        return (t[-1] - t[0]) if len(t) > 1 else 0.0

    @property
    def n_samples(self) -> int:
        return len(self.raw_data)

    def windowed_data(self, t_start: float, t_end: float):
        t = self.time_axis
        mask = (t >= t_start) & (t <= t_end)
        return t[mask], self.processed_data[mask]
