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
            self.assertEqual(started.call_args.kwargs,dict(cpu_limit=90,gpu_limit=90,generation=window.runtime.mining_generation))
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

    def test_waiting_is_visible_and_stop_cancels_recovery_before_worker_runs(self):
        window=self.window;window.timer.stop()
        window.runtime._mining_request=(OWNER,1,'opencl',0,60,60)
        window.runtime._mining_waiting=True
        window.render_mining_status();window.render_miner_telemetry()
        self.assertEqual(window.mining_status.text(),'Waiting for node')
        self.assertEqual(window.overview_miner.value.text(),'Waiting for node')
        self.assertIn('resume automatically',window.mining_session.text())
        self.assertTrue(window.stop_mining.isEnabled())
        self.assertFalse(window.start_mining.isEnabled())
        with patch.object(window,'submit') as submit:
            window.stop_mining.click()
        self.assertFalse(window.runtime.mining_requested)
        self.assertFalse(window.runtime.mining_waiting)
        submit.assert_called_once()

    def test_node_stop_cancels_waiting_before_worker_runs(self):
        window=self.window;window.timer.stop()
        window.runtime._mining_request=(OWNER,1,'opencl',0,60,60)
        window.runtime._mining_waiting=True
        with patch.object(window,'submit'):
            window.stop_node()
        self.assertFalse(window.runtime.mining_requested)

    def test_saving_settings_while_waiting_cancels_the_previous_recovery_request(self):
        window=self.window;window.timer.stop()
        window.runtime._mining_request=(OWNER,1,'opencl',0,60,60)
        window.runtime._mining_waiting=True
        with patch.object(window.runtime,'start_miner') as start,patch.object(window,'tick'):
            window.show_mining_settings('eco');window.payout.setText(OWNER)
            self.assertTrue(window.runtime._mining_editing)
            window.apply_mining_settings();until(lambda:not window.tasks)
        start.assert_not_called()
        self.assertFalse(window.runtime.mining_requested)
        self.assertFalse(window.runtime._mining_editing)
        self.assertEqual(settings.load(window.directory)['cpu_limit'],30)

    def test_poll_callback_does_not_stop_a_miner_on_a_single_unready_result(self):
        window=self.window;window.timer.stop()
        fake=SimpleNamespace(running=True,lines=deque(),metrics={},sample=None,process=None)
        fake.stop=Mock()
        state={'state':'Synchronizing','height':100,'peers':1,'reason':'syncing','recoverable':True}
        window.runtime.ready=False
        with patch.object(window.runtime,'miner',fake),patch.object(window.runtime,'poll',return_value=state) as poll,patch.object(window,'refresh_process_statistics'):
            window.tick();until(lambda:not window.tasks)
            poll.assert_called_once()
            fake.stop.assert_not_called()

    def test_hiding_to_tray_ends_settings_review_so_it_cannot_hold_recovery(self):
        window=self.window;window.timer.stop()
        window.preferences['close_to_tray']=True
        window.show_mining_settings('eco')
        self.assertTrue(window.runtime._mining_editing)
        with patch('wartnode.ui.QSystemTrayIcon.isSystemTrayAvailable',return_value=True),patch.object(window.tray,'showMessage'):
            window.close()
        until(lambda:not window.tasks)
        self.assertFalse(window.runtime._mining_editing)
        self.assertFalse(window.editing_mining)


if __name__ == '__main__':unittest.main()
