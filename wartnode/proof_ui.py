"""Local Sign / Verify workspace. Private operations stay in the signer helper."""
from pathlib import Path
import threading

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFrame, QHBoxLayout, QLineEdit, QPlainTextEdit, QProgressBar,
    QStackedWidget, QTabWidget, QVBoxLayout, QWidget)

from . import proofs
from .paths import atomic_json
from .widgets import button, label, FlowGrid, scroll_dialog


def panel(title, description):
    frame = QFrame()
    frame.setObjectName('panel')
    body = QVBoxLayout(frame)
    body.setContentsMargins(20, 16, 20, 16)
    body.setSpacing(8)
    body.addWidget(label(title, 'section'))
    body.addWidget(label(description, muted=True))
    return frame, body


def editor(name, placeholder, readonly=False):
    control = QPlainTextEdit()
    control.setAccessibleName(name)
    control.setPlaceholderText(placeholder)
    control.setReadOnly(readonly)
    control.setMinimumHeight(160)
    control.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
    return control


def path_row(name, callback):
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    field = QLineEdit()
    field.setReadOnly(True)
    field.setAccessibleName(name)
    field.setPlaceholderText('No file selected')
    layout.addWidget(field, 1)
    layout.addWidget(button('Choose file…', callback))
    return row, field


class ProofDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle('Sign / Verify · WartNode')
        self.resize(960, 740)
        self.sign_revision = self.verify_revision = 0
        self.sign_busy = self.verify_busy = False
        self.sign_cancel = threading.Event()
        self.verify_cancel = threading.Event()
        self.proof = None
        self.proof_source = None
        body = QVBoxLayout(self)
        body.setContentsMargins(24, 24, 24, 20)
        body.setSpacing(16)
        body.addWidget(label('Prove it’s yours', 'title'))
        body.addWidget(label('Sign a message or file with a Warthog address. Verify a proof from anyone.', muted=True))
        self.tabs = QTabWidget()
        body.addWidget(self.tabs)
        self.build_sign()
        self.build_verify()
        footer = QHBoxLayout()
        footer.addWidget(label('Works offline · No transaction or fee', muted=True), 1)
        footer.addWidget(button('Close', self.close))
        body.addLayout(footer)
        self.finished.connect(self.cancel_work)
        scroll_dialog(self)
        self.refresh_addresses()

    def build_sign(self):
        page = QWidget()
        body = QVBoxLayout(page)
        body.setContentsMargins(0, 12, 0, 0)
        source, form = panel('1. Choose what to sign', 'Your private key stays in your wallet.')
        form.addWidget(label('Signing address'))
        self.sign_address = QComboBox()
        self.sign_address.setAccessibleName('Signing address')
        self.sign_address.setMinimumContentsLength(20)
        self.sign_address.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        form.addWidget(self.sign_address)
        self.address_detail = label('', muted=True)
        self.address_detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addWidget(self.address_detail)
        self.kind = QComboBox()
        self.kind.setAccessibleName('Content to sign')
        self.kind.addItem('Message', 'message')
        self.kind.addItem('File', 'file')
        form.addWidget(self.kind)
        self.sign_inputs = QStackedWidget()
        self.sign_inputs.setFixedHeight(136)
        self.message = editor('Message to sign', 'Paste a challenge, or write a message…')
        self.message.setMinimumHeight(100)
        self.sign_inputs.addWidget(self.message)
        file_page = QWidget()
        file_body = QVBoxLayout(file_page)
        file_body.setContentsMargins(0, 0, 0, 0)
        file_body.setSpacing(12)
        row, self.sign_file = path_row('File to sign', self.choose_sign_file)
        file_body.addWidget(row)
        file_body.addWidget(label('The signature covers the file’s exact contents. Share the original file with its signature document.', muted=True))
        file_body.addStretch()
        self.sign_inputs.addWidget(file_page)
        form.addWidget(self.sign_inputs)
        self.sign_hint = label('Messages keep their exact spacing and line breaks. Up to 16 KiB.', muted=True)
        form.addWidget(self.sign_hint)
        self.sign_progress = QProgressBar()
        self.sign_progress.setRange(0, 0)
        self.sign_progress.setAccessibleName('Preparing signature')
        self.sign_progress.hide()
        form.addWidget(self.sign_progress)
        actions = QHBoxLayout()
        self.sign_button = button('Review and sign', self.start_sign, True)
        self.cancel_sign_button = button('Cancel', self.cancel_sign)
        self.cancel_sign_button.hide()
        actions.addWidget(self.sign_button)
        actions.addWidget(self.cancel_sign_button)
        form.addLayout(actions)
        output, result = panel('2. Share your proof', 'A signature document contains public data. Anyone with it can read the signed message.')
        self.sign_status = label('Your signature will appear here', 'proofVerdict')
        result.addWidget(self.sign_status)
        self.output = editor('Created signature document', 'Review and sign to create a shareable proof.', True)
        result.addWidget(self.output, 1)
        actions = QHBoxLayout()
        self.copy_button = button('Copy', self.copy_proof)
        self.save_button = button('Save signature…', self.save_proof)
        actions.addWidget(self.copy_button)
        actions.addWidget(self.save_button)
        result.addLayout(actions)
        self.try_button = button('Verify this proof →', self.try_proof)
        result.addWidget(self.try_button)
        body.addWidget(FlowGrid([source, output], columns=2, minimum=310))
        body.addWidget(label('A proof demonstrates access to the address’s private key. For a wallet claim, sign the service’s unique challenge.', muted=True))
        self.tabs.addTab(page, 'Sign')
        self.kind.currentIndexChanged.connect(self.sign_changed)
        self.sign_address.currentIndexChanged.connect(self.sign_changed)
        self.message.textChanged.connect(self.sign_changed)
        self.sign_file.textChanged.connect(self.sign_changed)
        self.sign_changed()

    def build_verify(self):
        page = QWidget()
        body = QVBoxLayout(page)
        body.setContentsMargins(0, 12, 0, 0)
        source, form = panel('1. Check a proof', 'Enter the address you expect. Get it from a source you trust.')
        form.addWidget(label('Expected Warthog address'))
        self.expected = QLineEdit()
        self.expected.setAccessibleName('Expected signer address')
        self.expected.setPlaceholderText('48-character address you expect to verify')
        self.expected.setMaxLength(256)
        form.addWidget(self.expected)
        form.addWidget(button('Open signature document…', self.open_proof))
        self.proof_input = editor('Signature document to verify', 'Or paste a WartNode signature document here…')
        self.proof_input.setFixedHeight(180)
        form.addWidget(self.proof_input, 1)
        self.verify_file_row, self.verify_file = path_row('Original file to verify', self.choose_verify_file)
        form.addWidget(self.verify_file_row)
        self.verify_file_row.hide()
        self.verify_progress = QProgressBar()
        self.verify_progress.setRange(0, 0)
        self.verify_progress.setAccessibleName('Verifying file contents')
        self.verify_progress.hide()
        form.addWidget(self.verify_progress)
        actions = QHBoxLayout()
        self.verify_button = button('Verify signature', self.start_verify, True)
        self.cancel_verify_button = button('Cancel', self.cancel_verify)
        self.cancel_verify_button.hide()
        actions.addWidget(self.verify_button)
        actions.addWidget(self.cancel_verify_button)
        form.addLayout(actions)
        form.addStretch()
        result, details = panel('2. See the result', 'Verification uses the public key and content. No wallet unlock is needed.')
        self.verdict = label('Ready to verify', 'proofVerdict')
        details.addWidget(self.verdict)
        self.verify_detail = label('Paste a proof and enter the expected address to begin.', muted=True)
        self.verify_detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details.addWidget(self.verify_detail)
        self.verified_content = editor('Verified content', 'Verified message or file details appear here.', True)
        self.verified_content.setMinimumHeight(100)
        details.addWidget(self.verified_content, 1)
        details.addWidget(label('A valid proof does not establish a real-world identity or a trusted signing time.', muted=True))
        body.addWidget(FlowGrid([source, result], columns=2, minimum=310))
        self.tabs.addTab(page, 'Verify')
        for control in (self.expected, self.proof_input, self.verify_file):
            control.textChanged.connect(self.verify_changed)

    def refresh_addresses(self, selected=None):
        previous = self.sign_address.currentData()
        desired = selected or previous
        self.sign_address.blockSignals(True)
        self.sign_address.clear()
        for row in self.owner.wallets:
            self.sign_address.addItem(row['label'], row['address'])
        index = self.sign_address.findData(desired)
        if index >= 0:
            self.sign_address.setCurrentIndex(index)
        self.sign_address.blockSignals(False)
        self.address_detail.setText(self.sign_address.currentData() or 'Create or import an address on the Wallet page to sign.')
        if self.sign_address.currentData() != previous:
            self.sign_changed()

    def sign_changed(self, *_):
        self.sign_revision += 1
        self.sign_cancel.set()
        self.sign_inputs.setCurrentIndex(self.kind.currentIndex())
        self.address_detail.setText(self.sign_address.currentData() or 'Create or import an address on the Wallet page to sign.')
        self.proof = None
        self.proof_source = None
        self.output.clear()
        self.sign_status.setText('Your signature will appear here')
        for control in (self.copy_button, self.save_button, self.try_button):
            control.setEnabled(False)
        self.sign_hint.setText('Messages keep their exact spacing and line breaks. Up to 16 KiB.'
                               if self.kind.currentData() == 'message' else 'Files are read locally, up to 16 GiB. Their contents are never uploaded.')

    def verify_changed(self, *_):
        self.verify_revision += 1
        self.verify_cancel.set()
        self.set_verdict('Ready to verify', 'Inputs changed. Verify again to check this proof.')
        self.verified_content.clear()
        try:
            is_file = proofs.loads(self.proof_input.toPlainText())['kind'] == 'file'
        except ValueError:
            is_file = False
        self.verify_file_row.setVisible(is_file)

    def set_verdict(self, title, detail, state='pending'):
        self.verdict.setText(title)
        self.verdict.setProperty('proofState', state)
        self.verdict.style().unpolish(self.verdict)
        self.verdict.style().polish(self.verdict)
        self.verify_detail.setText(detail)

    def choose_sign_file(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Choose a file to sign')
        if path:
            self.sign_file.setText(path)

    def choose_verify_file(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Choose the original signed file')
        if path:
            self.verify_file.setText(path)

    def cancel_sign(self):
        self.sign_revision += 1
        self.sign_cancel.set()
        self.sign_status.setText('Signing cancelled')

    def cancel_verify(self):
        self.verify_revision += 1
        self.verify_cancel.set()
        self.set_verdict('Verification cancelled', 'No verification result was retained.')

    def cancel_work(self, *_):
        if self.sign_busy:self.cancel_sign()
        if self.verify_busy:self.cancel_verify()

    def lock_signing(self):
        if self.sign_busy:
            self.cancel_sign()
            self.sign_status.setText('Profile locked. Review again to sign.')

    def set_sign_busy(self, busy):
        self.sign_busy = busy
        self.sign_button.setEnabled(not busy)
        self.sign_progress.setVisible(busy)
        self.cancel_sign_button.setVisible(busy)

    def start_sign(self):
        if self.sign_busy:
            return
        if not self.sign_address.currentData():
            self.sign_status.setText('Create or import an address first')
            return
        if not self.owner.profile_unlocked:
            self.owner.ensure_profile(self.start_sign)
            return
        self.sign_changed()
        revision, generation = self.sign_revision, self.owner.session_generation
        address, kind = self.sign_address.currentData(), self.kind.currentData()
        message, path = self.message.toPlainText(), self.sign_file.text()
        cancel = self.sign_cancel = threading.Event()

        def valid():
            return (revision == self.sign_revision and generation == self.owner.session_generation
                    and self.owner.profile_unlocked and not cancel.is_set())

        def authorized():
            return generation == self.owner.session_generation and self.owner.profile_unlocked

        def discard_review(result):
            if result:
                self.owner.submit(lambda: self.owner.signer.call('cancel_proof_review', token=result['token']), quiet=True)

        def prepare():
            content = proofs.fingerprint(path, cancel) if kind == 'file' else {'message': message}
            if cancel.is_set():return None
            return self.owner.signer.call('review_proof', _valid=authorized,
                                          address=address, kind=kind, content=content)

        def reviewed(result, error):
            if not result or not valid():
                self.set_sign_busy(False)
                discard_review(result)
                if error and valid():
                    self.sign_status.setText(error)
                return
            self.sign_progress.hide()
            self.cancel_sign_button.hide()
            if not self.confirm(result) or not valid():
                self.set_sign_busy(False)
                if valid():
                    self.sign_status.setText('Signing cancelled')
                discard_review(result)
                return
            self.sign_progress.show()
            self.cancel_sign_button.show()
            self.sign_status.setText('Creating signature…')
            self.owner.submit(lambda: self.owner.signer.call('sign_proof', _valid=authorized, token=result['token'])
                              if valid() else None, signed, quiet=True)

        def signed(result, error):
            self.set_sign_busy(False)
            if not valid():
                return
            if not result:
                self.sign_status.setText(error or 'Review again to sign.')
                return
            self.proof, self.proof_source = result, path if kind == 'file' else None
            self.output.setPlainText(proofs.dumps(result))
            self.sign_status.setText('Signature ready')
            for control in (self.copy_button, self.save_button, self.try_button):
                control.setEnabled(True)

        self.set_sign_busy(True)
        self.sign_status.setText('Reading file…' if kind == 'file' else 'Preparing review…')
        self.owner.submit(prepare, reviewed, quiet=True)

    def confirm(self, reviewed):
        dialog = QDialog(self)
        dialog.setWindowTitle('Review signature')
        dialog.resize(620, 560)
        body = QVBoxLayout(dialog)
        body.addWidget(label('Review your signature', 'title'))
        body.addWidget(label('Signing address', muted=True))
        address = label(reviewed['address'])
        address.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(address)
        body.addWidget(label('Exact message' if reviewed['kind'] == 'message' else 'File fingerprint', muted=True))
        content = reviewed['content']
        preview = editor('Content approved for signing', '', True)
        preview.setPlainText(content['message'] if reviewed['kind'] == 'message' else
            f"File: {content['name']}\nBytes: {content['size']:,}\nSHA-256:\n{content['sha256']}")
        body.addWidget(preview)
        body.addWidget(label('This proves access to your address’s private key. It creates no transaction. Share only messages you intend others to read.', muted=True))
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        approve = controls.addButton('Sign message' if reviewed['kind'] == 'message' else 'Sign file fingerprint',
                                     QDialogButtonBox.ButtonRole.AcceptRole)
        approve.setProperty('primary', True)
        controls.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
        approve.setAutoDefault(False)
        controls.accepted.connect(dialog.accept)
        controls.rejected.connect(dialog.reject)
        body.addWidget(controls)
        scroll_dialog(dialog)
        self.owner.secret_dialogs[dialog] = preview.clear
        try:
            return dialog.exec() == QDialog.DialogCode.Accepted
        finally:
            preview.clear()
            self.owner.secret_dialogs.pop(dialog, None)
            dialog.deleteLater()

    def copy_proof(self):
        if self.proof:
            QApplication.clipboard().setText(proofs.dumps(self.proof))
            self.sign_status.setText('Signature copied')

    def save_proof(self):
        if not self.proof:
            return
        suggested = self.proof['content'].get('name', 'message') + proofs.EXTENSION
        path, _ = QFileDialog.getSaveFileName(self, 'Save signature document', suggested,
                                             'WartNode signature (*.wartproof)')
        if not path:
            return
        if not path.lower().endswith(proofs.EXTENSION):
            self.sign_status.setText('Save the signature with the .wartproof extension.')
            return
        if self.proof_source and Path(path).resolve() == Path(self.proof_source).resolve():
            self.sign_status.setText('Choose a separate signature document to keep the original file.')
            return
        proof = self.proof
        def saved(result, error):
            self.sign_status.setText(error or 'Signature saved')
        self.owner.submit(lambda: atomic_json(path, proof), saved, quiet=True)

    def try_proof(self):
        if self.proof:
            self.expected.setText(self.proof['address'])
            self.expected.setCursorPosition(0)
            self.proof_input.setPlainText(proofs.dumps(self.proof))
            self.verify_file.setText(self.proof_source or '')
            self.tabs.setCurrentIndex(1)

    def open_proof(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Open signature document', '',
                                             'WartNode signature (*.wartproof *.json);;All files (*)')
        if not path:
            return
        self.verify_changed()
        revision = self.verify_revision
        def loaded(result, error):
            if revision != self.verify_revision:
                return
            if result:
                self.proof_input.setPlainText(proofs.dumps(result))
                self.verify_file.clear()
            else:
                self.proof_input.clear()
                self.verify_file.clear()
                self.set_verdict('Could not open proof', error, 'invalid')
        self.owner.submit(lambda: proofs.read(path), loaded, quiet=True)

    def start_verify(self):
        if self.verify_busy:
            return
        self.verify_changed()
        revision = self.verify_revision
        text, expected, path = self.proof_input.toPlainText(), self.expected.text(), self.verify_file.text()
        cancel = self.verify_cancel = threading.Event()
        self.verify_busy = True
        self.verify_button.setEnabled(False)
        self.verify_progress.show()
        self.cancel_verify_button.show()
        self.set_verdict('Verifying…', 'Checking the signature, address, and content locally.')
        def checked(result, error):
            self.verify_busy = False
            self.verify_button.setEnabled(True)
            self.verify_progress.hide()
            self.cancel_verify_button.hide()
            if revision != self.verify_revision:
                return
            if not result:
                self.set_verdict('Not verified', error, 'invalid')
                return
            content = result['content']
            self.set_verdict('Verified for this address', result['address'], 'verified')
            self.verified_content.setPlainText(content['message'] if result['kind'] == 'message' else
                f"File contents match\nOriginal name: {content['name']}\nBytes: {content['size']:,}\nSHA-256:\n{content['sha256']}")
        self.owner.submit(lambda: proofs.verify(proofs.loads(text), expected, path or None, cancel), checked, quiet=True)


class ProofActions:
    def show_proofs(self):
        if not hasattr(self, 'proof_dialog'):
            self.proof_dialog = ProofDialog(self)
        self.proof_dialog.refresh_addresses(self.selected_public_address())
        self.proof_dialog.show()
        self.proof_dialog.raise_()
        self.proof_dialog.activateWindow()
