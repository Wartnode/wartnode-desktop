"""Windows compatibility and product prerequisites, without changing the system."""
import os
import platform
import sys

MINIMUM_WINDOWS_BUILD = 26100
WALLET_SCHEMA = 1
CACHE_SCHEMA = 2


def windows_compatibility(build=None, architecture=None, workstation=None):
    if build is None:
        if os.name != 'nt':
            return {'supported': False, 'reason': 'Wartnode-Desktop requires Windows 11 x64 (24H2 or later).'}
        version = sys.getwindowsversion()
        build, workstation = version.build, version.product_type == 1
    architecture = architecture or os.environ.get('PROCESSOR_ARCHITEW6432') or platform.machine()
    supported = (type(build) is int and build>=MINIMUM_WINDOWS_BUILD
                 and architecture.lower() in ('amd64','x86_64') and workstation is True)
    return {'supported': supported, 'build': build, 'architecture': architecture,
            'reason': '' if supported else 'Wartnode-Desktop requires Windows 11 x64 (24H2 or later). ARM and Windows Server are not supported.'}


def cpu_info():
    name = platform.processor() or 'Unknown CPU'
    if os.name == 'nt':
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as key:
            name = winreg.QueryValueEx(key, 'ProcessorNameString')[0].strip()
    return {'name': name, 'threads': os.cpu_count() or 1, 'architecture': platform.machine()}
