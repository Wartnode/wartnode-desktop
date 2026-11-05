"""Project release verification using a separately trusted, pinned public key."""
import base64
import hashlib
import json
from pathlib import Path
import re
import time
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from . import updates
from .paths import asset_root,atomic_json

DOMAIN=b'WARTNODE-DESKTOP-RELEASE-V1\x00'
REPOSITORY='Wartnode/wartnode-desktop'


def policy():return updates.read_json(asset_root()/'assets/release-policy.json')


def names(version):
    updates.version_key(version)
    prefix='Wartnode-Desktop-'+version
    return {'installer':prefix+'-windows-x64-setup.exe','portable':prefix+'-windows-x64.zip','source':prefix+'-source.zip'}


def canonical(payload):return json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()


def verify_ci(ci,source_commit):
    if (not isinstance(ci,dict) or ci.get('repository')!=REPOSITORY or ci.get('head_sha')!=source_commit
            or ci.get('workflow_path')!='.github/workflows/build.yml' or ci.get('event')!='push'
            or ci.get('conclusion')!='success' or ci.get('status')!='completed'
            or ci.get('head_branch')!='main'
            or type(ci.get('workflow_id')) is not int or ci['workflow_id']<1
            or type(ci.get('run_id')) is not int or ci['run_id']<1
            or type(ci.get('run_attempt')) is not int or ci['run_attempt']<1):
        raise ValueError('Release does not identify a completed trusted build')
    expected={'delivery_artifact':f'Wartnode-Desktop-{source_commit}-windows11-x64-development-attempt-{ci["run_attempt"]}',
              'prepared_artifact':f'release-preparation-{source_commit}-attempt-{ci["run_attempt"]}'}
    for kind,name in expected.items():
        record=ci.get(kind,{})
        if (not isinstance(record,dict) or type(record.get('id')) is not int or record['id']<1 or record.get('name')!=name
                or not re.fullmatch('sha256:[0-9a-f]{64}',str(record.get('digest','')))):
            raise ValueError('Release does not identify its exact build artifacts')
    if ci['delivery_artifact']['id']==ci['prepared_artifact']['id']:raise ValueError('Build artifacts must have distinct identities')


def release_purpose(version, channel):
    updates.version_key(version)
    if channel == 'beta' and re.fullmatch(r'\d+\.\d+\.\d+-beta\.[1-9]\d*', version):
        return 'beta'
    if channel == 'development' and '-dev.' in version:
        return 'rehearsal'
    raise ValueError('The release version does not match its channel')


