"""Durable jobs, lost-message recovery, transactional finalization and migrations."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from coincurve import PrivateKey
from wartnode.paths import data_root
from wartnode.profile import ProfileStore
from wartnode.signer import Signer
from wartnode import profile_backup, vanity_jobs as jobs
from wartnode.compat import wallet_utils as crypto

PASSWORD = 'synthetic-recovery-password'


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = data_root(self.temp.name)
        self.signer = Signer(self.root)
        self.addCleanup(lambda:self.signer.store.con.close())
        self.signer.call('setup', dict(password=PASSWORD))

    def call(self, action, **args):
        return self.signer.call(action, args)

    def restart(self):
        self.signer.store.con.close()
        self.signer = Signer(self.root)

    def prepare(self, fixed=False):
        if fixed:
            address, _ = crypto.wallet_from_pkhex(f'{8:064x}')
            with patch('wartnode.vanity_keys.PrivateKey', return_value=PrivateKey.from_int(1)):
                return self.call('vanity_prepare', prefix=address[0])
        return self.call('vanity_prepare', prefix='0'*16)

    def found(self):
        run = self.prepare(fixed=True)
        address, _ = crypto.wallet_from_pkhex(f'{8:064x}')
        candidate = dict(offset=f'{7:064x}', transform='identity', address=address)
        self.call('vanity_found', job_id=run['job_id'], run_id=run['run_id'],
                  checkpoint=run['checkpoint'], candidate=candidate)
        return run, candidate

    def test_restart_pauses_without_hashing_and_manual_resume_keeps_seed_and_strategy(self):
        run = self.prepare()
        cp = dict(next=jobs.index(1234), evaluated=jobs.index(1300), elapsed_seconds=5.0)
        self.call('vanity_checkpoint', job_id=run['job_id'], run_id=run['run_id'], checkpoint=cp)
        seed = self.signer.store.con.execute('SELECT seed FROM desktop_vanity_jobs').fetchone()[0]
        self.restart()
        row = self.call('vanity_status')['record']
        self.assertEqual(row['state'], 'paused')
        self.assertEqual(row['checkpoint'], cp)
        self.assertEqual(row['job'], run['job'])
        self.assertIsNone(row['run_id'])
        with self.assertRaisesRegex(ValueError, 'Unlock'):
            self.call('vanity_resume', job_id=row['job_id'])
        self.call('unlock', password=PASSWORD)
        resumed = self.call('vanity_resume', job_id=row['job_id'], backend='cpu')
        self.assertNotEqual(resumed['run_id'], run['run_id'])
        self.assertEqual(resumed['checkpoint'], cp)
        self.assertEqual(self.signer.store.con.execute('SELECT seed FROM desktop_vanity_jobs').fetchone()[0], seed)

    def test_late_checkpoint_found_and_pause_cannot_change_resumed_job(self):
        run = self.prepare(fixed=True)
        self.call('vanity_pause', job_id=run['job_id'], run_id=run['run_id'])
        new = self.call('vanity_resume', job_id=run['job_id'])
        address, _ = crypto.wallet_from_pkhex(f'{8:064x}')
        args = dict(job_id=run['job_id'], run_id=run['run_id'], checkpoint=run['checkpoint'])
        self.assertFalse(self.call('vanity_checkpoint', **args)['accepted'])
        self.assertFalse(self.call('vanity_pause', **args)['accepted'])
        self.assertFalse(self.call('vanity_found', candidate=dict(offset=f'{7:064x}',
            transform='identity', address=address), **args)['accepted'])
        self.assertEqual(self.call('vanity_status')['record'], new)

    def test_checkpoint_and_found_write_failures_keep_last_committed_state(self):
        run = self.prepare(fixed=True)
        address, _ = crypto.wallet_from_pkhex(f'{8:064x}')
        args = dict(job_id=run['job_id'], run_id=run['run_id'])
        with patch.object(self.signer.vanity, '_write', side_effect=sqlite3.OperationalError('disk full fixture')):
            with self.assertRaises(sqlite3.OperationalError):
                self.call('vanity_checkpoint', checkpoint=dict(next=jobs.index(1),
                    evaluated=jobs.index(1), elapsed_seconds=1), **args)
            with self.assertRaises(sqlite3.OperationalError):
                self.call('vanity_found', checkpoint=run['checkpoint'],
                    candidate=dict(offset=f'{7:064x}', transform='identity', address=address), **args)
        self.assertEqual(self.call('vanity_status')['record'], run)

    def test_invalid_checkpoints_and_moving_past_hit_are_rejected(self):
        run = self.prepare(fixed=True)
        args = dict(job_id=run['job_id'], run_id=run['run_id'])
        for cp in [dict(next=jobs.index(1), evaluated=jobs.index(1), elapsed_seconds=float('nan')),
                   dict(next=jobs.index(0), evaluated=jobs.index(0), elapsed_seconds=-1),
                   dict(next=jobs.index(0), evaluated=jobs.index(0), elapsed_seconds=0, strategy='linear')]:
            with self.assertRaises(ValueError):
                self.call('vanity_checkpoint', checkpoint=cp, **args)
        cp = dict(next=jobs.index(43), evaluated=jobs.index(43), elapsed_seconds=1)
        address, _ = crypto.wallet_from_pkhex(f'{8:064x}')
        with self.assertRaisesRegex(ValueError, 'skip'):
            self.call('vanity_found', checkpoint=cp,
                candidate=dict(offset=f'{7:064x}', transform='identity', address=address), **args)
        self.assertEqual(self.call('vanity_status')['record'], run)

    def test_found_survives_restart_and_finalizes_exactly_once_without_seed(self):
        run, candidate = self.found()
        self.restart()
        self.assertEqual(self.call('vanity_status')['record']['candidate'], candidate)
        self.call('unlock', password=PASSWORD)
        first = self.call('vanity_finalize', job_id=run['job_id'])
        self.restart()
        self.call('unlock', password=PASSWORD)
        again = self.call('vanity_finalize', job_id=run['job_id'])
        self.assertTrue(again['already_saved'])
        self.assertEqual(again['address'], first['address'])
        self.assertEqual(len(self.signer.store.public_wallets()), 1)
        self.assertIsNone(self.call('vanity_status')['record'])
        seed, receipt = self.signer.store.con.execute('SELECT seed,reservation FROM desktop_vanity_jobs').fetchone()
        self.assertEqual(seed, '')
        self.assertTrue(receipt.startswith('gAAAA'))
        self.assertNotIn(f'{8:064x}', self.signer.store.snapshot().decode('latin1'))

    def test_receipt_write_failure_rolls_back_wallet_and_metadata_then_retry_succeeds(self):
        run, candidate = self.found()
        with self.signer.store.con:
            self.signer.store.con.execute("CREATE TRIGGER refuse_receipt BEFORE UPDATE ON desktop_vanity_jobs "
                "WHEN NEW.state='saved' BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.call('vanity_finalize', job_id=run['job_id'])
        self.assertEqual(self.signer.store.public_wallets(), [])
        self.assertEqual(self.signer.store.con.execute('SELECT COUNT(*) FROM desktop_address_meta').fetchone()[0], 0)
        self.assertEqual(self.call('vanity_status')['record']['state'], 'found')
        with self.signer.store.con:
            self.signer.store.con.execute('DROP TRIGGER refuse_receipt')
        self.call('vanity_finalize', job_id=run['job_id'])
        self.assertEqual(len(self.signer.store.public_wallets()), 1)

    def test_encrypted_readback_failure_rolls_back_wallet(self):
        run, _ = self.found()
        with patch.object(self.signer.store, 'private_key', side_effect=ValueError('readback fixture')):
            with self.assertRaisesRegex(ValueError, 'readback'):
                self.call('vanity_finalize', job_id=run['job_id'])
        self.assertEqual(self.signer.store.public_wallets(), [])
        self.assertEqual(self.call('vanity_status')['record']['state'], 'found')
        self.call('vanity_finalize', job_id=run['job_id'])

    def test_password_rewrap_preserves_pending_seed_and_finalization(self):
        run, _ = self.found()
        seed = self.signer.store.con.execute('SELECT seed FROM desktop_vanity_jobs').fetchone()[0]
        self.signer.store.change_password(PASSWORD, PASSWORD+'-new')
        self.call('lock')
        self.call('unlock', password=PASSWORD+'-new')
        self.assertEqual(self.signer.store.con.execute('SELECT seed FROM desktop_vanity_jobs').fetchone()[0], seed)
        self.call('vanity_finalize', job_id=run['job_id'])

    def test_discard_is_unlocked_stopped_only_and_new_job_has_new_seed(self):
        run = self.prepare()
        with self.assertRaisesRegex(ValueError, 'Pause'):
            self.call('vanity_discard', job_id=run['job_id'])
        self.call('vanity_pause', job_id=run['job_id'])
        self.call('lock')
        with self.assertRaisesRegex(ValueError, 'Unlock'):
            self.call('vanity_discard', job_id=run['job_id'])
        self.call('unlock', password=PASSWORD)
        self.call('vanity_discard', job_id=run['job_id'])
        new = self.prepare()
        self.assertNotEqual(new['job_id'], run['job_id'])
        self.assertNotEqual(new['job']['public_key'], run['job']['public_key'])

    def test_locked_persistence_does_not_extend_idle_unlock(self):
        run = self.prepare()
        last = self.signer.last_active
        self.call('vanity_status')
        self.call('vanity_checkpoint', job_id=run['job_id'], run_id=run['run_id'], checkpoint=run['checkpoint'])
        self.assertEqual(self.signer.last_active, last)
        self.call('lock')
        self.call('vanity_pause', job_id=run['job_id'])
        self.assertIsNone(self.signer.profile_key)

    def test_v2_backup_round_trips_running_paused_found_and_saved(self):
        run = self.prepare()
        for state in ('running','paused','found','saved'):
            if state == 'paused':
                self.call('vanity_pause', job_id=run['job_id'])
            elif state == 'found':
                self.call('vanity_discard', job_id=run['job_id'])
                run, _ = self.found()
            elif state == 'saved':
                self.call('vanity_finalize', job_id=run['job_id'])
            encoded = profile_backup.encode(self.signer.store.snapshot(), PASSWORD)
            self.assertTrue(encoded.startswith(profile_backup.MAGIC))
            destination = ProfileStore(self.root/(state+'.db'))
            try:
                destination.restore_profile(profile_backup.decode(encoded, PASSWORD), PASSWORD)
                row = jobs.read(destination.con, run['job_id'])
                self.assertEqual(row['state'], 'paused' if state == 'running' else state)
                from wartnode.vanity_keys import VanityKeys
                VanityKeys.validate_private(row, destination.unlock(PASSWORD))
                if state == 'saved':
                    result = VanityKeys(destination).finalize(destination.unlock(PASSWORD), destination, run['job_id'])
                    self.assertTrue(result['already_saved'])
            finally:
                destination.con.close()

    def test_tampered_seed_job_checkpoint_or_receipt_backup_preserves_destination(self):
        run, _ = self.found()
        valid = self.signer.store.snapshot()
        for mutation in ('seed', 'job', 'checkpoint', 'reservation'):
            source = sqlite3.connect(':memory:')
            source.deserialize(valid)
            if mutation == 'seed':
                source.execute("UPDATE desktop_vanity_jobs SET seed='damaged'")
            elif mutation == 'job':
                job = dict(run['job'], strategy='linear')
                source.execute('UPDATE desktop_vanity_jobs SET job=?', (jobs.encode(job),))
            elif mutation == 'checkpoint':
                source.execute("UPDATE desktop_vanity_jobs SET checkpoint='{}'")
            else:
                source.execute("UPDATE desktop_vanity_jobs SET reservation='forged'")
            source.commit()
            destination = ProfileStore(self.root/(mutation+'.db'))
            before = destination.snapshot()
            try:
                with self.assertRaises(ValueError):
                    destination.restore_profile(source.serialize(), PASSWORD)
                self.assertEqual(destination.snapshot(), before)
            finally:
                destination.con.close()
                source.close()

    def legacy_fixture(self):
        store = self.signer.store
        address = store.add_address(self.signer.profile_key, f'{9:064x}', 'Legacy label')
        store.save_contact('Legacy contact', address)
        intent = crypto.TransferIntent(address, 100, 10)
        signed = crypto.SignedTransfer('ab'*32, {'pinHeight':32, 'nonceId':7,
                    'toAddr':address, 'amountE8':100, 'feeE8':10})
        store.record_outgoing(address, intent, signed, 'http://127.0.0.1:19400', 1000, 'mainnet')
        cipher = store.get_wallet_by_address(address)[2]
        source = sqlite3.connect(':memory:')
        source.deserialize(store.snapshot())
        source.execute('DROP TABLE desktop_vanity_jobs')
        source.execute('UPDATE desktop_profile SET format=1')
        source.commit()
        snapshot = source.serialize()
        source.close()
        return snapshot, address, cipher

    def test_format1_live_migration_preserves_ciphertext_labels_contacts(self):
        snapshot, address, cipher = self.legacy_fixture()
        path = self.root/'legacy.db'
        path.write_bytes(snapshot)
        migrated = ProfileStore(path)
        try:
            self.assertEqual(migrated.con.execute('SELECT format FROM desktop_profile').fetchone()[0], 2)
            self.assertEqual(migrated.get_wallet_by_address(address)[2], cipher)
            self.assertEqual(migrated.public_wallets()[0]['label'], 'Legacy label')
            self.assertEqual(migrated.contacts()[0]['name'], 'Legacy contact')
            self.assertEqual(migrated.reserved_amount(address), 110)
            self.assertEqual(migrated.private_key(migrated.unlock(PASSWORD), address)[0], address)
        finally:
            migrated.con.close()

    def test_format1_encrypted_backup_migrates_and_unknown_format_is_unchanged(self):
        snapshot, address, cipher = self.legacy_fixture()
        with patch.object(profile_backup, 'MAGIC', profile_backup.LEGACY_MAGIC):
            old_backup = profile_backup.encode(snapshot, PASSWORD)
        destination = ProfileStore(self.root/'legacy-restored.db')
        try:
            destination.restore_profile(profile_backup.decode(old_backup, PASSWORD), PASSWORD)
            self.assertEqual(destination.con.execute('SELECT format FROM desktop_profile').fetchone()[0], 2)
            self.assertEqual(destination.get_wallet_by_address(address)[2], cipher)
            self.assertEqual(destination.reserved_amount(address), 110)
        finally:
            destination.con.close()
        path = self.root/'future.db'
        path.write_bytes(snapshot)
        con = sqlite3.connect(path)
        with con:
            con.execute('UPDATE desktop_profile SET format=999')
        con.close()
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            ProfileStore(path)
        self.assertEqual(path.read_bytes(), before)

    def test_failed_migration_rolls_back_table_and_format_together(self):
        snapshot, address, cipher = self.legacy_fixture()
        path = self.root/'migration-failure.db'
        path.write_bytes(snapshot)
        con = sqlite3.connect(path)
        with con:
            con.execute("CREATE TRIGGER refuse_migration BEFORE UPDATE ON desktop_profile "
                        "BEGIN SELECT RAISE(ABORT,'fixture'); END")
        con.close()
        failed = ProfileStore.__new__(ProfileStore)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                failed.__init__(path)
            self.assertEqual(failed.con.execute('SELECT format FROM desktop_profile').fetchone()[0], 1)
            self.assertIsNone(failed.con.execute("SELECT 1 FROM sqlite_master WHERE name='desktop_vanity_jobs'").fetchone())
            self.assertEqual(failed.get_wallet_by_address(address)[2], cipher)
            self.assertEqual(failed.reserved_amount(address), 110)
        finally:
            failed.con.close()


if __name__ == '__main__':
    unittest.main()
