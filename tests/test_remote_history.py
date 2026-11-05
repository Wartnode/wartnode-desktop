"""Hosted discovery must resolve occurrences against canonical blocks."""
import unittest
from unittest.mock import patch
from wartnode.remote_history import RemoteHistory
from wartnode.rpc import NodeRPC,NodeError
from wartnode.connection import LITE_ENDPOINT
from wartnode.node_history import MAINNET_BLOCK_ONE
from test_core import OWNER,OTHER,block

HASH='cd'*32

class Client:
    remote=True
    def head(self):return {'height':64,'hash':'ab'*32}
    def request(self,path):return {'header':{'hash':MAINNET_BLOCK_ONE if path=='/chain/block/1/header' else 'ab'*32}}
    def block(self,height):return block(height,'ab'*32,[{'txHash':HASH,'fromAddress':OTHER,'toAddress':OWNER,'amount':'1','fee':'0.00009992','nonceId':1,'pinHeight':32}])

class RemoteHistoryTests(unittest.TestCase):
    def test_multiple_occurrences_with_same_hash_keep_distinct_positions(self):
        history=RemoteHistory(Client())
        data={'items':[{'hash':HASH,'height':32,'position':0},{'hash':HASH,'height':32,'position':1}],'nextCursor':None}
        with patch.object(history,'index_page',return_value=(data,{'coverage':{'complete':True},'stale':False})):
            result=history.hash_page(HASH)
        self.assertEqual(len(result['occurrences']),2)
        self.assertEqual([r['kind'] for r in result['occurrences']],['reward','transfer'])
        self.assertTrue(result['complete']);self.assertIsNone(history.chain_path)
    def test_index_cannot_invent_transactions_or_claim_stale_history_complete(self):
        history=RemoteHistory(Client())
        for position in (2,-1,True):
            with patch.object(history,'index_page',return_value=({'items':[{'hash':HASH,'height':32,'position':position}]},{})):
                with self.assertRaises(NodeError):history.hash_page(HASH)
        with patch.object(history,'index_page',return_value=({'items':[]},{'coverage':{'complete':True},'stale':True})):
            self.assertFalse(history.hash_page(HASH)['complete'])
    def test_invalid_or_repeated_cursor_and_reorg_fail_closed(self):
        history=RemoteHistory(Client())
        with self.assertRaises(NodeError):history.hash_page(HASH,'../bad')
        with patch.object(history,'index_page',return_value=({'items':[],'nextCursor':'abc'},{})):
            with self.assertRaises(NodeError):history.hash_page(HASH,'abc')
        with self.assertRaises(NodeError):history.hash_page(HASH,anchor={'height':64,'hash':'00'*32})
    def test_lite_transport_only_accepts_the_exact_fixed_https_origin(self):
        self.assertTrue(NodeRPC(LITE_ENDPOINT).remote)
        for peer in ('http://mainnet.wartnode.com','https://other.example','https://mainnet.wartnode.com:443','https://user@mainnet.wartnode.com','https://mainnet.wartnode.com/','https://mainnet.wartnode.com?x=1'):
            with self.subTest(peer=peer),self.assertRaises(NodeError):NodeRPC(peer)
        with self.assertRaisesRegex(NodeError,'Mining'):NodeRPC(LITE_ENDPOINT).template()

if __name__=='__main__':unittest.main()
