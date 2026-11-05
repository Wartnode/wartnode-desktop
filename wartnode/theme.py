"""Shared Warthog design tokens, applied to native Qt controls."""
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
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
QFrame#panel QLabel { background:transparent; }
QPushButton { background:#191b1e; border:1px solid #32363c; border-radius:7px; padding:10px 16px; }
QPushButton:hover { background:#24282e; border-color:#555a63; }
QPushButton:pressed { background:#30343a; }
QPushButton:checked { background:#222014; color:#fdb913; border-color:#fdb913; }
QPushButton:focus, QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus { border:1px solid #fdb913; }
QPushButton:disabled { color:#858a93; background:#141618; border-color:#24272c; }
QPushButton[primary='true'] { color:#191400; background:#fdb913; border-color:#fdb913; font-weight:600; }
QPushButton[primary='true']:hover { background:#ffca45; }
QPushButton[primary='true']:disabled { color:#92969e; background:#24272c; border-color:#24272c; }
QPushButton[nav='true'] { text-align:left; background:transparent; border:0; padding:12px 14px; color:#92969e; }
QPushButton[nav='true']:checked { color:#fdb913; background:#222014; }
QPushButton[nav='true']:hover { background:#191b1e; color:#eceef1; }
QPushButton[nav='true']:checked:hover { color:#fdb913; background:#222014; }
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextBrowser { background:#101214; border:1px solid #32363c; border-radius:6px; padding:9px; selection-background-color:#605020; }
QComboBox::drop-down { border:0; width:22px; }
QTableWidget { background:#141618; border:1px solid #24272c; gridline-color:#24272c; selection-background-color:#303025; alternate-background-color:#191b1e; }
QHeaderView::section { background:#191b1e; color:#92969e; border:0; padding:11px; text-align:left; }
QTableWidget::item { padding:8px; }
QTableWidget::item:selected { background:#303025; color:#eceef1; }
QListWidget { border:1px solid #24272c; border-radius:6px; padding:4px; }
QListWidget::item { padding:9px; }
QListWidget::item:selected { background:#222014; color:#fdb913; }
QTabWidget::pane { border:0; }
QTabBar::tab { color:#92969e; padding:12px 18px; border-bottom:2px solid transparent; }
QTabBar::tab:selected { color:#fdb913; border-bottom:2px solid #fdb913; }
QScrollBar:vertical { background:#101214; width:10px; }
QScrollBar::handle:vertical { background:#454a52; border-radius:5px; min-height:24px; }
QToolTip { background:#191b1e; color:#eceef1; border:1px solid #454a52; }
"""


def apply(app,appearance='dark',force=False):
    from .desktop_preferences import contrast_enabled, system_dark
    contrast=contrast_enabled();dark=appearance=='dark' or system_dark()
    identity=(appearance,contrast,dark)
    if not force and getattr(app,'wartnode_theme_identity',None)==identity:return
    app.wartnode_theme_identity=identity
    font_path = asset_root() / 'assets/fonts/montserrat-variable.ttf'
    if font_path.exists():
        QFontDatabase.addApplicationFont(str(font_path))
    app.setFont(QFont("Segoe UI", 10))
    if not hasattr(app,'wartnode_system_palette'):
        app.wartnode_system_palette=QPalette(app.palette())
    if contrast:
        app.setStyleSheet('');app.setPalette(app.style().standardPalette())
        return
    if appearance=='system' and not dark:
        palette=QPalette(app.style().standardPalette())
        palette.setColor(QPalette.ColorRole.Highlight,QColor('#8a5900'))
        palette.setColor(QPalette.ColorRole.HighlightedText,QColor('#ffffff'))
        app.setPalette(palette)
        replacements={'#0d0f11':'#f5f6f8','#eceef1':'#18202a','#101214':'#ffffff',
            '#24272c':'#d8dde5','#92969e':'#596474','#141618':'#ffffff','#191b1e':'#eef1f5',
            '#32363c':'#bdc5d0','#24282e':'#e2e7ee','#555a63':'#808b9a','#30343a':'#d7dee7',
            '#858a93':'#6e7885','#fdb913':'#8a5900','#191400':'#ffffff','#ffca45':'#6e4700',
            '#222014':'#fff0c9','#605020':'#fff0c9','#303025':'#e9edf3','#454a52':'#8a95a4'}
        import re
        app.setStyleSheet(re.sub(r'#[0-9a-f]{6}',lambda m:replacements.get(m[0],m[0]),STYLE))
        return
    palette=QPalette(app.palette())
    for role,color in ((QPalette.ColorRole.Window,'#0d0f11'),(QPalette.ColorRole.WindowText,'#eceef1'),
                       (QPalette.ColorRole.Base,'#101214'),(QPalette.ColorRole.Text,'#eceef1'),
                       (QPalette.ColorRole.Button,'#191b1e'),(QPalette.ColorRole.ButtonText,'#eceef1'),
                       (QPalette.ColorRole.PlaceholderText,'#92969e'),(QPalette.ColorRole.Mid,'#32363c'),
                       (QPalette.ColorRole.Highlight,'#fdb913'),(QPalette.ColorRole.HighlightedText,'#191400')):
        palette.setColor(role,QColor(color))
    app.setPalette(palette)
    app.setStyleSheet(STYLE)
