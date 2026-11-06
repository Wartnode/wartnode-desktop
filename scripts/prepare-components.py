"""Record exact build identities and assemble verified component payloads."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from wartnode import __version__
from wartnode.fees import NODE_MINIMUM_FEE, TRANSACTION_FEE_E8
components = ROOT / 'components'
components.mkdir(exist_ok=True)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

node_source=ROOT/'work/upstream-node-build/wart-node-windows.exe'
node_target=components/'wart-node-windows.exe'
build=json.loads((ROOT/'provenance/node-build.json').read_text())
assert build['source_verified'] and digest(node_source)==build['node_sha256'], 'Build the node from verified source before preparing components'
assert build['source_lock_sha256']==digest(ROOT/'scripts/ci/sources.lock.json')
assert build['recipe_sha256']==digest(ROOT/'scripts/core.Dockerfile')
lock=json.loads((ROOT/'scripts/ci/sources.lock.json').read_text())
assert build['patches']==lock['patches']
for name,value in build['build_overlay'].items():assert digest(ROOT/name)==value
miner_build=json.loads((ROOT/'provenance/miner-build.json').read_text())
assert miner_build['prebuilt_libraries_used'] is False
assert miner_build['engine_sha256']==digest(components/'wartnode-miner.exe')
assert miner_build['source_lock_sha256']==digest(ROOT/'scripts/ci/sources.lock.json')
miner_identity=hashlib.sha256((''.join(digest(p) for p in sorted([*(ROOT/'native').glob('*.cpp'),*(ROOT/'native').glob('*.hpp')]))
    +digest(ROOT/'scripts/ci/sources.lock.json')+digest(ROOT/'scripts/ci/build_miner.py')).encode()).hexdigest()
assert miner_build['build_id']==miner_identity, 'Rebuild the miner after changing its source or build recipe'
if not node_target.exists() or digest(node_source)!=digest(node_target):
    shutil.copy2(node_source,node_target)

manifest = {'schema':1,'network':'mainnet','application':__version__,'components':{
    'node':{'file':'wart-node-windows.exe','repository':'https://github.com/warthog-network/core',
        'commit':'f7b9dfcd4b9dfc088894c354bf9f2eacea5e19d2','branch':'master',
        'admission_fee_policy':'owner-selected managed-node minimum; upstream --minfee option',
        'minimum_fee_wart':NODE_MINIMUM_FEE,'minimum_fee_e8':TRANSACTION_FEE_E8,
        'patches':[
            {**lock['patches'][0], 'source_file':'src/node/main.cpp',
             'patched_source_sha256':digest(ROOT/'vendor/upstream-core/src/node/main.cpp'),
             'purpose':'Join event/chain workers before destroying HTTP endpoints',
             'user_approved':'2026-11-03','consensus_changes':False},
            {**lock['patches'][1], 'source_files':['src/node/asyncio/conman.cpp','src/node/asyncio/conman.hpp'],
             'purpose':'Serialize network wakeup closure with producers; reject callbacks after closure',
             'authorization':'Combined Steps 3 and 4 product hardening goal', 'consensus_changes':False}],
        'build_overlay':build['build_overlay'],
        'source_archive_sha256':digest(ROOT/'work/upstream-core-source.tar'),
        'sha256':digest(components/'wart-node-windows.exe')},
    'miner':{'file':'wartnode-miner.exe','repository':'https://github.com/JohnnyB813/BRminer',
        'upstream':'https://github.com/Brage321/BRminer',
        'commit':'87a6c85082505a0d2673a170e5fabde9a88cfbec',
        'entry_point_sha256':digest(ROOT/'native/miner_main.cpp'),
        'pipeline_sha256':digest(ROOT/'native/miner_pipeline.cpp'),
        'benchmark_sha256':digest(ROOT/'native/miner_benchmark.cpp'),
        'budget_header_sha256':digest(ROOT/'native/mining_budget.hpp'),
        'telemetry_header_sha256':digest(ROOT/'native/miner_telemetry.hpp'),
        'engine':'WartNode in-house BRminer-derived','gpu_backend':'driver OpenCL','compiler':'clang-cl',
        'source_archive_sha256':digest(ROOT/'work/miner-source.tar'),
        'source_build_libraries':'provenance/miner-link-inputs.json',
        'prebuilt_libraries_used':False,
        'sha256':digest(components/'wartnode-miner.exe')},
    'wallet':{'repository':'https://github.com/JohnnyB813/wart-dapp',
        'commit':'772f55d99d066988927d82cd787ab17be5e6c8c9', 'source_archive_sha256':digest(ROOT/'work/wallet-source.tar')}
}}
vanity_receipt = ROOT/'provenance/vanity-build.json'
if vanity_receipt.exists():
    vanity = json.loads(vanity_receipt.read_text(encoding='utf-8'))
    assert vanity['file']=='wartnode-vanity.exe' and digest(components/vanity['file'])==vanity['sha256'], 'Rebuild the vanity worker'
    assert all(digest(ROOT/path)==value for path,value in vanity['adapter_sources'].items()), 'Rebuild the vanity worker after changing its source'
    manifest['components']['vanity'] = vanity
(components/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
(ROOT/'provenance/components.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
for name, source in [('Warthog-core-MIT.txt','vendor/upstream-core/LICENSE'),('BRminer-GPL3.txt','vendor/brminer/LICENSE'),('Warthog-wallet-MIT.txt','vendor/wart_dapp/LICENSE')]:
    target = ROOT/'notices'/name
    target.parent.mkdir(exist_ok=True)
    shutil.copy2(ROOT/source,target)
for source in (ROOT/'vendor/brminer/src/warthog_native_batch/LICENSES').glob('*'):
    shutil.copy2(source,ROOT/'notices'/source.name)
shutil.copy2(ROOT/'vendor/brminer/src/warthog_cuda_kernel.LICENSE',ROOT/'notices/CUDA-kernel-source.txt')
for name, source in [('ConnectCoin-MIT.txt','vendor/connectcoin/COPYING'),('JanusMiner-MIT.txt','vendor/janusminer/LICENSE')]:
    shutil.copy2(ROOT/source,ROOT/'notices'/name)
print(json.dumps(manifest,indent=2))
