"""
core/data_loader.py
Handles loading data from CSV and other formats.

Import pipeline
───────────────
1. Read all lines from the file.
2. Run csv_detector.detect_parser() against the first MAX_DETECTION_LINES lines.
   - If a parser plugin is found, call plugin.parse() → ParsedMetadata.
   - If no plugin matches, fall back to the original '#key=value' metadata logic.
3. Build LoadResult: parse CSV data starting at ParsedMetadata.data_start_line,
   apply per-column metadata (names, units, is_time, skip), convert datetime /
   unix-epoch time columns to float seconds, and store wall-clock anchor.

Native TraceLab '#key=value' format
────────────────────────────────────
TraceLab native files carry metadata in '#' comment lines before the header row.
Supported keys (case-insensitive):
  #samplerate=10000   #dt=0.0001   #time=time   #gain=2.5/4096
  #offset=-1.25   #unit=V   #coupling=AC
  #impedance=50   #bwlimit=200M   #zerotime=N
  #viewtimestart=   #viewtimestop=   #viewsamplestart=   #viewsamplestop=

All metric suffixes (k/M/G/m/u/n/p) and fractions (2.5/4096) are supported.
"""

import numpy as np
import csv
import io
import os
import re
import unicodedata
from datetime import datetime, timezone
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


# ── TraceLab native metadata ──────────────────────────────────────────────────

class CsvMetadata:
    """Parsed metadata from '#' header lines (TraceLab native format)."""
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
        # Each entry is (group_name: str, members: list[int | str])
        # Integers are 1-based column indices; strings are exact column names.
        # Resolved to ColumnGroup objects by tracelab_native.py once the
        # column list is known.
        self.groups: list = []

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
            elif key in ("addgroup", "add_group"):
                parsed = _parse_addgroup_value(val)
                if parsed:
                    meta.groups.append(parsed)
        except (ValueError, ZeroDivisionError):
            pass
    return meta


def _parse_addgroup_value(value: str):
    """
    Parse the value half of an #addgroup directive.

    Accepted format:  { "Group Name", member, member, ... }

    Members can be:
      integer — 1-based column index (consistent with #time=N)
      "string" — exact column name as it appears in the CSV header

    Anything after the closing } is ignored, so inline comments are fine:
      #addgroup={ "Temps", 2, 4, 6 }  # the thermocouple channels

    Returns (group_name: str, members: list[int | str]) or None if unparseable.
    """
    start = value.find('{')
    end   = value.rfind('}')
    if start == -1 or end == -1 or end <= start:
        return None
    inner = value[start + 1 : end]

    try:
        items = next(csv.reader([inner.strip()], skipinitialspace=True))
    except StopIteration:
        return None

    items = [item.strip() for item in items if item.strip()]
    if len(items) < 2:
        return None     # need at least a name and one member

    name    = items[0]
    members = []
    for item in items[1:]:
        try:
            members.append(int(item))
        except ValueError:
            members.append(item)    # keep as column-name string

    return name, members


# ── Load result ───────────────────────────────────────────────────────────────

class LoadResult:
    def __init__(self):
        self.columns: Dict[str, np.ndarray] = {}
        self.suggested_time_col: Optional[str] = None
        self.n_rows: int = 0
        self.filename: str = ""
        self.error: Optional[str] = None
        self.metadata: CsvMetadata = CsvMetadata()

        # ── Parser plugin results ─────────────────────────────────────
        # Name of the plugin that handled this file, or "" for built-in logic.
        self.parser_name: str = ""

        # {clean_col_name: ColumnInfo} — populated when a plugin matched.
        # Import dialog uses these for default labels, units, skip flags, groups.
        self.column_infos: dict = {}

        # Suggested column groupings from the parser (list[ColumnGroup]).
        self.column_groups: list = []

        # ISO 8601 string for what real-world moment corresponds to t=0.
        # "" if unknown.  Set to the instrument trigger time (scope) or first
        # sample timestamp (data logger) by the relevant parser plugin.
        self.t0_wall_clock: str = ""

        # How the time axis was derived — matches ParsedMetadata.time_format.
        self.source_time_format: str = "seconds_relative"

        # ── Segment metadata ──────────────────────────────────────────
        # Forwarded from ParsedMetadata.segments / primary_segment.
        # None when the parser did not supply segment info.
        self.segments: Optional[list] = None
        self.primary_segment: Optional[int] = None

        # Per-trace segment lists, built when importing TraceLab native
        # segmented CSVs (which store each segment as a .SEGn column).
        # {clean_trace_name: list[(start, end, t0_abs, t0_rel)]}
        # Falls back to self.segments when the key is absent.
        self.trace_segments: dict = {}

        # Per-trace segment settings parsed from #trace_settings= headers.
        # {clean_trace_name: {"primary_segment": int|None,
        #                     "non_primary_viewmode": str}}
        self.trace_segment_settings: dict = {}

        # Per-column valid data row range from #trace_data_range= headers.
        # {clean_col_name: (start_1based, end_1based)} — both inclusive.
        # Absent key = trace spans all rows.
        self.trace_data_ranges: dict = {}

    @property
    def column_names(self) -> List[str]:
        return list(self.columns.keys())


