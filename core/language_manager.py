"""
core/language_manager.py

Loads TOML files from the active language folder under ./languages/<lang>/.
Falls back to the built-in Application English (languages/en/) when:
  - The requested language folder does not exist
  - The language's _menu.toml sets enabled = false
  - Any file is missing from the requested language (per-file fallback)

Usage (module-level singleton):

    from core.language_manager import get_language_manager
    lm = get_language_manager("en")       # init once at startup
    data = lm.load("notice_bar.toml")     # returns a dict

All file paths are constructed with os.path so they handle Unicode folder/file
names on any platform (Windows / macOS / Linux).
"""

from __future__ import annotations

import os
from typing import Optional

try:
    import tomllib                        # Python 3.11+
except ImportError:
    import tomli as tomllib               # type: ignore[no-redef]


_LANGUAGES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "languages"
)
_FALLBACK_LANG = "en"


class LanguageManager:
    """Resolves language data files and exposes them as parsed dicts."""

    def __init__(self, lang: str = _FALLBACK_LANG):
        self._lang = _FALLBACK_LANG
        self._cache: dict[str, dict] = {}
        self.set_language(lang)

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def lang(self) -> str:
        return self._lang

    def set_language(self, lang: str):
        """Switch to a different language (with fallback to 'en' on failure)."""
        if lang == _FALLBACK_LANG:
            self._lang = _FALLBACK_LANG
            self._cache.clear()
            return

        lang_dir = os.path.join(_LANGUAGES_DIR, lang)
        if not os.path.isdir(lang_dir):
            self._lang = _FALLBACK_LANG
            self._cache.clear()
            return

        menu_file = os.path.join(lang_dir, "_menu.toml")
        if os.path.isfile(menu_file):
            try:
                with open(menu_file, "rb") as fh:
                    meta = tomllib.load(fh)
                if not meta.get("enabled", True):
                    self._lang = _FALLBACK_LANG
                    self._cache.clear()
                    return
            except Exception:
                self._lang = _FALLBACK_LANG
                self._cache.clear()
                return

        self._lang = lang
        self._cache.clear()

    def load(self, filename: str) -> dict:
        """Return parsed TOML dict for *filename* in the active language folder.

        If the file does not exist in the active language, tries the 'en'
        fallback.  Returns an empty dict if neither is found or parseable.
        """
        if filename in self._cache:
            return self._cache[filename]

        result = self._try_load(self._lang, filename)
        if result is None and self._lang != _FALLBACK_LANG:
            result = self._try_load(_FALLBACK_LANG, filename)
        if result is None:
            result = {}

        self._cache[filename] = result
        return result

    def invalidate_cache(self):
        """Force re-read of all files on next access."""
        self._cache.clear()

    # ── Internal ──────────────────────────────────────────────────────────────

    @staticmethod
    def _try_load(lang: str, filename: str) -> Optional[dict]:
        path = os.path.join(_LANGUAGES_DIR, lang, filename)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "rb") as fh:
                return tomllib.load(fh)
        except Exception:
            return None


# ── Module-level singleton ────────────────────────────────────────────────────

_instance: Optional[LanguageManager] = None


def get_language_manager(lang: str = _FALLBACK_LANG) -> LanguageManager:
    """Return (or create) the process-wide LanguageManager singleton.

    Pass *lang* only on the first call (app startup); subsequent calls
    return the existing instance regardless of the argument.
    """
    global _instance
    if _instance is None:
        _instance = LanguageManager(lang)
    return _instance
