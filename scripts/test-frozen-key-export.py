"""Qualify per-address export using the actual packaged helper and synthetic keys."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wartnode import __version__
from wartnode.compat import wallet_utils as crypto
from wartnode.processes import SignerClient

PASSWORD = 'synthetic-frozen-key-export-password'


def rejected(operation):
    try:
        operation()
    except ValueError:
        return
    raise ValueError('An unauthorized private-key export was accepted')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT/'evidence/ci/frozen-key-export.json')
    options = parser.parse_args()
    package = options.package_dir.resolve()
    helper = package/'WartNodeHelper.exe'
    context = json.loads((package/'provenance/build-context.json').read_text(encoding='utf-8'))
    for name in ('wartnode/key_ui.py', 'wartnode/wallet_ui.py', 'wartnode/signer.py'):
        if context['runtime_sources'][name] != hashlib.sha256((ROOT/name).read_bytes()).hexdigest():
            raise ValueError('The packaged export source differs from the qualification source')
    environment = {k: v for k, v in os.environ.items() if not k.startswith(('PYTHON', 'QT_'))}
    environment['PATH'] = str(Path(os.environ['SystemRoot'])/'System32')
    spec = importlib.util.spec_from_file_location('frozen_vanity', ROOT/'scripts/test-frozen-vanity.py')
    vanity = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vanity)

    def client(path):
        path.mkdir(exist_ok=True)
        with patch('wartnode.processes.application_command', lambda *a: [str(helper), *a]), \
                patch.dict(os.environ, environment, clear=True):
            return SignerClient(path)

    with tempfile.TemporaryDirectory(prefix='frozen-key-export-', dir=ROOT/'work') as temporary:
        directory = Path(temporary)
        source = client(directory/'source')
        target = client(directory/'target')
        restored = client(directory/'restored')
        secrets = []
        try:
            source.call('setup', password=PASSWORD)
            target.call('setup', password=PASSWORD)
            owners = [source.call('import', kind='private', secret=f'{n:064x}')['address'] for n in (1, 2)]
            generated = source.call('create')
            owners.append(generated['address'])
            # Importing the displayed recovery phrase must identify the same key.
            phrase_profile = client(directory/'phrase')
            try:
                phrase_profile.call('setup', password=PASSWORD)
                phrase_owner = phrase_profile.call('import', kind='phrase', secret=generated['phrase'])['address']
                phrase_key = phrase_profile.call('export_private_key', address=phrase_owner, password=PASSWORD)
                if crypto.wallet_from_pkhex(phrase_key['private_key'])[0] != generated['address']:
                    raise ValueError('Recovery-phrase import/export changed the address')
                phrase_key.clear()
            finally:
                generated.clear()
                phrase_profile.close()
            run = source.call('vanity_prepare', prefix='a', backend='cpu')
            worker = vanity.Pipe([package/'_internal/components/wartnode-vanity.exe'], environment)
            try:
                worker.send(dict(protocol=3, purpose='search', seconds=None, job=run['job'],
                    run_id=run['run_id'], checkpoint=run['checkpoint'], backend='cpu', threads=2))
                deadline = time.monotonic()+45
                while time.monotonic() < deadline:
                    message = worker.receive()
                    if message['event'] == 'progress':
                        source.call('vanity_checkpoint', job_id=run['job_id'], run_id=run['run_id'],
                                    checkpoint=message['data']['checkpoint'])
                    if message['event'] == 'found':
                        source.call('vanity_found', job_id=run['job_id'], run_id=run['run_id'],
                                    checkpoint=message['data']['checkpoint'], candidate=message['data']['candidate'])
                        owners.append(source.call('vanity_finalize', job_id=run['job_id'])['address'])
                        break
                    if message['event'] in ('error', 'paused'):
                        raise ValueError('The packaged vanity fixture did not find an address')
                else:
                    raise ValueError('The packaged vanity fixture exceeded its time budget')
            finally:
                worker.close()
            selected = source.call('list')['address']
            for address in owners:
                rejected(lambda: source.call('export_private_key', address=address, password='wrong-password'))
                exported = source.call('export_private_key', address=address, password=PASSWORD)
                secret = exported.pop('private_key')
                if len(secret) != 64 or crypto.wallet_from_pkhex(secret)[0] != address:
                    raise ValueError('An exported key did not identify the requested address')
                if target.call('import', kind='private', secret=secret)['address'] != address:
                    raise ValueError('A destination profile imported a different address')
                secrets.append(secret)
                exported.clear()
            if source.call('list')['address'] != selected:
                raise ValueError('Export changed the selected sending address')
            foreign, _ = crypto.wallet_from_pkhex(f'{3:064x}')
            rejected(lambda: source.call('export_private_key', address=foreign, password=PASSWORD))
            rejected(lambda: source.call('export_private_key', address=owners[0]))
            source.call('lock')
            rejected(lambda: source.call('export_private_key', address=owners[0], password=PASSWORD))
            source.call('unlock', password=PASSWORD)
            backup = directory/'keys.wartprofile'
            source.call('backup', path=str(backup), password=PASSWORD)
            restored.call('restore', path=str(backup), password=PASSWORD)
            restored.call('unlock', password=PASSWORD)
            for address, secret in zip(owners, secrets):
                result = restored.call('export_private_key', address=address, password=PASSWORD)
                if result.pop('private_key') != secret:
                    raise ValueError('Backup/restore changed an exported key')
                result.clear()
            public = json.dumps(source.call('list')).encode()
            source.call('change_password', old_password=PASSWORD, new_password=PASSWORD+'-changed')
            source.call('unlock', password=PASSWORD+'-changed')
            rejected(lambda: source.call('export_private_key', address=owners[0], password=PASSWORD))
            result = source.call('export_private_key', address=owners[0], password=PASSWORD+'-changed')
            result.clear()
            for path in (directory/'source/wallet.db', backup):
                raw = path.read_bytes()
                if any(secret.encode() in raw or secret.encode() in public for secret in secrets):
                    raise ValueError('Plaintext key found outside an explicit export response')
        finally:
            secrets.clear()
            source.close()
            target.close()
            restored.close()
    result = dict(all_passed=True, application=__version__, source_commit=context['source_commit'],
        helper_sha256=hashlib.sha256(helper.read_bytes()).hexdigest(), exported_addresses=4,
        imported_generated_mnemonic_and_vanity=True, destination_import_round_trip=True,
        locked_wrong_password_missing_password_and_foreign_address_rejected=True,
        password_change=True, encrypted_backup_restore=True, public_and_disk_plaintext_check=True,
        python_on_helper_path=False, live_chain_submission=False, funds_used=False)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
