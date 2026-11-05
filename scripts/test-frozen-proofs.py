"""Exercise the packaged signing helper with synthetic keys and no node/funds."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wartnode import proofs
from wartnode.processes import SignerClient

OWNER = '751e76e8199196d454941c45d1b3a323f1433bd6ffded884'
OTHER = '06afd46bcdfd22ef94ac122aa11f241244a37ecc2db5740b'


def rejected(operation):
    try:
        operation()
    except ValueError:
        return
    raise AssertionError('An invalid proof or approval was accepted')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package-dir', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'evidence/frozen-proofs.json')
    args = parser.parse_args()
    package = args.package_dir
    if package is None:
        package = Path(json.loads((ROOT / 'evidence/extracted-package.json').read_text())['directory'])
    helper = package.resolve() / 'WartNodeHelper.exe'
    environment = {k: v for k, v in os.environ.items() if not k.startswith(('PYTHON', 'QT_'))}
    environment['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
    with tempfile.TemporaryDirectory(prefix='frozen-proofs-', dir=ROOT / 'work') as directory:
        with patch('wartnode.processes.application_command', lambda *a: [str(helper), *a]), \
                patch.dict(os.environ, environment, clear=True):
            client = SignerClient(directory)
        try:
            client.call('setup', password='synthetic-frozen-proof-password')
            client.call('import', kind='private', secret='00' * 31 + '01', label='Synthetic proof fixture')
            message = '  I control this address 🐗\r\nChallenge: frozen-prototype-123'
            reviewed = client.call('review_proof', address=OWNER, kind='message', content={'message': message})
            proof = client.call('sign_proof', token=reviewed['token'])
            assert proof['version'] == 2 and len(bytes.fromhex(proof['signature'])) == 65
            assert proofs.verify(proof, OWNER)['content']['message'] == message
            rejected(lambda: client.call('sign_proof', token=reviewed['token']))
            path = Path(directory) / 'release.bin'
            path.write_bytes(b'\x00\xfffrozen proof fixture\r\n')
            reviewed = client.call('review_proof', address=OWNER, kind='file', content=proofs.fingerprint(path))
            file_proof = client.call('sign_proof', token=reviewed['token'])
            assert proofs.verify(file_proof, OWNER, path)['kind'] == 'file'
            reviewed = client.call('review_proof', address=OWNER, kind='message', content={'message': 'cancelled'})
            client.call('lock')
            rejected(lambda: client.call('sign_proof', token=reviewed['token']))
            # Public verification still works after the helper is locked.
            assert proofs.verify(proof, OWNER)['address'] == OWNER
            rejected(lambda: proofs.verify(proof, OTHER))
            changed = copy.deepcopy(proof)
            changed['content']['message'] += ' '
            rejected(lambda: proofs.verify(changed, OWNER))
            recid = int(proof['signature'][128:], 16) ^ 1
            rejected(lambda: proofs.verify(dict(proof, signature=proof['signature'][:128] + f'{recid:02x}'), OWNER))
            path.write_bytes(b'changed')
            rejected(lambda: proofs.verify(file_proof, OWNER, path))
            assert proofs.verify(proofs.read(ROOT / 'tests/vectors/message-v1.wartproof'), OWNER)['address'] == OWNER
        finally:
            client.close()
    result = dict(passed=True, helper_sha256=hashlib.sha256(helper.read_bytes()).hexdigest(),
                  proofVersion=2, signatureBytes=65, messageAndFile=True, lockedVerification=True,
                  singleUseAndLockInvalidation=True, tamperingRejected=True, legacyProofVerified=True,
                  pythonOnHelperPath=False, liveChainSubmission=False, fundsUsed=False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