# ── Main load function ────────────────────────────────────────────────────────

def load_csv(filepath: str, delimiter: str = None,
             rejection_enabled: bool = False,
             rejection_max_lines: int = 10,
             honor_skip_rows: bool = True) -> LoadResult:
    result = LoadResult()
    result.filename = os.path.basename(filepath)
    try:
        # Try encodings in order: UTF-8 with BOM (TraceLab native, most modern
        # tools), Windows-1252 (Agilent/Keysight BenchLink and other legacy
        # Windows apps), Latin-1 (accepts every byte — last-resort fallback).
        # Try encodings in order: UTF-8 with BOM (TraceLab native, most modern
        # tools), Windows-1252 (Agilent/Keysight BenchLink and other legacy
        # Windows apps), Latin-1 (accepts every byte — last-resort fallback).
        # Read the whole file as a single string so we can normalise line
        # endings before splitting: \r\n and bare \r both become \n, which
        # prevents the csv module from seeing \r inside unquoted fields.
        _encodings = ("utf-16", "utf-8-sig", "cp1252", "latin-1")
        all_lines = None
        for _enc in _encodings:
            try:
                with open(filepath, "r", newline="", encoding=_enc) as f:
                    _raw = f.read()
                _raw = _raw.replace("\r\n", "\n").replace("\r", "\n")
                all_lines = _raw.splitlines(keepends=True)
                break
            except UnicodeDecodeError:
                pass
        if all_lines is None:
            result.error = "Could not decode file (tried utf-8, cp1252, latin-1)."
            return result

        # ── Try plugin detection ──────────────────────────────────────
        parsed_meta = None
        try:
            from core.csv_detector import detect_parser
            parser_module = detect_parser(all_lines)
            if parser_module is not None and hasattr(parser_module, "parse"):
                parsed_meta = parser_module.parse(filepath, all_lines)
        except Exception as e:
            # Detection errors must never prevent loading; fall back silently.
            # Print so any parse exception is visible in the console.
            print(f"[data_loader] Parser exception ({type(e).__name__}): {e}")
            parsed_meta = None

        # ── Split into header / data sections ────────────────────────
        if parsed_meta is not None:
            result.parser_name = parsed_meta.parser_name
            header_idx = parsed_meta.data_start_line
            rows_idx   = parsed_meta.data_rows_start_line
            if rows_idx > header_idx:
                # Splice: keep the column-name row, skip the inter-header lines,
                # then append the actual data rows.
                data_lines = [all_lines[header_idx]] + all_lines[rows_idx:]
            else:
                data_lines = all_lines[header_idx:]
            if delimiter is None:
                delimiter = parsed_meta.data_delimiter or ","
            # Build a minimal CsvMetadata from ParsedMetadata for backward compat
            result.metadata = _csv_meta_from_parsed(parsed_meta)
        else:
            # Original TraceLab fallback: '#' comment lines are metadata
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

        # ── Delimiter sniff ───────────────────────────────────────────
        # Try the header row first: it reliably contains the delimiter even
        # when following lines are garbage (which confuses the sniffer).
        data_sample = "".join(data_lines[:20])
        if delimiter is None:
            for _sniff_src in (data_lines[0], data_sample):
                try:
                    dialect = csv.Sniffer().sniff(_sniff_src, delimiters=",;\t |")
                    delimiter = dialect.delimiter
                    break
                except csv.Error:
                    pass
            if delimiter is None:
                delimiter = ","

        # ── Pre-data garbage rejection ────────────────────────────────
        # If enabled, scan lines after the header and silently drop any
        # that look non-numeric (no delimiter, wrong column count, no
        # parseable numbers), up to rejection_max_lines.  Once the first
        # valid data row is found the scan stops; after that, bad lines
        # are the parser's problem (normal errors).
        if rejection_enabled and len(data_lines) > 1:
            data_lines = _skip_preamble_garbage(
                data_lines, delimiter, rejection_max_lines)

        # ── Skip rows requested by parser plugin ─────────────────────
        # Plugins may mark specific data rows for removal (e.g. repeat headers
        # between segments).  0-based indices relative to the first data row
        # (i.e. data_lines[1] = index 0).  None / empty → no filtering.
        # honor_skip_rows=False disables this (controlled via import dialog setting).
        if honor_skip_rows and parsed_meta is not None and getattr(parsed_meta, 'skip_rows', None):
            _skip = set(parsed_meta.skip_rows)
            data_lines = [data_lines[0]] + [
                ln for i, ln in enumerate(data_lines[1:]) if i not in _skip
            ]

        # ── CSV parsing ───────────────────────────────────────────────
        reader = csv.DictReader(io.StringIO("".join(data_lines)),
                                delimiter=delimiter)
        if not reader.fieldnames:
            result.error = "No column headers found."
            return result

        fieldnames_clean = [_clean_name(n) for n in reader.fieldnames]
        name_map = dict(zip(reader.fieldnames, fieldnames_clean))

        raw: Dict[str, List[str]] = {clean: [] for clean in fieldnames_clean}
        for row in reader:
            for orig, clean in name_map.items():
                val = row.get(orig)
                raw[clean].append(val.strip() if val is not None else "")

        for col_name, values in raw.items():
            result.columns[col_name] = _try_parse_numeric(values)

        result.n_rows = len(next(iter(raw.values()))) if raw else 0

        # ── Apply plugin column metadata ──────────────────────────────
        if parsed_meta is not None:
            _apply_plugin_meta(result, parsed_meta)
        else:
            # Original time-column detection
            meta_time = result.metadata.time_col
            if meta_time is not None:
                col_names = list(result.columns.keys())
                meta_time_clean = _clean_name(meta_time) if meta_time else meta_time
                if meta_time_clean.isdigit():
                    idx = int(meta_time_clean) - 1
                    if 0 <= idx < len(col_names):
                        result.suggested_time_col = col_names[idx]
                elif meta_time_clean in result.columns:
                    result.suggested_time_col = meta_time_clean
            else:
                result.suggested_time_col = _detect_time_column(result.columns)

    except Exception as e:
        result.error = str(e)
    return result


