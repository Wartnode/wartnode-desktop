"""Read current advisories for every hash-locked Python input, including source pins."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
HASH = r'[0-9a-f]{64}'
NAME = r'[A-Za-z0-9][A-Za-z0-9_.-]*'
VERSION = r'[A-Za-z0-9_.+!-]+'
FIELDS = ('ghsa_id', 'cve_id', 'summary', 'severity', 'html_url',
          'repository_advisory_url', 'withdrawn_at', 'vulnerabilities')


def canonical(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def source_input(root, name, url, digest):
    """Use verified source metadata, never the version of an unrelated installed wheel."""
    manifests = {'warthog-py': root / 'vendor/sources/warthog-python-sdk.json'}
    if name not in manifests:
        raise ValueError('Direct dependency needs reviewed source provenance: ' + name)
    record = json.loads(manifests[name].read_text(encoding='utf-8'))
    repository, commit = record['repository'], record['commit']
    if (record['schema'] != 1 or canonical(record['name']) != name
            or not re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository)
            or not re.fullmatch(r'[0-9a-f]{40}', commit)):
        raise ValueError('Invalid direct dependency provenance: ' + name)
    expected_url = 'https://codeload.github.com/' + repository.removeprefix('https://github.com/') + '/tar.gz/' + commit
    if url != expected_url or digest != record['sha256']:
        raise ValueError('Direct dependency differs from reviewed provenance: ' + name)
    archive = (root / record['archive']).resolve()
    if not archive.is_relative_to(root.resolve()) or hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
        raise ValueError('Direct dependency source archive hash mismatch: ' + name)
    with tarfile.open(archive, 'r:gz') as source:
        projects = [m for m in source.getmembers() if m.isfile()
                    and len(PurePosixPath(m.name).parts) == 2
                    and PurePosixPath(m.name).name == 'pyproject.toml']
        if len(projects) != 1 or projects[0].size > 128 * 1024:
            raise ValueError('Missing or ambiguous source package metadata: ' + name)
        project = tomllib.loads(source.extractfile(projects[0]).read().decode('utf-8'))['project']
    version = record['version']
    if (canonical(project['name']) != name or project['version'] != version
            or not re.fullmatch(VERSION, version)):
        raise ValueError('Direct dependency version differs from verified source: ' + name)
    return dict(type='source-archive', url=url, sha256=digest, repository=repository,
                commit=commit, archive=record['archive'], version=version)


def locked_inputs(root=ROOT):
    inputs = {}
    locks = sorted((root / 'scripts/ci').glob('*requirements.lock'))
    if not locks:
        raise ValueError('No dependency locks found')
    for path in locks:
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            pinned = re.fullmatch(rf'({NAME})==({VERSION})(?: --hash=sha256:{HASH})+', line)
            direct = re.fullmatch(rf'({NAME}) @ (https://\S+) --hash=sha256:({HASH})', line)
            location = path.relative_to(root).as_posix() + ':' + str(number)
            if pinned:
                name, version = canonical(pinned[1]), pinned[2]
                source = dict(type='pypi')
            elif direct:
                name = canonical(direct[1])
                source = source_input(root, name, direct[2], direct[3])
                version = source['version']
            else:
                raise ValueError('Unsupported dependency lock entry: ' + location)
            item = dict(package=name, version=version, source=source, locks=[location])
            if name in inputs:
                previous = inputs[name]
                if previous['version'] != version or previous['source'] != source:
                    raise ValueError('Conflicting dependency versions or sources: ' + name)
                previous['locks'].append(location)
            else:
                inputs[name] = item
    return [inputs[name] for name in sorted(inputs)]


def github_entries(endpoint, *parameters):
    result = subprocess.run(['gh', 'api', '--method', 'GET', endpoint, '-f', 'per_page=100', *parameters],
                            capture_output=True, text=True, encoding='utf-8', timeout=60,
                            creationflags=0x08000000 if os.name == 'nt' else 0)
    if result.returncode:
        raise ValueError('Advisory lookup failed: ' + endpoint)
    entries = json.loads(result.stdout)
    if not isinstance(entries, list) or len(entries) >= 100:
        raise ValueError('Advisory results incomplete or need pagination: ' + endpoint)
    return [{key: entry.get(key) for key in FIELDS}
            for entry in entries if entry.get('withdrawn_at') is None]


def query(item):
    name, version = item['package'], item['version']
    advisories = github_entries('/advisories', '-f', 'ecosystem=pip', '-f', 'type=reviewed',
                               '-f', f'affects={name}@{version}')
    result = dict(item, advisories=advisories)
    if item['source']['type'] == 'source-archive':
        repository = item['source']['repository'].removeprefix('https://github.com/')
        # An unpublished commit may have no package-index advisory mapping.
        # Retain all published repository advisories for source applicability review.
        result['repository_advisories'] = github_entries('/repos/' + repository + '/security-advisories')
        result['source_review_note'] = 'Version and repository lookups do not certify the pinned commit; assess source applicability.'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'work/step5/dependency-advisories.json')
    args = parser.parse_args()
    inputs = locked_inputs()
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(query, inputs))
    findings = [r for r in results if r['advisories'] or r.get('repository_advisories')]
    record = dict(checked_utc=datetime.now(timezone.utc).isoformat(),
                  provider='GitHub reviewed global and published repository advisory APIs',
                  scope='Every Python runtime/build/release lock, including verified source pins; native and full source review remain separate.',
                  lookup_complete=True, findings_require_review=bool(findings), results=results)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(packages=len(results), source_pins=sum(r['source']['type'] == 'source-archive' for r in results),
                          findings_require_review=bool(findings), advisories=findings), indent=2))


if __name__ == '__main__':
    main()
