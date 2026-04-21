"""
csv_parsers/lecroy_da3000.py
Parser for LeCroy DA3000 series oscilloscope waveform CSV files.

File structure (single-segment example):
    LECROYDDA3000,10081,Waveform
    Segments,1,SegmentSize,100002
    Segment,TrigTime,TimeSinceSegment1
    #1,23-Mar-2002 02:21:36,0
    Time,Ampl
    -0.0050000283,0.354573
    ...

The time column is in seconds relative to the trigger (already floating point).
t=0 is the trigger moment; pre-trigger samples have negative time.
The trigger wall-clock time is extracted from the #N segment lines.
"""

import csv
import io
import re
from datetime import datetime
from core.csv_parser_types import ParsedMetadata, ColumnInfo, ColumnGroup

PARSER_NAME = "LeCroy DA3000 Waveform"

# LeCroy uses this format in segment header lines: "23-Mar-2002 02:21:36"
_TRIGTIME_FMT = "%d-%b-%Y %H:%M:%S"

# LeCroy-style segment marker: "#1", "#2", etc.
_SEGMENT_RE = re.compile(r"^#(\d+),")


def parse(filepath: str, all_lines: list) -> ParsedMetadata:
    meta = ParsedMetadata()
    meta.parser_name = PARSER_NAME
    meta.time_format = "seconds_relative"
    meta.t0_policy   = "as_stored"     # LeCroy time is already trigger-relative

    # ── Locate the data column-header row ──────────────────────────────
    # Skip the 3-line preamble (lines 0-2), then skip #N,date,offset segment
    # entries, and take the first remaining non-empty line as the header.
    data_header_idx = None
    segment_times   = {}   # seg_number → datetime (for t0_wall_clock)

    for i in range(len(all_lines)):
        line = all_lines[i].strip()
        if not line:
            continue
        if i < 3:
            # Fixed preamble lines — parse line 1 for segment count
            if i == 1:
                _parse_segments_line(line, meta)
            continue

        m = _SEGMENT_RE.match(line)
        if m:
            seg_num = int(m.group(1))
            _parse_segment_entry(line, seg_num, segment_times)
            continue

        # First non-segment, non-empty line after the preamble = column header
        data_header_idx = i
        break

    if data_header_idx is None:
        # Fallback: assume line 4
        data_header_idx = 4

    meta.data_start_line  = data_header_idx
    meta.raw_header_lines = [l.rstrip() for l in all_lines[:data_header_idx]]

    # ── Trigger wall-clock time (segment 1 = first trigger) ────────────
    if 1 in segment_times:
        meta.start_wall_clock = segment_times[1].isoformat()

    # ── Delimiter ───────────────────────────────────────────────────────
    data_sample = "".join(all_lines[data_header_idx:data_header_idx + 20])
    try:
        dialect = csv.Sniffer().sniff(data_sample, delimiters=",;\t")
        meta.data_delimiter = dialect.delimiter
    except csv.Error:
        meta.data_delimiter = ","

    # ── Column info from the header row ─────────────────────────────────
    header_line = all_lines[data_header_idx].strip()
    try:
        raw_names = next(csv.reader([header_line], delimiter=meta.data_delimiter))
    except StopIteration:
        raw_names = ["Time", "Ampl"]

    for idx, name in enumerate(raw_names):
        name = name.strip()
        is_time = (idx == 0 and name.lower() in ("time", "t", "seconds", "sec"))
        ci = ColumnInfo(
            index         = idx,
            original_name = name,
            display_name  = name,
            unit          = "s" if is_time else "V",
            gain          = 1.0,
            offset        = 0.0,
            is_time       = is_time,
            skip          = False,
            group         = "",
        )
        meta.columns.append(ci)

    return meta


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_segments_line(line: str, meta: ParsedMetadata):
    """Parse 'Segments,N,SegmentSize,M' to extract sample count / implied rate."""
    parts = [p.strip() for p in line.split(",")]
    # parts: ["Segments", "1", "SegmentSize", "100002"]
    seg_count = None
    seg_size  = None
    for i, p in enumerate(parts):
        if p.lower() == "segments" and i + 1 < len(parts):
            try:
                seg_count = int(parts[i + 1])
            except ValueError:
                pass
        if p.lower() == "segmentsize" and i + 1 < len(parts):
            try:
                seg_size = int(parts[i + 1])
            except ValueError:
                pass
    # We don't know the sample rate from this alone; leave meta.sample_rate = None.
    # The time column in the data provides the actual sample spacing.


def _parse_segment_entry(line: str, seg_num: int, out: dict):
    """
    Parse a segment entry line: '#1,23-Mar-2002 02:21:36,0'
    Stores the trigger datetime for seg_num in out[seg_num].
    """
    # Strip the leading '#N'
    body = line.split(",", 1)[1] if "," in line else ""
    # body = '23-Mar-2002 02:21:36,0'
    parts = [p.strip() for p in body.split(",")]
    if not parts:
        return
    trig_str = parts[0]
    try:
        dt = datetime.strptime(trig_str, _TRIGTIME_FMT)
        out[seg_num] = dt
    except ValueError:
        pass
