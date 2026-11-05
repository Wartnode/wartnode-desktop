"""Compatibility entry: protected PC signing now binds the complete CI release."""
from pathlib import Path
import runpy

if __name__=='__main__':
    runpy.run_path(str(Path(__file__).with_name('sign-release-run.py')),run_name='__main__')
