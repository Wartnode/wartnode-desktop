"""Immediate, validated native preferences and their lifecycle effects."""
import time
from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QSystemTrayIcon
from . import settings, theme, updates
from . import desktop_preferences as desktop


class PreferencesActions:
    def set_preference(self,key,value):
        previous=self.preferences[key]
        try:
            if key=='launch_at_startup':desktop.set_startup(value,self.installed_root,self.directory)
            self.preferences=settings.save(self.directory,**{key:value})
        except (OSError,ValueError) as error:
            if key=='launch_at_startup':
                try:desktop.set_startup(previous,self.installed_root,self.directory)
                except (OSError,ValueError):pass
            self.notice.setText(str(error));self.reload_preference_controls();return
        if key=='appearance':theme.apply(QApplication.instance(),value)
        if key=='auto_lock_minutes':
            self.refresh_lock_policy()
            self.submit(lambda:self.signer.call('touch'),quiet=True)
        if key=='hide_balances':self.render_privacy()
        if key in ('automatic_updates','update_channel'):
            self.update_envelope=None;self.install_update_button.setEnabled(False)
            self.last_update_check=0
            self.schedule_update_checks()

    def reload_preference_controls(self):
        for key,control in self.preference_controls.items():
            control.blockSignals(True)
            if hasattr(control,'setChecked'):control.setChecked(self.preferences[key])
            else:control.setCurrentIndex(control.findData(self.preferences[key]))
            control.blockSignals(False)

    def open_profile_folder(self):
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.directory))):
            self.notice.setText('The profile folder could not be opened.')

    def change_profile_password(self):
        if not self.profile_unlocked:
            self.ensure_profile(self.change_profile_password);return
        generation=self.session_generation
        old=self.password('Change profile password','Current password')
        if old is None:return
        new=self.password('Change profile password','New password (at least 8 characters)')
        if new is None:return
        confirmation=self.password('Confirm new password')
        if generation!=self.session_generation or confirmation is None:return
        if new!=confirmation:self.notice.setText('The new passwords do not match.');return
        def changed(result,error):
            if result:
                self.lock_wallet()
                self.notice.setText('Profile password changed. Create a new backup; older backups still use their original password.')
        self.submit(lambda:self.guard_signer(generation,'change_password',old_password=old,new_password=new),changed)

    def render_privacy(self):
        self.update_profile_balance_display();self.refresh_address_tables()
        self.render_network_statistics()
        if hasattr(self,'activity_data'):self.render_activity()
        if hasattr(self,'latest_block_rows'):self.display_recent_blocks(self.latest_block_rows)
        # Search details can contain public balances and amounts. Clear them
        # immediately rather than retaining unmasked text in accessibility APIs.
        self.explorer_generation+=1;self.explorer_page=None
        self.explorer_results.setRowCount(0);self.explorer_details.clear()
        if self.profile_unlocked:self.wallet_status.setText('Profile unlocked.')

    def update_trust(self):
        return updates.channel_policy(updates.policy(),self.preferences['update_channel'])

    def schedule_update_checks(self):
        if not hasattr(self,'update_timer'):
            self.update_timer=QTimer(self);self.update_timer.setInterval(60*60000)
            self.update_timer.timeout.connect(self.automatic_update_check)
        supported=bool(self.installed_root and updates.configured(self.update_trust()))
        if self.preferences['automatic_updates'] and supported:
            self.update_timer.start();QTimer.singleShot(2000,self.automatic_update_check)
        else:self.update_timer.stop()

    def automatic_update_check(self):
        if (not self.preferences['automatic_updates'] or self.closing or self.exiting or self.update_checking
                or time.monotonic()-self.last_update_check<60*60):return
        if self.installed_root and updates.configured(self.update_trust()):self.check_updates()
