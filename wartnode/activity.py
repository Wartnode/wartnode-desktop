"""Durable public address activity, discovered by bounded canonical history pages.

This is a projection, not a signing journal or a second blockchain index. No
SQLite transaction spans a network request. Reorgs invalidate coverage before
any total can be presented as complete. Explorer cache maintenance is unrelated.
"""
import json
from pathlib import Path
import sqlite3
import time
from .node_history import transaction_row, verify_anchor
from .rpc import HEX64, NodeError


def fingerprint(row):
    raw=json.loads(row['raw'])
    extra=[]
    if row['kind']=='transfer':
        for key in ('pinHeight','nonceId'):
            value=raw.get(key)
            if type(value) is not int or value<0:raise NodeError('Invalid transfer identity in address history')
            extra.append(value)
    return json.dumps([row[k] for k in ('kind','hash','sender','recipient','amount','fee')]+extra,separators=(',',':'))


class ActivityStore:
    def __init__(self,path):
        self.con=sqlite3.connect(Path(path),timeout=2)
        self.con.row_factory=sqlite3.Row
        self.con.execute('PRAGMA journal_mode=WAL')
        self.con.execute('PRAGMA synchronous=FULL')
        self.con.execute('PRAGMA max_page_count=16384')  # 64 MiB with the default 4 KiB pages
        with self.con:
            self.con.execute('CREATE TABLE IF NOT EXISTS owners(address TEXT PRIMARY KEY,source TEXT NOT NULL,'
                'height INTEGER NOT NULL,hash TEXT NOT NULL,cursor INTEGER NOT NULL,complete INTEGER NOT NULL,turn INTEGER NOT NULL,checked INTEGER NOT NULL)')
            self.con.execute('CREATE TABLE IF NOT EXISTS activity(owner TEXT NOT NULL,height INTEGER NOT NULL,'
                'block_hash TEXT NOT NULL,position INTEGER NOT NULL,tx_hash TEXT NOT NULL,kind TEXT NOT NULL,'
                'sender TEXT NOT NULL,recipient TEXT NOT NULL,amount TEXT NOT NULL,fee TEXT NOT NULL,'
                'fingerprint TEXT NOT NULL,timestamp INTEGER NOT NULL,status TEXT NOT NULL,checked INTEGER NOT NULL,'
                'PRIMARY KEY(owner,block_hash,position))')
            self.con.execute('CREATE INDEX IF NOT EXISTS activity_owner_height ON activity(owner,height DESC)')

    def close(self):self.con.close()

    def sync(self,history,owner,budget=8):
        from .compat import wallet_utils as crypto
        owner=crypto.validate_address(owner)
        if type(budget) is not int or not 1<=budget<=25:raise ValueError('Invalid history work limit')
        current=history.anchor();client=history.client
        source='hosted node' if getattr(client,'remote',False) else 'local node history'
        saved=self.con.execute('SELECT * FROM owners WHERE address=?',(owner,)).fetchone()
        reset=not saved or saved['source']!=source or saved['height']>current['height']
        if saved and not reset:
            header=client.request(f"/chain/block/{saved['height']}/header").get('header',{}) if saved['height'] else {}
            reset=header.get('hash')!=saved['hash'] if saved['height'] else False
        if reset:
            # Save the invalidation independently. A failed later read must not
            # leave old confirmed totals looking current after a detected fork.
            with self.con:
                self.con.execute("UPDATE activity SET status='unverified' WHERE owner=? AND status='confirmed'",(owner,))
                self.con.execute('INSERT OR REPLACE INTO owners VALUES(?,?,?,?,0,0,0,0)',(owner,source,current['height'],current['hash']))
            saved=self.con.execute('SELECT * FROM owners WHERE address=?',(owner,)).fetchone()
        # Recheck historical occurrences in small rotating batches. This also
        # recovers records discovered before an interrupted projection write.
        changes=[]
        for row in self.con.execute("SELECT * FROM activity WHERE owner=? ORDER BY (status='unverified') DESC,checked LIMIT 8",(owner,)).fetchall():
            canonical=(client.request(f"/chain/block/{row['height']}/header").get('header',{}).get('hash')
                       if row['height']<=current['height'] else None)
            changes.append(('confirmed' if canonical==row['block_hash'] else 'orphaned',time.time_ns(),owner,row['block_hash'],row['position']))
        before=saved['cursor'] if saved['cursor'] and saved['turn']%2 else 0
        page=history.address_page(owner,before,anchor=current)
        rows=page['occurrences']
        if len(rows)>100:raise NodeError('Address activity exceeds its page bound')
        known={}
        for row in self.con.execute("SELECT height,fingerprint FROM activity WHERE owner=? AND status='confirmed'",(owner,)):
            known[(row['height'],row['fingerprint'])]=True
        bridge=any((row['height'],fingerprint(row)) in known for row in rows)
        fetched={};records=[];finished=True
        for candidate in rows:
            identity=fingerprint(candidate)
            if (candidate['height'],identity) in known:continue
            height=candidate['height']
            if not 0<height<=current['height']:raise NodeError('Address activity is ahead of its chain anchor')
            if height not in fetched:
                if len(fetched)>=budget:finished=False;continue
                block=client.block(height)
                header,body=block.get('header',{}),block.get('body',{})
                if (block.get('height')!=height or not HEX64.fullmatch(str(header.get('hash','')))
                        or type(header.get('timestamp')) is not int or not 0<=header['timestamp']<=0xffffffff
                        or any(not isinstance(body.get(key),list) or len(body[key])>10000 for key in ('rewards','transfers'))):
                    raise NodeError('Invalid canonical activity block')
                verify_anchor(client,{'height':height,'hash':header['hash']})
                entries=[]
                for pos,tx in enumerate(body['rewards']+body['transfers']):
                    entry=transaction_row(tx,height,pos,header['hash'])
                    if owner in (entry['sender'],entry['recipient']):entries.append(entry)
                fetched[height]=(block,entries)
            block,entries=fetched[height]
            matches=[entry for entry in entries if fingerprint(entry)==identity]
            if not matches:raise NodeError('Address activity does not match the canonical block')
            # Store all address occurrences from this verified block, including
            # distinct positions sharing the same transaction hash.
            for entry in entries:
                records.append((owner,height,block['header']['hash'],entry['position'],entry['hash'],entry['kind'],
                    entry['sender'],entry['recipient'],entry['amount'],entry['fee'],fingerprint(entry),
                    block['header']['timestamp'],'confirmed',time.time_ns()))
                known[(height,fingerprint(entry))]=True
        verify_anchor(client,current)
        cursor=saved['cursor'];complete=bool(saved['complete'])
        if finished:
            next_before=page.get('next_before',0)
            if before:
                cursor=next_before
                complete=not cursor and page.get('complete') is True
            elif not next_before:
                cursor=0;complete=page.get('complete') is True
            elif not bridge:
                # A burst larger than one page can leave a gap. Start from this
                # page's boundary and backfill again; do not claim all-time scope.
                cursor=next_before;complete=False
            elif not complete and not cursor:cursor=next_before
        else:complete=False
        with self.con:
            self.con.executemany('UPDATE activity SET status=?,checked=? WHERE owner=? AND block_hash=? AND position=?',changes)
            self.con.executemany('INSERT OR REPLACE INTO activity VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',records)
            self.con.execute('INSERT OR REPLACE INTO owners VALUES(?,?,?,?,?,?,?,?)',
                (owner,source,current['height'],current['hash'],cursor,int(complete),saved['turn']+1,int(time.time())))
            # Transfer activity is a recent presentation list. Reward records
            # remain durable; a full database reports a refresh error honestly.
            self.con.execute("DELETE FROM activity WHERE rowid IN (SELECT rowid FROM activity WHERE kind='transfer' ORDER BY height DESC LIMIT -1 OFFSET 5000)")
        return self.snapshot([owner])

    def snapshot(self,owners,payout=None):
        owners=list(dict.fromkeys(owners))
        selected=list(dict.fromkeys(owners+([payout] if payout else [])))
        states={r['address']:dict(r) for r in self.con.execute('SELECT * FROM owners') if r['address'] in selected}
        rows=[];rewards=[];reward_total=0;reward_count=0;unverified=False
        for owner in selected:
            entries=self.con.execute('SELECT * FROM activity WHERE owner=? ORDER BY height DESC,position LIMIT 100',(owner,)).fetchall()
            if owner in owners:rows.extend(dict(r) for r in entries)
            if owner==payout:
                for record in self.con.execute("SELECT * FROM activity WHERE owner=? AND kind='reward' ORDER BY height DESC,position",(owner,)):
                    if record['status']=='confirmed':reward_total+=int(record['amount']);reward_count+=1
                    if record['status']=='unverified':unverified=True
                    if len(rewards)<50:rewards.append(dict(record))
        unique={}
        for row in sorted(rows,key=lambda r:(-r['height'],r['position'])):
            unique.setdefault((row['block_hash'],row['position']),row)
        return {'rows':list(unique.values())[:100],'states':states,'rewards':rewards,'reward_count':reward_count,
                'reward_total':reward_total,'reward_complete':bool(states.get(payout,{}).get('complete')) and not unverified,
                'payout':payout}
