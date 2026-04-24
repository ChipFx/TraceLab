"""
csv_parsers/keysight_benchlink3_34970.py
Parser for Keysight/Agilent/HP Benchlink 3 data-logger CSV files (34970A).

Time column format: "4/21/2026 13:39:54:460"
  → parsed as %m/%d/%Y %H:%M:%S:%f  (last field is milliseconds, %f pads right→µs)

Invalid instrument readings appear as -9.9E+37; converted to NaN on import.

Column layout after the data header "Scan,Time,…":
  col 0  Scan      — row number, skip
  col 1  Time      — datetime string, is_time = True
  col 2  101 (C)   — measurement, unit from parentheses
  col 3  Alarm 101 — alarm flag, skip = True, group = "Alarms"
  col 4  102 (C)   — measurement
  col 5  Alarm 102 — alarm flag
  …

Groups returned:
  "Measurements"  — all data columns (even indices ≥ 2 in 0-based data header)
  "Alarms"        — all alarm columns (odd indices ≥ 3)
"""

import csv
import re
from datetime import datetime
from typing import Optional
from core.csv_parser_types import ParsedMetadata, ColumnInfo, ColumnGroup

PARSER_NAME = "Keysight Benchlink 3 — 34970A"

# Benchlink time format: month/day/year hour:min:sec:milliseconds
_TIME_FMT = "%m/%d/%Y %H:%M:%S:%f"

# Values the 34970A uses to signal an invalid / overrange reading
_INVALID_ABOVE = 9.9e36

# Pattern that matches the acquisition date line value, e.g.:
#   "4/21/2026 1:39:54 PM"  or  "4/21/2026 13:39:54"
_ACQDATE_FMT_12H = "%m/%d/%Y %I:%M:%S %p"
_ACQDATE_FMT_24H = "%m/%d/%Y %H:%M:%S"

# Regex to extract unit from column names like "101 (C)" or "301 (VDC)"
_UNIT_RE = re.compile(r"\(([^)]+)\)\s*$")


def parse(filepath: str, all_lines: list) -> ParsedMetadata:
    meta = ParsedMetadata()
    meta.parser_name  = PARSER_NAME
    meta.time_format  = "datetime:" + _TIME_FMT
    meta.t0_policy    = "first_sample"
    meta.invalid_above = _INVALID_ABOVE

    # ── Find the data column-header line ───────────────────────────────
    data_header_idx = _find_data_header(all_lines)
    if data_header_idx is None:
        # Fallback: find the last line that starts with "Scan" before data
        data_header_idx = 0

    meta.data_start_line  = data_header_idx
    meta.raw_header_lines = [l.rstrip() for l in all_lines[:data_header_idx]]

    # ── Parse file-level metadata from the header section ──────────────
    _extract_file_metadata(meta, all_lines[:data_header_idx])

    # ── Delimiter (nearly always comma) ────────────────────────────────
    data_sample = "".join(all_lines[data_header_idx:data_header_idx + 10])
    try:
        dialect = csv.Sniffer().sniff(data_sample, delimiters=",;\t")
        meta.data_delimiter = dialect.delimiter
    except csv.Error:
        meta.data_delimiter = ","

    # ── Build ColumnInfo list from the data header row ──────────────────
    header_line = all_lines[data_header_idx].strip()
    try:
        raw_names = next(csv.reader([header_line], delimiter=meta.data_delimiter))
    except StopIteration:
        return meta

    meas_indices  = []
    alarm_indices = []

    for idx, name in enumerate(raw_names):
        name = name.strip()

        # col 0 = "Scan" row counter — skip
        if idx == 0 and name.lower() == "scan":
            ci = ColumnInfo(
                index=idx, original_name=name, display_name=name,
                unit="", gain=1.0, offset=0.0,
                is_time=False, skip=True, group="",
            )
            meta.columns.append(ci)
            continue

        # col 1 = "Time" datetime column
        if idx == 1 and name.lower() == "time":
            ci = ColumnInfo(
                index=idx, original_name=name, display_name=name,
                unit="datetime", gain=1.0, offset=0.0,
                is_time=True, skip=False, group="",
            )
            meta.columns.append(ci)
            continue

        # Alarm columns: name starts with "Alarm "
        if name.lower().startswith("alarm "):
            ci = ColumnInfo(
                index=idx, original_name=name, display_name=name,
                unit="flag", gain=1.0, offset=0.0,
                is_time=False, skip=True, group="Alarms",
            )
            meta.columns.append(ci)
            alarm_indices.append(idx)
            continue

        # Measurement column: extract unit from "101 (C)" → unit="C", display="101"
        unit = ""
        display = name
        m = _UNIT_RE.search(name)
        if m:
            unit    = m.group(1)
            display = name[:m.start()].strip()

        ci = ColumnInfo(
            index=idx, original_name=name, display_name=display,
            unit=unit, gain=1.0, offset=0.0,
            is_time=False, skip=False, group="Measurements",
        )
        meta.columns.append(ci)
        meas_indices.append(idx)

    # ── Column groups ───────────────────────────────────────────────────
    if meas_indices:
        meta.groups.append(ColumnGroup("Measurements", meas_indices))
    if alarm_indices:
        meta.groups.append(ColumnGroup("Alarms", alarm_indices))

    return meta


# ── Helpers ──────────────────────────────────────────────────────────────────

def _find_data_header(lines: list) -> Optional[int]:
    """
    Locate the line that starts the data section.
    Benchlink uses a line like: "Scan,Time,101 (C),Alarm 101,…"
    We look for field 0 == "Scan" AND field 1 == "Time".
    """
    for i, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            continue
        try:
            fields = next(csv.reader([line]))
        except StopIteration:
            continue
        if (len(fields) >= 2
                and fields[0].strip().lower() == "scan"
                and fields[1].strip().lower() == "time"):
            return i
    return None


def _extract_file_metadata(meta: ParsedMetadata, header_lines: list):
    """
    Pull acquisition date (→ start_wall_clock) and any other scalar metadata
    from the pre-data header section.
    """
    for line in header_lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            fields = next(csv.reader([stripped]))
        except StopIteration:
            continue

        if not fields:
            continue

        key = fields[0].strip().rstrip(":")

        if key.lower() == "acquisition date" and len(fields) >= 2:
            val = fields[1].strip()
            dt = _parse_acq_date(val)
            if dt:
                meta.start_wall_clock = dt.isoformat()

        # Extend here for other header fields as needed (Owner, Comments, etc.)


def _parse_acq_date(s: str):
    """Try both 12-hour and 24-hour Benchlink date formats."""
    for fmt in (_ACQDATE_FMT_12H, _ACQDATE_FMT_24H):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None
