import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from wartnode.compat import wallet_utils as crypto
from wartnode.explorer import Explorer
from wartnode.rpc import NodeRPC, NodeError, BroadcastUncertain, GENESIS
from wartnode.signer import Signer

OWNER, _ = crypto.wallet_from_pkhex('00'*31+'01')
OTHER, _ = crypto.wallet_from_pkhex('00'*31+'02')
THIRD, _ = crypto.wallet_from_pkhex('00'*31+'03')


def block(height=1, block_hash='ab'*32, transfers=None):
    return {'height':height,'header':{'hash':block_hash,'prevHash':GENESIS,'timestamp':1700000000},
        'body':{'rewards':[{'txHash':'cd'*32,'toAddress':OWNER,'amount':'3.00000000'}], 'transfers':transfers or []}}


class LoopbackTests(unittest.TestCase):
    def test_nested_empty_peer_response_cannot_make_node_ready(self):
        client=NodeRPC()
        for peers in ([],['127.0.0.1:9186','127.0.0.1:9187']):
            with patch.object(client,'request',return_value={'data':peers}):
                self.assertEqual(client.connected_peers(),peers)
        with patch.object(client,'request',return_value={'data':[]}):
            with self.assertRaisesRegex(NodeError,'no connected'):client.require_network()
        from types import SimpleNamespace
        from wartnode.processes import Runtime
        with tempfile.TemporaryDirectory() as directory:
            runtime=Runtime(directory)
            runtime.node=SimpleNamespace(running=True,rpc_ready_at=0)
            with patch('wartnode.rpc.NodeRPC',return_value=client),\
                 patch.object(client,'head',return_value={'synced':True,'height':64,'hash':'ab'*32}),\
                 patch.object(client,'request',return_value={'data':[]}):
                state=runtime.probe()
                self.assertEqual(state['peers'],0)
                self.assertFalse(runtime.ready)
    def test_rejects_remote_redirect_origins_and_other_networks(self):
        for url in ('http://example.com:19400','https://127.0.0.1:19400','http://localhost:19400',
                    'http://127.0.0.1:19400/path','http://user:pw@127.0.0.1:19400'):
            with self.subTest(url=url),self.assertRaises(ValueError):
                NodeRPC(url)
        with self.assertRaises(ValueError):
            NodeRPC(network='testnet')

    def test_empty_genesis_never_ready_even_when_node_says_synced(self):
        client=NodeRPC()
        with patch.object(client,'request',return_value={'height':0,'hash':GENESIS,'synced':True}):
            self.assertFalse(client.head()['synced'])
            with self.assertRaises(NodeError):
                client.head(require_synced=True)

    def test_wrong_genesis_and_testnet_template_fail_closed(self):
        client=NodeRPC()
        with patch.object(client,'request',return_value={'height':0,'hash':'00'*32,'synced':True}):
            with self.assertRaises(NodeError): client.head()
        for result in ({'testnet':True},{},{'testnet':'false'}):
            with patch.object(client,'request',return_value=result),self.assertRaises(NodeError): client.template()


class ExplorerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.index=Explorer(Path(self.temp.name)/'explorer.db')

    def tearDown(self):
        self.index.con.close()
        self.temp.cleanup()

    def test_same_hash_keeps_distinct_senders_and_occurrences(self):
        transfers=[{'txHash':'ee'*32,'fromAddress':sender,'toAddress':OTHER,'amount':'1.00000000',
                    'fee':'0.00009992','pinHeight':0,'nonceId':5} for sender in (OWNER,THIRD)]
        self.index.ingest(block(transfers=transfers))
        self.assertEqual(len(self.index.search('ee'*32)['occurrences']),2)
        self.assertEqual(len(self.index.search(OWNER)['occurrences']),2)
        self.index.ingest(block(transfers=transfers))
        self.assertEqual(len(self.index.search('ee'*32)['occurrences']),2)
        self.assertEqual(len(self.index.search('cd'*32)['occurrences']),1)

    def test_replacement_atomically_removes_orphaned_rows(self):
        self.index.ingest(block())
        child=block(2,'de'*32)
        child['header']['prevHash']='ab'*32
        self.index.ingest(child)
        replacement=block(1,'fa'*32)
        self.index.ingest(replacement)
        self.assertEqual(self.index.recent(),[{'height':1,'hash':'fa'*32,'timestamp':1700000000}])
        self.assertEqual(len(self.index.search('cd'*32)['occurrences']),1)

    def test_malformed_replacement_preserves_existing_data(self):
        self.index.ingest(block())
        invalid=block(block_hash='fa'*32)
        invalid['body']['rewards'][0]['toAddress']='invalid'
        with self.assertRaises(ValueError):self.index.ingest(invalid)
        self.assertEqual(self.index.recent()[0]['hash'],'ab'*32)

    def test_journal_match_requires_sender_pin_nonce_amount_fee_and_recipient(self):
        tx={'txHash':'ee'*32,'fromAddress':OWNER,'toAddress':OTHER,'amount':'1.00000000',
            'fee':'0.00009992','pinHeight':32,'nonceId':5}
        self.index.ingest(block(transfers=[tx]))
        row={'tx_hash':'ee'*32,'counterparty':OTHER,'amount_e8':'100000000','fee_e8':'9992',
             'payload':json.dumps({'pinHeight':32,'nonceId':5})}
        self.assertIsNotNone(self.index.transaction_matches(OWNER,row))
        self.assertIsNone(self.index.transaction_matches(THIRD,row))
        row['payload']=json.dumps({'pinHeight':32,'nonceId':6})
        self.assertIsNone(self.index.transaction_matches(OWNER,row))


