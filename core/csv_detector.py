"""
core/csv_detector.py
CSV format auto-detection and parser plugin loader.

Detection pipeline:
  1. Load every *.toml file from the csv_parsers/ directory (next to core/).
  2. For each parser, evaluate its [[detection]] rule groups against the first
     MAX_DETECTION_LINES lines of the file.
  3. A rule group passes when its strategy ("all" or "any") is satisfied.
  4. A parser is a candidate when ANY of its detection groups passes.
  5. For parsers with advanced_detection = true, the Python plugin's detect()
     function is called to confirm or veto the TOML result.
  6. If no TOML match was found, all plugins that have a detect() function are
     called as a last-resort fallback (there will never be more than ~20).
  7. Among all confirmed candidates, the one with the highest priority wins.
     Ties are broken alphabetically by parser name.

Rule structure inside [[detection]] groups:
  strategy = "all"   (default) all rules must pass; use "any" for OR logic

  [[detection.rules]]
  # Location — pick ONE:
  line = 0               exact 0-based line index
  line_search = 30       any of the first N lines

  # Target — optional, defaults to whole line text:
  field = 0              0-based field index after splitting by the sniffed delimiter

  # Match — pick ONE:
  equals   = "Name:"     exact match after strip() (default case-insensitive unless
                          case_sensitive = true)
  contains = "34970"     substring check
  pattern  = "^LECROY"   regex search (re.search)

  # Modifier:
  case_sensitive = false  (default false for equals/contains; patterns always exact)
"""

import os
import re
import csv
import importlib.util
import sys
from typing import Optional

try:
    import tomllib                    # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib       # type: ignore   pip install tomli
    except ImportError:
        tomllib = None                # graceful degradation: detection disabled

MAX_DETECTION_LINES = 100            # how many lines to read for detection

# Cache: list of (toml_dict, parser_py_path)  — loaded once, reused
_parser_registry: Optional[list] = None
_registry_mtimes: dict = {}         # {toml_path: mtime} — per-file, for hot-reload


def _parsers_dir() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "csv_parsers")


def _load_registry(force: bool = False) -> list:
    """Load (or reload) the list of (toml_dict, py_path) from csv_parsers/.

    Uses per-file mtime tracking instead of directory mtime so that editing
    a TOML file on Windows (where directory mtime is not updated on content
    changes, only on file creation/deletion) is correctly detected.
    """
    global _parser_registry, _registry_mtimes

    if tomllib is None:
        return []

    pdir = _parsers_dir()
    if not os.path.isdir(pdir):
        return []

    # Collect current mtime of every .toml in the directory
    try:
        current_mtimes = {
            os.path.join(pdir, f): os.path.getmtime(os.path.join(pdir, f))
            for f in sorted(os.listdir(pdir)) if f.endswith(".toml")
        }
    except OSError:
        current_mtimes = {}

    if not force and _parser_registry is not None and current_mtimes == _registry_mtimes:
        return _parser_registry

    registry = []
    for toml_path in sorted(current_mtimes.keys()):
        fname = os.path.basename(toml_path)
        try:
            with open(toml_path, "rb") as fh:
                data = tomllib.load(fh)
        except Exception as e:
            print(f"[csv_detector] Failed to load {fname}: {e}")
            continue

        py_name = data.get("parser", "")
        if not py_name:
            # Derive from toml filename
            py_name = os.path.splitext(fname)[0] + ".py"

        py_path = os.path.join(os.path.dirname(toml_path), py_name)
        if not os.path.isfile(py_path):
            py_path = None

        registry.append((data, py_path))

    registry.sort(key=lambda x: -x[0].get("priority", 0))  # highest priority first
    _parser_registry = registry
    _registry_mtimes = current_mtimes
    return registry


def _sniff_delimiter(lines: list) -> str:
    """Quick delimiter sniff on the first few lines."""
    sample = "".join(lines[:10])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t |")
        return dialect.delimiter
    except csv.Error:
        return ","


def _split_line(line: str, delimiter: str) -> list:
    """Split a single CSV line; strip each field."""
    try:
        return [f.strip() for f in next(csv.reader([line], delimiter=delimiter))]
    except Exception:
        return [line.strip()]


