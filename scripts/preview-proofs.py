"""Open the signing prototype with a disposable wallet, separate from real profiles."""
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from wartnode.paths import data_root
    from wartnode.proof_ui import ProofDialog
    from wartnode.theme import apply
    from wartnode.ui import Window
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    apply(app)
    work = ROOT / 'work'
    work.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='proof-preview-', dir=work) as directory:
        window = Window(data_root(directory), autostart=False)
        window.timer.stop()
        timer = QTimer()
        def launch():
            if window.tasks:return
            timer.stop()
            window.signer.call('setup', password='prototype-preview')
            created = window.signer.call('create', label='Disposable preview address')
            window.wallets = window.signer.call('list')['wallets']
            window.profile_ready = window.profile_unlocked = True
            window.address = created['address']
            created.clear()
            dialog = window.proof_dialog = ProofDialog(window)
            dialog.setWindowTitle('WartNode prototype · disposable wallet · do not fund')
            dialog.message.setPlainText('I control this Warthog address.\n\nThis is a disposable signing preview. Do not send funds to this address.\nChallenge: wartnode-desktop-prototype')
            dialog.finished.connect(app.quit)
            dialog.show()
        timer.timeout.connect(launch)
        timer.start(30)
        print('Disposable preview wallet. No real profile is opened. Password: prototype-preview')
        try:
            return app.exec()
        finally:
            timer.stop()
            window.timer.stop();window.idle.stop()
            window.exiting = True
            window.signer.close()
            window.pool.waitForDone(30000)
            window.lifecycle_pool.waitForDone(30000)
            window.tray.hide();window.close()
            app.processEvents()


if __name__ == '__main__':raise SystemExit(main())
