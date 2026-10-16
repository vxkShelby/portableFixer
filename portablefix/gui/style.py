"""Cyberpunk / power-user terminal QSS theme for the PortableFix main window
(dark by default; a light palette is selectable - see PALETTES / set_theme).

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
from string import Template

# Colour tokens per theme. Every colour in the stylesheet (and every inline
# colour: risk badges, console tints, the avatar) comes from here; the
# translucent rgba() tints use the derived "<name>_rgb" ("r, g, b").
PALETTES = {
    "dark": {
        "accent": "#2fe6ff",
        "bad": "#ff2d6f",
        "inset": "#06080c",
        "text": "#d6e2f0",
        "line_strong": "#232d3a",
        "warn": "#ffb020",
        "ok": "#39ff88",
        "line": "#1c2530",
        "muted": "#7c8799",
        "bg": "#0b0e14",
        "surface": "#10141c",
        "disabled": "#4b5568",
        "control": "#141a24",
        "control_hover": "#1a212d",
        "accent_text": "#8ff2ff",
        "subtle": "#8a97a8",
        "purple": "#b26bff",
        "dashed": "#2a3542",
        "control_hover2": "#182028",
        "magenta": "#ff2bd6",
        "bg_end": "#090b10",
        "accent2": "#5ee6ff",
        "console_text": "#9fd9e8",
        "text_soft": "#c4d0de",
        "line_top": "#26323f",
        "run_a": "#00d9ff",
        "run_b": "#00ffa3",
        "run_a_hover": "#4ee6ff",
        "run_b_hover": "#4dffc0",
        "disabled_danger": "#3a4250",
        "bar_a": "#00e5ff",
        "row_bg": "#0d1118",
        "custom_border": "#3a2a55",
        "custom_text": "#c9a8ff",
        "custom_hover": "#d9c2ff",
        "custom_checked": "#e6d6ff",
        "on_fill": "#06080c",
        "overlay": "#ffffff",
        "avatar_fg": "#06141a",
        "avatar_bgs": ("#5ee6ff", "#39c2ff", "#6bd4c2", "#8f7cff", "#4dd0e1", "#64b5f6"),
    },
    "light": {
        "accent": "#00708c",
        "bad": "#c4123c",
        "inset": "#ffffff",
        "text": "#1b2433",
        "line_strong": "#c3ccd9",
        "warn": "#8f5200",
        "ok": "#0a7338",
        "line": "#d5dbe5",
        "muted": "#55627a",
        "bg": "#f3f5f9",
        "surface": "#ffffff",
        "disabled": "#9aa5b6",
        "control": "#eef1f6",
        "control_hover": "#e1e7ef",
        "accent_text": "#00566c",
        "subtle": "#4f5d73",
        "purple": "#6a2fd0",
        "dashed": "#b4bfce",
        "control_hover2": "#e6ebf3",
        "magenta": "#a8168c",
        "bg_end": "#eaeef4",
        "accent2": "#00708c",
        "console_text": "#1f4b5c",
        "text_soft": "#34415a",
        "line_top": "#e3e8f0",
        "run_a": "#00708c",
        "run_b": "#0b7a4a",
        "run_a_hover": "#005f77",
        "run_b_hover": "#09683f",
        "disabled_danger": "#b9c1cd",
        "bar_a": "#0a8fb0",
        "row_bg": "#f8fafc",
        "custom_border": "#d9c8f5",
        "custom_text": "#5b2bb5",
        "custom_hover": "#4a1f99",
        "custom_checked": "#3d1a80",
        "on_fill": "#ffffff",
        "overlay": "#000000",
        "avatar_fg": "#0a2530",
        "avatar_bgs": ("#bfeaf6", "#b9dcff", "#bfeadf", "#d6cfff", "#bfeaee", "#c6defa"),
    },
}
THEMES = ("auto", "light", "dark")

# Risk / severity colours for the active theme. Mutated in place by
# set_theme so holders of a reference (the console) follow a live switch.
RISK_COLORS: dict[str, str] = {}
CONSOLE_COLORS: dict[str, str] = {}

_active = "dark"

STYLE_TEMPLATE = """QMainWindow, QWidget#central {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 $bg, stop:1 $bg_end);
}
QWidget {
    color: $text;
    font-family: 'Segoe UI';
    font-size: 10pt;
}

