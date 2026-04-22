"""
csv_parsers/tracelab_native.py
Parser for the TraceLab native '#key=value' CSV format.

Wraps the existing parse_metadata_lines() logic so that all CSV imports go
through the unified parser pipeline — even the home format.
"""

import csv
import io
from core.csv_parser_types import ParsedMetadata, ColumnInfo, ColumnGroup
from core.data_loader import parse_metadata_lines, _clean_name

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
                unit          = csv_meta.unit or "",
                gain          = csv_meta.gain  if csv_meta.gain   is not None else 1.0,
                offset        = csv_meta.offset if csv_meta.offset is not None else 0.0,
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
                # Silently skip unresolvable names — column may have been
                # removed or renamed since the file was written.

        if resolved:
            # Also stamp the group name onto the matching ColumnInfo objects
            # so the import dialog can use it for display.
            for idx in resolved:
                meta.columns[idx].group = group_name
            meta.groups.append(ColumnGroup(group_name, resolved))

    return meta
