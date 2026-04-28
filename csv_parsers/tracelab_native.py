"""
csv_parsers/tracelab_native.py
Parser for the TraceLab native '#key=value' CSV format.

Wraps the existing parse_metadata_lines() logic so that all CSV imports go
through the unified parser pipeline — even the home format.
"""

import csv
import io
from core.csv_parser_types import ParsedMetadata, ColumnInfo, ColumnGroup
from core.data_loader import parse_metadata_lines, _clean_name, parse_value

PARSER_NAME = "TraceLab Native CSV"


def parse(filepath: str, all_lines: list) -> ParsedMetadata:
    meta = ParsedMetadata()
    meta.parser_name = PARSER_NAME

    # Split '#' comment/metadata lines from data lines (existing semantics)
    comment_lines = []
    data_lines    = []
    for line in all_lines:
        stripped = line.lstrip()
        if stripped.startswith("#"):
            comment_lines.append(stripped.rstrip())
        else:
            data_lines.append(line)

    meta.raw_header_lines = comment_lines

    # Re-use the existing metadata parser
    csv_meta = parse_metadata_lines(comment_lines)

    # data_start_line: index of the first non-comment line in all_lines
    data_start_line = 0
    for i, line in enumerate(all_lines):
        if not line.lstrip().startswith("#"):
            data_start_line = i
            break
    meta.data_start_line = data_start_line

    # Delimiter
    sample = "".join(data_lines[:10])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t |")
        meta.data_delimiter = dialect.delimiter
    except csv.Error:
        meta.data_delimiter = ","

    # Transfer scalar metadata
    if csv_meta.sample_rate:
        meta.sample_rate = csv_meta.sample_rate
    meta.time_format = "seconds_relative"
    meta.t0_policy   = "as_stored"

    # Build ColumnInfo list from the header row
    if data_lines:
        reader = csv.reader(io.StringIO(data_lines[0]), delimiter=meta.data_delimiter)
        try:
            raw_names = next(reader)
        except StopIteration:
            raw_names = []

        time_hint = csv_meta.time_col  # name or 1-based index

        for idx, raw_name in enumerate(raw_names):
            clean = _clean_name(raw_name)
            ci = ColumnInfo(
                index         = idx,
                original_name = clean,
                display_name  = clean,
                unit          = csv_meta.unit      or "",
                gain          = csv_meta.gain      if csv_meta.gain   is not None else 1.0,
                offset        = csv_meta.offset    if csv_meta.offset is not None else 0.0,
                coupling      = csv_meta.coupling  or "",
                impedance     = csv_meta.impedance or "",
                bwlimit       = csv_meta.bwlimit   or "",
                is_time       = False,
                skip          = False,
                group         = "",
            )

            # Mark the time column
            if time_hint is not None:
                if str(time_hint).isdigit():
                    if idx == int(time_hint) - 1:  # 1-based
                        ci.is_time = True
                elif _clean_name(str(time_hint)).lower() == clean.lower():
                    ci.is_time = True

            meta.columns.append(ci)

    # ── Resolve #addgroup directives ─────────────────────────────────────
    # Build lookup tables from the now-complete column list.
    # name_to_idx: exact clean name → 0-based index (with case-insensitive fallback)
    name_to_idx_exact = {ci.original_name: ci.index for ci in meta.columns}
    name_to_idx_lower = {ci.original_name.lower(): ci.index for ci in meta.columns}

    for group_name, members in csv_meta.groups:
        resolved = []
        for m in members:
            if isinstance(m, int):
                # 1-based column index → 0-based
                idx = m - 1
                if 0 <= idx < len(meta.columns):
                    resolved.append(idx)
            else:
                # String: exact match first, then case-insensitive
                clean_m = _clean_name(m)
                if clean_m in name_to_idx_exact:
                    resolved.append(name_to_idx_exact[clean_m])
                elif clean_m.lower() in name_to_idx_lower:
                    resolved.append(name_to_idx_lower[clean_m.lower()])
                else:
                    # Try matching as a logical segmented trace name.
                    # The exporter writes the trace label (e.g. "Ampl"), but
                    # the CSV columns are "Ampl.SEG0", "Ampl.SEG1", etc.
                    # Resolve to all SEGn columns so the group gets stamped on
                    # every segment column; the segment-merge step in
                    # _apply_plugin_meta then copies it to the merged ColumnInfo.
                    _seg_key = clean_m if clean_m in _seg_cols else next(
                        (k for k in _seg_cols if k.lower() == clean_m.lower()),
                        None)
                    if _seg_key:
                        for ref in _seg_cols[_seg_key]:
                            if isinstance(ref, int):
                                idx = ref - 1
                                if 0 <= idx < len(meta.columns):
                                    resolved.append(idx)
                            else:
                                ref_c = _clean_name(str(ref))
                                if ref_c in name_to_idx_exact:
                                    resolved.append(name_to_idx_exact[ref_c])
                                elif ref_c.lower() in name_to_idx_lower:
                                    resolved.append(
                                        name_to_idx_lower[ref_c.lower()])
                    # else: silently skip — column removed or renamed

        if resolved:
            # Also stamp the group name onto the matching ColumnInfo objects
            # so the import dialog can use it for display.
            for idx in resolved:
                meta.columns[idx].group = group_name
            meta.groups.append(ColumnGroup(group_name, resolved))

    # ── Resolve #segments= / #segment_meta= / #trace_settings= /
    #          #trace_meta= / #t0_wall_clock= directives ──────────────────────
    # These are accumulated into dicts keyed by trace name, then assembled
    # into meta.segment_col_groups, meta.trace_segment_settings, etc.
    _seg_cols    = {}   # trace_name → list of col-name/index references
    _seg_metas   = {}   # trace_name → list of (idx, start, stop, t0_abs, t0_rel)
    _trace_metas = {}   # trace_name → {attr: value}  from #trace_meta= headers

    for line in comment_lines:
        # Strip the leading '#' and split on first '='
        bare = line.lstrip("#").strip()
        if "=" not in bare:
            continue
        key, _, val = bare.partition("=")
        key = key.strip().lower()

        if key == "segments":
            tname, col_refs = _parse_segments_header(val)
            if tname is not None:
                _seg_cols.setdefault(tname, []).extend(col_refs)

        elif key == "segment_meta":
            result = _parse_segment_meta_header(val)
            if result is not None:
                tname, idx, start, stop, t0_abs, t0_rel = result
                _seg_metas.setdefault(tname, []).append(
                    (idx, start, stop, t0_abs, t0_rel))

        elif key == "trace_settings":
            tname, settings = _parse_trace_settings_header(val)
            if tname is not None:
                meta.trace_segment_settings[tname] = settings

        elif key == "trace_meta":
            tname, attrs = _parse_trace_meta_header(val)
            if tname is not None:
                # Key by cleaned name so lookup against ci.original_name is direct
                _trace_metas[_clean_name(tname)] = attrs

        elif key == "t0_wall_clock":
            meta.start_wall_clock = val.strip()

        elif key == "trace_data_range":
            result = _parse_trace_data_range_header(val)
            if result is not None:
                tname, r0, r1 = result
                meta.trace_data_ranges[_clean_name(tname)] = (r0, r1)

    # Build segment_col_groups from the accumulated dicts
    for tname, col_refs in _seg_cols.items():
        # col_refs may be strings (column names) or ints (1-based indices).
        # Resolve integer refs to column names where possible.
        resolved_names = []
        all_col_names = [ci.original_name for ci in meta.columns]
        for ref in col_refs:
            if isinstance(ref, int):
                idx0 = ref - 1   # 1-based → 0-based
                if 0 <= idx0 < len(all_col_names):
                    resolved_names.append(all_col_names[idx0])
            else:
                resolved_names.append(ref)
        metas = sorted(_seg_metas.get(tname, []), key=lambda m: m[0])
        meta.segment_col_groups.append({
            "trace_name"   : tname,
            "seg_col_names": resolved_names,
            "seg_metas"    : metas,
        })

    # ── Apply #trace_meta= attributes to ColumnInfo objects ──────────────────
    # Keys in _trace_metas are already _clean_name'd; ci.original_name is clean.
    for ci in meta.columns:
        tm = _trace_metas.get(ci.original_name)
        if not tm:
            continue
        if tm.get("unit"):
            ci.unit = tm["unit"]
        if tm.get("coupling"):
            ci.coupling = tm["coupling"]
        if tm.get("impedance"):
            ci.impedance = tm["impedance"]
        if tm.get("bwlimit"):
            ci.bwlimit = tm["bwlimit"]
        if tm.get("gain"):
            try:
                ci.gain = parse_value(tm["gain"])
            except (ValueError, ZeroDivisionError):
                pass
        if tm.get("offset"):
            try:
                ci.offset = parse_value(tm["offset"])
            except (ValueError, ZeroDivisionError):
                pass
        # sps= and dt= — prefer dt when both present (fewer floating-point steps)
        _sps_str = tm.get("sps") or tm.get("samplerate") or tm.get("sample_rate")
        _dt_str  = tm.get("dt")
        _sps_val = None
        if _dt_str:
            try:
                _dt_val = parse_value(_dt_str)
                if _dt_val > 0:
                    _sps_val = 1.0 / _dt_val
            except (ValueError, ZeroDivisionError):
                pass
        if _sps_val is None and _sps_str:
            try:
                _sps_val = parse_value(_sps_str)
            except (ValueError, ZeroDivisionError):
                pass
        if _sps_val is not None and _sps_val > 0:
            ci.sample_rate = _sps_val
        if tm.get("t0_wall_clock"):
            ci.t0_wall_clock = tm["t0_wall_clock"]

    return meta


