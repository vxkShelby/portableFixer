"""Dark QSS theme for the PortableFix main window (no extra dependencies)."""

RISK_COLORS = {
    "SAFE": "#9ece6a",
    "MODERATE": "#e0af68",
    "DESTRUCTIVE": "#f7768e",
    "REQUIRES_REBOOT": "#bb9af7",
}

STYLE = """
QMainWindow, QWidget#central {
    background-color: #1a1b26;
}
QWidget {
    color: #c0caf5;
    font-family: 'Segoe UI';
    font-size: 10pt;
}

QLabel#appTitle {
    font-size: 14pt;
    font-weight: bold;
    color: #7aa2f7;
}
QLabel#adminPill {
    border-radius: 10px;
    padding: 3px 12px;
    font-weight: bold;
    font-size: 9pt;
}
QLabel#adminPill[admin="true"] {
    background-color: #9ece6a;
    color: #1a1b26;
}
QLabel#adminPill[admin="false"] {
    background-color: #e0af68;
    color: #1a1b26;
}

QListWidget#categoryList {
    background-color: #24283b;
    border: none;
    border-radius: 8px;
    padding: 6px;
    outline: none;
}
QListWidget#categoryList::item {
    padding: 10px 12px;
    border-radius: 6px;
    margin: 2px 0;
}
QListWidget#categoryList::item:hover {
    background-color: #2f3549;
}
QListWidget#categoryList::item:selected {
    background-color: #7aa2f7;
    color: #1a1b26;
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
    background: #1a1b26;
    width: 10px;
    border-radius: 5px;
}
QScrollBar::handle:vertical {
    background: #3b4261;
    border-radius: 5px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: #7aa2f7;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

QFrame#actionCard {
    background-color: #24283b;
    border-radius: 8px;
}
QLabel#cardHeading {
    font-size: 11pt;
    font-weight: bold;
    color: #7aa2f7;
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
    border: 2px solid #3b4261;
    background: #1a1b26;
}
QCheckBox::indicator:hover {
    border-color: #7aa2f7;
}
QCheckBox::indicator:checked {
    background-color: #7aa2f7;
    border-color: #7aa2f7;
}

QLabel#riskBadge {
    border-radius: 8px;
    padding: 1px 8px;
    font-size: 8pt;
    font-weight: bold;
    color: #1a1b26;
}
QLabel#riskBadge[risk="SAFE"] { background-color: #9ece6a; }
QLabel#riskBadge[risk="MODERATE"] { background-color: #e0af68; }
QLabel#riskBadge[risk="DESTRUCTIVE"] { background-color: #f7768e; }
QLabel#riskBadge[risk="REQUIRES_REBOOT"] { background-color: #bb9af7; }

QPushButton {
    background-color: #2f3549;
    border: none;
    border-radius: 6px;
    padding: 7px 16px;
}
QPushButton:hover {
    background-color: #3b4261;
}
QPushButton#runButton {
    background-color: #7aa2f7;
    color: #1a1b26;
    font-weight: bold;
    padding: 9px 24px;
}
QPushButton#runButton:hover {
    background-color: #9db6f9;
}
QPushButton#runButton:disabled {
    background-color: #3b4261;
    color: #565f89;
}
QPushButton#cancelButton {
    background-color: #24283b;
    color: #f7768e;
    border: 1px solid #f7768e;
    padding: 9px 18px;
}
QPushButton#cancelButton:hover {
    background-color: #3b4261;
}
QPushButton#cancelButton:disabled {
    background-color: #24283b;
    color: #565f89;
    border: 1px solid #414868;
}

QPushButton#selectionBtn {
    background: transparent;
    border: 1px solid #3b4261;
    border-radius: 5px;
    padding: 3px 10px;
    font-size: 8.5pt;
    color: #a9b1d6;
}
QPushButton#selectionBtn:hover {
    border-color: #7aa2f7;
    color: #7aa2f7;
    background: transparent;
}
QLabel#selectionScope {
    color: #7982a9;
    font-size: 9pt;
}

QDialog {
    background-color: #1a1b26;
}
QLabel#summaryHeader {
    font-size: 12pt;
    font-weight: bold;
    color: #7aa2f7;
}
QLabel#summaryDryRunNote {
    color: #e0af68;
    font-weight: bold;
}
QLabel#summaryRow[ok="true"] { color: #9ece6a; }
QLabel#summaryRow[ok="false"] { color: #f7768e; }

QPlainTextEdit#console {
    background-color: #16161e;
    border: none;
    border-radius: 8px;
    padding: 8px;
    font-family: 'Cascadia Mono', 'Consolas';
    font-size: 9pt;
    color: #a9b1d6;
}
"""
