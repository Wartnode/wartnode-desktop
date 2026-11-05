"""Materialize only the hash-locked source archives committed with this checkout."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[2]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def confined(root, name):
    part = PurePosixPath(name)
    if part.is_absolute() or '..' in part.parts or '\\' in name or ':' in name:
        raise ValueError(f'Unsafe source path: {name}')
    result = (root / name).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError(f'Source path escapes destination: {name}')
    return result


def materialize(data, destination):
    """Check every path before writing; materialize in-archive symlinks as files."""
    with tarfile.open(fileobj=io.BytesIO(data)) as source:
        outputs = {}
        for member in source.getmembers():
            path = confined(destination, member.name)
            if member.isdir():
                continue
            if not (member.isfile() or member.issym()):
                raise ValueError(f'Unsupported archive entry: {member.name}')
            if member.issym():
                confined(destination, str(PurePosixPath(member.name).parent / member.linkname))
            if path in outputs:
                raise ValueError(f'Duplicate archive path: {member.name}')
            outputs[path] = source.extractfile(member).read()
        for path, payload in outputs.items():
            if path.exists() and path.read_bytes() != payload:
                raise ValueError(f'Refusing to replace changed source: {path}')
        for path, payload in outputs.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
    return len(outputs)


def bootstrap(root):
    lock_path = root / 'scripts/ci/sources.lock.json'
    lock = json.loads(lock_path.read_text())
    if lock['schema'] != 1:
        raise ValueError('Unsupported source lock schema')
    # Verify all inputs before materializing any source.
    inputs = []
    for item in lock['sources']:
        compressed = confined(root, item['archive']).read_bytes()
        if digest(compressed) != item['sha256']:
            raise ValueError('Source archive hash mismatch: ' + item['name'])
        data = gzip.decompress(compressed)
        if digest(data) != item['tar_sha256']:
            raise ValueError('Source tar hash mismatch: ' + item['name'])
        inputs.append((item, data))
    for patch in lock['patches']:
        if digest(confined(root, patch['file']).read_bytes()) != patch['sha256']:
            raise ValueError('Approved patch hash mismatch')
    receipts = []
    for item, data in inputs:
        count = materialize(data, confined(root, item['directory']))
        target = confined(root, item['tar_output'])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        receipts.append({**item, 'files_verified': count})
    for folder in ('evidence', 'provenance', 'components'):
        (root / folder).mkdir(exist_ok=True)
    subprocess.run([sys.executable, str(root / 'scripts/apply-upstream-shutdown.py')], cwd=root, check=True)
    result = {'schema': 1, 'all_passed': True, 'source_lock_sha256': digest(lock_path.read_bytes()),
              'sources': receipts, 'patches': lock['patches'], 'prebuilt_libraries_used': False}
    (root / 'work/ci').mkdir(parents=True, exist_ok=True)
    (root / 'work/ci/bootstrap.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    print(json.dumps(bootstrap(args.root.resolve()), indent=2))
