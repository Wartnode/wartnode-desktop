"""Exercise the real private helper, review dialogs and offline verification."""
import copy
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QPlainTextEdit
import test_ui
from test_core import OWNER, OTHER
from wartnode import proofs

until = test_ui.until


class ProofUITests(unittest.TestCase):
    setUp = test_ui.NativeUITests.setUp
    tearDown = test_ui.NativeUITests.tearDown

    def configure(self):
        window = self.window
        window.timer.stop()
        window.signer.call('setup', password='synthetic-proof-ui-password')
        window.signer.call('import', kind='private', secret='00'*31+'01', label='Main wallet')
        window.signer.call('import', kind='private', secret='00'*31+'02', label='Mining wallet')
        window.refresh_wallets();until(lambda:not window.tasks)
        window.select_unlocked(OWNER);until(lambda:not window.tasks)
        window.show_proofs()
        dialog = window.proof_dialog
        dialog.sign_address.setCurrentIndex(dialog.sign_address.findData(OWNER))
        return dialog

    def test_message_review_export_and_locked_verification_then_tampering(self):
        dialog = self.configure()
        message = 'Claim this address for Johnny\nChallenge: prototype-ui-test'
        dialog.message.setPlainText(message)
        observed = []
        timer = QTimer()
        def approve():
            for review in self.window.secret_dialogs:
                if review.windowTitle() == 'Review signature':
                    observed.append(review.findChild(QPlainTextEdit).toPlainText())
                    review.accept();timer.stop();return
        timer.timeout.connect(approve);timer.start(10)
        try:
            dialog.start_sign();until(lambda:not dialog.sign_busy)
        finally:timer.stop()
        self.assertEqual(observed, [message])
        proof = proofs.loads(dialog.output.toPlainText())
        self.assertEqual(proofs.verify(proof, OWNER)['content']['message'], message)
        dialog.copy_proof()
        self.assertEqual(test_ui.APP.clipboard().text(), proofs.dumps(proof))
        target = Path(self.temp.name) / 'claim.wartproof'
        with patch('wartnode.proof_ui.QFileDialog.getSaveFileName', return_value=(str(target), '')):
            dialog.save_proof();until(lambda:not self.window.tasks)
        self.assertEqual(proofs.read(target), proof)
        dialog.try_proof()
        self.window.lock_wallet();until(lambda:not self.window.tasks)
        with patch.object(self.window.signer, 'call', side_effect=AssertionError('Verification contacted the wallet')):
            dialog.start_verify();until(lambda:not dialog.verify_busy)
        self.assertIn('Verified for this address', dialog.verdict.text())
        self.assertEqual(dialog.verified_content.toPlainText(), message)
        dialog.expected.setText(OTHER)
        self.assertEqual(dialog.verdict.text(), 'Ready to verify')
        self.assertEqual(dialog.verified_content.toPlainText(), '')
        dialog.start_verify();until(lambda:not dialog.verify_busy)
        self.assertIn('Different signer', dialog.verify_detail.text())
        dialog.expected.setText(OWNER)
        changed = copy.deepcopy(proof);changed['content']['message'] += '!'
        dialog.proof_input.setPlainText(proofs.dumps(changed))
        dialog.start_verify();until(lambda:not dialog.verify_busy)
        self.assertEqual(dialog.verdict.text(), 'Not verified')

    def test_real_file_signing_and_verification_detect_modified_file(self):
        dialog = self.configure()
        source = Path(self.temp.name) / 'release.bin'
        source.write_bytes(b'\x00\xffsynthetic release\n')
        dialog.kind.setCurrentIndex(1)
        dialog.sign_file.setText(str(source))
        with patch.object(dialog, 'confirm', return_value=True):
            dialog.start_sign();until(lambda:not dialog.sign_busy)
        self.assertIsNotNone(dialog.proof, dialog.sign_status.text())
        dialog.try_proof()
        dialog.start_verify();until(lambda:not dialog.verify_busy)
        self.assertIn('Verified', dialog.verdict.text())
        source.write_bytes(b'changed')
        dialog.start_verify();until(lambda:not dialog.verify_busy)
        self.assertEqual(dialog.verdict.text(), 'Not verified')
        self.assertIn('file does not match', dialog.verify_detail.text())

    def test_session_lock_during_review_rejects_signing_and_closes_approval(self):
        dialog = self.configure()
        dialog.message.setPlainText('Never sign after the session locks')
        timer = QTimer()
        def lock_review():
            if any(d.windowTitle() == 'Review signature' for d in self.window.secret_dialogs):
                timer.stop();self.window.lock_wallet()
        timer.timeout.connect(lock_review);timer.start(10)
        try:
            dialog.start_sign();until(lambda:not dialog.sign_busy and not self.window.tasks)
        finally:timer.stop()
        self.assertIsNone(dialog.proof)
        self.assertEqual(dialog.output.toPlainText(), '')
        self.assertTrue(self.window.signer.call('list')['locked'])
        self.assertFalse(self.window.secret_dialogs)

    def test_cancelled_file_read_never_prompts_or_exports_a_signature(self):
        dialog = self.configure()
        dialog.kind.setCurrentIndex(1)
        dialog.sign_file.setText('synthetic.bin')
        began = threading.Event()
        def read_file(path, cancel):
            began.set()
            if not cancel.wait(5):raise AssertionError('Hashing was not cancelled')
            raise ValueError('File hashing cancelled.')
        with patch('wartnode.proofs.fingerprint', side_effect=read_file), \
             patch.object(dialog, 'confirm', side_effect=AssertionError('Unexpected review')):
            dialog.start_sign();until(began.is_set)
            dialog.cancel_sign();until(lambda:not dialog.sign_busy)
        self.assertIsNone(dialog.proof)
        self.assertFalse(dialog.save_button.isEnabled())
        self.assertFalse(self.window.signer.call('list')['locked'])

    def test_loading_proof_never_uses_its_address_as_the_expected_identity(self):
        dialog = self.configure()
        dialog.message.setPlainText('Signed claim')
        with patch.object(dialog, 'confirm', return_value=True):
            dialog.start_sign();until(lambda:not dialog.sign_busy)
        target = Path(self.temp.name) / 'claim.wartproof'
        target.write_text(proofs.dumps(dialog.proof), encoding='utf-8')
        dialog.expected.clear()
        with patch('wartnode.proof_ui.QFileDialog.getOpenFileName', return_value=(str(target), '')):
            dialog.open_proof();until(lambda:not self.window.tasks)
        self.assertEqual(dialog.expected.text(), '')
        dialog.start_verify();until(lambda:not dialog.verify_busy)
        self.assertEqual(dialog.verdict.text(), 'Not verified')
        self.assertIn('expected', dialog.verify_detail.text())

    def test_selecting_a_different_public_address_clears_the_old_created_proof(self):
        dialog = self.configure()
        dialog.message.setPlainText('Claim the first address')
        with patch.object(dialog, 'confirm', return_value=True):
            dialog.start_sign();until(lambda:not dialog.sign_busy)
        self.assertEqual(dialog.proof['address'], OWNER)
        dialog.refresh_addresses(OTHER)
        self.assertEqual(dialog.sign_address.currentData(), OTHER)
        self.assertIsNone(dialog.proof)
        self.assertFalse(dialog.copy_button.isEnabled())
        self.assertEqual(dialog.output.toPlainText(), '')

    def test_workspace_renders_at_small_size_and_captures_sign_and_verify(self):
        dialog = self.configure()
        directory = Path(__file__).resolve().parents[1] / 'work/proof-preview'
        directory.mkdir(parents=True, exist_ok=True)
        dialog.resize(940, 720)
        dialog.message.setPlainText('I control this Warthog address and would like to claim it as Johnny.\n\nChallenge: wartnode-prototype-demo')
        test_ui.QTest.qWait(80)
        self.assertTrue(dialog.grab().save(str(directory / 'sign.png')))
        with patch.object(dialog, 'confirm', return_value=True):
            dialog.start_sign();until(lambda:not dialog.sign_busy)
        dialog.try_proof();dialog.start_verify();until(lambda:not dialog.verify_busy)
        test_ui.QTest.qWait(80)
        self.assertTrue(dialog.grab().save(str(directory / 'verified.png')))
        dialog.expected.setText(OTHER);dialog.start_verify();until(lambda:not dialog.verify_busy)
        self.assertTrue(dialog.grab().save(str(directory / 'different-signer.png')))
        dialog.resize(680, 600)
        test_ui.APP.processEvents()
        from PySide6.QtWidgets import QScrollArea
        scroll = dialog.findChild(QScrollArea)
        self.assertLessEqual(scroll.widget().minimumSizeHint().width(), scroll.viewport().width())
        self.assertTrue(dialog.grab().save(str(directory / 'compact.png')))


if __name__ == '__main__':unittest.main()
