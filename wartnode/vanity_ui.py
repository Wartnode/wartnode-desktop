"""Vanity address creation belongs to Wallet, in a nonblocking native dialog."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QToolButton, QVBoxLayout, QWidget)
from .vanity import chance, duration, estimate, pattern
from .vanity_rates import automatic_backend, mode as match_mode
from .widgets import scroll_dialog


class WrappedText(QLabel):
    """Make changing estimate text grow the scroll content instead of clipping."""
    def fit_height(self):
        height = self.heightForWidth(max(1,self.contentsRect().width())) if self.text() else 0
        if height >= 0 and height != self.minimumHeight():
            self.setMinimumHeight(height)

    def setText(self, value):
        super().setText(value)
        self.fit_height()

    def clear(self):
        self.setText('')

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_height()


def text(value='', muted=False):
    widget = WrappedText(value)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    widget.setProperty('muted', muted)
    return widget


def disclosure(title, content):
    button = QToolButton()
    button.setText(title)
    button.setCheckable(True)
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    button.setArrowType(Qt.ArrowType.RightArrow)
    button.setMinimumHeight(32)
    button.setStyleSheet('QToolButton { background: transparent; border: 0; padding: 4px 2px; }')
    content.hide()
    def toggled(opened):
        content.setVisible(opened)
        button.setArrowType(Qt.ArrowType.DownArrow if opened else Qt.ArrowType.RightArrow)
    button.toggled.connect(toggled)
    return button


class VanityDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.record = None
        self.polling = False
        self.busy = False
        self.operation_error = ''
        self.calibration_notice = ''
        self.last_measure_run = None
        self.saved_address = None
        self.epoch = 0
        self.setWindowTitle('Add vanity address')
        self.resize(640, 620)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        self.intro = text('Choose a prefix, suffix, or both for your address.')
        layout.addWidget(self.intro)
        self.pattern_fields = QWidget()
        form = QFormLayout(self.pattern_fields)
        form.setContentsMargins(0,0,0,0)
        form.setVerticalSpacing(10)
        self.mode = QComboBox()
        for label, value in [('Prefix', 'prefix'), ('Suffix', 'suffix'), ('Prefix + suffix', 'both')]:
            self.mode.addItem(label, value)
        self.mode.setAccessibleName('Vanity search mode')
        form.addRow('Match', self.mode)
        self.prefix = QLineEdit(); self.prefix.setPlaceholderText('cafe'); self.prefix.setMaxLength(48)
        self.prefix.setAccessibleName('Address prefix')
        self.suffix = QLineEdit(); self.suffix.setPlaceholderText('beef'); self.suffix.setMaxLength(48)
        self.suffix.setAccessibleName('Address suffix')
        form.addRow('Prefix', self.prefix); form.addRow('Suffix', self.suffix)
        layout.addWidget(self.pattern_fields)
        self.character_hint = text('Use 0–9 and a–f.', True); layout.addWidget(self.character_hint)
        self.options = QWidget()
        self.options_form = QFormLayout(self.options)
        self.options_form.setContentsMargins(0,0,0,0)
        self.options_form.setVerticalSpacing(10)
        self.backend = QComboBox()
        for label, value in [('Automatic', 'auto'), ('CPU', 'cpu'), ('GPU (OpenCL)', 'opencl')]:
            self.backend.addItem(label, value)
        self.backend.setAccessibleName('Vanity search hardware')
        self.options_form.addRow('Search with', self.backend)
        self.name = QLineEdit('Vanity address'); self.name.setMaxLength(80)
        self.name.setAccessibleName('Vanity address label'); self.options_form.addRow('Wallet label', self.name)
        for control in (self.mode, self.prefix, self.suffix, self.backend, self.name):
            control.setMinimumHeight(40)
        self.form = form
        self.preview = text(); self.preview.setObjectName('vanityPreview')
        self.preview.setStyleSheet('font-family: Consolas; font-size: 16px;')
        layout.addWidget(self.preview)
        self.estimate_card = QFrame(); self.estimate_card.setObjectName('panel')
        estimate_layout = QVBoxLayout(self.estimate_card)
        estimate_layout.setContentsMargins(16,14,16,14); estimate_layout.setSpacing(8)
        estimate_layout.addWidget(text('Search estimate', True))
        self.difficulty = text(); self.difficulty.setObjectName('vanityDifficulty')
        self.difficulty.setStyleSheet('font-size: 19px; font-weight: 600;')
        estimate_layout.addWidget(self.difficulty)
        self.estimate_note = text('', True); estimate_layout.addWidget(self.estimate_note)
        self.measure_button = QPushButton('Estimate time on this computer'); self.measure_button.clicked.connect(self.measure_speed)
        self.measure_button.setToolTip('Stop mining first. Measures this computer for about 8 seconds after hardware preparation.')
        self.estimate_details = QWidget()
        details_layout = QVBoxLayout(self.estimate_details); details_layout.setContentsMargins(0,4,0,0)
        self.details_text = text('', True); details_layout.addWidget(self.details_text)
        self.details_toggle = disclosure('Estimate details', self.estimate_details)
        estimate_actions = QHBoxLayout(); estimate_actions.addWidget(self.measure_button)
        estimate_actions.addWidget(self.details_toggle)
        estimate_layout.addLayout(estimate_actions); estimate_layout.addWidget(self.estimate_details)
        layout.addWidget(self.estimate_card)
        self.feasibility = text(); self.feasibility.setObjectName('vanityFeasibility'); layout.addWidget(self.feasibility)
        self.mining_rule = text(); layout.addWidget(self.mining_rule)
        self.stop_mining = QPushButton('Stop mining'); self.stop_mining.clicked.connect(window.stop_miner)
        layout.addWidget(self.stop_mining)
        self.options_toggle = disclosure('Search options', self.options)
        layout.addWidget(self.options_toggle); layout.addWidget(self.options)
        self.status = text('')
        self.status.setObjectName('vanityStatus'); layout.insertWidget(0,self.status)
        self.progress = text('', True); layout.addWidget(self.progress)
        self.saved_value = text(); self.saved_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.saved_value.setStyleSheet('font-family: Consolas;'); self.saved_value.hide(); layout.addWidget(self.saved_value)
        layout.addStretch()
        self.scroll = scroll_dialog(self)
        self.footer = QWidget()
        footer_layout = QVBoxLayout(self.footer); footer_layout.setContentsMargins(16,10,16,14)
        footer_layout.setSpacing(10)
        self.recovery_note = text('Recovery uses a profile backup, not recovery words.', True)
        footer_layout.addWidget(self.recovery_note)
        actions = QHBoxLayout()
        self.actions = actions
        self.start_button = QPushButton('Start search'); self.start_button.clicked.connect(self.start_search)
        self.cancel_button = QPushButton('Pause search'); self.cancel_button.clicked.connect(self.pause_search)
        self.save_button = QPushButton('Save to wallet'); self.save_button.clicked.connect(self.save_address)
        self.close_button = QPushButton('Close'); self.close_button.clicked.connect(self.reject)
        self.close_button.setAutoDefault(False)
        self.backup_button = QPushButton('Back up profile…'); self.backup_button.clicked.connect(window.backup_wallet)
        for control in (self.start_button, self.cancel_button, self.save_button, self.backup_button):
            control.setProperty('primary', True); control.setAutoDefault(False); actions.addWidget(control)
        actions.addStretch()
        self.discard_button = QPushButton('Discard…'); self.discard_button.clicked.connect(self.discard)
        self.discard_button.setAutoDefault(False)
        self.discard_button.setToolTip('Delete this unfinished search and any unsaved match.')
        actions.addWidget(self.discard_button); actions.addWidget(self.close_button)
        footer_layout.addLayout(actions)
        self.layout().addWidget(self.footer)
        self.timer = QTimer(self); self.timer.setInterval(250); self.timer.timeout.connect(self.refresh)
        self.mode.currentIndexChanged.connect(self.changed)
        self.prefix.textChanged.connect(self.changed); self.suffix.textChanged.connect(self.changed)
        self.backend.currentIndexChanged.connect(self.changed)
        self.changed()

    def value(self):
        mode = self.mode.currentData()
        value = pattern(self.prefix.text() if mode != 'suffix' else '',
                        self.suffix.text() if mode != 'prefix' else '')
        if mode == 'both' and (not value.prefix or not value.suffix):
            raise ValueError('Enter both a prefix and a suffix')
        return value

    def changed(self, *_):
        if self.record is None:
            self.progress.clear()
        self.form.setRowVisible(self.prefix, self.mode.currentData() != 'suffix')
        self.form.setRowVisible(self.suffix, self.mode.currentData() != 'prefix')
        try:
            value = self.value()
            self.preview.setText('Preview: '+value.prefix+('…' if value.length < 48 else '')+value.suffix)
            self.show_estimate(value)
        except ValueError as error:
            self.preview.clear(); self.difficulty.setText(str(error)); self.feasibility.clear()
            self.estimate_note.clear(); self.details_text.clear()
        self.refresh()

    def show_estimate(self, value, speed=None):
        backend = automatic_backend(self.backend.currentData(), value)
        measured = speed or self.window.runtime.vanity.measurement(backend, match_mode(value))
        info = estimate(value, measured['rate'] if measured else None)
        attempts = f"{info['attempts']:,}" if info['attempts'] < 10**15 else f"{info['attempts']:.3g}"
        self.difficulty.setToolTip(f"{info['attempts']:,} expected attempts under the uniform hash model")
        details = [f"Characters: {value.length} combined", f"Expected attempts: {attempts}"]
        if measured:
            conservative = estimate(value, measured['low'])
            probability = chance(value, 3600, measured['rate'])
            probability_text = 'over 99.9%' if probability > .999 else 'under 0.001%' if probability < .00001 else f'{100*probability:.2f}%'
            source = 'Current search' if speed else 'Recent local measurement'
            self.difficulty.setText('Average: '+duration(info['mean_seconds']))
            self.estimate_note.setText(f"{value.length} characters · {'approximate; excludes setup' if info['approximate'] else 'excludes setup; time can vary'}.")
            self.measure_button.setText('Update estimate')
            details += [f"{source}: {measured['rate']:,.0f} candidates/s",
                f"Observed range: {measured['low']:,.0f}–{measured['high']:,.0f} candidates/s",
                f"50% chance within: {duration(info['median_seconds'])}",
                f"95% chance within: {duration(conservative['p95_seconds'])} at the slower observed speed",
                f"Chance within one hour: {probability_text}"]
            if measured['variable']:
                details.append('Speed varied during measurement. Stop other heavy work for a steadier estimate.')
        else:
            level = 'Low difficulty' if value.length <= 5 else 'Moderate difficulty' if value.length <= 8 else 'Demanding pattern'
            self.difficulty.setText(level)
            self.estimate_note.setText(f'{value.length} characters · time depends on your computer.')
            self.measure_button.setText('Estimate time on this computer')
            details += [f"Example CPU average: {duration(info['cpu_seconds'])} at 1 million/s",
                        f"Example GPU average: {duration(info['gpu_seconds'])} at 100 million/s"]
        if info['approximate']:
            details.append('Suffix estimates approximate the checksum distribution.')
        details += ['Each extra character usually means 16× more work.',
            'Times exclude hardware preparation and can change with other work. A search has no fixed finish time.',
            'Searches stay on this computer and do not require a node connection.']
        self.details_text.setText('\n'.join(details))
        if info['sparse']:
            warning = 'A matching address may not exist. Use a shorter pattern.'
        elif not info['practical']:
            warning = 'Not practical for a desktop computer. Try fewer characters.'
            self.difficulty.setText('Not practical')
            self.details_text.setText(self.details_text.text()+'\nEven at 1 billion candidates/s, the average is '
                                     +duration(info['attempts']/1_000_000_000)+'.')
        elif measured and info['mean_seconds'] >= 365.25*86400:
            warning = 'The average exceeds a year on this computer. Try fewer characters.'
        elif value.length >= 12:
            warning = 'This can take days or longer. A shorter pattern will be faster.'
        else:
            warning = ''
        self.feasibility.setText(warning)
        self.feasibility.setVisible(bool(warning))
        self.feasibility.setStyleSheet('font-weight: 600;')

    def load_record(self):
        if self.polling or self.busy:
            return
        self.polling = True
        epoch = self.epoch
        def loaded(result, error):
            self.polling = False
            if epoch != self.epoch:
                return
            if error:
                self.operation_error = error
            elif result:
                previous = self.record
                self.record = result['record']
                if self.record and (not previous or previous['job_id'] != self.record['job_id']):
                    value = self.record['job']['pattern']
                    mode = 'both' if value['prefix'] and value['suffix'] else 'prefix' if value['prefix'] else 'suffix'
                    self.mode.setCurrentIndex(self.mode.findData(mode))
                    self.prefix.setText(value['prefix']); self.suffix.setText(value['suffix'])
                    self.name.setText(self.record['label'])
                    self.backend.setCurrentIndex(self.backend.findData(self.record['backend']))
                self.changed()
            self.refresh()
        self.window.submit(lambda:self.window.signer.call('vanity_status'), loaded, quiet=True)

    def refresh(self):
        try:
            value = self.value()
        except ValueError:
            value = None
        record = self.record
        status = record['state'] if record else None
        resumable = status in ('paused','failed')
        saved = self.saved_address is not None
        state = self.window.runtime.vanity.snapshot()
        same_run = bool(record and state.get('job_id') == record['job_id'] and state.get('run_id') == record['run_id'])
        calibration = state.get('purpose') == 'calibration'
        calibrating = calibration and self.window.runtime.vanity.running
        searching = status == 'running'
        mining = self.window.runtime.miner.running or self.window.runtime.mining_requested
        blocked = bool(value and value.exclusive and mining)

        if calibration and not calibrating and state['status'] in ('calibrated','paused','error') and state.get('run_id') != self.last_measure_run:
            self.last_measure_run = state.get('run_id')
            if state['status'] == 'calibrated':
                self.calibration_notice = ('Time estimate updated.' if state.get('measurement') else
                    'Could not get a steady speed. Close other heavy apps and try again.')
            else:
                self.calibration_notice = state.get('error') or 'Time estimate stopped.'

        if value:
            speed = state.get('progress', {}).get('speed') if searching and same_run and not calibration else None
            self.show_estimate(value, speed)
        self.intro.setVisible(not record and not saved)
        self.pattern_fields.setVisible(not record and not saved)
        self.character_hint.setVisible(not record and not saved)
        self.preview.setVisible(value is not None and not saved)
        self.estimate_card.setVisible(not saved and status != 'found' and bool(value or self.prefix.text() or self.suffix.text()))
        self.feasibility.setVisible(not saved and status != 'found' and bool(self.feasibility.text()))
        self.saved_value.setVisible(saved)

        options_available = not saved and not searching and not calibrating
        self.options_toggle.setVisible(options_available and status != 'found')
        self.options_toggle.setEnabled(not self.busy)
        self.options.setVisible(options_available and (status == 'found' or self.options_toggle.isChecked()))
        self.options_form.setRowVisible(self.backend,status != 'found')
        for control in (self.mode,self.prefix,self.suffix):
            control.setEnabled(not record and not self.busy and not self.polling and not calibrating)
        self.backend.setEnabled((not record or resumable) and not self.busy and not calibrating)
        self.name.setEnabled(not searching and not self.busy)

        rule = ''
        if not saved and status != 'found':
            if calibrating:
                rule = 'Mining stays off while estimating.'
            elif value and value.exclusive:
                rule = 'Stop mining to search for more than 8 characters.' if blocked else 'Mining must stay off during searches over 8 characters.'
            elif value and mining and (not record or resumable):
                rule = 'You can search while mining. Stop it to estimate time.'
        self.mining_rule.setText(rule); self.mining_rule.setVisible(bool(rule))
        self.stop_mining.setVisible(bool(value and mining and (not record or resumable) and not calibrating and not saved))
        self.stop_mining.setEnabled(not self.window.mining_busy)

        self.start_button.setText('Create another' if saved else 'Resume search' if resumable else 'Start search')
        self.start_button.setVisible(saved or (not searching and status != 'found' and not calibrating))
        self.start_button.setEnabled(not self.busy and (saved or (not searching and status != 'found' and not calibrating
                                     and value is not None and not blocked and not self.polling)))
        self.cancel_button.setText('Stop estimate' if calibrating else 'Pause search')
        self.cancel_button.setVisible(searching or calibrating)
        self.cancel_button.setEnabled((searching or calibrating) and not self.busy)
        self.save_button.setVisible(status == 'found' and not calibrating)
        self.save_button.setEnabled(status == 'found' and not self.busy and not calibrating)
        self.backup_button.setVisible(saved)
        self.discard_button.setVisible(bool(record and not searching and not calibrating))
        self.discard_button.setEnabled(not self.busy)
        self.measure_button.setVisible(not searching and status != 'found' and not calibrating)
        self.measure_button.setEnabled(value is not None and not mining and not self.busy and not self.polling)
        primary = self.backup_button if saved else self.save_button if status == 'found' and not calibrating else self.cancel_button if searching or calibrating else self.start_button
        if self.actions.itemAt(0).widget() is not primary:
            self.actions.insertWidget(0,primary)
        if self.start_button.property('primary') != (not saved):
            self.start_button.setProperty('primary',not saved)
            self.start_button.style().unpolish(self.start_button); self.start_button.style().polish(self.start_button)
        for button in (self.start_button,self.cancel_button,self.save_button,self.backup_button):
            button.setDefault(button is primary)
        self.close_button.setToolTip('Closing pauses your search.' if searching or calibrating else '')
        self.recovery_note.setText('Back up your profile to recover this address.' if saved else
                                   'Recovery uses a profile backup, not recovery words.')
        self.status.setStyleSheet('font-size: 19px; font-weight: 600;' if saved or status == 'found' else '')

        if self.busy:
            self.status.setVisible(bool(self.status.text()))
            return
        if saved:
            self.status.setText('Saved to wallet')
            self.status.setVisible(True)
            return
        if calibrating:
            self.status.setText('Preparing time estimate…' if state['status'] == 'initializing' else 'Measuring your computer’s speed…')
            self.progress.setText('About 8 seconds after preparation.')
        elif record:
            if searching and same_run and state['status'] in ('found','paused','error','failed'):
                self.load_record()
            message = {
                'running':'Searching…' if same_run and state['status'] == 'searching' else 'Preparing search…',
                'paused':'Search paused. Resume whenever you are ready.',
                'failed':record['error'] or 'Search stopped. Resume to try again.',
                'found':'Match found'
            }.get(status,status)
            if searching and same_run and state['status'] == 'error':
                message = state['error']
            self.status.setText(self.operation_error or (self.calibration_notice if resumable else '') or message)
            progress = state.get('progress',{}) if same_run else {}
            elapsed = progress.get('elapsed_seconds',record['checkpoint']['elapsed_seconds'])
            self.progress.setText('Search time: '+duration(elapsed) if status != 'found' else '')
            if progress:
                self.details_text.setText(self.details_text.text()+f"\nCandidates checked: {int(progress.get('candidates_exact','0')):,}"
                    '\nSaved checkpoints may replay some work after interruption.')
        else:
            self.status.setText(self.operation_error or self.calibration_notice)
            self.progress.clear()
        self.status.setVisible(bool(self.status.text()))
        self.progress.setVisible(bool(self.progress.text()))

    def measure_speed(self):
        if self.busy or self.polling or self.window.runtime.vanity.running:
            return
        try:
            value = self.value()
        except ValueError as error:
            self.status.setText(str(error)); return
        self.operation_error = self.calibration_notice = ''
        self.busy = True; self.status.setText('Preparing time estimate…'); self.refresh()
        epoch = self.epoch
        backend = automatic_backend(self.backend.currentData(), value)
        def started(_, error):
            if epoch != self.epoch:
                return
            self.busy = False
            if error:
                self.operation_error = error
                self.status.setText(error)
            self.refresh()
        self.window.submit(lambda:self.window.runtime.calibrate_vanity(backend, match_mode(value)), started, lifecycle=True)

    def start_search(self):
        if self.saved_address:
            self.saved_address = None
            self.operation_error = self.calibration_notice = ''
            self.options_toggle.setChecked(False); self.details_toggle.setChecked(False)
            self.status.clear(); self.saved_value.clear(); self.changed()
            return
        if self.busy or self.polling or self.window.runtime.vanity.running or (self.record and self.record['state'] not in ('paused','failed')):
            return
        if not self.window.profile_ready or not self.window.profile_unlocked:
            self.window.ensure_profile(self.start_search)
            return
        try:
            value = self.value()
            if value.exclusive and (self.window.runtime.miner.running or self.window.runtime.mining_requested):
                raise ValueError('Stop mining before starting this search')
        except ValueError as error:
            self.status.setText(str(error)); return
        self.busy = True; self.backup_button.hide(); self.progress.clear()
        self.operation_error = ''
        self.calibration_notice = ''
        self.status.setText('Preparing search…'); self.refresh()
        epoch, generation = self.epoch, self.window.session_generation
        backend, name = automatic_backend(self.backend.currentData(), value), self.name.text()
        record = self.record

        def prepared(result, error):
            if not result:
                if epoch == self.epoch:
                    self.busy = False
                    self.operation_error = error or 'Unlock the profile and try again'
                    self.status.setText(self.operation_error); self.refresh()
                return
            if epoch != self.epoch or generation != self.window.session_generation or not self.isVisible():
                self.window.submit(lambda:self.window.signer.call('vanity_pause',
                    job_id=result['job_id'], run_id=result['run_id']), quiet=True)
                return
            self.record = result
            def launch():
                try:
                    return self.window.runtime.start_vanity(result, backend)
                except Exception:
                    self.window.signer.call('vanity_pause', job_id=result['job_id'], run_id=result['run_id'])
                    raise
            def started(_, error):
                if epoch != self.epoch:
                    return
                self.busy = False
                if error:
                    self.operation_error = error
                self.load_record()
                self.refresh()
            self.window.submit(launch, started, lifecycle=True)
        action = 'vanity_resume' if record else 'vanity_prepare'
        args = dict(job_id=record['job_id'], backend=backend) if record else dict(
            prefix=value.prefix, suffix=value.suffix, label=name, backend=backend)
        self.window.submit(lambda:self.window.guard_signer(generation, action, **args), prepared)

    def pause_search(self, checked=False):
        self.epoch += 1
        epoch, record = self.epoch, self.record
        self.operation_error = ''
        self.busy = True; self.status.setText('Pausing…'); self.refresh()
        def pause():
            self.window.runtime.stop_vanity()
            if record:
                self.window.signer.call('vanity_pause', job_id=record['job_id'], run_id=record['run_id'])
        def paused(_, error):
            if epoch != self.epoch:
                return
            self.busy = False
            self.operation_error = error or ''
            self.load_record()
        self.window.submit(pause, paused, quiet=True, lifecycle=True)

    def discard(self):
        if self.busy or not self.record or self.record['state'] == 'running':
            return
        if not self.window.profile_unlocked:
            self.window.ensure_profile(self.discard); return
        if QMessageBox.question(self, 'Discard unfinished search?',
                'Permanently discard this search and any unsaved match? You will need to start a new search.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.epoch += 1
        generation, job_id = self.window.session_generation, self.record['job_id']
        self.busy = True
        def discarded(result, error):
            self.busy = False
            if result:
                self.record = None; self.operation_error = ''
                self.prefix.clear(); self.suffix.clear()
                self.changed()
            else:
                self.operation_error = error or 'Unlock the profile and try again'
            self.refresh()
        self.window.submit(lambda:self.window.guard_signer(generation, 'vanity_discard', job_id=job_id), discarded)

    def save_address(self):
        if self.busy or not self.record or self.record['state'] != 'found':
            return
        if not self.window.profile_unlocked:
            self.window.ensure_profile(self.save_address); return
        generation = self.window.session_generation
        job_id, name = self.record['job_id'], self.name.text()
        self.operation_error = ''
        self.busy = True; self.status.setText('Saving address…'); self.refresh()
        def saved(result, error):
            self.busy = False
            if result:
                self.record = None
                self.saved_address = result['address']
                self.saved_value.setText(result['address'])
                self.status.setText('Saved to wallet')
                self.progress.clear(); self.backup_button.show()
                if generation == self.window.session_generation:
                    self.window.select_unlocked(result['address'])
                self.window.refresh_wallets()
            else:
                self.operation_error = error or 'Unlock the profile and save again'
                self.status.setText(self.operation_error)
            self.refresh()
        self.window.submit(lambda:self.window.guard_signer(generation, 'vanity_finalize',
            job_id=job_id, label=name), saved)

    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start(); self.load_record(); self.refresh()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def reject(self):
        self.pause_search()
        super().reject()
