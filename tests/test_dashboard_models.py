"""Presentation contracts must not fabricate progress, funds, or sensor values."""
import json
import unittest
from wartnode.dashboard import ConnectionStatus, MinerSample, ProfileBalance, SampleHistory


def wire(**changes):
    value = dict(schema=1, session='session-a', elapsed_s=5, janus_hps=10,
                 average_janus_hps=9, gpu_sha_hps=20, verus_hps=2,
                 accepted=1, rejected=0, blocks=0, gpus=[])
    value.update(changes)
    return json.dumps(value)


class PresentationTests(unittest.TestCase):
    def test_ring_uses_existing_readiness_and_has_no_made_up_sync_progress(self):
        syncing = ConnectionStatus.from_runtime({'state': 'Synchronizing', 'height': 42}, 'local')
        self.assertEqual(syncing.name, 'Syncing')
        self.assertIsNone(syncing.progress)
        self.assertFalse(syncing.ready)
        self.assertEqual(ConnectionStatus.from_runtime({'state': 'Ready'}, 'local').name, 'Synced')
        self.assertEqual(ConnectionStatus.from_runtime({'state': 'Connected'}, 'lite').name, 'Lite')
        failed = ConnectionStatus.from_runtime({'state': 'Service unavailable', 'error': 'Timed out'}, 'lite')
        self.assertEqual((failed.name, failed.detail), ('Offline', 'Timed out'))

    def test_partial_balance_is_never_advertised_as_a_complete_total(self):
        row = dict(address='a', balance=100, reserved=25, available=75)
        missing = ProfileBalance.from_rows(['a', 'b'], [row], 'local')
        self.assertFalse(missing.complete)
        self.assertIsNone(missing.available)
        complete = ProfileBalance.from_rows(['a', 'A'], [row], 'local')
        self.assertTrue(complete.complete)
        self.assertEqual(complete.available, 75)
        with self.assertRaises(ValueError):
            ProfileBalance.from_rows(['a'], [dict(row, available=100)], 'local')
        with self.assertRaises(ValueError):
            ProfileBalance.from_rows(['a'], [row, row], 'local')

    def test_unavailable_sensor_survives_and_invalid_numeric_data_is_rejected(self):
        sample = MinerSample.from_wire(wire(gpus=[dict(id='pci:1', name='Selected GPU', power_w=None)]), received=10)
        self.assertIsNone(sample.gpus[0].power_w)
        self.assertTrue(sample.fresh(now=20))
        self.assertFalse(sample.fresh(now=40))
        for changes in (dict(janus_hps=float('nan')), dict(rejected=True),
                        dict(blocks=-1), dict(gpus=[dict(id='pci:1', name='GPU', utilization_pct=101)])):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                MinerSample.from_wire(wire(**changes))
        with self.assertRaises(ValueError):
            MinerSample.from_wire('x'*4097)

    def test_charts_are_bounded_reset_on_restart_and_ignore_old_samples(self):
        history = SampleHistory(maximum=3)
        for elapsed in range(1, 6):
            history.append(MinerSample.from_wire(wire(elapsed_s=elapsed)))
        self.assertEqual([s.elapsed_s for s in history.samples], [3, 4, 5])
        self.assertFalse(history.append(MinerSample.from_wire(wire(elapsed_s=4))))
        history.append(MinerSample.from_wire(wire(session='session-b', elapsed_s=1)))
        self.assertEqual(len(history.samples), 1)


if __name__ == '__main__':
    unittest.main()
