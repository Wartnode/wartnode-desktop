"""Sign a reviewed passing push build on this PC. Contains no publish command."""
import argparse
import json
from pathlib import Path
import sys
import runpy
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from maintainer import github,keys,releases

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-id',required=True,type=int)
    parser.add_argument('--expected-commit',required=True)
    parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args()
    runpy.run_path(str(ROOT/'scripts/audit-runtime.py'))['audit']()
    ci=github.identify(args.run_id,args.expected_commit)
    directory=github.download(ci,args.out)
    if github.identify(args.run_id,args.expected_commit)!=ci:
        raise ValueError('The GitHub run changed during verification; nothing was signed')
    releases.inspect_delivery(directory)
    payload=releases.sign(directory,keys.load(),ci)
    print(json.dumps({'signed':True,'version':payload['version'],'source_commit':payload['source_commit'],
        'purpose':payload['purpose'],'production_release':False,'published':False,
        'run_id':ci['run_id'],'prepared_artifact_id':ci['prepared_artifact']['id']},indent=2))

if __name__=='__main__':main()
