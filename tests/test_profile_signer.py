"""Profile authorization must preserve the existing payment safety boundaries."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from wartnode.signer import Signer
from wartnode.dashboard import ProfileBalance
from wartnode.compat import wallet_utils as crypto
from test_core import FakeRPC, OWNER, OTHER

PASSWORD = 'synthetic-profile-password'


class ProfileSignerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.signer = Signer(self.temp.name)
        self.signer.call('setup', dict(password=PASSWORD))
        self.signer.call('import', dict(kind='private', secret='00'*31+'01', label='Main'))
        self.signer.call('import', dict(kind='private', secret='00'*31+'02', label='Mining'))
        FakeRPC.fee, FakeRPC.result, FakeRPC.broadcasts = 9992, 'pending', 0

    def tearDown(self):
        self.signer.lock()
        self.signer.store.con.close()
        self.temp.cleanup()

    @patch('wartnode.signer.NodeRPC', FakeRPC)
    def test_unlock_once_select_another_address_and_send_without_an_address_password(self):
        self.signer.call('lock', {})
        with self.assertRaisesRegex(ValueError, 'Unlock'):
            self.signer.call('create', {})
        self.signer.call('unlock', dict(password=PASSWORD))
        self.signer.call('select', dict(address=OWNER))
        review = self.signer.call('review', dict(recipient=OTHER, amount='1'))
        prepared = crypto.SignedTransfer('ed'*32, dict(pinHeight=32, nonceId=7,
                    toAddr=OTHER, amountE8=100000000, feeE8=9992))
        with patch.object(crypto, 'prepare_tx', return_value=prepared):
            result = self.signer.call('send', dict(token=review['token']))
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(self.signer.store.reserved_amount(OWNER), 100009992)
        self.assertEqual(FakeRPC.broadcasts, 1)
        with self.assertRaises(ValueError):
            self.signer.call('send', dict(token=review['token']))

    @patch('wartnode.signer.NodeRPC', FakeRPC)
    def test_locked_public_balance_reads_neither_decrypt_nor_refresh_authorization(self):
        self.signer.call('lock', {})
        last_active = self.signer.last_active
        with patch.object(self.signer.store, 'private_key', side_effect=AssertionError('Secret read')):
            result = self.signer.call('profile_balances', {})
        snapshot = ProfileBalance.from_rows([OWNER, OTHER], result['rows'], result['source'])
        self.assertTrue(snapshot.complete)
        self.assertEqual(snapshot.available, 2000000000)
        self.assertIsNone(self.signer.profile_key)
        self.assertEqual(self.signer.last_active, last_active)
        public = json.dumps(self.signer.call('list', {}))
        self.assertNotIn('profile_key', public)
        self.assertNotIn('private_key', public)
        self.assertNotIn('wrapped_key', public)

    @patch('wartnode.signer.NodeRPC', FakeRPC)
    def test_partial_balance_and_reservations_cannot_inflate_a_profile_total(self):
        self.signer.call('select', dict(address=OWNER))
        review = self.signer.call('review', dict(recipient=OTHER, amount='1'))
        FakeRPC.result = 'uncertain'
        prepared = crypto.SignedTransfer('ed'*32, dict(pinHeight=32, nonceId=7,
                    toAddr=OTHER, amountE8=100000000, feeE8=9992))
        with patch.object(crypto, 'prepare_tx', return_value=prepared):
            self.signer.call('send', dict(token=review['token']))
        result = self.signer.call('profile_balances', {})
        snapshot = ProfileBalance.from_rows([OWNER, OTHER], result['rows'], result['source'])
        self.assertEqual(snapshot.available, 2000000000-100009992)
        with patch.object(FakeRPC, 'balance', side_effect=lambda owner: '10' if owner == OWNER else (_ for _ in ()).throw(ValueError('unavailable'))):
            result = self.signer.call('profile_balances', {})
        incomplete = ProfileBalance.from_rows([OWNER, OTHER], result['rows'], result['source'])
        self.assertFalse(incomplete.complete)
        self.assertIsNone(incomplete.available)
        self.assertEqual(self.signer.store.reserved_amount(OWNER), 100009992)

    @patch('wartnode.signer.NodeRPC', FakeRPC)
    def test_select_lock_and_password_change_invalidate_payment_review(self):
        review = self.signer.call('review', dict(recipient=OWNER, amount='1'))
        self.signer.call('cancel_review',{})
        with self.assertRaises(ValueError):self.signer.call('send',dict(token=review['token']))
        review = self.signer.call('review', dict(recipient=OWNER, amount='1'))
        self.signer.call('select', dict(address=OWNER))
        with self.assertRaises(ValueError):
            self.signer.call('send', dict(token=review['token']))
        self.signer.call('change_password', dict(old_password=PASSWORD, new_password='replacement-profile-password'))
        self.assertIsNone(self.signer.profile_key)
        with self.assertRaises(ValueError):
            self.signer.call('unlock', dict(password=PASSWORD))
        self.signer.call('unlock', dict(password='replacement-profile-password'))
        self.assertFalse(self.signer.call('list', {})['locked'])


if __name__ == '__main__':
    unittest.main()
