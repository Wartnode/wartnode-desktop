"""Native Qt layout at logical 100/150/200% sizes, synthetic offline profile.

Offscreen screenshots supplement the on-screen UIA review; they do not claim a
Windows settings change, Narrator speech, a multi-monitor move, or a clean VM.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
parser=argparse.ArgumentParser();parser.add_argument('--scale',type=float)
options=parser.parse_args()
if options.scale is None:
    from wartnode.processes import hidden_options
    records=[]
    for scale in (1,1.5,2):
        env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QT_SCALE_FACTOR=str(scale))
        result=subprocess.run([sys.executable,__file__,'--scale',str(scale)],env=env,capture_output=True,text=True,timeout=90,**hidden_options())
        if result.returncode:raise RuntimeError(result.stdout+result.stderr)
        records.append(json.loads(result.stdout))
    receipt={'all_passed':True,'kind':'Qt layout renders, synthetic offline data','scales':records,
             'windows_scaling_changed':False,'narrator_speech_tested':False,'multi_monitor_tested':False}
    output=ROOT/'evidence/dashboard';output.mkdir(exist_ok=True)
    (output/'qt-layout.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps(receipt,indent=2));raise SystemExit(0)

from PySide6.QtCore import QRect,Qt
from PySide6.QtWidgets import QApplication,QDialog,QPushButton,QScrollArea
from PySide6.QtTest import QTest
from wartnode.paths import data_root
from wartnode.signer import Signer
from wartnode.ui import Window,SetupDialog
from wartnode import __version__

app=QApplication([])
def drain():
    for _ in range(4):app.processEvents();QTest.qWait(20)

output=ROOT/'evidence/dashboard'/('scale-'+str(options.scale));output.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='dashboard-layout-',dir=ROOT/'work') as temporary:
    directory=data_root(temporary);signer=Signer(directory)
    signer.call('setup',{'password':'synthetic-layout-password'})
    for n,name in ((1,'Everyday'),(2,'Mining rewards'),(3,'Savings')):
        signer.call('import',{'kind':'private','secret':f'{n:064x}','label':name})
    signer.lock();signer.store.con.close()
    width,height=int(1920/options.scale),int(1040/options.scale)
    screen=SimpleNamespace(availableGeometry=lambda:QRect(0,0,width,height))
    with patch('wartnode.ui.QApplication.primaryScreen',return_value=screen):
        window=Window(directory)
        window.show();drain();window.timer.stop()
        for name in ('Overview','Node','Wallet','Mine','Explorer','Settings'):
            window.navigate(('Overview','Node','Wallet','Mine','Explorer','Settings').index(name));drain()
            page=window.stack.currentWidget()
            assert window.width()<=width and window.height()<=height,(name,window.size(),width,height)
            assert page.horizontalScrollBar().maximum()==0,(options.scale,name,'horizontal overflow',page.horizontalScrollBar().maximum(),page.viewport().width(),page.widget().minimumSizeHint().width())
            assert window.grab().save(str(output/(name.lower()+'.png')))
        dialogs=[window.send_dialog,window.receive_dialog,window.mining_settings_dialog,SetupDialog(directory,{},window)]
        for dialog in dialogs:
            dialog.show();drain()
            assert dialog.height()+32<=height,(dialog.windowTitle(),'too tall',dialog.height(),height)
            assert dialog.width()+16<=width,(dialog.windowTitle(),'too wide')
            scroll=dialog.findChild(QScrollArea)
            assert scroll is not None
            buttons=dialog.findChildren(QPushButton)
            if buttons:scroll.ensureWidgetVisible(buttons[-1]);drain()
            assert dialog.grab().save(str(output/(dialog.windowTitle().replace(' ','-').lower()+'.png')))
            dialog.hide()
    window.exiting=True;window.timer.stop();window.idle.stop()
    window.signer.close();window.pool.waitForDone(30000);window.lifecycle_pool.waitForDone(30000)
    window.tray.hide();window.close();drain()
print(json.dumps({'application':__version__,'scale':options.scale,'logical_desktop':[width,height],
                  'pages':6,'dialogs':4,'all_passed':True,'directory':str(output.relative_to(ROOT))}))
