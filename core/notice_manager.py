"""
core/notice_manager.py

Process-wide singleton that tracks active notices for the notice bar.

Any component that wants to raise or clear a user-visible notice imports
get_notice_manager() and calls attach() / detach():

    from core.notice_manager import get_notice_manager
    nm = get_notice_manager()
    nm.attach("realtime_no_date")   # show notice
    nm.detach("realtime_no_date")   # hide notice

The NoticeBarWidget listens to notices_changed and updates its display.
Notice keys are arbitrary short strings; their display text and colours
are resolved by the NoticeBarWidget via the language/colour TOML files.
"""

from __future__ import annotations

from typing import Optional
from PyQt6.QtCore import QObject, pyqtSignal


class NoticeManager(QObject):
    """Tracks the set of currently active notices and signals on changes."""

    notices_changed = pyqtSignal()   # emitted whenever the active set changes

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active: list[str] = []

    # ── Public API ────────────────────────────────────────────────────────────

    def attach(self, key: str):
        """Make *key* active.  No-op if already active."""
        if key not in self._active:
            self._active.append(key)
            self.notices_changed.emit()

    def detach(self, key: str):
        """Remove *key* from active set.  No-op if not present."""
        if key in self._active:
            self._active.remove(key)
            self.notices_changed.emit()

    def set(self, key: str, active: bool):
        """Convenience: attach when *active* is True, detach when False."""
        if active:
            self.attach(key)
        else:
            self.detach(key)

    def active_keys(self) -> list[str]:
        return list(self._active)

    def has_notices(self) -> bool:
        return bool(self._active)

    def is_active(self, key: str) -> bool:
        return key in self._active


# ── Module-level singleton ────────────────────────────────────────────────────

_instance: Optional[NoticeManager] = None


def get_notice_manager() -> NoticeManager:
    """Return (or create) the process-wide NoticeManager singleton."""
    global _instance
    if _instance is None:
        _instance = NoticeManager()
    return _instance
