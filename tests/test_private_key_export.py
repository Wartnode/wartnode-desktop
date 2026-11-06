"""Per-address key portability and reveal lifecycle, using disposable keys only."""
import json
import tempfile
import unittest
from unittest.mock import patch

from coincurve import PrivateKey
from cryptography.fernet import Fernet
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog, QPushButton

from wartnode import settings
from wartnode.compat import wallet_utils as crypto
from wartnode.key_ui import PrivateKeyDialog
from wartnode.signer import Signer
from test_core import OWNER, OTHER
import test_ui

APP = test_ui.APP
until = test_ui.until

PASSWORD = 'synthetic-export-password'
SECRET = f'{1:064x}'


class PrivateKeySignerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.signer = Signer(self.temp.name)
        self.signer.call('setup', {'password': PASSWORD})
        for scalar in (1, 2):
            self.signer.call('import', {'kind': 'private', 'secret': f'{scalar:064x}'})

    def tearDown(self):
        self.signer.lock()
        self.signer.store.con.close()
        self.temp.cleanup()

    def export(self, address=OWNER, password=PASSWORD):
        return self.signer.call('export_private_key', {'address': address, 'password': password})

    def test_exports_requested_address_offline_without_changing_selected_address(self):
        with patch('wartnode.signer.NodeRPC', side_effect=AssertionError('Network access')):
            result = self.export()
        self.assertEqual(result, {'address': OWNER, 'private_key': SECRET})
        self.assertEqual(self.signer.address, OTHER)
        with tempfile.TemporaryDirectory() as destination:
            other = Signer(destination)
            try:
                other.call('setup', {'password': 'synthetic-destination-password'})
                imported = other.call('import', {'kind': 'private', 'secret': result['private_key']})
                self.assertEqual(imported['address'], OWNER)
            finally:
                other.store.con.close()
        public = json.dumps(self.signer.call('list', {}))
        self.assertNotIn(SECRET, public)
        self.assertNotIn(SECRET.encode(), self.signer.store.snapshot())

    def test_requires_current_password_and_unlocked_unexpired_profile_before_decryption(self):
        with patch.object(self.signer.store, 'private_key', side_effect=AssertionError('Secret read')):
            for password in ('wrong-password', '', None):
                with self.subTest(password=password), self.assertRaises(ValueError):
                    self.export(password=password)
            self.signer.lock()
            with self.assertRaisesRegex(ValueError, 'Unlock'):
                self.export()
            self.signer.call('unlock', {'password': PASSWORD})
            settings.save(self.signer.directory, auto_lock_minutes=5)
            self.signer.last_active -= 301
            with self.assertRaisesRegex(ValueError, 'Unlock'):
                self.export()

    def test_password_change_revokes_old_password_for_each_reveal(self):
        replacement = 'synthetic-new-export-password'
        self.signer.call('change_password', {'old_password': PASSWORD, 'new_password': replacement})
        self.signer.call('unlock', {'password': replacement})
        with self.assertRaises(ValueError):
            self.export()
        self.assertEqual(self.export(password=replacement)['private_key'], SECRET)

    def test_rejects_foreign_address_and_a_key_that_does_not_match_its_address(self):
        foreign, _ = crypto.wallet_from_pkhex(f'{3:064x}')
        with self.assertRaisesRegex(ValueError, 'not in the profile'):
            self.export(address=foreign)
        payload = json.dumps({'address': OWNER, 'private_key': f'{2:064x}'}).encode()
        token = Fernet(self.signer.profile_key).encrypt(payload).decode('ascii')
        with self.signer.store.con:
            self.signer.store.con.execute('UPDATE wallets SET pk=? WHERE address=?', (token, OWNER))
        with self.assertRaisesRegex(ValueError, 'does not match'):
            self.export()

    def test_generated_and_finalized_vanity_keys_export_to_the_same_address(self):
        generated = self.signer.call('create', {})
        result = self.export(address=generated['address'])
        self.assertEqual(crypto.wallet_from_pkhex(result['private_key'])[0], generated['address'])
        address, _ = crypto.wallet_from_pkhex(f'{8:064x}')
        with patch('wartnode.vanity_keys.PrivateKey', return_value=PrivateKey.from_int(1)):
            run = self.signer.call('vanity_prepare', {'prefix': address[0]})
        candidate = {'offset': f'{7:064x}', 'transform': 'identity', 'address': address}
        self.signer.call('vanity_found', {'job_id': run['job_id'], 'run_id': run['run_id'],
                         'checkpoint': run['checkpoint'], 'candidate': candidate})
        self.signer.call('vanity_finalize', {'job_id': run['job_id']})
        self.assertEqual(self.export(address=address), {'address': address, 'private_key': f'{8:064x}'})

    def test_changed_profile_key_cannot_export_using_stale_unlock(self):
        self.signer.profile_key = Fernet.generate_key()
        with patch.object(self.signer.store, 'private_key', side_effect=AssertionError('Secret read')):
            with self.assertRaisesRegex(ValueError, 'profile changed'):
                self.export()
        self.assertIsNone(self.signer.profile_key)

    def test_connection_change_during_export_discards_authorization(self):
        binding = self.signer.binding
        with patch.object(self.signer, 'connection_context', side_effect=[binding, (binding[0], 999)]):
            with self.assertRaisesRegex(ValueError, 'connection changed'):
                self.export()
        self.assertIsNone(self.signer.profile_key)