QLabel#appTitle {
    font-family: 'Sora', 'Segoe UI Semibold', 'Segoe UI';
    font-size: 14pt;
    font-weight: 700;
    color: $accent;
}
QLabel#adminPill {
    border-radius: 10px;
    padding: 3px 12px;
    font-weight: bold;
    font-size: 9pt;
}
QLabel#adminPill[admin="true"] {
    background-color: $ok;
    color: $on_fill;
}
QLabel#adminPill[admin="false"] {
    background-color: $warn;
    color: $on_fill;
}

QListWidget#categoryList {
    background-color: $surface;
    border: 1px solid $line;
    border-radius: 4px;
    padding: 6px;
    outline: none;
}
QListWidget#categoryList::item {
    padding: 10px 12px;
    border-radius: 16px;
    margin: 2px 4px;
}
QListWidget#categoryList::item:hover {
    background-color: rgba($overlay_rgb, 10);
}
QListWidget#categoryList::item:selected {
    background-color: rgba($accent_rgb, 32);
    color: $accent_text;
    font-weight: bold;
}
QListWidget#categoryList::item:focus {
    outline: 2px solid $accent;
}

QScrollArea {
    border: none;
    background: transparent;
}
QScrollArea > QWidget > QWidget {
    background: transparent;
}
QScrollBar:vertical {
    background: $bg;
    width: 10px;
    border-radius: 4px;
}
QScrollBar::handle:vertical {
    background: $line_strong;
    border-radius: 4px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: $accent;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

QFrame#actionCard {
    background-color: $surface;
    border: 1px solid $line;
    border-top: 1px solid $line_top;
    border-radius: 16px;
}
QLabel#cardHeading {
    font-family: 'Sora', 'Segoe UI Semibold', 'Segoe UI';
    font-size: 11pt;
    font-weight: 600;
    color: $accent;
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
    border: 2px solid $line_strong;
    background: $inset;
}
QCheckBox::indicator:hover {
    border-color: $accent;
}
QCheckBox::indicator:checked {
    background-color: $accent;
    border-color: $accent;
}
QCheckBox::indicator:focus {
    outline: 2px solid $accent;
}

QLabel#riskBadge {
    border-radius: 10px;
    padding: 1px 8px;
    font-size: 9pt;
    font-weight: bold;
}
QLabel#riskBadge[risk="SAFE"] {
    background: transparent;
    border: 1px solid rgba($ok_rgb, 110);
    color: $ok;
    font-weight: normal;
}
QLabel#riskBadge[risk="MODERATE"] { background-color: $warn; color: $on_fill; }
QLabel#riskBadge[risk="DESTRUCTIVE"] { background-color: $bad; color: $on_fill; }
QLabel#riskBadge[risk="REQUIRES_REBOOT"] { background-color: $purple; color: $on_fill; }
QLabel#riskBadge[risk="CUSTOM"] { background: transparent; border: 1px solid $accent2; color: $accent2; }
QLabel#riskBadge[risk="ADMIN"] { background: transparent; border: 1px solid $warn; color: $warn; }

QPushButton {
    background-color: $control;
    border: 1px solid $line_strong;
    border-radius: 10px;
    padding: 7px 16px;
}
QPushButton:hover {
    background-color: $control_hover;
    border-color: $accent;
}
QPushButton:focus {
    outline: 2px solid $accent;
    outline-offset: 1px;
}
QPushButton#runButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 $run_a, stop:1 $run_b);
    border: none;
    border-radius: 16px;
    color: $on_fill;
    font-weight: bold;
    padding: 9px 24px;
}
QPushButton#runButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 $run_a_hover, stop:1 $run_b_hover);
}
/* DRY-RUN on: amber, so it is obvious the batch will change nothing. */
QPushButton#runButton[dryrun="true"] { background: $warn; }
QPushButton#runButton[dryrun="true"]:hover { background: rgba($warn_rgb, 210); }
QPushButton#runButton:disabled {
    background: $line_strong;
    color: $disabled;
}
QPushButton#cancelButton {
    background-color: $surface;
    color: $bad;
    border: 1px solid $bad;
    border-radius: 16px;
    padding: 9px 18px;
}
QPushButton#cancelButton:hover {
    background-color: rgba($bad_rgb, 20);
}
QPushButton#cancelButton:disabled {
    background-color: $surface;
    color: $disabled;
    border: 1px solid $line_strong;
}

