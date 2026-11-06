"""Vanity pattern math, private ownership, and the bidirectional mining boundary."""
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from wartnode.vanity import pattern, estimate
from wartnode.vanity_keys import ORDER, LAMBDA, TRANSFORMS
from wartnode.compat import wallet_utils as crypto
from wartnode.paths import data_root
from wartnode.processes import Runtime
from wartnode.signer import Signer

PASSWORD = 'synthetic-vanity-password'


class PatternTests(unittest.TestCase):
    def test_modes_normalization_and_eight_character_boundary(self):
        for prefix, suffix in [('ABCD', ''), ('', 'ABCD'), ('AB', 'CD')]:
            value = pattern(prefix, suffix)
            self.assertEqual(value.length, 4)
            self.assertTrue(value.matches('abcd'+'0'*40+'abcd'))
        self.assertFalse(pattern('abcd', 'abcd').exclusive)
        self.assertTrue(pattern('abcde', 'abcd').exclusive)

    def test_invalid_patterns_and_inconsistent_known_checksums_fail_before_work(self):
        for prefix, suffix in [('', ''), ('wart', ''), ('0xabc', ''), ('0'*25, '0'*24), ('0'*48, '')]:
            with self.subTest(prefix=prefix, suffix=suffix), self.assertRaises(ValueError):
                pattern(prefix, suffix)
        owner, _ = crypto.wallet_from_pkhex('00'*31+'01')
        self.assertTrue(pattern(owner[:40], owner[40:]).matches(owner))

    def test_difficulty_scales_by_sixteen_and_impractical_targets_are_explicit(self):
        self.assertEqual(estimate(pattern('abcdef'))['attempts'], 16**6)
        self.assertEqual(estimate(pattern('abcdef0'))['attempts'], 16*16**6)
        self.assertTrue(estimate(pattern('', 'abcdef'))['approximate'])
        self.assertTrue(estimate(pattern('abc', 'def'))['approximate'])
        self.assertFalse(estimate(pattern('0'*16))['practical'])
        measured = estimate(pattern('abcdef'), 1_000_000)
        self.assertAlmostEqual(measured['mean_seconds'], 16.777216)
        self.assertGreater(measured['p95_seconds'], 2.9*measured['mean_seconds'])


class SignerVanityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = data_root(self.temp.name)
        self.signer = Signer(self.directory)
        self.addCleanup(self.signer.store.con.close)
        self.signer.call('setup', dict(password=PASSWORD))

    def synthetic_candidate(self, branch=0):
        # Fixed, public, test-only key; match the resulting address's first nibble.
        exponent = ((1+7)*pow(int.from_bytes(LAMBDA, 'big'), branch % 3, ORDER)) % ORDER
        if branch >= 3:
            exponent = ORDER-exponent
        private = exponent.to_bytes(32, 'big').hex()
        address, _ = crypto.wallet_from_pkhex(private)
        from coincurve import PrivateKey
        with patch('wartnode.vanity_keys.PrivateKey', return_value=PrivateKey.from_int(1)):
            run = self.signer.call('vanity_prepare', dict(prefix=address[:1]))
        candidate = dict(offset=f'{7:064x}', transform=TRANSFORMS[branch], address=address)
        self.signer.call('vanity_found', dict(job_id=run['job_id'], run_id=run['run_id'],
                         checkpoint=run['checkpoint'], candidate=candidate))
        return run['job'], candidate, private

    def test_all_six_transformations_import_and_encrypted_backup_restore(self):
        owners = []
        for branch in range(6):
            job, candidate, private = self.synthetic_candidate(branch)
            self.assertNotIn('secret', json.dumps(job))
            result = self.signer.call('vanity_finalize', dict(job_id=job['session_id'], candidate=candidate, label='Vanity'))
            self.assertEqual(result['address'], candidate['address'])
            owner, key = self.signer.store.private_key(self.signer.profile_key, result['address'])
            self.assertEqual(key.to_string().hex(), private)
            owners.append(owner)
            again = self.signer.call('vanity_finalize', dict(job_id=job['session_id'], candidate=candidate))
            self.assertTrue(again['already_saved'])
            self.assertEqual(again['address'], owner)
        backup = self.directory/'synthetic.wartprofile'
        self.signer.call('backup', dict(path=str(backup), password=PASSWORD))
        destination = data_root(self.directory/'restored')
        restored = Signer(destination)
        try:
            restored.call('restore', dict(path=str(backup), password=PASSWORD))
            restored.call('unlock', dict(password=PASSWORD))
            self.assertEqual({w['address'] for w in restored.store.public_wallets()}, set(owners))
            self.assertNotIn(private.encode(), (self.directory/'wallet.db').read_bytes())
        finally:
            restored.store.con.close()

    def test_locked_finalization_and_forged_results_fail_without_import(self):
        job, candidate, _ = self.synthetic_candidate()
        self.signer.lock()
        with self.assertRaisesRegex(ValueError, 'Unlock'):
            self.signer.call('vanity_finalize', dict(job_id=job['session_id'], candidate=candidate))
        self.signer.call('unlock', dict(password=PASSWORD))
        with self.assertRaisesRegex(ValueError, 'verification'):
            self.signer.call('vanity_finalize', dict(job_id=job['session_id'], candidate={**candidate, 'address':'0'*48}))
        self.assertEqual(self.signer.store.public_wallets(), [])
        self.signer.call('vanity_discard', dict(job_id=job['session_id']))
        with self.assertRaises(ValueError):
            self.signer.call('vanity_finalize', dict(job_id=job['session_id'], candidate=candidate))

    def test_pending_seed_is_sealed_and_no_new_job_can_replace_it(self):
        job, candidate, _ = self.synthetic_candidate()
        public = self.signer.call('vanity_status', {})['record']
        self.assertNotIn('seed', public)
        token = self.signer.store.con.execute('SELECT seed FROM desktop_vanity_jobs').fetchone()[0]
        self.assertNotIn('00'*31+'01', token)
        with self.assertRaisesRegex(ValueError, 'Finish or discard'):
            self.signer.call('vanity_prepare', dict(prefix='ab'))


class MiningExclusionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runtime = Runtime(data_root(self.temp.name))
        self.runtime.vanity = SimpleNamespace(running=False, exclusive=False, start=Mock(), close=Mock())
        self.runtime.miner = SimpleNamespace(running=False, stop=Mock())

    def job(self, prefix, suffix=''):
        return dict(pattern=dict(prefix=prefix, suffix=suffix))

    def test_more_than_eight_combined_characters_requires_mining_stopped(self):
        self.runtime.miner.running = True
        for prefix, suffix in [('0'*9, ''), ('', '0'*9), ('0'*5, '0'*4)]:
            with self.subTest(prefix=prefix, suffix=suffix), self.assertRaisesRegex(ValueError, 'Stop mining'):
                self.runtime.start_vanity(self.job(prefix, suffix), 'cpu')
        self.runtime.start_vanity(self.job('0'*4, '0'*4), 'cpu')
        self.runtime.vanity.start.assert_called_once()

    def test_waiting_miner_and_queued_recovery_also_block_long_search(self):
        self.runtime._mining_request = ('synthetic',)
        self.runtime._mining_waiting = True
        with self.assertRaisesRegex(ValueError, 'Stop mining'):
            self.runtime.start_vanity(self.job('0'*9), 'cpu')
        self.runtime.cancel_mining()
        self.runtime.start_vanity(self.job('0'*9), 'cpu')
        self.runtime.vanity.start.assert_called_once()

    def test_long_search_blocks_manual_and_automatic_mining_starts(self):
        self.runtime.vanity.running = self.runtime.vanity.exclusive = True
        for automatic in (False, True):
            with self.assertRaisesRegex(ValueError, 'Stop the vanity search'):
                self.runtime.start_miner('synthetic', 1, 'opencl', automatic=automatic)
        self.runtime.shutdown()
        self.runtime.vanity.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
