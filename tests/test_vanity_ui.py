"""Wallet integration, useful estimates, and actual native search/save controls."""
from pathlib import Path
import os
from unittest.mock import patch
from types import SimpleNamespace
import unittest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QPushButton
from PySide6.QtTest import QTest
from wartnode.paths import verified_component
import test_ui


class VanityUITests(unittest.TestCase):
    setUp = test_ui.NativeUITests.setUp

    def tearDown(self):
        if hasattr(self.window, 'vanity_dialog'):
            self.window.vanity_dialog.reject()
            test_ui.until(lambda:not self.window.tasks)
        self.window.runtime.stop_vanity()
        test_ui.NativeUITests.tearDown(self)

    def dialog(self):
        self.window.timer.stop()
        self.window.navigate(2)
        buttons = [button for button in self.window.findChildren(QPushButton) if button.text() == 'Vanity address']
        self.assertEqual(len(buttons), 1)
        buttons[0].click(); test_ui.APP.processEvents()
        test_ui.until(lambda:not self.window.tasks)
        self.assertEqual(self.window.stack.count(), 6)
        return self.window.vanity_dialog

    def test_wallet_modes_difficulty_and_combined_mining_boundary(self):
        dialog = self.dialog()
        self.assertEqual([dialog.mode.itemText(i) for i in range(3)],['Prefix','Suffix','Prefix + suffix'])
        dialog.prefix.setText('cafe')
        self.assertIn('Low difficulty', dialog.difficulty.text())
        self.assertIn('65,536', dialog.details_text.text())
        self.assertTrue(dialog.estimate_details.isHidden())
        self.assertTrue(dialog.options.isHidden())
        self.assertTrue(dialog.cancel_button.isHidden())
        self.assertTrue(dialog.save_button.isHidden())
        self.assertTrue(dialog.discard_button.isHidden())
        self.assertTrue(dialog.start_button.isEnabled())
        dialog.mode.setCurrentIndex(dialog.mode.findData('both'))
        dialog.suffix.setText('beef')
        self.assertIn('8 characters', dialog.estimate_note.text())
        with patch.object(type(self.window.runtime.miner), 'running', new_callable=lambda:property(lambda _:True)):
            dialog.refresh()
            self.assertTrue(dialog.start_button.isEnabled())
            dialog.prefix.setText('cafee')
            self.assertFalse(dialog.start_button.isEnabled())
            self.assertTrue(dialog.stop_mining.isVisible())
        dialog.mode.setCurrentIndex(dialog.mode.findData('suffix'))
        self.assertIn('Suffix estimates', dialog.details_text.text())
        self.assertTrue(dialog.prefix.isHidden())
        dialog.suffix.setText('0'*16)
        self.assertIn('Not practical', dialog.feasibility.text())
        self.assertIn('billion', dialog.details_text.text())
        dialog.options_toggle.click(); test_ui.APP.processEvents()
        self.assertTrue(dialog.backend.isVisible())
        for field in (dialog.mode, dialog.suffix, dialog.backend, dialog.name):
            self.assertGreaterEqual(field.height(), field.fontMetrics().height()+18)
        screenshot = Path(__file__).resolve().parents[1]/'work/test-screenshots/vanity-difficulty.png'
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        self.assertTrue(dialog.grab().save(str(screenshot)))

    def test_dialog_scrolls_on_a_small_logical_desktop(self):
        screen = SimpleNamespace(availableGeometry=lambda:QRect(0,0,980,520))
        with patch('wartnode.widgets.QApplication.primaryScreen', return_value=screen):
            dialog = self.dialog()
        dialog.mode.setCurrentIndex(dialog.mode.findData('both'))
        dialog.prefix.setText('cafe'); dialog.suffix.setText('beef')
        test_ui.APP.processEvents()
        self.assertLessEqual(dialog.height(), 480)
        self.assertLessEqual(dialog.width(), 956)
        self.assertGreater(dialog.scroll.verticalScrollBar().maximum(), 0)
        self.assertEqual(dialog.scroll.horizontalScrollBar().maximum(), 0)
        dialog.options_toggle.click(); dialog.details_toggle.click()
        test_ui.APP.processEvents()
        dialog.scroll.verticalScrollBar().setValue(dialog.scroll.verticalScrollBar().maximum())
        test_ui.APP.processEvents()
        self.assertEqual(dialog.start_button.visibleRegion().boundingRect().height(),dialog.start_button.height())
        self.assertEqual(dialog.close_button.visibleRegion().boundingRect().height(),dialog.close_button.height())

    def test_real_cpu_search_and_save_from_wallet_dialog(self):
        try:
            verified_component('vanity')
        except (OSError, KeyError, ValueError):
            self.skipTest('Build the vanity prototype worker first')
        self.window.signer.call('setup', password='synthetic-ui-vanity-password')
        self.window.profile_ready = self.window.profile_unlocked = True
        dialog = self.dialog()
        dialog.mode.setCurrentIndex(dialog.mode.findData('both'))
        dialog.prefix.setText('a'); dialog.suffix.setText('b')
        dialog.backend.setCurrentIndex(dialog.backend.findData('cpu'))
        dialog.start_button.click()
        test_ui.until(lambda:dialog.save_button.isEnabled(), seconds=30)
        self.assertIn('Match found', dialog.status.text())
        self.assertTrue(dialog.estimate_card.isHidden())
        self.assertTrue(dialog.name.isVisible())
        self.assertTrue(dialog.start_button.isHidden())
        self.assertTrue(dialog.cancel_button.isHidden())
        found_shot = Path(__file__).resolve().parents[1]/'work/test-screenshots/vanity-found.png'
        found_shot.parent.mkdir(parents=True,exist_ok=True)
        QTest.qWait(80)
        self.assertTrue(dialog.grab().save(str(found_shot)))
        self.assertEqual(self.window.signer.call('list')['wallets'], [])
        with patch.object(self.window, 'guard_signer', side_effect=ValueError('Synthetic verification rejection')):
            dialog.save_button.click()
            test_ui.until(lambda:not self.window.tasks)
            dialog.refresh()
            self.assertIn('Synthetic verification rejection', dialog.status.text())
            self.assertEqual(self.window.signer.call('list')['wallets'], [])
        dialog.save_button.click()
        test_ui.until(lambda:not self.window.tasks)
        wallets = self.window.signer.call('list')['wallets']
        self.assertEqual(len(wallets), 1)
        self.assertTrue(wallets[0]['address'].startswith('a'))
        self.assertTrue(wallets[0]['address'].endswith('b'))
        self.assertTrue(dialog.backup_button.isVisible())
        self.assertIn('Saved to wallet', dialog.status.text())
        self.assertTrue(dialog.pattern_fields.isHidden())
        self.assertTrue(dialog.estimate_card.isHidden())
        self.assertTrue(dialog.save_button.isHidden())
        screenshot = Path(__file__).resolve().parents[1]/'work/test-screenshots/vanity-saved.png'
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        self.assertTrue(dialog.grab().save(str(screenshot)))
        dialog.reject(); test_ui.until(lambda:not self.window.tasks)
        dialog.show(); test_ui.until(lambda:not self.window.tasks)
        self.assertEqual(dialog.status.text(),'Saved to wallet')
        dialog.start_button.click()
        self.assertTrue(dialog.pattern_fields.isVisible())
        self.assertFalse(self.window.runtime.vanity.running)
        self.assertEqual(dialog.progress.text(), '')

    def test_close_preserves_search_and_reopen_requires_manual_resume(self):
        self.window.signer.call('setup', password='synthetic-ui-vanity-password')
        self.window.profile_ready = self.window.profile_unlocked = True
        dialog = self.dialog()
        dialog.prefix.setText('0'*16)
        dialog.backend.setCurrentIndex(dialog.backend.findData('cpu'))
        dialog.start_button.click()
        test_ui.until(lambda:not dialog.busy and self.window.runtime.vanity.running)
        job_id = dialog.record['job_id']
        dialog.reject()
        test_ui.until(lambda:not self.window.tasks)
        self.assertFalse(self.window.runtime.vanity.running)
        self.assertEqual(self.window.signer.call('vanity_status')['record']['state'], 'paused')
        dialog.show()
        test_ui.until(lambda:not self.window.tasks)
        self.assertEqual(dialog.record['job_id'], job_id)
        self.assertEqual(dialog.start_button.text(), 'Resume search')
        self.assertFalse(self.window.runtime.vanity.running)
        with patch.object(type(self.window.runtime.miner), 'running', new_callable=lambda:property(lambda _:True)):
            dialog.refresh()
            self.assertFalse(dialog.start_button.isEnabled())
        dialog.refresh()
        dialog.start_button.click()
        test_ui.until(lambda:not dialog.busy and self.window.runtime.vanity.running)
        self.assertEqual(dialog.record['job_id'], job_id)

    @unittest.skipUnless(os.name == 'nt', 'Windows native suspend message')
    def test_suspend_pauses_search_and_wake_does_not_resume(self):
        import ctypes
        from ctypes import wintypes
        self.window.signer.call('setup', password='synthetic-ui-vanity-password')
        self.window.profile_ready = self.window.profile_unlocked = True
        dialog = self.dialog()
        dialog.prefix.setText('0'*16)
        dialog.backend.setCurrentIndex(dialog.backend.findData('cpu'))
        dialog.start_button.click()
        test_ui.until(lambda:not dialog.busy and self.window.runtime.vanity.running)
        msg = wintypes.MSG()
        msg.message, msg.wParam = 0x0218, 4
        self.window.nativeEvent(b'windows_generic_MSG', ctypes.addressof(msg))
        test_ui.until(lambda:not self.window.tasks)
        self.assertEqual(self.window.signer.call('vanity_status')['record']['state'], 'paused')
        msg.wParam = 18
        self.window.nativeEvent(b'windows_generic_MSG', ctypes.addressof(msg))
        test_ui.APP.processEvents()
        self.assertFalse(self.window.runtime.vanity.running)
        self.assertEqual(dialog.start_button.text(), 'Resume search')

    def test_public_measurement_updates_estimate_without_profile_unlock(self):
        dialog = self.dialog()
        dialog.prefix.setText('cafe')
        dialog.backend.setCurrentIndex(dialog.backend.findData('cpu'))
        self.assertTrue(dialog.measure_button.isEnabled())
        dialog.measure_button.click()
        test_ui.until(lambda:'Average:' in dialog.difficulty.text(),seconds=30)
        self.assertIn('50% chance',dialog.details_text.text())
        self.assertIn('95% chance',dialog.details_text.text())
        self.assertIn('Chance within one hour',dialog.details_text.text())
        self.assertTrue(dialog.estimate_details.isHidden())
        test_ui.APP.processEvents()
        self.assertGreaterEqual(dialog.difficulty.height(),dialog.difficulty.heightForWidth(dialog.difficulty.width()))
        self.assertFalse(self.window.signer.call('list')['profile_ready'])
        self.assertIsNone(self.window.signer.call('vanity_status')['record'])
        screenshot = Path(__file__).resolve().parents[1]/'work/test-screenshots/vanity-measured.png'
        screenshot.parent.mkdir(parents=True,exist_ok=True)
        self.assertTrue(dialog.grab().save(str(screenshot)))
        dialog.mode.setCurrentIndex(dialog.mode.findData('suffix'))
        dialog.suffix.setText('beef')
        self.assertIn('time depends on your computer',dialog.estimate_note.text())
        inconclusive = dict(purpose='calibration',status='calibrated',run_id='synthetic-inconclusive',
                            measurement=None,progress={},error='')
        with patch.object(self.window.runtime.vanity,'snapshot',return_value=inconclusive):
            dialog.refresh()
            self.assertIn('steady speed',dialog.status.text())
            self.assertTrue(dialog.start_button.isEnabled())


if __name__ == '__main__':
    unittest.main()
