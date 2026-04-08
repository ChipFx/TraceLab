"""
core/data_loader.py
Handles loading data from CSV and other formats.
Returns raw column data for the import dialog to configure.
"""

import numpy as np
import csv
import os
from typing import Dict, List, Optional, Tuple


class LoadResult:
    """Result of a file load operation."""
    def __init__(self):
        self.columns: Dict[str, np.ndarray] = {}   # col_name -> raw array
        self.suggested_time_col: Optional[str] = None
        self.n_rows: int = 0
        self.filename: str = ""
        self.error: Optional[str] = None

    @property
    def column_names(self) -> List[str]:
        return list(self.columns.keys())


def load_csv(filepath: str, delimiter: str = None) -> LoadResult:
    """
    Load a CSV file and return all columns as numpy arrays.
    Auto-detects delimiter if not specified.
    """
    result = LoadResult()
    result.filename = os.path.basename(filepath)

    try:
        with open(filepath, "r", newline="", encoding="utf-8-sig") as f:
            # Sniff delimiter
            if delimiter is None:
                sample = f.read(4096)
                f.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=",;\t |")
                    delimiter = dialect.delimiter
                except csv.Error:
                    delimiter = ","

            reader = csv.DictReader(f, delimiter=delimiter)
            if not reader.fieldnames:
                result.error = "No column headers found."
                return result

            # Collect raw rows
            raw: Dict[str, List[str]] = {name: [] for name in reader.fieldnames}
            for row in reader:
                for name in reader.fieldnames:
                    raw[name].append(row.get(name, "").strip())

        # Convert to numpy, trying float first
        for col_name, values in raw.items():
            arr = _try_parse_numeric(values)
            result.columns[col_name] = arr

        result.n_rows = len(next(iter(raw.values()))) if raw else 0

        # Heuristic: detect time column
        result.suggested_time_col = _detect_time_column(result.columns)

    except Exception as e:
        result.error = str(e)

    return result


def _try_parse_numeric(values: List[str]) -> np.ndarray:
    """Try to parse a list of strings as floats. Returns object array if not numeric."""
    try:
        arr = np.array([float(v) if v else np.nan for v in values], dtype=np.float64)
        return arr
    except (ValueError, TypeError):
        return np.array(values, dtype=object)


def _detect_time_column(columns: Dict[str, np.ndarray]) -> Optional[str]:
    """Heuristic to identify a time column by name."""
    time_keywords = ["time", "t", "timestamp", "ts", "seconds", "ms", "us", "ns", "sample_time"]
    for name in columns:
        if name.lower() in time_keywords:
            return name
        if any(kw in name.lower() for kw in ["time", "stamp", "elapsed"]):
            return name
    return None


def is_numeric_column(arr: np.ndarray) -> bool:
    return arr.dtype.kind in ("f", "i", "u")
