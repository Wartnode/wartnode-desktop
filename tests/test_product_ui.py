import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import unittest
from PySide6.QtWidgets import QDialog
from wartnode.paths import data_root
from wartnode.ui import Window
from wartnode import settings
from test_ui import APP,until


class ProductUITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.directory=data_root(Path(self.temp.name)/'profile')
        self.window=Window(self.directory,autostart=False)
        self.window.show();until(lambda:not self.window.tasks)

    def tearDown(self):
        self.window.timer.stop();self.window.exiting=True
        self.window.signer.close();self.window.pool.waitForDone(30000)
        self.window.tray.hide();self.window.close();APP.processEvents();self.temp.cleanup()

    def test_initialization_saves_storage_without_starting_node(self):
        target=Path(self.temp.name)/'chain'
        with patch('wartnode.ui.SetupDialog') as dialog,patch.object(self.window,'start_node') as start:
            dialog.return_value.exec.return_value=QDialog.DialogCode.Accepted
            dialog.return_value.storage.text.return_value=str(target)
            dialog.return_value.method.currentData.return_value='fresh'
            self.window.first_use()
            until(lambda:not self.window.tasks)
            self.assertTrue(settings.load(self.directory)['setup_complete'])
            self.assertEqual(settings.chain_directory(self.directory),target)
            self.assertEqual(self.window.stack.currentIndex(),1)
            self.assertFalse(self.window.review_button.isEnabled())
            start.assert_not_called()

    def test_postponing_setup_never_starts_a_node_or_marks_setup_complete(self):
        with patch('wartnode.ui.SetupDialog') as dialog,patch.object(self.window,'start_node') as start:
            dialog.return_value.exec.return_value=QDialog.DialogCode.Rejected
            self.window.first_use()
            start.assert_not_called()
            self.assertFalse(settings.load(self.directory)['setup_complete'])

    def test_diagnostics_omit_addresses_paths_and_raw_node_errors(self):
        path=Path(self.temp.name)/'diagnostics.json'
        self.window.address='private-address-marker'
        self.window.runtime.state={'state':'Stopped','height':0,'peers':0,
                                   'error':'secret-node-error','head':{'hash':'secret-chain-hash'}}
        with patch('wartnode.ui.QFileDialog.getSaveFileName',return_value=(str(path),'')):
            self.window.export_diagnostics()
        raw=path.read_text()
        for secret in ('private-address-marker','secret-node-error','secret-chain-hash',str(self.directory)):
            self.assertNotIn(secret,raw)
        result=json.loads(raw)
        self.assertIn('free_bytes',result['storage'])
        self.assertIn('build',result['windows'])

    def test_settings_text_remains_readable_in_a_small_window(self):
        self.window.resize(980,680)
        self.window.nav_buttons[5].click()
        APP.processEvents()
        status=self.window.storage_status
        self.assertGreaterEqual(status.height(),status.heightForWidth(status.width()))
        self.assertGreaterEqual(status.height(),status.minimumSizeHint().height())
        page=self.window.stack.currentWidget()
        self.assertGreater(page.verticalScrollBar().maximum(),0)
        page.ensureWidgetVisible(status)
        APP.processEvents()
        self.assertTrue(status.isVisible())

    def test_first_window_fits_small_logical_desktops_without_maximizing(self):
        from PySide6.QtCore import QRect
        from types import SimpleNamespace
        for width,height in ((1024,720),(1280,672),(960,492)):
            screen=SimpleNamespace(availableGeometry=lambda:QRect(0,0,width,height))
            with patch('wartnode.ui.QApplication.primaryScreen',return_value=screen):
                other=Window(self.directory,autostart=False)
            try:
                other.show();until(lambda:not other.tasks)
                self.assertLessEqual(other.width()+16,width)
                self.assertLessEqual(other.height()+32,height)
                other.navigate(5)
                until(lambda:not other.tasks)
                page=other.stack.currentWidget()
                page.ensureWidgetVisible(other.storage_status);APP.processEvents()
                # Qt lays out the new multiline text after the storage task's
                # completion callback, sometimes on a second event-loop turn.
                until(lambda:other.storage_status.height()>=other.storage_status.heightForWidth(other.storage_status.width()))
                self.assertGreaterEqual(other.storage_status.height(),other.storage_status.heightForWidth(other.storage_status.width()))
            finally:
                other.timer.stop();other.exiting=True;other.signer.close()
                other.pool.waitForDone(30000);other.tray.hide();other.close();APP.processEvents()

    def test_opening_an_initialized_profile_still_requires_explicit_start(self):
        settings.save(self.directory,setup_complete=True)
        with patch('wartnode.processes.Runtime.start_node') as start:
            other=Window(self.directory,autostart=True)
            try:
                until(lambda:not other.tasks)
                start.assert_not_called()
            finally:
                other.timer.stop();other.exiting=True;other.signer.close()
                other.pool.waitForDone(30000);other.tray.hide();other.close()

    def test_switching_to_lite_locks_wallet_disables_mining_and_persists_mode(self):
        from PySide6.QtWidgets import QMessageBox
        self.window.connection_choice.setCurrentIndex(1)
        with patch('wartnode.ui.QMessageBox.question',return_value=QMessageBox.StandardButton.Yes),patch.object(self.window,'tick'):
            self.window.change_connection();until(lambda:not self.window.tasks)
        self.assertEqual(settings.load(self.directory)['connection_mode'],'lite')
        self.assertIsNone(self.window.address)
        self.window.update_state({'state':'Connected','height':100,'peers':0})
        self.assertFalse(self.window.start_mining.isEnabled())
        self.assertFalse(self.window.start_node_button.isEnabled())
        self.assertFalse(self.window.initialize_node_button.isEnabled())
        self.assertNotIn('MAINNET',self.window.mode_label.text())
        self.assertEqual(self.window.windowTitle(),'Wartnode-Desktop')

    def test_contact_and_explorer_links_keep_full_identifiers(self):
        from wartnode.compat import wallet_utils
        address,_=wallet_utils.wallet_from_pkhex('00'*31+'01')
        settings.save_contact(self.directory,'Recipient',address)
        with patch('wartnode.ui.QInputDialog.getItem',return_value=('Recipient · '+address,True)):
            self.window.choose_contact()
        self.assertEqual(self.window.recipient.text(),address)
        with patch.object(self.window,'search_explorer') as search:
            self.window.explore_value(address)
            self.assertEqual(self.window.search.text(),address)
            self.assertEqual(self.window.stack.currentIndex(),4)
            search.assert_called_once()

    def test_mining_presets_overrides_and_persistence(self):
        window=self.window
        self.assertEqual(window.mining_mode.currentData(),'normal')
        self.assertEqual((window.cpu_limit.value(),window.gpu_limit.value()),(50,50))
        for mode,percent in (('light',20),('heavy',80),('normal',50)):
            window.mining_mode.setCurrentIndex(window.mining_mode.findData(mode))
            self.assertEqual((window.cpu_limit.value(),window.gpu_limit.value()),(percent,percent))
        window.cpu_limit.setValue(5);window.gpu_limit.setValue(95)
        self.assertEqual(window.mining_mode.currentData(),'custom')
        value=settings.load(self.directory)
        self.assertEqual((value['mining_mode'],value['cpu_limit'],value['gpu_limit']),('custom',5,95))
        for control in (window.cpu_limit,window.gpu_limit):
            self.assertEqual((control.minimum(),control.maximum()),(5,95))

    def test_starting_mining_freezes_the_reviewed_limits_and_blocks_duplicate_start(self):
        from PySide6.QtWidgets import QMessageBox
        with patch('wartnode.ui.QMessageBox.question',return_value=QMessageBox.StandardButton.Yes),patch.object(self.window,'submit') as submit:
            self.window.start_miner()
            self.assertTrue(self.window.mining_busy)
            for control in (self.window.mining_mode,self.window.cpu_limit,self.window.gpu_limit):
                self.assertFalse(control.isEnabled())
            self.window.start_miner();self.assertEqual(submit.call_count,1)
            with patch.object(self.window,'tick'):
                submit.call_args.args[1](None,None)
            self.assertFalse(self.window.mining_busy)
