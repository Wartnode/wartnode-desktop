"""New signer through real loopback HTTP, using synthetic keys and chain data."""
from hashlib import sha256
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from ecdsa.util import sigdecode_string
from wartnode.compat import wallet_utils as crypto
from wartnode.signer import Signer

OWNER,KEY=crypto.wallet_from_pkhex('00'*31+'01')
OTHER,_=crypto.wallet_from_pkhex('00'*31+'02')


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def send(self,data):
        raw=json.dumps({'code':0,'data':data}).encode()
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def do_GET(self):
        if self.path=='/chain/head':return self.send({'height':64,'hash':'ab'*32,'pinHeight':32,'pinHash':'ef'*32,'synced':True})
        if self.path.startswith('/chain/mine/'):return self.send({'testnet':False,'synced':True,'height':65,'header':'ab'*32+'00'*48})
        if self.path.startswith('/account/'):return self.send({'balance':'10.00000000'})
        if self.path=='/transaction/minfee':return self.send({'E8':9992})
        if self.path=='/peers/connected/connection':return self.send({'data':['127.0.0.1:9186']})
        self.send_error(404)
    def do_POST(self):
        payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.payloads.append(payload)
        signed=(bytes.fromhex('ef'*32)+payload['pinHeight'].to_bytes(4,'big')+payload['nonceId'].to_bytes(4,'big')
                +b'\0\0\0'+payload['feeE8'].to_bytes(8,'big')+bytes.fromhex(payload['toAddr'])[:20]+payload['amountE8'].to_bytes(8,'big'))
        digest=sha256(signed).digest()
        KEY.get_verifying_key().verify_digest(bytes.fromhex(payload['signature65'])[:64],digest,sigdecode=sigdecode_string)
        self.server.verified+=1
        if self.server.drop_reply:
            self.close_connection=True
            return
        self.send({'txHash':digest.hex()})


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.payloads=[]
        self.server.verified=0
        self.server.drop_reply=False
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.temp=tempfile.TemporaryDirectory()
        self.signer=Signer(self.temp.name,rpc=f'http://127.0.0.1:{self.server.server_port}')
        self.signer.call('import',{'kind':'private','secret':'00'*31+'01','password':'synthetic-password'})
    def tearDown(self):
        self.signer.store.con.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()
    def test_exact_review_sign_reserve_and_node_acknowledgement(self):
        review=self.signer.call('review',{'recipient':OTHER,'amount':'1.25000000'})
        result=self.signer.call('send',{'token':review['token'],'password':'synthetic-password'})
        self.assertEqual(result['status'],'pending')
        self.assertEqual(self.server.verified,1)
        self.assertEqual(self.server.payloads[0]['feeE8'],9992)
        self.assertEqual(self.server.payloads[0]['amountE8'],125000000)
        self.assertEqual(self.signer.store.reserved_amount(OWNER),125009992)
    def test_accepted_then_lost_reply_stays_uncertain_and_not_retried(self):
        self.server.drop_reply=True
        review=self.signer.call('review',{'recipient':OTHER,'amount':'1'})
        result=self.signer.call('send',{'token':review['token'],'password':'synthetic-password'})
        self.assertEqual(result['status'],'uncertain')
        self.assertEqual(self.server.verified,1)
        self.assertEqual(len(self.server.payloads),1)
        with self.assertRaises(ValueError):self.signer.call('send',{'token':review['token'],'password':'synthetic-password'})
        self.assertEqual(len(self.server.payloads),1)


if __name__=='__main__':unittest.main()
