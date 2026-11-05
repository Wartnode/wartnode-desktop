from pathlib import Path
import unittest
from unittest.mock import patch
import test_ui
from wartnode import settings,theme,desktop_preferences
from wartnode.activity import ActivityStore
from test_core import OWNER


class PreferencesDashboardTests(unittest.TestCase):
    setUp=test_ui.NativeUITests.setUp
    tearDown=test_ui.NativeUITests.tearDown

    def test_preferences_persist_apply_and_failed_startup_rolls_back(self):
        window=self.window;window.timer.stop()
        window.preference_controls['auto_lock_minutes'].setCurrentIndex(3)
        self.assertFalse(window.idle.isActive())
        self.assertEqual(settings.load(window.directory)['auto_lock_minutes'],0)
        window.set_preference('start_page','Mine')
        window.set_preference('close_to_tray',False)
        self.assertEqual(settings.load(window.directory)['start_page'],'Mine')
        with patch('wartnode.preferences_ui.desktop.set_startup',side_effect=OSError('Registration refused')):
            window.set_preference('launch_at_startup',True)
        self.assertFalse(settings.load(window.directory)['launch_at_startup'])
        self.assertIn('Registration refused',window.notice.text())

    def test_privacy_removes_amounts_from_activity_rewards_and_tooltips(self):
        window=self.window;window.timer.stop()
        window.activity_data={'rows':[dict(timestamp=1700000000,kind='reward',sender='',recipient=OWNER,owner=OWNER,
            amount='150000000',fee='0',status='confirmed',tx_hash='ab'*32,height=4)],
            'states':{},'payout':OWNER,'reward_complete':False,'reward_count':1,'reward_total':150000000,'rewards':[]}
        window.render_activity();self.assertIn('1.50000000',window.overview_activity.text())
        window.set_preference('hide_balances',True)
        self.assertNotIn('1.50000000',window.overview_activity.text())
        self.assertNotIn('1.50000000',window.reward_summary.text())
        self.assertEqual(window.history_table.item(0,1).toolTip(),'••••••')
        window.set_preference('hide_balances',False)
        self.assertIn('1.50000000',window.overview_activity.text())

    def test_tray_off_and_unavailable_tray_use_orderly_exit(self):
        from PySide6.QtGui import QCloseEvent
        window=self.window;window.timer.stop()
        for enabled,available in ((False,True),(True,False)):
            window.preferences['close_to_tray']=enabled
            with patch('wartnode.ui.QSystemTrayIcon.isSystemTrayAvailable',return_value=available),patch.object(window,'exit_application') as exit:
                window.closeEvent(QCloseEvent());exit.assert_called_once()

    def test_system_light_and_contrast_use_readable_native_palettes(self):
        from PySide6.QtGui import QPalette
        with patch('wartnode.desktop_preferences.system_dark',return_value=False),patch('wartnode.desktop_preferences.contrast_enabled',return_value=False):
            theme.apply(test_ui.APP,'system')
            self.assertGreater(test_ui.APP.palette().color(QPalette.ColorRole.Window).lightness(),128)
        with patch('wartnode.desktop_preferences.contrast_enabled',return_value=True):
            theme.apply(test_ui.APP,'dark');self.assertEqual(test_ui.APP.styleSheet(),'')
        theme.apply(test_ui.APP)

    def test_startup_command_uses_stable_launcher_and_explicit_profile_without_secrets(self):
        root=Path(self.temp.name)/'installed';root.mkdir();(root/'WartNode.exe').write_bytes(b'fixture')
        command=desktop_preferences.startup_command(root,self.window.directory)
        self.assertIn('--data-dir',command);self.assertIn('--no-node',command)
        self.assertNotIn('versions',command);self.assertNotIn('password',command)

    def test_reinstall_reflects_removed_startup_registration_instead_of_showing_a_stale_check(self):
        from wartnode.ui import Window
        settings.save(self.window.directory,launch_at_startup=True)
        with patch('wartnode.ui.startup_enabled',return_value=False):
            other=Window(self.window.directory,installed_root=Path(self.temp.name)/'installed')
        try:
            test_ui.until(lambda:not other.tasks)
            self.assertFalse(other.preference_controls['launch_at_startup'].isChecked())
            self.assertFalse(settings.load(self.window.directory)['launch_at_startup'])
        finally:
            other.timer.stop();other.exiting=True;other.signer.close()
            other.pool.waitForDone(30000);other.lifecycle_pool.waitForDone(30000)
            other.tray.hide();other.close();test_ui.APP.processEvents()


if __name__=='__main__':unittest.main()
