"""Reviewed mining configuration changes and public telemetry presentation."""
import time
from . import settings
from .mining import PRESETS, validate, worker_threads
from .dashboard import SampleHistory
from .statistics import hashrate_text
from .compat import wallet_utils as crypto


class MiningActions:
    def stop_miner(self):
        self.runtime.cancel_mining()
        self.mining_busy=True
        def stopped(*_):
            self.mining_busy=False
            self.tick()
        self.submit(lambda:self.runtime.stop_miner(cancel=False),stopped,lifecycle=True)

    def render_mining_status(self):
        running=self.runtime.miner.running
        waiting=self.runtime.mining_waiting
        status='Waiting for node' if waiting else 'Mining to local node' if running else 'Unavailable in Lite mode' if self.preferences['connection_mode']=='lite' else 'Stopped'
        self.mining_status.setText(status)
        self.summaries['Solo mining'].setText('Waiting for node' if waiting else 'Running' if running else 'Stopped')
        self.stop_mining.setEnabled(running or self.runtime.mining_requested or self.mining_busy)

    def restore_mining_form(self):
        fields=(self.payout,self.mining_mode,self.cpu_limit,self.gpu_limit,self.device)
        for field in fields:field.blockSignals(True)
        value=self.preferences
        self.payout.setText(value['payout_address'])
        self.mining_mode.setCurrentIndex(self.mining_mode.findData(value['mining_mode']))
        self.cpu_limit.setValue(value['cpu_limit']);self.gpu_limit.setValue(value['gpu_limit'])
        index=self.device.findData(value['gpu_device'])
        if index>=0:self.device.setCurrentIndex(index)
        for field in fields:field.blockSignals(False)

    def show_mining_settings(self,preset=None,payout=None):
        if self.mining_busy or self.mode_busy or self.init_busy:return
        self.editing_mining=True
        self.runtime.set_mining_editing(True)
        self.restore_mining_form()
        if preset in PRESETS:
            self.mining_mode.setCurrentIndex(self.mining_mode.findData(preset))
        if payout:self.payout.setText(payout)
        for field in (self.payout,self.mining_mode,self.cpu_limit,self.gpu_limit,self.device):field.setEnabled(True)
        self.mining_apply.setText('Apply and restart' if self.runtime.miner.running else 'Save settings')
        self.mining_dialog_error.clear()
        self.mining_settings_dialog.show();self.mining_settings_dialog.raise_()
        self.mining_settings_dialog.activateWindow()
        self.start_mining.setEnabled(False)
        self.render_mining_configuration()

    def cancel_mining_settings(self):
        self.editing_mining=False
        self.runtime.set_mining_editing(False)
        self.restore_mining_form()
        self.render_mining_configuration()
        self.update_state(self.runtime.state)

    def apply_mining_settings(self):
        if self.mining_busy or self.mode_busy or self.init_busy:return
        try:
            cpu,gpu=self.cpu_limit.value(),self.gpu_limit.value()
            mode=self.mining_mode.currentData();validate(cpu,gpu,mode)
            payout=self.payout.text().strip()
            if payout:payout=crypto.validate_address(payout)
            device=self.device.currentData() or 0
            backend=self.backend.currentData()
        except ValueError as error:
            self.notice.setText(str(error));self.mining_dialog_error.setText(str(error));return
        was_running=self.runtime.miner.running
        was_waiting=self.runtime.mining_waiting
        self.runtime.cancel_mining()
        generation=self.runtime.mining_generation
        self.runtime.set_mining_editing(False)
        self.mining_busy=True
        self.editing_mining=False
        self.mining_settings_dialog.hide()
        def apply():
            with self.runtime.operation_lock:
                self.runtime.stop_miner('settings_change',cancel=False)
                if was_running:
                    if self.runtime.miner.running:raise ValueError('The previous miner has not stopped')
                preferences=settings.save(self.directory,mining_mode=mode,cpu_limit=cpu,gpu_limit=gpu,
                    payout_address=payout,gpu_device=device,cpu_threads=worker_threads(cpu))
                if was_running:
                    self.runtime.start_miner(payout,worker_threads(cpu),backend,device,cpu_limit=cpu,gpu_limit=gpu,generation=generation)
                return preferences
        def applied(result,error):
            self.mining_busy=False
            self.preferences=settings.load(self.directory)
            self.restore_mining_form();self.render_mining_configuration()
            if result:self.notice.setText('Miner restarted with the reviewed configuration.' if was_running else
                'Mining settings saved. Press Start mining when the node is ready.' if was_waiting else 'Mining settings saved.')
            self.tick()
        self.submit(apply,applied,lifecycle=True)

    def render_mining_configuration(self):
        if not hasattr(self,'mining_presets'):return
        for key,control in self.mining_presets.items():control.setChecked(self.preferences['mining_mode']==key)
        payout=self.preferences['payout_address']
        self.mining_payout_summary.setText('Payout: '+(self.address_label(payout) if payout else 'Choose an address'))
        self.mining_payout_summary.setToolTip(payout)
        if hasattr(self,'reward_table') and hasattr(self,'activity_data'):
            self.render_activity()

    def choose_mining_address(self):
        if not self.wallets:
            self.notice.setText('Create/import an address on Wallet, or enter an external payout address.');return
        from PySide6.QtWidgets import QInputDialog
        names=[row['label']+' · '+row['address'] for row in self.wallets]
        value,ok=QInputDialog.getItem(self,'Payout address','Your addresses',names,0,False)
        if ok:self.payout.setText(self.wallets[names.index(value)]['address'])

    def render_miner_telemetry(self):
        if not hasattr(self,'miner_panels'):return
        sample=self.runtime.miner.sample
        running=self.runtime.miner.running
        fresh=running and sample is not None and sample.fresh()
        if fresh:
            self.hash_history.append(sample)
            values={'janus':sample.janus_hps,'gpu':sample.gpu_sha_hps,'cpu':sample.verus_hps}
            for key,panel in self.miner_panels.items():panel.set_value(hashrate_text(values[key]))
            self.overview_miner.set_value(hashrate_text(sample.janus_hps),
                f'GPU SHA {hashrate_text(sample.gpu_sha_hps)} · CPU Verus {hashrate_text(sample.verus_hps)}')
            elapsed=int(sample.elapsed_s)
            rejected=sample.rejected/max(1,sample.accepted+sample.rejected)*100
            self.mining_session.setText(f'Uptime {elapsed//3600}h {(elapsed%3600)//60}m · Accepted {sample.accepted} · Rejected {sample.rejected} ({rejected:.1f}%) · Session blocks {sample.blocks}')
            gpu=sample.gpus[0] if sample.gpus else None
            value=lambda number,unit:'Unavailable' if number is None else f'{number:.1f}{unit}'
            self.gpu_telemetry.setText('GPU telemetry unavailable' if gpu is None else
                f'{gpu.name}\nTemperature {value(gpu.temperature_c,"°C")} · Utilization {value(gpu.utilization_pct,"%")}'
                f'\nPower {value(gpu.power_w," W")} · Core {value(gpu.core_mhz," MHz")} · Memory {value(gpu.memory_mhz," MHz")}')
        else:
            for panel in self.miner_panels.values():panel.set_value('—')
            waiting=self.runtime.mining_waiting
            self.overview_miner.set_value('Running' if running else 'Waiting for node' if waiting else 'Unavailable' if self.preferences['connection_mode']=='lite' else 'Stopped',
                'Waiting for fresh miner telemetry.' if running else 'Mining will resume automatically when the node is ready.' if waiting else 'Solo mining requires Full node.' if self.preferences['connection_mode']=='lite' else 'Start mining to see performance')
            self.mining_session.setText('Waiting for fresh miner telemetry.' if running else 'Waiting for node · mining will resume automatically' if waiting else self.runtime.mining_error or 'Miner stopped · session telemetry inactive')
            self.gpu_telemetry.setText('GPU telemetry unavailable' if running else 'GPU telemetry appears while mining.')
            if not running:self.hash_history.clear()
        cpu=self.miner_resources.get('cpu_percent') if running else None
        memory=self.miner_resources.get('memory_bytes') if running else None
        self.cpu_telemetry.setText('Miner CPU: '+(f'{cpu:.1f}% of total capacity' if cpu is not None else 'Unavailable')+
            '\nMiner memory: '+(f'{memory/1024**2:.1f} MiB' if memory is not None else 'Unavailable')+
            '\nCPU temperature / power: Unavailable')
        self.mining_chart.set_samples(self.hash_history.samples)
        self.overview_hash_chart.set_samples(self.hash_history.samples)
