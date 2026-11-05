"""On-demand history from the managed official node; no secondary chain index."""
import json
from pathlib import Path
import sqlite3
from .rpc import GENESIS, HEX64, NodeError

MAINNET_BLOCK_ONE = 'c20f1a38fb3e5032aea7d5f6f452063bab55edcb49762ec88c5a00a600000000'
PAGE_SIZE = 50

def verify_anchor(client, anchor):
    height, block_hash = anchor['height'], anchor['hash']
    if type(height) is not int or height < 0 or not HEX64.fullmatch(str(block_hash)):
        raise NodeError('Invalid history page context')
    if height == 0:
        if block_hash != GENESIS:raise NodeError('Unexpected mainnet genesis')
        return
    header = client.request(f'/chain/block/{height}/header').get('header',{})
    if header.get('hash','').lower() != block_hash.lower():
        raise NodeError('The chain changed. Search again from the newest page.')

def transaction_row(transaction, height=None, position=0, block_hash=None):
    from .compat import wallet_utils as crypto
    tx_hash = transaction.get('txHash','')
    if not HEX64.fullmatch(str(tx_hash)):raise NodeError('Invalid transaction hash')
    height = transaction.get('blockHeight') if height is None else height
    if type(height) is not int or height < 1:raise NodeError('Invalid confirmed transaction height')
    kind = transaction.get('type','Transfer' if 'fromAddress' in transaction else 'Reward').lower()
    if kind not in ('transfer','reward'):raise NodeError('Unsupported transaction kind')
    recipient = crypto.validate_address(transaction.get('toAddress'))
    sender = crypto.validate_address(transaction.get('fromAddress')) if kind == 'transfer' else ''
    amount = crypto.parse_e8(transaction.get('amount'),allow_zero=True)
    fee = crypto.parse_e8(transaction.get('fee','0'),allow_zero=True)
    raw=json.dumps(transaction)
    if len(raw.encode())>8192:raise NodeError('Transaction data exceeds the size limit')
    return {'height':height,'position':position,'hash':tx_hash.lower(),'sender':sender,'recipient':recipient,
        'amount':str(amount),'fee':str(fee),'kind':kind,'raw':raw,'block_hash':block_hash}

def matches_payment(transaction, owner, record):
    try:
        row = transaction_row(transaction,height=transaction.get('blockHeight',1))
        expected = json.loads(record['payload'])
        return (row['kind']=='transfer' and row['hash']==record['tx_hash'] and row['sender']==owner
            and row['recipient']==record['counterparty'] and row['amount']==str(record['amount_e8'])
            and row['fee']==str(record['fee_e8']) and type(transaction.get('nonceId')) is int
            and type(transaction.get('pinHeight')) is int and transaction['nonceId']==expected.get('nonceId')
            and transaction['pinHeight']==expected.get('pinHeight'))
    except (ValueError,TypeError,KeyError):return False

