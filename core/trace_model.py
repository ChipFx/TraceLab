"""
core/trace_model.py
Data model for a single oscilloscope trace/channel.
"""

import numpy as np
from dataclasses import dataclass, field
from typing import Optional


# Default LeCroy/scope-style colors
DEFAULT_TRACE_COLORS = [
    "#F0C040",  # C1 - Yellow (classic scope)
    "#40C0F0",  # C2 - Cyan
    "#F04080",  # C3 - Magenta/Pink
    "#40F080",  # C4 - Green
    "#F08040",  # C5 - Orange
    "#A040F0",  # C6 - Purple
    "#40F0F0",  # C7 - Light Cyan
    "#F0F040",  # C8 - Light Yellow
    "#F04040",  # C9 - Red
    "#4080F0",  # C10 - Blue
]


@dataclass
class ScalingConfig:
    """ADC-to-physical-unit scaling configuration."""
    enabled: bool = False
    input_min: float = 0.0
    input_max: float = 4095.0
    output_min: float = -1.25
    output_max: float = 1.25
    unit: str = "V"
    # Optional multiplier for things like current shunts (V -> A)
    post_scale: float = 1.0
    post_scale_unit: str = ""  # if set, overrides unit

    def apply(self, data: np.ndarray) -> np.ndarray:
        if not self.enabled:
            return data
        in_range = self.input_max - self.input_min
        out_range = self.output_max - self.output_min
        if in_range == 0:
            return data
        scaled = (data - self.input_min) / in_range * out_range + self.output_min
        if self.post_scale != 1.0:
            scaled = scaled * self.post_scale
        return scaled

    @property
    def display_unit(self):
        if self.post_scale_unit:
            return self.post_scale_unit
        return self.unit


@dataclass
class TraceModel:
    """Represents a single data trace/channel."""
    name: str
    raw_data: np.ndarray
    time_data: Optional[np.ndarray] = None  # None = use sample index + sps
    sample_rate: float = 1.0  # samples per second
    dt: float = 1.0           # seconds per sample (= 1/sample_rate)

    color: str = "#F0C040"
    visible: bool = True
    label: str = ""           # display label (defaults to name)
    unit: str = "V"

    scaling: ScalingConfig = field(default_factory=ScalingConfig)

    # Display state
    y_offset: float = 0.0     # volts offset
    y_scale: float = 1.0      # vertical zoom multiplier
    display_row: int = 0      # which "lane" in split view (-1 = overlay)

    # Processed cache
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
        if self._processed_data is None:
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
        self._processed_data = None

    @property
    def duration(self) -> float:
        t = self.time_axis
        if len(t) == 0:
            return 0.0
        return t[-1] - t[0]

    @property
    def n_samples(self) -> int:
        return len(self.raw_data)

    def windowed_data(self, t_start: float, t_end: float):
        """Return (time, data) arrays clipped to [t_start, t_end]."""
        t = self.time_axis
        mask = (t >= t_start) & (t <= t_end)
        return t[mask], self.processed_data[mask]
