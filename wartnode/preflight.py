"""Windows compatibility and product prerequisites, without changing the system."""
import os
import platform
import sys

MINIMUM_WINDOWS_BUILD = 22000
WALLET_SCHEMA = 1
CACHE_SCHEMA = 2


def current_windows_build():
    """Actual Windows build for manifest checks; conservative offline floor."""
    return sys.getwindowsversion().build if os.name == 'nt' else MINIMUM_WINDOWS_BUILD


def windows_compatibility(build=None, architecture=None, workstation=None):
    if build is None:
        if os.name != 'nt':
            return {'supported': False, 'reason': 'Wartnode-Desktop requires Windows 11 x64 (build 22000 or newer).'}
        version = sys.getwindowsversion()
        build, workstation = version.build, version.product_type == 1
    architecture = architecture or os.environ.get('PROCESSOR_ARCHITEW6432') or platform.machine()
    supported = (type(build) is int and build>=MINIMUM_WINDOWS_BUILD
                 and architecture.lower() in ('amd64','x86_64') and workstation is True)
    return {'supported': supported, 'build': build, 'architecture': architecture,
            'reason': '' if supported else 'Wartnode-Desktop requires Windows 11 x64 (build 22000 or newer). ARM and Windows Server are not supported.'}


def cpu_info():
    name = platform.processor() or 'Unknown CPU'
    if os.name == 'nt':
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as key:
            name = winreg.QueryValueEx(key, 'ProcessorNameString')[0].strip()
    return {'name': name, 'threads': os.cpu_count() or 1, 'architecture': platform.machine()}