# ── Segment header parsers ────────────────────────────────────────────────────

def _parse_segments_header(value_str: str):
    """
    Parse '#segments=(name, col1, col2, ...)' or '#segments={name, 2, 3, 4}'.
    Returns (trace_name, [col_ref, ...]) where col_ref is str or int.
    """
    inner = value_str.strip()
    if (inner.startswith("(") and inner.endswith(")")) or \
       (inner.startswith("{") and inner.endswith("}")):
        inner = inner[1:-1].strip()
    try:
        tokens = next(csv.reader([inner], skipinitialspace=True))
    except (StopIteration, csv.Error):
        return None, []
    if not tokens:
        return None, []
    trace_name = tokens[0].strip().strip("\"'")
    col_refs = []
    for tok in tokens[1:]:
        tok = tok.strip()
        try:
            col_refs.append(int(tok))
        except ValueError:
            col_refs.append(tok.strip("\"'"))
    return trace_name, col_refs


def _parse_segment_meta_header(value_str: str):
    """
    Parse '#segment_meta={name, idx, start, stop, t0_abs, t0_rel}'.
    Returns (trace_name, idx, start_row, stop_row, t0_abs, t0_rel) or None.
    start_row / stop_row are 1-based inclusive row numbers.
    """
    inner = value_str.strip()
    if inner.startswith("{") and inner.endswith("}"):
        inner = inner[1:-1].strip()
    try:
        tokens = next(csv.reader([inner], skipinitialspace=True))
    except (StopIteration, csv.Error):
        return None
    if len(tokens) < 6:
        return None
    try:
        trace_name = tokens[0].strip().strip("\"'")
        idx    = int(tokens[1])
        start  = int(tokens[2])
        stop   = int(tokens[3])
        t0_abs = float(tokens[4])
        t0_rel = float(tokens[5])
    except (ValueError, IndexError):
        return None
    return trace_name, idx, start, stop, t0_abs, t0_rel


