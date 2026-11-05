"""Tip races and temporary outages must not silently abandon reviewed mining."""
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.error

from wartnode.paths import data_root
from wartnode.processes import Runtime
from wartnode.rpc import NodeRPC, NodeError, NodeUnavailable
from test_core import OWNER


class MiningRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory=data_root(self.temporary.name)
        self.runtime=Runtime(self.directory)
        self.now=100.0
        self.head={'height':100,'hash':'ab'*32,'synced':True}
        self.node=SimpleNamespace(running=True,rpc_ready_at=0,process=None)
        self.node.stop=Mock(side_effect=self.stop_node)
        self.runtime.node=self.node
        self.miner=SimpleNamespace(running=False,metrics={})
        self.miner.start=Mock(side_effect=lambda *a,**k:setattr(self.miner,'running',True))
        self.miner.stop=Mock(side_effect=lambda:setattr(self.miner,'running',False))
        self.runtime.miner=self.miner
        self.rpc=Mock()
        self.rpc.head.side_effect=lambda:dict(self.head)
        self.rpc.connected_peers.return_value=['dummy-peer']
        self.rpc.template.side_effect=lambda:self.template(self.head)
        self.preferences={'connection_mode':'local','connection_revision':0}
        for name,kwargs in (
            ('wartnode.rpc.NodeRPC',{'return_value':self.rpc}),
            ('wartnode.settings.load',{'return_value':self.preferences}),
            ('wartnode.processes.storage_info',{'return_value':{'free':10**12}}),
            ('wartnode.processes.time.monotonic',{'side_effect':lambda:self.now}),
            ('wartnode.processes.time.sleep',{}),
            ('wartnode.processes.verified_component',{'return_value':Path('fake-miner.exe')}),
        ):
            p=patch(name,**kwargs);p.start();self.addCleanup(p.stop)
        p=patch.object(self.runtime,'detect_devices',return_value={'opencl':['GPU 0','GPU 1']})
        self.hardware=p.start();self.addCleanup(p.stop)

    def stop_node(self):
        self.node.running=False
        return {'graceful':True}

    @staticmethod
    def template(head):
        return {'height':head['height']+1,'header':head['hash']+'00'*48,'synced':head['synced'],'testnet':False}

    def start(self):
        self.runtime.start_miner(OWNER,os.cpu_count() or 1,'opencl',1,cpu_limit=30,gpu_limit=90)

    def wait_for_node(self):
        self.start()
        self.head['synced']=False
        self.runtime.poll()
        self.now+=5.01
        self.runtime.poll()
        self.assertTrue(self.runtime.mining_waiting)
        self.assertFalse(self.miner.running)

    def events(self):
        return [json.loads(line) for line in (self.directory/'logs/lifecycle.log').read_text().splitlines()]

    def test_tip_advancing_between_synced_responses_retries_without_stopping(self):
        self.start()
        old=dict(self.head)
        new={**old,'height':101,'hash':'cd'*32}
        self.rpc.head.side_effect=[old,new,new,new]
        self.rpc.template.side_effect=[self.template(old),self.template(new)]
        state=self.runtime.poll()
        self.assertTrue(self.runtime.ready)
        self.assertEqual(state['height'],101)
        self.miner.stop.assert_not_called()
        self.assertEqual(self.miner.start.call_count,1)
        self.assertIn('node_probe_retry',[e['event'] for e in self.events()])

    def test_brief_unsynced_response_is_rechecked_inside_the_probe(self):
        self.start()
        self.rpc.head.side_effect=[{**self.head,'synced':False},self.head,self.head]
        self.runtime.poll()
        self.assertTrue(self.runtime.ready)
        self.miner.stop.assert_not_called()

    def test_persistent_sync_has_bounded_retries_and_a_five_second_grace(self):
        self.start()
        self.head['synced']=False
        self.rpc.head.reset_mock()
        self.runtime.poll()
        self.assertEqual(self.rpc.head.call_count,3)
        self.now+=4.99
        self.runtime.poll()
        self.miner.stop.assert_not_called()
        self.head['synced']=True
        self.runtime.poll()
        self.assertTrue(self.miner.running)
        self.assertFalse(self.runtime.mining_waiting)
        self.assertIn('mining_continued',[e['event'] for e in self.events()])

    def test_long_sync_resumes_once_after_two_good_checks_with_original_configuration(self):
        self.wait_for_node()
        first_call=self.miner.start.call_args
        self.head['synced']=True
        self.runtime.poll()
        self.assertFalse(self.miner.running)
        self.runtime.poll()
        self.assertTrue(self.miner.running)
        self.assertEqual(self.miner.start.call_args,first_call)
        self.runtime.poll()
        self.assertEqual(self.miner.start.call_count,2)
        self.miner.stop.assert_called_once()
        self.assertFalse(self.runtime.mining_waiting)
        self.assertIn('mining_resumed',[e['event'] for e in self.events()])

    def test_recovery_requires_consecutive_checks_and_waits_for_settings_review(self):
        self.wait_for_node()
        self.head['synced']=True
        self.runtime.poll()
        self.head['synced']=False
        self.runtime.poll()
        self.head['synced']=True
        self.runtime.set_mining_editing(True)
        self.runtime.poll();self.runtime.poll()
        self.assertFalse(self.miner.running)
        self.runtime.set_mining_editing(False)
        self.runtime.poll()
        self.assertFalse(self.miner.running)
        self.runtime.poll()
        self.assertTrue(self.miner.running)

    def test_stop_cancels_waiting_and_invalidates_a_queued_start(self):
        self.wait_for_node()
        queued_generation=self.runtime.mining_generation
        self.runtime.cancel_mining()  # Immediate UI cancellation, before worker stop.
        self.head['synced']=True
        self.runtime.poll();self.runtime.poll()
        self.runtime.stop_miner(cancel=False)
        with self.assertRaisesRegex(ValueError,'cancelled'):
            self.runtime.start_miner(OWNER,1,'opencl',generation=queued_generation)
        self.assertEqual(self.miner.start.call_count,1)
        self.assertFalse(self.runtime.mining_requested)

    def test_stop_during_recovery_probe_cannot_restart_after_the_probe_finishes(self):
        self.wait_for_node()
        self.head['synced']=True
        self.runtime.poll()
        entered=threading.Event();release=threading.Event();errors=[]
        def blocked_head():
            entered.set()
            if not release.wait(3):raise AssertionError('Probe was not released')
            return dict(self.head)
        self.rpc.head.side_effect=blocked_head
        def run():
            try:self.runtime.poll()
            except Exception as error:errors.append(error)
        worker=threading.Thread(target=run)
        worker.start()
        try:
            self.assertTrue(entered.wait(3))
            self.runtime.cancel_mining()  # Must not acquire operation_lock.
        finally:
            release.set();worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors,[])
        self.assertFalse(self.runtime.mining_requested)
        self.assertEqual(self.miner.start.call_count,1)

    def test_stop_during_final_hardware_validation_prevents_process_spawn(self):
        self.wait_for_node()
        self.head['synced']=True
        def cancel_during_detection():
            self.runtime.cancel_mining()
            return {'opencl':['GPU 0','GPU 1']}
        self.hardware.side_effect=cancel_during_detection
        self.runtime.poll();self.runtime.poll()
        self.assertEqual(self.miner.start.call_count,1)
        self.assertFalse(self.runtime.mining_requested)
        self.assertEqual(self.runtime.mining_error,'')

    def test_slow_hardware_detection_requires_fresh_readiness_before_restart(self):
        self.wait_for_node()
        self.head['synced']=True
        def slow_detection():
            self.now+=11
            self.head['synced']=False
            return {'opencl':['GPU 0','GPU 1']}
        self.hardware.side_effect=slow_detection
        self.runtime.poll();self.runtime.poll()
        self.assertFalse(self.miner.running)
        self.assertTrue(self.runtime.mining_waiting)
        self.assertEqual(self.miner.start.call_count,1)

    def test_settings_review_opened_during_restart_validation_holds_the_spawn(self):
        self.wait_for_node()
        self.head['synced']=True
        def open_review():
            self.runtime.set_mining_editing(True)
            return {'opencl':['GPU 0','GPU 1']}
        self.hardware.side_effect=open_review
        self.runtime.poll();self.runtime.poll()
        self.assertFalse(self.miner.running)
        self.assertTrue(self.runtime.mining_waiting)
        self.assertEqual(self.miner.start.call_count,1)
        self.hardware.side_effect=None
        self.runtime.set_mining_editing(False)
        self.runtime.poll();self.runtime.poll()
        self.assertTrue(self.miner.running)

    def test_node_stop_cancels_recovery_even_if_node_is_started_later(self):
        self.wait_for_node()
        self.runtime.stop_node()
        self.node.running=True;self.head['synced']=True
        self.runtime.poll();self.runtime.poll()
        self.assertEqual(self.miner.start.call_count,1)
        self.assertFalse(self.runtime.mining_requested)

    def test_lite_mode_and_shutdown_cancel_recovery(self):
        self.wait_for_node()
        with patch('wartnode.settings.save',return_value={'connection_mode':'lite'}):
            self.runtime.set_mode('lite')
        self.assertFalse(self.runtime.mining_requested)
        self.runtime.shutdown()
        with self.assertRaisesRegex(ValueError,'shutting down'):
            self.runtime.start_miner(OWNER,1,'opencl')

    def test_low_disk_stops_both_processes_and_cancels_recovery(self):
        self.start()
        with patch('wartnode.processes.storage_info',return_value={'free':0}):
            state=self.runtime.poll()
        self.assertEqual(state['state'],'Low disk space')
        self.assertFalse(self.node.running)
        self.assertFalse(self.miner.running)
        self.assertFalse(self.runtime.mining_requested)

    def test_transport_outage_can_recover_but_invalid_responses_cannot(self):
        self.start()
        self.rpc.head.side_effect=NodeUnavailable('connection lost')
        self.runtime.poll();self.now+=6;self.runtime.poll()
        self.assertTrue(self.runtime.mining_waiting)
        self.rpc.head.side_effect=lambda:dict(self.head)
        self.runtime.poll();self.runtime.poll()
        self.assertEqual(self.miner.start.call_count,2)
        self.rpc.head.side_effect=NodeError('invalid response')
        self.runtime.poll()
        self.assertFalse(self.runtime.mining_requested)
        self.assertFalse(self.miner.running)

    def test_invalid_mining_template_stops_without_a_grace_period(self):
        self.start()
        self.rpc.template.side_effect=None
        self.rpc.template.return_value={**self.template(self.head),'header':'z'*160}
        self.runtime.poll()
        self.assertFalse(self.miner.running)
        self.assertFalse(self.runtime.mining_requested)

    def test_unexpected_probe_failure_clears_stale_readiness_and_cancels_mining(self):
        self.start()
        with patch('wartnode.processes.storage_info',side_effect=OSError('storage unavailable')):
            with self.assertRaises(OSError):self.runtime.poll()
        self.assertFalse(self.runtime.ready)
        self.assertFalse(self.runtime.mining_requested)
        self.assertFalse(self.miner.running)
        self.assertEqual(self.runtime.state['reason'],'readiness_check_failed')

    def test_a_reorg_during_a_probe_never_grants_readiness_for_the_old_template(self):
        self.start()
        old=dict(self.head)
        replacement={**old,'hash':'cd'*32}
        self.rpc.head.side_effect=[old,replacement,replacement,replacement,replacement,replacement]
        self.rpc.template.side_effect=lambda:self.template(old)
        self.runtime.poll()
        self.assertFalse(self.runtime.ready)
        self.assertEqual(self.runtime.state['reason'],'template_changed')
        self.now+=6
        self.rpc.head.side_effect=lambda:dict(replacement)
        self.runtime.poll()
        self.assertFalse(self.miner.running)
        self.assertTrue(self.runtime.mining_waiting)
        self.rpc.template.side_effect=lambda:self.template(replacement)
        self.runtime.poll();self.runtime.poll()
        self.assertTrue(self.miner.running)

    def test_miner_crash_and_restart_failure_do_not_create_restart_loops(self):
        self.wait_for_node()
        self.head['synced']=True
        self.hardware.side_effect=ValueError('GPU unavailable')
        self.runtime.poll();self.runtime.poll();self.runtime.poll()
        self.assertFalse(self.runtime.mining_requested)
        self.assertIn('could not resume',self.runtime.mining_error)
        self.assertEqual(self.miner.start.call_count,1)
        self.hardware.side_effect=None
        self.start()
        self.miner.running=False
        self.runtime.poll();self.runtime.poll()
        self.assertFalse(self.runtime.mining_requested)
        self.assertIn('exited unexpectedly',self.runtime.mining_error)
        self.assertEqual(self.miner.start.call_count,2)

    def test_intent_is_not_restored_on_application_restart_and_logs_exclude_payout(self):
        self.wait_for_node()
        other=Runtime(self.directory)
        self.assertFalse(other.mining_requested)
        contents=(self.directory/'logs/lifecycle.log').read_text()
        self.assertNotIn(OWNER,contents)
        self.assertTrue(all(e['time'].endswith('+00:00') for e in self.events()))


class TransportClassificationTests(unittest.TestCase):
    def test_connection_failure_is_recoverable_and_malformed_json_is_not(self):
        client=NodeRPC()
        with patch('wartnode.rpc.open_response',side_effect=urllib.error.URLError('offline')):
            with self.assertRaises(NodeUnavailable):client.head()
        response=Mock(status=200)
        response.read_bounded.return_value=b'invalid json'
        context=Mock()
        context.__enter__=Mock(return_value=response);context.__exit__=Mock(return_value=False)
        with patch('wartnode.rpc.open_response',return_value=context):
            with self.assertRaises(NodeError) as caught:client.head()
        self.assertNotIsInstance(caught.exception,NodeUnavailable)


if __name__=='__main__':unittest.main()
