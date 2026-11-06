"""Inventory the complete application before portable and installed packaging."""
from pathlib import Path
from datetime import datetime,timezone
import json
import argparse
import os
import re
import subprocess
import sys
import runpy
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from wartnode import __version__
from wartnode.preflight import MINIMUM_WINDOWS_BUILD
from wartnode.installed import inventory,verify_bundle
from wartnode import updates
from wartnode.paths import atomic_json
package=root/'dist/WartNode'
parser=argparse.ArgumentParser()
parser.add_argument('--source-commit')
source_commit=parser.parse_args().source_commit or subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
if not re.fullmatch('[0-9a-f]{40}',source_commit):raise ValueError('Invalid package source commit')
manifest=updates.read_json(package/'_internal/components/manifest.json')
assert manifest['application']==__version__
# A post-build delivery receipt cannot be embedded in its own hashed ZIP. Never
# ship a tracked receipt left over from a previously accepted development build.
(package/'provenance/build.json').unlink(missing_ok=True)
context={'schema':1,'application':__version__,'source_commit':source_commit,
    'native_runtime':runpy.run_path(str(root/'scripts/audit-runtime.py'))['audit']()['observed'],
    'created_utc':datetime.now(timezone.utc).isoformat(),'release_target':'Windows 11 x64','minimum_windows_build':MINIMUM_WINDOWS_BUILD,
    'data_schema':updates.DATA_SCHEMA,'github_run_id':os.environ.get('GITHUB_RUN_ID'),
    'source_lock_sha256':updates.sha(root/'scripts/ci/sources.lock.json'),
    'toolchains_lock_sha256':updates.sha(root/'scripts/ci/toolchains.lock.json'),
    'components':{name:entry['sha256'] for name,entry in manifest['components'].items() if 'file' in entry},
    'runtime_sources':{p.relative_to(root).as_posix():updates.sha(p) for p in sorted((root/'wartnode').glob('*.py'))},
    'production_release':False,'clean_windows11_certified':False,
    'verification_receipts':'Generated after packaging; included with source and delivery artifacts'}
atomic_json(package/'provenance/build-context.json',context)
inventory(package,__version__)
verify_bundle(package,__version__)
print('Complete application inventory verified')