# ── Plugin integration helpers ────────────────────────────────────────────────

def _apply_plugin_meta(result: LoadResult, parsed_meta):
    """
    Apply per-column metadata from a ParsedMetadata instance to result.
    Handles datetime / unix-epoch time column conversion.
    """

    # Build column_infos dict keyed by clean name
    time_col_clean = None
    for ci in parsed_meta.columns:
        clean = _clean_name(ci.original_name)
        result.column_infos[clean] = ci
        # Also store by original in case caller uses the raw name
        if ci.original_name != clean:
            result.column_infos[ci.original_name] = ci
        if ci.is_time and clean in result.columns:
            time_col_clean = clean

    result.column_groups           = parsed_meta.groups
    result.source_time_format      = parsed_meta.time_format
    result.segments                = getattr(parsed_meta, 'segments', None)
    result.primary_segment         = getattr(parsed_meta, 'primary_segment', None)
    result.trace_segment_settings  = dict(
        getattr(parsed_meta, 'trace_segment_settings', {}))
    result.trace_data_ranges       = dict(
        getattr(parsed_meta, 'trace_data_ranges', {}))

    # ── NaN sentinels / invalid_above ────────────────────────────────
    if parsed_meta.invalid_above is not None:
        thresh = abs(parsed_meta.invalid_above)
        for col_name, arr in result.columns.items():
            if arr.dtype.kind == "f":
                result.columns[col_name] = np.where(
                    np.abs(arr) > thresh, np.nan, arr)

    for sentinel in (parsed_meta.nan_sentinels or []):
        for col_name, arr in result.columns.items():
            if arr.dtype.kind == "f":
                result.columns[col_name] = np.where(
                    arr == sentinel, np.nan, arr)

    # ── Time column handling ──────────────────────────────────────────
    if time_col_clean is None:
        # Fall back to keyword detection if plugin didn't mark one
        time_col_clean = _detect_time_column(result.columns)

    if time_col_clean:
        result.suggested_time_col = time_col_clean
        time_format = parsed_meta.time_format

        if time_format.startswith("datetime:"):
            fmt = time_format[len("datetime:"):]
            arr = result.columns[time_col_clean]
            if arr.dtype == object:
                float_arr, t0_iso = _parse_datetime_series(list(arr), fmt)
                result.columns[time_col_clean] = float_arr
                result.t0_wall_clock = t0_iso
            # If it somehow parsed as numeric already, leave it
        elif time_format == "unix_epoch":
            arr = result.columns[time_col_clean]
            if arr.dtype.kind == "f":
                float_arr, t0_iso = _unix_epoch_to_relative(arr)
                result.columns[time_col_clean] = float_arr
                result.t0_wall_clock = t0_iso
        # "seconds_relative" — already floats, no conversion needed

    # t0_wall_clock may also come from header (scope trigger time)
    if not result.t0_wall_clock and parsed_meta.start_wall_clock:
        result.t0_wall_clock = parsed_meta.start_wall_clock

    # Apply t0_policy for loggers that want "first_sample" as zero
    if (time_col_clean and
            parsed_meta.t0_policy == "first_sample" and
            result.t0_wall_clock == "" ):
        # Parser didn't extract a wall clock but wants first sample as t=0
        arr = result.columns.get(time_col_clean)
        if arr is not None and arr.dtype.kind == "f" and len(arr) > 0:
            result.columns[time_col_clean] = arr - arr[0]

    # ── Merge TraceLab-native segment columns ─────────────────────────
    # When importing a TraceLab segmented CSV, each segment is stored as
    # a separate .SEGn column.  Merge them back into one flat trace with
    # a populated segments list, and extend the time column to match.
    seg_groups = getattr(parsed_meta, 'segment_col_groups', [])
    if seg_groups:
        time_arr  = result.columns.get(time_col_clean) if time_col_clean else None
        time_merged = False  # merge the time column only once

        for group in seg_groups:
            trace_name   = _clean_name(group["trace_name"])
            seg_col_names = [_clean_name(n) for n in group["seg_col_names"]]
            # Sort metas by segment index so concatenation order is guaranteed
            seg_metas = sorted(group["seg_metas"], key=lambda m: m[0])

            merged_data_parts = []
            merged_time_parts = []
            flat_segments     = []
            flat_offset       = 0

            for (idx, start_row, stop_row, t0_abs, t0_rel) in seg_metas:
                # start_row / stop_row are 1-based inclusive row numbers
                r0 = start_row - 1          # 0-based start (inclusive)
                r1 = stop_row               # 0-based end   (exclusive)

                col_name = (seg_col_names[idx]
                            if 0 <= idx < len(seg_col_names) else None)
                if col_name is None or col_name not in result.columns:
                    continue

                seg_arr = result.columns[col_name]
                r1_clamped = min(r1, len(seg_arr))
                seg_slice  = seg_arr[r0:r1_clamped]
                # Drop trailing NaN rows (from empty cells in shorter segments)
                if seg_slice.dtype.kind == "f":
                    last_valid = len(seg_slice)
                    while last_valid > 0 and np.isnan(seg_slice[last_valid - 1]):
                        last_valid -= 1
                    seg_slice = seg_slice[:last_valid]

                n = len(seg_slice)
                merged_data_parts.append(seg_slice)
                flat_segments.append((flat_offset, flat_offset + n, t0_abs, t0_rel))
                flat_offset += n

                # Time slice for this segment (same trigger-relative window)
                if time_arr is not None:
                    t1c = min(r1, len(time_arr))
                    t_slice = time_arr[r0:t1c]
                    merged_time_parts.append(t_slice[:n])

            if not merged_data_parts:
                continue

            # Store merged data under the logical trace name
            merged_arr = np.concatenate(merged_data_parts)
            result.columns[trace_name] = merged_arr
            result.trace_segments[trace_name] = flat_segments

            # Before removing SEGn columns, capture any group assignment that
            # was stamped on them (from #addgroup= using the logical trace name).
            _seg_group = ""
            for col_name in seg_col_names:
                ci = result.column_infos.get(col_name)
                if ci and ci.group:
                    _seg_group = ci.group
                    break

            # Remove the individual .SEGn columns (data + column_infos)
            for col_name in seg_col_names:
                result.columns.pop(col_name, None)
                result.column_infos.pop(col_name, None)

            # Add a ColumnInfo for the merged trace if none already present
            if trace_name not in result.column_infos:
                from core.csv_parser_types import ColumnInfo  # lazy to avoid circular
                result.column_infos[trace_name] = ColumnInfo(
                    index=0,
                    original_name=group["trace_name"],
                    display_name=group["trace_name"],
                    group=_seg_group,
                )

            # Extend the time column once (all traces share the same time axis)
            if not time_merged and merged_time_parts and time_col_clean:
                merged_time = np.concatenate(merged_time_parts)
                result.columns[time_col_clean] = merged_time
                result.n_rows = len(merged_time)
                time_arr     = merged_time   # keep in sync for subsequent groups
                time_merged  = True


