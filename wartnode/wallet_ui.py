"""Labeled public address workflows and owned Send/Receive dialogs."""
import io
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFormLayout,
    QInputDialog, QLineEdit, QMessageBox, QVBoxLayout)
from .widgets import populate, format_wart
from . import settings


class WalletActions:
    def close_send(self):
        self.payment_generation+=1
        self.recipient.clear();self.amount.clear()
        self.submit(lambda:self.signer.call('cancel_review'),quiet=True)

    def refresh_address_tables(self):
        if not hasattr(self,'address_table'):return
        old=self.address_table.item(self.address_table.currentRow(),0)
        selected=old.data(Qt.ItemDataRole.UserRole) if old else None
        contact=self.contact_table.item(self.contact_table.currentRow(),1)
        selected_contact=contact.text() if contact else None
        count=len(self.wallets)
        self.address_page=max(0,min(self.address_page,max(0,(count-1)//50)))
        rows=self.wallets[self.address_page*50:(self.address_page+1)*50]
        self.visible_addresses=rows
        masked=self.preferences['hide_balances']
        balances=getattr(self,'address_balances',{})
        populate(self.address_table,[[r['label']+'\n'+r['address'],
            ' · '.join((['Default'] if r['default'] else [])+(['Mining payout'] if r['address']==self.preferences['payout_address'] else [])) or 'Personal',
            '••••••' if masked else format_wart(balances[r['address']]['available'])
                if r['address'] in balances and not balances[r['address']].get('error') else '—'] for r in rows])
        self.address_page_label.setText(f'{count} addresses · page {self.address_page+1} of {max(1,(count+49)//50)}')
        self.address_previous.setEnabled(self.address_page>0)
        self.address_next.setEnabled((self.address_page+1)*50<count)
        for index,row in enumerate(rows):self.address_table.item(index,0).setData(Qt.ItemDataRole.UserRole,row['address'])
        if rows:self.address_table.selectRow(next((i for i,r in enumerate(rows) if r['address']==selected),0))
        populate(self.contact_table,[[r['name'],r['address']] for r in self.profile_contacts])
        if self.profile_contacts:self.contact_table.selectRow(next((i for i,r in enumerate(self.profile_contacts) if r['address']==selected_contact),0))

    def change_address_page(self, delta):
        self.address_page+=delta
        self.refresh_address_tables()

    def selected_public_address(self):
        row=self.address_table.currentRow()
        return self.visible_addresses[row]['address'] if 0<=row<len(self.visible_addresses) else self.public_address

    def address_label(self, address):
        return next((r['label'] for r in self.wallets if r['address']==address),address)

    def show_receive(self, address=None):
        address=address if isinstance(address,str) else self.selected_public_address()
        if not address:
            self.notice.setText('Create or import an address first.');return
        self.display_receiving(address)
        self.receive_dialog.show();self.receive_dialog.raise_();self.receive_dialog.activateWindow()

    def display_receiving(self, address):
        if not address:return
        self.public_address=address
        self.receive_address.setText(address)
        index=self.receive_choice.findData(address)
        self.receive_choice.blockSignals(True)
        self.receive_choice.setCurrentIndex(index)
        self.receive_choice.blockSignals(False)
        import qrcode
        output=io.BytesIO();qrcode.make(address).save(output,format='PNG')
        pixmap=QPixmap();pixmap.loadFromData(output.getvalue())
        self.qr.setPixmap(pixmap.scaled(220,220,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.FastTransformation))

    def show_send(self, recipient=None):
        if not self.profile_ready or not self.profile_unlocked:
            self.ensure_profile(lambda:self.show_send(recipient));return
        if not self.wallets:
            self.notice.setText('Create or import an address first.');return
        address=self.selected_public_address() or self.wallets[0]['address']
        if isinstance(recipient,str):self.recipient.setText(recipient)
        generation=self.session_generation
        def selected(result,error):
            if result and generation==self.session_generation:
                self.select_unlocked(result['address'])
                self.send_dialog.show();self.send_dialog.raise_();self.send_dialog.activateWindow()
        self.submit(lambda:self.guard_signer(generation,'select',address=address),selected)

    def change_send_address(self):
        address=self.wallet_choice.currentData()
        if not address or not self.profile_unlocked or address==self.address:return
        self.session_generation+=1
        generation=self.session_generation
        self.submit(lambda:self.guard_signer(generation,'select',address=address),
            lambda result,error:self.select_unlocked(result['address']) if result and generation==self.session_generation else None)

    def copy_selected_address(self):
        address=self.selected_public_address()
        if address:QApplication.clipboard().setText(address)

    def label_selected_address(self):
        address=self.selected_public_address()
        if not address:return
        if not self.profile_unlocked:
            self.ensure_profile(self.label_selected_address);return
        generation=self.session_generation
        name,accepted=QInputDialog.getText(self,'Address label','Label',text=self.address_label(address))
        if accepted:self.submit(lambda:self.guard_signer(generation,'edit_address',address=address,label=name),lambda *_:self.refresh_wallets())

    def default_selected_address(self):
        address=self.selected_public_address()
        if not address:return
        if not self.profile_unlocked:
            self.ensure_profile(self.default_selected_address);return
        generation=self.session_generation
        self.submit(lambda:self.guard_signer(generation,'edit_address',address=address,default=True),lambda *_:self.refresh_wallets())

    def mine_selected_address(self):
        address=self.selected_public_address()
        if not address:return
        self.navigate(3)
        if self.runtime.miner.running or self.mining_busy:
            if not self.mining_busy:self.show_mining_settings(payout=address)
            return
        self.payout.setText(address)
        self.preferences=settings.save(self.directory,payout_address=address)
        self.render_mining_configuration()

    def choose_profile_contact(self):
        contacts=self.profile_contacts
        if not contacts:
            self.notice.setText('Add a recipient in the address book first.');return
        values=[c['name']+' · '+c['address'] for c in contacts]
        value,accepted=QInputDialog.getItem(self,'Address book','Recipient',values,0,False)
        if accepted:self.recipient.setText(contacts[values.index(value)]['address'])

    def edit_profile_contact(self, edit=False, recipient=None):
        if not self.profile_unlocked:
            self.ensure_profile(lambda:self.edit_profile_contact(edit,recipient));return
        row=self.contact_table.currentRow()
        previous=self.profile_contacts[row] if edit and 0<=row<len(self.profile_contacts) else None
        if edit and previous is None:return
        generation=self.session_generation
        dialog=QDialog(self);dialog.setWindowTitle('Edit contact' if edit else 'Add contact')
        dialog.resize(560,220)
        body=QVBoxLayout(dialog);form=QFormLayout()
        name=QLineEdit(previous['name'] if previous else '')
        address=QLineEdit(previous['address'] if previous else recipient or '')
        name.setAccessibleName('Contact label');address.setAccessibleName('Warthog address')
        form.addRow('Label',name);form.addRow('Warthog address',address);body.addLayout(form)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);body.addWidget(buttons)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            label,value=name.text(),address.text()
            self.submit(lambda:self.guard_signer(generation,'save_contact',name=label,address=value,
                previous=previous['address'] if previous else None),lambda *_:self.refresh_wallets())
        dialog.deleteLater()

    def delete_profile_contact(self):
        row=self.contact_table.currentRow()
        if not 0<=row<len(self.profile_contacts):return
        if not self.profile_unlocked:
            self.ensure_profile(self.delete_profile_contact);return
        contact=self.profile_contacts[row]
        generation=self.session_generation
        if QMessageBox.question(self,'Remove contact',f"Remove {contact['name']} from the address book?",
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)==QMessageBox.StandardButton.Yes:
            self.submit(lambda:self.guard_signer(generation,'delete_contact',address=contact['address']),lambda *_:self.refresh_wallets())

    def send_to_contact(self):
        row=self.contact_table.currentRow()
        if 0<=row<len(self.profile_contacts):self.show_send(self.profile_contacts[row]['address'])
