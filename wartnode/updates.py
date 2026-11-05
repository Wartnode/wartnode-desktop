"""Authenticated releases and journaled, schema-compatible installed transitions.

No wallet, chain or payment database is opened or modified by this module.
The stable launcher owns activation; the UI may only prepare a request.
"""
import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import time
import urllib.request
from urllib.parse import urlsplit
import uuid
import zipfile
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from .paths import asset_root, atomic_json
from .preflight import MINIMUM_WINDOWS_BUILD, current_windows_build

MAX_ENVELOPE = 1024*1024
MAX_ARCHIVE = 256*1024*1024
MAX_EXPANDED = 512*1024*1024
DATA_SCHEMA = 4  # Fresh shared-password profiles; no legacy wallet conversion.
HEX = re.compile(r'[0-9a-f]{64}\Z')
VERSION = re.compile(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(dev|beta|rc)\.([1-9]\d*))?\Z')


def version_key(version):
    match = VERSION.fullmatch(version) if isinstance(version, str) and len(version)<=64 and version.isascii() else None
    if not match:
        raise ValueError('Invalid application version')
    major, minor, patch, kind, number = match.groups()
    return (int(major), int(minor), int(patch), {'dev':0,'beta':1,'rc':2,None:3}[kind], int(number or 0))


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read_json(path, maximum=MAX_ENVELOPE):
    with Path(path).open('rb') as stream:
        raw = stream.read(maximum+1)
    if len(raw)>maximum:
        raise ValueError('Update information exceeds the size limit')
    return json.loads(raw)


def policy():
    return read_json(asset_root()/'assets/update-policy.json')


def configured(trust):
    return bool(trust.get('enabled',True) and trust.get('public_key') and trust.get('manifest_url'))


def channels(trust):
    """Only publisher-configured feeds can appear in the channel picker."""
    feeds=trust.get('channels')
    if feeds is None:return {trust.get('channel','development'):trust}
    if not isinstance(feeds,dict) or not feeds or set(feeds)-{'beta','stable'}:
        raise ValueError('Invalid configured update channels')
    result={}
    for name,feed in feeds.items():
        if not isinstance(feed,dict) or feed.get('channel')!=name:
            raise ValueError('Invalid configured update channel')
        result[name]=feed
    return result


def channel_policy(trust,requested):
    feeds=channels(trust)
    if len(feeds)==1:return next(iter(feeds.values()))
    if requested not in feeds:raise ValueError('This update channel is not configured')
    return feeds[requested]


def https_url(url, origin=None):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment or parsed.port not in (None,443)):
        raise ValueError('Updates require a valid HTTPS publisher address')
    if origin and parsed.netloc.lower() != urlsplit(origin).netloc.lower():
        raise ValueError('The update download must use the configured publisher host')
    if origin and urlsplit(origin).hostname=='github.com':
        github_url(url)
    return url


def github_url(url):
    parsed=urlsplit(url)
    if (parsed.scheme!='https' or parsed.netloc!='github.com' or parsed.query or parsed.fragment
            or not re.fullmatch(r'/Wartnode/wartnode-desktop/releases/(?:download/[A-Za-z0-9._-]+|latest/download)/[A-Za-z0-9._-]+',parsed.path)):
        raise ValueError('The update must use this project’s GitHub release')
    return url


def safe_name(value):
    if not isinstance(value,str) or not value or len(value)>240 or '\\' in value or ':' in value:
        raise ValueError('Unsafe update file path')
    path = PurePosixPath(value)
    if path.is_absolute() or str(path)!=value or any(
        part in ('.','..') or part.endswith(('.', ' ')) or any(ord(c)<32 for c in part)
        or re.fullmatch(r'(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?',part,re.I)
        for part in path.parts):
        raise ValueError('Unsafe update file path')
    return path


