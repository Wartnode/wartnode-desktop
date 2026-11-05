"""Prepare and sign exact passing CI artifacts; never build or publish them."""
import base64
import hashlib
import json
from pathlib import Path
import shutil
import time
import zipfile
from wartnode import updates,release
from wartnode.paths import atomic_json
from .keys import public_record


def descriptor(path):
    path=Path(path)
    return {'name':path.name,'size':path.stat().st_size,'sha256':updates.sha(path)}


def portable(path):
    files={};folded=set()
    with zipfile.ZipFile(path) as archive:
        entries=[i for i in archive.infolist() if not i.is_dir()]
        if not 3<=len(entries)<=4096 or sum(i.file_size for i in entries)>updates.MAX_EXPANDED:
            raise ValueError('The portable package exceeds its limits')
        for item in entries:
            if not item.filename.startswith('WartNode/'):raise ValueError('Unexpected portable layout')
            name=item.filename[len('WartNode/'):];updates.safe_name(name)
            if name.casefold() in folded or (item.external_attr>>16)&0o170000==0o120000:
                raise ValueError('Duplicate or linked portable entry')
            folded.add(name.casefold());digest=hashlib.sha256();count=0
            with archive.open(item) as stream:
                while block:=stream.read(65536):
                    count+=len(block)
                    if count>item.file_size:raise ValueError('Invalid portable size')
                    digest.update(block)
            if count!=item.file_size:raise ValueError('Truncated portable entry')
            files[name]={'size':count,'sha256':digest.hexdigest()}
        def read(name):
            full='WartNode/'+name
            if archive.getinfo(full).file_size>updates.MAX_ENVELOPE:raise ValueError('Oversized package metadata')
            return json.loads(archive.read(full))
        manifest=read('_internal/components/manifest.json');trust=read('_internal/assets/update-policy.json')
        publisher=read('_internal/assets/release-policy.json');context=read('provenance/build-context.json');bundle=read('bundle.json')
        inventory={name:entry for name,entry in files.items() if name!='bundle.json'}
        if bundle.get('files')!=inventory or bundle.get('data_schema')!=updates.DATA_SCHEMA:
            raise ValueError('Portable inventory verification failed')
        if (manifest.get('application')!=context.get('application') or bundle.get('version')!=manifest.get('application')
                or manifest.get('network')!='mainnet' or context.get('data_schema')!=updates.DATA_SCHEMA):
            raise ValueError('Portable version or compatibility mismatch')
    return {'files':files,'manifest':manifest,'trust':trust,'publisher':publisher,'context':context}


def inspect_delivery(directory):
    root=Path(directory).resolve();delivery=updates.read_json(root/'delivery.json')
    if delivery.get('all_passed') is not True or delivery.get('production_release') is not False:
        raise ValueError('A passing development delivery is required')
    expected=release.names(delivery['application'])
    if set(delivery.get('artifacts',{}))!=set(expected.values()):raise ValueError('Incomplete development delivery')
    artifacts={}
    for kind,name in expected.items():
        path=root/name
        if path.is_symlink() or not path.is_file() or not 1<=path.stat().st_size<=1024**3:
            raise ValueError('Invalid delivery file')
        artifacts[kind]=descriptor(path)
        if artifacts[kind]['sha256']!=delivery['artifacts'][name]:raise ValueError('Delivery checksum mismatch')
    package=portable(root/expected['portable'])
    if package['context'].get('source_commit')!=delivery['commit'] or package['manifest']['application']!=delivery['application']:
        raise ValueError('Delivery source identity mismatch')
    with zipfile.ZipFile(root/expected['source']) as source:
        def read(name):
            full='WartNode-source/'+name
            if source.getinfo(full).file_size>8*updates.MAX_ENVELOPE:raise ValueError('Oversized source metadata')
            return json.loads(source.read(full))
        build=read('provenance/build.json')
        if (build.get('commit')!=delivery['commit'] or build.get('application')!=delivery['application']
                or build.get('checks',{}).get('all_passed') is not True
                or build.get('installer_sha256')!=artifacts['installer']['sha256']
                or build.get('windows_sha256')!=artifacts['portable']['sha256']
                or read('assets/release-policy.json')!=package['publisher']
                or read('assets/update-policy.json')!=package['trust']):
            raise ValueError('Corresponding source or check receipt mismatch')
    return delivery,artifacts,package