class FakeRPC:
    fee=9992
    result='pending'
    broadcasts=0
    def __init__(self,*args,**kwargs): pass
    def head(self,require_synced=False):return {'height':64,'hash':'ab'*32,'synced':True,'pinHeight':32,'pinHash':'ef'*32}
    def require_network(self):pass
    def balance(self,address):return '10.00000000'
    def minimum_fee(self):return self.fee
    def broadcast(self,payload,expected_hash=None):
        type(self).broadcasts+=1
        if self.result=='uncertain':raise BroadcastUncertain('timeout')
        if self.result=='rejected':raise NodeError('rejected')
        return expected_hash


class SignerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.signer=Signer(self.temp.name)
        self.password='synthetic-test-password'
        self.signer.call('import',{'secret':'00'*31+'01','kind':'private','password':self.password})
        FakeRPC.fee,FakeRPC.result,FakeRPC.broadcasts=9992,'pending',0

    def tearDown(self):
        self.signer.store.con.close()
        self.temp.cleanup()

    def test_wallet_encryption_lock_wrong_password_and_backup_restore(self):
        self.assertNotIn('secret',json.dumps(self.signer.call('list',{})))
        self.signer.call('lock',{})
        with self.assertRaises(ValueError):self.signer.call('unlock',{'address':OWNER,'password':'wrong password'})
        self.signer.call('unlock',{'address':OWNER,'password':self.password})
        backup=Path(self.temp.name)/'wallet.wartbackup'
        self.signer.call('backup',{'path':str(backup),'password':'backup password'})
        with tempfile.TemporaryDirectory() as directory:
            restored=Signer(directory)
            try:
                restored.call('restore',{'path':str(backup),'password':'backup password'})
                self.assertEqual(restored.call('unlock',{'address':OWNER,'password':self.password})['address'],OWNER)
            finally:restored.store.con.close()

    def test_polling_does_not_prevent_autolock(self):
        self.signer.last_active=time.monotonic()-301
        self.assertIsNone(self.signer.call('list',{})['address'])

    def review(self,maximum=False):
        return self.signer.call('review',{'recipient':OTHER,'amount':'1','max':maximum})

    @patch('wartnode.signer.NodeRPC',FakeRPC)
    def test_review_uses_exact_fee_and_send_max_reservations(self):
        result=self.review(True)
        self.assertEqual(result['fee'],9992)
        self.assertEqual(result['amount']+result['fee'],1000000000)

    @patch('wartnode.signer.NodeRPC',FakeRPC)
    def test_fee_change_invalidates_review_before_sign_or_broadcast(self):
        result=self.review()
        FakeRPC.fee=10000
        with self.assertRaises(ValueError):self.signer.call('send',{'token':result['token'],'password':self.password})
        self.assertEqual(FakeRPC.broadcasts,0)

    @patch('wartnode.signer.NodeRPC',FakeRPC)
    def test_uncertain_broadcast_is_reserved_and_cannot_repeat_confirmation(self):
        result=self.review()
        FakeRPC.result='uncertain'
        prepared=crypto.SignedTransfer('ed'*32,{'pinHeight':32,'nonceId':7,'toAddr':OTHER,'amountE8':100000000,'feeE8':9992})
        with patch.object(crypto,'prepare_tx',return_value=prepared):
            sent=self.signer.call('send',{'token':result['token'],'password':self.password})
            self.assertEqual(sent['status'],'uncertain')
            self.assertEqual(self.signer.store.reserved_amount(OWNER),100009992)
            with self.assertRaises(ValueError):self.signer.call('send',{'token':result['token'],'password':self.password})
        self.assertEqual(FakeRPC.broadcasts,1)
        self.signer.store.con.close()
        self.signer=Signer(self.temp.name)
        self.assertEqual(self.signer.store.reserved_amount(OWNER),100009992)

    @patch('wartnode.signer.NodeRPC',FakeRPC)
    def test_explicit_rejection_releases_reservation(self):
        result=self.review()
        FakeRPC.result='rejected'
        prepared=crypto.SignedTransfer('ed'*32,{'pinHeight':32,'nonceId':7})
        with patch.object(crypto,'prepare_tx',return_value=prepared),self.assertRaises(NodeError):
            self.signer.call('send',{'token':result['token'],'password':self.password})
        self.assertEqual(self.signer.store.reserved_amount(OWNER),0)

    @patch('wartnode.signer.NodeRPC',FakeRPC)
    def test_confirmed_payment_orphaned_by_reorg_becomes_reserved_again(self):
        reviewed=self.review()
        payload={'pinHeight':32,'nonceId':7,'toAddr':OTHER,'amountE8':100000000,'feeE8':9992}
        prepared=crypto.SignedTransfer('ed'*32,payload)
        with patch.object(crypto,'prepare_tx',return_value=prepared):
            self.signer.call('send',{'token':reviewed['token'],'password':self.password})
        row=self.signer.store.history(OWNER)[0]
        self.signer.store.save_history([dict(row,status='confirmed',block_height=1,confirmations=64)])
        self.assertEqual(self.signer.store.reserved_amount(OWNER),0)
        index_path=Path(self.temp.name)/'explorer.db'
        index=Explorer(index_path)
        transfer={'txHash':'ed'*32,'fromAddress':OWNER,'toAddress':OTHER,'amount':'1.00000000',
            'fee':'0.00009992','pinHeight':32,'nonceId':7}
        index.ingest(block(transfers=[transfer]))
        index.con.close()
        # The explorer can be absent or corrupt without determining confirmation.
        index_path.write_bytes(b'not a SQLite database')
        with patch.object(FakeRPC,'block',create=True,return_value=block(transfers=[transfer])),\
             patch('wartnode.node_history.NodeHistory.hash_page',side_effect=AssertionError('Confirmed path needs no history index')):
            self.signer.call('reconcile',{'address':OWNER})
        self.assertEqual(self.signer.store.history(OWNER)[0]['status'],'confirmed')
        self.assertEqual(self.signer.store.reserved_amount(OWNER),0)
        index_path.unlink()
        with patch.object(FakeRPC,'block',create=True,return_value=block(transfers=[transfer])):
            self.signer.call('reconcile',{'address':OWNER})
        self.assertEqual(self.signer.store.history(OWNER)[0]['status'],'confirmed')
        with patch.object(FakeRPC,'request',create=True,return_value={'header':{'hash':'fa'*32}}),\
             patch.object(FakeRPC,'head',return_value={'height':64,'hash':'fa'*32,'synced':True,'pinHeight':32,'pinHash':'ef'*32}),\
             patch.object(FakeRPC,'block',create=True,return_value=block(block_hash='fa'*32)),\
             patch('wartnode.node_history.NodeHistory.hash_page',return_value={'occurrences':[],'next_before':0}):
            self.signer.call('reconcile',{'address':OWNER,'index_path':str(index_path)})
        self.assertEqual(self.signer.store.history(OWNER)[0]['status'],'uncertain')
        self.assertEqual(self.signer.store.reserved_amount(OWNER),100009992)
        # A lookup failure after detecting an orphan must keep the reservation durable.
        row=self.signer.store.history(OWNER)[0]
        self.signer.store.save_history([dict(row,status='confirmed',block_height=1,confirmations=64)])
        # A legacy/restored confirmation has no trustworthy settlement anchor.
        with self.signer.store.con:self.signer.store.con.execute('DELETE FROM desktop_settlement_anchor')
        with patch.object(FakeRPC,'block',create=True,return_value=block(block_hash='fa'*32)),\
             patch('wartnode.node_history.NodeHistory.hash_page',side_effect=NodeError('busy')),\
             self.assertRaisesRegex(NodeError,'busy'):
            self.signer.call('reconcile',{'address':OWNER})
        self.assertEqual(self.signer.store.reserved_amount(OWNER),100009992)


if __name__=='__main__':unittest.main()