QPushButton#selectionBtn, QToolButton#selectionBtn {
    background-color: $control;
    border: 1px dashed $dashed;
    border-radius: 10px;
    padding: 3px 10px;
    font-family: 'Segoe UI';
    font-size: 9pt;
    color: $subtle;
}
QToolButton#selectionBtn::menu-indicator {
    image: none;
    width: 0px;
}
QPushButton#selectionBtn:hover, QToolButton#selectionBtn:hover {
    border: 1px solid $accent;
    color: $accent;
    background-color: $control_hover2;
}
QPushButton#selectionBtn[danger="true"]:enabled {
    border: 1px solid $bad;
    color: $bad;
}
QPushButton#selectionBtn[danger="true"]:enabled:hover {
    background: rgba($bad_rgb, 20);
    border: 1px solid $bad;
    color: $bad;
}
QPushButton#selectionBtn[danger="true"]:disabled {
    border: 1px dashed $control_hover;
    color: $disabled_danger;
}
QPushButton#presetBtn {
    background-color: $control;
    border: 1px dashed $dashed;
    border-radius: 10px;
    padding: 3px 10px;
    font-family: 'Segoe UI';
    font-size: 9pt;
    color: $subtle;
}
QPushButton#presetBtn:hover {
    border: 1px solid $accent;
    color: $accent;
    background-color: $control_hover2;
}
QPushButton#presetBtn:checked {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba($accent_rgb, 45), stop:1 rgba($accent_rgb, 5));
    border: 1px solid $accent;
    color: $accent_text;
    font-weight: bold;
}
/* Muted text needs WCAG AA 4.5:1 on every card/button surface it sits on
   (9pt text); tests/test_style.py checks both palettes. */
QLabel#selectionScope {
    font-family: 'Segoe UI';
    color: $muted;
    font-size: 9pt;
}

QLabel#emptyState {
    color: $muted;
    font-size: 11pt;
    padding: 24px 8px;
}

QLabel#actionStatus {
    font-size: 9pt;
    font-weight: bold;
}
QLabel#actionStatus[state="running"] { color: $warn; }
QLabel#actionStatus[state="ok"] { color: $ok; }
QLabel#actionStatus[state="fail"] { color: $bad; }

QToolButton#actionDetailToggle {
    background: transparent;
    border: 1px dashed $line_strong;
    border-radius: 4px;
    padding: 1px 6px;
    font-family: 'Consolas', 'Cascadia Mono';
    font-size: 9pt;
    color: $subtle;
}
QToolButton#actionDetailToggle:hover {
    border: 1px solid $accent;
    color: $accent;
}
QToolButton#actionDetailToggle:checked {
    color: $accent;
    border: 1px solid $accent;
}
/* QPushButton:focus doesn't match tool buttons - without this the
   per-action "details" toggle showed no keyboard focus at all. */
QToolButton:focus {
    outline: 2px solid $accent;
    border: 1px solid $accent;
}
/* Dashboard tiles are keyboard-focusable (Tab, then Enter/Space). */
QFrame#actionCard[tile="true"]:focus {
    border: 2px solid $accent;
}
QWidget#actionDetailPanel {
    background-color: $bg;
    border: 1px solid $line;
    border-radius: 4px;
}
QLabel#actionDetailDescription {
    color: $text;
    font-size: 9pt;
}
QLabel#actionDetailLabel {
    font-family: 'Consolas', 'Cascadia Mono';
    font-size: 9pt;
    font-weight: bold;
    color: $muted;
}
QPlainTextEdit#actionDetailCommand {
    background-color: $inset;
    border: 1px solid $line;
    border-radius: 4px;
    padding: 6px;
    font-family: 'Cascadia Mono', 'Consolas';
    font-size: 9pt;
    color: $console_text;
}

QLineEdit#searchBox {
    background-color: $inset;
    border: 1px solid $line_strong;
    border-radius: 10px;
    padding: 5px 10px;
    color: $text;
}
QLineEdit#searchBox:focus {
    border: 1px solid $accent;
}

QDialog {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 $bg, stop:1 $bg_end);
}
QWidget#updateBanner {
    background-color: $surface;
    border: 1px solid $magenta;
    border-left: 3px solid $magenta;
    border-radius: 4px;
}
QLabel#summaryHeader {
    font-family: 'Sora', 'Segoe UI Semibold', 'Segoe UI';
    font-size: 12pt;
    font-weight: 700;
    color: $accent;
}
QLabel#healthState {
    font-family: 'Sora', 'Segoe UI Semibold', 'Segoe UI';
    font-size: 11pt;
    font-weight: 700;
    color: $muted;
}
QLabel#healthState[state="ok"] { color: $ok; }
QLabel#healthState[state="attention"] { color: $warn; }
QLabel#healthState[state="critical"] { color: $bad; }
QLabel#summaryDryRunNote {
    font-family: 'Consolas', 'Cascadia Mono';
    color: $warn;
    font-weight: bold;
}
QLabel#summaryRow[ok="true"] { color: $ok; }
QLabel#summaryRow[ok="false"] { color: $bad; }
QLabel#summaryHint { color: $muted; font-size: 9pt; padding-left: 14px; }
QLabel#summaryMetricName { color: $text; font-size: 9pt; }
QLabel#summaryMetricDelta {
    font-family: 'Consolas', 'Cascadia Mono';
    font-size: 9pt;
    font-weight: bold;
    color: $muted;
}
QLabel#summaryMetricDelta[trend="good"] { color: $ok; }
QLabel#summaryMetricDelta[trend="bad"] { color: $bad; }

