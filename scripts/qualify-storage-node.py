"""Exercise custom storage with the real official node; no mining or spending."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from wartnode import __version__,updates
from wartnode.paths import data_root
from wartnode.processes import Runtime,SignerClient
from wartnode.rpc import NodeRPC
from wartnode.node_history import NodeHistory
from wartnode.settings import chain_directory,load


def main():
    with tempfile.TemporaryDirectory(prefix='storage-node-',dir=ROOT/'work') as temporary:
        base=Path(temporary)
        profile=data_root(base/'钱包 Wallet data')
        signer=SignerClient(profile)
        try:
            signer.call('setup',password='synthetic-storage-password')
            signer.call('import',kind='private',secret='00'*31+'01',label='Storage test')
        finally:signer.close()
        wallet_hash=updates.sha(profile/'wallet.db')
        runtime=Runtime(profile)
        observations=[]
        try:
            for name in ('链 data one','Chain data two'):
                target=base/name
                runtime.change_storage(target)
                assert chain_directory(profile)==target.resolve()
                if not load(profile)["setup_complete"]:
                    runtime.initialize_node(target)
                runtime.start_node()
                deadline=time.monotonic()+25
                while not runtime.rpc_available:
                    if not runtime.node.running:
                        raise RuntimeError('Node stopped in custom storage: '+'\n'.join(runtime.node.lines)[-2500:])
                    if time.monotonic()>deadline:raise RuntimeError('The node did not start from custom storage')
                    time.sleep(.1)
                client=NodeRPC(deadline=time.monotonic()+10)
                head=client.head()
                NodeHistory(client,target/'chain.db3').anchor()
                assert runtime.node.cwd==target.resolve()
                assert '--chain-db=chain.db3' in runtime.node.process.args
                stopped=runtime.stop_node()
                assert stopped.get('graceful') and stopped.get('exit_code')==0 and not stopped.get('forced'), stopped
                with closing(sqlite3.connect((target/'chain.db3').resolve().as_uri()+'?mode=ro',uri=True)) as connection:
                    assert connection.execute('PRAGMA quick_check').fetchone()[0]=='ok'
                assert not (profile/'node/chain.db3').exists()
                assert updates.sha(profile/'wallet.db')==wallet_hash
                observations.append({'custom_folder':name,'height':head['height'],'mainnet_verified':True,
                                     'graceful_stop':True,'database_integrity':'ok','wallet_unchanged':True})
            archive=Path(runtime.archive_chain())
            assert (archive/'chain.db3').is_file()
            assert not (chain_directory(profile)/'chain.db3').exists()
            assert updates.sha(profile/'wallet.db')==wallet_hash
            record={'all_passed':True,'application':__version__,'observations':observations,
                    'custom_storage_resync_preserves_wallet':True,'mining_started':False,'funds_used':False,
                    'live_chain_submission':False,'additional_block_discovery_required':False}
            (ROOT/'evidence/ci/storage-node.json').write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
            print(json.dumps(record,indent=2))
        finally:runtime.shutdown()


if __name__=='__main__':main()
