"""Qualify a pinned warthog_py checkout for offline address proofs.

By default imports the SDK from an isolated workspace folder; --installed
checks the desktop's installed SDK instead. No wallet files are replaced.
All private keys below are public fixtures from the upstream SDK's tests.
"""
import argparse
import ast
import base64
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
TEST_KEY = '966a71a98bb5d13e9116c0dffa3f1a7877e45c6f563897b96cfd5c59bf0803e0'
TEST_ADDRESS = '3661579d61abde5837a8686dc4d65348a2fc61b1fe5f4093'


def strict_signature(signature):
    """Demonstrate the public-input guard needed around SDK recovery."""
    if not isinstance(signature, str) or not re.fullmatch('[0-9a-f]{130}', signature):
        raise ValueError('Invalid signature encoding')
    raw = bytes.fromhex(signature)
    r, s = int.from_bytes(raw[:32], 'big'), int.from_bytes(raw[32:64], 'big')
    if not 1 <= r < ORDER or not 1 <= s <= ORDER // 2 or raw[64] not in range(4):
        raise ValueError('Invalid signature values')
    return signature


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=ROOT / 'work/sdk-evaluation')
    parser.add_argument('--node', default=shutil.which('node'))
    parser.add_argument('--installed', action='store_true', help='Qualify the installed desktop SDK/dependencies')
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    sdk_root = workspace / 'upstream-py'
    manifest = json.loads((ROOT / 'tests/fixtures/proof-sdk-sources.json').read_text(encoding='utf-8'))
    for path, expected_hash in manifest['sha256'].items():
        if hashlib.sha256((workspace / path).read_bytes()).hexdigest() != expected_hash:
            raise ValueError('The SDK source differs from the pinned qualification input: ' + path)
    sys.path[:0] = ([str(ROOT)] if args.installed else
                   [str(workspace / 'deps-py314'), str(sdk_root / 'src'), str(ROOT)])
    (workspace / 'tmp').mkdir(exist_ok=True)
    os.environ['TMP'] = os.environ['TEMP'] = str(workspace / 'tmp')
    tempfile.tempdir = str(workspace / 'tmp')

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, utils
    from ecdsa import SigningKey, SECP256k1
    from warthog_py import Account
    from warthog_py.errors import InvalidSignatureError
    from wartnode import proofs
    if args.installed:
        import warthog_py
        installed = Path(warthog_py.__file__).parent
        for path, expected_hash in manifest['sha256'].items():
            prefix = 'upstream-py/src/warthog_py/'
            if path.startswith(prefix) and hashlib.sha256((installed / path.removeprefix(prefix)).read_bytes()).hexdigest() != expected_hash:
                raise ValueError('Installed SDK differs from the qualified commit: ' + path)

    account = Account.from_private_key_hex(TEST_KEY)
    vectors = None
    for statement in ast.parse((sdk_root / 'tests/test_account.py').read_text(encoding='utf-8')).body:
        if isinstance(statement, ast.Assign) and any(isinstance(n, ast.Name) and n.id == 'PINNED_VECTORS'
                                                    for n in statement.targets):
            vectors = ast.literal_eval(statement.value)
    if vectors is None:
        raise ValueError('The pinned upstream account test vectors are missing')

    messages = [v['message'] for v in vectors] + [
        'Warthog 🐗 — ownership'.encode('utf-8'), b'  leading and trailing  ',
        b'first\r\nsecond\n', b'00ff0123456789', b'\x00\xff\x80\x01',
        b'WartNode Signed Proof\x00v1\x00', b'x' * 16384,
    ]
    # Exercise complete exported v2 proofs through the actual TypeScript SDK.
    synthetic_key = SigningKey.from_string(bytes.fromhex(TEST_KEY), curve=SECP256k1)
    desktop_proofs = [proofs.sign(proofs.prepare(TEST_ADDRESS, kind, content), synthetic_key)
                      for kind, content in (
                          ('message', {'message': 'Claim Johnny 🐗\r\nChallenge: synthetic-123'}),
                          ('file', {'name': 'release.bin', 'size': 0, 'sha256': hashlib.sha256(b'').hexdigest()}))]
    messages.extend(proofs.signing_bytes({k: v for k, v in proof.items() if k != 'signature'})
                    for proof in desktop_proofs)
    diagnostics = {}
    observations = []
    cross = None

    def no_network(*_args, **_kwargs):
        raise AssertionError('SDK qualification attempted network access')

    def typescript_results():
        nonlocal cross
        if cross is None:
            if not args.node:
                raise AssertionError('Node.js is required for the TypeScript comparison')
            payload = dict(privateKeyHex=TEST_KEY, cases=[
                dict(name=str(i), messageBase64=base64.b64encode(m).decode('ascii'),
                     signature=(desktop_proofs[i - len(messages) + len(desktop_proofs)]['signature']
                                if i >= len(messages) - len(desktop_proofs) else account.sign_bytes(m)[3]),
                     asString=i in (0, 1, 2, 3, 4, 5, 6))
                for i, m in enumerate(messages)])
            result = subprocess.run([args.node, str(ROOT / 'scripts/qualify-proof-sdk.mjs'), str(workspace / 'typescript')],
                                    input=json.dumps(payload).encode(), capture_output=True, timeout=30)
            if result.returncode:
                raise AssertionError(result.stderr.decode('utf-8', errors='replace'))
            cross = json.loads(result.stdout)
        return cross

    class Qualification(unittest.TestCase):
        def test_01_address_matches_existing_desktop_derivation(self):
            self.assertEqual(account.address.hex, TEST_ADDRESS)
            self.assertEqual(proofs.address_from_public_key(account.public_key_hex), TEST_ADDRESS)

        def test_02_published_rfc6979_vectors_match_exactly(self):
            for vector in vectors:
                with self.subTest(vector=vector['name']):
                    r, s, recid, wire = account.sign_bytes(vector['message'])
                    self.assertEqual(r.hex(), vector['r'])
                    self.assertEqual(s.hex(), vector['s'])
                    self.assertEqual(recid, vector['recid'])
                    self.assertEqual(wire, vector['r'] + vector['s'] + f'{recid:02x}')

        def test_03_signing_is_deterministic_and_normalized(self):
            for message in messages:
                first, second = account.sign_bytes(message), account.sign_bytes(message)
                self.assertEqual(first, second)
                self.assertEqual(strict_signature(first[3]), first[3])

        def test_04_unicode_and_string_bytes_inputs_agree(self):
            for text in ['Warthog 🐗', 'line1\r\nline2\t', '  preserve spacing  ']:
                self.assertEqual(account.sign_bytes(text), account.sign_bytes(text.encode('utf-8')))

        def test_05_signatures_verify_with_desktop_native_crypto(self):
            public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256K1(), account.public_key)
            for message in messages:
                r, s, _, _ = account.sign_bytes(message)
                public.verify(utils.encode_dss_signature(int.from_bytes(r, 'big'), int.from_bytes(s, 'big')),
                              message, ec.ECDSA(hashes.SHA256()))

        def test_06_python_signatures_recover_in_typescript(self):
            result = typescript_results()
            self.assertEqual(result['address'], TEST_ADDRESS)
            for row in result['results']:
                self.assertEqual(row['recovered'], TEST_ADDRESS)

        def test_07_typescript_signatures_recover_in_python(self):
            for message, row in zip(messages, typescript_results()['results']):
                self.assertEqual(Account.recover_address(message, row['signature']).hex, TEST_ADDRESS)

        def test_08_both_sdks_produce_identical_wire_signatures(self):
            for message, row in zip(messages, typescript_results()['results']):
                self.assertEqual(account.sign_bytes(message)[3], row['signature'])

        def test_09_desktop_proofs_use_exact_sdk_wire_signatures(self):
            legacy_key = SigningKey.from_string(bytes.fromhex(TEST_KEY), curve=SECP256k1)
            prepared = proofs.prepare(TEST_ADDRESS, 'message', {'message': 'Proof compatibility check'})
            proof = proofs.sign(prepared, legacy_key)
            body = {k: v for k, v in proof.items() if k != 'signature'}
            wire = account.sign_bytes(proofs.signing_bytes(body))[3]
            self.assertEqual(wire, proof['signature'])
            self.assertEqual(Account.recover_address(proofs.signing_bytes(body), wire).hex, TEST_ADDRESS)
            self.assertEqual(proofs.verify(proof, TEST_ADDRESS)['address'], TEST_ADDRESS)

        def test_10_file_manifest_can_be_signed_and_checked(self):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'fixture.bin'
                path.write_bytes(b'\x00\xffSDK file fixture\r\n')
                content = proofs.fingerprint(path)
                body = proofs.prepare(TEST_ADDRESS, 'file', content)
                preimage = proofs.signing_bytes(body)
                signature = account.sign_bytes(preimage)[3]
                self.assertEqual(Account.recover_address(preimage, signature).hex, TEST_ADDRESS)
                self.assertEqual(proofs.fingerprint(path), content)
                path.write_bytes(b'changed')
                self.assertNotEqual(proofs.fingerprint(path)['sha256'], content['sha256'])

        def test_11_manifest_metadata_and_domain_changes_fail_expected_address(self):
            body = proofs.prepare(TEST_ADDRESS, 'file', {'name': 'file.bin', 'size': 0,
                                   'sha256': hashlib.sha256(b'').hexdigest()})
            original = proofs.signing_bytes(body)
            signature = account.sign_bytes(original)[3]
            for change in [b'other domain' + original, original + b' ', original.replace(b'file.bin', b'other.bin')]:
                self.assertNotEqual(Account.recover_address(change, signature).hex, TEST_ADDRESS)

        def test_12_tampering_requires_expected_address_comparison(self):
            signature = account.sign_bytes('hello')[3]
            recovered = Account.recover_address('modified message', signature)
            self.assertNotEqual(recovered.hex, TEST_ADDRESS)
            diagnostics['modifiedMessageCanRecoverAnotherAddress'] = True

        def test_13_wrong_expected_address_is_rejected(self):
            other = Account.from_private_key_hex('00' * 31 + '02').address.hex
            self.assertNotEqual(Account.recover_address('hello', account.sign_bytes('hello')[3]).hex, other)

        def test_14_recovery_id_changes_cannot_impersonate_expected_address(self):
            wire = account.sign_bytes('hello')[3]
            changed = wire[:128] + f'{int(wire[128:], 16) ^ 1:02x}'
            try:
                recovered = Account.recover_address('hello', changed)
                self.assertNotEqual(recovered.hex, TEST_ADDRESS)
            except InvalidSignatureError:
                pass

        def test_15_strict_input_guard_rejects_malformed_signatures(self):
            good = account.sign_bytes('hello')[3]
            invalid = [None, '', ' ' * 130, 'z' * 130, good[:-1], '00' * 65,
                       good[:128] + '04', ORDER.to_bytes(32, 'big').hex() + good[64:],
                       good[:64] + ORDER.to_bytes(32, 'big').hex() + good[128:]]
            for value in invalid:
                with self.subTest(type=type(value).__name__), self.assertRaises(ValueError):
                    strict_signature(value)

        def test_16_recovery_needs_no_private_key(self):
            wire = account.sign_bytes('public verifier')[3]
            with patch('coincurve.PrivateKey', side_effect=AssertionError('Private key access')):
                self.assertEqual(Account.recover_address('public verifier', wire).hex, TEST_ADDRESS)

        def test_17_signing_and_recovery_are_offline(self):
            with patch.object(socket.socket, 'connect', no_network), \
                 patch.object(socket, 'create_connection', no_network):
                created = Account.from_private_key_hex(TEST_KEY)
                wire = created.sign_bytes('offline qualification')[3]
                self.assertEqual(Account.recover_address('offline qualification', wire).hex, TEST_ADDRESS)

        def test_18_high_s_policy_is_retained_outside_the_sdk(self):
            r, s, recid, _ = account.sign_bytes('hello')
            high = (r + (ORDER - int.from_bytes(s, 'big')).to_bytes(32, 'big') + bytes([recid ^ 1])).hex()
            with self.assertRaises(ValueError):strict_signature(high)
            try:
                diagnostics['sdkRecoveryAcceptsHighS'] = Account.recover_address('hello', high).hex == TEST_ADDRESS
            except InvalidSignatureError:
                diagnostics['sdkRecoveryAcceptsHighS'] = False

        def test_19_existing_encrypted_vault_key_can_feed_sdk_factory(self):
            from wartnode.profile import ProfileStore
            with tempfile.TemporaryDirectory() as directory:
                store = ProfileStore(Path(directory) / 'wallet.db')
                try:
                    wrapping_key = store.setup('synthetic-sdk-qualification')
                    store.add_address(wrapping_key, TEST_KEY, 'Synthetic fixture')
                    owner, legacy_key = store.private_key(wrapping_key, TEST_ADDRESS)
                    sdk_account = Account.from_private_key(legacy_key.to_string())
                    wire = sdk_account.sign_bytes('vault compatibility')[3]
                    self.assertEqual(Account.recover_address('vault compatibility', wire).hex, owner)
                finally:
                    store.con.close()

        def test_20_sdk_edge_behaviors_are_recorded_for_adapter_design(self):
            diagnostics['accountReprContainsPrivateKey'] = TEST_KEY in repr(account)
            try:
                Account.try_recover_address('hello', ' ' * 130)
                diagnostics['whitespaceRecoveryException'] = None
            except Exception as error:
                diagnostics['whitespaceRecoveryException'] = type(error).__name__
            try:
                Account.from_private_key_hex('SYNTHETIC_INVALID_KEY')
            except Exception as error:
                diagnostics['invalidPrivateKeyErrorEchoesInput'] = 'SYNTHETIC_INVALID_KEY' in str(error)

        def test_21_typescript_signatures_verify_as_complete_desktop_proofs(self):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'release.bin'
                path.write_bytes(b'')
                for proof, row in zip(desktop_proofs, typescript_results()['results'][-len(desktop_proofs):]):
                    signed = dict(proof, signature=row['signature'])
                    self.assertEqual(proofs.verify(signed, TEST_ADDRESS, path)['address'], TEST_ADDRESS)

        def test_22_legacy_prototype_vector_remains_verifiable(self):
            proof = proofs.read(ROOT / 'tests/vectors/message-v1.wartproof')
            legacy = Account.from_private_key(bytes.fromhex('00' * 31 + '01'))
            body = {k: v for k, v in proof.items() if k != 'signature'}
            wire = legacy.sign_bytes(proofs.signing_bytes(body))[3]
            self.assertEqual(wire[:128], proof['signature'])
            self.assertEqual(proofs.verify(proof, legacy.address.hex)['address'], legacy.address.hex)

    class Results(unittest.TextTestResult):
        def addSuccess(self, test):
            super().addSuccess(test)
            observations.append(dict(name=test.id().split('.')[-1], passed=True))

        def addFailure(self, test, error):
            super().addFailure(test, error)
            observations.append(dict(name=test.id().split('.')[-1], passed=False))

        def addError(self, test, error):
            super().addError(test, error)
            observations.append(dict(name=test.id().split('.')[-1], passed=False))

    result = unittest.TextTestRunner(verbosity=2, resultclass=Results).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(Qualification))
    dependencies = {name: importlib.metadata.version(name) for name in (
        'coincurve', 'cffi', 'bip32', 'mnemonic', 'pycryptodome', 'requests')}
    report = dict(passed=result.wasSuccessful(), testsRun=result.testsRun, python=sys.version,
                  installedDesktopSdk=args.installed, desktopProofVersion=proofs.VERSION,
                  desktopProofCases=len(desktop_proofs),
                  pythonSdkCommit=manifest['pythonSdkCommit'],
                  typescriptSdkCommit=manifest['typescriptSdkCommit'],
                  crossLanguageMessages=len(messages), dependencies=dependencies,
                  diagnostics=diagnostics, checks=observations)
    output = ROOT / 'work/sdk-migration/qualification.json' if args.installed else workspace / 'qualification.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':raise SystemExit(main())
