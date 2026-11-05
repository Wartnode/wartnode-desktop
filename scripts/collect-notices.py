"""Regenerate dependency notices from the exact installed, hash-locked inputs."""
import hashlib
from importlib import metadata
import json
from pathlib import Path
import re
import shutil
ROOT=Path(__file__).resolve().parents[1]
destination=(ROOT/'notices/dependencies').resolve()
if not destination.is_relative_to(ROOT.resolve()):raise ValueError('Invalid notice destination')
destination.mkdir(parents=True,exist_ok=True)
expected={'manifest.json'};packages={};direct={}
for lock in (ROOT/'scripts/ci/requirements.lock',ROOT/'scripts/ci/build-requirements.lock'):
    for name,version in re.findall(r'^([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+)',lock.read_text(),re.M):packages[name]=version
    for name,url,digest in re.findall(r'^([A-Za-z0-9_.-]+) @ (\S+) --hash=sha256:([0-9a-f]{64})$',lock.read_text(),re.M):
        direct[name]=(url,digest)
        packages[name]=metadata.version(name)
records=[]
for name,version in sorted(packages.items()):
    distribution=metadata.distribution(name)
    if distribution.version!=version:raise ValueError('Installed notice input differs from lock: '+name)
    package_source='https://pypi.org/project/'+name+'/'+version+'/'
    if name in direct:
        origin=json.loads(distribution.read_text('direct_url.json') or '{}')
        url,digest=direct[name]
        if origin.get('url')!=url or origin.get('archive_info',{}).get('hashes',{}).get('sha256')!=digest:
            raise ValueError('Installed direct dependency differs from source lock: '+name)
        package_source=url
    files={}
    for entry in distribution.files or []:
        if not any(part.lower().endswith('.dist-info') for part in entry.parts):continue
        if not ('licenses' in entry.parts or entry.name.lower().startswith(('license','copying','copyright','notice','authors'))):continue
        source=Path(distribution.locate_file(entry)).resolve()
        relative=Path(*entry.parts)
        if relative.is_absolute() or '..' in relative.parts:raise ValueError('Unsafe dependency notice path')
        target=destination/relative
        if not target.resolve().is_relative_to(destination) or any(p.is_symlink() or p.is_junction() for p in (target,*target.parents)):
            raise ValueError('Linked dependency notice path')
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        label=relative.as_posix();expected.add(label);files[label]=hashlib.sha256(target.read_bytes()).hexdigest()
    if not files and name=='warthog-py':
        # This upstream commit declares MIT but ships no separate license file.
        # Retain its metadata verbatim alongside the attributed MIT notice.
        if distribution.metadata.get('License')!='MIT':raise ValueError('SDK license declaration changed')
        label='warthog_py-'+version+'.dist-info/METADATA'
        target=destination/label;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(distribution.read_text('METADATA'),encoding='utf-8',newline='\n')
        expected.add(label);files[label]=hashlib.sha256(target.read_bytes()).hexdigest()
        notice=ROOT/'notices/Warthog-Python-SDK.txt'
        label='warthog_py-'+version+'.dist-info/NOTICE'
        target=destination/label;shutil.copyfile(notice,target)
        expected.add(label);files[label]=hashlib.sha256(target.read_bytes()).hexdigest()
    if not files:raise ValueError('No license text found for locked input: '+name)
    records.append({'name':name,'version':version,'license_files':files,'package_source':package_source})
# Only this dedicated generated directory is pruned, after absolute containment
# checks. Historical packaged/source ZIPs and other notice directories are kept.
for path in sorted(destination.rglob('*'),key=lambda p:len(p.parts),reverse=True):
    if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(destination):raise ValueError('Unsafe notice cleanup path')
    if path.is_file() and path.relative_to(destination).as_posix() not in expected:path.unlink()
    elif path.is_dir() and not any(path.iterdir()):path.rmdir()
(destination/'manifest.json').write_text(json.dumps({'schema':1,'scope':'Locked runtime/build input licenses','packages':records},indent=2)+'\n',encoding='utf-8')
print('Collected license texts for '+str(len(records))+' locked inputs')
