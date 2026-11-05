from pathlib import Path
from PyInstaller.utils.win32.versioninfo import VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct
import re
version_text=re.search(r'__version__ = "([^"]+)"',(Path(SPECPATH)/'wartnode/__init__.py').read_text()).group(1)
version_numbers=tuple(int(n) for n in re.findall(r'\d+',version_text))
version_numbers=(version_numbers+(0,0,0,0))[:4]
version_info=VSVersionInfo(ffi=FixedFileInfo(filevers=version_numbers,prodvers=version_numbers,mask=0x3f,flags=0,OS=0x40004,fileType=1,subtype=0,date=(0,0)),kids=[StringFileInfo([StringTable('040904B0',[StringStruct('CompanyName','Wartnode contributors'),StringStruct('FileDescription','Wartnode-Desktop'),StringStruct('ProductName','Wartnode-Desktop'),StringStruct('FileVersion',version_text),StringStruct('ProductVersion',version_text)])]),VarFileInfo([VarStruct('Translation',[1033,1200])])])
# Build one native GUI and a private console helper with shared runtime files.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

root=Path(SPECPATH)
datas=[(str(root/'assets'),'assets'),(str(root/'components'),'components'),(str(root/'notices'),'notices')]
datas+=collect_data_files('mnemonic')
a=Analysis([str(root/'run.py')],pathex=[str(root),str(root/'vendor/wart_dapp')],
    binaries=[],datas=datas,hiddenimports=['wallet_settings','history','bundled_nodes'],
    hookspath=[],hooksconfig={},runtime_hooks=[],excludes=['tkinter','PySide6.QtWebEngineCore','PySide6.QtWebEngineWidgets'],noarchive=False)
pyz=PYZ(a.pure)
gui=EXE(pyz,a.scripts,[],exclude_binaries=True,name='WartNode',debug=False,bootloader_ignore_signals=False,
    strip=False,upx=False,console=False,version=version_info,icon=str(root/'assets/warthog.ico'))
helper=EXE(pyz,a.scripts,[],exclude_binaries=True,name='WartNodeHelper',debug=False,bootloader_ignore_signals=False,
    strip=False,upx=False,console=True,version=version_info,icon=str(root/'assets/warthog.ico'))
coll=COLLECT(gui,helper,a.binaries,a.datas,strip=False,upx=False,name='WartNode')