def _eval_rule(rule: dict, lines: list, delimiter: str) -> bool:
    """Evaluate a single detection rule against the file lines."""
    # --- Determine which line(s) to check ---
    if "line" in rule:
        idx = rule["line"]
        if idx >= len(lines):
            return False
        candidate_lines = [lines[idx]]
    elif "line_search" in rule:
        n = rule["line_search"]
        candidate_lines = lines[:n]
    else:
        # No location specified → check first line as default
        candidate_lines = [lines[0]] if lines else []

    # --- Match parameters ---
    field_idx   = rule.get("field", None)
    match_eq    = rule.get("equals", None)
    match_sub   = rule.get("contains", None)
    match_pat   = rule.get("pattern", None)
    case_sens   = rule.get("case_sensitive", False)

    for raw_line in candidate_lines:
        raw_line = raw_line.rstrip("\n\r")

        if field_idx is not None:
            fields = _split_line(raw_line, delimiter)
            text = fields[field_idx] if field_idx < len(fields) else ""
        else:
            text = raw_line.strip()

        if match_eq is not None:
            a = text if case_sens else text.lower()
            b = match_eq if case_sens else match_eq.lower()
            if a == b:
                return True

        if match_sub is not None:
            a = text if case_sens else text.lower()
            b = match_sub if case_sens else match_sub.lower()
            if b in a:
                return True

        if match_pat is not None:
            flags = 0 if case_sens else re.IGNORECASE
            if re.search(match_pat, text, flags):
                return True

    return False


def _eval_detection_group(group: dict, lines: list, delimiter: str) -> bool:
    """
    Evaluate one [[detection]] group.
    strategy = "all" (default): every rule must pass.
    strategy = "any": at least one rule must pass.
    """
    rules = group.get("rules", [])
    if not rules:
        return False

    strategy = group.get("strategy", "all").lower()
    results = [_eval_rule(r, lines, delimiter) for r in rules]

    if strategy == "any":
        return any(results)
    return all(results)   # "all" is default


def _load_plugin(py_path: str):
    """Dynamically import a parser plugin .py file."""
    mod_name = "csv_parsers." + os.path.splitext(os.path.basename(py_path))[0]
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, py_path)
    mod  = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def detect_parser(all_lines: list):
    """
    Given the raw lines of a CSV file, return the best matching parser module
    (a Python module with a parse() function), or None if no match.

    all_lines: list of str, typically the complete file or at least the first
               MAX_DETECTION_LINES lines.
    """
    if tomllib is None:
        return None

    registry = _load_registry()
    if not registry:
        return None

    lines = all_lines[:MAX_DETECTION_LINES]
    delimiter = _sniff_delimiter(lines)

    candidates = []   # (priority, name, py_path)

    for toml_data, py_path in registry:
        name     = toml_data.get("name", "")
        priority = toml_data.get("priority", 0)
        adv      = toml_data.get("advanced_detection", False)

        # --- TOML rule evaluation ---
        detection_groups = toml_data.get("detection", [])

        # Normalise: TOML may give a single table or a list of tables
        if isinstance(detection_groups, dict):
            detection_groups = [detection_groups]

        toml_matched = False
        if detection_groups:
            # Parser matches if ANY detection group passes
            toml_matched = any(
                _eval_detection_group(g, lines, delimiter)
                for g in detection_groups
            )

        # --- Python detect() override ---
        if adv and py_path:
            try:
                mod = _load_plugin(py_path)
                if hasattr(mod, "detect"):
                    toml_matched = mod.detect(lines)
            except Exception as e:
                print(f"[csv_detector] detect() error in {py_path}: {e}")
                toml_matched = False

        if toml_matched:
            candidates.append((priority, name, py_path))

    # If TOML approach found nothing, give every plugin with detect() a shot
    if not candidates:
        for toml_data, py_path in registry:
            if py_path is None:
                continue
            try:
                mod = _load_plugin(py_path)
                if not hasattr(mod, "detect"):
                    continue
                if mod.detect(lines):
                    candidates.append((
                        toml_data.get("priority", 0),
                        toml_data.get("name", ""),
                        py_path,
                    ))
            except Exception:
                pass

    if not candidates:
        return None

    # Pick highest priority; alphabetical tie-break
    candidates.sort(key=lambda x: (-x[0], x[1]))
    _, _, best_py = candidates[0]

    if best_py is None:
        return None

    try:
        return _load_plugin(best_py)
    except Exception as e:
        print(f"[csv_detector] Failed to load parser {best_py}: {e}")
        return None