class NodeHistory:
    def __init__(self, client, chain_path):
        self.client, self.chain_path = client, Path(chain_path)

    def anchor(self):
        head = self.client.head()
        if head['height']:
            first = self.client.request('/chain/block/1/header').get('header',{})
            if first.get('hash') != MAINNET_BLOCK_ONE:raise NodeError('Unexpected mainnet chain anchor')
        return {'height':head['height'],'hash':head['hash']}

    def block_height(self, block_hash):
        if not HEX64.fullmatch(str(block_hash)):raise NodeError('Invalid block hash')
        try:
            connection=sqlite3.connect(self.chain_path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.5)
            try:
                connection.execute('PRAGMA query_only=ON')
                row=connection.execute('SELECT height FROM Blocks WHERE hash=? LIMIT 1',(bytes.fromhex(block_hash),)).fetchone()
                return int(row[0]) if row else None
            finally:connection.close()
        except sqlite3.Error as error:raise NodeError('The node database is busy or unavailable. Try the search again.') from error

    def hash_page(self, tx_hash, before=0, anchor=None):
        """Read existing hash-index IDs briefly; close SQLite before verifying RPC.

        The official node owns the index. This connection never writes, creates
        an index or holds a read transaction across an RPC call. Canonical head
        anchors protect history-ID pagination against reorganization and reuse.
        """
        if not HEX64.fullmatch(str(tx_hash)) or type(before) is not int or not 0 <= before < 2**63:
            raise NodeError('Invalid transaction page request')
        current = self.anchor()
        if anchor:verify_anchor(self.client,anchor)
        try:
            connection = sqlite3.connect(self.chain_path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.5)
            try:
                connection.execute('PRAGMA query_only=ON')
                connection.execute('BEGIN')
                columns = {row[1].lower() for row in connection.execute('PRAGMA table_info(History)')}
                index = [str(row[2]).lower() for row in connection.execute('PRAGMA index_info(history_index)')]
                if not {'id','hash','data'} <= columns or index != ['hash']:
                    raise NodeError('This node history format is not supported by WartNode')
                head = connection.execute('SELECT c.height,b.hash FROM Consensus c JOIN Blocks b ON b.rowid=c.block_id '
                    'WHERE c.height>0 ORDER BY c.height DESC LIMIT 1').fetchone()
                snapshot = {'height':int(head[0]),'hash':bytes(head[1]).hex()} if head else current
                ids = [int(row[0]) for row in connection.execute(
                    'SELECT id FROM History INDEXED BY history_index WHERE hash=? AND id<? ORDER BY id DESC LIMIT ?',
                    (bytes.fromhex(tx_hash),before or 2**63-1,PAGE_SIZE+1))]
                if head is None and current['height']>0:
                    raise NodeError("The node's current block could not be found")
            finally:connection.close()
        except sqlite3.Error as error:
            raise NodeError('Node history is busy or unavailable. Try the search again.') from error
        verify_anchor(self.client,snapshot)
        page_anchor = anchor or snapshot
        rows=[]
        for history_id in ids[:PAGE_SIZE]:
            tx = self.client.request(f'/transaction/lookup/{history_id}').get('transaction')
            if not isinstance(tx,dict) or tx.get('txHash','').lower()!=tx_hash.lower():
                raise NodeError('History changed during lookup. Search again from the newest page.')
            row = transaction_row(tx,position=history_id)
            row['history_id'] = history_id
            rows.append(row)
        verify_anchor(self.client,page_anchor)
        verify_anchor(self.client,snapshot)
        return {'type':'transactions','occurrences':rows,'next_before':ids[PAGE_SIZE-1] if len(ids)>PAGE_SIZE else 0,
            'anchor':page_anchor,'source':'local node history','complete':len(ids)<=PAGE_SIZE}

    def address_page(self, address, before=0, anchor=None):
        from .compat import wallet_utils as crypto
        address = crypto.validate_address(address)
        if type(before) is not int or not 0 <= before < 2**63:raise NodeError('Invalid address page cursor')
        current = self.anchor()
        if anchor:verify_anchor(self.client,anchor)
        # Upstream treats zero literally (h.id < 0), not as a newest-page sentinel.
        try:result = self.client.request(f'/account/{address}/history/{before or 2**63-1}')
        except NodeError as error:
            if error.code != 57:raise
            verify_anchor(self.client,anchor or current)
            return {'type':'address','address':address,'balance':'0.00000000','occurrences':[],
                'next_before':0,'anchor':anchor or current,'source':'local node history','complete':True}
        if not isinstance(result.get('perBlock'),list):raise NodeError('Unsupported account history response')
        rows=[]
        for group in result['perBlock']:
            transactions = group.get('transactions',{})
            for field in ('rewards','transfers'):
                values=transactions.get(field)
                if not isinstance(values,list):raise NodeError('Invalid account history entries')
                for tx in values:
                    row=transaction_row(tx,height=group.get('height'),position=len(rows))
                    if row['sender']!=address and row['recipient']!=address:
                        raise NodeError('Node returned history for a different address')
                    rows.append(row)
        if len(rows)>100:raise NodeError('Account history exceeds the page size limit')
        cursor=result.get('fromId')
        if type(cursor) is not int or cursor < 0 or (before and cursor>=before and rows):
            raise NodeError('Invalid account history cursor')
        page_anchor=anchor or current
        verify_anchor(self.client,page_anchor)
        verify_anchor(self.client,current)
        crypto.parse_e8(result.get('balance'),allow_zero=True)
        return {'type':'address','address':address,'balance':result['balance'],'occurrences':rows,
            'next_before':cursor if len(rows)==100 else 0,'anchor':page_anchor,
            'source':'local node history','complete':len(rows)<100}
