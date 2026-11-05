"""Use Windows certificate-chain validation, including missing issuer retrieval."""
import os
import ssl

def client_context():
    if os.name=='nt':
        import truststore
        context=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    else:
        context=ssl.create_default_context()
    if not context.check_hostname or context.verify_mode!=ssl.CERT_REQUIRED:
        raise RuntimeError('HTTPS certificate verification must remain enabled')
    return context