class PrivateKeyDialogTests(unittest.TestCase):
    def setUp(self):
        self.dialog = PrivateKeyDialog(None, OWNER, SECRET)
        self.dialog.show()
        APP.processEvents()

    def tearDown(self):
        self.dialog.clear_secret()
        self.dialog.close()
        self.dialog.deleteLater()
        APP.processEvents()
        QApplication.clipboard().clear()

    def test_copy_expires_and_close_clears_key_and_clipboard(self):
        self.assertTrue(self.dialog.key.isReadOnly())
        self.dialog.copy_button.click()
        self.assertEqual(QApplication.clipboard().text(), SECRET)
        self.assertTrue(self.dialog.clipboard_timer.isActive())
        self.dialog.clipboard_timer.start(1)
        until(lambda: QApplication.clipboard().text() == '')
        self.assertEqual(self.dialog.key.text(), SECRET)
        # Keyboard/context-menu copies receive the same timeout and cleanup.
        self.dialog.key.selectAll()
        self.dialog.key.copy()
        self.assertTrue(self.dialog.clipboard_timer.isActive())
        self.dialog.reject()
        self.assertEqual(self.dialog.key.text(), '')
        self.assertEqual(QApplication.clipboard().text(), '')
        self.assertFalse(self.dialog.clipboard_timer.isActive())

    def test_cleanup_preserves_replacement_clipboard_content(self):
        self.dialog.copy_key()
        QApplication.clipboard().setText('replacement clipboard content')
        self.dialog.accept()
        self.assertEqual(self.dialog.key.text(), '')
        self.assertEqual(QApplication.clipboard().text(), 'replacement clipboard content')