def verify_envelope(envelope, trust, current, *, now=None, build=None, highest=None):
    if not (trust.get('public_key') and trust.get('manifest_url')):
        raise ValueError('Updates are not configured for this development build')
    https_url(trust['manifest_url'])
    try:
        if set(envelope)!= {'payload','signature'}:
            raise ValueError()
        raw = base64.b64decode(envelope['payload'],validate=True)
        signature = base64.b64decode(envelope['signature'],validate=True)
        if len(raw)>MAX_ENVELOPE or len(signature)!=64:
            raise ValueError()
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(trust['public_key']))
        key.verify(signature,raw)
        payload = json.loads(raw)
    except (KeyError, TypeError, ValueError, InvalidSignature) as exc:
        raise ValueError('The update signature could not be verified') from exc
    timestamp = int(time.time()) if now is None else now
    if (payload.get('schema')!=1 or payload.get('application')!='wartnode'
            or payload.get('channel')!=trust['channel'] or payload.get('platform')!='windows-x64'
            or payload.get('data_schema')!=DATA_SCHEMA or payload.get('rollback_schema')!=DATA_SCHEMA):
        raise ValueError('This update is not compatible with this application or its data')
    if (type(payload.get('issued')) is not int or type(payload.get('expires')) is not int
            or payload['issued']>timestamp+300 or payload['expires']<=timestamp
            or payload['expires']<=payload['issued'] or payload['expires']-payload['issued']>31*86400):
        raise ValueError('The update information has expired or its date is invalid')
    if build is None:build=current_windows_build()
    if (type(build) is not int or type(payload.get('minimum_windows_build')) is not int
            or not MINIMUM_WINDOWS_BUILD<=payload['minimum_windows_build']<=build):
        raise ValueError('This update needs a newer Windows 11 version')
    if version_key(payload.get('version'))<=max(version_key(current),version_key(highest or current)):
        raise ValueError('This update version has already been used or is older than the installed version')
    asset = payload.get('asset',{})
    https_url(asset.get('url',''),trust['manifest_url'])
    if type(asset.get('size')) is not int or not 1<=asset['size']<=MAX_ARCHIVE or not HEX.fullmatch(str(asset.get('sha256'))):
        raise ValueError('Invalid update download size or checksum')
    files=payload.get('files')
    if not isinstance(files,dict) or not 3<=len(files)<=4096:
        raise ValueError('Invalid update file list')
    lowered=set()
    total=0
    for name,entry in files.items():
        safe_name(name)
        if name=='release-envelope.json':raise ValueError('Reserved update file path')
        if name.casefold() in lowered or not isinstance(entry,dict):
            raise ValueError('Duplicate update file path')
        lowered.add(name.casefold())
        if (type(entry.get('size')) is not int or not 0<=entry['size']<=MAX_EXPANDED
                or not HEX.fullmatch(str(entry.get('sha256')))):
            raise ValueError('Invalid update file size or checksum')
        total+=entry['size']
    if total>MAX_EXPANDED or not {'WartNode.exe','WartNodeHelper.exe','_internal/components/manifest.json'}<=set(files):
        raise ValueError('Incomplete or oversized update')
    return payload


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise ValueError('Update redirects are not allowed')


class GitHubRedirect(urllib.request.HTTPRedirectHandler):
    """Only GitHub's project-release to release-asset redirect chain is valid."""
    def __init__(self):
        super().__init__();self.hops=0

    def redirect_request(self,request,fp,code,message,headers,newurl):
        self.hops+=1
        old,new=urlsplit(request.full_url),urlsplit(https_url(newurl))
        if self.hops>3 or old.hostname!='github.com':
            raise ValueError('Unexpected GitHub release redirect')
        github_url(request.full_url)
        if new.hostname=='github.com':github_url(newurl)
        elif new.netloc!='release-assets.githubusercontent.com':
            raise ValueError('Unexpected GitHub release download host')
        # No tokens, cookies, credentials or request body are forwarded.
        return urllib.request.Request(newurl,headers={'Accept':'application/octet-stream'},method='GET')


