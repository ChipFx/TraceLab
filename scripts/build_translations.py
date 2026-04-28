#!/usr/bin/env python3
"""
scripts/build_translations.py  —  ChipFX TraceLab translation build helper

COMMANDS
--------
  update                  Re-scan source for new tr() / translate() calls
                          and update all existing .ts files.

  release [lang ...]      Compile .ts → .qm for the listed languages
                          (or all non-en .ts files if none specified).

  all [lang ...]          update + release in one step.

EXAMPLES
--------
  python scripts/build_translations.py update
  python scripts/build_translations.py release nl de
  python scripts/build_translations.py all

ADDING A NEW LANGUAGE
---------------------
  1. Copy  translations/en.ts  to  translations/<lang>.ts
  2. Set   <TS language="<locale>">  in the new file (e.g. "nl_NL")
  3. Create  languages/<lang>/  with at minimum  _menu.toml  and  _colour.toml
     (copy from languages/en/ and adjust)
  4. Translate strings in Qt Linguist or your favourite editor
  5. Run:  python scripts/build_translations.py release <lang>
  6. Test:  add  "language": "<lang>"  to settings.json and launch the app

NOTES ON lrelease
-----------------
  lrelease is part of a Qt installation and is NOT bundled with PyQt6.
  If it is not in your PATH, this script searches a few common locations.

  To install:
    Windows: install Qt via https://www.qt.io/download-open-source/
             and add  <Qt>/<version>/msvc2022_64/bin/  (or mingw) to PATH.
    Linux:   sudo apt install qt6-tools-dev-tools   # or equivalent
    macOS:   brew install qt  (then  export PATH=$(brew --prefix qt)/bin:$PATH)

  Alternatively, on any platform:
    pip install pyqt6-tools    # third-party, may lag behind PyQt6 releases
"""

from __future__ import annotations

import os
import sys
import glob
import shutil
import subprocess

# ── Paths ─────────────────────────────────────────────────────────────────────
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TRANS_DIR = os.path.join(_REPO_ROOT, "translations")
_CORE_DIR  = os.path.join(_REPO_ROOT, "core")
_PLUGINS_DIR = os.path.join(_REPO_ROOT, "plugins")
_CSV_DIR   = os.path.join(_REPO_ROOT, "csv_parsers")

# Source files pylupdate6 should scan (including the extraction stubs)
_SOURCE_PATTERNS = [
    os.path.join(_REPO_ROOT, "main.py"),
    os.path.join(_CORE_DIR,  "*.py"),
    os.path.join(_PLUGINS_DIR, "*.py"),
    os.path.join(_CSV_DIR,   "*.py"),
    os.path.join(_TRANS_DIR, "strings_*.py"),   # extraction stubs
]


# ── Tool discovery ─────────────────────────────────────────────────────────────

def _find_tool(name: str) -> str | None:
    """Return full path to *name* or None."""
    # 1. Already in PATH
    p = shutil.which(name)
    if p:
        return p

    # 2. Common Windows Qt installation paths
    if sys.platform == "win32":
        for qt_base in [r"C:\Qt", r"C:\Program Files\Qt"]:
            if not os.path.isdir(qt_base):
                continue
            for entry in sorted(os.listdir(qt_base), reverse=True):
                for compiler in ["msvc2022_64", "msvc2019_64", "mingw_64", "mingw81_64"]:
                    candidate = os.path.join(qt_base, entry, compiler, "bin", f"{name}.exe")
                    if os.path.isfile(candidate):
                        return candidate

    # 3. pyqt6-tools shim (if installed)
    try:
        import pyqt6_tools
        candidate = os.path.join(os.path.dirname(pyqt6_tools.__file__), name)
        if os.path.isfile(candidate):
            return candidate
    except ImportError:
        pass

    return None


def _require_tool(name: str) -> str:
    path = _find_tool(name)
    if path:
        return path
    print(f"\n  ERROR: '{name}' not found.")
    print(f"  See the NOTES ON LRELEASE section at the top of this script.")
    sys.exit(1)


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_update():
    """Run pylupdate6 to refresh all .ts files from source."""
    pylupdate = _require_tool("pylupdate6")

    # Expand source file globs
    sources: list[str] = []
    for pat in _SOURCE_PATTERNS:
        sources.extend(glob.glob(pat))
    sources = sorted(set(sources))

    # Find all .ts files
    ts_files = sorted(glob.glob(os.path.join(_TRANS_DIR, "*.ts")))
    if not ts_files:
        print("No .ts files found in translations/. Nothing to update.")
        return

    for ts in ts_files:
        lang = os.path.splitext(os.path.basename(ts))[0]
        print(f"  Updating {os.path.relpath(ts, _REPO_ROOT)} …")
        cmd = [pylupdate, "-source-language", "en", *sources, "-ts", ts]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"    FAILED:\n{result.stderr}")
        else:
            if result.stdout.strip():
                print(f"    {result.stdout.strip()}")
            print(f"    OK")


def cmd_release(langs: list[str]):
    """Compile .ts → .qm for the given languages (or all non-en .ts files)."""
    lrelease = _require_tool("lrelease")

    if langs:
        ts_files = [os.path.join(_TRANS_DIR, f"{lang}.ts") for lang in langs]
    else:
        ts_files = [
            f for f in sorted(glob.glob(os.path.join(_TRANS_DIR, "*.ts")))
            if os.path.splitext(os.path.basename(f))[0] != "en"
        ]

    if not ts_files:
        print("No non-English .ts files found. Nothing to compile.")
        return

    for ts in ts_files:
        lang = os.path.splitext(os.path.basename(ts))[0]
        qm   = os.path.join(_TRANS_DIR, f"{lang}.qm")
        print(f"  Compiling {os.path.relpath(ts, _REPO_ROOT)} → {os.path.relpath(qm, _REPO_ROOT)} …")
        result = subprocess.run([lrelease, ts, "-qm", qm], capture_output=True, text=True)
        if result.returncode != 0:
            print(f"    FAILED:\n{result.stderr}")
        else:
            print(f"    OK  ({os.path.getsize(qm):,} bytes)")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__)
        return

    command = args[0].lower()
    rest    = args[1:]

    if command == "update":
        print("Updating .ts files …")
        cmd_update()

    elif command == "release":
        print("Compiling .qm files …")
        cmd_release(rest)

    elif command == "all":
        print("Updating .ts files …")
        cmd_update()
        print("\nCompiling .qm files …")
        cmd_release(rest)

    else:
        print(f"Unknown command: {command!r}")
        print("Use  update | release | all")
        sys.exit(1)

    print("\nDone.")


if __name__ == "__main__":
    main()