class PrivateKeyWalletTests(unittest.TestCase):
    setUp = test_ui.NativeUITests.setUp
    tearDown = test_ui.NativeUITests.tearDown

    def configure(self):
        self.window.timer.stop()
        self.window.signer.call('setup', password=PASSWORD)
        for scalar in (1, 2):
            self.window.signer.call('import', kind='private', secret=f'{scalar:064x}')
        self.window.refresh_wallets()
        until(lambda: not self.window.tasks)
        self.window.select_unlocked(OTHER)
        until(lambda: not self.window.tasks)
        self.window.address_table.selectRow(0)

    def test_wallet_action_reveals_selected_address_and_scrubs_after_close(self):
        self.configure()
        window = self.window
        action = next(b for b in window.wallet_tabs.findChildren(QPushButton) if b.text() == 'Private key…')
        observed = {}
        timer = QTimer()
        def inspect():
            for dialog in list(window.secret_dialogs):
                if isinstance(dialog, PrivateKeyDialog):
                    observed['secret'] = dialog.key.text()
                    dialog.copy_key()
                    dialog.accept()
                    observed['cleared'] = dialog.key.text() == ''
                    timer.stop()
        timer.timeout.connect(inspect)
        timer.start(20)
        try:
            with patch.object(window, 'password', return_value=PASSWORD) as password:
                action.click()
                until(lambda: bool(observed) and not window.tasks)
            self.assertIn(OWNER, password.call_args.args[1])
            self.assertEqual(observed, {'secret': SECRET, 'cleared': True})
            self.assertEqual(window.address, OTHER)
            self.assertEqual(QApplication.clipboard().text(), '')
            self.assertFalse(window.secret_dialogs)
            self.assertNotIn(SECRET, window.notice.text())
        finally:
            timer.stop()

    def test_cancelled_or_wrong_password_never_opens_reveal(self):
        self.configure()
        with patch('wartnode.key_ui.PrivateKeyDialog', side_effect=AssertionError('Secret dialog opened')):
            with patch.object(self.window, 'password', return_value=None):
                self.window.reveal_selected_private_key()
            with patch.object(self.window, 'password', return_value='wrong-password'):
                self.window.reveal_selected_private_key()
                until(lambda: not self.window.tasks)
        self.assertIn('password is incorrect', self.window.notice.text())
        self.assertFalse(self.window.secret_dialogs)

    def test_lock_clears_display_clipboard_and_rejects_open_reveal(self):
        self.configure()
        observed = {}
        timer = QTimer()
        def lock():
            for dialog in list(self.window.secret_dialogs):
                if isinstance(dialog, PrivateKeyDialog):
                    dialog.copy_key()
                    self.window.lock_wallet()
                    observed['cleared'] = dialog.key.text() == ''
                    observed['rejected'] = dialog.result() == QDialog.DialogCode.Rejected
                    timer.stop()
        timer.timeout.connect(lock)
        timer.start(20)
        try:
            with patch.object(self.window, 'password', return_value=PASSWORD):
                self.window.reveal_selected_private_key()
                until(lambda: bool(observed) and not self.window.tasks)
            self.assertEqual(observed, {'cleared': True, 'rejected': True})
            self.assertEqual(QApplication.clipboard().text(), '')
            self.assertFalse(self.window.secret_dialogs)
        finally:
            timer.stop()

    def test_late_result_after_lock_is_discarded(self):
        self.configure()
        window = self.window
        with patch.object(window, 'password', return_value=PASSWORD), patch.object(window, 'submit') as submit:
            window.reveal_selected_private_key()
        callback = submit.call_args.args[1]
        window.lock_wallet()
        result = {'address': OWNER, 'private_key': SECRET}
        with patch('wartnode.key_ui.PrivateKeyDialog', side_effect=AssertionError('Stale key shown')):
            callback(result, None)
        self.assertEqual(result, {})
        until(lambda: not window.tasks)

    def test_shutdown_discards_queued_secret_before_callback(self):
        self.configure()
        result = {'address': OWNER, 'private_key': SECRET}
        with patch('wartnode.key_ui.PrivateKeyDialog', side_effect=AssertionError('Shutdown key shown')):
            callback = unittest.mock.Mock()
            self.window.submit(lambda: result, callback)
            self.window.closing = True
            until(lambda: not self.window.tasks)
            callback.assert_not_called()
        self.assertEqual(result, {})

    def test_cancelling_unlock_does_not_start_an_export(self):
        self.configure()
        self.window.lock_wallet()
        until(lambda: not self.window.tasks)
        with patch.object(self.window, 'password', return_value=None), \
                patch.object(self.window, 'guard_signer', side_effect=AssertionError('Cancelled export sent')):
            self.window.reveal_selected_private_key()
        self.assertFalse(self.window.secret_dialogs)


if __name__ == '__main__':
    unittest.main()
