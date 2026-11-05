"""CI-only unsigned preparation after both build jobs; no signing or publishing."""
import argparse
import os
from pathlib import Path
import re
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from maintainer.releases import prepare
from wartnode.release import REPOSITORY

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--directory',required=True,type=Path)
    parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args()
    commit=os.environ.get('GITHUB_SHA','');digest=os.environ.get('DELIVERY_ARTIFACT_DIGEST','')
    if (os.environ.get('GITHUB_REPOSITORY')!=REPOSITORY or os.environ.get('GITHUB_EVENT_NAME')!='push'
            or not re.fullmatch('[0-9a-f]{40}',commit) or not re.fullmatch('[0-9a-f]{64}',digest)):
        raise ValueError('Unsigned preparation requires this repository’s push workflow')
    attempt=int(os.environ['GITHUB_RUN_ATTEMPT']);artifact_id=int(os.environ['DELIVERY_ARTIFACT_ID'])
    if min(attempt,artifact_id)<1:raise ValueError('Invalid CI artifact identity')
    ci={'repository':REPOSITORY,'run_id':int(os.environ['GITHUB_RUN_ID']),'run_attempt':attempt,
        'head_sha':commit,'head_branch':os.environ['GITHUB_REF_NAME'],'event':'push',
        'workflow_path':'.github/workflows/build.yml','delivery_artifact':{'id':artifact_id,
        'name':f'Wartnode-Desktop-{commit}-windows11-x64-development-attempt-{attempt}','digest':'sha256:'+digest}}
    request=prepare(args.directory,args.out,ci)
    print('Prepared unsigned release request for '+request['version']+' at '+commit)

if __name__=='__main__':main()