def _csv_meta_from_parsed(parsed_meta) -> CsvMetadata:
    """Build a minimal CsvMetadata for backward compatibility from ParsedMetadata."""
    m = CsvMetadata()
    if parsed_meta.sample_rate:
        m.sample_rate = parsed_meta.sample_rate
        m.dt = 1.0 / parsed_meta.sample_rate
    return m


# ── Datetime / epoch helpers ──────────────────────────────────────────────────

def _parse_datetime_series(values: list, fmt: str):
    """
    Convert a list of datetime strings to float seconds from the first valid sample.
    Returns (float_array, t0_iso_string).
    """
    parsed = []
    for v in values:
        v = v.strip()
        if not v:
            parsed.append(None)
            continue
        try:
            parsed.append(datetime.strptime(v, fmt))
        except ValueError:
            parsed.append(None)

    first = next((dt for dt in parsed if dt is not None), None)
    if first is None:
        return np.full(len(values), np.nan, dtype=np.float64), ""

    t0_iso = first.isoformat()
    seconds = np.array(
        [(dt - first).total_seconds() if dt is not None else np.nan
         for dt in parsed],
        dtype=np.float64,
    )
    return seconds, t0_iso


def _unix_epoch_to_relative(arr: np.ndarray):
    """
    Convert Unix epoch seconds to seconds-from-first-sample.
    Returns (relative_array, t0_iso_string).
    """
    valid = arr[np.isfinite(arr)]
    if len(valid) == 0:
        return arr.copy(), ""
    t0 = float(valid[0])
    t0_iso = datetime.fromtimestamp(t0, tz=timezone.utc).isoformat()
    return arr - t0, t0_iso


