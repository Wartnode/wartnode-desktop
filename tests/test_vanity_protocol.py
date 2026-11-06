"""Protocol faults, finite probability math and measurement provenance."""
import copy
import json
import math
import unittest
import uuid
from wartnode import vanity_jobs as jobs
from wartnode.vanity import chance, estimate, pattern
from wartnode.vanity_protocol import ProtocolState, ProtocolError
from wartnode.vanity_rates import Measurements, Samples, PUBLIC_POINT, MAX_AGE_SECONDS


def run():
    ident = str(uuid.uuid4())
    return dict(job_id=ident, run_id=str(uuid.uuid4()), checkpoint=jobs.initial_checkpoint(),
        job=dict(schema=3, session_id=ident, public_key=PUBLIC_POINT, strategy='endomorphism-v1',
            pattern=dict(profile=dict(chain='warthog', network='mainnet', address_type='account'), prefix='a', suffix='')))


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.run = run()
        self.parser = ProtocolState(self.run)
        self.sequence = 0

    def event(self, kind, data):
        self.sequence += 1
        return dict(protocol=3, sequence=self.sequence, event=kind, job_id=self.run['job_id'],
                    run_id=self.run['run_id'], data=data)

    def consume(self, value):
        return self.parser.consume((json.dumps(value)+'\n').encode())

    def ready(self):
        self.consume(self.event('initializing', dict(backend='cpu')))
        self.consume(self.event('ready', dict(workers=['cpu:0'], hardware=dict(cpu={}, gpus=[], threads=1),
                                             initialization_seconds=.1)))

    def progress(self, seconds=1, count=1):
        cp = dict(next=jobs.index(count), evaluated=jobs.index(count), elapsed_seconds=seconds)
        return dict(elapsed_seconds=seconds, search_seconds=seconds, candidates_exact=str(count),
                    workers=['cpu:0'], checkpoint=cp)

    def test_valid_messages_and_monotonic_progress(self):
        self.ready()
        self.assertEqual(self.consume(self.event('progress', self.progress()))[0], 'progress')
        self.assertEqual(self.parser.checkpoint['next'], jobs.index(1))

    def test_wrong_envelopes_never_enter_ready_state(self):
        base = self.event('initializing', dict(backend='cpu'))
        for key, value in [('protocol',2), ('protocol',True), ('sequence',2), ('sequence',True),
                           ('job_id',str(uuid.uuid4())), ('run_id',str(uuid.uuid4())), ('extra',None)]:
            with self.subTest(key=key, value=value):
                candidate = dict(base); candidate[key] = value
                with self.assertRaises(ProtocolError):
                    self.consume(candidate)
        self.assertEqual(self.parser.phase, 'new')

    def test_duplicate_fields_partial_lines_oversized_nonobjects_and_nonfinite(self):
        for raw in [b'{}\n', b'[]\n', b'{"protocol":3,"protocol":3}\n', b'{"a":NaN}\n',
                    b'{"a":Infinity}\n', b'{"a":1}', b' '*(65537)+b'\n', b'\xff\n']:
            with self.subTest(raw=raw[:40]), self.assertRaises(ProtocolError):
                self.parser.consume(raw)

    def test_transition_order_and_repeated_initializing(self):
        with self.assertRaises(ProtocolError):
            self.consume(self.event('progress', self.progress()))
        self.sequence = 0
        self.consume(self.event('initializing', dict(backend='cpu')))
        with self.assertRaises(ProtocolError):
            self.consume(self.event('initializing', dict(backend='cpu')))

    def test_invalid_progress_numbers_counts_fields_and_flooding(self):
        self.ready()
        base = self.progress()
        for key, value in [('search_seconds',True), ('search_seconds',float('inf')),
                           ('elapsed_seconds',-1), ('candidates_exact','01'), ('candidates_exact','9'),
                           ('workers',['cpu:1']), ('extra',1)]:
            candidate = dict(base); candidate[key] = value
            with self.subTest(key=key), self.assertRaises(ProtocolError):
                self.consume(self.event('progress', candidate))
            self.sequence -= 1
        self.consume(self.event('progress', base))
        with self.assertRaises(ProtocolError):
            self.consume(self.event('progress', self.progress(1.01, 2)))

    def test_regression_and_unexpected_candidate_are_rejected(self):
        self.ready()
        self.consume(self.event('progress', self.progress(1, 5)))
        with self.assertRaises(ProtocolError):
            self.consume(self.event('progress', self.progress(2, 4)))
        self.sequence -= 1
        with self.assertRaises(ProtocolError):
            self.consume(self.event('paused', dict(self.progress(2, 6), candidate={})))

    def test_terminal_is_unique_and_calibration_cannot_be_search_result(self):
        self.ready()
        with self.assertRaises(ProtocolError):
            self.consume(self.event('calibrated', dict(self.progress(), candidate=None)))
        self.sequence -= 1
        self.consume(self.event('paused', dict(self.progress(), candidate=None)))
        with self.assertRaises(ProtocolError):
            self.consume(self.event('progress', self.progress(2, 2)))

    def test_raw_paths_and_unknown_codes_are_not_accepted_as_errors(self):
        self.ready()
        for data in (dict(code='unknown'), dict(code='search_failed', message='C:/private/path')):
            with self.assertRaises(ProtocolError):
                self.consume(self.event('error', data))
            self.sequence -= 1
        self.consume(self.event('error', dict(code='search_failed')))

    def test_wall_clock_flood_limit_cannot_be_bypassed_by_forged_worker_time(self):
        self.parser = ProtocolState(self.run,clock=lambda:0.0)
        self.ready()
        for i in range(1,25):
            self.consume(self.event('progress',self.progress(i*.2,i)))
        with self.assertRaises(ProtocolError):
            self.consume(self.event('progress',self.progress(5,25)))


