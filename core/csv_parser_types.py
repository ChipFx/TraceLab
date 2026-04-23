"""
core/csv_parser_types.py
Shared data types for the CSV format detection and parsing plugin system.

A CSV parser plugin lives in csv_parsers/ as a pair of files:
  <name>.toml   — detection rules and metadata (no Python required for simple formats)
  <name>.py     — parse() function (and optional detect() for complex cases)

The parser's parse() function signature:
    def parse(filepath: str, all_lines: list[str]) -> ParsedMetadata

The optional detect() function signature (used when toml has advanced_detection = true):
    def detect(all_lines: list[str]) -> bool
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ColumnInfo:
    """Per-column metadata returned by a CSV parser plugin."""
    index: int                        # 0-based column index in data header row
    original_name: str                # column name exactly as read from the CSV header
    display_name: str = ""            # suggested display label (falls back to original_name)
    unit: str = ""                    # physical unit: "°C", "VDC", "V", …
    gain: float = 1.0                 # scale factor: output = raw * gain + offset
    offset: float = 0.0
    is_time: bool = False             # True → this column is the time axis
    skip: bool = False                # True → default-unchecked in import dialog
    group: str = ""                   # group name, e.g. "Temperatures" or "Alarms"

    def __post_init__(self):
        if not self.display_name:
            self.display_name = self.original_name


@dataclass
class ColumnGroup:
    """A named set of column indices for quick select/deselect in the import UI."""
    name: str
    column_indices: list = field(default_factory=list)   # list[int]


@dataclass
class ParsedMetadata:
    """
    Everything a CSV parser plugin knows about a file before the data rows begin.

    Fields the import pipeline uses directly:
      data_start_line   — 0-based index of the column-header row in the file
      data_delimiter    — field separator for the data section
      columns           — per-column metadata (list[ColumnInfo])
      time_format       — one of:
                            "seconds_relative"          existing float seconds, keep as-is
                            "unix_epoch"                convert epoch→seconds from first sample
                            "datetime:<strptime_fmt>"   parse and convert to float seconds
      t0_policy         — where to place t=0 on the time axis:
                            "as_stored"    keep whatever the file says (scopes: trigger point)
                            "first_sample" subtract first timestamp so t0 = 0
                            "last_sample"  subtract last timestamp (useful for some loggers)
      sample_rate       — if derivable from header metadata (Hz); None if unknown
      start_wall_clock  — ISO 8601 string of the real-world moment corresponding to t=0
                          (trigger time for scopes, acquisition start for loggers)
                          "" if not available.
      groups            — suggested column groupings for the import UI (list[ColumnGroup])
      nan_sentinels     — float values that mean "invalid reading"; replaced with NaN
      invalid_above     — values with abs() above this threshold become NaN (e.g. 9e36)
      raw_header_lines  — the raw lines above data_start_line, for debugging/display
      parser_name       — human-readable name of the parser that produced this
    """
    parser_name: str = ""
    data_start_line: int = 0          # 0-based index of the column-header row
    data_rows_start_line: int = 0     # 0-based index of the first actual data row
                                      # 0 (default) → immediately after data_start_line
                                      # Set > data_start_line when there are per-column
                                      # metadata rows between the header and the data
                                      # (units row, range row, comment row, etc.).
                                      # The parser can read those rows itself and fold
                                      # the info into ColumnInfo fields; the loader just
                                      # needs to know where the numbers start.
    data_delimiter: str = ","
    columns: list = field(default_factory=list)           # list[ColumnInfo]
    time_format: str = "seconds_relative"
    t0_policy: str = "as_stored"
    sample_rate: Optional[float] = None
    start_wall_clock: str = ""
    groups: list = field(default_factory=list)            # list[ColumnGroup]
    nan_sentinels: list = field(default_factory=list)     # list[float]
    invalid_above: Optional[float] = None
    raw_header_lines: list = field(default_factory=list)  # list[str]

    # ── Segment metadata ─────────────────────────────────────────────
    # For instruments that pack multiple trigger segments into one file.
    # Each tuple: (start_index, end_index, t0_absolute, t0_relative)
    #   start_index : int   — inclusive 0-based index into the data arrays
    #   end_index   : int   — exclusive end index (Python slice convention)
    #   t0_absolute : float — Unix timestamp of this segment's trigger
    #   t0_relative : float — seconds since segment-1 trigger (0.0 for seg 1)
    # None = non-segmented or unknown (no special handling needed).
    segments: Optional[list] = None        # list[tuple[int,int,float,float]] | None
    primary_segment: Optional[int] = None  # 0-based index; None = all equal

    # ── Row filtering ────────────────────────────────────────────────
    # 0-based indices of data rows (after the column-header row) to drop
    # before parsing.  None = skip nothing.  Used when a format embeds
    # per-segment repeat headers or comment rows inside the data section.
    skip_rows: Optional[list] = None       # list[int] | None

    # ── Segment column groupings (TraceLab native segmented format) ───
    # Parsed from #segments= and #segment_meta= headers.
    # The import pipeline uses this to merge .SEGn columns back into one
    # flat trace with a populated segments list.
    #
    # Each entry in segment_col_groups:
    # {
    #   "trace_name"  : str,
    #   "seg_col_names": list[str],  # column names, index-ordered
    #   "seg_metas"   : list[(idx, start_row, stop_row, t0_abs, t0_rel)]
    # }
    segment_col_groups: list = field(default_factory=list)

    # Per-trace settings from #trace_settings= headers.
    # {trace_name: {"primary_segment": int|None, "non_primary_viewmode": str}}
    trace_segment_settings: dict = field(default_factory=dict)
