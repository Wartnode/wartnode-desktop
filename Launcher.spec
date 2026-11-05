from pathlib import Path
from PyInstaller.utils.win32.versioninfo import VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct
import re
version_text=re.search(r'__version__ = "([^"]+)"',(Path(SPECPATH)/'wartnode/__init__.py').read_text()).group(1)
version_numbers=tuple(int(n) for n in re.findall(r'\d+',version_text))
version_numbers=(version_numbers+(0,0,0,0))[:4]
version_info=VSVersionInfo(ffi=FixedFileInfo(filevers=version_numbers,prodvers=version_numbers,mask=0x3f,flags=0,OS=0x40004,fileType=1,subtype=0,date=(0,0)),kids=[StringFileInfo([StringTable('040904B0',[StringStruct('CompanyName','Wartnode contributors'),StringStruct('FileDescription','Wartnode-Desktop'),StringStruct('ProductName','Wartnode-Desktop'),StringStruct('FileVersion',version_text),StringStruct('ProductVersion',version_text)])]),VarFileInfo([VarStruct('Translation',[1033,1200])])])
# The one-file launcher stays outside version folders and needs no Qt runtime.
a=Analysis(['run-launcher.py'],pathex=[SPECPATH],binaries=[],
           datas=[('assets/update-policy.json','assets')],hiddenimports=[],
           excludes=['PySide6','tkinter'],noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,a.binaries,a.datas,[],name='WartNodeLauncher',
        debug=False,strip=False,upx=False,console=False,version=version_info,icon='assets/warthog.ico')
