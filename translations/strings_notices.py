"""
translations/strings_notices.py  —  pylupdate6 extraction stub for notices

PURPOSE
-------
This file is NOT imported at runtime.  It exists solely so that pylupdate6
can extract notice-bar strings into the .ts files via static analysis.

Notice strings live in  languages/<lang>/notice_bar.toml  (the `source`
field) rather than in normal source code.  pylupdate6 cannot see TOML files,
so every notice source string must also appear here as an explicit
QCoreApplication.translate() call.

HOW TO KEEP THIS IN SYNC
------------------------
When you add a new notice to notice_bar.toml:
  1. Add its `source` string to this file using the same context ("notices")
     and include an extracomment for the translator.
  2. Run:  scripts/build_translations.py update
     pylupdate6 will add the new entry to all existing .ts files.

The `source` field in the TOML and the first argument to translate() below
MUST match exactly (byte-for-byte) for Qt to link them at runtime.
"""

# fmt: off  (keep one call per line for pylupdate6 readability)
from PyQt6.QtCore import QCoreApplication as _QCA

# ── notices context ────────────────────────────────────────────────────────────
# Each entry corresponds to one [notice_KEY] section in notice_bar.toml.

#: Notice bar: Real Time axis mode is active but no trace has a wall-clock
#: anchor (t0_wall_clock). The axes fall back to standard time display.
#: Keep this short — it appears in a narrow status bar widget.
_QCA.translate("notices", "Real Time mode enabled without known date")
