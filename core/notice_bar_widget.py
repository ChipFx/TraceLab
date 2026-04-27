"""
core/notice_bar_widget.py

Right-anchored notice bar widget for the main window's QStatusBar.

Responsibilities:
  - Reads notice text and type from   languages/<lang>/notice_bar.toml
  - Reads colour sets from            languages/<lang>/_colour.toml
  - Listens to NoticeManager.notices_changed
  - Cycles through active notices at a configurable interval
  - Hides itself completely when there are no active notices
  - Per-notice fg/bg/acc_override fields in the TOML take precedence over
    the type's colour set

Cycling interval:
  Set via set_cycle_interval(seconds).  Sourced from settings.json key
  "notice_cycle_interval_s" (default 3.0) — not exposed in the UI.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QSizePolicy
from PyQt6.QtCore import QTimer, Qt

from core.notice_manager import NoticeManager
from core.language_manager import LanguageManager


# Fallback colours when TOML is missing or a type is unknown
_FALLBACK_FG     = "#e0e0e0"
_FALLBACK_BG     = "#333333"
_FALLBACK_ACCENT = "#888888"


class NoticeBarWidget(QWidget):
    """Cycling notice display, right-anchored in the status bar."""

    def __init__(
        self,
        notice_manager: NoticeManager,
        language_manager: LanguageManager,
        cycle_interval_s: float = 3.0,
        parent=None,
    ):
        super().__init__(parent)
        self._nm = notice_manager
        self._lm = language_manager
        self._cycle_interval_s = max(0.1, float(cycle_interval_s))
        self._cycle_idx = 0

        self._notices: dict[str, dict] = {}   # key -> notice dict from TOML
        self._colours: dict[str, dict] = {}   # type -> colour dict from TOML
        self._reload_config()

        # ── Layout ────────────────────────────────────────────────────────────
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._lbl = QLabel()
        self._lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        layout.addWidget(self._lbl)

        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(80)

        # ── Cycle timer ───────────────────────────────────────────────────────
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance_cycle)

        # ── Connect to notice manager ─────────────────────────────────────────
        self._nm.notices_changed.connect(self._on_notices_changed)

        # Initial state
        self._update_display()

    # ── Public API ────────────────────────────────────────────────────────────

    def set_cycle_interval(self, seconds: float):
        """Change the cycling interval (seconds, minimum 0.1)."""
        self._cycle_interval_s = max(0.1, float(seconds))
        if self._timer.isActive():
            self._timer.setInterval(int(self._cycle_interval_s * 1000))

    def reload_language(self):
        """Re-read TOML files after a language change."""
        self._lm.invalidate_cache()
        self._reload_config()
        self._update_display()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _reload_config(self):
        notice_data = self._lm.load("notice_bar.toml")
        colour_data = self._lm.load("_colour.toml")

        self._notices = {}
        for key, val in notice_data.items():
            if key.startswith("notice_") and isinstance(val, dict):
                notice_key = key[len("notice_"):]
                self._notices[notice_key] = val

        self._colours = colour_data

    def _on_notices_changed(self):
        active = self._nm.active_keys()

        # Clamp / reset the cycle index
        if len(active) <= 1:
            self._cycle_idx = 0
        else:
            self._cycle_idx = self._cycle_idx % len(active)

        # Start or stop the cycle timer
        if len(active) > 1:
            if not self._timer.isActive():
                self._timer.start(int(self._cycle_interval_s * 1000))
        else:
            self._timer.stop()

        self._update_display()

    def _advance_cycle(self):
        active = self._nm.active_keys()
        if len(active) > 1:
            self._cycle_idx = (self._cycle_idx + 1) % len(active)
        self._update_display()

    def _update_display(self):
        active = self._nm.active_keys()
        if not active:
            self.hide()
            return

        self.show()

        key   = active[self._cycle_idx % len(active)]
        count = len(active)

        notice = self._notices.get(key, {})
        text   = notice.get("string", key)
        ntype  = notice.get("type", "user_notice")

        # Resolve colours: per-notice override > type set > fallback
        cset  = self._colours.get(ntype, {})
        fg    = notice.get("fg_override")  or cset.get("fg",     _FALLBACK_FG)
        bg    = notice.get("bg_override")  or cset.get("bg",     _FALLBACK_BG)

        # Count indicator when multiple notices are cycling
        prefix = f"[{self._cycle_idx + 1}/{count}] " if count > 1 else ""

        self._lbl.setText(f"{prefix}{text}")
        self._lbl.setStyleSheet(
            f"color: {fg}; background-color: {bg}; "
            f"padding: 2px 10px; font-weight: bold;"
        )
        self.setStyleSheet(f"background-color: {bg};")