def download(url, destination, limit, expected_size=None, *, budget=300):
    https_url(url)
    redirect=NoRedirect()
    if urlsplit(url).hostname=='github.com':
        github_url(url);redirect=GitHubRedirect()
    from .tls import client_context
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),redirect,
                                     urllib.request.HTTPSHandler(context=client_context()))
    if not 0 < budget <= 300:raise ValueError('Invalid download time limit')
    deadline=time.monotonic()+budget
    try:
        from .network import open_response
        with open_response(opener,url,deadline,timeout=20) as response, Path(destination).open('xb') as output:
            if response.status!=200:
                raise ValueError('The publisher could not provide the update')
            count=0
            while True:
                chunk=response.read1(min(65536,limit-count+1))
                if not chunk:break
                count+=len(chunk)
                if count>limit or time.monotonic()>deadline:
                    raise ValueError('The update download exceeded its size or time limit')
                output.write(chunk)
            output.flush();os.fsync(output.fileno())
        if expected_size is not None and count!=expected_size:
            raise ValueError('The update download is incomplete')
    except OSError as error:
        Path(destination).unlink(missing_ok=True)
        raise ValueError('The update could not be downloaded. Try again.') from error
    except Exception:
        Path(destination).unlink(missing_ok=True)
        raise


def discover(trust,current,highest=None,build=None):
    if not configured(trust):
        raise ValueError('Updates are not configured for this development build')
    import tempfile
    with tempfile.TemporaryDirectory(prefix='wartnode-update-') as temporary:
        path=Path(temporary)/'manifest.json'
        download(trust['manifest_url'],path,MAX_ENVELOPE)
        envelope=read_json(path)
    return envelope,verify_envelope(envelope,trust,current,highest=highest,build=build)


def verify_files(folder,files):
    folder=Path(folder).resolve()
    entries=list(folder.rglob('*'))
    if any(p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()) for p in entries):
        raise ValueError('Linked update files or folders are not allowed')
    actual={p.relative_to(folder).as_posix() for p in entries if p.is_file()}
    # The release receipt is installed by the updater, outside the signed file set.
    actual.discard('release-envelope.json')
    if actual!=set(files):raise ValueError('Update files are missing or unexpected')
    for name,entry in files.items():
        safe_name(name)
        path=folder/name
        if path.is_symlink() or not path.resolve().is_relative_to(folder) or path.stat().st_size!=entry['size'] or sha(path)!=entry['sha256']:
            raise ValueError('An update file could not be verified: '+name)


def stage_archive(archive,destination,payload):
    archive,destination=Path(archive),Path(destination)
    if archive.stat().st_size!=payload['asset']['size'] or sha(archive)!=payload['asset']['sha256']:
        raise ValueError('The update archive checksum does not match')
    total=sum(entry['size'] for entry in payload['files'].values())
    if shutil.disk_usage(destination.parent).free < total+1024**3:
        raise ValueError('There is not enough space to stage this update and keep 1 GiB free')
    destination.mkdir(exist_ok=False)
    seen=set()
    with zipfile.ZipFile(archive) as z:
        if len(z.infolist())>8192:raise ValueError('Too many update archive entries')
        for item in z.infolist():
            raw=item.filename
            if not raw.startswith('WartNode/'):
                raise ValueError('Unexpected update archive layout')
            relative=raw[len('WartNode/'):]
            if item.is_dir():
                if relative:safe_name(relative.rstrip('/'))
                continue
            safe_name(relative)
            if (relative.casefold() in seen or relative not in payload['files']
                    or (item.external_attr>>16)&0o170000==0o120000):
                raise ValueError('Unexpected or duplicate update archive file')
            seen.add(relative.casefold())
            expected=payload['files'][relative]
            if item.file_size!=expected['size']:
                raise ValueError('Update file size does not match')
            target=destination/relative
            target.parent.mkdir(parents=True,exist_ok=True)
            with z.open(item) as incoming,target.open('xb') as outgoing:
                count=0
                while chunk:=incoming.read(1024*1024):
                    count+=len(chunk)
                    if count>expected['size']:raise ValueError('Update file exceeds its size limit')
                    outgoing.write(chunk)
    verify_files(destination,payload['files'])
    component=read_json(destination/'_internal/components/manifest.json')
    if component.get('application')!=payload['version'] or component.get('network')!='mainnet':
        raise ValueError('Update application version or network does not match')


