"""Offline address proofs. Wire formats are specified in docs/SIGNED_PROOFS.md.

This module has no profile, IPC, RPC, or UI dependency. Only the private helper
calls sign(); verification needs only public data and the original file.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from Cryptodome.Hash import RIPEMD160
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils
from warthog_py import Account

FORMAT = 'wartnode-signed-proof'
VERSION = 2
NETWORK = 'warthog-mainnet'
DOMAIN = b'\x00WartNode Signed Proof\x00v2\x00'
LEGACY_DOMAIN = b'\x00WartNode Signed Proof\x00v1\x00'
ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
MAX_MESSAGE_BYTES = 16 * 1024
MAX_PROOF_BYTES = 128 * 1024
MAX_FILE_BYTES = 16 * 1024**3
EXTENSION = '.wartproof'


def _hex(value, length):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{%d}' % (2 * length), value):
        raise ValueError('The proof contains an invalid hexadecimal value.')
    return bytes.fromhex(value)


def address_from_public_key(public_key):
    _hex(public_key, 33)
    if public_key[:2] not in ('02', '03'):
        raise ValueError('The proof needs a compressed secp256k1 public key.')
    encoded = bytes.fromhex(public_key)
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256K1(), encoded)
    raw = RIPEMD160.new(hashlib.sha256(encoded).digest()).digest()
    return (raw + hashlib.sha256(raw).digest()[:4]).hex()


def validate_address(address):
    if not isinstance(address, str):
        raise ValueError('Enter the expected 48-character Warthog address.')
    address = address.strip().lower()
    try:
        raw = _hex(address, 24)
    except ValueError as error:
        raise ValueError('Enter the expected 48-character Warthog address.') from error
    if hashlib.sha256(raw[:20]).digest()[:4] != raw[20:]:
        raise ValueError('The Warthog address checksum is invalid.')
    return address


def validate_content(kind, content):
    if not isinstance(content, dict):
        raise ValueError('The proof content is invalid.')
    if kind == 'message':
        message = content.get('message')
        if set(content) != {'message'} or not isinstance(message, str):
            raise ValueError('The proof needs a text message.')
        try:
            size = len(message.encode('utf-8'))
        except UnicodeError as error:
            raise ValueError('The message is not valid Unicode text.') from error
        if not size or size > MAX_MESSAGE_BYTES:
            raise ValueError('Enter a message of 1 to 16,384 UTF-8 bytes.')
        if any(ord(c) < 32 and c not in '\r\n\t' for c in message):
            raise ValueError('The message contains unsupported control characters.')
    elif kind == 'file':
        if set(content) != {'name', 'size', 'sha256'}:
            raise ValueError('The file proof metadata is invalid.')
        name = content['name']
        if (not isinstance(name, str) or not 1 <= len(name) <= 255
                or any(ord(c) < 32 or c in '/\\' for c in name)):
            raise ValueError('The file proof needs a filename without a folder path.')
        try:
            name.encode('utf-8')
        except UnicodeError as error:
            raise ValueError('The filename is not valid Unicode text.') from error
        if type(content['size']) is not int or not 0 <= content['size'] <= MAX_FILE_BYTES:
            raise ValueError('Choose a file of at most 16 GiB.')
        _hex(content['sha256'], 32)
    else:
        raise ValueError('Unsupported proof type. Choose a message or file.')
    return copy.deepcopy(content)


def prepare(address, kind, content):
    return dict(format=FORMAT, version=VERSION, network=NETWORK, kind=kind,
                address=validate_address(address), content=validate_content(kind, content))


def signing_bytes(body):
    if type(body.get('version')) is not int or body['version'] not in (1, VERSION):
        raise ValueError('Unsupported signature document format or version.')
    encoded = json.dumps(body, sort_keys=True, ensure_ascii=False,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    domain = LEGACY_DOMAIN if body['version'] == 1 else DOMAIN
    return domain + len(encoded).to_bytes(4, 'big') + encoded


def sign(prepared, private_key):
    """Private-helper only: construct and sign our domain, never a supplied hash."""
    body = prepare(prepared['address'], prepared['kind'], prepared['content'])
    account = None
    try:
        # Account's repr contains its secret. Never log, serialize or return it,
        # or propagate SDK exception text across the helper boundary.
        account = Account.from_private_key(private_key.to_string())
        owner = account.address.hex
        body['publicKey'] = account.public_key_hex
        if owner != body['address']:
            signature = None
        else:
            signature = account.sign_bytes(signing_bytes(body))[3]
    except Exception:
        raise ValueError('The wallet could not sign this proof.') from None
    finally:
        del account
    if signature is None:
        raise ValueError('The signing key does not match the selected address.')
    result = dict(body, signature=signature)
    validate(result)
    return result


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('The proof contains duplicate JSON fields.')
        result[key] = value
    return result


def loads(text):
    try:
        if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_PROOF_BYTES:
            raise ValueError('The signature document exceeds the 128 KiB limit.')
        proof = json.loads(text, object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError('Paste or open a valid WartNode signature document.') from error
    validate(proof)
    return proof


def validate(proof):
    if not isinstance(proof, dict) or set(proof) != {
            'format', 'version', 'network', 'kind', 'address', 'content', 'publicKey', 'signature'}:
        raise ValueError('This is not a supported WartNode signature document.')
    if proof['format'] != FORMAT or type(proof['version']) is not int or proof['version'] not in (1, VERSION):
        raise ValueError('Unsupported signature document format or version.')
    if proof['network'] != NETWORK:
        raise ValueError('This proof is not for Warthog mainnet.')
    if validate_address(proof['address']) != proof['address']:
        raise ValueError('The proof address must use lowercase hexadecimal.')
    validate_content(proof['kind'], proof['content'])
    _hex(proof['publicKey'], 33)
    # Validate before calling the SDK: its permissive hex parser can accept
    # whitespace and raise IndexError, and recovery also accepts high-s forms.
    signature = _hex(proof['signature'], 64 if proof['version'] == 1 else 65)
    r, s = int.from_bytes(signature[:32], 'big'), int.from_bytes(signature[32:64], 'big')
    if not 1 <= r < ORDER or not 1 <= s <= ORDER // 2:
        raise ValueError('The proof signature is invalid or not normalized.')
    if proof['version'] == VERSION and signature[64] > 3:
        raise ValueError('The proof signature recovery ID is invalid.')


def dumps(proof):
    validate(proof)
    return json.dumps(proof, ensure_ascii=False, sort_keys=True, indent=2) + '\n'


def read(path):
    with open(path, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Choose a regular signature document.')
        data = stream.read(MAX_PROOF_BYTES + 1)
    if len(data) > MAX_PROOF_BYTES:
        raise ValueError('The signature document exceeds the 128 KiB limit.')
    try:
        return loads(data.decode('utf-8'))
    except UnicodeError as error:
        raise ValueError('The signature document must be UTF-8 text.') from error


def fingerprint(path, cancel=None, progress=None):
    """Stream a stable regular file on a worker; no file bytes enter the signer."""
    def check_cancel():
        if cancel is not None and cancel.is_set():
            raise ValueError('File hashing cancelled.')
    check_cancel()
    with open(path, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('Choose a regular file.')
        if before.st_size > MAX_FILE_BYTES:
            raise ValueError('Choose a file of at most 16 GiB.')
        digest, size = hashlib.sha256(), 0
        while True:
            check_cancel()
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                raise ValueError('Choose a file of at most 16 GiB.')
            digest.update(chunk)
            if progress is not None:
                progress(size, before.st_size)
        after = os.fstat(stream.fileno())
    check_cancel()
    current = Path(path).stat()
    # On Windows, fstat and path.stat can report different creation/ctime
    # values after a rename. File identity, length and modification time agree.
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns,
                          s.st_ctime_ns if os.name != 'nt' else 0)
    if size != before.st_size or identity(before) != identity(after) or identity(after) != identity(current):
        raise ValueError('The file changed while it was being read. Choose it again.')
    return validate_content('file', dict(name=Path(path).name, size=size, sha256=digest.hexdigest()))


def verify(proof, expected_address, file_path=None, cancel=None, progress=None):
    validate(proof)
    expected_address = validate_address(expected_address)
    actual = address_from_public_key(proof['publicKey'])
    if actual != proof['address']:
        raise ValueError('The public key does not belong to the address in this proof.')
    body = {key: value for key, value in proof.items() if key != 'signature'}
    mismatch = 'The signature does not match the content. The proof may have been changed.'
    if proof['version'] == VERSION:
        try:
            recovered = Account.recover_public_key(signing_bytes(body), proof['signature'])
        except Exception:
            raise ValueError(mismatch) from None
        # Recovery on modified content can succeed with a different valid key.
        # Success requires the signed key/address AND independently expected owner.
        if recovered.hex() != proof['publicKey']:
            raise ValueError(mismatch)
    else:
        # Read-only compatibility for proofs exported by the original prototype.
        raw = bytes.fromhex(proof['signature'])
        der = utils.encode_dss_signature(int.from_bytes(raw[:32], 'big'), int.from_bytes(raw[32:], 'big'))
        key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256K1(), bytes.fromhex(proof['publicKey']))
        try:
            key.verify(der, signing_bytes(body), ec.ECDSA(hashes.SHA256()))
        except InvalidSignature:
            raise ValueError(mismatch) from None
    if actual != expected_address:
        raise ValueError('Different signer: this proof was signed by ' + actual + '.')
    if proof['kind'] == 'file':
        if not file_path:
            raise ValueError('Choose the original file to verify its contents.')
        content = fingerprint(file_path, cancel, progress)
        if any(content[key] != proof['content'][key] for key in ('size', 'sha256')):
            raise ValueError('The file does not match the signed fingerprint.')
    return dict(address=actual, kind=proof['kind'], content=copy.deepcopy(proof['content']))
