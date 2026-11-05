"""Installed version inventory and launcher lifecycle; never removes user data."""
import json
import ctypes
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import time
from .paths import atomic_json
from . import updates
from .process_guard import guard_process


def inventory(folder,version):
    folder=Path(folder)
    files={p.relative_to(folder).as_posix():{'size':p.stat().st_size,'sha256':updates.sha(p)}
           for p in folder.rglob('*') if p.is_file() and p.relative_to(folder).as_posix() not in ('bundle.json','release-envelope.json')}
    record={'schema':1,'application':'wartnode','version':version,'data_schema':updates.DATA_SCHEMA,'files':files}
    atomic_json(folder/'bundle.json',record)
    return record


def verify_bundle(folder,version):
    folder=Path(folder)
    record=updates.read_json(folder/'bundle.json')
    if (record.get('schema')!=1 or record.get('application')!='wartnode' or record.get('version')!=version
            or record.get('data_schema')!=updates.DATA_SCHEMA):
        raise ValueError('The installed application version or data format does not match')
    files={**record['files'],'bundle.json':{'size':(folder/'bundle.json').stat().st_size,'sha256':updates.sha(folder/'bundle.json')}}
    updates.verify_files(folder,files)
    return record


def register(root,version):
    updates.version_key(version)
    root=Path(root).resolve()
    with updates.install_lock(root):
        verify_bundle(root/'versions'/version,version)
        if (root/'active.json').exists():
            current=updates.active(root,allow_legacy=True)
            if version!=current['version'] and updates.version_key(version)<updates.version_key(current.get('highest',current['version'])):
                raise ValueError('A newer Wartnode-Desktop version has already been installed. Use its installer or a compatible rollback.')
            previous=current
        else:previous=None
        highest=max((version,previous.get('highest',previous['version'])),key=updates.version_key) if previous else version
        atomic_json(root/'active.json',{'version':version,'highest':highest,'data_schema':updates.DATA_SCHEMA})
        if previous and previous['version']!=version:
            atomic_json(root/'update-journal.json',{'schema':1,'phase':'complete','previous':previous,
                        'version':version,'data_schema':updates.DATA_SCHEMA,'source':'installer'})


def check_target(root,version=None):
    root=Path(root).resolve()
    data=Path(os.environ.get('LOCALAPPDATA',Path.home()))/'WartNode'
    if root==data.resolve() or data.resolve() in root.parents or root in data.resolve().parents:
        raise ValueError('Choose an application folder separate from the wallet data folder')
    if root.exists():
        markers=(root/'installation.json',root/'installation-retained.json')
        recognized=any(p.is_file() and updates.read_json(p).get('application')=='wartnode' for p in markers)
        if any(p.name!='installation.lock' for p in root.iterdir()) and not (root/'active.json').exists() and not recognized:
            raise ValueError('Choose an empty application folder or the existing Wartnode-Desktop installation')
        with updates.install_lock(root):
            if (root/'active.json').exists():
                current=updates.active(root,allow_legacy=True)
                if version and version!=current['version'] and updates.version_key(version)<updates.version_key(current.get('highest',current['version'])):
                    raise ValueError('A newer Wartnode-Desktop version has already been installed')


def uninstall_versions(root):
    """Remove inventoried application files only; leave unknown files and all data."""
    root=Path(root).resolve()
    with updates.install_lock(root):
        if (root/'active.json').exists():updates.active(root,allow_legacy=True)
        versions=root/'versions'
        for folder in versions.iterdir():
            if not folder.is_dir() or folder.is_symlink() or folder.is_junction():continue
            try:
                updates.version_key(folder.name)
                record=updates.read_json(folder/'bundle.json')
                if record['version']!=folder.name or record['application']!='wartnode':continue
                names=list(record['files'])+['bundle.json','release-envelope.json']
                for name in names:
                    updates.safe_name(name)
                    path=folder/name
                    if path.is_file() and path.resolve().is_relative_to(folder.resolve()):path.unlink()
                for path in sorted(folder.rglob('*'),key=lambda p:len(p.parts),reverse=True):
                    if path.is_dir() and not any(path.iterdir()):path.rmdir()
                if not any(folder.iterdir()):folder.rmdir()
            except (ValueError,KeyError,OSError):continue
        for name in ('active.json','update-journal.json','update-request.json','rollback-request.json'):
            (root/name).unlink(missing_ok=True)
        updates.cleanup_staging(root)
        atomic_json(root/'installation-retained.json',{'schema':1,'application':'wartnode','data_retained':True})


@contextmanager
def desktop_guard():
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateMutexW.restype=ctypes.c_void_p
    kernel.OpenMutexW.restype=ctypes.c_void_p
    kernel.OpenMutexW.argtypes=[ctypes.c_uint32,ctypes.c_bool,ctypes.c_wchar_p]
    deadline=time.monotonic()+30
    while setup:=kernel.OpenMutexW(0x100000,False,'Local\\WartNodeSetup'):
        kernel.CloseHandle(ctypes.c_void_p(setup))
        if time.monotonic()>deadline:raise ValueError('Installation is in progress. Open Wartnode-Desktop when it finishes.')
        time.sleep(.2)
    desktop=kernel.CreateMutexW(None,False,'Local\\WartNodeDesktop')
    if not desktop:raise ValueError('Could not reserve the installed application')
    try:yield
    finally:kernel.CloseHandle(ctypes.c_void_p(desktop))


def launch(root,arguments,trust=None,health_timeout=90):
    root=Path(root).resolve()
    trust=updates.policy() if trust is None else trust
    with updates.install_lock(root),desktop_guard():
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        updates.recover(root)
        while True:
            transition=updates.activate_request(root,trust,build=sys.getwindowsversion().build) if (root/'update-request.json').exists() else None
            current=updates.active(root)
            folder=root/'versions'/current['version']
            try:
                verify_bundle(folder,current['version'])
            except ValueError:
                if transition:updates.recover(root)
                raise
            command=[str(folder/'WartNode.exe'),*arguments,'--installed-root',str(root)]
            if transition:command.extend(['--health-token',transition['token']])
            process=subprocess.Popen(command,cwd=folder)
            job=guard_process(process)
            if transition:
                deadline=time.monotonic()+health_timeout
                while process.poll() is None and time.monotonic()<deadline:
                    if updates.confirm_health(root,transition):break
                    time.sleep(.2)
                else:
                    if process.poll() is None:
                        # The UI owns all children through kill-on-close jobs.
                        process.terminate();process.wait(timeout=30)
                    kernel.CloseHandle(ctypes.c_void_p(job))
                    updates.recover(root)
                    continue
            process.wait()
            kernel.CloseHandle(ctypes.c_void_p(job))
            rollback_file=root/'rollback-request.json'
            if rollback_file.exists():
                updates.rollback(root)
                rollback_file.unlink()
                continue
            if not (root/'update-request.json').exists():
                return process.returncode