QPlainTextEdit#console {
    background-color: $inset;
    border: 1px solid $line;
    border-radius: 4px;
    padding: 8px;
    font-family: 'Cascadia Mono', 'Consolas';
    font-size: 9pt;
    color: $console_text;
}
QSplitter::handle {
    background-color: $line;
}
QSplitter::handle:vertical {
    height: 6px;
}
QSplitter::handle:hover {
    background-color: $accent;
}

QProgressBar#batchProgress {
    background-color: $inset;
    border: 1px solid $line;
    border-radius: 10px;
    text-align: center;
    font-family: 'Consolas', 'Cascadia Mono';
    font-weight: bold;
    font-size: 9pt;
    color: $accent_text;
    padding: 1px;
    min-height: 16px;
}
QProgressBar#batchProgress::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 $bar_a, stop:1 $magenta);
    width: 8px;
    margin: 1px;
}

QLabel#countPill {
    border-radius: 10px;
    padding: 1px 9px;
    font-family: 'Consolas', 'Cascadia Mono';
    font-weight: bold;
    font-size: 9pt;
}
QLabel#countPill[state="ok"] {
    background-color: rgba($ok_rgb, 30);
    color: $ok;
}
QLabel#countPill[state="warn"] {
    background-color: rgba($warn_rgb, 30);
    color: $warn;
}
QLabel#countPill[state="idle"] {
    background-color: rgba($overlay_rgb, 8);
    /* Real content (the "0" before any analysis), not a disabled control -
       so it needs 4.5:1 like other text (the disabled grey is far lower). */
    color: $muted;
}

QPushButton#panelBtn {
    background-color: $control;
    border: 1px solid $line_strong;
    border-radius: 10px;
    padding: 7px 12px;
    color: $text_soft;
    font-size: 9.5pt;
}
QPushButton#panelBtn:hover {
    border-color: $accent;
    color: $accent;
    background-color: $control_hover2;
}
QPushButton#panelBtn:disabled {
    color: $disabled;
    border-color: $control_hover;
}

QLabel#wingetBanner {
    border-radius: 10px;
    padding: 8px 12px;
    font-size: 9pt;
}
QLabel#wingetBanner[state="warn"] {
    background-color: rgba($warn_rgb, 22);
    border: 1px solid rgba($warn_rgb, 90);
    color: $text;
}
QWidget#writeBanner {
    background-color: rgba($bad_rgb, 22);
    border: 1px solid rgba($bad_rgb, 110);
    border-radius: 10px;
    color: $text;
}
QLabel#targetUserBanner {
    background-color: rgba($warn_rgb, 22);
    border: 1px solid rgba($warn_rgb, 90);
    border-radius: 10px;
    padding: 8px 12px;
    color: $text;
}
QFrame#firstRunBanner {
    background-color: rgba($accent2_rgb, 18);
    border: 1px solid rgba($accent2_rgb, 80);
    border-radius: 10px;
    color: $text;
}
QLabel#wingetBanner[state="ok"] {
    background: transparent;
    border: none;
    color: $muted;
    font-family: 'Consolas', 'Cascadia Mono';
}

QComboBox {
    background-color: $inset;
    border: 1px solid $line_strong;
    border-radius: 10px;
    padding: 4px 10px;
    color: $text;
    min-width: 80px;
}
QComboBox:hover, QComboBox:focus {
    border: 1px solid $accent;
}
QComboBox:disabled {
    background-color: $bg;
    border: 1px solid $control_hover;
    color: $disabled;
}
QComboBox QAbstractItemView {
    background-color: $surface;
    border: 1px solid $line_strong;
    selection-background-color: rgba($accent_rgb, 45);
    selection-color: $accent_text;
    outline: none;
    padding: 4px;
}

QCheckBox:disabled {
    color: $disabled;
}
QCheckBox::indicator:disabled {
    border-color: $control_hover;
    background: $bg;
}

