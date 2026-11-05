"""Stable per-user launcher; its own files are changed only by the installer."""
import argparse
import ctypes
import json
from pathlib import Path
import sys
from wartnode import installed, updates


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path)
    parser.add_argument('--register')
    parser.add_argument('--check-installation',action='store_true')
    parser.add_argument('--target-version')
    parser.add_argument('--uninstall-files',action='store_true')
    args,remaining=parser.parse_known_args()
    root=(args.root or Path(sys.executable).parent).resolve()
    try:
        if args.check_installation:
            installed.check_target(root,args.target_version)
            return 0
        if args.uninstall_files:
            installed.uninstall_versions(root)
            return 0
        if args.register:
            installed.register(root,args.register)
            return 0
        return installed.launch(root,remaining)
    except Exception as exc:
        message=str(exc) if isinstance(exc,ValueError) else 'WartNode could not start. Repair the installation and keep your data folder.'
        if args.register or args.check_installation or args.uninstall_files:
            # Installer/silent callers use the exit code, without a blocking dialog.
            return 2
        ctypes.windll.user32.MessageBoxW(None,message,'WartNode',0x10)
        return 1


if __name__=='__main__':raise SystemExit(main())