def prepare(directory,output,ci):
    delivery,artifacts,package=inspect_delivery(directory)
    if ci['head_sha']!=delivery['commit'] or ci['repository']!=release.REPOSITORY:
        raise ValueError('Build and preparation identities differ')
    output=Path(output)
    output.mkdir(parents=True,exist_ok=False)
    for entry in artifacts.values():shutil.copyfile(Path(directory)/entry['name'],output/entry['name'])
    shutil.copyfile(Path(directory)/'delivery.json',output/'delivery.json')
    request={'schema':1,'application':'wartnode','purpose':release.release_purpose(delivery['application'], package['trust']['channel']),'production_release':False,
             'version':delivery['application'],'source_commit':delivery['commit'],'artifacts':artifacts,
             'channel':package['trust']['channel'],'ci':ci}
    atomic_json(output/'release-request.json',request)
    return request


def sign(directory,keys,ci):
    root=Path(directory);request=updates.read_json(root/'release-request.json')
    delivery,artifacts,package=inspect_delivery(root)
    if (request.get('schema')!=1 or request.get('application')!='wartnode' or request.get('purpose')!=release.release_purpose(delivery['application'], package['trust']['channel'])
            or request.get('production_release') is not False or request.get('artifacts')!=artifacts
            or request.get('version')!=delivery['application'] or request.get('source_commit')!=delivery['commit']
            or request.get('channel')!=package['trust']['channel']):
        raise ValueError('Release request and exact delivery do not agree')
    for name,value in request['ci'].items():
        if ci.get(name)!=value:raise ValueError('Release request and GitHub build do not agree')
    if package['publisher']!=public_record(keys) or package['trust']['public_key']!=public_record(keys)['keys']['update']['public_key']:
        raise ValueError('Local signing keys do not match the final package’s pinned keys')
    if ci['head_sha']!=delivery['commit']:raise ValueError('Signing source identity mismatch')
    release.verify_ci(ci,delivery['commit'])
    now=int(time.time());version=delivery['application'];asset=artifacts['portable']
    update={'schema':1,'application':'wartnode','channel':request['channel'],'platform':'windows-x64',
            'version':version,'data_schema':updates.DATA_SCHEMA,'rollback_schema':updates.DATA_SCHEMA,
            'minimum_windows_build':26100,'issued':now,'expires':now+14*86400,'files':package['files'],
            'asset':{'url':f'https://github.com/{release.REPOSITORY}/releases/download/v{version}/{asset["name"]}',
                     'size':asset['size'],'sha256':asset['sha256']}}
    raw=release.canonical(update)
    update_envelope={'payload':base64.b64encode(raw).decode(),'signature':base64.b64encode(keys['update'].sign(raw)).decode()}
    updates.verify_envelope(update_envelope,package['trust'],'0.0.0')
    if (root/'update-envelope.json').exists() or (root/'release-envelope.json').exists():
        raise ValueError('Signed metadata already exists; it was kept')
    atomic_json(root/'update-envelope.json',update_envelope)
    payload={'schema':1,'application':'wartnode','repository':release.REPOSITORY,
        'key_id':public_record(keys)['keys']['release']['key_id'],'version':version,'channel':request['channel'],
        'platform':'windows-x64','data_schema':updates.DATA_SCHEMA,'rollback_schema':updates.DATA_SCHEMA,
        'minimum_windows_build':26100,'purpose':release.release_purpose(delivery['application'], package['trust']['channel']),'production_release':False,'source_commit':delivery['commit'],
        'issued':now,'expires':now+14*86400,'artifacts':artifacts,'ci':ci,
        'update_manifest':descriptor(root/'update-envelope.json')}
    raw=release.canonical(payload)
    envelope={'payload':base64.b64encode(raw).decode(),'signature':base64.b64encode(keys['release'].sign(release.DOMAIN+raw)).decode()}
    release.verify(envelope,package['publisher'])
    atomic_json(root/'release-envelope.json',envelope)
    release.verify_directory(root/'release-envelope.json',root,trust=package['publisher'],update_trust=package['trust'])
    return payload
