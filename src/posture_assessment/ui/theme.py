from __future__ import annotations

PINK = "#ea5064"
PINK_DARK = "#b33745"
PINK_SOFT = "#fff0f2"
NAV = "#24292a"
TEXT = "#303437"
MUTED = "#73797d"
BORDER = "#cfd3d5"
BACKGROUND = "#f5f6f7"


APP_STYLESHEET = f"""
QWidget {{
    font-family: "Microsoft YaHei UI", "Microsoft YaHei";
    font-size: 14px;
    color: {TEXT};
}}
QMainWindow, QDialog {{ background: {BACKGROUND}; }}
QLabel#pageTitle {{ font-size: 24px; font-weight: 700; }}
QLabel#sectionTitle {{ font-size: 18px; font-weight: 700; }}
QLabel#muted {{ color: {MUTED}; }}
QFrame#card {{
    background: white;
    border: 1px solid {BORDER};
    border-radius: 4px;
}}
QLineEdit, QComboBox, QDateEdit, QDoubleSpinBox {{
    min-height: 36px;
    padding: 0 10px;
    background: white;
    border: 1px solid {BORDER};
    border-radius: 2px;
    selection-background-color: {PINK};
}}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QDoubleSpinBox:focus {{
    border: 1px solid {PINK};
}}
QPushButton {{
    min-height: 38px;
    padding: 0 20px;
    border: 1px solid {BORDER};
    border-radius: 3px;
    background: white;
}}
QPushButton:hover {{ background: #f2f3f4; }}
QPushButton[primary="true"] {{
    color: white;
    font-weight: 600;
    border: 1px solid {PINK};
    background: {PINK};
}}
QPushButton[primary="true"]:hover {{ background: #d94459; }}
QPushButton[danger="true"] {{ color: white; background: {PINK}; border-color: {PINK}; }}
QPushButton:disabled {{ color: #aaa; background: #e8e8e8; border-color: #ddd; }}
QToolButton {{
    min-width: 34px;
    min-height: 30px;
    border: none;
    background: transparent;
    font-size: 18px;
}}
QToolButton:hover {{ color: {PINK}; background: {PINK_SOFT}; }}
QTableWidget, QTableView {{
    background: white;
    alternate-background-color: #fafafa;
    border: 1px solid {BORDER};
    gridline-color: {BORDER};
    selection-background-color: #c9464b;
    selection-color: white;
}}
QHeaderView::section {{
    background: #f0f1f2;
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    padding: 9px 6px;
    font-weight: 700;
}}
QTableWidget::item {{ padding: 6px; }}
QCheckBox, QRadioButton {{ spacing: 8px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 17px; height: 17px; }}
QProgressBar {{
    border: 1px solid {BORDER};
    border-radius: 4px;
    background: white;
    text-align: center;
}}
QProgressBar::chunk {{ background: {PINK}; }}
QScrollBar:vertical {{ width: 12px; background: #eee; }}
QScrollBar::handle:vertical {{ background: #bbb; min-height: 24px; border-radius: 5px; }}
"""


def set_primary(button) -> None:
    button.setProperty("primary", True)

