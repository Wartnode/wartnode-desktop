"""Run tests with disposable data confined to this workspace."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
import unittest

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--legacy',action='store_true')
args=parser.parse_args()
temp=root/'work/test-tmp'
temp.mkdir(parents=True,exist_ok=True)
tempfile.tempdir=str(temp)
os.environ['TMP']=str(temp)
os.environ['TEMP']=str(temp)
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
target=root/'vendor/wart_dapp' if args.legacy else root
os.chdir(target)
sys.path.insert(0,str(target))
sys.path.insert(0,str(target/'tests'))
suite=unittest.defaultTestLoader.discover(str(target/'tests'))
result=unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
