"""Current-user DPAPI custody and password-encrypted portable recovery.

No private key is accepted through an argument or environment variable. The
production CLI uses a dedicated folder outside the repository and build roots.
Python process memory is trusted; this module does not claim memory zeroization
or protection from software already running as this Windows user.
"""
import base64
import csv
import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

DOMAIN=b'Wartnode-Desktop publisher recovery v1'
ROLES=('release','update')
MAX_KEYS=65536


def default_directory():
    if os.name!='nt':raise ValueError('Publisher signing requires Windows')
    return Path(os.environ['LOCALAPPDATA'])/'Wartnode-Desktop/Signing'


def no_links(path):
    path=Path(path).absolute()
    if str(path).startswith('\\\\') or any(p.is_symlink() or p.is_junction() for p in (path,*path.parents)):
        raise ValueError('Signing paths must be local and cannot use links')
    return path


def public_record(keys):
    result={}
    for role in ROLES:
        public=keys[role].public_key().public_bytes_raw()
        result[role]={'public_key':public.hex(),'key_id':hashlib.sha256(public).hexdigest()}
    return {'schema':1,'application':'wartnode','repository':'Wartnode/wartnode-desktop','keys':result}


def generate():return {role:Ed25519PrivateKey.generate() for role in ROLES}


def encode(keys):
    return json.dumps({'schema':1,'keys':{role:keys[role].private_bytes_raw().hex() for role in ROLES}},sort_keys=True).encode()


def decode(raw):
    value=json.loads(raw)
    if not isinstance(value,dict) or value.get('schema')!=1 or set(value.get('keys',{}))!=set(ROLES):
        raise ValueError('Unsupported signing key store')
    return {role:Ed25519PrivateKey.from_private_bytes(bytes.fromhex(value['keys'][role])) for role in ROLES}


def dpapi(raw, *, decrypt=False):
    if os.name!='nt':raise ValueError('Publisher signing requires Windows')
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
    buffer=ctypes.create_string_buffer(raw)
    source=Blob(len(raw),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));output=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.LocalFree.argtypes=[ctypes.c_void_p]
    if decrypt:
        function=crypt.CryptUnprotectData
        function.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
        success=function(ctypes.byref(source),None,None,None,None,1,ctypes.byref(output))
    else:
        function=crypt.CryptProtectData
        function.argtypes=[ctypes.POINTER(Blob),wintypes.LPCWSTR,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
        # CRYPTPROTECT_UI_FORBIDDEN only. Never LOCAL_MACHINE.
        success=function(ctypes.byref(source),'Wartnode-Desktop publisher',None,None,None,1,ctypes.byref(output))
    if not success:raise ValueError('Windows could not protect or unlock the publisher key store')
    try:return ctypes.string_at(output.data,output.size)
    finally:
        ctypes.memset(output.data,0,output.size);kernel.LocalFree(output.data)


def restrict(directory):
    directory=no_links(directory)
    directory.mkdir(parents=True,exist_ok=True)
    system=Path(os.environ['SystemRoot'])/'System32'
    identity=subprocess.check_output([str(system/'whoami.exe'),'/user','/fo','csv','/nh'],text=True,creationflags=0x08000000)
    sid=next(csv.reader(io.StringIO(identity.strip())))[1]
    if not sid.startswith('S-1-'):raise ValueError('Windows account identity is unavailable')
    # Replace the DACL rather than merely adding grants to an existing folder.
    from ctypes import wintypes
    advapi=ctypes.WinDLL('advapi32',use_last_error=True);kernel=ctypes.WinDLL('kernel32')
    descriptor=ctypes.c_void_p();dacl=ctypes.c_void_p();present=wintypes.BOOL();defaulted=wintypes.BOOL()
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p]
    advapi.GetSecurityDescriptorDacl.argtypes=[ctypes.c_void_p,ctypes.POINTER(wintypes.BOOL),ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(wintypes.BOOL)]
    advapi.SetNamedSecurityInfoW.argtypes=[wintypes.LPWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p]
    advapi.SetNamedSecurityInfoW.restype=wintypes.DWORD;kernel.LocalFree.argtypes=[ctypes.c_void_p]
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(f'D:P(A;OICI;FA;;;{sid})(A;OICI;FA;;;SY)',1,ctypes.byref(descriptor),None):
        raise ValueError('Windows could not protect the signing folder')
    try:
        if (not advapi.GetSecurityDescriptorDacl(descriptor,ctypes.byref(present),ctypes.byref(dacl),ctypes.byref(defaulted))
                or not present or not dacl or advapi.SetNamedSecurityInfoW(str(directory),1,0x80000004,None,None,dacl,None)):
            raise ValueError('Windows could not protect the signing folder')
    finally:kernel.LocalFree(descriptor)
    return directory


