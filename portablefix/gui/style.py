"""Cyberpunk / power-user terminal QSS theme for the PortableFix main window.

Design direction: near-black dashboard chrome, one signature duo-accent
(electric cyan + magenta) for structural/interactive UI, monospace readouts
on anything numeric or status-like. Cards, buttons and the category list use
soft/pill rounding (rounded-consumer-app feel, modeled on Ashampoo
WinOptimizer / IObit) rather than the sharp corners of an earlier pass.
Risk-level colors stay their own neon family so color-coding meaning is
never confused with the brand accent. SAFE badges are an outline (not
filled) since almost every action is SAFE - a filled neon pill on every
single row drowned out the handful of rows that actually need attention
(MODERATE/DESTRUCTIVE/REQUIRES_REBOOT).
"""

import ctypes
import sys

RISK_COLORS = {
    "SAFE": "#39ff88",
    "MODERATE": "#ffb020",
    "DESTRUCTIVE": "#ff2d6f",
    "REQUIRES_REBOOT": "#b26bff",
}

STYLE = """
QMainWindow, QWidget#central {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #0b0e14, stop:1 #090b10);
}
QWidget {
    color: #d6e2f0;
    font-family: 'Segoe UI';
    font-size: 10pt;
}

QLabel#appTitle {
    font-family: 'Sora', 'Segoe UI Semibold', 'Segoe UI';
    font-size: 14pt;
    font-weight: 700;
    color: #2fe6ff;
}
QLabel#adminPill {
    border-radius: 10px;
    padding: 3px 12px;
    font-family: 'Consolas', 'Cascadia Mono';
    font-weight: bold;
    font-size: 9pt;
}
QLabel#adminPill[admin="true"] {
    background-color: #39ff88;
    color: #06080c;
}
QLabel#adminPill[admin="false"] {
    background-color: #ffb020;
    color: #06080c;
}

QListWidget#categoryList {
    background-color: #10141c;
    border: 1px solid #1c2530;
    border-radius: 2px;
    padding: 6px;
    outline: none;
}
QListWidget#categoryList::item {
    padding: 10px 12px;
    border-radius: 14px;
    margin: 2px 4px;
}
QListWidget#categoryList::item:hover {
    background-color: rgba(255, 255, 255, 10);
}
QListWidget#categoryList::item:selected {
    background-color: rgba(47, 230, 255, 32);
    color: #8ff2ff;
    font-weight: bold;
}
QListWidget#categoryList::item:focus {
    outline: 2px solid #2fe6ff;
}

QScrollArea {
    border: none;
    background: transparent;
}
QScrollArea > QWidget > QWidget {
    background: transparent;
}
QScrollBar:vertical {
    background: #0b0e14;
    width: 10px;
    border-radius: 2px;
}
QScrollBar::handle:vertical {
    background: #232d3a;
    border-radius: 2px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: #2fe6ff;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

QFrame#actionCard {
    background-color: #10141c;
    border: 1px solid #1c2530;
    border-top: 1px solid #26323f;
    border-radius: 14px;
}
QLabel#cardHeading {
    font-family: 'Sora', 'Segoe UI Semibold', 'Segoe UI';
    font-size: 11pt;
    font-weight: 600;
    color: #2fe6ff;
    padding: 2px 0;
}

QCheckBox {
    spacing: 8px;
    padding: 4px 0;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 2px solid #232d3a;
    background: #06080c;
}
QCheckBox::indicator:hover {
    border-color: #2fe6ff;
}
QCheckBox::indicator:checked {
    background-color: #2fe6ff;
    border-color: #2fe6ff;
}
QCheckBox::indicator:focus {
    outline: 2px solid #2fe6ff;
}

QLabel#riskBadge {
    border-radius: 8px;
    padding: 1px 8px;
    font-family: 'Consolas', 'Cascadia Mono';
    font-size: 8pt;
    font-weight: bold;
}
QLabel#riskBadge[risk="SAFE"] {
    background: transparent;
    border: 1px solid rgba(57, 255, 136, 110);
    color: #39ff88;
    font-weight: normal;
}
QLabel#riskBadge[risk="MODERATE"] { background-color: #ffb020; color: #06080c; }
QLabel#riskBadge[risk="DESTRUCTIVE"] { background-color: #ff2d6f; color: #06080c; }
QLabel#riskBadge[risk="REQUIRES_REBOOT"] { background-color: #b26bff; color: #06080c; }
QLabel#riskBadge[risk="CUSTOM"] { background: transparent; border: 1px solid #5ee6ff; color: #5ee6ff; }

QPushButton {
    background-color: #141a24;
    border: 1px solid #232d3a;
    border-radius: 10px;
    padding: 7px 16px;
}
QPushButton:hover {
    background-color: #1a212d;
    border-color: #2fe6ff;
}
QPushButton:focus {
    outline: 2px solid #2fe6ff;
    outline-offset: 1px;
}
QPushButton#runButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #00d9ff, stop:1 #00ffa3);
    border: none;
    border-radius: 18px;
    color: #06080c;
    font-weight: bold;
    padding: 9px 24px;
}
QPushButton#runButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #4ee6ff, stop:1 #4dffc0);
}
QPushButton#runButton:disabled {
    background: #232d3a;
    color: #4b5568;
}
QPushButton#cancelButton {
    background-color: #10141c;
    color: #ff2d6f;
    border: 1px solid #ff2d6f;
    border-radius: 18px;
    padding: 9px 18px;
}
QPushButton#cancelButton:hover {
    background-color: rgba(255, 45, 111, 20);
}
QPushButton#cancelButton:disabled {
    background-color: #10141c;
    color: #4b5568;
    border: 1px solid #232d3a;
}

QPushButton#selectionBtn {
    background-color: #141a24;
    border: 1px dashed #2a3542;
    border-radius: 10px;
    padding: 3px 10px;
    font-family: 'Consolas', 'Cascadia Mono';
    font-size: 8.5pt;
    color: #8a97a8;
}
QPushButton#selectionBtn:hover {
    border: 1px solid #2fe6ff;
    color: #2fe6ff;
    background-color: #182028;
}
QPushButton#selectionBtn[danger="true"]:enabled {
    border: 1px solid #ff2d6f;
    color: #ff2d6f;
}
QPushButton#selectionBtn[danger="true"]:enabled:hover {
    background: rgba(255, 45, 111, 20);
    border: 1px solid #ff2d6f;
    color: #ff2d6f;
}
QPushButton#selectionBtn[danger="true"]:disabled {
    border: 1px dashed #1a212d;
    color: #3a4250;
}
QPushButton#presetBtn {
    background-color: #141a24;
    border: 1px dashed #2a3542;
    border-radius: 10px;
    padding: 3px 10px;
    font-family: 'Consolas', 'Cascadia Mono';
    font-size: 8.5pt;
    color: #8a97a8;
}
QPushButton#presetBtn:hover {
    border: 1px solid #2fe6ff;
    color: #2fe6ff;
    background-color: #182028;
}
QPushButton#presetBtn:checked {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba(47, 230, 255, 45), stop:1 rgba(47, 230, 255, 5));
    border: 1px solid #2fe6ff;
    color: #8ff2ff;
    font-weight: bold;
}
/* Muted text is #7c8799, not the earlier #6b7686 (3.8:1 on #141a24):
   9pt text needs WCAG AA 4.5:1 on every card/button surface it sits on. */
QLabel#selectionScope {
    font-family: 'Consolas', 'Cascadia Mono';
    color: #7c8799;
    font-size: 9pt;
}

QLabel#actionStatus {
    font-family: 'Consolas', 'Cascadia Mono';
    font-size: 8pt;
    font-weight: bold;
}
QLabel#actionStatus[state="running"] { color: #ffb020; }
QLabel#actionStatus[state="ok"] { color: #39ff88; }
QLabel#actionStatus[state="fail"] { color: #ff2d6f; }

QToolButton#actionDetailToggle {
    background: transparent;
    border: 1px dashed #232d3a;
    border-radius: 2px;
    padding: 1px 6px;
    font-family: 'Consolas', 'Cascadia Mono';
    font-size: 9pt;
    color: #8a97a8;
}
QToolButton#actionDetailToggle:hover {
    border: 1px solid #2fe6ff;
    color: #2fe6ff;
}
QToolButton#actionDetailToggle:checked {
    color: #2fe6ff;
    border: 1px solid #2fe6ff;
}
/* QPushButton:focus doesn't match tool buttons - without this the
   per-action "details" toggle showed no keyboard focus at all. */
QToolButton:focus {
    outline: 2px solid #2fe6ff;
    border: 1px solid #2fe6ff;
}
/* Dashboard tiles are keyboard-focusable (Tab, then Enter/Space). */
QFrame#actionCard[tile="true"]:focus {
    border: 2px solid #2fe6ff;
}
QWidget#actionDetailPanel {
    background-color: #0b0e14;
    border: 1px solid #1c2530;
    border-radius: 2px;
}
QLabel#actionDetailDescription {
    color: #d6e2f0;
    font-size: 9pt;
}
QLabel#actionDetailLabel {
    font-family: 'Consolas', 'Cascadia Mono';
    font-size: 8pt;
    font-weight: bold;
    color: #7c8799;
}
QPlainTextEdit#actionDetailCommand {
    background-color: #06080c;
    border: 1px solid #1c2530;
    border-radius: 2px;
    padding: 6px;
    font-family: 'Cascadia Mono', 'Consolas';
    font-size: 8.5pt;
    color: #9fd9e8;
}

QLineEdit#searchBox {
    background-color: #06080c;
    border: 1px solid #232d3a;
    border-radius: 10px;
    padding: 5px 10px;
    font-family: 'Consolas', 'Cascadia Mono';
    color: #d6e2f0;
}
QLineEdit#searchBox:focus {
    border: 1px solid #2fe6ff;
}

QDialog {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #0b0e14, stop:1 #090b10);
}
QWidget#updateBanner {
    background-color: #10141c;
    border: 1px solid #ff2bd6;
    border-left: 3px solid #ff2bd6;
    border-radius: 2px;
}
QLabel#summaryHeader {
    font-family: 'Sora', 'Segoe UI Semibold', 'Segoe UI';
    font-size: 12pt;
    font-weight: 700;
    color: #2fe6ff;
}
QLabel#healthState {
    font-family: 'Sora', 'Segoe UI Semibold', 'Segoe UI';
    font-size: 11pt;
    font-weight: 700;
    color: #7c8799;
}
QLabel#healthState[state="ok"] { color: #39ff88; }
QLabel#healthState[state="attention"] { color: #ffb020; }
QLabel#healthState[state="critical"] { color: #ff2d6f; }
QLabel#summaryDryRunNote {
    font-family: 'Consolas', 'Cascadia Mono';
    color: #ffb020;
    font-weight: bold;
}
QLabel#summaryRow[ok="true"] { color: #39ff88; }
QLabel#summaryRow[ok="false"] { color: #ff2d6f; }
QLabel#summaryMetricName { color: #d6e2f0; font-size: 9pt; }
QLabel#summaryMetricDelta {
    font-family: 'Consolas', 'Cascadia Mono';
    font-size: 9pt;
    font-weight: bold;
    color: #7c8799;
}
QLabel#summaryMetricDelta[trend="good"] { color: #39ff88; }
QLabel#summaryMetricDelta[trend="bad"] { color: #ff2d6f; }

QPlainTextEdit#console {
    background-color: #06080c;
    border: 1px solid #1c2530;
    border-radius: 2px;
    padding: 8px;
    font-family: 'Cascadia Mono', 'Consolas';
    font-size: 9pt;
    color: #9fd9e8;
}
QSplitter::handle {
    background-color: #1c2530;
}
QSplitter::handle:vertical {
    height: 6px;
}
QSplitter::handle:hover {
    background-color: #2fe6ff;
}

QProgressBar#batchProgress {
    background-color: #06080c;
    border: 1px solid #1c2530;
    border-radius: 8px;
    text-align: center;
    font-family: 'Consolas', 'Cascadia Mono';
    font-weight: bold;
    font-size: 8.5pt;
    color: #8ff2ff;
    padding: 1px;
    min-height: 16px;
}
QProgressBar#batchProgress::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #00e5ff, stop:1 #ff2bd6);
    width: 8px;
    margin: 1px;
}

QLabel#countPill {
    border-radius: 9px;
    padding: 1px 9px;
    font-family: 'Consolas', 'Cascadia Mono';
    font-weight: bold;
    font-size: 8.5pt;
}
QLabel#countPill[state="ok"] {
    background-color: rgba(57, 255, 136, 30);
    color: #39ff88;
}
QLabel#countPill[state="warn"] {
    background-color: rgba(255, 176, 32, 30);
    color: #ffb020;
}
QLabel#countPill[state="idle"] {
    background-color: rgba(255, 255, 255, 8);
    /* Real content (the "0" before any analysis), not a disabled control -
       so it needs 4.5:1 like other text; #4b5568 was 2.5:1. */
    color: #7c8799;
}

QPushButton#panelBtn {
    background-color: #141a24;
    border: 1px solid #232d3a;
    border-radius: 10px;
    padding: 7px 12px;
    color: #c4d0de;
    font-size: 9.5pt;
}
QPushButton#panelBtn:hover {
    border-color: #2fe6ff;
    color: #2fe6ff;
    background-color: #182028;
}
QPushButton#panelBtn:disabled {
    color: #4b5568;
    border-color: #1a212d;
}

QLabel#wingetBanner {
    border-radius: 10px;
    padding: 8px 12px;
    font-size: 9pt;
}
QLabel#wingetBanner[state="warn"] {
    background-color: rgba(255, 176, 32, 22);
    border: 1px solid rgba(255, 176, 32, 90);
    color: #d6e2f0;
}
QLabel#targetUserBanner {
    background-color: rgba(255, 176, 32, 22);
    border: 1px solid rgba(255, 176, 32, 90);
    border-radius: 10px;
    padding: 8px 12px;
    color: #d6e2f0;
}
QLabel#wingetBanner[state="ok"] {
    background: transparent;
    border: none;
    color: #7c8799;
    font-family: 'Consolas', 'Cascadia Mono';
}

QComboBox {
    background-color: #06080c;
    border: 1px solid #232d3a;
    border-radius: 10px;
    padding: 4px 10px;
    font-family: 'Consolas', 'Cascadia Mono';
    color: #d6e2f0;
    min-width: 80px;
}
QComboBox:hover, QComboBox:focus {
    border: 1px solid #2fe6ff;
}
QComboBox:disabled {
    background-color: #0b0e14;
    border: 1px solid #1a212d;
    color: #4b5568;
}
QComboBox QAbstractItemView {
    background-color: #10141c;
    border: 1px solid #232d3a;
    selection-background-color: rgba(47, 230, 255, 45);
    selection-color: #8ff2ff;
    outline: none;
    padding: 4px;
}

QCheckBox:disabled {
    color: #4b5568;
}
QCheckBox::indicator:disabled {
    border-color: #1a212d;
    background: #0b0e14;
}

QToolTip {
    background-color: #10141c;
    color: #d6e2f0;
    border: 1px solid #2fe6ff;
    border-radius: 6px;
    padding: 6px 8px;
}

QStatusBar {
    background-color: #06080c;
    border-top: 1px solid #1c2530;
    color: #8a97a8;
    font-family: 'Consolas', 'Cascadia Mono';
    font-size: 9pt;
}
QStatusBar::item {
    border: none;
}

QMessageBox {
    background-color: #0b0e14;
}
QMessageBox QLabel {
    color: #d6e2f0;
}
QMessageBox QPushButton {
    min-width: 84px;
}

QScrollBar:horizontal {
    background: #0b0e14;
    height: 10px;
    border-radius: 2px;
}
QScrollBar::handle:horizontal {
    background: #232d3a;
    border-radius: 2px;
    min-width: 30px;
}
QScrollBar::handle:horizontal:hover {
    background: #2fe6ff;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}

QPushButton:disabled {
    color: #4b5568;
    border-color: #1a212d;
}

QFrame#historyRow {
    background-color: #0d1118;
    border: 1px solid #1c2530;
    border-left: 3px solid #39ff88;
    border-radius: 8px;
}
QFrame#historyRow[failed="true"] {
    border-left: 3px solid #ff2d6f;
}
QLabel#historyText {
    font-family: 'Consolas', 'Cascadia Mono';
    font-size: 9pt;
    color: #c4d0de;
}
QPushButton#presetBtn[custom="true"] {
    border-style: solid;
    border-color: #3a2a55;
    color: #c9a8ff;
}
QPushButton#presetBtn[custom="true"]:hover {
    border-color: #b26bff;
    color: #d9c2ff;
}
QPushButton#presetBtn[custom="true"]:checked {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba(178, 107, 255, 50), stop:1 rgba(178, 107, 255, 8));
    border: 1px solid #b26bff;
    color: #e6d6ff;
    font-weight: bold;
}
QPushButton#jobBtn {
    background-color: #141a24;
    border: 1px dashed #2a3542;
    border-radius: 10px;
    padding: 5px 12px;
    color: #8a97a8;
}
QPushButton#jobBtn:hover {
    border: 1px solid #2fe6ff;
    color: #2fe6ff;
}
QPushButton#jobBtn[set="true"] {
    border: 1px solid #2fe6ff;
    color: #8ff2ff;
    background-color: rgba(47, 230, 255, 18);
}
"""