QToolTip {
    background-color: $surface;
    color: $text;
    border: 1px solid $accent;
    border-radius: 4px;
    padding: 6px 8px;
}

QStatusBar {
    background-color: $inset;
    border-top: 1px solid $line;
    color: $subtle;
    font-size: 9pt;
}
QStatusBar::item {
    border: none;
}

QMessageBox {
    background-color: $bg;
}
QMessageBox QLabel {
    color: $text;
}
QMessageBox QPushButton {
    min-width: 84px;
}

QScrollBar:horizontal {
    background: $bg;
    height: 10px;
    border-radius: 4px;
}
QScrollBar::handle:horizontal {
    background: $line_strong;
    border-radius: 4px;
    min-width: 30px;
}
QScrollBar::handle:horizontal:hover {
    background: $accent;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}

QPushButton:disabled {
    color: $disabled;
    border-color: $control_hover;
}

QFrame#historyRow {
    background-color: $row_bg;
    border: 1px solid $line;
    border-left: 3px solid $ok;
    border-radius: 10px;
}
QFrame#historyRow[failed="true"] {
    border-left: 3px solid $bad;
}
QLabel#historyText {
    font-size: 9pt;
    color: $text_soft;
}
QPushButton#presetBtn[custom="true"] {
    border-style: solid;
    border-color: $custom_border;
    color: $custom_text;
}
QPushButton#presetBtn[custom="true"]:hover {
    border-color: $purple;
    color: $custom_hover;
}
QPushButton#presetBtn[custom="true"]:checked {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba($purple_rgb, 50), stop:1 rgba($purple_rgb, 8));
    border: 1px solid $purple;
    color: $custom_checked;
    font-weight: bold;
}
QPushButton#jobBtn {
    background-color: $control;
    border: 1px dashed $dashed;
    border-radius: 10px;
    padding: 5px 12px;
    color: $subtle;
}
QPushButton#jobBtn:hover {
    border: 1px solid $accent;
    color: $accent;
}
QPushButton#jobBtn[set="true"] {
    border: 1px solid $accent;
    color: $accent_text;
    background-color: rgba($accent_rgb, 18);
}
"""


def _tokens(name: str) -> dict:
    tokens = {k: v for k, v in PALETTES[name].items() if isinstance(v, str)}
    for key, value in list(tokens.items()):
        tokens[key + "_rgb"] = ", ".join(str(int(value[i:i + 2], 16)) for i in (1, 3, 5))
    return tokens


def build_stylesheet(name: str) -> str:
    return Template(STYLE_TEMPLATE).substitute(_tokens(name))


def resolve_theme(setting: str) -> str:
    """"light" or "dark" for a Settings.theme value. "auto" follows the OS
    colour scheme (Qt 6.5+); anything unknown - older Qt, the offscreen
    platform, no QGuiApplication yet - falls back to dark."""
    if setting in PALETTES:
        return setting
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication

        scheme = getattr(QGuiApplication.styleHints(), "colorScheme", None)
        light = getattr(getattr(Qt, "ColorScheme", None), "Light", None)
        if scheme is not None and light is not None and scheme() == light:
            return "light"
    except Exception:
        pass
    return "dark"


def active_theme() -> str:
    return _active


def set_theme(setting: str) -> str:
    """Make `setting` (auto/light/dark) the theme stylesheet() builds and
    RISK_COLORS/CONSOLE_COLORS report. Returns the resolved name."""
    global _active
    _active = resolve_theme(setting)
    p = PALETTES[_active]
    RISK_COLORS.update(SAFE=p["ok"], MODERATE=p["warn"], DESTRUCTIVE=p["bad"], REQUIRES_REBOOT=p["purple"])
    # Console line tints by severity - the same ok/warn/fail colours the
    # action status labels and the summary rows already use.
    CONSOLE_COLORS.update(success=p["ok"], warning=p["warn"], error=p["bad"])
    return _active


def avatar_colors(name: str) -> tuple[str, str]:
    """(background, text) of the initials avatar for `name`, active theme."""
    p = PALETTES[_active]
    return p["avatar_bgs"][sum(ord(c) for c in name) % len(p["avatar_bgs"])], p["avatar_fg"]


set_theme("dark")
STYLE = build_stylesheet("dark")



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
    Contrast mode, the active theme's sheet otherwise (set_theme). Checked on
    each call (it is cheap); High Contrast switched while the app runs fully
    applies after a restart."""
    if high_contrast is None:
        high_contrast = is_high_contrast()
    return "" if high_contrast else build_stylesheet(_active)
