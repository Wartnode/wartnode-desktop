"""Verify the packaged GUI, private signer, public worker and encrypted backup."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wartnode.processes import hidden_options
from wartnode.vanity_protocol import ProtocolState
from wartnode.vanity_rates import Samples, PUBLIC_POINT

PASSWORD = 'public-synthetic-frozen-vanity-password'


class Pipe:
    def __init__(self, arguments, environment):
        self.process = subprocess.Popen([str(a) for a in arguments], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, **hidden_options())
        self.lines = queue.Queue()
        def read():
            for line in iter(self.process.stdout.readline, b''):
                self.lines.put(line)
            self.lines.put(None)
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()
        self.counter = 0

    def send(self, value):
        self.process.stdin.write((json.dumps(value)+'\n').encode())
        self.process.stdin.flush()

    def receive(self, seconds=40):
        line = self.lines.get(timeout=seconds)
        if line is None:
            details = self.process.stderr.read(4000).decode('utf-8', errors='replace')
            raise ValueError('Packaged process closed without returning a result: '+details)
        return json.loads(line)

    def call(self, action, **args):
        self.counter += 1
        self.send(dict(id=self.counter, action=action, args=args))
        response = self.receive()
        if response.get('id') != self.counter:
            raise ValueError('Packaged signer protocol mismatch')
        if 'error' in response:
            raise ValueError(response['error'])
        return response['result']

    def close(self):
        if self.process.poll() is None:
            try:
                self.process.stdin.close()
                self.process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                self.process.kill(); self.process.wait(timeout=5)
        self.reader.join(timeout=3)
        self.process.stdout.close()
        self.process.stderr.close()
        if not self.process.stdin.closed:
            self.process.stdin.close()


def pause_at_checkpoint(signer, package, environment, run):
    worker = Pipe([package/'_internal/components/wartnode-vanity.exe'], environment)
    try:
        worker.send(dict(protocol=3, purpose='search', seconds=None, job=run['job'], run_id=run['run_id'],
                         checkpoint=run['checkpoint'], backend='cpu', threads=2))
        requested = False
        deadline = time.monotonic()+45
        while time.monotonic() < deadline:
            message = worker.receive()
            kind, data = message['event'], message['data']
            args = dict(job_id=run['job_id'], run_id=run['run_id'])
            if kind == 'progress':
                signer.call('vanity_checkpoint', checkpoint=data['checkpoint'], **args)
                if int(data['checkpoint']['next'], 16) > int(run['checkpoint']['next'], 16) and not requested:
                    worker.send(dict(command='pause'))
                    requested = True
            elif kind == 'paused':
                return signer.call('vanity_pause', checkpoint=data['checkpoint'], **args)['record']
            elif kind in ('found','error'):
                raise ValueError('Unexpected long-search result: '+str(message))
        raise ValueError('Packaged search did not checkpoint and pause')
    finally:
        worker.close()


def legacy_round_trip(package, legacy, directory, environment):
    """Use the actual frozen format-1 helper to produce the upgrade fixtures."""
    original = directory/'legacy-live'
    original.mkdir()
    backup = directory/'legacy.wartprofile'
    old = Pipe([legacy/'WartNodeHelper.exe', '--signer', original], environment)
    try:
        old.call('setup', password=PASSWORD)
        address = old.call('import', kind='private', secret='00'*31+'09', label='Legacy fixture')['address']
        old.call('backup', path=str(backup), password=PASSWORD)
    finally:
        old.close()
    if not backup.read_bytes().startswith(b'WARTNODE-PROFILE\x01'):
        raise ValueError('The legacy fixture is not a format-1 backup')
    for target, restore in ((original, False), (directory/'legacy-restored', True)):
        target.mkdir(exist_ok=True)
        upgraded = Pipe([package/'WartNodeHelper.exe', '--signer', target], environment)
        try:
            if restore:
                upgraded.call('restore', path=str(backup), password=PASSWORD)
            upgraded.call('unlock', password=PASSWORD)
            if [row['address'] for row in upgraded.call('list')['wallets']] != [address]:
                raise ValueError('The packaged migration lost the legacy wallet')
        finally:
            upgraded.close()
    before = hashlib.sha256((original/'wallet.db').read_bytes()).hexdigest()
    rejected = Pipe([legacy/'WartNodeHelper.exe', '--signer', original], environment)
    try:
        try:
            rejected.call('list')
        except ValueError as error:
            if 'Unsupported or damaged profile format' not in str(error):
                raise
        else:
            raise ValueError('The legacy binary accepted the incompatible profile')
    finally:
        rejected.close()
    if hashlib.sha256((original/'wallet.db').read_bytes()).hexdigest() != before:
        raise ValueError('The refused downgrade modified the migrated database')


def calibration(package, environment):
    ident = str(uuid.uuid4())
    job = dict(schema=3, session_id=ident, public_key=PUBLIC_POINT, strategy='endomorphism-v1',
        pattern=dict(profile=dict(chain='warthog',network='mainnet',address_type='account'),prefix='0'*16,suffix=''))
    run = dict(job_id=ident,run_id=str(uuid.uuid4()),job=job,
               checkpoint=dict(next='0'*64,evaluated='0'*64,elapsed_seconds=0.0))
    worker = Pipe([package/'_internal/components/wartnode-vanity.exe'],environment)
    parser, samples = ProtocolState(run,'calibration'), Samples()
    try:
        worker.send(dict(protocol=3,purpose='calibration',seconds=8,job=job,run_id=run['run_id'],
                         checkpoint=run['checkpoint'],backend='cpu',threads=2))
        while True:
            message = worker.receive()
            kind, data = parser.consume((json.dumps(message)+'\n').encode())
            if kind in ('progress','calibrated'):
                samples.add(data['search_seconds'],int(data['candidates_exact']))
            if kind == 'calibrated':
                if samples.summary() is None:
                    raise ValueError('Packaged calibration did not produce enough samples')
                return samples.summary()
            if kind in ('error','found','paused'):
                raise ValueError('Packaged calibration stopped early')
    finally:
        worker.close()


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--package-dir', type=Path, default=ROOT/'dist/vanity-ui-cleanup/WartNode')
    parser.add_argument('--legacy-package-dir', type=Path)
    options = parser.parse_args()
    package = options.package_dir.resolve()
    with tempfile.TemporaryDirectory(prefix='frozen-vanity-', dir=ROOT/'work') as temporary:
        directory = Path(temporary)
        (directory/'runtime').mkdir()
        environment = dict(os.environ, TEMP=str(directory/'runtime'), TMP=str(directory/'runtime'), QT_QPA_PLATFORM='offscreen')
        signer = Pipe([package/'WartNodeHelper.exe', '--signer', directory], environment)
        owners = []
        try:
            signer.call('setup', password=PASSWORD)
            for prefix, suffix in [('ab', ''), ('', 'ab'), ('a', 'b')]:
                run = signer.call('vanity_prepare', prefix=prefix, suffix=suffix)
                job = run['job']
                worker = Pipe([package/'_internal/components/wartnode-vanity.exe'], environment)
                try:
                    worker.send(dict(protocol=3, purpose='search', seconds=None, job=job, run_id=run['run_id'],
                                     checkpoint=run['checkpoint'], backend='cpu', threads=2))
                    while True:
                        message = worker.receive()
                        if message['event'] == 'progress':
                            signer.call('vanity_checkpoint', job_id=run['job_id'], run_id=run['run_id'],
                                        checkpoint=message['data']['checkpoint'])
                        if message['event'] in ('found', 'error', 'paused'):
                            break
                    if message['event'] != 'found':
                        raise ValueError('Packaged worker failed: '+str(message['data']))
                    signer.call('vanity_found', job_id=run['job_id'], run_id=run['run_id'],
                                checkpoint=message['data']['checkpoint'], candidate=message['data']['candidate'])
                    result = signer.call('vanity_finalize', job_id=job['session_id'],
                                         candidate=message['data']['candidate'], label='Synthetic vanity')
                    address = result['address']
                    if not address.startswith(prefix) or not address.endswith(suffix):
                        raise ValueError('Packaged address does not match its pattern')
                    owners.append(address)
                finally:
                    worker.close()
            run = signer.call('vanity_prepare', prefix='0'*16, backend='cpu')
            paused = pause_at_checkpoint(signer, package, environment, run)
            signer.close()
            signer = Pipe([package/'WartNodeHelper.exe', '--signer', directory], environment)
            if signer.call('vanity_status')['record'] != paused:
                raise ValueError('Packaged restart did not preserve the paused search')
            signer.call('unlock', password=PASSWORD)
            run = signer.call('vanity_resume', job_id=run['job_id'], backend='cpu')
            resumed = pause_at_checkpoint(signer, package, environment, run)
            if int(resumed['checkpoint']['next'], 16) <= int(paused['checkpoint']['next'], 16):
                raise ValueError('Packaged manual resume did not advance')
            calibration(package,environment)
            if signer.call('vanity_status')['record'] != resumed:
                raise ValueError('Packaged calibration changed the pending job')
            backup = directory/'synthetic.wartprofile'
            signer.call('backup', path=str(backup), password=PASSWORD)
            restored_directory = directory/'restored'
            restored_directory.mkdir()
            restored = Pipe([package/'WartNodeHelper.exe', '--signer', restored_directory], environment)
            try:
                restored.call('restore', path=str(backup), password=PASSWORD)
                restored.call('unlock', password=PASSWORD)
                if {row['address'] for row in restored.call('list')['wallets']} != set(owners):
                    raise ValueError('Packaged backup did not restore every vanity address')
                if restored.call('vanity_status')['record'] != resumed:
                    raise ValueError('Packaged backup did not restore the pending search')
            finally:
                restored.close()
        finally:
            signer.close()
        if options.legacy_package_dir:
            legacy_round_trip(package, options.legacy_package_dir.resolve(), directory, environment)
        gui = subprocess.run([str(package/'WartNode.exe'), '--data-dir', str(directory),
                              '--no-node', '--smoke-seconds', '2'], env=environment,
                             capture_output=True, timeout=40, **hidden_options())
        if gui.returncode:
            raise ValueError('Packaged GUI smoke test failed: '+gui.stderr.decode('utf-8', errors='replace')[:2000])
    print(json.dumps(dict(all_passed=True, cpu_search_modes=3, encrypted_backup_restore=True,
                          manual_resume_after_restart=True, pending_search_backup_restore=True,
                          public_calibration=True,
                          legacy_upgrade_and_downgrade_refusal=bool(options.legacy_package_dir),
                          packaged_gui_startup_shutdown=True, transactions_broadcast=0)))


if __name__ == '__main__':
    main()
