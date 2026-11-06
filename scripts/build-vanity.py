"""Build the prototype's public-only Rust worker from pinned Vanity Pro source."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wartnode import __version__
REVISION = 'c4f00f05afc2232ec82a9e6511a2333c187d0176'
SOURCE_SHA256 = '80d3377d73651b137e818fc37d45ed494d9e191c6f2362b611025852fd759244'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--cargo', default='cargo')
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--generate-lockfile', action='store_true')
    parser.add_argument('--checks', action='store_true', help='Require rustfmt, tests and strict Clippy')
    args = parser.parse_args()
    archive = ROOT/'vendor/sources/vanity-pro.tar.gz'
    if digest(archive) != SOURCE_SHA256:
        raise ValueError('Vanity Pro source archive failed verification')
    source = ROOT/'vendor/vanity-pro'
    stamp = source/'.wartnode-source'
    if not stamp.exists():
        if source.exists():
            raise ValueError('Unrecognized vendor/vanity-pro directory; preserve it and choose a fresh checkout')
        source.mkdir()
        with tarfile.open(archive, 'r:gz') as bundle:
            for member in bundle.getmembers():
                parts = PurePosixPath(member.name).parts
                if len(parts) < 2:
                    continue
                relative = PurePosixPath(*parts[1:])
                if relative.is_absolute() or '..' in relative.parts or not (member.isfile() or member.isdir()):
                    raise ValueError('Unsafe source archive member')
                target = source.joinpath(*relative.parts)
                if not target.resolve().is_relative_to(source.resolve()):
                    raise ValueError('Source archive escapes its directory')
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.extractfile(member) as data:
                        target.write_bytes(data.read())
        stamp.write_text(SOURCE_SHA256, encoding='ascii')
    if stamp.read_text(encoding='ascii') != SOURCE_SHA256:
        raise ValueError('Unexpected Vanity Pro source identity')
    with tarfile.open(archive, 'r:gz') as bundle:
        for member in bundle.getmembers():
            if member.isfile():
                relative = PurePosixPath(*PurePosixPath(member.name).parts[1:])
                if source.joinpath(*relative.parts).read_bytes() != bundle.extractfile(member).read():
                    raise ValueError('Materialized Vanity Pro source changed: '+str(relative))
    crate = ROOT/'native/vanity-worker'
    flags = ['--offline'] if args.offline else []
    if args.generate_lockfile:
        subprocess.run([args.cargo, 'generate-lockfile', *flags], cwd=crate, check=True)
    if args.checks:
        subprocess.run([args.cargo, 'fmt', '--', '--check'], cwd=crate, check=True)
        for operation in ('test', 'clippy'):
            command = [args.cargo, operation, '--all-targets', '--locked', *flags,
                       '--target-dir', str(ROOT/'work/vanity-target')]
            if operation == 'clippy':
                command += ['--', '-D', 'warnings']
            subprocess.run(command, cwd=crate, check=True)
    subprocess.run([args.cargo, 'build', '--release', '--locked', *flags,
                    '--target-dir', str(ROOT/'work/vanity-target')], cwd=crate, check=True)
    name = 'wartnode-vanity.exe' if os.name == 'nt' else 'wartnode-vanity'
    target = ROOT/'components'/name
    shutil.copy2(ROOT/'work/vanity-target/release'/name, target)
    entry = {'file': name, 'sha256': digest(target), 'protocol': 3,
             'repository': 'https://github.com/VanityProject/vanity-pro',
             'commit': REVISION, 'source_archive_sha256': SOURCE_SHA256,
             'adapter_sources': {p.relative_to(ROOT).as_posix():digest(p) for p in
                (crate/'Cargo.toml', crate/'Cargo.lock', crate/'src/main.rs', crate/'.cargo/config.toml')}}
    manifest_path = ROOT/'components/manifest.json'
    manifest = (json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists()
                else dict(schema=1, network='mainnet', application=__version__, components={}))
    manifest['application'] = __version__
    manifest['components']['vanity'] = entry
    manifest_path.write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    (ROOT/'vendor/sources/vanity-build.json').write_text(json.dumps(entry, indent=2)+'\n', encoding='utf-8')
    shutil.copy2(source/'LICENSE', ROOT/'notices/Vanity-Pro-MIT.txt')
    notices = ROOT/'notices/vanity-pro'
    shutil.copytree(source/'licenses', notices, dirs_exist_ok=True)
    subprocess.run([sys.executable, str(ROOT/'scripts/collect-vanity-notices.py'),
                    '--cargo', args.cargo, *flags], cwd=ROOT, check=True)
    print('Built and verified '+str(target))


if __name__ == '__main__':
    main()
