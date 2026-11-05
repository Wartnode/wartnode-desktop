"""Local publisher key setup. Passwords stay in this native dialog and memory."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from maintainer import keys
from wartnode.paths import atomic_json


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--public',action='store_true')
    parser.add_argument('--setup',action='store_true')
    parser.add_argument('--recover',action='store_true')
    parser.add_argument('--receipt',type=Path,default=ROOT/'work/step5/key-custody-public.json')
    args=parser.parse_args()
    if args.public:
        print(json.dumps(keys.public_record(keys.load()),indent=2));return
    if not (args.setup or args.recover) or args.setup and args.recover:parser.error('Choose --setup, --recover or --public')
    from PySide6.QtWidgets import QApplication,QDialog,QVBoxLayout,QLabel,QLineEdit,QPushButton,QFileDialog,QFormLayout
    app=QApplication([]);dialog=QDialog();dialog.setWindowTitle('Wartnode-Desktop release signing');dialog.resize(610,360)
    layout=QVBoxLayout(dialog)
    intro=QLabel('Restore the existing publisher keys from an encrypted recovery file. Existing signing keys will never be replaced.' if args.recover else 'Create release and update signing keys for this Windows account. Save an encrypted recovery backup and keep its password separately. This does not publish a release.')
    intro.setWordWrap(True);layout.addWidget(intro)
    form=QFormLayout();password=QLineEdit();confirm=QLineEdit()
    for field in (password,confirm):field.setEchoMode(QLineEdit.EchoMode.Password)
    form.addRow('Recovery password',password)
    if not args.recover:form.addRow('Confirm password (20+ characters)',confirm)
    layout.addLayout(form)
    destination=QLineEdit();destination.setReadOnly(True);layout.addWidget(destination)
    choose=QPushButton('Choose recovery file…');layout.addWidget(choose)
    def select():
        if args.recover:path,_=QFileDialog.getOpenFileName(dialog,'Open encrypted signing-key recovery file','','Signing-key recovery (*.wartkeys)')
        else:path,_=QFileDialog.getSaveFileName(dialog,'Save encrypted signing-key recovery file',str(Path.home()/'Documents/Wartnode-Desktop-signing-recovery.wartkeys'),'Signing-key recovery (*.wartkeys)')
        if path:destination.setText(path)
    choose.clicked.connect(select)
    status=QLabel('Keep a separate copy of this recovery file. Losing the Windows account and the recovery backup would lose release signing authority.');status.setWordWrap(True);layout.addWidget(status)
    create=QPushButton('Restore signing keys' if args.recover else 'Create keys and backup');layout.addWidget(create)
    def initialize():
        if not args.recover and password.text()!=confirm.text():status.setText('The passwords do not match.');return
        if not destination.text():status.setText('Choose a recovery file.');return
        create.setEnabled(False);app.processEvents()
        try:
            receipt=(keys.restore if args.recover else keys.initialize)(Path(destination.text()),password.text())
            args.receipt.parent.mkdir(parents=True,exist_ok=True);atomic_json(args.receipt,receipt)
            password.clear();confirm.clear();dialog.accept()
        except Exception as error:
            status.setText(str(error) if isinstance(error,ValueError) else 'Key setup failed. Existing files were kept.')
            create.setEnabled(True)
    create.clicked.connect(initialize)
    from PySide6.QtCore import QTimer
    idle=QTimer(dialog);idle.setSingleShot(True);idle.timeout.connect(dialog.reject);idle.start(300000)
    try:return 0 if dialog.exec()==QDialog.DialogCode.Accepted else 2
    finally:password.clear();confirm.clear()


if __name__=='__main__':raise SystemExit(main())