class EstimateTests(unittest.TestCase):
    def test_exact_geometric_percentiles_and_future_chance(self):
        value = pattern('a')
        info = estimate(value, 1)
        self.assertEqual(info['attempts'], 16)
        self.assertEqual(info['median_seconds'], math.ceil(math.log(.5)/math.log(15/16)))
        self.assertEqual(info['p95_seconds'], math.ceil(math.log(.05)/math.log(15/16)))
        self.assertAlmostEqual(chance(value, 1, 1), 1/16)
        self.assertEqual(chance(value, 0, 1), 0)
        self.assertGreaterEqual(chance(value, info['p95_seconds'], 1), .95)

    def test_entire_payload_does_not_count_redundant_checksum_as_work(self):
        from wartnode.compat import wallet_utils as crypto
        address, _ = crypto.wallet_from_pkhex('00'*31+'01')
        for value in (pattern(address), pattern(address[:20],address[20:]), pattern(address[:40])):
            info = estimate(value, 1)
            self.assertEqual(info['attempts'], 2**160)
            self.assertFalse(info['approximate'])
            self.assertFalse(info['practical'])

    def test_suffix_sparse_and_nonfinite_rates_are_explicit(self):
        self.assertTrue(estimate(pattern('', 'a'))['approximate'])
        self.assertTrue(estimate(pattern('a'*39, 'bb'))['sparse'])
        for rate in (True,0,-1,float('inf'),float('nan'),'fast'):
            self.assertIsNone(estimate(pattern('a'), rate)['mean_seconds'])
            self.assertIsNone(chance(pattern('a'), 1, rate))
        self.assertGreater(chance(pattern('a'), 1e9, 1e9), .99999)


class MeasurementTests(unittest.TestCase):
    def test_calibration_preserves_the_checksum_hot_path_for_short_suffixes(self):
        from unittest.mock import Mock
        from wartnode.vanity_worker import VanityWorker
        worker = VanityWorker(None)
        worker._start = Mock()
        worker.calibrate('cpu','suffix')
        self.assertEqual(worker._start.call_args.args[0]['job']['pattern']['prefix'],'')
        self.assertEqual(worker._start.call_args.args[0]['job']['pattern']['suffix'],'0'*8)
        worker.calibrate('cpu','both')
        self.assertEqual(worker._start.call_args.args[0]['job']['pattern']['prefix'],'0')

    def test_initialization_warmup_and_lucky_short_runs_are_excluded(self):
        samples = Samples()
        samples.add(.2, 100_000_000)
        samples.add(.9, 200_000_000)
        self.assertIsNone(samples.summary())
        for seconds in range(1,12,2):
            samples.add(seconds, 200_000_000+seconds*1_000_000)
        summary = samples.summary()
        self.assertEqual(summary['rate'], 1_000_000)
        self.assertEqual(summary['samples'], 5)
        self.assertFalse(summary['variable'])

    def test_variability_and_zero_or_insufficient_samples(self):
        samples = Samples()
        for seconds, count in zip(range(1,14,2),(0,1,2,3,4,10,11)):
            samples.add(seconds, count)
        self.assertTrue(samples.summary()['variable'])
        empty = Samples()
        for seconds in range(1,10):
            empty.add(seconds, 0)
        self.assertIsNone(empty.summary())

    def test_session_cache_is_scoped_expires_and_rejects_component_or_hardware_changes(self):
        clock = [100]
        measurements = Measurements(clock=lambda:clock[0])
        summary = dict(rate=100, low=90, high=110, samples=5, seconds=5, variable=False)
        first = measurements.put('cpu','prefix',summary,dict(model='first'),['cpu:0'],'build1')
        self.assertIsNone(measurements.get('opencl','prefix'))
        self.assertIsNone(measurements.get('cpu','suffix'))
        self.assertIsNone(measurements.get('cpu','prefix','build2'))
        measurements.put('cpu','suffix',summary,dict(model='second'),['cpu:0'],'build1')
        self.assertIsNone(measurements.get('cpu','prefix'))
        self.assertNotEqual(first['fingerprint'], measurements.get('cpu','suffix')['fingerprint'])
        clock[0] += MAX_AGE_SECONDS+1
        self.assertIsNone(measurements.get('cpu','suffix'))

    def test_a_new_worker_invalidates_changed_hardware_before_measurement_completes(self):
        measurements = Measurements()
        summary = dict(rate=100,low=90,high=110,samples=5,seconds=5,variable=False)
        measurements.put('opencl','prefix',summary,dict(driver='old'),['opencl:0'],'build')
        measurements.observe('opencl',dict(driver='new'),['opencl:0'],'build')
        self.assertIsNone(measurements.get('opencl','prefix'))

    def test_live_measurement_window_is_bounded(self):
        samples = Samples()
        for seconds in range(1,100):
            samples.add(seconds,seconds*100)
        self.assertEqual(samples.summary()['samples'],24)
        self.assertEqual(samples.summary()['seconds'],48)


if __name__ == '__main__':
    unittest.main()
