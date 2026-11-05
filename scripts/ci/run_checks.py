"""Run shared CI/local checks; never treat absent GPU qualification as a pass."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from wartnode.processes import hidden_options


def run(name, command):
    path = ROOT / 'evidence/ci' / (name + '.log')
    path.parent.mkdir(parents=True, exist_ok=True)
    print('Running ' + name, flush=True)
    with path.open('w', encoding='utf-8') as output:
        result = subprocess.run(command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
                                timeout=900, **hidden_options())
    text = path.read_text(encoding='utf-8', errors='replace')
    print('\n'.join(text.splitlines()[-8:]), flush=True)
    if result.returncode:
        raise RuntimeError(f'{name} failed with code {result.returncode}; see {path}')
    tests = re.search(r'Ran (\d+) tests? in ', text)
    return {'exit_code': 0, 'log': path.relative_to(ROOT).as_posix(),
            'tests': int(tests.group(1)) if tests else None}


if __name__ == '__main__':
    powershell = shutil.which('pwsh')
    if not powershell:
        raise RuntimeError('PowerShell 7 (pwsh) is required for development packaging')
    results = {}
    for name, command in (
        ('native-runtime', [sys.executable, 'scripts/audit-runtime.py']),
        ('application-tests', [sys.executable, 'scripts/run-tests.py']),
        ('wallet-tests', [sys.executable, 'scripts/run-tests.py', '--legacy']),
        ('node-native-tests', [sys.executable, 'scripts/test-native.py']),
        ('dashboard-layout', [sys.executable, 'scripts/qualify-dashboard-ui.py']),
        ('package', [powershell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                     'scripts/package.ps1', '-PythonExecutable', sys.executable]),
        ('extract', [sys.executable, 'scripts/extract-package.py']),
    ):
        results[name] = run(name, command)
    extracted = json.loads((ROOT / 'evidence/extracted-package.json').read_text())
    results['frozen-recovery'] = run('frozen-recovery', [sys.executable, 'scripts/smoke-package.py',
                                                       '--package-dir', extracted['directory']])
    results['frozen-payments'] = run('frozen-payments', [sys.executable, 'scripts/test-frozen-payments.py'])
    results['installer'] = run('installer', [powershell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                                           'scripts/build-installer.ps1', '-PythonExecutable', sys.executable])
    results['upgrade-fixture'] = run('upgrade-fixture', [sys.executable, 'scripts/build-upgrade-fixture.py'])
    results['installed-product'] = run('installed-product', [sys.executable, 'scripts/qualify-installed-product.py'])
    results['custom-storage-node'] = run('custom-storage-node', [sys.executable, 'scripts/qualify-storage-node.py'])
    record = {'all_passed': True, 'checks': results,
              'hardware_checks': {'status': 'not_run', 'reason': 'Physical GPU and clean Windows 11 qualification are separate release gates'},
              'additional_block_discovery_required': False}
    (ROOT / 'evidence/ci/checks.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))
