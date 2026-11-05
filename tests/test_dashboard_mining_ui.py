"""Pending controls cannot mutate a running miner before a reviewed restart."""
from collections import deque
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import test_ui
from wartnode import settings
from test_core import OWNER

until=test_ui.until


class DashboardMiningTests(unittest.TestCase):
    setUp=test_ui.NativeUITests.setUp
    tearDown=test_ui.NativeUITests.tearDown

    def test_cancel_discards_pending_limits_and_payout_without_changing_settings(self):
        window=self.window;window.timer.stop()
        before=settings.load(window.directory)
        window.show_mining_settings('heavy')
        window.payout.setText(OWNER);window.cpu_limit.setValue(35)
        self.assertEqual(settings.load(window.directory),before)
        self.assertFalse(window.start_mining.isEnabled())
        window.mining_settings_dialog.reject()
        self.assertEqual((window.cpu_limit.value(),window.gpu_limit.value()),(60,60))
        self.assertEqual(window.payout.text(),before['payout_address'])
        self.assertEqual(settings.load(window.directory),before)

    def test_running_changes_stop_save_and_start_once_with_the_reviewed_values(self):
        window=self.window;window.timer.stop();events=[]
        fake=SimpleNamespace(running=True,lines=deque(),metrics={},sample=None,process=SimpleNamespace(pid=99999))
        def stop():events.append('stop');fake.running=False
        fake.stop=Mock(side_effect=stop)
        def start(*args,**kwargs):
            events.append(('start',args,kwargs));fake.running=True
        with patch.object(window.runtime,'miner',fake),patch.object(window.runtime,'start_miner',side_effect=start) as started,patch.object(window,'tick'):
            window.show_mining_settings('heavy')
            window.payout.setText(OWNER)
            self.assertEqual(settings.load(window.directory)['cpu_limit'],60)
            window.apply_mining_settings();window.apply_mining_settings()
            until(lambda:not window.tasks)
            self.assertEqual(events[0],'stop')
            self.assertEqual(events[1][0],'start')
            self.assertEqual(started.call_count,1)
            self.assertEqual(started.call_args.args[0],OWNER)
            self.assertEqual(started.call_args.kwargs,dict(cpu_limit=90,gpu_limit=90))
            self.assertEqual(settings.load(window.directory)['payout_address'],OWNER)
            self.assertTrue(window.mining_presets['heavy'].isChecked())

    def test_restart_failure_leaves_a_stopped_miner_and_retains_the_requested_configuration(self):
        window=self.window;window.timer.stop()
        fake=SimpleNamespace(running=True)
        fake.stop=lambda:setattr(fake,'running',False)
        with patch.object(window.runtime,'miner',fake),patch.object(window.runtime,'start_miner',side_effect=ValueError('Node not ready')),patch.object(window,'tick'):
            window.show_mining_settings('eco');window.payout.setText(OWNER)
            window.apply_mining_settings();until(lambda:not window.tasks)
            self.assertFalse(fake.running)
            self.assertEqual(settings.load(window.directory)['cpu_limit'],30)
            self.assertIn('Node not ready',window.notice.text())

    def test_missing_telemetry_clears_overview_rates_instead_of_showing_them_as_current(self):
        window=self.window;window.timer.stop()
        window.overview_miner.set_value('374 MH/s','GPU SHA 687 MH/s')
        fake=SimpleNamespace(running=True,sample=None)
        with patch.object(window.runtime,'miner',fake):window.render_miner_telemetry()
        self.assertEqual(window.overview_miner.value.text(),'Running')
        self.assertIn('Waiting for fresh',window.overview_miner.detail.text())


if __name__ == '__main__':unittest.main()
