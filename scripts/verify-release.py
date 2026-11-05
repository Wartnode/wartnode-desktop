"""Run only from a trusted checkout; do not trust a key supplied by a download."""
import argparse
import json
import os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from wartnode.release import verify_directory

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('manifest',type=Path)
    parser.add_argument('--directory',type=Path)
    parser.add_argument('--state',type=Path,default=Path(os.environ.get('LOCALAPPDATA',Path.home()))/'Wartnode-Desktop/Verification/release-state.json')
    args=parser.parse_args()
    result=verify_directory(args.manifest,args.directory or args.manifest.parent,state=args.state)
    print(json.dumps({'verified':True,'version':result['version'],'source_commit':result['source_commit'],
                      'purpose':result['purpose'],'production_release':result['production_release']},indent=2))

if __name__=='__main__':main()
