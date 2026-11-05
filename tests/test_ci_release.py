"""Exercise source-ingress failures and the release version boundary."""
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import subprocess
import unittest

from scripts.ci.bootstrap import bootstrap, materialize
from scripts.ci.deliver import valid_version
from scripts.ci.extract_toolchain import extract_toolchain


def archive(entries):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as source:
        for name, data in entries:
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            source.addfile(entry, io.BytesIO(data))
    return output.getvalue()


class SourceIngress(unittest.TestCase):
    def test_all_paths_validated_before_any_source_write(self):
        for unsafe in ('../outside.txt', '/absolute.txt', 'C:/outside.txt', 'dir\\outside.txt'):
            with self.subTest(path=unsafe), tempfile.TemporaryDirectory() as name:
                destination = Path(name) / 'source'
                with self.assertRaises(ValueError):
                    materialize(archive([('valid.txt', b'content'), (unsafe, b'bad')]), destination)
                self.assertFalse(destination.exists())

    def test_changed_local_input_is_preserved(self):
        with tempfile.TemporaryDirectory() as name:
            destination = Path(name)
            changed = destination / 'source.cpp'
            changed.write_bytes(b'local change')
            with self.assertRaises(ValueError):
                materialize(archive([('source.cpp', b'upstream')]), destination)
            self.assertEqual(changed.read_bytes(), b'local change')

    def test_duplicate_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            with self.assertRaises(ValueError):
                materialize(archive([('same.cpp', b'first'), ('same.cpp', b'second')]), Path(name))

    def test_exact_sources_materialize_without_host_line_ending_changes(self):
        with tempfile.TemporaryDirectory() as name:
            destination = Path(name)
            payload = b'first\r\nsecond\n'
            self.assertEqual(materialize(archive([('nested/source.cpp', payload)]), destination), 1)
            self.assertEqual((destination / 'nested/source.cpp').read_bytes(), payload)

    def test_corrupt_archive_fails_before_source_materialization(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / 'scripts/ci').mkdir(parents=True)
            (root / 'input.gz').write_bytes(gzip.compress(b'corrupted'))
            locked = {'schema': 1, 'patches': [], 'sources': [{'name': 'test', 'archive': 'input.gz',
                'sha256': hashlib.sha256(b'expected').hexdigest(), 'tar_sha256': '0' * 64,
                'directory': 'vendor/test', 'tar_output': 'work/test.tar'}]}
            (root / 'scripts/ci/sources.lock.json').write_text(json.dumps(locked))
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                bootstrap(root)
            self.assertFalse((root / 'vendor').exists())


class ReleaseVersions(unittest.TestCase):
    def test_application_version_is_independent_of_windows_release_names(self):
        for version in ('0.2.2-dev.1', '0.3.0-beta.2', '1.0.0-rc.1', '1.0.0'):
            self.assertTrue(valid_version(version), version)
        for version in ('24H2', 'Windows11', '1.0', '01.0.0', '1.0.0-dev.0', '1.0.0-latest', '../1.0.0'):
            self.assertFalse(valid_version(version), version)


class CrossPlatformBuildInputs(unittest.TestCase):
    def test_git_checkout_preserves_identical_node_recipe_bytes(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            repo = root / 'repo'
            repo.mkdir()
            (repo / 'scripts').mkdir()
            (repo / '.gitattributes').write_bytes((project / '.gitattributes').read_bytes())
            expected = (project / 'scripts/core.Dockerfile').read_bytes().replace(b'\r\n', b'\n')
            (repo / 'scripts/core.Dockerfile').write_bytes(expected)
            def git(*args):
                return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)
            git('init', '--quiet')
            git('-c', 'core.autocrlf=false', 'add', '.gitattributes', 'scripts/core.Dockerfile')
            for setting in ('false', 'true'):
                destination = root / ('checkout-' + setting)
                destination.mkdir()
                git('-c', 'core.autocrlf=' + setting, 'checkout-index', '--all', '--prefix=' + destination.as_posix() + '/')
                self.assertEqual((destination / 'scripts/core.Dockerfile').read_bytes(), expected)


class ToolchainExtraction(unittest.TestCase):
    def test_large_zstandard_window_extracts_without_windows_tar(self):
        from compression.zstd import decompress, ZstdError
        payload = archive([('llvm/bin/tool.txt', b'large-window fixture')])
        # A valid raw-block frame declares a 2 GiB window, matching the boundary
        # that broke hosted Windows tar, without needing a large test payload.
        frame = b'\x28\xb5\x2f\xfd\x00\xa8' + ((len(payload) << 3) | 1).to_bytes(3, 'little') + payload
        with self.assertRaises(ZstdError):
            decompress(frame)
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / 'llvm.tar.zst'
            source.write_bytes(frame)
            extract_toolchain(source, root / 'output', hashlib.sha256(frame).hexdigest())
            self.assertEqual((root / 'output/llvm/bin/tool.txt').read_bytes(), b'large-window fixture')

    def test_unverified_toolchain_archive_writes_nothing(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / 'llvm.tar.zst'
            source.write_bytes(b'wrong input')
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                extract_toolchain(source, root / 'output', '0' * 64)
            self.assertFalse((root / 'output').exists())
