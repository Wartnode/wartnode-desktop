"""Retain dependency metadata and upstream notice files for the locked worker."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--cargo', default='cargo')
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    flags = ['--offline'] if args.offline else []
    metadata = json.loads(subprocess.check_output([args.cargo, 'metadata', '--locked',
        '--format-version', '1', '--filter-platform', 'x86_64-pc-windows-msvc', *flags],
        cwd=ROOT/'native/vanity-worker'))
    reachable = {node['id'] for node in metadata['resolve']['nodes']}
    packages = [p for p in metadata['packages'] if p['id'] in reachable]
    destination = ROOT/'notices/vanity-worker-dependencies'
    destination.mkdir(parents=True, exist_ok=True)
    records = []
    for package in sorted(packages, key=lambda p:(p['name'], p['version'])):
        name = package['name']+'-'+package['version']
        if not re.fullmatch(r'[A-Za-z0-9_.+\-]+', name):
            raise ValueError('Unsafe dependency notice name')
        source = Path(package['manifest_path']).parent
        output = destination/name
        output.mkdir(exist_ok=True)
        files = [p for p in source.rglob('*') if p.is_file() and len(p.relative_to(source).parts) <= 3
                 and p.name.upper().startswith(('LICENSE', 'LICENCE', 'COPYING', 'NOTICE', 'UNLICENSE', 'COPYRIGHT'))
                 and not p.is_symlink()]
        if package['name'] == 'wartnode-vanity':
            files.append(ROOT/'LICENSE')
        retained = {}
        for path in sorted(set(files)):
            relative = path.relative_to(source) if path.is_relative_to(source) else Path('LICENSE')
            target = output/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            data = path.read_bytes()
            target.write_bytes(data)
            retained[relative.as_posix()] = hashlib.sha256(data).hexdigest()
        # Some crates ship only an SPDX declaration and a README with attribution.
        # Preserve that upstream evidence rather than inventing a copyright notice.
        if not files:
            for path in sorted(source.glob('*')):
                if path.is_file() and path.name.upper().startswith('README'):
                    data = path.read_bytes(); (output/path.name).write_bytes(data)
                    retained[path.name] = hashlib.sha256(data).hexdigest()
        if not package.get('license') and not package.get('license_file'):
            raise ValueError('Missing upstream license declaration: '+name)
        records.append(dict(name=package['name'], version=package['version'],
            license=package.get('license'), authors=package['authors'], repository=package.get('repository'),
            source=package.get('source') or 'reviewed local source', files=retained,
            upstream_notice_files=bool(files)))
    record = dict(schema=1, target='x86_64-pc-windows-msvc',
        cargo_lock_sha256=hashlib.sha256((ROOT/'native/vanity-worker/Cargo.lock').read_bytes()).hexdigest(),
        scope='Cargo resolved target graph, including build inputs; not a claim that every crate is linked',
        packages=records)
    (destination/'inventory.json').write_text(json.dumps(record, indent=2)+'\n', encoding='utf-8')
    print('Retained worker notices and upstream declarations for '+str(len(records))+' locked packages')


if __name__ == '__main__':
    main()