@contextmanager
def install_lock(root):
    path=Path(root)/'installation.lock'
    stream=path.open('a+b')
    try:
        if os.name=='nt':
            import msvcrt
            stream.seek(0,2)
            if stream.tell()==0:stream.write(b'\0');stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError as exc:
        stream.close()
        raise ValueError('WartNode is running or another installation is in progress. Use Exit WartNode first.') from exc
    try:yield
    finally:stream.close()


def active(root,allow_legacy=False):
    value=read_json(Path(root)/'active.json')
    version_key(value['version'])
    if value.get('data_schema')!=DATA_SCHEMA and not (allow_legacy and value.get('data_schema') in (1,2,3)):
        raise ValueError('The installed version uses an incompatible data format')
    return value


def prepare(root,envelope,trust,current,archive=None,build=None):
    staging=Path(root).resolve()/'staging'
    staging.mkdir(exist_ok=True)
    if staging.is_symlink() or staging.is_junction():raise ValueError('The update staging folder is a link')
    with install_lock(staging):
        return _prepare(root,envelope,trust,current,archive,build)


def _prepare(root,envelope,trust,current,archive=None,build=None):
    """Stage only; the launcher activates this request after the UI exits."""
    root=Path(root).resolve()
    if (root/'update-request.json').exists():raise ValueError('An update is already staged. Restart WartNode to finish it.')
    current_record=active(root)
    if current_record['version']!=current:raise ValueError('The installed version changed. Restart WartNode.')
    payload=verify_envelope(envelope,trust,current,highest=current_record.get('highest'),build=build)
    staging=root/'staging'
    staging.mkdir(exist_ok=True)
    _cleanup_staging(root)
    request=staging/uuid.uuid4().hex
    request.mkdir()
    atomic_json(request/'stage.json',{'schema':1,'application':'wartnode'})
    downloaded=request/'package.zip'
    if archive is None:
        total=payload['asset']['size']+sum(e['size'] for e in payload['files'].values())+1024**3
        if shutil.disk_usage(staging).free<total:raise ValueError('There is not enough space to download and stage this update')
        download(payload['asset']['url'],downloaded,MAX_ARCHIVE,payload['asset']['size'])
        archive=downloaded
    stage_archive(archive,request/'app',payload)
    atomic_json(request/'app/release-envelope.json',envelope)
    atomic_json(root/'update-request.json',{'directory':request.name,'from':current,'channel':trust['channel']})
    return payload


def cleanup_staging(root):
    staging=Path(root).resolve()/'staging'
    if not staging.exists():return
    if staging.is_symlink() or staging.is_junction():raise ValueError('The update staging folder is a link')
    with install_lock(staging):_cleanup_staging(root)


def _cleanup_staging(root):
    """Only remove marked updater-owned staging folders, never version/data paths."""
    staging=(Path(root).resolve()/'staging')
    if not staging.is_dir():return
    if staging.is_symlink() or staging.is_junction():raise ValueError('The update staging folder is a link')
    for folder in staging.iterdir():
        if (not re.fullmatch('[0-9a-f]{32}',folder.name) or not folder.is_dir()
                or folder.is_symlink() or folder.is_junction() or not (folder/'stage.json').is_file()):continue
        if read_json(folder/'stage.json')!={'schema':1,'application':'wartnode'}:continue
        target=folder.resolve()
        if target.parent!=staging.resolve():raise ValueError('Unsafe staging cleanup path')
        shutil.rmtree(target)


def recover(root,checkpoint=lambda _:None):
    root=Path(root)
    journal=root/'update-journal.json'
    if not journal.exists():return None
    value=read_json(journal)
    if value['phase'] in ('prepared','activating','awaiting_health'):
        old=dict(value['previous'])
        version_key(old['version'])
        old['highest']=max((old.get('highest',old['version']),value['version']),key=version_key)
        if old.get('data_schema')!=DATA_SCHEMA:raise ValueError('Automatic rollback is not compatible with these data files')
        if not (root/'versions'/old['version']/'WartNode.exe').is_file():
            raise ValueError('The previous application is missing. Keep the data folder and repair the installation.')
        atomic_json(root/'active.json',old)
        value['phase']='rolled_back'
        value['reason']='Startup was interrupted or could not be confirmed'
        atomic_json(journal,value)
        (root/'update-request.json').unlink(missing_ok=True)
        checkpoint('recovered')
    return value


