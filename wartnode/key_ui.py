"""Password-confirmed, temporary display of one address's private key."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLineEdit,
                             QVBoxLayout)
from .widgets import button, label


class PrivateKeyDialog(QDialog):
    def __init__(self, owner, address, private_key):
        super().__init__(owner)
        self.setWindowTitle('Private key · WartNode')
        self.resize(740, 300)
        body = QVBoxLayout(self)
        body.setContentsMargins(24, 24, 24, 20)
        body.setSpacing(12)
        body.addWidget(label('Private key', 'title'))
        body.addWidget(label('Anyone with this key can spend funds from this address.'))
        address_label = label(address, muted=True)
        address_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(address_label)
        self.key = QLineEdit(private_key)
        self.key.setReadOnly(True)
        self.key.setAccessibleName('Private key')
        self.key.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.key.setCursorPosition(0)
        body.addWidget(self.key)
        body.addWidget(label('Import this 64-character hexadecimal key into another Warthog wallet.', muted=True))
        self.copy_status = label('', muted=True)
        body.addWidget(self.copy_status)
        actions = QHBoxLayout()
        self.copy_button = button('Copy private key', self.copy_key)
        actions.addWidget(self.copy_button)
        actions.addStretch()
        actions.addWidget(button('Close', self.accept))
        body.addLayout(actions)
        self.clipboard = QApplication.clipboard()
        self.clipboard_timer = QTimer(self)
        self.clipboard_timer.setSingleShot(True)
        self.clipboard_timer.timeout.connect(self.clear_clipboard)
        self.clipboard.dataChanged.connect(self.clipboard_changed)
        self.clipboard_connected = True
        self.finished.connect(self.clear_secret)

    def copy_key(self):
        if self.key.text():
            self.clipboard.setText(self.key.text())

    def clipboard_changed(self):
        if self.key.text() and self.clipboard.text() == self.key.text():
            self.clipboard_timer.start(60000)
            self.copy_status.setText('Copied. Clipboard clears in 60 seconds or when this window closes.')
        else:
            self.clipboard_timer.stop()
            self.copy_status.clear()

    def clear_clipboard(self):
        if self.key.text() and self.clipboard.text() == self.key.text():
            self.clipboard.clear()

    def clear_secret(self, *_):
        self.clipboard_timer.stop()
        self.clear_clipboard()
        self.key.clear()
        self.copy_button.setEnabled(False)
        if self.clipboard_connected:
            self.clipboard.dataChanged.disconnect(self.clipboard_changed)
            self.clipboard_connected = False
