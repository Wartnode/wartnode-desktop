"""Regression cases for the source-validated Step 5 findings; synthetic data only."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog,QInputDialog,QPlainTextEdit
from wartnode import settlement,settings,updates
from wartnode.paths import data_root
from wartnode.processes import Runtime
from wartnode.rpc import NodeRPC,NodeError
from wartnode.remote_history import RemoteHistory
from wartnode.signer import Signer
from wartnode.compat import wallet_utils as crypto
from wartnode.ui import Window
from test_core import FakeRPC,OWNER,OTHER
from test_ui import APP,until


class SecretDialogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.window=Window(data_root(self.temp.name),autostart=False)
        self.window.timer.stop()
        until(lambda:not self.window.tasks)

    def tearDown(self):
        self.window.exiting=True;self.window.signer.close()
        self.window.pool.waitForDone(30000);self.window.tray.hide();self.window.close()
        APP.processEvents();self.temp.cleanup()

    def test_idle_lock_clears_an_open_phrase_and_retains_a_recoverable_wallet(self):
        observed={}
        timer=QTimer()
        def inspect():
            for dialog in self.window.secret_dialogs:
                phrase=dialog.findChild(QPlainTextEdit)
                if phrase is not None:
                    observed['words']=len(phrase.toPlainText().split())
                    self.window.lock_wallet()
                    observed['cleared']=phrase.toPlainText()==''
                    observed['rejected']=dialog.result()==QDialog.DialogCode.Rejected
                    timer.stop();break
        timer.timeout.connect(inspect);timer.start(20)
        with patch.object(self.window,'password',return_value='synthetic-phrase-password'):
            self.window.create_wallet()
            until(lambda:bool(observed) and not self.window.tasks)
        timer.stop()
        self.assertEqual(observed,{'words':24,'cleared':True,'rejected':True})
        self.assertIsNone(self.window.address)
        address=self.window.signer.call('list')['wallets'][0]['address']
        self.window.signer.call('unlock',address=address,password='synthetic-phrase-password')
        backup=Path(self.temp.name)/'recovery.wartbackup'
        self.window.signer.call('backup',path=str(backup),password='synthetic-backup-password')
        with tempfile.TemporaryDirectory() as restored:
            signer=Signer(restored)
            try:
                signer.call('restore',{'path':str(backup),'password':'synthetic-backup-password'})
                self.assertEqual(signer.call('unlock',{'address':address,'password':'synthetic-phrase-password'})['address'],address)
            finally:signer.store.con.close()

    def test_lock_cancels_and_clears_secret_entry(self):
        observed={}
        def lock():
            dialog=next(iter(self.window.secret_dialogs))
            dialog.setTextValue('synthetic-secret')
            self.window.lock_wallet()
            observed['value']=dialog.textValue()
        QTimer.singleShot(30,lock)
        self.assertIsNone(self.window.secret_input('Test password','Password'))
        self.assertEqual(observed['value'],'')
        self.assertFalse(self.window.secret_dialogs)


class SettlementRPC(FakeRPC):
    current='ab'*32
    def head(self,require_synced=False):
        return dict(super().head(require_synced),hash=type(self).current)
    def request(self,path):return {'header':{'hash':type(self).current}}
    def block(self,height):return {'height':height,'header':{'hash':type(self).current},'body':{'transfers':[]}}


class SettlementTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.signer=Signer(self.temp.name)
        self.signer.call('import',{'kind':'private','secret':'00'*31+'01','password':'synthetic-password'})
        self.transport=patch('wartnode.signer.NodeRPC',SettlementRPC);self.transport.start()
        SettlementRPC.current='ab'*32;FakeRPC.result='pending';FakeRPC.fee=9992
        self.signer.call('balance',{})

    def tearDown(self):
        self.transport.stop();self.signer.store.con.close();self.temp.cleanup()

    def confirmed(self,index,amount=1000000):
        row={'wallet_address':OWNER,'tx_hash':f'{index:064x}','direction':'outgoing','counterparty':OTHER,
            'amount_e8':str(amount),'fee_e8':'9992','timestamp':index,'status':'confirmed','confirmations':1,
            'block_height':1,'peer':self.signer.rpc,'payload':json.dumps({'pinHeight':32,'nonceId':index}), 'network':'mainnet'}
        self.signer.store.save_history([row]);return row

    def test_reorg_reserves_every_settled_payment_before_balance_or_review(self):
        for index in range(1,105):self.confirmed(index)
        SettlementRPC.current='fa'*32  # Head changed; old canonical header no longer matches.
        balance=self.signer.call('balance',{})
        self.assertEqual(balance['reserved'],104*1009992)
        self.assertEqual(len([r for r in self.signer.store.history(OWNER) if r['status']=='uncertain']),104)
        with self.assertRaisesRegex(ValueError,'Insufficient'):
            self.signer.call('review',{'recipient':OTHER,'amount':'10'})
        with patch('wartnode.node_history.NodeHistory.hash_page',return_value={'occurrences':[],'next_before':0}):
            self.signer.call('reconcile',{'address':OWNER})
            self.signer.call('reconcile',{'address':OWNER})
        self.assertEqual(self.signer.store.con.execute('SELECT COUNT(*) FROM desktop_reconcile_checks').fetchone()[0],104)
        self.assertEqual(self.signer.store.reserved_amount(OWNER),104*1009992)

    def test_unavailable_canonical_check_cannot_advertise_or_authorize_funds(self):
        self.confirmed(1,900000000);SettlementRPC.current='fa'*32
        with patch.object(SettlementRPC,'request',side_effect=NodeError('unavailable')):
            for action,args in (('balance',{}),('review',{'recipient':OTHER,'amount':'1'})):
                with self.assertRaisesRegex(NodeError,'unavailable'):self.signer.call(action,args)

    def test_legacy_or_restored_confirmations_start_reserved(self):
        self.confirmed(1,900000000)
        with self.signer.store.con:self.signer.store.con.execute('DELETE FROM desktop_settlement_anchor')
        self.assertEqual(self.signer.call('balance',{})['reserved'],900009992)

    def test_reorg_after_review_blocks_send_before_broadcast_even_with_same_pin(self):
        self.confirmed(1,900000000)
        review=self.signer.call('review',{'recipient':OTHER,'amount':'2'})
        SettlementRPC.current='fa'*32
        with patch.object(crypto,'prepare_tx',return_value=crypto.SignedTransfer('cd'*32,{'pinHeight':32,'nonceId':3})),\
             patch.object(SettlementRPC,'broadcast') as broadcast:
            with self.assertRaisesRegex(ValueError,'Insufficient'):
                self.signer.call('send',{'token':review['token'],'password':'synthetic-password'})
            broadcast.assert_not_called()


class SlowHandler(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def do_GET(self):
        try:
            self.send_response(200);self.send_header('Content-Length','1000000');self.end_headers()
            for _ in range(200):
                self.wfile.write(b' ');self.wfile.flush();time.sleep(.02)
        except (OSError,ValueError):pass


class DeadlineTests(unittest.TestCase):
    def setUp(self):
        self.server=ThreadingHTTPServer(('127.0.0.1',0),SlowHandler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()

    def test_progressive_rpc_body_honors_deadline_and_cancellation(self):
        for cancel in (False,True):
            event=threading.Event();client=NodeRPC(self.url,deadline=time.monotonic()+(.8 if cancel else .2),cancel=event)
            timer=threading.Timer(.12,event.set) if cancel else None
            if timer:timer.start()
            start=time.monotonic()
            with self.assertRaises(NodeError):client.request('/chain/head')
            self.assertLess(time.monotonic()-start,.65)
            if timer:timer.join()

    def test_hosted_history_and_update_share_the_total_body_budget(self):
        client=NodeRPC(self.url,deadline=time.monotonic()+.2)
        with patch('wartnode.remote_history.EXPLORER_ENDPOINT',self.url):
            start=time.monotonic()
            with self.assertRaises(NodeError):RemoteHistory(client).index_page('ab'*32,0)
            self.assertLess(time.monotonic()-start,.65)
        with tempfile.TemporaryDirectory() as directory,patch('wartnode.updates.https_url'):
            path=Path(directory)/'partial.zip';start=time.monotonic()
            with self.assertRaises(ValueError):updates.download(self.url,path,1000000,budget=.2)
            self.assertLess(time.monotonic()-start,.65);self.assertFalse(path.exists())


class ManagedConfigurationTests(unittest.TestCase):
    def test_custom_chain_config_cannot_enable_extra_node_listeners(self):
        with tempfile.TemporaryDirectory() as directory:
            root=data_root(directory);settings.save(root,setup_complete=True)
            original=root/'node/config.toml';content='[publicrpc]\nbind="0.0.0.0:9999"\n'
            original.write_text(content)
            runtime=Runtime(root)
            with patch('wartnode.processes.verified_component',return_value='core.exe'),\
                 patch('wartnode.processes.socket.socket'),patch.object(runtime.node,'start') as start:
                runtime.start_node()
            config=Path(next(a.split('=',1)[1] for a in start.call_args.args[0] if str(a).startswith('--config=')))
            self.assertEqual(original.read_text(),content)
            self.assertEqual(config,root/'runtime/managed-node.toml')
            self.assertNotIn('publicrpc',config.read_text())
