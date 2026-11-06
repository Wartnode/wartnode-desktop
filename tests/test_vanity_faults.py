"""Faulty real subprocesses must stop and preserve recoverable profile state."""
import os
import json
import queue
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from wartnode.paths import data_root
from wartnode.processes import Runtime, SignerClient
from wartnode.vanity_worker import Timeouts

FIXTURE = Path(__file__).parent/'fixtures/vanity_worker_faults.py'


class FaultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = data_root(self.temp.name)
        self.signer = SignerClient(self.directory)
        self.addCleanup(self.signer.close)
        self.signer.call('setup', password='synthetic-fault-password')
        self.runtime = Runtime(self.directory)
        self.addCleanup(self.runtime.shutdown)
        self.runtime.vanity.persist = self.signer.vanity_call
        self.runtime.vanity.timeouts = Timeouts(startup=2, heartbeat=.8, stalled=1.3, exit=.4, pause=.4)

    def start(self, scenario, backend='cpu'):
        run = self.signer.call('vanity_prepare', prefix='a')
        original = subprocess.Popen
        def spawn(_arguments, **options):
            return original([sys.executable,str(FIXTURE),scenario], **options)
        with patch('wartnode.vanity_worker.verified_component', return_value=Path(sys.executable)), \
                patch('wartnode.vanity_worker.subprocess.Popen', side_effect=spawn):
            self.runtime.start_vanity(run, backend)
        return run

    def wait(self, predicate, seconds=8):
        end = time.monotonic()+seconds
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(.02)
        self.fail('Worker fixture timed out: '+str(self.runtime.vanity.snapshot()))

    def test_silent_and_partial_startup_time_out(self):
        for scenario in ('silent','partial'):
            with self.subTest(scenario=scenario):
                run = self.start(scenario)
                self.wait(lambda:self.runtime.vanity.snapshot().get('error_code') == 'startup_timeout')
                self.runtime.stop_vanity()
                self.assertFalse(self.runtime.vanity.running)
                self.assertEqual(self.signer.call('vanity_status')['record']['checkpoint'],run['checkpoint'])
                self.signer.call('vanity_discard', job_id=run['job_id'])

    def test_missing_heartbeat_and_stalled_work_are_distinct(self):
        for scenario, code in [('ready_silent','heartbeat_timeout'), ('stalled','work_stalled')]:
            with self.subTest(scenario=scenario):
                run = self.start(scenario)
                self.wait(lambda:self.runtime.vanity.snapshot().get('error_code') == code)
                self.runtime.stop_vanity()
                self.assertEqual(self.signer.call('vanity_status')['record']['checkpoint']['next'],'0'*64)
                self.signer.call('vanity_discard', job_id=run['job_id'])

    def test_malformed_stale_flooded_and_forged_messages_stop_without_wallet_insertion(self):
        for scenario in ('bad_json','oversized','duplicate','wrong_id','wrong_protocol','wrong_sequence',
                         'nan','flood','reverse','forged','raw_error','after_terminal'):
            with self.subTest(scenario=scenario):
                run = self.start(scenario)
                self.wait(lambda:self.runtime.vanity.snapshot().get('error_code') == 'protocol_error')
                state = self.runtime.vanity.snapshot()
                self.assertNotIn('C:/private',state['error'])
                self.runtime.stop_vanity()
                self.assertEqual(self.signer.call('list')['wallets'],[])
                self.assertNotEqual(self.signer.call('vanity_status')['record']['state'],'running')
                self.signer.call('vanity_discard', job_id=run['job_id'])

    def test_crash_and_stderr_flood_never_block_pipes(self):
        for scenario in ('crash','stderr_flood'):
            with self.subTest(scenario=scenario):
                run = self.start(scenario)
                self.wait(lambda:self.runtime.vanity.snapshot().get('error_code') == 'worker_crashed')
                self.runtime.stop_vanity()
                self.signer.call('vanity_discard', job_id=run['job_id'])

    def test_terminal_hang_is_killed_and_checkpoint_stays_paused(self):
        run = self.start('terminal_hang')
        process = self.runtime.vanity.process
        self.wait(lambda:self.runtime.vanity.snapshot().get('error_code') == 'exit_timeout')
        self.runtime.stop_vanity()
        self.assertIsNotNone(process.poll())
        self.assertEqual(self.signer.call('vanity_status')['record']['state'],'paused')
        self.assertEqual(self.signer.call('vanity_status')['record']['checkpoint']['next'],run['checkpoint']['next'])

    def test_found_result_survives_worker_hang_and_saves_exactly_once(self):
        run = self.start('found_hang')
        self.wait(lambda:self.runtime.vanity.snapshot().get('error_code') == 'exit_timeout')
        self.runtime.stop_vanity()
        row = self.signer.call('vanity_status')['record']
        self.assertEqual(row['state'],'found')
        self.assertEqual(self.signer.call('list')['wallets'],[])
        first = self.signer.call('vanity_finalize',job_id=run['job_id'])
        again = self.signer.call('vanity_finalize',job_id=run['job_id'])
        self.assertEqual(first['address'],again['address'])
        self.assertTrue(again['already_saved'])
        self.assertEqual(len(self.signer.call('list')['wallets']),1)

    def test_progress_write_failure_keeps_unconfirmed_work_replayable(self):
        persist = self.signer.vanity_call
        def failing(action, **args):
            if action == 'vanity_checkpoint':
                raise ValueError('fixture C:/private/path')
            return persist(action, **args)
        self.runtime.vanity.persist = failing
        run = self.start('checkpoint')
        self.wait(lambda:self.runtime.vanity.snapshot().get('error_code') == 'persistence_error')
        self.runtime.stop_vanity()
        self.assertEqual(self.signer.call('vanity_status')['record']['checkpoint'],run['checkpoint'])
        self.assertEqual(self.signer.call('list')['wallets'],[])

    def test_explicit_gpu_cannot_be_replaced_with_a_cpu_worker(self):
        self.start('clean_pause', backend='opencl')
        self.wait(lambda:self.runtime.vanity.snapshot().get('error_code') == 'protocol_error')
        self.runtime.stop_vanity()
        self.assertEqual(self.signer.call('list')['wallets'],[])

    @unittest.skipUnless(os.name == 'nt','Windows kill-on-owner-exit job object')
    def test_abrupt_owner_death_kills_owned_worker(self):
        import ctypes
        from ctypes import wintypes
        from wartnode.processes import hidden_options
        run = self.signer.call('vanity_prepare',prefix='a')
        request = dict(protocol=3, purpose='search', seconds=None, job=run['job'], run_id=run['run_id'],
                       checkpoint=run['checkpoint'], backend='cpu',threads=1)
        owner = subprocess.run([sys.executable,str(FIXTURE.with_name('vanity_worker_owner.py'))],
            input=json.dumps(request).encode()+b'\n',capture_output=True,timeout=15,**hidden_options())
        self.assertEqual(owner.returncode,0,owner.stderr)
        result = json.loads(owner.stdout)
        self.assertTrue(result['guarded'])
        kernel = ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE,wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x100000,False,result['pid'])
        if handle:
            try:
                self.assertEqual(kernel.WaitForSingleObject(handle,5000),0)
            finally:
                kernel.CloseHandle(handle)
        else:
            self.assertEqual(ctypes.get_last_error(),87)

    def test_cancellation_terminates_owned_process_and_threads(self):
        self.start('clean_pause')
        session = self.runtime.vanity.session
        self.wait(lambda:self.runtime.vanity.snapshot().get('hardware') is not None)
        self.runtime.stop_vanity()
        self.assertIsNotNone(session.process.poll())
        self.assertFalse(session.reader.is_alive())
        self.assertFalse(session.watchdog.is_alive())
        self.assertEqual(self.signer.call('vanity_status')['record']['state'],'paused')

    def test_pause_during_unresponsive_initialization_is_bounded(self):
        self.start('silent')
        session = self.runtime.vanity.session
        began = time.monotonic()
        self.runtime.stop_vanity()
        self.assertLess(time.monotonic()-began,2)
        self.assertIsNotNone(session.process.poll())
        self.assertFalse(session.reader.is_alive())
        self.assertEqual(self.signer.call('vanity_status')['record']['state'],'paused')

    def test_failed_found_write_never_announces_a_match(self):
        persist = self.signer.vanity_call
        def failing(action, **args):
            if action == 'vanity_found':
                raise ValueError('fixture storage full')
            return persist(action, **args)
        self.runtime.vanity.persist = failing
        run = self.start('found_hang')
        self.wait(lambda:self.runtime.vanity.snapshot().get('error_code') == 'persistence_error')
        self.assertNotIn('candidate',self.runtime.vanity.snapshot()['progress'])
        self.runtime.stop_vanity()
        row = self.signer.call('vanity_status')['record']
        self.assertEqual(row['checkpoint'],run['checkpoint'])
        self.assertIsNone(row['candidate'])
        self.assertEqual(self.signer.call('list')['wallets'],[])

    def test_persistence_response_deadline_kills_desynchronized_helper_and_recovers_on_restart(self):
        run = self.signer.call('vanity_prepare',prefix='a')
        with patch.object(self.signer.responses,'get',side_effect=queue.Empty) as response:
            with self.assertRaisesRegex(ValueError,'timed out'):
                self.signer.vanity_call('vanity_status')
            self.assertLessEqual(response.call_args.kwargs['timeout'],5)
        self.signer.close()
        recovered = SignerClient(self.directory)
        try:
            row = recovered.call('vanity_status')['record']
            self.assertEqual(row['state'],'paused')
            self.assertEqual(row['checkpoint'],run['checkpoint'])
            self.assertTrue(recovered.call('list')['locked'])
        finally:
            recovered.close()

    def test_public_persistence_has_a_deadline_including_lock_wait(self):
        self.signer.lock.acquire()
        try:
            began = time.monotonic()
            with patch('wartnode.processes.time.monotonic', side_effect=(began,began+4.99)), \
                    patch.object(self.signer,'_exchange', side_effect=AssertionError('request overtook lock')):
                # A held transport cannot be used by another progress writer.
                with self.assertRaisesRegex(ValueError,'busy'):
                    self.signer.vanity_call('vanity_status')
            self.assertLess(time.monotonic()-began,6)
        finally:
            self.signer.lock.release()
        with self.assertRaises(ValueError):
            self.signer.vanity_call('unlock', password='synthetic-fault-password')


if __name__ == '__main__':
    unittest.main()
