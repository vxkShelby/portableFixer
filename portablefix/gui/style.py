"""Cyberpunk / power-user terminal QSS theme for the PortableFix main window.

Design direction: near-black dashboard chrome, one signature duo-accent
(electric cyan + magenta) for structural/interactive UI, monospace readouts
on anything numeric or status-like, and sharp/mixed corner radii instead of
uniform pill-rounding. Risk-level colors stay their own neon family so
color-coding meaning is never confused with the brand accent. SAFE badges
are an outline (not filled) since almost every action is SAFE - a filled
neon pill on every single row drowned out the handful of rows that actually
need attention (MODERATE/DESTRUCTIVE/REQUIRES_REBOOT).
"""

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
    font-family: 'Segoe UI Semibold', 'Segoe UI';
    font-size: 14pt;
    font-weight: bold;
    color: #2fe6ff;
    border-left: 3px solid #2fe6ff;
    padding-left: 8px;
}
QLabel#adminPill {
    border-radius: 2px;
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
    border-radius: 0px;
    border-left: 3px solid transparent;
    margin: 2px 0;
}
QListWidget#categoryList::item:hover {
    background-color: rgba(47, 230, 255, 18);
    border-left: 3px solid #1c6b78;
}
QListWidget#categoryList::item:selected {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba(47, 230, 255, 45), stop:1 rgba(47, 230, 255, 5));
    border-left: 3px solid #2fe6ff;
    color: #8ff2ff;
    font-weight: bold;
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
    border-radius: 3px;
}
QLabel#cardHeading {
    font-family: 'Segoe UI Semibold', 'Segoe UI';
    font-size: 11pt;
    font-weight: bold;
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
    border-radius: 0px;
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

QLabel#riskBadge {
    border-radius: 2px;
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

QPushButton {
    background-color: #141a24;
    border: 1px solid #232d3a;
    border-radius: 4px;
    padding: 7px 16px;
}
QPushButton:hover {
    background-color: #1a212d;
    border-color: #2fe6ff;
}
QPushButton#runButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #00d9ff, stop:1 #00ffa3);
    border: none;
    border-radius: 3px;
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
    border-radius: 4px;
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
    background: transparent;
    border: 1px dashed #232d3a;
    border-radius: 2px;
    padding: 3px 10px;
    font-family: 'Consolas', 'Cascadia Mono';
    font-size: 8.5pt;
    color: #8a97a8;
}
QPushButton#selectionBtn:hover {
    border: 1px solid #2fe6ff;
    color: #2fe6ff;
    background: transparent;
}
QLabel#selectionScope {
    font-family: 'Consolas', 'Cascadia Mono';
    color: #6b7686;
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
    color: #6b7686;
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
    border-radius: 2px;
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
    font-family: 'Segoe UI Semibold', 'Segoe UI';
    font-size: 12pt;
    font-weight: bold;
    color: #2fe6ff;
}
QLabel#summaryDryRunNote {
    font-family: 'Consolas', 'Cascadia Mono';
    color: #ffb020;
    font-weight: bold;
}
QLabel#summaryRow[ok="true"] { color: #39ff88; }
QLabel#summaryRow[ok="false"] { color: #ff2d6f; }

QPlainTextEdit#console {
    background-color: #06080c;
    border: 1px solid #1c2530;
    border-radius: 2px;
    padding: 8px;
    font-family: 'Cascadia Mono', 'Consolas';
    font-size: 9pt;
    color: #9fd9e8;
}

QProgressBar#batchProgress {
    background-color: #06080c;
    border: 1px solid #1c2530;
    border-radius: 2px;
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
"""