def verify(envelope,trust, *, now=None,highest=None,build=26100):
    try:
        if not isinstance(envelope,dict) or set(envelope)!={'payload','signature'}:raise ValueError()
        raw=base64.b64decode(envelope['payload'],validate=True);signature=base64.b64decode(envelope['signature'],validate=True)
        if len(raw)>updates.MAX_ENVELOPE or len(signature)!=64:raise ValueError()
        trusted=trust['keys']['release'];public=bytes.fromhex(trusted['public_key'])
        if hashlib.sha256(public).hexdigest()!=trusted['key_id']:raise ValueError()
        Ed25519PublicKey.from_public_bytes(public).verify(signature,DOMAIN+raw)
        payload=json.loads(raw)
        if not isinstance(payload,dict):raise ValueError()
    except (KeyError,TypeError,ValueError,InvalidSignature) as error:
        raise ValueError('The release signature could not be verified') from error
    now=int(time.time()) if now is None else now
    if (payload.get('schema')!=1 or payload.get('application')!='wartnode' or payload.get('repository')!=REPOSITORY
            or trust.get('repository')!=REPOSITORY or payload.get('key_id')!=trusted['key_id']
            or payload.get('platform')!='windows-x64' or payload.get('data_schema')!=updates.DATA_SCHEMA
            or payload.get('rollback_schema')!=updates.DATA_SCHEMA
            or payload.get('channel') not in ('development','beta','stable')
            or payload.get('purpose')!=release_purpose(payload.get('version'), payload.get('channel'))
            or payload.get('production_release') is not False):
        raise ValueError('Unsupported release identity or compatibility')
    if (type(payload.get('issued')) is not int or type(payload.get('expires')) is not int
            or payload['issued']>now+300 or payload['expires']<=now or payload['expires']<=payload['issued']
            or payload['expires']-payload['issued']>31*86400):
        raise ValueError('The release information has expired or its date is invalid')
    if type(payload.get('minimum_windows_build')) is not int or not 26100<=payload['minimum_windows_build']<=build:
        raise ValueError('This release requires a newer Windows 11 build')
    expected_names=names(payload.get('version'))
    if highest is not None and updates.version_key(payload['version'])<=updates.version_key(highest):
        raise ValueError('This release version has already been verified or is older')
    if not re.fullmatch('[0-9a-f]{40}',str(payload.get('source_commit',''))):raise ValueError('Invalid release source identity')
    artifacts=payload.get('artifacts')
    if not isinstance(artifacts,dict) or set(artifacts)!=set(expected_names):raise ValueError('Incomplete release artifacts')
    for kind,name in expected_names.items():
        entry=artifacts[kind]
        if (not isinstance(entry,dict) or entry.get('name')!=name or type(entry.get('size')) is not int
                or not 1<=entry['size']<=1024**3 or not updates.HEX.fullmatch(str(entry.get('sha256','')))):
            raise ValueError('Invalid release artifact')
    update=payload.get('update_manifest',{})
    if (update.get('name')!='update-envelope.json' or type(update.get('size')) is not int
            or not 1<=update['size']<=updates.MAX_ENVELOPE or not updates.HEX.fullmatch(str(update.get('sha256','')))):
        raise ValueError('Invalid release update metadata')
    verify_ci(payload.get('ci'),payload['source_commit'])
    return payload


def verify_files(payload,directory,trust,update_trust):
    root=Path(directory).resolve()
    for entry in [*payload['artifacts'].values(),payload['update_manifest']]:
        path=root/entry['name']
        if (path.is_symlink() or path.is_junction() or not path.is_file() or path.resolve().parent!=root
                or path.stat().st_size!=entry['size'] or updates.sha(path)!=entry['sha256']):
            raise ValueError('Release file verification failed: '+entry['name'])
    if update_trust.get('public_key')!=trust['keys']['update']['public_key']:
        raise ValueError('Release and update trust do not agree')
    update=updates.verify_envelope(updates.read_json(root/'update-envelope.json'),update_trust,'0.0.0')
    asset=payload['artifacts']['portable']
    if (update['version']!=payload['version'] or update['asset']['sha256']!=asset['sha256']
            or update['asset']['size']!=asset['size'] or update['channel']!=payload['channel']):
        raise ValueError('The release and update describe different packages')
    return payload


def verify_directory(envelope_path,directory, *, state=None,trust=None,update_trust=None):
    trust=policy() if trust is None else trust
    update_trust=updates.policy() if update_trust is None else update_trust
    envelope=updates.read_json(envelope_path);digest=hashlib.sha256(canonical(envelope)).hexdigest()
    previous=updates.read_json(state) if state is not None and Path(state).exists() else None
    if previous is not None and (not isinstance(previous,dict) or previous.get('application')!='wartnode-release-verification'):
        raise ValueError('The verification state is invalid; the original file was kept')
    highest=previous['version'] if previous and previous.get('manifest_sha256')!=digest else None
    payload=verify(envelope,trust,highest=highest)
    verify_files(payload,directory,trust,update_trust)
    if state is not None:
        path=Path(state);path.parent.mkdir(parents=True,exist_ok=True)
        atomic_json(path,{'application':'wartnode-release-verification','version':payload['version'],'manifest_sha256':digest})
    return payload
