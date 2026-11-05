"""Bounded, disposable block cache over the managed node's authoritative history."""
import json
from pathlib import Path
import sqlite3
import time
import zlib
from .rpc import GENESIS, HEX64, NodeError
from .node_history import MAINNET_BLOCK_ONE, NodeHistory, transaction_row, matches_payment, verify_anchor

MAX_BLOCKS = 128
MAX_PAYLOAD_BYTES = 16*1024*1024
MAX_DATABASE_BYTES = 32*1024*1024
MAX_BLOCK_BYTES = 4*1024*1024

class Explorer:
    def __init__(self, path, max_blocks=MAX_BLOCKS, max_bytes=MAX_PAYLOAD_BYTES):
        self.path=Path(path)
        self.max_blocks,self.max_bytes=max_blocks,max_bytes
        if max_blocks<1 or max_bytes<1:raise ValueError('Invalid explorer cache limits')
        self.con=sqlite3.connect(path,timeout=5)
        self.con.row_factory=sqlite3.Row
        self.con.execute('PRAGMA journal_mode=DELETE')
        self.con.execute('PRAGMA auto_vacuum=FULL')
        self.con.execute('PRAGMA cache_size=-2048')
        try:
            self.con.execute('BEGIN IMMEDIATE')
            self.con.execute('CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            identity=self.con.execute("SELECT value FROM metadata WHERE key='genesis'").fetchone()
            if identity and identity[0]!=GENESIS:raise NodeError('Explorer cache belongs to an unexpected chain')
            if self.con.execute('PRAGMA user_version').fetchone()[0]<2:
                self.con.execute('DROP TABLE IF EXISTS occurrences')
                self.con.execute('DROP TABLE IF EXISTS blocks')
                self.con.execute('DELETE FROM metadata')
                self.con.execute("INSERT INTO metadata VALUES('compact','1')")
                self.con.execute('PRAGMA user_version=2')
            self.con.execute('CREATE TABLE IF NOT EXISTS blocks(height INTEGER PRIMARY KEY,hash TEXT UNIQUE NOT NULL,'
                'parent TEXT NOT NULL,timestamp INTEGER NOT NULL,payload BLOB NOT NULL,touched INTEGER NOT NULL)')
            self.con.execute("INSERT OR IGNORE INTO metadata VALUES('genesis',?)",(GENESIS,))
            self.con.commit()
            if self.con.execute("SELECT 1 FROM metadata WHERE key='compact'").fetchone():
                try:
                    self.con.execute('VACUUM')
                    with self.con:self.con.execute("DELETE FROM metadata WHERE key='compact'")
                except sqlite3.OperationalError:pass
            page_size=self.con.execute('PRAGMA page_size').fetchone()[0]
            self.con.execute(f'PRAGMA max_page_count={MAX_DATABASE_BYTES//page_size}')
            self.prune()
        except Exception:
            self.con.rollback();self.con.close();raise

    def prune(self):
        with self.con:
            while True:
                row=self.con.execute('SELECT COUNT(*),COALESCE(SUM(length(payload)),0) FROM blocks').fetchone()
                if row[0]<=self.max_blocks and row[1]<=self.max_bytes:break
                self.con.execute('DELETE FROM blocks WHERE height=(SELECT height FROM blocks ORDER BY touched,height LIMIT 1)')

    def ingest(self, block):
        height,header,body=block.get('height'),block.get('header'),block.get('body')
        if (type(height) is not int or height<1 or not isinstance(header,dict) or not isinstance(body,dict)
                or not HEX64.fullmatch(str(header.get('hash',''))) or not HEX64.fullmatch(str(header.get('prevHash','')))
                or type(header.get('timestamp')) is not int):raise NodeError('Invalid block response')
        for field in ('rewards','transfers'):
            if not isinstance(body.get(field),list) or len(body[field])>10000:raise NodeError('Invalid block transactions')
            for position,tx in enumerate(body[field]):transaction_row(tx,height,position)
        raw=json.dumps(block,separators=(',',':')).encode()
        if len(raw)>MAX_BLOCK_BYTES:raise NodeError('Block response exceeds cache memory bound')
        payload=zlib.compress(raw,level=1)
        if len(payload)>self.max_bytes:return False
        with self.con:
            parent=self.con.execute('SELECT hash FROM blocks WHERE height=?',(height-1,)).fetchone()
            old=self.con.execute('SELECT hash FROM blocks WHERE height=?',(height,)).fetchone()
            child=self.con.execute('SELECT parent FROM blocks WHERE height=?',(height+1,)).fetchone()
            if parent and parent[0]!=header['prevHash'].lower():self.con.execute('DELETE FROM blocks WHERE height>=?',(height-1,))
            if old and old[0]!=header['hash'].lower():self.con.execute('DELETE FROM blocks WHERE height>=?',(height,))
            if child and child[0]!=header['hash'].lower():self.con.execute('DELETE FROM blocks WHERE height>?',(height,))
            self.con.execute('INSERT OR REPLACE INTO blocks VALUES(?,?,?,?,?,?)',
                (height,header['hash'].lower(),header['prevHash'].lower(),header['timestamp'],payload,time.time_ns()))
        self.prune()
        return True

    def clear(self):
        with self.con:
            self.con.execute('DELETE FROM blocks')
            self.con.execute("DELETE FROM metadata WHERE key!='genesis'")
        self.con.execute('VACUUM')

    def sync(self, client, budget=10):
        head=client.head()
        if not head['height']:return self.summary()
        if client.request('/chain/block/1/header').get('header',{}).get('hash')!=MAINNET_BLOCK_ONE:
            raise NodeError('Explorer refused a non-mainnet chain anchor')
        saved=dict(self.con.execute("SELECT key,value FROM metadata WHERE key IN ('head','head_hash')").fetchall())
        if saved:
            old_height=int(saved['head'])
            if old_height>head['height'] or client.request(f'/chain/block/{old_height}/header').get('header',{}).get('hash')!=saved['head_hash']:
                with self.con:self.con.execute('DELETE FROM blocks')
        fetched=0
        for height in range(head['height'],max(0,head['height']-30),-1):
            if self.con.execute('SELECT 1 FROM blocks WHERE height=?',(height,)).fetchone():continue
            self.ingest(client.block(height));fetched+=1
            if fetched>=budget:break
        verify_anchor(client,{'height':head['height'],'hash':head['hash']})
        with self.con:
            for key,value in (('head',head['height']),('head_hash',head['hash']),('checked',int(time.time()))):
                self.con.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',(key,str(value)))
        return self.summary()

    def summary(self):
        row=self.con.execute('SELECT COUNT(*),MIN(height),MAX(height),COALESCE(SUM(length(payload)),0) FROM blocks').fetchone()
        return {'cached_blocks':row[0],'from':row[1],'to':row[2],'payload_bytes':row[3],
            'max_blocks':self.max_blocks,'max_payload_bytes':self.max_bytes,'database_limit_bytes':MAX_DATABASE_BYTES,
            'database_bytes':self.path.stat().st_size if self.path.exists() else 0,
            'maintenance_pending':bool(self.con.execute("SELECT 1 FROM metadata WHERE key='compact'").fetchone())}

    def recent(self, limit=30):
        return [dict(row) for row in self.con.execute('SELECT height,hash,timestamp FROM blocks ORDER BY height DESC LIMIT ?',(min(100,limit),))]

    def _block(self, identifier):
        row=self.con.execute('SELECT height,payload FROM blocks WHERE '+('height=?' if type(identifier) is int else 'hash=?'),(identifier,)).fetchone()
        if not row:return None
        with self.con:self.con.execute('UPDATE blocks SET touched=? WHERE height=?',(time.time_ns(),row['height']))
        return self.decode(row['payload'])

    @staticmethod
    def decode(payload):
        try:
            decoder=zlib.decompressobj()
            raw=decoder.decompress(payload,MAX_BLOCK_BYTES+1)
            if len(raw)>MAX_BLOCK_BYTES or not decoder.eof or decoder.unused_data:
                raise NodeError('Invalid explorer cache payload; clear the cache')
            return json.loads(raw)
        except (zlib.error,ValueError) as error:raise NodeError('Invalid explorer cache payload; clear the cache') from error

    def search(self, value):
        value=value.strip().lower()
        if value.isdecimal() and len(value)<=10:return {'type':'block','block':self._block(int(value)),'source':'cache'}
        if HEX64.fullmatch(value):
            block=self._block(value)
            if block:return {'type':'block','block':block,'source':'cache'}
            kind='transactions';address=None
        else:
            from .compat import wallet_utils as crypto
            address=crypto.validate_address(value);kind='address'
        rows=[]
        for cached in self.con.execute('SELECT payload FROM blocks ORDER BY height DESC'):
            block=self.decode(cached['payload'])
            position=0
            for field in ('rewards','transfers'):
                for tx in block['body'][field]:
                    row=transaction_row(tx,block['height'],position,block['header']['hash']);position+=1
                    if (row['hash']==value if address is None else address in (row['sender'],row['recipient'])):
                        rows.append(row)
                        if len(rows)>=200:
                            return {'type':kind,'address':address,'occurrences':rows,'source':'cache only','complete':False,'next_before':0}
        return {'type':kind,'address':address,'occurrences':rows[:200],'source':'cache only','complete':False,'next_before':0}

    def lookup(self, client, value, chain_path, before=0, anchor=None):
        value=value.strip().lower()
        from .connection import history as selected_history
        history=selected_history(client,chain_path)
        if value.isdecimal() and len(value)<=10:block=client.block(int(value))
        elif HEX64.fullmatch(value):
            height=history.block_height(value)
            if height is None:return history.hash_page(value,before,anchor)
            canonical=client.request(f'/chain/block/{height}/header').get('header',{}).get('hash')==value
            if not canonical:
                return {'type':'block','block':None,'orphaned':True,'height':height,'hash':value,'source':'hosted node' if getattr(client,'remote',False) else 'local node'}
            block=client.block(height)
            if block['header']['hash']!=value:raise NodeError('Chain changed during block lookup; retry')
        else:return history.address_page(value,before,anchor)
        current=history.anchor()
        header=client.request(f"/chain/block/{block['height']}/header").get('header',{})
        canonical=header.get('hash')==block['header']['hash']
        verify_anchor(client,current)
        if canonical:self.ingest(block)
        return {'type':'block','block':block,'canonical':canonical,'confirmations':max(0,current['height']-block['height']+1) if canonical else 0,'source':'hosted node' if getattr(client,'remote',False) else 'local node'}

    def transaction_matches(self, owner, record):
        for row in self.search(record['tx_hash']).get('occurrences',[]):
            if matches_payment(json.loads(row['raw']),owner,record):return row
        return None
