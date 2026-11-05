"""Native wallet workflows use one profile authorization and public totals."""
import unittest
from unittest.mock import patch
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QPlainTextEdit
import test_ui
from test_core import OWNER, OTHER

until=test_ui.until


class DashboardWalletTests(unittest.TestCase):
    setUp=test_ui.NativeUITests.setUp
    tearDown=test_ui.NativeUITests.tearDown

    def configure(self):
        self.window.timer.stop()
        self.window.signer.call('setup',password='synthetic-profile-password')
        for secret,name in [('01','Main'),('02','Mining')]:
            self.window.signer.call('import',kind='private',secret='00'*31+secret,label=name)
        self.window.refresh_wallets();until(lambda:not self.window.tasks)
        self.window.select_unlocked(OTHER);until(lambda:not self.window.tasks)

    def test_combined_reserved_balance_remains_public_after_lock_and_partial_reads_are_not_totals(self):
        self.configure();window=self.window
        original=window.signer.call
        rows=[dict(address=OWNER,balance=300000000,reserved=100000000,available=200000000),
              dict(address=OTHER,balance=500000000,reserved=0,available=500000000)]
        def call(action,**args):
            if action=='profile_balances':return dict(rows=list(rows),total=2,next_offset=None,source='local')
            return original(action,**args)
        with patch.object(window.signer,'call',side_effect=call):
            window.runtime.ready=True
            window.refresh_profile_balances();until(lambda:not window.tasks)
            self.assertEqual(window.overview_balance.value.text(),'7.00000000')
            self.assertEqual(window.wallet_balance.value.text(),'7.00000000')
            window.lock_wallet();until(lambda:not window.tasks)
            self.assertFalse(window.profile_unlocked)
            self.assertIsNone(original('list')['address'])
            self.assertEqual(window.overview_balance.value.text(),'7.00000000')
            self.assertTrue(window.receive_address.text())
            rows[1]=dict(address=OTHER,error='unavailable')
            window.refresh_profile_balances();until(lambda:not window.tasks)
            self.assertEqual(window.overview_balance.value.text(),'Unavailable')

    def test_new_address_asks_for_a_label_and_never_another_password(self):
        self.configure();window=self.window
        observed=[]
        timer=QTimer()
        def accept_phrase():
            for dialog in list(window.secret_dialogs):
                phrase=dialog.findChild(QPlainTextEdit)
                if phrase is not None:
                    observed.append(len(phrase.toPlainText().split()))
                    dialog.accept();timer.stop();return
        timer.timeout.connect(accept_phrase);timer.start(20)
        try:
            with patch.object(window,'password',side_effect=AssertionError('Repeated password prompt')), \
                 patch('wartnode.ui.QInputDialog.getText',return_value=('Workshop',True)):
                window.create_wallet();until(lambda:len(window.wallets)==3 and not window.tasks)
            self.assertEqual(observed,[24])
            self.assertIn('Workshop',[row['label'] for row in window.wallets])
        finally:timer.stop()

    def test_labels_contacts_and_default_address_are_reflected_in_the_native_lists(self):
        self.configure();window=self.window
        window.signer.call('edit_address',address=OTHER,label='Workshop',default=True)
        window.signer.call('save_contact',name='Alex',address=OWNER)
        window.refresh_wallets();until(lambda:not window.tasks)
        self.assertEqual(window.address_table.item(0,0).text(),'Workshop\n'+OTHER)
        self.assertIn('Default',window.address_table.item(0,1).text())
        self.assertEqual(window.contact_table.item(0,0).text(),'Alex')
        with patch('wartnode.ui.QInputDialog.getItem',return_value=('Alex · '+OWNER,True)):
            window.choose_profile_contact()
        self.assertEqual(window.recipient.text(),OWNER)

    def test_helper_crash_invalidates_ui_authorization_and_keeps_public_addresses(self):
        self.configure();window=self.window
        window.signer.process.kill();window.signer.process.wait(5)
        window.refresh_wallets();until(lambda:not window.tasks)
        self.assertFalse(window.profile_unlocked)
        self.assertIsNone(window.address)
        self.assertEqual(len(window.wallets),2)
        self.assertIn('signing helper stopped',window.notice.text())


if __name__ == '__main__':unittest.main()
