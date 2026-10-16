"""Module-level helpers shared by MainWindow and its mixins.

Re-exported from main_window, which is where tests import them from."""

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame

from .. import intake


# Keys of user-saved presets in _preset_buttons, kept apart from the
# built-in PRESETS keys so a user can name a preset "quick_clean" safely.
CUSTOM_PRESET_PREFIX = "custom:"
CONSOLE_MAX_LINES = 20000
HISTORY_MAX_ROWS = 5
# Dashboard one-click complaints (ids in Modules/symptoms.yaml); a missing id is skipped.
DASHBOARD_SYMPTOM_CHIPS = ("no_internet", "slow_pc", "printer_not_working", "outlook_wont_open")


# Console severity by keyword (research-design-additions item 7). Command
# output is free-form PowerShell text, so this sniffs words, not a format.
# ponytail: keyword heuristic - a line like "error-free" still tints red;
# a real per-line severity would need every command to emit PFJSON.
_CONSOLE_ERROR_RE = re.compile(
    r"\b(errors?|exception|failed|failure|fatal|timed out|access is denied|chyba|zlyhal\w*)\b"
    r"|FullyQualifiedErrorId|CategoryInfo",
    re.IGNORECASE,
)
_CONSOLE_NO_ERROR_RE = re.compile(r"\b(no|0|zero|without|not any)\s+(errors?|failures?|exceptions?)\b", re.IGNORECASE)
_CONSOLE_WARNING_RE = re.compile(r"\b(warning|cancell?ed|upozornenie|varovanie)\b", re.IGNORECASE)
_CONSOLE_SUCCESS_RE = re.compile(r"\b(success|successfully|succeeded|úspešne)\b", re.IGNORECASE)


def _console_severity(line: str) -> str | None:
    """"error", "warning", "success" or None (plain) for one console line."""
    if _CONSOLE_ERROR_RE.search(line) and not _CONSOLE_NO_ERROR_RE.search(line):
        return "error"
    if _CONSOLE_WARNING_RE.search(line):
        return "warning"
    if _CONSOLE_SUCCESS_RE.search(line):
        return "success"
    return None


def _elapsed_text(seconds: float) -> str:
    """"3.2s" for a short action, "0:02:05" once it runs past a minute."""
    return f"{seconds:.1f}s" if seconds < 60 else intake.format_clock(seconds)


class _DashboardTile(QFrame):
    """Dashboard category tile. Was a QFrame with a patched mousePressEvent,
    unreachable by Tab and silent for screen readers
    (research-accessibility.md "keyboard-only operability")."""

    activated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Picked up by the "tile" focus rule in style.py.
        self.setProperty("tile", "true")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit()
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.activated.emit()
            return
        super().keyPressEvent(event)


# Banner text for each step of an in-app update (see _begin_update_step).
_UPDATE_PHASE_KEYS = {
    "download": "update_downloading",
    "stage": "update_preparing",
    "launch": "update_starting",
}


def _ignore_deleted(func):
    """Slot wrapper for a panel's closures: the runner is parented to the
    window (never to the panel, which a language toggle deletes), so its
    signals can still arrive after the panel's widgets are gone. The audit
    entry in each closure is written before any widget is touched."""
    def wrapper(*args):
        try:
            return func(*args)
        except RuntimeError as exc:
            if "already deleted" not in str(exc):
                raise
            return None
    return wrapper


def _thread_running(runner) -> bool:
    # A finished QThread may already be deleteLater'd - its wrapper then
    # raises RuntimeError, which just means "not running".
    try:
        return bool(runner.isRunning())
    except (RuntimeError, AttributeError):
        return False
