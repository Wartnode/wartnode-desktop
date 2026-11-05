"""Per-user desktop integration; never starts a node or stores credentials."""
import os
from pathlib import Path
import subprocess

RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_NAME = 'Wartnode-Desktop'


def remembered_profile():
    if os.name!='nt':return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\WartNode\Desktop') as key:
            value,kind=winreg.QueryValueEx(key,'ProfileDirectory')
        path=Path(value)
        return path if kind==winreg.REG_SZ and path.is_absolute() and path.is_dir() else None
    except (OSError,ValueError,TypeError):return None


def remember_profile(directory):
    if os.name!='nt':return
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,r'Software\WartNode\Desktop',0,winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key,'ProfileDirectory',0,winreg.REG_SZ,str(Path(directory).resolve()))


def startup_command(installed_root, directory):
    if not installed_root:
        raise ValueError('Install Wartnode-Desktop to enable Windows startup')
    launcher = Path(installed_root).resolve()/'WartNode.exe'
    if not launcher.is_file():
        raise ValueError('The installed launcher is missing. Repair the installation first.')
    command = subprocess.list2cmdline([str(launcher), '--data-dir', str(Path(directory).resolve()), '--no-node'])
    # Windows documents a 260-character limit for Run values.
    if len(command)>260:raise ValueError('The application and profile paths are too long for Windows startup')
    return command


def set_startup(enabled, installed_root, directory):
    # https://learn.microsoft.com/windows/win32/setupapi/run-and-runonce-registry-keys
    if os.name!='nt':raise ValueError('Windows startup is unavailable on this system')
    import winreg
    command=startup_command(installed_root,directory) if enabled else None
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,RUN_KEY,0,winreg.KEY_SET_VALUE) as key:
        if enabled:winreg.SetValueEx(key,RUN_NAME,0,winreg.REG_SZ,command)
        else:
            try:winreg.DeleteValue(key,RUN_NAME)
            except FileNotFoundError:pass


def startup_enabled(installed_root,directory):
    if os.name!='nt' or not installed_root:return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,RUN_KEY) as key:
            value,kind=winreg.QueryValueEx(key,RUN_NAME)
        return kind==winreg.REG_SZ and value==startup_command(installed_root,directory)
    except (OSError,ValueError):return False


def remove_startup_for_installation(root):
    """Uninstall removes only a registration pointing at this installation."""
    if os.name!='nt':return
    import winreg
    launcher=str(Path(root).resolve()/'WartNode.exe')
    prefix=subprocess.list2cmdline([launcher])+' '
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,RUN_KEY) as key:
            value,kind=winreg.QueryValueEx(key,RUN_NAME)
        if not (kind==winreg.REG_SZ and isinstance(value,str) and value.casefold().startswith(prefix.casefold())):
            return
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,RUN_KEY,0,winreg.KEY_QUERY_VALUE|winreg.KEY_SET_VALUE) as key:
            value,kind=winreg.QueryValueEx(key,RUN_NAME)
            if kind==winreg.REG_SZ and isinstance(value,str) and value.casefold().startswith(prefix.casefold()):
                winreg.DeleteValue(key,RUN_NAME)
    except FileNotFoundError:pass


def contrast_enabled():
    # SPI_GETHIGHCONTRAST: https://learn.microsoft.com/windows/win32/winauto/high-contrast-parameter
    if os.name!='nt':return False
    import ctypes
    from ctypes import wintypes
    class HighContrast(ctypes.Structure):
        _fields_=[('cbSize',wintypes.UINT),('dwFlags',wintypes.DWORD),('lpszDefaultScheme',wintypes.LPWSTR)]
    value=HighContrast();value.cbSize=ctypes.sizeof(value)
    return bool(ctypes.windll.user32.SystemParametersInfoW(0x42,value.cbSize,ctypes.byref(value),0) and value.dwFlags&1)


def system_dark():
    if os.name!='nt':return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
            return winreg.QueryValueEx(key,'AppsUseLightTheme')[0]==0
    except OSError:return False
