"""Shared Warthog design tokens, applied to native Qt controls."""
from PySide6.QtGui import QFont, QFontDatabase
from .paths import asset_root

STYLE = """
QWidget { background:#0d0f11; color:#eceef1; font-family:'Segoe UI'; font-size:14px; }
QWidget#sidebar { background:#101214; border-right:1px solid #24272c; }
QLabel { background:transparent; }
QLabel#brand { color:#fdb913; font-family:'Montserrat'; font-size:22px; font-weight:700; letter-spacing:1px; }
QLabel#subtitle, QLabel[muted='true'] { color:#92969e; }
QLabel#title { font-family:'Montserrat'; font-size:27px; font-weight:600; }
QLabel#value { font-size:28px; font-weight:600; }
QFrame#panel { background:#141618; border:1px solid #24272c; border-radius:10px; }
QFrame#panel QLabel, QFrame#panel QWidget { background:transparent; }
QPushButton { background:#191b1e; border:1px solid #32363c; border-radius:7px; padding:10px 16px; }
QPushButton:hover { background:#24282e; border-color:#555a63; }
QPushButton:pressed { background:#30343a; }
QPushButton:focus, QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus { border:1px solid #fdb913; }
QPushButton:disabled { color:#858a93; background:#141618; border-color:#24272c; }
QPushButton[primary='true'] { color:#191400; background:#fdb913; border-color:#fdb913; font-weight:600; }
QPushButton[primary='true']:hover { background:#ffca45; }
QPushButton[primary='true']:disabled { color:#92969e; background:#24272c; border-color:#24272c; }
QPushButton[nav='true'] { text-align:left; background:transparent; border:0; padding:12px 14px; color:#92969e; }
QPushButton[nav='true']:checked { color:#fdb913; background:#222014; }
QPushButton[nav='true']:hover { background:#191b1e; color:#eceef1; }
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextBrowser { background:#101214; border:1px solid #32363c; border-radius:6px; padding:9px; selection-background-color:#605020; }
QComboBox::drop-down { border:0; width:22px; }
QTableWidget { background:#141618; border:1px solid #24272c; gridline-color:#24272c; selection-background-color:#303025; alternate-background-color:#191b1e; }
QHeaderView::section { background:#191b1e; color:#92969e; border:0; padding:11px; text-align:left; }
QTableWidget::item { padding:8px; }
QTabWidget::pane { border:0; }
QTabBar::tab { color:#92969e; padding:12px 18px; border-bottom:2px solid transparent; }
QTabBar::tab:selected { color:#fdb913; border-bottom:2px solid #fdb913; }
QScrollBar:vertical { background:#101214; width:10px; }
QScrollBar::handle:vertical { background:#454a52; border-radius:5px; min-height:24px; }
QToolTip { background:#191b1e; color:#eceef1; border:1px solid #454a52; }
"""


def apply(app):
    font_path = asset_root() / 'assets/fonts/montserrat-variable.ttf'
    if font_path.exists():
        QFontDatabase.addApplicationFont(str(font_path))
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(STYLE)
