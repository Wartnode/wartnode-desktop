"""Rewards survive restarts/cache clearing and only canonical occurrences count."""
import json
from pathlib import Path
import tempfile
import unittest
from wartnode.activity import ActivityStore
from wartnode.explorer import Explorer
from wartnode.node_history import transaction_row,verify_anchor
from wartnode.rpc import NodeError
from test_core import OWNER,OTHER,block


class History:
    def __init__(self,count=4):
        self.client=self;self.blocks={};self.calls=[];self.forged=False;self.fork=False
        for height in range(1,count+1):
            value=block(height,f'{height:064x}')
            value['header']['timestamp']=1700000000+height
            value['body']['rewards']=[{'txHash':'ab'*32,'toAddress':OWNER,'amount':'1.50000000'}]
            self.blocks[height]=value
    def anchor(self):return {'height':len(self.blocks),'hash':self.blocks[len(self.blocks)]['header']['hash']}
    def request(self,path):
        height=int(path.split('/')[3]);return {'header':self.blocks[height]['header']}
    def block(self,height):self.calls.append(height);return self.blocks[height]
    def address_page(self,owner,before=0,anchor=None):
        rows=[]
        for height in sorted(self.blocks,reverse=True):
            if before and height>=before:continue
            tx=self.blocks[height]['body']['rewards'][0]
            row=transaction_row(dict(tx,amount='9.00000000') if self.forged else tx,height)
            rows.append(row)
        next_before=rows[99]['height'] if len(rows)>=100 else 0
        return {'occurrences':rows[:100],'next_before':next_before,'complete':not next_before,'anchor':anchor}


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'activity.db'
        self.store=ActivityStore(self.path);self.history=History()
    def tearDown(self):self.store.close();self.temp.cleanup()
    def test_occurrence_identity_and_rewards_survive_reopen_and_explorer_clear(self):
        self.store.sync(self.history,OWNER)
        snapshot=self.store.snapshot([OWNER],OWNER)
        self.assertEqual((snapshot['reward_count'],snapshot['reward_total']),(4,600000000))
        self.assertTrue(snapshot['reward_complete'])
        cache=Explorer(self.path.with_name('explorer.db'));cache.clear();cache.con.close()
        self.store.close();self.store=ActivityStore(self.path)
        self.assertEqual(self.store.snapshot([OWNER],OWNER)['reward_count'],4)
        self.store.sync(self.history,OWNER)
        self.assertEqual(self.store.snapshot([OWNER],OWNER)['reward_count'],4)
    def test_partial_work_is_bounded_and_resumes_without_dropping_rows(self):
        self.history=History(105)
        for _ in range(60):
            before=len(self.history.calls);self.store.sync(self.history,OWNER,budget=8)
            self.assertLessEqual(len(self.history.calls)-before,8)
            snap=self.store.snapshot([OWNER],OWNER)
            if snap['reward_complete']:break
        self.assertTrue(snap['reward_complete'])
        self.assertEqual(snap['reward_count'],105)
    def test_forged_history_is_rejected_before_confirmation(self):
        self.history.forged=True
        with self.assertRaisesRegex(NodeError,'canonical block'):self.store.sync(self.history,OWNER)
        self.assertEqual(self.store.snapshot([OWNER],OWNER)['reward_count'],0)
    def test_complete_history_becomes_partial_while_new_rewards_are_still_unverified(self):
        self.store.sync(self.history,OWNER)
        self.assertTrue(self.store.snapshot([OWNER],OWNER)['reward_complete'])
        self.store.close();self.store=ActivityStore(self.path)
        self.history=History(20)
        self.store.sync(self.history,OWNER,budget=2)
        snap=self.store.snapshot([OWNER],OWNER)
        self.assertEqual(snap['reward_count'],6)
        self.assertFalse(snap['reward_complete'])
        for _ in range(12):
            self.store.sync(self.history,OWNER,budget=2)
            snap=self.store.snapshot([OWNER],OWNER)
            if snap['reward_complete']:break
        self.assertTrue(snap['reward_complete']);self.assertEqual(snap['reward_count'],20)
    def test_reorg_invalidates_totals_and_keeps_orphaned_rewards(self):
        self.store.sync(self.history,OWNER)
        self.history.blocks[4]['header']['hash']='ee'*32
        self.history.blocks[4]['body']['rewards'][0]['amount']='2.00000000'
        self.store.sync(self.history,OWNER)
        snap=self.store.snapshot([OWNER],OWNER)
        self.assertEqual(snap['reward_count'],4)
        self.assertEqual(snap['reward_total'],650000000)
        self.assertEqual([r['status'] for r in snap['rewards']].count('orphaned'),1)
    def test_network_failure_after_reorg_does_not_leave_old_confirmed_totals(self):
        self.store.sync(self.history,OWNER)
        self.history.blocks[4]['header']['hash']='ee'*32
        self.history.address_page=lambda *a,**k:(_ for _ in ()).throw(NodeError('offline'))
        with self.assertRaises(NodeError):self.store.sync(self.history,OWNER)
        snap=self.store.snapshot([OWNER],OWNER)
        self.assertFalse(snap['reward_complete']);self.assertEqual(snap['reward_count'],0)


if __name__=='__main__':unittest.main()
