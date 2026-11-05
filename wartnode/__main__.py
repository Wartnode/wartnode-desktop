import argparse
import sys
from .paths import data_root
from .settings import SettingsError


def main():
    parser = argparse.ArgumentParser(description='Wartnode-Desktop')
    parser.add_argument('--data-dir')
    parser.add_argument('--signer')
    parser.add_argument('--signal', type=int)
    parser.add_argument('--no-node', action='store_true', help='Open the desktop without starting the node')
    parser.add_argument('--installed-root')
    parser.add_argument('--health-token')
    parser.add_argument('--inspect-chain',help=argparse.SUPPRESS)
    parser.add_argument('--initialize-node',help=argparse.SUPPRESS)
    parser.add_argument('--chain-source',help=argparse.SUPPRESS)
    parser.add_argument('--smoke-seconds',type=int,help=argparse.SUPPRESS)
    parser.add_argument('--verify-release',help='Verify a signed project release manifest and its files')
    parser.add_argument('--release-directory')
    parser.add_argument('--verification-state')
    args = parser.parse_args()
    if args.verify_release:
        import json
        import os
        from pathlib import Path
        from .release import verify_directory
        try:
            state=Path(args.verification_state) if args.verification_state else Path(os.environ.get('LOCALAPPDATA',Path.home()))/'Wartnode-Desktop/Verification/release-state.json'
            result=verify_directory(args.verify_release,args.release_directory or Path(args.verify_release).parent,state=state)
            print(json.dumps({'verified':True,'version':result['version'],'source_commit':result['source_commit'],
                'purpose':result['purpose'],'production_release':result['production_release']}))
            return 0
        except (ValueError,OSError,KeyError,TypeError) as error:
            print(str(error),file=sys.stderr);return 1
    if args.inspect_chain or args.initialize_node:
        import json
        from .chain_import import inspect_chain
        try:
            if args.inspect_chain:result=inspect_chain(args.inspect_chain)
            else:
                from .processes import Runtime
                if not args.data_dir:raise ValueError('An explicit data directory is required')
                from PySide6.QtCore import QLockFile
                directory=data_root(args.data_dir)
                initialization_lock=QLockFile(str(directory/'desktop.lock'))
                initialization_lock.setStaleLockTime(0)
                if not initialization_lock.tryLock(100):
                    raise ValueError('Close Wartnode-Desktop before initializing this profile from the command line')
                result=Runtime(directory).initialize_node(args.initialize_node,args.chain_source)
            print(json.dumps(result))
            return 0
        except (ValueError,OSError) as error:
            print(str(error),file=sys.stderr)
            return 1
    if args.signal:
        from .processes import signal_console
        return 0 if signal_console(args.signal) else 1
    if args.signer:
        from .signer import serve
        serve(args.signer)
        return 0
    from PySide6.QtWidgets import QApplication, QMessageBox, QFileDialog
    from PySide6.QtCore import QLockFile
    from .ui import Window
    from .theme import apply
    app = QApplication(sys.argv)
    app.setApplicationName('Wartnode-Desktop')
    app.setOrganizationName('WartNode')
    app.setQuitOnLastWindowClosed(False)
    from .preflight import windows_compatibility
    compatibility = windows_compatibility()
    if not compatibility['supported']:
        QMessageBox.critical(None,'Windows version not supported',compatibility['reason'])
        return 1
    from .desktop_preferences import remembered_profile,remember_profile
    directory = data_root(args.data_dir or remembered_profile())
    selected_fresh=False
    from .profile import guard_profile
    while True:
        try:
            guard_profile(directory/'wallet.db')
            break
        except ValueError as error:
            QMessageBox.information(None,'Choose a fresh profile',str(error))
            chosen=QFileDialog.getExistingDirectory(None,'Choose a fresh Wartnode profile folder')
            if not chosen:return 1
            directory=data_root(chosen)
            selected_fresh=True
    if selected_fresh and not args.data_dir:
        try:remember_profile(directory)
        except OSError:QMessageBox.information(None,'Profile location','Windows could not remember this profile folder. Open it next time with --data-dir "'+str(directory)+'".')
    lock = QLockFile(str(directory/'desktop.lock'))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None,'Wartnode-Desktop is already running','Open Wartnode-Desktop from the tray icon. Another window is already using this data folder.')
        return 1
    apply(app)
    try:
        window = Window(directory,autostart=False,installed_root=args.installed_root)
    except SettingsError as exc:
        from . import settings
        dialog=QMessageBox(QMessageBox.Icon.Warning,'Settings need recovery',str(exc),parent=None)
        dialog.setInformativeText('Reset setup uses the default blockchain folder. The original settings file and existing blockchain files are kept.')
        restore=dialog.addButton('Restore previous settings',QMessageBox.ButtonRole.AcceptRole)
        restore.setEnabled((directory/'settings.previous.json').exists())
        reset=dialog.addButton('Reset setup',QMessageBox.ButtonRole.DestructiveRole)
        cancel=dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(cancel)
        dialog.exec()
        if dialog.clickedButton() not in (restore,reset):return 1
        try:
            settings.recover_settings(directory,dialog.clickedButton()==restore)
            window=Window(directory,autostart=False,installed_root=args.installed_root)
        except (ValueError,OSError) as error:
            QMessageBox.critical(None,'Settings could not be restored',str(error))
            return 1
    except ValueError as exc:
        QMessageBox.critical(None,'Wartnode-Desktop could not start',str(exc))
        return 1
    window.show()
    if not args.smoke_seconds:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0,window.onboard_profile)
    if args.smoke_seconds:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(max(1,min(args.smoke_seconds,300))*1000,window.exit_application)
    if args.health_token and args.installed_root:
        from PySide6.QtCore import QTimer
        from . import updates, __version__
        def healthy(result,error):
            if result is not None and not error:
                updates.mark_healthy(args.installed_root,__version__,args.health_token)
        QTimer.singleShot(1000,lambda:window.submit(lambda:window.signer.call('list'),healthy))
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
