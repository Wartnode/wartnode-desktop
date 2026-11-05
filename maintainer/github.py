"""Read-only GitHub provenance checks. No signing secrets or publish operation."""
import json
import os
from pathlib import Path
import re
import subprocess
import zipfile
from wartnode import release,updates


def api(path, *, output=None):
    command=['gh','api','--method','GET',path]
    result=subprocess.run(command,stdout=output or subprocess.PIPE,stderr=subprocess.PIPE,timeout=300,
                          **({'creationflags':0x08000000} if os.name=='nt' else {}))
    if result.returncode:raise ValueError('GitHub could not provide the requested build record')
    if output is None:return json.loads(result.stdout)


def identify(run_id,expected_commit):
    if type(run_id) is not int or run_id<1 or not re.fullmatch('[0-9a-f]{40}',expected_commit):raise ValueError('Invalid build identity')
    base=f'/repos/{release.REPOSITORY}/actions'
    run=api(f'{base}/runs/{run_id}');workflow=api(f'{base}/workflows/build.yml')
    if (run.get('status')!='completed' or run.get('conclusion')!='success' or run.get('event')!='push'
            or run.get('head_sha')!=expected_commit or run.get('workflow_id')!=workflow['id']
            or run.get('path')!='.github/workflows/build.yml'
            or run.get('repository',{}).get('full_name')!=release.REPOSITORY
            or run.get('head_repository',{}).get('full_name')!=release.REPOSITORY
            or run.get('head_branch')!='main'):
        raise ValueError('Signing requires a successful trusted push build at the reviewed commit')
    attempt=run['run_attempt'];records=[]
    for page in range(1,21):
        current=api(f'{base}/runs/{run_id}/artifacts?per_page=100&page={page}')['artifacts'];records.extend(current)
        if len(current)<100:break
    else:raise ValueError('Too many artifacts for a bounded release request')
    def artifact(name):
        matches=[a for a in records if a['name']==name and a.get('expired') is False]
        if len(matches)!=1:raise ValueError('An exact current-attempt build artifact is required')
        record=matches[0]
        if (record.get('workflow_run',{}).get('id')!=run_id or record['workflow_run'].get('head_sha')!=expected_commit
                or not re.fullmatch('sha256:[0-9a-f]{64}',str(record.get('digest','')))):
            raise ValueError('Invalid artifact provenance')
        return {key:record[key] for key in ('id','name','digest')}
    delivery=artifact(f'Wartnode-Desktop-{expected_commit}-windows11-x64-development-attempt-{attempt}')
    prepared=artifact(f'release-preparation-{expected_commit}-attempt-{attempt}')
    return {'repository':release.REPOSITORY,'run_id':run_id,'run_attempt':attempt,'head_sha':expected_commit,
        'head_branch':run['head_branch'],'workflow_path':run['path'],'workflow_id':run['workflow_id'],
        'event':run['event'],'status':run['status'],'conclusion':run['conclusion'],
        'delivery_artifact':delivery,'prepared_artifact':prepared}


def download(ci,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    transport=output/'github-artifact.zip';record=ci['prepared_artifact']
    try:
        with transport.open('xb') as stream:api(f'/repos/{release.REPOSITORY}/actions/artifacts/{record["id"]}/zip',output=stream)
        if updates.sha(transport)!=record['digest'].split(':')[1]:raise ValueError('GitHub artifact digest mismatch')
        with zipfile.ZipFile(transport) as archive:
            entries=archive.infolist()
            if len(entries)>8 or sum(i.file_size for i in entries)>1536*1024**2:raise ValueError('Oversized prepared artifact')
            request_item=archive.getinfo('release-request.json')
            if request_item.file_size>updates.MAX_ENVELOPE:raise ValueError('Oversized release request')
            request=json.loads(archive.read(request_item))
            expected={*release.names(request['version']).values(),'release-request.json','delivery.json'}
            if {i.filename for i in entries}!=expected or len(entries)!=len(expected):raise ValueError('Unexpected prepared artifact layout')
            for item in entries:
                updates.safe_name(item.filename)
                if len(Path(item.filename).parts)!=1 or (item.external_attr>>16)&0o170000==0o120000:
                    raise ValueError('Unsafe prepared artifact entry')
                count=0
                with archive.open(item) as incoming,(output/item.filename).open('xb') as outgoing:
                    while block:=incoming.read(65536):
                        count+=len(block)
                        if count>item.file_size:raise ValueError('Invalid prepared file size')
                        outgoing.write(block)
                    if count!=item.file_size:raise ValueError('Truncated prepared file')
        return output
    finally:transport.unlink(missing_ok=True)
