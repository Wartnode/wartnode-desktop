"""Native desktop shell. All network, signing and process waits run off the UI thread."""
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import time
import threading
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QColor, QCloseEvent, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLayout, QLineEdit, QMainWindow,
    QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QStackedWidget,
    QSystemTrayIcon, QTabWidget, QCheckBox, QProgressDialog, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)
from . import __version__, theme
from .paths import asset_root
from .processes import Runtime, SignerClient
from .rpc import NodeRPC
from .connection import client as connection_client
from .explorer import Explorer
from . import settings, updates
from .mining import PRESETS, worker_threads
from .dashboard import ConnectionStatus, ProfileBalance, SampleHistory
from .wallet_ui import WalletActions
from .statistics import ProcessSampler, network_statistics, hashrate_text, difficulty_text
from .mining_ui import MiningActions
from .explorer_ui import ExplorerActions
from .preferences_ui import PreferencesActions
from .activity_ui import ActivityActions
from .proof_ui import ProofActions
from .desktop_preferences import startup_enabled


class SetupDialog(QDialog):
    def __init__(self, directory, devices, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Initialize node')
        self.resize(660, 520)
        self.directory=Path(directory)
        layout=QVBoxLayout(self)
        layout.addWidget(label('Initialize node','title'))
        layout.addWidget(label('Choose where to keep the blockchain and how to initialize it. The node starts only when you select Start node.'))
        layout.addWidget(label('Blockchain folder'))
        self.storage=QLineEdit(str(settings.chain_directory(directory)));self.storage.setReadOnly(True)
        self.storage.setAccessibleName('Blockchain folder');layout.addWidget(self.storage)
        layout.addWidget(button('Choose folder…',self.choose_folder))
        self.method=QComboBox();self.method.addItem('Sync from genesis','fresh');self.method.addItem('Import chain database','import')
        self.method.setAccessibleName('Initialization method');layout.addWidget(self.method)
        self.source=QLineEdit();self.source.setReadOnly(True);self.source.setAccessibleName('Source chain database')
        layout.addWidget(self.source)
        self.source_valid=False;self.source_check=0
        self.source_status=label('',muted=True);layout.addWidget(self.source_status)
        self.source_button=button('Choose database…',self.choose_source);layout.addWidget(self.source_button)
        self.trust=QCheckBox('I trust the source of this chain database.');layout.addWidget(self.trust)
        layout.addWidget(label('Use a standalone database from a cleanly stopped node or a SQLite backup. The original file is kept. Import checks do not independently validate its entire history.',muted=True))
        self.space=label('',muted=True);layout.addWidget(self.space)
        self.continue_button=button('Initialize',self.accept,True)
        actions=QHBoxLayout();actions.addWidget(button('Cancel',self.reject));actions.addWidget(self.continue_button);layout.addLayout(actions)
        self.method.currentIndexChanged.connect(self.update_space);self.trust.toggled.connect(self.update_space)
        self.update_space()
        from .widgets import scroll_dialog
        scroll_dialog(self)

    def choose_folder(self):
        path=QFileDialog.getExistingDirectory(self,'Choose an empty blockchain folder',self.storage.text())
        if path:self.storage.setText(path);self.update_space()

    def choose_source(self):
        path,_=QFileDialog.getOpenFileName(self,'Choose a standalone chain database','','SQLite chain database (*.db3 *.db *.sqlite);;All files (*)')
        if not path:return
        self.source.setText(path);self.source_valid=False;self.trust.setChecked(False)
        self.source_check+=1;generation=self.source_check
        self.source_status.setText('Checking database…');self.update_space()
        from .chain_import import inspect_chain
        def checked(result,error):
            if generation!=self.source_check:return
            self.source_valid=bool(result)
            self.source_status.setText(f"Detected block height: {result['height']:,}" if result else error)
            self.update_space()
        self.parent().submit(lambda:inspect_chain(path),checked,quiet=True)

    def update_space(self):
        import shutil
        importing=self.method.currentData()=='import'
        for widget in (self.source,self.source_button,self.trust):widget.setEnabled(importing)
        path=Path(self.storage.text())
        while not path.exists():path=path.parent
        free=shutil.disk_usage(path).free
        source=Path(self.source.text())
        size=source.stat().st_size if importing and self.source.text() and source.is_file() else 0
        self.space.setText(f'Available: {free/1024**3:.1f} GiB · '+(f'File: {size/1024**3:.2f} GiB · ' if importing else '')+'1 GiB free headroom required.')
        self.continue_button.setEnabled(free>=size+1024**3 and (not importing or (self.source_valid and size>0 and self.trust.isChecked())))


class TaskSignals(QObject):
    done = Signal(object, object)


class Task(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function, self.signals = function, TaskSignals()

    def run(self):
        try:
            self.signals.done.emit(self.function(), None)
        except Exception as exc:
            self.signals.done.emit(None, str(exc)[:300])


from .widgets import label, button, table, populate, format_wart, LogView


class Window(WalletActions,MiningActions,ExplorerActions,PreferencesActions,ActivityActions,ProofActions,QMainWindow):
    def __init__(self, directory, autostart=False, installed_root=None):
        super().__init__()
        self.directory = Path(directory)
        self.installed_root = Path(installed_root) if installed_root else None
        self.runtime=Runtime(directory)
        self.preferences = settings.load(directory)
        if self.installed_root:
            registered=startup_enabled(self.installed_root,self.directory)
            if registered!=self.preferences['launch_at_startup']:
                self.preferences=settings.save(directory,launch_at_startup=registered)
        self.init_busy=False
        self.mode_busy=False
        self.mining_busy=False
        self.editing_mining=False
        self.hash_history=SampleHistory()
        self.init_cancel=threading.Event()
        self.update_envelope = None
        self.update_checking=False
        self.last_update_check=0
        self.signer = SignerClient(directory)
        self.runtime.vanity.persist = self.signer.vanity_call
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(4)
        self.lifecycle_pool=QThreadPool(self)
        self.lifecycle_pool.setMaxThreadCount(1)
        self.tasks = set()
        self.wallets, self.address = [], None
        self.profile_contacts=[]
        self.public_address=None
        self.address_page=0
        self.visible_addresses=[]
        self.address_balances={}
        self.wallet_generation=0
        self.balance_polling=False
        self.balance_offset=0
        self.balance_rows=[]
        self.balance_cycle_started=0
        self.profile_balance=None
        self.activity_data=None
        self.activity_error=None
        self.activity_busy=False
        self.activity_turn=0
        self.journal_busy=False
        self.journal_turn=0
        self.payment_rows={}
        self.network_data=None
        self.network_polling=False
        self.last_network_attempt=0.0
        self.resources_polling=False
        self.node_sampler=ProcessSampler()
        self.miner_sampler=ProcessSampler()
        self.miner_resources={}
        self.profile_ready = False
        self.profile_unlocked = False
        self.last_profile_touch = 0.0
        self.devices = {}
        self.session_generation = 0
        self.payment_generation=0
        self.secret_dialogs = {}
        self.explorer_generation = 0
        self.explorer_page = None
        self.blocks_paused=False
        self.explorer_selected=None
        self.latest_block_rows=[]
        self.rolling_rows=[]
        self.polling, self.indexing, self.exiting, self.closing = False, False, False, False
        self.session_notifications = False
        self.setWindowTitle("Wartnode-Desktop")
        available = QApplication.primaryScreen().availableGeometry()
        # Keep the frame inside the desktop, including small logical desktops
        # at higher Windows scaling. Dense pages provide their own scrolling.
        width, height = available.width()-16, available.height()-32
        self.setMinimumSize(min(980,width),min(680,height))
        self.resize(min(1240,width),min(820,height))
        mark = QPixmap(64, 64)
        mark.fill(QColor("#0d0f11"))
        painter = QPainter(mark)
        painter.setPen(QColor("#fdb913"))
        font = painter.font()
        font.setPixelSize(42)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(mark.rect(), Qt.AlignmentFlag.AlignCenter, "W")
        painter.end()
        self.setWindowIcon(QIcon(mark))
        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(205)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(20, 24, 20, 16)
        side.addWidget(label("Wartnode", "brand"))
        side.addWidget(label("Desktop", muted=True))
        side.addSpacing(30)
        self.stack = QStackedWidget()
        self.nav_buttons = []
        for index, name in enumerate(("Overview", "Node", "Wallet", "Mine", "Explorer", "Settings")):
            nav = button(name, lambda _=False, i=index: self.navigate(i))
            nav.setProperty("nav", True)
            nav.setCheckable(True)
            nav.setAccessibleName(name)
            side.addWidget(nav)
            self.nav_buttons.append(nav)
        side.addStretch()
        self.mode_label=label("Lite wallet" if self.preferences["connection_mode"]=="lite" else "Full node",muted=True)
        side.addWidget(self.mode_label)
        side.addWidget(label(f"v{__version__}", muted=True))
        layout.addWidget(sidebar)
        workspace = QWidget()
        content = QVBoxLayout(workspace)
        content.setContentsMargins(32, 24, 32, 24)
        content.setSpacing(20)
        top = QHBoxLayout()
        self.context = label("Full node · Stopped", muted=True)
        self.lock_button = button("Profile locked", lambda:self.lock_wallet() if self.profile_unlocked else self.ensure_profile())
        top.addWidget(self.context, 1)
        top.addWidget(self.lock_button)
        content.addLayout(top)
        content.addWidget(self.stack, 1)
        self.notice = label(self.runtime.recovery_error, muted=True)
        content.addWidget(self.notice)
        layout.addWidget(workspace, 1)
        self.build_overview()
        self.build_node()
        self.build_wallet()
        self.build_mine()
        self.build_explorer()
        self.build_settings()
        self.navigate(settings.PAGES.index(self.preferences['start_page']))
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip("Wartnode-Desktop")
        menu = QMenu()
        menu.addAction("Open Wartnode-Desktop", self.show_normal)
        menu.addAction("Lock wallet", self.lock_wallet)
        menu.addAction("Exit Wartnode-Desktop", self.exit_application)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_normal() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self.tray.show()
        theme.apply(QApplication.instance(),self.preferences['appearance'])
        self.schedule_update_checks()
        self.refresh_wallets()
        self.update_state(self.runtime.state)
        if self.preferences["connection_mode"]=="local":self.submit(self.runtime.detect_devices, self.devices_loaded)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(5000)
        self.idle = QTimer(self)
        self.idle.setSingleShot(True)
        self.idle.timeout.connect(self.lock_wallet)
        self.refresh_lock_policy()
        QApplication.instance().installEventFilter(self)
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            self.wts = ctypes.WinDLL('wtsapi32', use_last_error=True)
            self.wts.WTSRegisterSessionNotification.argtypes = [wintypes.HWND, wintypes.DWORD]
            self.wts.WTSUnRegisterSessionNotification.argtypes = [wintypes.HWND]
            self.session_notifications = bool(self.wts.WTSRegisterSessionNotification(int(self.winId()), 0))
            if not self.session_notifications:
                self.notice.setText('Automatic locking when Windows locks is unavailable. Lock your wallet before leaving the computer.')
        # Launch and upgrades never initialize or start a node implicitly.

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if event.type() in (QEvent.Type.KeyPress, QEvent.Type.MouseButtonPress):
            self.refresh_lock_policy()
            if self.profile_unlocked and time.monotonic()-self.last_profile_touch > 15:
                self.last_profile_touch = time.monotonic()
                def touched(result, error):
                    if error == 'Unlock your profile first':self.lock_wallet()
                self.submit(lambda:self.signer.call('touch'),touched,quiet=True)
        return super().eventFilter(obj, event)

    def refresh_lock_policy(self):
        minutes = self.preferences['auto_lock_minutes']
        if minutes:self.idle.start(minutes*60000)
        else:self.idle.stop()

    def nativeEvent(self, event_type, message):
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message in (0x001A,0x031A):
                QTimer.singleShot(0,lambda:theme.apply(QApplication.instance(),self.preferences['appearance']))
            if msg.message == 0x02B1 and msg.wParam in (2, 6, 7):
                # Remote disconnect, logoff and session lock invalidate signing context.
                self.lock_wallet()
            if msg.message == 0x0218 and msg.wParam == 4:
                # Windows suspend preserves the checkpoint; waking never resumes hashing.
                if hasattr(self, 'vanity_dialog'):
                    self.vanity_dialog.pause_search()
                else:
                    self.submit(self.runtime.stop_vanity, quiet=True, lifecycle=True)
        return False, 0

    def submit(self, function, callback=None, quiet=False, during_shutdown=False,lifecycle=False):
        task = Task(function)
        self.tasks.add(task)

        def done(result, error):
            self.tasks.discard(task)
            if self.exiting or (self.closing and not during_shutdown):
                if isinstance(result, dict) and 'private_key' in result:
                    result.clear()
                return
            if error:
                if self.signer.process.poll() is not None and self.profile_unlocked:
                    self.lock_wallet()
                    error='The signing helper stopped. Restart Wartnode-Desktop; saved payments remain reserved.'
                    self.notice.setText(error)
                if not quiet:
                    self.notice.setText(error)
                if callback:
                    callback(None, error)
            elif callback:
                callback(result, None)
        task.signals.done.connect(done, Qt.ConnectionType.QueuedConnection)
        (self.lifecycle_pool if lifecycle or during_shutdown else self.pool).start(task)

    def guard_signer(self,generation,action,**args):
        return self.signer.call(action,_valid=lambda:generation==self.session_generation,**args)

    def page(self, title, description):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(label(title, "title"))
        if description:
            layout.addWidget(label(description, muted=True))
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        self.stack.addWidget(scroll)
        return layout

    def navigate(self, index):
        self.stack.setCurrentIndex(index)
        for i, nav in enumerate(self.nav_buttons):
            nav.setChecked(i == index)
        if index == 4:
            self.refresh_explorer()
        if index == 2:
            self.refresh_wallets()
        if index == 5:
            def loaded(result,error):
                if result:self.storage_status.setText(f"Blockchain folder\n{result['path']}\n\nUsed: {result['used']/1024**3:.2f} GiB · Available: {result['free']/1024**3:.1f} GiB")
            self.submit(lambda:settings.storage_info(self.directory),loaded)

    def build_overview(self):
        from .pages import build_overview
        build_overview(self)

    def build_node(self):
        from .pages import build_node
        build_node(self)

    def build_wallet(self):
        from .pages import build_wallet
        build_wallet(self)

    def build_mine(self):
        from .pages import build_mine
        build_mine(self)

    def save_mining_limits(self):
        if self.editing_mining:return
        self.preferences=settings.save(self.directory,mining_mode=self.mining_mode.currentData(),
            cpu_limit=self.cpu_limit.value(),gpu_limit=self.gpu_limit.value())
        self.render_mining_configuration()

    def change_mining_mode(self):
        mode=self.mining_mode.currentData()
        if mode in PRESETS:
            for control in (self.cpu_limit,self.gpu_limit):
                control.blockSignals(True);control.setValue(PRESETS[mode]);control.blockSignals(False)
        self.save_mining_limits()

    def change_mining_limits(self):
        self.mining_mode.blockSignals(True)
        self.mining_mode.setCurrentIndex(self.mining_mode.findData('custom'))
        self.mining_mode.blockSignals(False)
        self.save_mining_limits()

    def build_explorer(self):
        from .pages import build_explorer
        build_explorer(self)

    def build_settings(self):
        from .pages import build_settings
        build_settings(self)

    def change_connection(self):
        mode=self.connection_choice.currentData()
        if mode==self.preferences['connection_mode'] or self.mode_busy or self.init_busy or self.mining_busy:return
        if mode=='lite' and QMessageBox.question(self,'Lite wallet',
            'Connect to mainnet.wartnode.com and the Wartnode explorer service? They can see queried public addresses and supply balances, fees and confirmations. Keys and signing stay on this computer. Solo mining is unavailable.',
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)!=QMessageBox.StandardButton.Yes:
            self.connection_choice.setCurrentIndex(0);return
        self.mode_busy=True;self.lock_wallet();self.explorer_generation+=1;self.explorer_page=None
        self.runtime.cancel_mining()
        self.apply_connection_button.setEnabled(False)
        def switch():
            self.signer.call('lock')  # Wait for any in-flight private-helper operation before changing policy.
            return self.runtime.set_mode(mode)
        def switched(result,error):
            self.mode_busy=False;self.apply_connection_button.setEnabled(True)
            if result:
                self.preferences=result
                self.network_data=None;self.last_network_attempt=0
                self.profile_balance=None;self.address_balances={}
                self.wallet_generation+=1;self.balance_offset=0;self.balance_rows=[]
                self.mode_label.setText('Lite wallet' if mode=='lite' else 'Full node')
                self.notice.setText('Connection changed. Unlock your wallet to continue.')
                self.refresh_wallets()
                if mode=='local':self.submit(self.runtime.detect_devices,self.devices_loaded)
            self.connection_choice.setCurrentIndex(1 if self.preferences['connection_mode']=='lite' else 0)
            self.tick()
        self.submit(switch,switched,lifecycle=True)

    def first_use(self):
        if self.preferences['connection_mode']=='lite':
            self.notice.setText('Choose Full node on the Node page to initialize a local node.');return
        if self.runtime.node.running or self.runtime.miner.running or self.init_busy:
            self.notice.setText('Stop the node and mining before initialization.');return
        dialog=SetupDialog(self.directory,self.devices,self)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        source=dialog.source.text() if dialog.method.currentData()=='import' else None
        self.init_cancel.clear();self.init_busy=True
        status={'copied':0,'total':1,'phase':'Initializing'}
        self.import_progress=QProgressDialog('Initializing node…','Cancel',0,100,self)
        self.import_progress.setWindowTitle('Initialize node');self.import_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self.import_progress.setMinimumDuration(0);self.import_progress.setAutoClose(False)
        self.import_progress.canceled.connect(self.init_cancel.set)
        def progress(copied,total,phase):status.update(copied=copied,total=max(1,total),phase=phase)
        progress_timer=QTimer(self)
        def render():
            self.import_progress.setLabelText(status['phase']);self.import_progress.setValue(min(99,int(100*status['copied']/status['total'])))
        progress_timer.timeout.connect(render);progress_timer.start(150)
        def configured(result,error):
            progress_timer.stop();progress_timer.deleteLater();self.import_progress.close();self.init_busy=False
            if result:
                self.preferences=settings.load(self.directory)
                self.storage_status.setText('Blockchain folder\n'+str(settings.chain_directory(self.directory)))
                self.notice.setText('Node initialized. Select Start node when ready.');self.navigate(1)
            self.update_state(self.runtime.state)
        self.submit(lambda:self.runtime.initialize_node(dialog.storage.text(),source,self.init_cancel,progress),configured)

    def change_storage(self):
        if self.runtime.node.running or self.runtime.miner.running:
            self.notice.setText('Stop the node and mining before changing blockchain storage.')
            return
        destination=QFileDialog.getExistingDirectory(self,'Choose an empty blockchain folder',str(settings.chain_directory(self.directory)))
        if not destination:return
        if QMessageBox.question(self,'Move blockchain',f'Move blockchain data to this folder?\n\n{destination}\n\nWallets and payment history stay in the wallet data folder.',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)!=QMessageBox.StandardButton.Yes:return
        self.storage_button.setEnabled(False)
        self.notice.setText('Moving blockchain data… Keep Wartnode-Desktop open until the move finishes.')
        def moved(result,error):
            self.storage_button.setEnabled(True)
            if result:
                self.storage_status.setText('Blockchain folder\n'+result)
                self.notice.setText('Blockchain storage changed. Start the node to continue syncing.')
        self.submit(lambda:self.runtime.change_storage(destination),moved)

    def explore_value(self,value):
        if value and value!='—':
            self.navigate(4)
            self.search.setText(value)
            self.search_explorer()

    def explorer_link(self,row,column):
        if column==0:self.open_web_explorer(self.explorer_results.item(row,column).text())
        elif column in (3,4,5):self.explore_value(self.explorer_results.item(row,column).text())

    def check_updates(self):
        if self.update_checking or not self.installed_root or not updates.configured(self.update_trust()):return
        self.update_checking=True;self.last_update_check=time.monotonic()
        trust=self.update_trust()
        self.check_updates_button.setEnabled(False)
        self.update_status.setText('Checking for updates…')
        current=updates.active(self.installed_root)
        def checked(result,error):
            self.update_checking=False
            self.check_updates_button.setEnabled(True)
            if trust!=self.update_trust():return
            if result:
                self.update_envelope,payload=result
                self.update_status.setText('Version '+payload['version']+' is available.')
                self.install_update_button.setEnabled(True)
            elif error:self.update_status.setText(error)
        import sys
        self.submit(lambda:updates.discover(trust,__version__,current.get('highest'),sys.getwindowsversion().build),checked)

    def install_update(self):
        if not self.update_envelope or not self.installed_root:return
        if QMessageBox.question(self,'Update Wartnode-Desktop','Download the verified update, stop mining and the node, and restart Wartnode-Desktop? Your wallets and payment history will be kept.',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)!=QMessageBox.StandardButton.Yes:return
        self.install_update_button.setEnabled(False)
        self.check_updates_button.setEnabled(False)
        self.update_status.setText('Downloading and verifying the update…')
        def prepared(result,error):
            if result:self.exit_application()
            else:
                self.install_update_button.setEnabled(True)
                self.check_updates_button.setEnabled(True)
        import sys
        self.submit(lambda:updates.prepare(self.installed_root,self.update_envelope,self.update_trust(),__version__,build=sys.getwindowsversion().build),prepared)

    def rollback_version(self):
        if not self.installed_root:return
        if not updates.rollback_available(self.installed_root):
            self.notice.setText('No compatible previous version is available.')
            return
        journal=updates.read_json(self.installed_root/'update-journal.json')
        if QMessageBox.question(self,'Return to previous version','Stop the node and mining, then restart with the previous compatible application? Wallets and payment history will be kept.',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)==QMessageBox.StandardButton.Yes:
            from .paths import atomic_json
            atomic_json(self.installed_root/'rollback-request.json',{'version':journal['previous']['version']})
            self.exit_application()

    def tick(self):
        if self.exiting or self.closing or self.polling or self.init_busy or self.mode_busy:
            return
        self.polling = True
        from .connection import context
        binding=context(self.directory)

        def polled(state, error):
            self.polling = False
            if binding!=context(self.directory):return
            if state:
                self.update_state(state)
                if self.runtime.ready:
                    self.refresh_profile_balances()
                    self.refresh_network_statistics()
                    self.refresh_history()
                if self.runtime.ready and self.address and self.send_dialog.isVisible():
                    self.submit(lambda: self.signer.call('balance'), self.balance_loaded, quiet=True)
            self.render_mining_status()
            self.render_miner_telemetry()
            if self.runtime.rpc_available and state and state.get("height", 0) > 0:
                self.update_index()
        self.submit(self.runtime.poll, polled, quiet=True)
        self.refresh_process_statistics()
        self.node_log.update_lines(self.runtime.node.lines)
        self.miner_log.update_lines(self.runtime.miner.lines)
        running = self.runtime.miner.running
        self.render_mining_status()
        for widget in (self.payout, self.mining_mode, self.cpu_limit, self.gpu_limit, self.backend, self.device):
            widget.setEnabled(not self.mining_busy and (not running or self.editing_mining))
        metrics = self.runtime.miner.metrics
        if metrics and running:
            rate = lambda key: f"{float(metrics.get(key,0))/1000000:.2f} MH/s"
            self.miner_rates.setText(f"Janus: {rate('janus_hps')} · GPU SHA: {rate('gpu_sha_hps')} · CPU Verus: {rate('verus_hps')}")
        elif not running:
            self.miner_rates.setText("Choose Full node to use solo mining." if self.preferences["connection_mode"]=="lite" else "Start mining to see hash rates.")
        self.render_miner_telemetry()

    def update_state(self, state):
        text = {'Starting / loading chain': 'Loading blockchain',
                'Starting / unavailable': 'Node unavailable',
                'Synchronizing': 'Syncing'}.get(state['state'], state['state'])
        if state["state"]=="Stopped" and not self.preferences["setup_complete"] and self.preferences["connection_mode"]=="local":text="Not initialized"
        peers = state.get('peers',0)
        mode=settings.load(self.directory)["connection_mode"]
        local=mode=="local"
        self.context.setText(f"{'Full node' if local else 'Lite wallet · mainnet.wartnode.com'}  ·  {text}" + (f"  ·  {peers} {'peer' if peers==1 else 'peers'}" if local else ""))
        self.start_node_button.setEnabled(local and self.preferences["setup_complete"] and not self.init_busy and not self.runtime.node.running)
        self.initialize_node_button.setEnabled(local and not self.init_busy and not self.runtime.node.running)
        self.stop_node_button.setEnabled(self.runtime.node.running)
        if text in ('Ready','Syncing') and self.notice.text() == 'Starting node…':
            self.notice.clear()
        elif text=='Failed' and self.notice.text()=='Starting node…':
            self.notice.setText(state['error'])
        self.node_status.setText(text)
        status=ConnectionStatus.from_runtime(state,mode)
        self.connection_ring.set_status(status)
        self.connection_detail.setText(status.detail)
        self.node_panels['height'].set_value(f"{state.get('height',0):,}" if state.get('height') else '—')
        self.node_panels['peers'].set_value(str(peers) if local else 'Remote')
        self.node_panels['endpoint'].set_value('Local' if local else 'Lite',
            '127.0.0.1:19400' if local else 'mainnet.wartnode.com')
        self.node_details.setText(f"Block height: {state.get('height',0):,} · "+
            (f"Peers: {state.get('peers',0)}" if local else 'Hosted mainnet service; remote peer count unavailable')+
            f"\n{state.get('error','')}")
        self.overview_status.setText(f"Block height: {state.get('height',0):,}. " + ("Ready to send payments." if self.runtime.ready and not local else ("Ready to send payments and mine." if self.runtime.ready else "Connect to a ready node to send payments.")))
        self.review_button.setEnabled(self.runtime.ready and bool(self.address))
        self.max_button.setEnabled(self.runtime.ready and bool(self.address))
        vanity_exclusive = self.runtime.vanity.running and self.runtime.vanity.exclusive
        self.start_mining.setEnabled(local and self.runtime.ready and not self.runtime.miner.running and not self.runtime.mining_requested and not self.mining_busy and not self.editing_mining and not vanity_exclusive and self.has_mining_gpu())
        self.start_mining.setToolTip('Stop the vanity search before starting mining; the pattern exceeds 8 characters.' if vanity_exclusive else '')
        if self.address and not self.runtime.ready:
            self.wallet_status.setText('Wallet unlocked. Balance unavailable until the node is ready.')
        self.update_profile_balance_display()
        self.render_network_statistics()
        if not self.runtime.rpc_available:
            self.latest_blocks_status.setText('Cached blocks · Offline' if self.latest_block_rows else 'Connect a node to load the latest blocks.')

    def refresh_network_statistics(self):
        if self.network_polling or time.monotonic()-self.last_network_attempt<30:return
        from .connection import context
        binding=context(self.directory)
        self.network_polling=True;self.last_network_attempt=time.monotonic()
        def loaded(result,error):
            self.network_polling=False
            if binding!=context(self.directory):return
            if result:self.network_data=result
            self.render_network_statistics()
        self.submit(lambda:network_statistics(connection_client(self.directory,deadline=time.monotonic()+20)),loaded,quiet=True)

    def render_network_statistics(self):
        data=self.network_data
        if not data:
            for panel in self.network_panels.values():panel.set_value('—')
            self.network_source.setText('Network statistics unavailable.' if self.runtime.ready else 'Network statistics unavailable while offline or syncing.')
            return
        self.network_panels['hashrate'].set_value(hashrate_text(data['hashrate']),f"Estimate · last {data['hashrate_window']} blocks")
        self.network_panels['difficulty'].set_value(difficulty_text(data['difficulty']))
        self.network_panels['difficulty'].value.setToolTip(
            f"{data['difficulty']:,.17g}" if data['difficulty'] is not None else 'Difficulty unavailable')
        self.network_panels['interval'].set_value(f"{data['interval']:.1f} s" if data['interval'] is not None else '—',
                                                f"Mean over {data['interval_blocks']} block intervals")
        self.network_panels['reward'].set_value('••••••' if self.preferences['hide_balances'] else format_wart(data['reward'])+' WART' if data['reward'] is not None else '—','Most recent block reward')
        age=max(0,int(time.monotonic()-data['checked']))
        self.network_source.setText(f"{'Last known · ' if not self.runtime.ready or age>60 else ''}{data['source']} · height {data['height']:,} · updated {age}s ago")

    def refresh_process_statistics(self):
        if self.resources_polling:return
        self.resources_polling=True
        node_pid=self.runtime.node.process.pid if self.runtime.node.running else None
        miner_pid=self.runtime.miner.process.pid if self.runtime.miner.running else None
        def sample():
            return self.node_sampler.sample(node_pid),self.miner_sampler.sample(miner_pid),settings.storage_info(self.directory)
        def loaded(result,error):
            self.resources_polling=False
            if not result:return
            node,miner,storage=result
            local=self.preferences['connection_mode']=='local'
            current_node=self.runtime.node.process.pid if self.runtime.node.running else None
            current_miner=self.runtime.miner.process.pid if self.runtime.miner.running else None
            if node['pid']==current_node:
                self.node_panels['memory'].set_value(f"{node['memory_bytes']/1024**2:.1f} MiB" if node['memory_bytes'] is not None else '—')
                cpu=f"{node['cpu_percent']:.1f}%" if node['cpu_percent'] is not None else 'Unavailable'
                from .fees import NODE_MINIMUM_FEE
                self.node_resources.setText(f'Node CPU: {cpu} · Managed minimum fee: {NODE_MINIMUM_FEE} WART' if local else 'Lite uses the selected remote service. Signing remains on this computer.')
            if miner['pid']==current_miner:self.miner_resources=miner
            self.node_panels['storage'].set_value(f"{storage['used']/1024**3:.2f} GiB" if local else 'Not required',
                                                f"{storage['free']/1024**3:.1f} GiB free" if local else f"Local chain retained · {storage['used']/1024**3:.2f} GiB")
            since=self.runtime.node.started_at if local and self.runtime.node.running else self.runtime.lite_connected_at if not local else None
            elapsed=max(0,int(time.monotonic()-since)) if since is not None else None
            self.node_panels['uptime'].set_value(f'{elapsed//3600}h {(elapsed%3600)//60}m' if elapsed is not None else '—')
        self.submit(sample,loaded,quiet=True)

    def start_node(self):
        self.notice.setText("Starting node…")
        self.submit(self.runtime.start_node, lambda result, error: self.tick(),lifecycle=True)

    def stop_node(self):
        self.runtime.cancel_mining()
        self.submit(self.runtime.stop_node,lambda *_:self.tick(),lifecycle=True)

    def update_index(self):
        if self.indexing:
            return
        self.indexing = True
        from .connection import context
        binding=context(self.directory)

        def index():
            store = Explorer(self.directory / "explorer.db")
            try:
                store.sync(connection_client(self.directory,deadline=time.monotonic()+18), budget=10)
                return store.summary(), store.recent_details(25)
            finally:
                store.con.close()

        def indexed(result, error):
            self.indexing = False
            if binding!=context(self.directory):return
            if result:
                summary, rows = result
                self.coverage.setText(f"Cache: {summary['cached_blocks']} blocks · {summary['database_bytes']/1048576:.2f}/32 MiB" + (' · Cleanup pending' if summary['maintenance_pending'] else ''))
                self.display_recent_blocks(rows)
        self.submit(index, indexed, quiet=True)

    def refresh_explorer(self):
        self.update_index() if self.runtime.rpc_available else None

    def open_block(self, height):
        self.navigate(4)
        self.search.setText(str(height))
        self.search_explorer()

    def search_explorer(self, older=False, query_override=None, show_results=True):
        query = query_override or self.search.text().strip().lower()
        if not query:
            self.show_latest_blocks();return
        if show_results:self.explorer_views.setCurrentIndex(1)
        previous = self.explorer_page if older and self.explorer_page and self.explorer_page['query']==query else None
        before,anchor = (previous['next_before'],previous['anchor']) if previous else (0,None)
        self.explorer_generation += 1
        generation = self.explorer_generation
        self.older_results.setEnabled(False)

        def search():
            store = Explorer(self.directory / "explorer.db")
            try:
                result = (store.lookup(connection_client(self.directory,deadline=time.monotonic()+30),query,settings.chain_directory(self.directory)/'chain.db3',before,anchor)
                    if self.runtime.rpc_available else store.search(query))
                result['checked'] = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
                return result
            finally:
                store.con.close()

        def found(result, error):
            if generation!=self.explorer_generation:return
            if not result:
                self.explorer_page = None
                populate(self.explorer_results,[])
                self.explorer_details.setPlainText(error or 'No results found.')
                return
            self.explorer_page = dict(query=query,next_before=result.get('next_before',0),anchor=result.get('anchor'))
            self.older_results.setEnabled(bool(result.get('next_before')))
            if result['type'] == 'block':
                block = result.get('block')
                if not block:
                    self.explorer_details.setPlainText((f"Block {result['height']} · {result['hash']} is no longer on the current chain."
                        if result.get('orphaned') else 'This block is not cached. Start the node to load it.'))
                    populate(self.explorer_results, [])
                    return
                entries = []
                for kind, key in (('Reward','rewards'),('Transfer','transfers')):
                    for tx in block['body'][key]:
                        entries.append([block['height'],kind,'••••••' if self.preferences['hide_balances'] else tx['amount'],tx.get('fromAddress','—'),tx['toAddress'],tx['txHash']])
                populate(self.explorer_results, entries)
                canonical = result.get('canonical')
                chain_status = 'Yes' if canonical is True else ('No' if canonical is False else 'Not checked (offline)')
                self.explorer_details.setPlainText(f"Block {block['height']:,}\nHash: {block['header']['hash']}\nPrevious block: {block['header']['prevHash']}\nTime: {block.get('utc','')}\nTransactions: {len(entries)}\nConfirmations: {result.get('confirmations','unavailable')} · On current chain: {chain_status}\nLast checked: {result['checked']}")
            else:
                populate(self.explorer_results, [[r['height'],r['kind'].capitalize(),'••••••' if self.preferences['hide_balances'] else format_wart(r['amount']),r['sender'] or '—',r['recipient'],r['hash']] for r in result['occurrences']])
                prefix = (f"Address: {result['address']}\nOn-chain balance: {'••••••' if self.preferences['hide_balances'] else result.get('balance','unavailable')} WART\n" if result['type']=='address' else '')
                cached = result['source']=='cache only'
                scope = 'Cached results only. Start the node to load older data.' if cached else ('More results are available.' if result.get('next_before') else ('No more matches in the current chain.' if result.get('complete') else 'Hosted index coverage is incomplete; more matches may exist.'))
                source = 'Local cache' if cached else result.get('source','Selected node')
                self.explorer_details.setPlainText(prefix + f"{len(result['occurrences'])} matches · {source}\n{scope}\nDifferent transfers can have the same transaction hash.\nLast checked: {result['checked']}")
        self.submit(search, found)

    def refresh_wallets(self):
        generation=self.session_generation
        def loaded(result, error):
            if not result:
                return
            selected = self.address or self.wallet_choice.currentData()
            self.profile_ready = result['profile_ready']
            if not self.profile_unlocked:
                self.wallet_status.setText('Profile locked. Public addresses and recorded activity remain available.' if self.profile_ready else 'Set up your profile, then create or import an address.')
            if generation==self.session_generation and result['locked'] and self.profile_unlocked:self.lock_wallet()
            if [r['address'] for r in self.wallets] != [r['address'] for r in result['wallets']]:
                self.wallet_generation+=1
                self.balance_offset=0;self.balance_rows=[];self.profile_balance=None
            self.wallets = result['wallets']
            self.profile_contacts=result['contacts']
            self.wallet_choice.blockSignals(True)
            self.wallet_choice.clear()
            for record in self.wallets:
                self.wallet_choice.addItem(record['label']+' · '+record['address'], record['address'])
            index = self.wallet_choice.findData(selected)
            if index >= 0:
                self.wallet_choice.setCurrentIndex(index)
            self.wallet_choice.blockSignals(False)
            self.overview_balance.detail.setText(f'Combined across {len(self.wallets)} addresses in this profile')
            if hasattr(self,'receive_choice'):
                self.receive_choice.blockSignals(True);self.receive_choice.clear()
                for record in self.wallets:self.receive_choice.addItem(record['label'],record['address'])
                self.receive_choice.blockSignals(False)
                self.display_receiving(self.public_address if any(r['address']==self.public_address for r in self.wallets)
                    else self.wallets[0]['address'] if self.wallets else None)
            self.refresh_address_tables()
            if hasattr(self, 'proof_dialog'):self.proof_dialog.refresh_addresses()
            if not self.wallets:self.profile_balance=ProfileBalance.from_rows([],[],'')
            self.update_profile_balance_display()
            self.refresh_activity(network=False)
            if self.runtime.ready:self.refresh_profile_balances()
        self.submit(lambda: self.signer.call('list'), loaded)

    def refresh_profile_balances(self):
        if self.balance_polling or not self.wallets or not self.runtime.ready:return
        from .connection import context
        binding=context(self.directory)
        generation=self.wallet_generation
        expected=[r['address'] for r in self.wallets]
        offset=self.balance_offset
        if offset==0:
            self.balance_rows=[];self.balance_cycle_started=time.monotonic()
        self.balance_polling=True
        def loaded(result,error):
            self.balance_polling=False
            if generation!=self.wallet_generation or binding!=context(self.directory):return
            if not result:
                self.balance_offset=0;self.balance_rows=[]
                self.update_profile_balance_display(failed=True)
                return
            if result['total']!=len(expected):
                self.balance_offset=0;self.balance_rows=[];self.refresh_wallets();return
            self.balance_rows.extend(result['rows'])
            self.address_balances.update({r['address']:r for r in result['rows']})
            self.balance_offset=result['next_offset'] or 0
            if not self.balance_offset:
                try:
                    self.profile_balance=ProfileBalance.from_rows(expected,self.balance_rows,result['source'],self.balance_cycle_started)
                except ValueError:
                    self.profile_balance=None
            self.refresh_address_tables()
            self.update_profile_balance_display()
        self.submit(lambda:self.signer.call('profile_balances',offset=offset,limit=20),loaded,quiet=True)

    def update_profile_balance_display(self,failed=False):
        snapshot=self.profile_balance
        masked=self.preferences['hide_balances']
        panels=[self.overview_balance]
        if hasattr(self,'wallet_balance'):panels.append(self.wallet_balance)
        complete=snapshot is not None and snapshot.complete
        unavailable=failed or not self.runtime.ready or snapshot is not None and not complete
        value='••••••' if masked else format_wart(snapshot.available) if complete else 'Unavailable' if unavailable else 'Loading…'
        count=len(self.wallets)
        detail=f'Combined across {count} addresses in this profile'
        if complete and count:
            age=max(0,int(time.monotonic()-snapshot.checked))
            detail+=f' · {"Last known · " if failed or not self.runtime.ready or age>30 else ""}updated {age}s ago'
        elif count:detail+=' · complete balance unavailable'
        for panel in panels:panel.set_value(value,detail)

    def password(self, title, prompt='Password'):
        return self.secret_input(title, prompt)

    def secret_input(self, title, prompt, multiline=False):
        dialog = QInputDialog(self)
        dialog.setWindowTitle(title)
        dialog.setLabelText(prompt)
        if multiline:
            dialog.setOption(QInputDialog.InputDialogOption.UsePlainTextEditForTextInput)
        else:dialog.setTextEchoMode(QLineEdit.EchoMode.Password)
        generation = self.session_generation
        def clear():
            dialog.setTextValue('')
            for kind in (QLineEdit,QPlainTextEdit):
                for widget in dialog.findChildren(kind):widget.clear()
        self.secret_dialogs[dialog] = clear
        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            return dialog.textValue() if accepted and generation == self.session_generation else None
        finally:
            clear();self.secret_dialogs.pop(dialog,None);dialog.deleteLater()

    def create_wallet(self):
        if not self.profile_ready or not self.profile_unlocked:
            self.ensure_profile(self.create_wallet)
            return
        generation = self.session_generation
        name, accepted = QInputDialog.getText(self, 'New address', 'Address label',
                                            text=f'Address {len(self.wallets)+1}')
        if not accepted or generation != self.session_generation:
            return

        def created(result, error):
            if not result:
                return
            if generation != self.session_generation:
                result.clear()
                self.refresh_wallets()
                return
            self.select_unlocked(result['address'])
            dialog = QDialog(self)
            dialog.setWindowTitle('Write down your recovery phrase')
            dialog.resize(620, 310)
            layout = QVBoxLayout(dialog)
            layout.addWidget(label('Write down all 24 words and store them offline. Anyone with this phrase can access your wallet.'))
            phrase = QPlainTextEdit(result['phrase'])
            phrase.setReadOnly(True)
            layout.addWidget(phrase)
            controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
            controls.accepted.connect(dialog.accept)
            layout.addWidget(controls)
            def clear():phrase.clear();result.clear()
            self.secret_dialogs[dialog] = clear
            try:dialog.exec()
            finally:
                clear();self.secret_dialogs.pop(dialog,None);dialog.deleteLater()
            if generation != self.session_generation:
                self.notice.setText('Recovery phrase hidden. Back up this wallet before receiving funds. Your saved wallet is kept.')
            self.refresh_wallets()
        self.submit(lambda: self.guard_signer(generation,'create', label=name), created)

    def ensure_profile(self, after=None):
        if self.mode_busy or self.closing or self.exiting:
            self.notice.setText('Wait for the current connection or shutdown operation to finish.')
            return
        if self.profile_ready:
            if self.profile_unlocked:
                if after:after()
            else:self.unlock_wallet(after)
            return
        generation = self.session_generation
        password = self.password('Secure your profile', 'One password protects all your addresses (at least 8 characters)')
        if password is None:return
        confirmation = self.password('Confirm profile password')
        if generation != self.session_generation:return
        if password != confirmation:
            self.notice.setText('The passwords do not match.')
            return
        def configured(result, error):
            if not result or generation != self.session_generation:return
            self.profile_ready = self.profile_unlocked = True
            self.lock_button.setText('Lock profile')
            self.wallet_status.setText('Profile unlocked. Create or import an address.')
            if after:after()
        self.submit(lambda:self.guard_signer(generation,'setup',password=password), configured)

    def onboard_profile(self):
        def loaded(result, error):
            if result and not result['profile_ready']:
                self.ensure_profile()
        self.submit(lambda:self.signer.call('list'), loaded)

    def import_wallet(self):
        if not self.profile_ready or not self.profile_unlocked:
            self.ensure_profile(self.import_wallet)
            return
        generation = self.session_generation
        kind, ok = QInputDialog.getItem(self, 'Import wallet', 'Import using', ['Recovery phrase (Warthog Desktop)', 'Private key'], 0, False)
        if not ok:
            return
        is_phrase = kind.startswith('Recovery')
        secret = self.secret_input('Import wallet', 'Original recovery phrase (keep the spelling and spacing)' if is_phrase else kind,is_phrase)
        if secret is None or generation != self.session_generation:
            return
        legacy_confirmed = False
        if is_phrase:
            from mnemonic import Mnemonic
            if not Mnemonic('english').check(secret):
                legacy_confirmed = QMessageBox.question(self, 'Legacy recovery phrase',
                    'This phrase failed its checksum check. Some older Warthog Desktop wallets used phrases without this check. Import it exactly as entered, then check that the address matches your wallet?',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Yes
                if not legacy_confirmed:
                    return
        name, accepted = QInputDialog.getText(self, 'Import address', 'Address label', text=f'Address {len(self.wallets)+1}')
        if not accepted or generation != self.session_generation:return
        self.submit(lambda: self.guard_signer(generation,'import', kind='phrase' if is_phrase else 'private', secret=secret, label=name,legacy_confirmed=legacy_confirmed),
            lambda result, error: (self.select_unlocked(result['address']),self.refresh_wallets()) if result and generation==self.session_generation else self.refresh_wallets())

    def unlock_wallet(self, after=None):
        generation = self.session_generation
        address = self.wallet_choice.currentData()
        password = self.password('Unlock profile')
        if password is not None:
            def unlocked(result, error):
                if not result or generation != self.session_generation:return
                self.select_unlocked(result['address'])
                if callable(after):after()
            self.submit(lambda: self.guard_signer(generation,'unlock', address=address, password=password),
                unlocked)

    def select_unlocked(self, address):
        self.profile_unlocked = True
        self.address = address
        if hasattr(self,'wallet_choice'):
            self.wallet_choice.blockSignals(True)
            self.wallet_choice.setCurrentIndex(self.wallet_choice.findData(address))
            self.wallet_choice.blockSignals(False)
        self.lock_button.setText('Lock profile')
        if not address:
            self.wallet_status.setText('Profile unlocked. Create or import an address.')
            return
        self.wallet_status.setText('Unlocked · ' + address)
        self.display_receiving(address)
        if not self.payout.text():
            self.payout.setText(address)
            if not self.editing_mining and not self.runtime.miner.running:
                self.preferences=settings.save(self.directory,payout_address=address)
        self.render_mining_configuration()
        self.submit(lambda: self.signer.call('balance'), self.balance_loaded, quiet=True)
        self.update_state(self.runtime.state)
        self.refresh_history()

    def balance_loaded(self, result, error):
        if error == 'Unlock your profile first' and (self.profile_unlocked or self.address):
            self.lock_wallet()
            return
        if error and self.address:
            self.wallet_status.setText('Wallet unlocked. Could not refresh the balance from the node.')
        if result and self.address and result.get('address') == self.address:
            self.wallet_status.setText('Profile unlocked.' if self.preferences['hide_balances'] else
                f"Selected address: available {format_wart(result['available'])} WART · Reserved {format_wart(result['reserved'])} WART")

    def lock_wallet(self):
        self.profile_unlocked = False
        self.session_generation += 1
        if hasattr(self, 'proof_dialog'):self.proof_dialog.lock_signing()
        # Qt modal dialogs run a nested event loop: idle and Windows lock
        # events still arrive while a recovery phrase or password is open.
        for dialog,clear in list(self.secret_dialogs.items()):
            clear();dialog.reject()
        self.address = None
        self.receive_address.clear()
        self.qr.clear()
        self.qr.setText('Wallet locked')
        self.recipient.clear()
        self.amount.clear()
        self.wallet_status.setText('Wallet locked.')
        self.lock_button.setText('Wallet locked')
        self.summaries['Available WART'].setText('Unlock a wallet')
        self.review_button.setEnabled(False)
        self.max_button.setEnabled(False)
        if hasattr(self,'receive_choice'):self.display_receiving(self.public_address)
        self.update_profile_balance_display()
        self.submit(lambda: self.signer.call('lock'), quiet=True,lifecycle=True)

    def backup_wallet(self):
        if not self.profile_unlocked:
            self.ensure_profile(self.backup_wallet)
            return
        generation=self.session_generation
        path, _ = QFileDialog.getSaveFileName(self, 'Save encrypted profile backup', str(self.directory/'backups'/'personal.wartprofile'), 'Wartnode profile (*.wartprofile)')
        if not path:
            return
        password = self.password('Back up profile', 'Profile password')
        if password is not None:
            self.submit(lambda: self.guard_signer(generation,'backup', path=path, password=password),
                lambda result, error: self.notice.setText('Profile backup saved, including every address, label, contact, and payment record. Keep your profile password.') if result else None)

    def restore_wallet(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Restore into an empty profile', '', 'Wartnode profile (*.wartprofile)')
        if not path:
            return
        password = self.password('Restore backup', 'Backup password')
        if password is not None:
            self.lock_wallet()
            self.submit(lambda: self.signer.call('restore', path=path, password=password),
                lambda result, error: (self.notice.setText(f"Restored {result['added']} addresses. Unlock with the profile password from this backup."),self.refresh_wallets()) if result else None)

    def review_payment(self, maximum=False):
        generation = self.session_generation
        payment_generation=self.payment_generation
        recipient, amount = self.recipient.text(), self.amount.text()
        self.review_button.setEnabled(False)

        def reviewed(result, error):
            self.review_button.setEnabled(bool(self.address) and self.runtime.ready)
            if not result or generation != self.session_generation or payment_generation!=self.payment_generation or result['address'] != self.address:
                return
            text = (f"Warthog\n\nFrom: {result['address']}\nTo: {result['recipient']}\n\n"
                f"Amount: {format_wart(result['amount'])} WART\nFee: {format_wart(result['fee'])} WART\n"
                f"Total: {format_wart(result['total'])} WART\n\nNode: {result['node']}\n\nSend this payment?")
            dialog=QMessageBox(QMessageBox.Icon.Question,'Review payment',text,
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,self)
            dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
            self.secret_dialogs[dialog]=lambda:dialog.setText('')
            try:answer=dialog.exec()
            finally:self.secret_dialogs.pop(dialog,None);dialog.deleteLater()
            if (answer != QMessageBox.StandardButton.Yes or generation != self.session_generation
                    or payment_generation!=self.payment_generation or result['address'] != self.address or not self.profile_unlocked):
                self.submit(lambda:self.signer.call('cancel_review'),quiet=True)
                return
            self.submit(lambda: self.guard_signer(generation,'send',token=result['token']),sent)

        def sent(result, error):
            if result:
                message = ('Submission could not be confirmed. Check Payments before sending again. The funds remain reserved.'
                           if result['status']=='uncertain' else 'Payment submitted. Waiting for confirmation.')
                self.notice.setText(f"{message}\nTransaction: {result['tx_hash']}")
                self.refresh_history()
                self.submit(lambda: self.signer.call('balance'),self.balance_loaded,quiet=True)
        self.submit(lambda: self.guard_signer(generation,'review',recipient=recipient,amount=amount,max=bool(maximum)),reviewed)

    def start_miner(self):
        if self.mining_busy or self.mode_busy or self.init_busy or self.closing or self.exiting or self.runtime.miner.running or self.runtime.mining_requested or self.editing_mining:return
        payout, backend, device = self.payout.text().strip(),self.backend.currentData(),self.device.currentData() or 0
        cpu,gpu=self.cpu_limit.value(),self.gpu_limit.value()
        threads=worker_threads(cpu)
        answer = QMessageBox.question(self,'Start mining',f"Start solo mining to this address?\n\n{payout}\n\nCPU limit: {cpu}%\nGPU limit: {gpu}%\nHardware: {self.backend.currentText()}\nGPU: {self.device.currentText()}\nNode: Local",QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Yes:
            generation=self.runtime.mining_generation
            self.mining_busy=True;self.start_mining.setEnabled(False)
            for control in (self.payout,self.mining_mode,self.cpu_limit,self.gpu_limit,self.backend,self.device):
                control.setEnabled(False)
            def start():
                self.runtime.start_miner(payout,threads,backend,device,cpu_limit=cpu,gpu_limit=gpu,generation=generation)
                settings.save(self.directory,payout_address=payout,cpu_threads=threads,gpu_device=device)
            def started(*_):
                self.mining_busy=False;self.tick()
            self.submit(start,started,lifecycle=True)

    def devices_loaded(self, result, error):
        if result:
            self.devices = result
        self.update_devices()

    def update_devices(self):
        backend = self.backend.currentData()
        names = self.devices.get('opencl', [])
        self.device.clear()
        for index,name in enumerate(names):
            self.device.addItem(f'GPU {index}: {name}',index)
        if not names:
            self.device.addItem('No supported GPU found',0)
        elif self.preferences['gpu_device']<len(names):
            self.device.setCurrentIndex(self.preferences['gpu_device'])
        self.start_mining.setEnabled(self.runtime.ready and not self.runtime.miner.running and not self.runtime.mining_requested and not self.mining_busy and not self.editing_mining and not (self.runtime.vanity.running and self.runtime.vanity.exclusive) and bool(names))

    def has_mining_gpu(self):
        backend = self.backend.currentData()
        return bool(self.devices.get('opencl'))

    def clear_explorer_cache(self):
        if QMessageBox.question(self,'Clear explorer cache','Clear cached blocks? Your wallets, payment history and node data will be kept.',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)!=QMessageBox.StandardButton.Yes:return
        def clear():
            cache=Explorer(self.directory/'explorer.db')
            try:cache.clear()
            finally:cache.con.close()
        self.submit(clear,lambda result,error: self.notice.setText('Explorer cache cleared. You can still look up older data through the node.') if not error else None)

    def resync(self):
        if QMessageBox.question(self,'Resync blockchain','Stop mining and the node, and back up the current blockchain data? Start the node afterward to download the blockchain again. Your wallets and payment history will be kept.',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Yes:
            self.runtime.cancel_mining()
            self.submit(self.runtime.archive_chain,lambda result,error: self.notice.setText('Blockchain backup saved to '+result+'. Start the node to sync again.') if result else None)

    def export_diagnostics(self):
        path,_ = QFileDialog.getSaveFileName(self,'Save diagnostics','wartnode-diagnostics.json','JSON (*.json)')
        if path:
            from .paths import atomic_json
            from .preflight import windows_compatibility,cpu_info
            from PySide6.QtCore import qVersion
            info=settings.storage_info(self.directory)
            atomic_json(Path(path),{'version':__version__,'network':'mainnet','node_state':{k:v for k,v in self.runtime.state.items() if k not in ('head','error')},
                'miner_running':self.runtime.miner.running,'windows':windows_compatibility(),'cpu':cpu_info(),'qt':qVersion(),
                'opencl_devices':self.devices.get('opencl',[]),'storage':{'used_bytes':info['used'],'free_bytes':info['free']},
                'updates_configured':updates.configured(updates.policy()),'installed':bool(self.installed_root)})
            self.notice.setText('Diagnostics saved. Wallet keys, passwords, addresses and transactions are not included.')

    def show_versions(self):
        path = asset_root()/'components'/'manifest.json'
        QMessageBox.information(self,'Component details',path.read_text(encoding='utf-8') if path.exists() else 'Component information is unavailable.')

    def show_normal(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event: QCloseEvent):
        if not self.exiting:
            event.ignore()
            self.lock_wallet()
            if self.preferences['close_to_tray'] and QSystemTrayIcon.isSystemTrayAvailable():
                if hasattr(self, 'vanity_dialog') and self.vanity_dialog.isVisible():self.vanity_dialog.reject()
                self.hide()
                if self.mining_settings_dialog.isVisible():self.mining_settings_dialog.reject()
                for dialog in (self.send_dialog,self.receive_dialog,self.node_log_dialog,self.mining_log_dialog,self.mining_settings_dialog):dialog.hide()
                self.tray.showMessage('Wartnode-Desktop is still running','Your profile is locked. Use Exit Wartnode-Desktop to close the app.')
            else:self.exit_application()
        else:
            QApplication.instance().removeEventFilter(self)
            self.idle.stop()
            event.accept()

    def exit_application(self):
        self.init_cancel.set()
        if self.exiting or self.closing:
            return
        self.closing = True
        if hasattr(self, 'vanity_dialog'):
            self.vanity_dialog.timer.stop()
            self.vanity_dialog.hide()
        self.runtime.cancel_mining()
        self.timer.stop()
        self.lock_wallet()
        self.notice.setText('Closing Wartnode-Desktop…')
        self.setEnabled(False)

        def stopped(result,error):
            self.exiting = True
            if self.session_notifications:
                self.wts.WTSUnRegisterSessionNotification(int(self.winId()))
            self.signer.close()
            self.tray.hide()
            QApplication.instance().quit()
        self.submit(self.runtime.shutdown,stopped,during_shutdown=True)
