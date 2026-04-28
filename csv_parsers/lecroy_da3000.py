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
    seg_count       = None   # from "Segments,N,..." line
    seg_size        = None   # from "SegmentSize,M" on the same line
    segment_info    = {}     # seg_number (1-based) → (datetime, relative_time_s)

    for i in range(len(all_lines)):
        line = all_lines[i].strip()
        if not line:
            continue
        if i < 3:
            # Fixed preamble lines — parse line 1 for segment count + size
            if i == 1:
                seg_count, seg_size = _parse_segments_line(line)
            continue

        m = _SEGMENT_RE.match(line)
        if m:
            seg_num = int(m.group(1))
            _parse_segment_entry(line, seg_num, segment_info)
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
    if 1 in segment_info:
        meta.start_wall_clock = segment_info[1][0].isoformat()

    # ── Build segment metadata ──────────────────────────────────────────
    # Only populate when there are 2+ segments; single-segment files remain
    # non-segmented (segments=None) so all existing code paths are unchanged.
    if seg_count is not None and seg_size is not None and seg_count > 1:
        segs = []
        for k in range(1, seg_count + 1):
            start_idx = (k - 1) * seg_size
            end_idx   = k * seg_size
            if k in segment_info:
                dt_obj, t_rel = segment_info[k]
                t_abs = dt_obj.timestamp()
            else:
                t_abs = 0.0
                t_rel = 0.0
            segs.append((start_idx, end_idx, t_abs, t_rel))
        meta.segments        = segs
        meta.primary_segment = None   # GUI will later add a selector

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

def _parse_segments_line(line: str):
    """
    Parse 'Segments,N,SegmentSize,M'.
    Returns (seg_count, seg_size) as ints, or (None, None) on failure.
    """
    parts = [p.strip() for p in line.split(",")]
    # parts: ["Segments", "10", "SegmentSize", "10001"]
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
    return seg_count, seg_size


def _parse_segment_entry(line: str, seg_num: int, out: dict):
    """
    Parse a segment entry line: '#1,23-Mar-2002 02:21:36,0'
    Stores (datetime, relative_time_seconds) for seg_num in out[seg_num].
    """
    # Strip the leading '#N,'
    body = line.split(",", 1)[1] if "," in line else ""
    # body = '23-Mar-2002 02:21:36,0.00132238'
    parts = [p.strip() for p in body.split(",")]
    if not parts:
        return
    trig_str = parts[0]
    try:
        dt = datetime.strptime(trig_str, _TRIGTIME_FMT)
    except ValueError:
        return
    t_rel = 0.0
    if len(parts) >= 2:
        try:
            t_rel = float(parts[1])
        except ValueError:
            pass
    out[seg_num] = (dt, t_rel)
