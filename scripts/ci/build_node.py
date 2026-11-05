"""Build pinned official core with locked shutdown patches and owned Windows resources."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.ci.core_inputs import verify
from scripts.ci.core_overlay import prepare


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


if __name__ == '__main__':
    item, patches, originals, patched = verify(ROOT)
    (ROOT / 'work/ci').mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='core-build-', dir=ROOT / 'work/ci') as temporary:
        source = Path(temporary) / 'source'
        shutil.copytree(ROOT / item['directory'], source)
        prepare(ROOT, source, originals)
        subprocess.run(['docker', 'build', '--file', str(ROOT / 'scripts/core.Dockerfile'), '--build-arg',
                        'BUILD_JOBS=' + os.environ.get('WARTNODE_BUILD_JOBS', '2'),
                        '--output', 'type=local,dest=work/upstream-node-build', str(source)], cwd=ROOT, check=True)
    output = ROOT / 'work/upstream-node-build'
    overlay_files = ['native/core_shutdown_regression.cpp', 'native/core.manifest', 'native/core.rc',
                     'scripts/ci/core_overlay.py', 'scripts/ci/core_inputs.py']
    record = {'schema': 1, 'source': item, 'source_verified': True, 'patches': patches,
              'source_lock_sha256': sha(ROOT / 'scripts/ci/sources.lock.json'),
              'recipe_sha256': sha(ROOT / 'scripts/core.Dockerfile'),
              'build_overlay': {name: sha(ROOT / name) for name in overlay_files},
              'node_sha256': sha(output / 'wart-node-windows.exe'),
              'toolchain': (output / 'toolchain.txt').read_text(),
              'test_binaries': {p.name: sha(p) for p in sorted((output / 'tests').glob('*.exe'))}}
    (ROOT / 'provenance/node-build.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))
