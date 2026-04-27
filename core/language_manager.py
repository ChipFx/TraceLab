"""
core/language_manager.py

Two responsibilities:

1. TOML loader — resolves language data files (notice types, colour sets,
   language metadata) from ./languages/<lang>/, falling back to en/ when a
   file is missing or the language is disabled.

2. Qt translator manager — installs/uninstalls a QTranslator for the active
   language so that all QCoreApplication.translate() / tr() calls in the UI
   automatically return the right text.

   For the source language (English) no .qm file is needed: Qt returns the
   source string verbatim when no translator is installed.  .qm files are
   only required for non-English languages and are placed in translations/.

Usage (module-level singleton, init once at startup):

    from core.language_manager import get_language_manager
    lm = get_language_manager("en")          # init once, installs Qt translator
    data = lm.load("notice_bar.toml")        # load any TOML by filename

Language switch at runtime (e.g. from a future language menu):

    lm.set_language("nl")                    # swaps QTranslator + flushes cache
    # post QEvent::LanguageChange to top-level widgets to retranslate their UI
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
_TRANSLATIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "translations"
)
_FALLBACK_LANG = "en"


class LanguageManager:
    """Resolves language data files and manages the Qt string translator."""

    def __init__(self, lang: str = _FALLBACK_LANG):
        self._lang: str = _FALLBACK_LANG
        self._cache: dict[str, dict] = {}
        self._qt_translator = None          # QTranslator | None
        self.set_language(lang)

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def lang(self) -> str:
        return self._lang

    def set_language(self, lang: str):
        """Switch active language.

        * Validates that the language folder exists and _menu.toml has
          enabled = true (or is absent).
        * Flushes the TOML cache.
        * Installs a QTranslator for the new language (if a .qm file exists).
          For English (the source language) no translator is needed.
        * Safe to call before QApplication exists — translator install is
          skipped silently and can be retried by calling
          install_qt_translator() once QApplication is up.
        """
        resolved = self._resolve_lang(lang)
        if resolved == self._lang and self._cache:
            return                          # nothing to do

        self._lang = resolved
        self._cache.clear()
        self._install_qt_translator()

    def install_qt_translator(self):
        """Explicitly (re)install the Qt translator for the current language.

        Call this from main() after QApplication is created if set_language()
        was called before QApplication existed.
        """
        self._install_qt_translator()

    def load(self, filename: str) -> dict:
        """Return parsed TOML dict for *filename* in the active language folder.

        Falls back to 'en/' if the file is absent in the active language.
        Returns {} if neither is found or parseable.
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
        """Force re-read of all TOML files on next access."""
        self._cache.clear()

    # ── Internal: language resolution ────────────────────────────────────────

    def _resolve_lang(self, lang: str) -> str:
        """Return *lang* if it is valid and enabled, else _FALLBACK_LANG."""
        if lang == _FALLBACK_LANG:
            return _FALLBACK_LANG

        lang_dir = os.path.join(_LANGUAGES_DIR, lang)
        if not os.path.isdir(lang_dir):
            return _FALLBACK_LANG

        menu_file = os.path.join(lang_dir, "_menu.toml")
        if os.path.isfile(menu_file):
            try:
                with open(menu_file, "rb") as fh:
                    meta = tomllib.load(fh)
                if not meta.get("enabled", True):
                    return _FALLBACK_LANG
            except Exception:
                return _FALLBACK_LANG

        return lang

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

    # ── Internal: Qt translator ───────────────────────────────────────────────

    def _install_qt_translator(self):
        """Install (or remove) the QTranslator for self._lang.

        For the source language (en) we uninstall any existing translator so
        that Qt returns source strings verbatim — no .qm file required.

        For other languages we look for  translations/<lang>.qm  and install
        it.  If the file is not found we still uninstall the old translator so
        the app stays in a defined state (source strings visible).
        """
        try:
            from PyQt6.QtCore import QCoreApplication, QTranslator
        except ImportError:
            return

        app = QCoreApplication.instance()
        if app is None:
            return                          # QApplication not yet created

        # Remove previous translator
        if self._qt_translator is not None:
            app.removeTranslator(self._qt_translator)
            self._qt_translator = None

        # English = source language: no translator needed
        if self._lang == _FALLBACK_LANG:
            return

        qm_path = os.path.join(_TRANSLATIONS_DIR, f"{self._lang}.qm")
        if not os.path.isfile(qm_path):
            return                          # no .qm yet — fall back to source strings

        translator = QTranslator(app)
        if translator.load(qm_path):
            app.installTranslator(translator)
            self._qt_translator = translator


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
