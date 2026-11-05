"""Build an earlier-version fixture from current source, without cached binaries.

This fixture is test input, not an earlier public release. Only the application
version changes; the qualified node/miner and source-built runtime are reused.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from wartnode import __version__,installed,updates
from wartnode.processes import hidden_options


def build(version='0.4.0-dev.1'):
    if updates.version_key(version)>=updates.version_key(__version__):
        raise ValueError('The upgrade fixture must be older than the candidate')
    folder=Path(tempfile.mkdtemp(prefix='upgrade-fixture-',dir=ROOT/'work'))
    for name in ('wartnode','assets','components','notices'):
        shutil.copytree(ROOT/name,folder/name,ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(ROOT/'vendor/wart_dapp',folder/'vendor/wart_dapp',
                    ignore=shutil.ignore_patterns('__pycache__','.venv','.git'))
    for name in ('WartNode.spec','run.py'):
        shutil.copy2(ROOT/name,folder/name)
    init=folder/'wartnode/__init__.py'
    original=init.read_text(encoding='utf-8')
    changed,count=re.subn(r'__version__ = "[^"]+"','__version__ = "'+version+'"',original)
    assert count==1
    init.write_text(changed,encoding='utf-8')
    manifest=updates.read_json(folder/'components/manifest.json');manifest['application']=version
    (folder/'components/manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    logs=ROOT/'evidence/ci';logs.mkdir(parents=True,exist_ok=True)
    environment=dict(os.environ)
    environment['PATH']=os.pathsep.join((str(Path(sys.executable).parent),sys.base_prefix,
                                        str(Path(os.environ['SystemRoot'])/'System32'),os.environ['SystemRoot']))
    with (logs/'upgrade-fixture-build.log').open('w',encoding='utf-8') as log:
        subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','WartNode.spec'],cwd=folder,
                       stdout=log,stderr=subprocess.STDOUT,check=True,timeout=300,env=environment,**hidden_options())
    bundle=folder/'dist/WartNode'
    shutil.copy2(ROOT/'LICENSE',bundle/'LICENSE.txt')
    shutil.copy2(ROOT/'docs/MVP_GUIDE.md',bundle/'START-HERE.md')
    installed.inventory(bundle,version)
    installed.verify_bundle(bundle,version)
    if any(p.name.lower()=='icuuc.dll' or p.name.lower().startswith('icudt') for p in bundle.rglob('*')):
        raise ValueError('The upgrade fixture contains an unexpected ICU runtime')
    compiler=ROOT/'work/ci/inno/ISCC.exe'
    numeric=version.replace('-dev.','.')
    with (logs/'upgrade-fixture-installer.log').open('w',encoding='utf-8') as log:
        subprocess.run([str(compiler),'--define=AppVersion='+version,'--define=FileVersion='+numeric,
                        '--define=BundleDir='+str(bundle),'--output-dir='+str(folder/'dist'),
                        str(ROOT/'packaging/windows/wartnode.iss')],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,
                       check=True,timeout=180,**hidden_options())
    installer=folder/'dist'/f'Wartnode-Desktop-{version}-windows-x64-setup.exe'
    result={'fixture_version':version,'candidate_version':__version__,'version_only_source_change':True,
            'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            'bundle':str(bundle),'installer':str(installer),'installer_sha256':updates.sha(installer),
            'gui_sha256':updates.sha(bundle/'WartNode.exe'),
            'components':{k:manifest['components'][k]['sha256'] for k in ('node','miner')}}
    (logs/'upgrade-fixture.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--version',default='0.4.0-dev.1')
    print(json.dumps(build(parser.parse_args().version),indent=2))
