import os
import ssl
import unittest
import socket
import tempfile
import threading
from pathlib import Path
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from wartnode.tls import client_context
from wartnode.rpc import NodeRPC

class WindowsTrustTests(unittest.TestCase):
    def test_client_context_requires_certificate_and_hostname_validation(self):
        context=client_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)
        self.assertGreaterEqual(context.minimum_version,ssl.TLSVersion.TLSv1_2)
        if os.name=='nt':self.assertEqual(type(context).__module__,'truststore._api')

    def test_lite_uses_the_platform_context_and_local_rpc_needs_no_tls(self):
        context=client_context()
        with patch('wartnode.tls.client_context',return_value=context) as factory:
            local=NodeRPC()
            factory.assert_not_called()
            remote=NodeRPC('https://mainnet.wartnode.com')
            factory.assert_called_once_with()
            self.assertTrue(any(isinstance(h,__import__('urllib.request',fromlist=['HTTPSHandler']).HTTPSHandler)
                                and h._context is context for h in remote.opener.handlers))

    def test_platform_validation_rejects_an_untrusted_self_signed_peer(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes,serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Untrusted local QA peer')])
        now=datetime.now(timezone.utc)
        cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
              .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1))
              .not_valid_after(now+timedelta(hours=1))
              .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]),critical=False)
              .sign(key,hashes.SHA256()))
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
            (root/'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
            server=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);server.load_cert_chain(root/'cert.pem',root/'key.pem')
            with socket.socket() as listener:
                listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(5)
                def serve():
                    try:
                        connection,_=listener.accept()
                        with server.wrap_socket(connection,server_side=True) as secured:secured.recv(1)
                    except (OSError,ssl.SSLError):pass
                thread=threading.Thread(target=serve,daemon=True);thread.start()
                try:
                    with socket.create_connection(listener.getsockname(),timeout=5) as raw:
                        with self.assertRaises(ssl.SSLError):
                            with client_context().wrap_socket(raw,server_hostname='localhost'):pass
                finally:thread.join(timeout=6)
