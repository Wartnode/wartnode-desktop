"""Refuse packaging with runtimes older than the reviewed native TLS inputs."""
import json
from pathlib import Path
import platform
import ssl
import sqlite3
from cryptography.hazmat.backends.openssl.backend import backend
import cryptography

ROOT = Path(__file__).resolve().parents[1]


def audit():
    lock = json.loads((ROOT / 'scripts/ci/toolchains.lock.json').read_text())
    observed = {'python': platform.python_version(), 'python_openssl': ssl.OPENSSL_VERSION,
                'cryptography': cryptography.__version__,
                'cryptography_openssl': backend.openssl_version_text(),
                'sqlite': sqlite3.sqlite_version}
    expected = {'python': lock['python'], **lock['reviewed_native_runtime']}
    for name, value in expected.items():
        if observed[name] != value:
            raise ValueError(f'Unreviewed native runtime {name}: {observed[name]!r}; expected {value!r}')
    return {'all_passed': True, 'observed': observed, 'expected': expected,
            'scope': 'Exact reviewed runtime versions; advisory applicability is recorded separately.'}


if __name__ == '__main__':
    record = audit()
    output = ROOT / 'evidence/ci/native-runtime.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))
