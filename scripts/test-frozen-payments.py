"""Exact packaged signer through controlled HTTP; synthetic keys, no funds."""
import hashlib
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
sys.path.insert(0,str(root/'tests'))
from test_payment_transport import Handler,OWNER,OTHER

record=json.loads((root/'evidence/extracted-package.json').read_text())
helper=Path(record['directory'])/'WartNodeHelper.exe'
environment={k:v for k,v in os.environ.items() if not k.startswith(('PYTHON','QT_'))}
environment['PATH']=str(Path(os.environ['SystemRoot'])/'System32')
server=ThreadingHTTPServer(('127.0.0.1',19400),Handler)
server.payloads=[];server.verified=0;server.drop_reply=False
thread=threading.Thread(target=server.serve_forever,daemon=True)
thread.start()

class Client:
    def __init__(self,directory):
        self.process=subprocess.Popen([str(helper),'--signer',str(directory)],stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=environment,creationflags=subprocess.CREATE_NO_WINDOW)
        self.counter=0
    def call(self,action,**args):
        self.counter+=1
        self.process.stdin.write((json.dumps({'id':self.counter,'action':action,'args':args})+'\n').encode())
        self.process.stdin.flush()
        line=self.process.stdout.readline()
        if not line:raise AssertionError(self.process.stderr.read().decode('utf-8','replace'))
        reply=json.loads(line)
        assert reply['id']==self.counter
        if 'error' in reply:raise ValueError(reply['error'])
        return reply['result']
    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=10)
        assert self.process.returncode==0

try:
    with tempfile.TemporaryDirectory(prefix='frozen-payments-',dir=root/'work') as directory:
        client=Client(directory)
        try:
            client.call('setup',password='synthetic-package-password')
            client.call('import',kind='private',secret='00'*31+'01',label='Main')
            review=client.call('review',recipient=OTHER,amount='1.25000000')
            assert review['fee']==9992 and review['total']==125009992
            assert client.call('send',token=review['token'],password='synthetic-package-password')['status']=='pending'
            server.drop_reply=True
            maximum=client.call('review',recipient=OTHER,amount='',max=True)
            assert maximum['amount']==874980016
            assert client.call('send',token=maximum['token'],password='synthetic-package-password')['status']=='uncertain'
            try:
                client.call('send',token=maximum['token'],password='synthetic-package-password')
                raise AssertionError('Consumed confirmation was reused')
            except ValueError:pass
        finally:client.close()
        restarted=Client(directory)
        try:
            restarted.call('unlock',address=OWNER,password='synthetic-package-password')
            assert restarted.call('balance')['available']==0
            rows=restarted.call('history',address=OWNER)['rows']
            assert sorted(r['status'] for r in rows)==['pending','uncertain']
        finally:restarted.close()
    assert server.verified==2 and len(server.payloads)==2
finally:
    server.shutdown();server.server_close();thread.join()
receipt={'archive_sha256':record['archive_sha256'],'helper_sha256':hashlib.sha256(helper.read_bytes()).hexdigest(),
    'real_signatures_verified':server.verified,'fee_e8':9992,'send_max_with_reservations':True,
    'lost_reply_reserved_after_restart':True,'no_automatic_retry':len(server.payloads)==2,
    'synthetic_mainnet_context':True,'live_chain_submission':False,'funds_used':False,'python_on_helper_path':False}
(root/'evidence/frozen-payments.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
print(json.dumps(receipt,indent=2))
