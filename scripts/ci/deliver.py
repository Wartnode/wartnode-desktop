"""Create source/checksum/build receipts for an already tested development bundle."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from wartnode import __version__


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def valid_version(version):
    return re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(dev|beta|rc)\.[1-9]\d*)?', version) is not None


def deliver(root):
    if not valid_version(__version__):
        raise ValueError('Invalid application release version')
    checks = json.loads((root / 'evidence/ci/checks.json').read_text())
    if not checks['all_passed']:
        raise ValueError('Required development checks did not pass')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    changed = subprocess.check_output(['git', 'diff', '--name-only', 'HEAD'], cwd=root, text=True).splitlines()
    untracked = subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard'], cwd=root, text=True).splitlines()
    unexpected = [name for name in changed + untracked if not name.startswith(('provenance/', 'evidence/', 'notices/'))
                  and name != 'components/manifest.json']
    if unexpected:
        raise ValueError('Commit the source changes before delivery: ' + ', '.join(unexpected))
    manifest = json.loads((root / 'components/manifest.json').read_text())
    if manifest['application'] != __version__:
        raise ValueError('Application/component version mismatch')
    archive = root / f'dist/Wartnode-Desktop-{__version__}-windows-x64.zip'
    installer = root / f'dist/Wartnode-Desktop-{__version__}-windows-x64-setup.exe'
    if sha(installer) != installer.with_suffix('.exe.sha256').read_text().split()[0]:
        raise ValueError('Installer checksum mismatch')
    installed_checks=json.loads((root/'evidence/ci/installed-product.json').read_text())
    if not installed_checks['all_passed'] or installed_checks['application']!=__version__:
        raise ValueError('Installed application checks did not pass for this version')
    if installed_checks.get('candidate_installer_sha256')!=sha(installer):
        raise ValueError('The installer differs from the qualified installer')
    if sha(archive) != archive.with_suffix('.zip.sha256').read_text().split()[0]:
        raise ValueError('Windows ZIP checksum mismatch')
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None:
            raise ValueError('Windows ZIP CRC failure')
        if json.loads(z.read('WartNode/_internal/components/manifest.json')) != manifest:
            raise ValueError('Packaged manifest mismatch')
        context=json.loads(z.read('WartNode/provenance/build-context.json'))
        if context.get('source_commit')!=commit or context.get('application')!=__version__:
            raise ValueError('Package the exact committed source before delivery')
        for name,digest in context['runtime_sources'].items():
            if sha(root/name)!=digest:raise ValueError('Runtime source changed after packaging: '+name)
    build = {'schema': 1, 'application': __version__, 'commit': commit,
             'created_utc': datetime.now(timezone.utc).isoformat(), 'release_target': 'Windows 11 x64',
             'qualification_host': platform.platform(), 'python': sys.version,
             'github_run_id': os.environ.get('GITHUB_RUN_ID'), 'github_run_attempt': os.environ.get('GITHUB_RUN_ATTEMPT'),
             'workflow_sha256': sha(root / '.github/workflows/build.yml'),
             'source_lock_sha256': sha(root / 'scripts/ci/sources.lock.json'),
             'python_lock_sha256': sha(root / 'scripts/ci/requirements.lock'),
             'toolchains_lock_sha256': sha(root / 'scripts/ci/toolchains.lock.json'),
             'manifest': manifest, 'checks': checks, 'windows_sha256': sha(archive),
             'installer_sha256':sha(installer),'installed_product':installed_checks,
             'source_build': True, 'bit_reproducible_claimed': False,
             'clean_windows11_certified': False, 'production_release': False}
    (root / 'provenance/build.json').write_text(json.dumps(build, indent=2) + '\n')
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
    sources = {name: root / name for name in tracked if name}
    # Replace historical/generated receipts rather than adding duplicate ZIP names.
    for folder in ('notices',):
        sources.update({p.relative_to(root).as_posix(): p for p in (root / folder).rglob('*') if p.is_file()})
    sources['components/manifest.json'] = root / 'components/manifest.json'
    source_zip = root / f'dist/Wartnode-Desktop-{__version__}-source.zip'
    hashes = {}
    with zipfile.ZipFile(source_zip, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, path in sorted(sources.items()):
            if name.startswith(('.git/', 'work/', 'dist/')) or path.suffix.lower() in ('.db', '.db3', '.wartbackup', '.exe', '.lib', '.obj', '.key', '.pem', '.dpapi', '.wartkeys', '.pfx', '.p12'):
                raise ValueError('Disallowed source payload: ' + name)
            if name.startswith(('evidence/', 'provenance/', 'work/', 'dist/')):
                raise ValueError('Collected test data is not public source: ' + name)
            z.write(path, 'WartNode-source/' + name)
            hashes[name] = sha(path)
        public_build = {key:build[key] for key in ('schema','application','commit','source_lock_sha256','python_lock_sha256','toolchains_lock_sha256','windows_sha256','installer_sha256','source_build')}
        public_build['checks'] = {'all_passed':True}
        z.writestr('WartNode-source/provenance/build.json', json.dumps(public_build,indent=2)+'\n')
    artifacts = {archive.name: sha(archive), source_zip.name: sha(source_zip), installer.name: sha(installer)}
    for name, value in artifacts.items():
        (root / 'dist' / (name + '.sha256')).write_text(value + '  ' + name + '\n', encoding='ascii')
    (root / 'dist/SHA256SUMS').write_text(''.join(value + '  ' + name + '\n' for name, value in artifacts.items()), encoding='ascii')
    with zipfile.ZipFile(source_zip) as z:
        if z.testzip() is not None or len(z.namelist()) != len(set(z.namelist())):
            raise ValueError('Invalid source ZIP')
        for name, value in hashes.items():
            if hashlib.sha256(z.read('WartNode-source/' + name)).hexdigest() != value:
                raise ValueError('Source ZIP hash mismatch: ' + name)
    result = {'all_passed': True, 'application': __version__, 'commit': commit,
              'artifacts': artifacts, 'source_files_verified': len(hashes),
              'production_release': False, 'clean_windows11_certified': False}
    (root / 'dist/delivery.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    deliver(ROOT)
