"""New-profile encryption, atomic metadata, and complete backup recovery."""
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from wartnode.profile import ProfileStore
from wartnode import profile_backup
from wartnode.compat import WalletDB

PASSWORD = 'synthetic-profile-password'
KEY1 = '00'*31+'01'
KEY2 = '00'*31+'02'


class ProfileVaultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = ProfileStore(self.root/'wallet.db')
        self.key = self.store.setup(PASSWORD)

    def tearDown(self):
        self.store.con.close()
        self.temp.cleanup()

    def test_one_password_covers_multiple_addresses_and_rewraps_without_reencrypting_keys(self):
        first = self.store.add_address(self.key, KEY1, 'Main wallet')
        second = self.store.add_address(self.key, KEY2, 'Mining rig')
        before = self.store.con.execute('SELECT pk FROM wallets ORDER BY id').fetchall()
        self.store.edit_address(second, 'Workshop', default=True)
        self.store.change_password(PASSWORD, 'a different profile password')
        with self.assertRaises(ValueError):
            self.store.unlock(PASSWORD)
        key = self.store.unlock('a different profile password')
        for address in (first, second):
            self.assertEqual(self.store.private_key(key, address)[0], address)
        self.assertEqual(before, self.store.con.execute('SELECT pk FROM wallets ORDER BY id').fetchall())
        self.assertEqual(self.store.public_wallets()[0]['label'], 'Workshop')
        self.assertNotIn(KEY1, repr(before))

    def test_duplicate_and_swapped_key_records_cannot_change_the_owner(self):
        first = self.store.add_address(self.key, KEY1, 'Main')
        second = self.store.add_address(self.key, KEY2, 'Mining')
        with self.assertRaises(ValueError):
            self.store.add_address(self.key, KEY1, 'Duplicate')
        with self.store.con:
            token = self.store.get_wallet_by_address(second)[2]
            self.store.con.execute('UPDATE wallets SET pk=? WHERE address=?', (token, first))
        with self.assertRaisesRegex(ValueError, 'match'):
            self.store.private_key(self.key, first)

    def test_address_and_label_insert_roll_back_together(self):
        with self.store.con:
            self.store.con.execute("CREATE TRIGGER refuse_label BEFORE INSERT ON desktop_address_meta BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.add_address(self.key, KEY1, 'Main')
        self.assertEqual(self.store.con.execute('SELECT COUNT(*) FROM wallets').fetchone()[0], 0)

    def test_old_profile_is_rejected_before_any_write(self):
        path = self.root/'old.db'
        old = WalletDB(path)
        old.insert_wallet('01'*24, 'old encrypted data', 'old salt')
        old.con.close()
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'old wallet format'):
            ProfileStore(path)
        self.assertEqual(path.read_bytes(), before)

    def test_backup_restores_every_address_label_contact_and_reservation(self):
        first = self.store.add_address(self.key, KEY1, 'Main')
        second = self.store.add_address(self.key, KEY2, 'Mining')
        self.store.save_contact('Alex', second)
        # Persist an unresolved payment using the real journal contract.
        from wartnode.compat import wallet_utils as crypto
        intent = crypto.TransferIntent(second, 100, 10)
        signed = crypto.SignedTransfer('ab'*32, {'pinHeight':32, 'nonceId':7,
                    'toAddr':second, 'amountE8':100, 'feeE8':10})
        self.store.record_outgoing(first, intent, signed, 'http://127.0.0.1:19400', 1000, 'mainnet')
        path = self.root/'complete.wartprofile'
        profile_backup.save(self.store.snapshot(), PASSWORD, path)
        with self.assertRaises(ValueError):
            profile_backup.load(path, 'incorrect password')
        destination = ProfileStore(self.root/'restored.db')
        try:
            self.assertEqual(destination.restore_profile(profile_backup.load(path, PASSWORD), PASSWORD), 2)
            recovered = destination.unlock(PASSWORD)
            self.assertEqual(destination.private_key(recovered, first)[0], first)
            self.assertEqual([r['label'] for r in destination.public_wallets()], ['Main', 'Mining'])
            self.assertEqual(destination.contacts(), [dict(address=second, name='Alex')])
            self.assertEqual(destination.reserved_amount(first), 110)
            with self.assertRaisesRegex(ValueError, 'empty profile'):
                destination.restore_profile(profile_backup.load(path, PASSWORD), PASSWORD)
        finally:
            destination.con.close()

    def test_tampered_or_unknown_schema_backup_leaves_target_unchanged(self):
        self.store.add_address(self.key, KEY1, 'Main')
        encoded = bytearray(profile_backup.encode(self.store.snapshot(), PASSWORD))
        encoded[-10] ^= 1
        with self.assertRaises(ValueError):
            profile_backup.decode(bytes(encoded), PASSWORD)
        destination = ProfileStore(self.root/'empty.db')
        try:
            with self.store.con:
                self.store.con.execute('CREATE TABLE unexpected(value TEXT)')
            with self.assertRaisesRegex(ValueError, 'format'):
                destination.restore_profile(self.store.snapshot(), PASSWORD)
            self.assertFalse(destination.configured)
            self.assertEqual(destination.public_wallets(), [])
        finally:
            destination.con.close()


if __name__ == '__main__':
    unittest.main()
