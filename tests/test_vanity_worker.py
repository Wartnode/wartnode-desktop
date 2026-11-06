"""Real public Rust worker → private signer → encrypted profile round trips."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from wartnode.paths import data_root, verified_component
from wartnode.processes import Runtime, SignerClient
from wartnode.vanity import pattern
from wartnode.vanity_jobs import parse_index


class WorkerTests(unittest.TestCase):
    def setUp(self):
        try:
            verified_component('vanity')
        except (OSError, KeyError, ValueError):
            self.skipTest('Build the Rust vanity prototype with scripts/build-vanity.py')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = data_root(self.temp.name)
        self.runtime = Runtime(self.directory)
        self.addCleanup(self.runtime.shutdown)
        self.signer = SignerClient(self.directory)
        self.addCleanup(self.signer.close)
        self.runtime.vanity.persist = self.signer.vanity_call
        self.addCleanup(self.runtime.stop_vanity)
        self.signer.call('setup', password='synthetic-worker-password')

    def wait(self, timeout=30):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            value = self.runtime.vanity.snapshot()
            if value['status'] in ('found', 'paused', 'error', 'calibrated'):
                return value
            time.sleep(.03)
        self.fail('Worker did not complete: '+str(self.runtime.vanity.snapshot()))

    def search_and_save(self, prefix, suffix, backend='cpu'):
        job = self.signer.call('vanity_prepare', prefix=prefix, suffix=suffix)
        self.runtime.start_vanity(job, backend)
        state = self.wait(90 if backend == 'opencl' else 30)
        self.assertEqual(state['status'], 'found', state)
        candidate = state['progress']['candidate']
        result = self.signer.call('vanity_finalize', job_id=job['job_id'], candidate=candidate, label='Recovery test')
        self.assertTrue(pattern(prefix, suffix).matches(result['address']))
        self.assertEqual(len(result['address']), 48)
        self.assertEqual(self.signer.call('list')['wallets'][-1]['address'], result['address'])
        self.runtime.stop_vanity()

    def test_cpu_prefix_suffix_and_combined_round_trips(self):
        for prefix, suffix in [('ab', ''), ('', 'ab'), ('a', 'b')]:
            with self.subTest(prefix=prefix, suffix=suffix):
                self.search_and_save(prefix, suffix)

    def test_found_while_locked_waits_for_unlock_before_import(self):
        job = self.signer.call('vanity_prepare', prefix='ab')
        self.runtime.start_vanity(job, 'cpu')
        self.signer.call('lock')
        state = self.wait()
        self.assertEqual(state['status'], 'found', state)
        args = dict(job_id=job['job_id'], candidate=state['progress']['candidate'], label='Locked search')
        with self.assertRaisesRegex(ValueError, 'Unlock'):
            self.signer.call('vanity_finalize', **args)
        self.signer.call('unlock', password='synthetic-worker-password')
        self.signer.call('vanity_finalize', **args)

    def test_pause_and_owner_shutdown_leave_no_running_worker(self):
        job = self.signer.call('vanity_prepare', prefix='0'*16)
        self.runtime.start_vanity(job, 'cpu')
        process = self.runtime.vanity.process
        self.runtime.stop_vanity()
        self.assertIsNotNone(process.poll())
        self.assertFalse(self.runtime.vanity.running)
        self.signer.call('vanity_discard', job_id=job['job_id'])
        self.assertEqual(self.signer.call('list')['wallets'], [])

    def test_worker_cannot_finalize_a_forged_public_result(self):
        job = self.signer.call('vanity_prepare', prefix='a')
        self.runtime.start_vanity(job, 'cpu')
        state = self.wait()
        self.assertEqual(state['status'], 'found', state)
        candidate = {**state['progress']['candidate'], 'offset':'f'*64}
        with self.assertRaises(ValueError):
            self.signer.call('vanity_finalize', job_id=job['job_id'], candidate=candidate)
        self.assertEqual(self.signer.call('list')['wallets'], [])

    def test_crashed_worker_can_be_closed_and_replaced_without_reusing_its_seed(self):
        job = self.signer.call('vanity_prepare', prefix='0'*16)
        self.runtime.start_vanity(job, 'cpu')
        self.runtime.vanity.process.kill()
        self.runtime.vanity.process.wait(timeout=5)
        self.runtime.stop_vanity()
        self.signer.call('vanity_discard', job_id=job['job_id'])
        self.search_and_save('ab', '')

    def pause_restart_resume(self, backend):
        run = self.signer.call('vanity_prepare', prefix='0'*16, backend=backend)
        def advance():
            deadline = time.monotonic()+(90 if backend == 'opencl' else 30)
            while time.monotonic() < deadline:
                row = self.signer.call('vanity_status')['record']
                if parse_index(row['checkpoint']['next']) > parse_index(run['checkpoint']['next']):
                    return row
                if row['state'] != 'running':
                    self.fail(str(row))
                time.sleep(.1)
            self.fail('No durable checkpoint advance: '+str(self.runtime.vanity.snapshot()))
        self.runtime.start_vanity(run, backend)
        advance()
        self.runtime.stop_vanity()
        paused = self.signer.call('vanity_status')['record']
        self.assertEqual(paused['state'], 'paused')
        self.signer.close()
        self.signer = SignerClient(self.directory)
        self.addCleanup(self.signer.close)
        self.runtime.vanity.persist = self.signer.vanity_call
        self.addCleanup(self.runtime.stop_vanity)
        recovered = self.signer.call('vanity_status')['record']
        self.assertEqual(recovered, paused)
        self.assertFalse(self.runtime.vanity.running)
        self.signer.call('unlock', password='synthetic-worker-password')
        run = self.signer.call('vanity_resume', job_id=run['job_id'], backend=backend)
        self.assertEqual(run['checkpoint'], paused['checkpoint'])
        self.assertEqual(run['job'], paused['job'])
        self.runtime.start_vanity(run, backend)
        advanced = advance()
        self.assertGreater(parse_index(advanced['checkpoint']['evaluated']), parse_index(paused['checkpoint']['evaluated']))
        self.runtime.stop_vanity()

    def test_real_cpu_checkpoint_manual_resume_after_signer_restart(self):
        self.pause_restart_resume('cpu')

    def test_public_cpu_measurement_preserves_pending_job_and_creates_no_wallet(self):
        run = self.signer.call('vanity_prepare', prefix='0'*16)
        self.signer.call('vanity_pause', job_id=run['job_id'], run_id=run['run_id'])
        before = self.signer.call('vanity_status')['record']
        self.signer.call('lock')
        self.runtime.calibrate_vanity('cpu','prefix')
        with self.assertRaisesRegex(ValueError,'Stop the vanity search'):
            self.runtime.start_miner('synthetic',1,'opencl')
        state = self.wait(30)
        self.assertEqual(state['status'],'calibrated',state)
        measured = self.runtime.vanity.measurement('cpu','prefix')
        self.assertGreater(measured['low'],0)
        self.assertGreaterEqual(measured['samples'],4)
        self.assertTrue(measured['hardware']['cpu']['model'])
        self.assertEqual(self.signer.call('vanity_status')['record'],before)
        self.assertEqual(self.signer.call('list')['wallets'],[])
        self.assertTrue(self.signer.call('list')['locked'])

    def test_calibration_discards_matches_and_runs_for_the_full_measurement(self):
        launch = self.runtime.vanity._start
        def easy_pattern(run, backend, purpose, mode, seconds):
            run['job']['pattern']['prefix'] = 'a'
            return launch(run,backend,purpose,mode,seconds)
        with patch.object(self.runtime.vanity,'_start',side_effect=easy_pattern):
            self.runtime.calibrate_vanity('cpu','prefix')
        state = self.wait(30)
        self.assertEqual(state['status'],'calibrated',state)
        self.assertGreaterEqual(state['progress']['search_seconds'],8)
        self.assertIsNone(state['progress']['candidate'])
        self.assertIsNone(self.signer.call('vanity_status')['record'])
        self.assertEqual(self.signer.call('list')['wallets'],[])

    @unittest.skipUnless(os.environ.get('WARTNODE_VANITY_GPU_TESTS') == '1', 'Physical OpenCL qualification is opt-in')
    def test_real_opencl_checkpoint_manual_resume_after_signer_restart(self):
        self.pause_restart_resume('opencl')

    @unittest.skipUnless(os.environ.get('WARTNODE_VANITY_GPU_TESTS') == '1', 'Physical OpenCL qualification is opt-in')
    def test_physical_opencl_prefix_suffix_and_combined_round_trips(self):
        for prefix, suffix in [('ab', ''), ('', 'ab'), ('a', 'b')]:
            with self.subTest(prefix=prefix, suffix=suffix):
                self.search_and_save(prefix, suffix, 'opencl')


if __name__ == '__main__':
    unittest.main()
