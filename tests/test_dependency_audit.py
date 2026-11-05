"""Advisory inventory must include source pins and fail on incomplete coverage."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('dependency_audit', ROOT / 'scripts/audit-dependencies.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class DependencyAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'scripts/ci').mkdir(parents=True)
        (self.root / 'vendor/sources').mkdir(parents=True)
        self.lock = self.root / 'scripts/ci/requirements.lock'
        self.archive = self.root / 'vendor/sources/sdk.tar.gz'
        with tarfile.open(self.archive, 'w:gz') as archive:
            data = b'[project]\nname = "warthog-py"\nversion = "0.4.0"\n'
            member = tarfile.TarInfo('sdk-root/pyproject.toml')
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
        self.digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.commit = 'a' * 40
        self.url = 'https://codeload.github.com/warthog-network/warthog_py/tar.gz/' + self.commit
        self.provenance = dict(schema=1, name='warthog-py', version='0.4.0',
                               repository='https://github.com/warthog-network/warthog_py',
                               commit=self.commit, sha256=self.digest, archive='vendor/sources/sdk.tar.gz')
        self.save_provenance()

    def save_provenance(self):
        (self.root / 'vendor/sources/warthog-python-sdk.json').write_text(json.dumps(self.provenance), encoding='utf-8')

    def direct_line(self):
        return 'Warthog_Py @ ' + self.url + ' --hash=sha256:' + self.digest + '\n'

    def test_inventory_covers_the_real_locked_sdk_and_all_lock_files(self):
        inputs = audit.locked_inputs(ROOT)
        sdk = next(i for i in inputs if i['package'] == 'warthog-py')
        self.assertEqual(sdk['version'], '0.4.0')
        self.assertEqual(sdk['source']['commit'], 'bb1a67ab91ac69c976e2652f545adcbef9d0bde8')
        self.assertEqual({location.split(':')[0] for i in inputs for location in i['locks']},
                         {'scripts/ci/requirements.lock', 'scripts/ci/build-requirements.lock', 'scripts/ci/release-requirements.lock'})

    def test_source_pin_is_resolved_from_the_archive_without_installed_sdk_metadata(self):
        self.lock.write_text(self.direct_line(), encoding='utf-8')
        item, = audit.locked_inputs(self.root)
        self.assertEqual((item['package'], item['version']), ('warthog-py', '0.4.0'))
        self.assertEqual(item['source']['sha256'], self.digest)

    def test_corrupt_archive_wrong_commit_and_invented_version_are_rejected(self):
        self.lock.write_text(self.direct_line(), encoding='utf-8')
        for field, value in [('version', '9.9.9'), ('commit', 'b' * 40), ('sha256', '0' * 64)]:
            original = self.provenance[field]
            self.provenance[field] = value
            self.save_provenance()
            with self.subTest(field=field), self.assertRaises(ValueError):
                audit.locked_inputs(self.root)
            self.provenance[field] = original
        self.save_provenance()
        self.archive.write_bytes(b'corrupted source')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            audit.locked_inputs(self.root)

    def test_unsupported_or_unreviewed_entries_are_never_silently_omitted(self):
        for line in ['example>=1', 'example==1', '-r another.lock',
                     self.direct_line().replace('Warthog_Py', 'unknown-package')]:
            self.lock.write_text(line, encoding='utf-8')
            with self.subTest(line=line), self.assertRaises(ValueError):
                audit.locked_inputs(self.root)

    def test_canonical_names_deduplicate_but_conflicting_sources_or_versions_fail(self):
        pinned = 'Example_Package==1.0 --hash=sha256:' + '0' * 64 + '\n'
        self.lock.write_text(pinned, encoding='utf-8')
        second = self.root / 'scripts/ci/build-requirements.lock'
        second.write_text(pinned.replace('Example_Package', 'example-package'), encoding='utf-8')
        item, = audit.locked_inputs(self.root)
        self.assertEqual(len(item['locks']), 2)
        second.write_text(pinned.replace('1.0', '2.0'), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            audit.locked_inputs(self.root)
        self.lock.write_text(self.direct_line(), encoding='utf-8')
        second.write_text('warthog-py==0.4.0 --hash=sha256:' + self.digest, encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            audit.locked_inputs(self.root)

    def test_source_query_includes_both_package_version_and_repository_advisories(self):
        self.lock.write_text(self.direct_line(), encoding='utf-8')
        item, = audit.locked_inputs(self.root)
        repo_advisory = {'ghsa_id': 'GHSA-synthetic', 'severity': 'high'}
        with patch.object(audit, 'github_entries', side_effect=[[], [repo_advisory]]) as lookup:
            result = audit.query(item)
        self.assertIn('affects=warthog-py@0.4.0', lookup.call_args_list[0].args)
        self.assertEqual(lookup.call_args_list[1].args, ('/repos/warthog-network/warthog_py/security-advisories',))
        self.assertEqual(result['repository_advisories'], [repo_advisory])
        self.assertEqual(result['source']['commit'], self.commit)

    def test_failed_or_truncated_lookup_cannot_be_reported_as_no_advisories(self):
        for response in [SimpleNamespace(returncode=1, stdout=''),
                         SimpleNamespace(returncode=0, stdout=json.dumps([{}] * 100)),
                         SimpleNamespace(returncode=0, stdout='{}')]:
            with patch.object(audit.subprocess, 'run', return_value=response), self.assertRaises(ValueError):
                audit.github_entries('/advisories')


if __name__ == '__main__':
    unittest.main()