def activate_request(root,trust,build=None,checkpoint=lambda _:None):
    root=Path(root).resolve()
    request_file=root/'update-request.json'
    if not request_file.exists():return None
    request=read_json(request_file)
    feeds=channels(trust)
    channel=request.get('channel',trust.get('channel'))
    if channel not in feeds:raise ValueError('The staged update channel is not configured')
    trust=feeds[channel]
    if not re.fullmatch('[0-9a-f]{32}',str(request.get('directory'))):
        raise ValueError('Invalid staged update location')
    folder=root/'staging'/request['directory']/'app'
    envelope=read_json(folder/'release-envelope.json')
    old=active(root)
    if request.get('from')!=old['version']:raise ValueError('The staged update no longer matches the installed version')
    payload=verify_envelope(envelope,trust,old['version'],highest=old.get('highest'),build=build)
    verify_files(folder,payload['files'])
    destination=root/'versions'/payload['version']
    if destination.exists():raise ValueError('This update version already has an installation folder')
    journal={'schema':1,'phase':'prepared','previous':old,'version':payload['version'],
             'token':uuid.uuid4().hex,'data_schema':DATA_SCHEMA}
    atomic_json(root/'update-journal.json',journal)
    checkpoint('prepared')
    os.replace(folder,destination)
    journal['phase']='activating'
    atomic_json(root/'update-journal.json',journal)
    checkpoint('activating')
    atomic_json(root/'active.json',{'version':payload['version'],'highest':payload['version'],'data_schema':DATA_SCHEMA})
    checkpoint('pointer')
    journal['phase']='awaiting_health'
    atomic_json(root/'update-journal.json',journal)
    request_file.unlink()
    checkpoint('awaiting_health')
    return journal


def mark_healthy(root,version,token):
    root=Path(root)
    journal=read_json(root/'update-journal.json')
    if journal['phase']!='awaiting_health' or journal['version']!=version or journal['token']!=token:
        raise ValueError('The update startup check no longer matches')
    atomic_json(root/('healthy-'+token+'.json'),{'version':version,'token':token})


def confirm_health(root,journal):
    root=Path(root)
    path=root/('healthy-'+journal['token']+'.json')
    if not path.exists():return False
    health=read_json(path)
    if health!={'version':journal['version'],'token':journal['token']}:return False
    journal={**journal,'phase':'complete'}
    atomic_json(root/'update-journal.json',journal)
    path.unlink()
    try:cleanup_staging(root)
    except (OSError,ValueError):pass  # Cleanup can retry; a healthy app must stay open.
    return True


def rollback_available(root):
    if not root:return False
    try:
        journal=read_json(Path(root)/'update-journal.json')
        previous=journal['previous'];version_key(previous['version'])
        return (journal.get('phase')=='complete' and journal.get('data_schema')==DATA_SCHEMA
                and previous.get('data_schema')==DATA_SCHEMA
                and (Path(root)/'versions'/previous['version']/'WartNode.exe').is_file())
    except (OSError,ValueError,KeyError,TypeError):return False


def rollback(root,checkpoint=lambda _:None):
    root=Path(root)
    journal=read_json(root/'update-journal.json')
    if journal['phase']!='complete' or journal.get('data_schema')!=DATA_SCHEMA:
        raise ValueError('No compatible previous version is available')
    current=active(root)
    previous=journal['previous']
    version_key(previous['version'])
    if previous.get('data_schema')!=current['data_schema']:
        raise ValueError('The previous version cannot use the current data format')
    if not (root/'versions'/previous['version']/'WartNode.exe').is_file():
        raise ValueError('The previous application files are missing')
    # Retain the high-water mark so discovery cannot replay an old release.
    atomic_json(root/'active.json',{**previous,'highest':current.get('highest',current['version'])})
    checkpoint('rollback')
    atomic_json(root/'update-journal.json',{**journal,'phase':'rolled_back','reason':'User requested rollback'})
