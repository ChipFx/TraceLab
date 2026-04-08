"""
core/data_loader.py
Handles loading data from CSV and other formats.

Supports '#' prefixed metadata lines at the top of the CSV, following the
widely-used convention of '#' as a comment/metadata marker (used by gnuplot,
numpy savetxt, R, LabVIEW exports, many instruments).  Lines starting with '#'
before the header row are parsed for key=value pairs.

Supported metadata keys (case-insensitive):
  #samplerate=10000        or  #samplerate=10k / 2.2M / 100G
  #dt=0.0001               (seconds per sample; linked with samplerate)
  #time=time               (column name to use as time axis)
  #time=3                  (1-based column index to use as time axis)
  #gain=2.5/4096           (multiplier applied after reading; supports fractions)
  #gain=1024
  #attenuation=4096        (divides; equivalent to gain=1/4096)
  #offset=-1.25            (added AFTER gain, in output units)
  #unit=V
  #coupling=AC
  #impedance=50
  #bwlimit=200M

Example CSV header block:
  #samplerate=10k
  #gain=2.5/4096
  #offset=-1.25
  #unit=V
  time,Ch1,Ch2,Ch3
  0.0,1234,2048,512
"""

import numpy as np
import csv
import io
import os
import re
from typing import Dict, List, Optional


_METRIC_SUFFIXES = {
    "T": 1e12, "G": 1e9, "M": 1e6,
    "k": 1e3,  "K": 1e3,
    "m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12,
}


def parse_value(s: str) -> float:
    """Parse numeric string with metric suffixes and fraction support."""
    s = s.strip()
    if "/" in s:
        parts = s.split("/", 1)
        return parse_value(parts[0]) / parse_value(parts[1])
    m = re.match(r"^([+-]?\d+\.?\d*(?:[eE][+-]?\d+)?)([TGMkKmunp]?)$", s)
    if m:
        num = float(m.group(1))
        suf = m.group(2)
        return num * _METRIC_SUFFIXES.get(suf, 1.0)
    return float(s)


class CsvMetadata:
    """Parsed metadata from '#' header lines."""
    def __init__(self):
        self.sample_rate: Optional[float] = None
        self.dt: Optional[float] = None
        self.time_col: Optional[str] = None
        self.gain: Optional[float] = None
        self.offset: Optional[float] = None
        self.unit: Optional[str] = None
        self.coupling: Optional[str] = None
        self.impedance: Optional[str] = None
        self.bwlimit: Optional[str] = None
        self.zerotime: Optional[int] = None
        self.view_time_start: Optional[float] = None
        self.view_time_stop: Optional[float] = None
        self.view_sample_start: Optional[int] = None
        self.view_sample_stop: Optional[int] = None
        self.raw_lines: List[str] = []

    def __repr__(self):
        return (f"CsvMetadata(sps={self.sample_rate}, gain={self.gain}, "
                f"offset={self.offset}, unit={self.unit})")


def parse_metadata_lines(lines: List[str]) -> CsvMetadata:
    meta = CsvMetadata()
    meta.raw_lines = lines
    for line in lines:
        line = line.lstrip("#").strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip().lower()
        val = val.strip()
        try:
            if key in ("samplerate", "sample_rate", "fs", "sps"):
                meta.sample_rate = parse_value(val)
                meta.dt = 1.0 / meta.sample_rate if meta.sample_rate else None
            elif key == "dt":
                meta.dt = parse_value(val)
                meta.sample_rate = 1.0 / meta.dt if meta.dt else None
            elif key == "time":
                meta.time_col = val
            elif key == "gain":
                meta.gain = parse_value(val)
            elif key in ("attenuation", "atten"):
                atten = parse_value(val)
                meta.gain = 1.0 / atten if atten != 0 else None
            elif key == "offset":
                meta.offset = parse_value(val)
            elif key == "unit":
                meta.unit = val
            elif key == "coupling":
                meta.coupling = val.upper()
            elif key in ("impedance", "imp"):
                meta.impedance = val
            elif key in ("bwlimit", "bw", "bandwidth"):
                meta.bwlimit = val
            elif key in ("zerotime", "zero_time", "t0", "triggersample"):
                meta.zerotime = int(float(val))
            elif key in ("viewtimestart", "view_time_start"):
                meta.view_time_start = float(val)
            elif key in ("viewtimestop", "view_time_stop", "viewtimeend"):
                meta.view_time_stop = float(val)
            elif key in ("viewsamplestart", "view_sample_start"):
                meta.view_sample_start = int(float(val))
            elif key in ("viewsamplestop", "view_sample_stop", "viewsampleend"):
                meta.view_sample_stop = int(float(val))
        except (ValueError, ZeroDivisionError):
            pass
    return meta


class LoadResult:
    def __init__(self):
        self.columns: Dict[str, np.ndarray] = {}
        self.suggested_time_col: Optional[str] = None
        self.n_rows: int = 0
        self.filename: str = ""
        self.error: Optional[str] = None
        self.metadata: CsvMetadata = CsvMetadata()

    @property
    def column_names(self) -> List[str]:
        return list(self.columns.keys())


def load_csv(filepath: str, delimiter: str = None) -> LoadResult:
    result = LoadResult()
    result.filename = os.path.basename(filepath)
    try:
        with open(filepath, "r", newline="", encoding="utf-8-sig") as f:
            all_lines = f.readlines()

        meta_lines = []
        data_lines = []
        for line in all_lines:
            stripped = line.lstrip()
            if stripped.startswith("#"):
                meta_lines.append(stripped.rstrip())
            else:
                data_lines.append(line)

        result.metadata = parse_metadata_lines(meta_lines)

        if not data_lines:
            result.error = "File contains no data rows."
            return result

        data_sample = "".join(data_lines[:20])
        if delimiter is None:
            try:
                dialect = csv.Sniffer().sniff(data_sample, delimiters=",;\t |")
                delimiter = dialect.delimiter
            except csv.Error:
                delimiter = ","

        reader = csv.DictReader(io.StringIO("".join(data_lines)),
                                delimiter=delimiter)
        if not reader.fieldnames:
            result.error = "No column headers found."
            return result

        raw: Dict[str, List[str]] = {name: [] for name in reader.fieldnames}
        for row in reader:
            for name in reader.fieldnames:
                raw[name].append(row.get(name, "").strip())

        for col_name, values in raw.items():
            result.columns[col_name] = _try_parse_numeric(values)

        result.n_rows = len(next(iter(raw.values()))) if raw else 0

        meta_time = result.metadata.time_col
        if meta_time is not None:
            col_names = list(result.columns.keys())
            if meta_time.isdigit():
                idx = int(meta_time) - 1
                if 0 <= idx < len(col_names):
                    result.suggested_time_col = col_names[idx]
            elif meta_time in result.columns:
                result.suggested_time_col = meta_time
        else:
            result.suggested_time_col = _detect_time_column(result.columns)

    except Exception as e:
        result.error = str(e)
    return result


def _try_parse_numeric(values):
    try:
        return np.array([float(v) if v else np.nan for v in values], dtype=np.float64)
    except (ValueError, TypeError):
        return np.array(values, dtype=object)


def _detect_time_column(columns):
    time_keywords = {"time", "t", "timestamp", "ts", "seconds",
                     "ms", "us", "ns", "sample_time"}
    for name in columns:
        if name.lower() in time_keywords:
            return name
        if any(kw in name.lower() for kw in ("time", "stamp", "elapsed")):
            return name
    return None


def is_numeric_column(arr: np.ndarray) -> bool:
    return arr.dtype.kind in ("f", "i", "u")