def atomic_bytes(path,raw, *, replace=False):
    path=no_links(path)
    if path.exists() and not replace:raise ValueError('The signing file already exists; it was kept')
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent,prefix='.publisher-',delete=False) as stream:
            temporary=Path(stream.name);stream.write(raw);stream.flush();os.fsync(stream.fileno())
        if replace:os.replace(temporary,path)
        else:os.rename(temporary,path)  # Windows refuses an existing destination.
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)


def save(directory,keys):
    directory=restrict(directory)
    atomic_bytes(directory/'publisher.dpapi',dpapi(encode(keys)))


def load(directory=None):
    directory=no_links(directory or default_directory())
    path=directory/'publisher.dpapi'
    with path.open('rb') as stream:raw=stream.read(MAX_KEYS+1)
    if len(raw)>MAX_KEYS:raise ValueError('Invalid publisher key store')
    return decode(dpapi(raw,decrypt=True))


def recovery(keys,password):
    if not isinstance(password,str) or len(password)<20:
        raise ValueError('Use a recovery password of at least 20 characters')
    salt,nonce=os.urandom(16),os.urandom(12)
    key=Scrypt(salt=salt,length=32,n=2**18,r=8,p=1).derive(password.encode())
    encrypted=AESGCM(key).encrypt(nonce,encode(keys),DOMAIN)
    return json.dumps({'schema':1,'format':'wartnode-publisher-recovery','kdf':'scrypt-262144-8-1',
        'salt':salt.hex(),'nonce':nonce.hex(),'ciphertext':base64.b64encode(encrypted).decode()},sort_keys=True).encode()


def recover(raw,password):
    if len(raw)>MAX_KEYS:raise ValueError('Invalid publisher recovery file')
    try:
        value=json.loads(raw)
        if value.get('schema')!=1 or value.get('format')!='wartnode-publisher-recovery' or value.get('kdf')!='scrypt-262144-8-1':raise ValueError()
        salt,nonce=bytes.fromhex(value['salt']),bytes.fromhex(value['nonce'])
        encrypted=base64.b64decode(value['ciphertext'],validate=True)
        if len(salt)!=16 or len(nonce)!=12 or not 32<=len(encrypted)<=MAX_KEYS:raise ValueError()
        key=Scrypt(salt=salt,length=32,n=2**18,r=8,p=1).derive(password.encode())
        return decode(AESGCM(key).decrypt(nonce,encrypted,DOMAIN))
    except Exception as error:
        raise ValueError('The recovery file or password could not be verified') from error


def prove(keys):
    challenge=os.urandom(64)
    for role in ROLES:keys[role].public_key().verify(keys[role].sign(challenge),challenge)


def initialize(backup,password):
    directory=default_directory();backup=no_links(backup)
    repository=Path(__file__).resolve().parents[1]
    if backup.is_relative_to(repository) or backup==directory or directory in backup.parents:
        raise ValueError('Save the recovery backup outside the repository and signing folder')
    if (directory/'publisher.dpapi').exists():raise ValueError('Publisher keys already exist; use recovery or inspect their public fingerprints')
    if backup.exists():raise ValueError('Choose a new recovery file; the existing file was kept')
    keys=generate();encrypted=recovery(keys,password)
    recovered=recover(encrypted,password)
    if public_record(keys)!=public_record(recovered):raise ValueError('Recovery verification failed')
    prove(recovered)
    # Write and verify the portable recovery before committing the local store.
    atomic_bytes(backup,encrypted)
    reread=recover(backup.read_bytes(),password);prove(reread)
    save(directory,keys);loaded=load(directory);prove(loaded)
    if public_record(keys)!=public_record(loaded):raise ValueError('Local key verification failed')
    return {**public_record(keys),'dpapi_current_user':True,'restricted_acl':True,
            'encrypted_backup_verified':True,'recovery_signature_verified':True,
            'independent_backup_copy':'Owner must retain the recovery file and password separately.'}


def restore(backup,password):
    directory=default_directory()
    if (directory/'publisher.dpapi').exists():raise ValueError('Existing signing keys were kept; restore requires an account without a key store')
    backup=no_links(backup)
    with backup.open('rb') as stream:raw=stream.read(MAX_KEYS+1)
    recovered=recover(raw,password);prove(recovered);save(directory,recovered)
    loaded=load(directory);prove(loaded)
    if public_record(loaded)!=public_record(recovered):raise ValueError('Recovered key verification failed')
    return {**public_record(loaded),'dpapi_current_user':True,'restricted_acl':True,'recovered':True,'recovery_signature_verified':True}
