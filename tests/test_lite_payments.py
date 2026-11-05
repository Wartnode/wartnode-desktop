"""Lite payments over real TLS with a fixture-only trust root and synthetic funds."""
from datetime import datetime,timedelta,timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
import socket
import ssl
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from wartnode import settings
from wartnode.connection import LITE_ENDPOINT
from wartnode.node_history import MAINNET_BLOCK_ONE
from wartnode.rpc import NodeRPC,NodeError
from wartnode.signer import Signer
from test_payment_transport import Handler,OWNER,OTHER

class LiteHandler(Handler):
    def do_GET(self):
        if self.path.startswith('/chain/mine/') or self.path.startswith('/peers/'):
            raise AssertionError('Lite requested a local-only endpoint')
        if self.path.startswith('/chain/block/') and self.path.endswith('/header'):
            height=int(self.path.split('/')[3])
            return self.send({'header':{'hash':self.server.anchor if height==1 else 'ef'*32 if height==32 else 'ab'*32}})
        return super().do_GET()

class LitePaymentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Lite test CA')]);now=datetime.now(timezone.utc)
        certificate=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('mainnet.wartnode.com')]),critical=False).sign(key,hashes.SHA256()))
        cert=self.base/'tls.pem';cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        pem=self.base/'key.pem';pem.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(cert,pem)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),LiteHandler)
        self.server.socket=tls.wrap_socket(self.server.socket,server_side=True)
        self.server.payloads=[];self.server.verified=0;self.server.drop_reply=False;self.server.anchor=MAINNET_BLOCK_ONE
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        original_dns=socket.getaddrinfo;original_opener=urllib.request.build_opener
        self.dns=patch('socket.getaddrinfo',side_effect=lambda host,port,*args,**kwargs:original_dns('127.0.0.1',self.server.server_port,*args,**kwargs) if host=='mainnet.wartnode.com' else original_dns(host,port,*args,**kwargs))
        context=ssl.create_default_context(cafile=str(cert))
        self.transport=patch('urllib.request.build_opener',side_effect=lambda *handlers:original_opener(
            *[h for h in handlers if not isinstance(h,urllib.request.HTTPSHandler)],urllib.request.HTTPSHandler(context=context)))
        self.dns.start();self.transport.start()
        self.profile=self.base/'wallet';self.profile.mkdir();settings.save(self.profile,connection_mode='lite')
        self.signer=Signer(self.profile)
        self.signer.call('setup',{'password':'synthetic-password'})
        self.signer.call('import',{'kind':'private','secret':'00'*31+'01','password':'synthetic-password'})
    def tearDown(self):
        self.signer.store.con.close();self.transport.stop();self.dns.stop()
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()
    def review(self):return self.signer.call('review',{'recipient':OTHER,'amount':'1.25'})
    def test_lite_signs_locally_and_only_sends_a_signature_then_reserves_lost_reply(self):
        review=self.review();self.assertEqual(review['node'],LITE_ENDPOINT)
        self.assertEqual(review['fee'],9992)
        result=self.signer.call('send',{'token':review['token'],'password':'synthetic-password'})
        self.assertEqual(result['status'],'pending');self.assertEqual(self.server.verified,1)
        self.assertNotIn('secret',self.server.payloads[0]);self.assertNotIn('password',self.server.payloads[0])
        self.server.drop_reply=True
        review=self.signer.call('review',{'recipient':OTHER,'max':True})
        self.assertEqual(review['amount'],874980016)
        self.assertEqual(self.signer.call('send',{'token':review['token'],'password':'synthetic-password'})['status'],'uncertain')
        self.signer.store.con.close();self.signer=Signer(self.profile)
        self.signer.call('unlock',{'address':OWNER,'password':'synthetic-password'})
        self.assertEqual(self.signer.call('balance',{})['available'],0)
        self.assertEqual(self.server.verified,2)
    def test_mode_revision_change_invalidates_review_and_unlock_even_when_endpoint_matches(self):
        review=self.review();settings.save(self.profile,connection_revision=1)
        with self.assertRaisesRegex(ValueError,'Unlock'):self.signer.call('send',{'token':review['token'],'password':'synthetic-password'})
        self.assertIsNone(self.signer.address);self.assertEqual(self.server.verified,0)
    def test_wrong_chain_anchor_prevents_review_or_signing(self):
        self.server.anchor='00'*32
        with self.assertRaisesRegex(NodeError,'anchor'):self.review()
        self.assertEqual(self.server.payloads,[])
    def test_invalid_chain_pin_prevents_signing(self):
        client=NodeRPC(LITE_ENDPOINT)
        with patch.object(client,'head',return_value={'height':64,'hash':'ab'*32,'synced':True,'pinHeight':32,'pinHash':'00'*32}):
            with self.assertRaisesRegex(NodeError,'chain changed'):client.require_network()

if __name__=='__main__':unittest.main()
