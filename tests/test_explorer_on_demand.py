"""Bounded cache and official-node history adapter contracts, using public test keys."""
import hashlib
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from wartnode.explorer import Explorer,MAX_DATABASE_BYTES
from wartnode.node_history import NodeHistory,MAINNET_BLOCK_ONE
from wartnode.rpc import GENESIS,NodeError
from test_core import OWNER,OTHER,THIRD,block

HASH='ee'*32

class HistoryRPC:
    def __init__(self,database):
        self.database=database
        self.changed=False
        self.wrong_transaction=False
        self.calls=[]
        self.history=None
    def head(self):return {'height':2000,'hash':'ab'*32,'synced':True}
    def request(self,path):
        self.calls.append(path)
        if path=='/chain/block/1/header':return {'header':{'hash':MAINNET_BLOCK_ONE}}
        if path=='/chain/block/2000/header':return {'header':{'hash':('fa' if self.changed else 'ab')*32}}
        if path.startswith('/transaction/lookup/'):
            # A read transaction must not remain open across an RPC round trip.
            with closing(sqlite3.connect(self.database,timeout=0)) as connection, connection:
                connection.execute('BEGIN EXCLUSIVE');connection.rollback()
            number=int(path.rsplit('/',1)[1])
            return {'transaction':{'txHash':'ff'*32 if self.wrong_transaction else HASH,'type':'Transfer',
                'blockHeight':number+10,'fromAddress':OWNER if number%2 else THIRD,'toAddress':OTHER,
                'amount':'1.00000000','fee':'0.00009992','nonceId':number,'pinHeight':0}}
        if path.startswith('/account/'):
            if isinstance(self.history,Exception):raise self.history
            return self.history
        raise AssertionError(path)

class HistoryAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.database=Path(self.temp.name)/'chain.db3'
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.executescript('CREATE TABLE History(id INTEGER PRIMARY KEY,hash BLOB,data BLOB);'
                'CREATE INDEX history_index ON History(hash);CREATE TABLE Blocks(height INTEGER,hash BLOB UNIQUE);'
                'CREATE TABLE Consensus(height INTEGER PRIMARY KEY,block_id INTEGER);')
            connection.execute('INSERT INTO Blocks VALUES(2000,?)',(bytes.fromhex('ab'*32),))
            connection.execute('INSERT INTO Consensus VALUES(2000,1)')
            connection.executemany('INSERT INTO History VALUES(?,?,?)',[(i,bytes.fromhex(HASH),b'fixture') for i in range(1,126)])
        self.rpc=HistoryRPC(self.database)
        self.history=NodeHistory(self.rpc,self.database)
    def tearDown(self):self.temp.cleanup()

    def test_collision_pages_have_no_omissions_and_never_write_node_database(self):
        before_hash=hashlib.sha256(self.database.read_bytes()).hexdigest()
        pages=[];cursor=0;anchor=None
        while True:
            result=self.history.hash_page(HASH,cursor,anchor)
            pages.append(result)
            if not result['next_before']:break
            cursor,anchor=result['next_before'],result['anchor']
        self.assertEqual([len(p['occurrences']) for p in pages],[50,50,25])
        entries=[row for p in pages for row in p['occurrences']]
        self.assertEqual([row['history_id'] for row in entries],list(range(125,0,-1)))
        self.assertEqual({row['sender'] for row in entries},{OWNER,THIRD})
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(),before_hash)

    def test_query_uses_existing_covering_hash_index(self):
        captured=[]
        original=sqlite3.connect
        def tracked(*args,**kwargs):
            connection=original(*args,**kwargs)
            connection.set_trace_callback(captured.append)
            return connection
        with patch('wartnode.node_history.sqlite3.connect',side_effect=tracked):self.history.hash_page(HASH)
        query=next(sql for sql in captured if sql.startswith('SELECT id FROM History'))
        with closing(original(self.database)) as connection, connection:
            plan=' '.join(row[3] for row in connection.execute('EXPLAIN QUERY PLAN '+query))
        self.assertIn('history_index',plan)
        self.assertIn('SEARCH',plan)
        self.assertNotIn('SCAN',plan)
        self.assertNotIn('TEMP B-TREE',plan)

    def test_block_hash_uses_stored_height_rather_than_sqlite_rowid(self):
        self.assertEqual(self.history.block_height('ab'*32),2000)
        self.assertIsNone(self.history.block_height('00'*32))

    def test_reorg_invalidates_an_older_cursor(self):
        page=self.history.hash_page(HASH)
        self.rpc.changed=True
        with self.assertRaisesRegex(NodeError,'chain changed'):
            self.history.hash_page(HASH,page['next_before'],page['anchor'])

    def test_reused_history_id_with_wrong_hash_fails(self):
        self.rpc.wrong_transaction=True
        with self.assertRaisesRegex(NodeError,'History changed'):self.history.hash_page(HASH)

    def test_unsupported_schema_fails_without_creating_an_index(self):
        with closing(sqlite3.connect(self.database)) as connection, connection:connection.execute('DROP INDEX history_index')
        with self.assertRaisesRegex(NodeError,'history format is not supported'):self.history.hash_page(HASH)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            self.assertEqual(connection.execute('PRAGMA index_list(History)').fetchall(),[])

    def test_address_pages_retain_cursor_and_reject_wrong_owner(self):
        tx={'txHash':HASH,'toAddress':OWNER,'amount':'1.00000000'}
        self.rpc.history={'balance':'100.00000000','fromId':10,'perBlock':[
            {'height':100,'transactions':{'rewards':[dict(tx) for _ in range(100)],'transfers':[]}}]}
        result=self.history.address_page(OWNER)
        self.assertIn(f'/account/{OWNER}/history/{2**63-1}',self.rpc.calls)
        self.assertEqual(len(result['occurrences']),100)
        self.assertEqual(result['next_before'],10)
        with self.assertRaisesRegex(NodeError,'cursor'):self.history.address_page(OWNER,10,result['anchor'])
        self.rpc.history['perBlock'][0]['transactions']['rewards'][0]['toAddress']=OTHER
        with self.assertRaisesRegex(NodeError,'different address'):self.history.address_page(OWNER)

    def test_unknown_account_is_empty_but_other_errors_are_not_hidden(self):
        self.rpc.history=NodeError('not found',57)
        self.assertEqual(self.history.address_page(OWNER)['occurrences'],[])
        self.rpc.history=NodeError('busy',99)
        with self.assertRaisesRegex(NodeError,'busy'):self.history.address_page(OWNER)

class CachePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'explorer.db'
    def tearDown(self):self.temp.cleanup()
    def chain_block(self,height):
        value=block(height,f'{height:064x}')
        value['header']['prevHash']=GENESIS if height==1 else f'{height-1:064x}'
        return value

    def test_lru_and_payload_budgets_survive_reopen(self):
        cache=Explorer(self.path,max_blocks=3,max_bytes=2000)
        try:
            for height in range(1,30):cache.ingest(self.chain_block(height))
            self.assertLessEqual(cache.summary()['cached_blocks'],3)
            self.assertLessEqual(cache.summary()['payload_bytes'],2000)
            self.assertLessEqual(cache.summary()['database_bytes'],MAX_DATABASE_BYTES)
        finally:cache.con.close()
        cache=Explorer(self.path,max_blocks=3,max_bytes=2000)
        try:self.assertIsNotNone(cache.search('29')['block'])
        finally:cache.con.close()

    def test_legacy_migration_and_clear_reclaim_space_without_touching_wallet(self):
        wallet=Path(self.temp.name)/'wallet.db'
        wallet.write_bytes(b'preserved payment journal fixture')
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.executescript('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);'
                'CREATE TABLE blocks(raw TEXT);CREATE TABLE occurrences(raw TEXT);')
            connection.execute('INSERT INTO metadata VALUES(?,?)',('genesis',GENESIS))
            connection.execute('INSERT INTO blocks VALUES(?)',('old copied chain data'*100000,))
        old_size=self.path.stat().st_size
        cache=Explorer(self.path)
        try:
            self.assertLess(self.path.stat().st_size,old_size//10)
            cache.ingest(self.chain_block(100))
            cache.clear()
            self.assertEqual(cache.summary()['cached_blocks'],0)
            self.assertEqual(wallet.read_bytes(),b'preserved payment journal fixture')
            self.assertNotIn('occurrences',[row[0] for row in cache.con.execute("SELECT name FROM sqlite_master WHERE type='table'")])
        finally:cache.con.close()

    def test_background_refresh_never_backfills_from_genesis(self):
        parent=self
        class RPC:
            fetched=[]
            def head(self):return {'height':1000,'hash':f'{1000:064x}','synced':True}
            def request(self,path):
                height=int(path.split('/')[3])
                return {'header':{'hash':MAINNET_BLOCK_ONE if height==1 else f'{height:064x}'}}
            def block(self,height):self.fetched.append(height);return parent.chain_block(height)
        rpc=RPC();cache=Explorer(self.path)
        try:
            for _ in range(12):cache.sync(rpc,budget=10)
            self.assertEqual(sorted(rpc.fetched),list(range(971,1001)))
            self.assertEqual(cache.summary()['cached_blocks'],30)
            self.assertFalse(cache.con.execute("SELECT 1 FROM metadata WHERE key='backfill'").fetchone())
        finally:cache.con.close()

    def test_corrupt_or_oversized_compressed_payload_is_rejected(self):
        import zlib
        from wartnode.explorer import MAX_BLOCK_BYTES
        for payload in (b'corrupt',zlib.compress(b'x'*(MAX_BLOCK_BYTES+1)),zlib.compress(b'{}')+b'trailing'):
            with self.subTest(length=len(payload)),self.assertRaisesRegex(NodeError,'cache payload'):
                Explorer.decode(payload)