# ── Shared utilities (imported by parser plugins) ─────────────────────────────

def _clean_name(s: str) -> str:
    """Strip control characters and leading/trailing whitespace from a column name."""
    cleaned = "".join(c for c in s if unicodedata.category(c) != "Cc")
    return cleaned.strip()


def _try_parse_numeric(values):
    try:
        return np.array([float(v) if v else np.nan for v in values],
                        dtype=np.float64)
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


# ── Pre-data garbage rejection ────────────────────────────────────────────────

def _is_valid_data_line(line: str, delimiter: str, expected_cols: int) -> bool:
    """
    Return True if line looks like a row of data values.
    Requires:
      - At least expected_cols fields when split by delimiter
      - At least one field parseable as a float
    """
    if delimiter in line:
        fields = [f.strip() for f in line.split(delimiter)]
    else:
        if expected_cols > 1:
            return False
        fields = [line.strip()]

    if len(fields) < expected_cols:
        return False

    for f in fields:
        if f:
            try:
                float(f)
                return True
            except ValueError:
                pass
    return False


def _skip_preamble_garbage(data_lines: list, delimiter: str,
                           max_skip: int) -> list:
    """
    data_lines[0] is the column-header row.  Scan lines[1..] and silently
    drop any that fail _is_valid_data_line, up to max_skip lines.
    Stops (keeps everything from) the first line that looks like real data.
    If max_skip is exhausted without finding valid data, the remaining lines
    are passed through unchanged so the normal parser can handle them.
    """
    if not data_lines or max_skip <= 0:
        return data_lines

    header = data_lines[0]
    try:
        expected_cols = len(next(csv.reader(
            [header.strip()], delimiter=delimiter)))
    except StopIteration:
        expected_cols = 1

    rest = data_lines[1:]
    skipped = 0
    kept_from = 0          # index in rest where kept data starts

    for i, line in enumerate(rest):
        stripped = line.strip()
        if not stripped:   # blank lines: skip without counting against budget
            continue
        if _is_valid_data_line(stripped, delimiter, expected_cols):
            kept_from = i
            break
        skipped += 1
        if skipped >= max_skip:
            kept_from = i + 1   # skip this line too, proceed from next
            break
    else:
        return data_lines   # no valid data found at all; leave untouched

    return [header] + rest[kept_from:]
