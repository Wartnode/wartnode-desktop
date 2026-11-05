"""Public proofs bind the content, domain, key, address, and expected identity."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives import hashes
from ecdsa import VerifyingKey, SECP256k1
from ecdsa.util import sigdecode_string
from warthog_py import Account
from wartnode import proofs
from wartnode.compat import wallet_utils as crypto
from wartnode.signer import Signer

OWNER, KEY = crypto.wallet_from_pkhex('00' * 31 + '01')
OTHER, OTHER_KEY = crypto.wallet_from_pkhex('00' * 31 + '02')
ROOT = Path(__file__).resolve().parents[1]


def message_proof(message='Claim this address for Johnny\nChallenge: synthetic-test-123'):
    return proofs.sign(proofs.prepare(OWNER, 'message', {'message': message}), KEY)


class ProofTests(unittest.TestCase):
    def test_message_roundtrip_preserves_exact_unicode_spacing_and_newlines(self):
        message = '  Warthog 🐗\r\nJohnny\t\n'
        proof = proofs.loads(proofs.dumps(message_proof(message)))
        self.assertEqual(proofs.verify(proof, OWNER.upper())['content']['message'], message)
        self.assertEqual(proof['address'], OWNER)
        self.assertEqual(proofs.address_from_public_key(proof['publicKey']), OWNER)

    def test_signature_verifies_with_an_independent_ecdsa_implementation(self):
        proof = message_proof()
        body = {k: v for k, v in proof.items() if k != 'signature'}
        # Deliberately reproduce the wire protocol without signing_bytes().
        encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        digest = hashlib.sha256(b'\x00WartNode Signed Proof\x00v2\x00' + len(encoded).to_bytes(4, 'big') + encoded).digest()
        public = VerifyingKey.from_string(bytes.fromhex(proof['publicKey']), curve=SECP256k1)
        self.assertTrue(public.verify_digest(bytes.fromhex(proof['signature'])[:64], digest, sigdecode=sigdecode_string))

    def test_published_vector_remains_deterministic_and_verifiable(self):
        vector = proofs.read(ROOT / 'tests/vectors/message-v2.wartproof')
        generated = message_proof(vector['content']['message'])
        self.assertEqual(generated, vector)
        self.assertEqual(proofs.verify(vector, OWNER)['address'], OWNER)

    def test_original_prototype_proofs_remain_verifiable_but_cannot_be_downgraded(self):
        legacy = proofs.read(ROOT / 'tests/vectors/message-v1.wartproof')
        self.assertEqual(proofs.verify(legacy, OWNER)['address'], OWNER)
        changed = copy.deepcopy(legacy)
        changed['content']['message'] += ' '
        with self.assertRaisesRegex(ValueError, 'does not match'):
            proofs.verify(changed, OWNER)
        current = message_proof()
        with self.assertRaisesRegex(ValueError, 'does not match'):
            proofs.verify(dict(current, version=1, signature=current['signature'][:128]), OWNER)

    def test_sdk_signing_and_recovery_use_the_same_framed_bytes(self):
        proof = message_proof('  Claim for Johnny 🐗\r\n')
        preimage = proofs.signing_bytes({k: v for k, v in proof.items() if k != 'signature'})
        sdk = Account.from_private_key(KEY.to_string())
        self.assertEqual(proof['signature'], sdk.sign_bytes(preimage)[3])
        self.assertEqual(len(bytes.fromhex(proof['signature'])), 65)
        self.assertEqual(Account.recover_address(preimage, proof['signature']).hex, OWNER)
        # Verification must never construct an account holding a private key.
        with patch.object(Account, 'from_private_key', side_effect=AssertionError('Private key access')):
            self.assertEqual(proofs.verify(proof, OWNER)['address'], OWNER)

    def test_sdk_failures_are_sanitized_and_invalid_signatures_never_reach_recovery(self):
        for target in ('from_private_key', 'sign_bytes'):
            with patch.object(Account, target, side_effect=ValueError('synthetic-private-data')):
                with self.assertRaisesRegex(ValueError, '^The wallet could not sign this proof[.]$'):
                    message_proof()
        proof = message_proof()
        with patch.object(Account, 'recover_public_key', side_effect=IndexError('sdk details')):
            with self.assertRaisesRegex(ValueError, '^The signature does not match the content[.]'):
                proofs.verify(proof, OWNER)
        malformed = [' ' * 130, proof['signature'].upper(), proof['signature'][:128],
                     proof['signature'][:128] + '04', '00' * 32 + proof['signature'][64:],
                     proofs.ORDER.to_bytes(32, 'big').hex() + proof['signature'][64:]]
        with patch.object(Account, 'recover_public_key') as recover:
            for signature in malformed:
                with self.subTest(signature=signature), self.assertRaises(ValueError):
                    proofs.verify(dict(proof, signature=signature), OWNER)
            recover.assert_not_called()

    def test_changing_recovery_id_or_version_invalidates_the_proof(self):
        proof = message_proof()
        changed = proof['signature'][:128] + bytes([int(proof['signature'][128:], 16) ^ 1]).hex()
        with self.assertRaisesRegex(ValueError, 'does not match'):
            proofs.verify(dict(proof, signature=changed), OWNER)
        with self.assertRaises(ValueError):
            proofs.verify(dict(proof, version=1), OWNER)

    def test_content_address_and_key_substitution_are_rejected(self):
        proof = message_proof()
        for field, value in [('address', OTHER), ('publicKey', OTHER_KEY.verifying_key.to_string('compressed').hex()),
                             ('signature', '01' * 65)]:
            changed = dict(proof, **{field: value})
            with self.subTest(field=field), self.assertRaises(ValueError):
                proofs.verify(changed, OWNER)
        changed = copy.deepcopy(proof)
        changed['content']['message'] += ' '
        with self.assertRaisesRegex(ValueError, 'does not match'):
            proofs.verify(changed, OWNER)
        with self.assertRaisesRegex(ValueError, 'Different signer'):
            proofs.verify(proof, OTHER)

    def test_domain_is_not_optional_and_high_s_is_rejected(self):
        proof = message_proof()
        body = {k: v for k, v in proof.items() if k != 'signature'}
        key = ec.derive_private_key(1, ec.SECP256K1())
        raw_json = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
        r, s = utils.decode_dss_signature(key.sign(raw_json, ec.ECDSA(hashes.SHA256())))
        proof['signature'] = (r.to_bytes(32, 'big') + min(s, proofs.ORDER-s).to_bytes(32, 'big') + b'\x00').hex()
        with self.assertRaisesRegex(ValueError, 'does not match'):
            proofs.verify(proof, OWNER)
        proof = message_proof()
        s = int(proof['signature'][64:128], 16)
        proof['signature'] = proof['signature'][:64] + (proofs.ORDER - s).to_bytes(32, 'big').hex() + proof['signature'][128:]
        with self.assertRaisesRegex(ValueError, 'normalized'):
            proofs.verify(proof, OWNER)

    def test_strict_format_rejects_ambiguous_or_unsupported_documents(self):
        proof = message_proof()
        invalid = [dict(proof, version=True), dict(proof, version=3), dict(proof, network='testnet'),
                   dict(proof, extra='ignored'), dict(proof, kind='transaction'),
                   dict(proof, signature='00' * 65), dict(proof, publicKey='00' * 33),
                   dict(proof, content={'message': 'ok', 'unsigned': 'hidden'}),
                   dict(proof, content={'message': '\ud800'}), dict(proof, content={'message': '\x1b[31m'})]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                proofs.verify(value, OWNER)
        duplicate = proofs.dumps(proof).replace('"version": 2', '"version": 2, "version": 2')
        for text in [duplicate, '[]', '{', '[' * 1500, ' ' * (proofs.MAX_PROOF_BYTES + 1)]:
            with self.assertRaises(ValueError):proofs.loads(text)
        for message in ['', 'x' * (proofs.MAX_MESSAGE_BYTES+1)]:
            with self.assertRaises(ValueError):message_proof(message)
        self.assertEqual(proofs.verify(message_proof('x' * proofs.MAX_MESSAGE_BYTES), OWNER)['kind'], 'message')

    def test_file_proofs_check_bytes_and_size_allowing_a_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'release.zip'
            path.write_bytes(b'\x00\xffsynthetic file\r\n')
            proof = proofs.sign(proofs.prepare(OWNER, 'file', proofs.fingerprint(path)), KEY)
            path = path.rename(Path(tmp) / 'renamed.zip')
            self.assertEqual(proofs.verify(proof, OWNER, path)['kind'], 'file')
            with self.assertRaisesRegex(ValueError, 'original file'):proofs.verify(proof, OWNER)
            path.write_bytes(b'\x00\xffsynthetic file\n\r')
            with self.assertRaisesRegex(ValueError, 'does not match'):proofs.verify(proof, OWNER, path)
            changed = copy.deepcopy(proof)
            changed['content']['name'] = 'different.zip'
            with self.assertRaisesRegex(ValueError, 'signature does not match'):proofs.verify(changed, OWNER, path)

    def test_empty_files_cancellation_and_files_changed_while_hashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'empty'
            path.write_bytes(b'')
            self.assertEqual(proofs.fingerprint(path)['sha256'], hashlib.sha256(b'').hexdigest())
            cancel = threading.Event();cancel.set()
            with self.assertRaisesRegex(ValueError, 'cancelled'):proofs.fingerprint(path, cancel)
            path.write_bytes(b'a' * 1048577)
            def append(*_):
                with path.open('ab') as stream:stream.write(b'x')
            # A one-time change is detected even though all bytes could be read.
            changed = [False]
            def change_once(*args):
                if not changed[0]:changed[0] = True;append()
            with self.assertRaisesRegex(ValueError, 'changed'):proofs.fingerprint(path, progress=change_once)

    def test_cli_works_without_desktop_or_wallet_unlock_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'message.wartproof'
            path.write_text(proofs.dumps(message_proof('Unicode CLI proof 🐗')), encoding='utf-8')
            command = [sys.executable, str(ROOT / 'scripts/verify-proof.py'), str(path), '--address']
            good = subprocess.run(command + [OWNER], capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(good.returncode, 0, good.stderr)
            self.assertIn('VERIFIED: ' + OWNER, good.stdout)
            bad = subprocess.run(command + [OTHER], capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(bad.returncode, 1)
            self.assertIn('Different signer', bad.stderr)


class ProofSignerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.signer = Signer(self.temp.name)
        self.signer.call('setup', {'password': 'synthetic-proof-password'})
        self.signer.call('import', {'kind': 'private', 'secret': '00'*31+'01'})

    def tearDown(self):
        self.signer.lock();self.signer.store.con.close();self.temp.cleanup()

    def review(self, address=OWNER):
        return self.signer.call('review_proof', dict(address=address, kind='message', content={'message': 'test challenge'}))

    def test_review_does_not_decrypt_and_signing_is_offline_single_use(self):
        with patch('wartnode.signer.NodeRPC', side_effect=AssertionError('Network request')), \
             patch.object(self.signer.store, 'private_key', wraps=self.signer.store.private_key) as private:
            review = self.review()
            private.assert_not_called()
            # Input is frozen at review time.
            review['content']['message'] = 'mutated UI response'
            proof = self.signer.call('sign_proof', {'token': review['token']})
            self.assertEqual(proofs.verify(proof, OWNER)['content']['message'], 'test challenge')
            self.assertEqual(private.call_count, 1)
            with self.assertRaises(ValueError):self.signer.call('sign_proof', {'token': review['token']})

    def test_lock_expiry_wrong_token_and_address_selection_cancel_authorization(self):
        review = self.review()
        with self.assertRaises(ValueError):self.signer.call('sign_proof', {'token': 'wrong'})
        self.assertIsNone(self.signer.proof_review)
        review = self.review();self.signer.proof_review['created'] -= 121
        with self.assertRaises(ValueError):self.signer.call('sign_proof', {'token': review['token']})
        review = self.review();self.signer.call('select', {'address': OWNER})
        with self.assertRaises(ValueError):self.signer.call('sign_proof', {'token': review['token']})
        review = self.review();self.signer.lock()
        with self.assertRaisesRegex(ValueError, 'Unlock'):self.review()
        with self.assertRaisesRegex(ValueError, 'Unlock'):self.signer.call('sign_proof', {'token': review['token']})

    def test_only_owned_addresses_and_matching_cancellations_are_accepted(self):
        with self.assertRaisesRegex(ValueError, 'not in the profile'):self.review(OTHER)
        old = self.review();new = self.review()
        self.signer.call('cancel_proof_review', {'token': old['token']})
        self.assertEqual(self.signer.proof_review['token'], new['token'])
        self.signer.call('cancel_proof_review', {'token': new['token']})
        self.assertIsNone(self.signer.proof_review)

    def test_connection_changes_and_idle_lock_invalidate_proof_approval(self):
        review = self.review()
        with patch.object(self.signer, 'connection_context', return_value=('changed', 1)):
            with self.assertRaisesRegex(ValueError, 'Unlock'):
                self.signer.call('sign_proof', {'token': review['token']})
        self.assertIsNone(self.signer.proof_review)
        self.signer.call('unlock', {'password': 'synthetic-proof-password'})
        review = self.review()
        from wartnode import settings
        settings.save(self.temp.name, auto_lock_minutes=5)
        self.signer.last_active -= 301
        with self.assertRaisesRegex(ValueError, 'Unlock'):
            self.signer.call('sign_proof', {'token': review['token']})

    def test_proof_and_payment_review_tokens_are_separate(self):
        self.signer.review = {'token': 'payment-review-fixture'}
        reviewed = self.review()
        with self.assertRaises(ValueError):
            self.signer.call('sign_proof', {'token': self.signer.review['token']})
        self.assertEqual(self.signer.review, {'token': 'payment-review-fixture'})
        reviewed = self.review()
        self.signer.call('sign_proof', {'token': reviewed['token']})
        self.assertEqual(self.signer.review, {'token': 'payment-review-fixture'})


if __name__ == '__main__':unittest.main()