def _parse_trace_settings_header(value_str: str):
    """
    Parse '#trace_settings={name,primary_segment_or_null,"viewmode"}'.
    Positional format — three tokens after the braces:
      0: trace name (quoted)
      1: primary_segment — integer or the word null/None
      2: non_primary_viewmode string (quoted)
    Returns (trace_name, settings_dict) or (None, {}).
    """
    inner = value_str.strip()
    if inner.startswith("{") and inner.endswith("}"):
        inner = inner[1:-1].strip()
    try:
        tokens = next(csv.reader([inner], skipinitialspace=True))
    except (StopIteration, csv.Error):
        return None, {}
    if not tokens:
        return None, {}
    trace_name = tokens[0].strip().strip("\"'")
    settings = {}
    if len(tokens) >= 2:
        v = tokens[1].strip().strip("\"'")
        if v.lower() in ("null", "none", ""):
            settings["primary_segment"] = None
        else:
            try:
                settings["primary_segment"] = int(v)
            except ValueError:
                settings["primary_segment"] = None
    if len(tokens) >= 3:
        settings["non_primary_viewmode"] = tokens[2].strip().strip("\"'")
    return trace_name, settings


def _parse_trace_meta_header(value_str: str):
    """
    Parse '#trace_meta={"TraceName","unit=V","coupling=DC","gain=1/4096",...}'.

    Each element after the trace name is a "key=value" token.
    Supported keys: unit, coupling, impedance, bwlimit, gain, offset.

    Returns (trace_name, {key: value_string}) or (None, {}) on failure.
    The caller is responsible for converting numeric strings (gain, offset)
    via parse_value() before use.
    """
    inner = value_str.strip()
    if inner.startswith("{") and inner.endswith("}"):
        inner = inner[1:-1].strip()
    try:
        tokens = next(csv.reader([inner], skipinitialspace=True))
    except (StopIteration, csv.Error):
        return None, {}
    if not tokens:
        return None, {}
    trace_name = tokens[0].strip().strip("\"'")
    attrs = {}
    for tok in tokens[1:]:
        tok = tok.strip().strip("\"'")
        if "=" in tok:
            k, _, v = tok.partition("=")
            attrs[k.strip().lower()] = v.strip()
    return trace_name, attrs


def _parse_trace_data_range_header(value_str: str):
    """
    Parse '#trace_data_range={"TraceName",start_row,end_row}'.

    Row numbers are 1-based and inclusive, relative to the first data row
    (i.e. row 1 = the row immediately after the column-header row).

    Returns (trace_name, start_row, end_row) or None on failure.
    """
    inner = value_str.strip()
    if inner.startswith("{") and inner.endswith("}"):
        inner = inner[1:-1].strip()
    try:
        tokens = next(csv.reader([inner], skipinitialspace=True))
    except (StopIteration, csv.Error):
        return None
    if len(tokens) < 3:
        return None
    try:
        trace_name = tokens[0].strip().strip("\"'")
        start = int(tokens[1])
        end   = int(tokens[2])
    except (ValueError, IndexError):
        return None
    if start < 1 or end < start:
        return None
    return trace_name, start, end