# --- Windows High Contrast (research-accessibility.md Finding 3) ---
#
# A user who turned on High Contrast needs *their* colors (often yellow on
# black, or black on white at large contrast) - the dark neon theme above
# overrides every one of them. Qt already fills the application palette
# from the High Contrast system colors, so in that mode the app simply
# applies no stylesheet and lets the (Fusion) style paint with that palette.
# The QStyleHints color scheme is not enough to detect this: it only
# reports light/dark, not "the user needs forced colors".

SPI_GETHIGHCONTRAST = 0x0042
HCF_HIGHCONTRASTON = 0x00000001


class _HighContrastW(ctypes.Structure):
    # HIGHCONTRASTW from winuser.h.
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("dwFlags", ctypes.c_uint32),
        ("lpszDefaultScheme", ctypes.c_void_p),
    ]


def is_high_contrast(system_parameters_info=None) -> bool:
    """True when Windows High Contrast is on.

    `system_parameters_info` stands in for user32.SystemParametersInfoW
    (same arguments, gets a ctypes pointer to the struct) so tests can
    drive it; off Windows, with no stand-in, this is always False."""
    if system_parameters_info is None:
        if sys.platform != "win32":
            return False
        try:
            system_parameters_info = ctypes.windll.user32.SystemParametersInfoW
        except (AttributeError, OSError):
            return False
    info = _HighContrastW()
    info.cbSize = ctypes.sizeof(info)
    try:
        ok = system_parameters_info(SPI_GETHIGHCONTRAST, info.cbSize, ctypes.pointer(info), 0)
    except (OSError, ctypes.ArgumentError):
        # Unknown is treated as "off" - the app's own theme is the default.
        return False
    return bool(ok) and bool(info.dwFlags & HCF_HIGHCONTRASTON)


def stylesheet(high_contrast: bool | None = None) -> str:
    """The stylesheet every window/dialog should apply (never STYLE directly),
    so High Contrast is honored everywhere consistently: "" in High
    Contrast mode, the custom theme otherwise. Checked on each call (it is
    cheap); the application-wide sheet is set once at startup, so switching
    the mode while the app runs fully applies after a restart."""
    if high_contrast is None:
        high_contrast = is_high_contrast()
    return "" if high_contrast else STYLE
