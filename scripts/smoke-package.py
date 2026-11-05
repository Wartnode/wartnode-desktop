"""Exercise the actual frozen helper without a Python runtime on PATH."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--package-dir',type=Path,default=root/'dist/WartNode')
options=parser.parse_args()
helper=options.package_dir.resolve()/'WartNodeHelper.exe'
environment={k:v for k,v in os.environ.items() if not k.startswith(('PYTHON','QT_'))}
environment['PATH']=str(Path(os.environ['SystemRoot'])/'System32')
environment['QT_QPA_PLATFORM']='offscreen'

class Client:
    def __init__(self,directory):
        self.process=subprocess.Popen([str(helper),'--signer',str(directory)],stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=environment,creationflags=subprocess.CREATE_NO_WINDOW)
        self.counter=0
    def call(self,action,**args):
        self.counter+=1
        self.process.stdin.write((json.dumps({'id':self.counter,'action':action,'args':args})+'\n').encode())
        self.process.stdin.flush()
        raw=self.process.stdout.readline()
        if not raw:raise AssertionError(self.process.stderr.read().decode('utf-8','replace'))
        value=json.loads(raw)
        if 'error' in value:raise AssertionError(value['error'])
        if value['id']!=self.counter:raise AssertionError('Protocol mismatch')
        return value['result']
    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=10)
        if self.process.returncode:raise AssertionError('Packaged signer did not stop cleanly')

with tempfile.TemporaryDirectory(prefix='packaged-wallet-',dir=root/'work') as directory:
    first=Path(directory)/'first'
    second=Path(directory)/'second'
    first.mkdir();second.mkdir()
    client=Client(first)
    try:
        client.call('setup',password='synthetic-package-password')
        created=client.call('create',label='Main')
        address=created['address']
        assert len(created['phrase'].split())==24
        created.clear()
        assert client.call('list')['wallets'][0]['address']==address
        client.call('lock')
        assert client.call('list')['address'] is None
        client.call('unlock',address=address,password='synthetic-package-password')
        imported=client.call('import',kind='private',secret='00'*31+'01',label='Mining')
        mining_address=imported['address']
        client.call('save_contact',name='Recipient',address=mining_address)
        client.call('edit_address',address=mining_address,default=True)
        client.call('change_password',old_password='synthetic-package-password',new_password='synthetic-new-password')
        client.call('unlock',password='synthetic-new-password')
        backup=Path(directory)/'profile.wartprofile'
        client.call('backup',path=str(backup),password='synthetic-new-password')
    finally:client.close()
    restored=Client(second)
    try:
        assert restored.call('restore',path=str(backup),password='synthetic-new-password')['added']==2
        assert restored.call('unlock',address=address,password='synthetic-new-password')['address']==address
        records=restored.call('list')
        assert {r['label'] for r in records['wallets']}=={'Main','Mining'}
        assert records['contacts']==[{'name':'Recipient','address':mining_address}]
        assert next(r for r in records['wallets'] if r['address']==mining_address)['default']
    finally:restored.close()
receipt={'frozen_helper':True,'python_on_path':False,'create_24_words':True,'lock_unlock':True,'encrypted_backup_restore':True,
         'shared_password_multiple_addresses':True,'labels_contacts_default_restored':True,'password_change_qualified':True,'funds_used':False}
receipt['helper_sha256']=hashlib.sha256(helper.read_bytes()).hexdigest()
receipt['package_dir']=str(options.package_dir.resolve())
(root/'evidence/package-smoke.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
print(json.dumps(receipt,indent=2))
