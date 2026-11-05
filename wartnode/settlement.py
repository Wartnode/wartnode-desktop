"""Canonical settlement checks before funds can be advertised or spent.

One verified chain anchor covers already settled entries. A reorganization or
legacy/restored journal without that evidence reserves every signed confirmed
entry in one durable transaction, including entries outside the display page.
"""
import time
from .rpc import NodeError


def initialize(store):
    with store.con:
        store.con.execute('CREATE TABLE IF NOT EXISTS desktop_settlement_anchor('
                          'owner TEXT PRIMARY KEY,height INTEGER NOT NULL,hash TEXT NOT NULL)')
        store.con.execute('CREATE TABLE IF NOT EXISTS desktop_reconcile_checks('
                          'owner TEXT NOT NULL,tx_hash TEXT NOT NULL,checked INTEGER NOT NULL,'
                          'PRIMARY KEY(owner,tx_hash))')


def anchor(store, owner):
    return store.con.execute('SELECT height,hash FROM desktop_settlement_anchor WHERE owner=?',(owner,)).fetchone()


def refresh(store, client, owner, head, require_context):
    expected = (head['height'],head['hash'])
    # Serialize with both reservations and other signer processes. No signer
    # may settle an old observation after another has invalidated its anchor.
    with store.con:
        store.con.execute('BEGIN IMMEDIATE')
        previous = anchor(store,owner)
        valid = previous == expected
        if previous and not valid and 0 < previous[0] <= head['height']:
            observed = client.request(f'/chain/block/{previous[0]}/header').get('header',{})
            valid = observed.get('hash') == previous[1]
        require_context()
        if not valid:
            store.con.execute("UPDATE tx_history SET status='uncertain',confirmations=0 "
                "WHERE network='mainnet' AND wallet_address=? AND direction='outgoing' "
                "AND payload!='' AND status='confirmed'",(owner,))
        store.con.execute('INSERT OR REPLACE INTO desktop_settlement_anchor VALUES(?,?,?)',(owner,*expected))
    return expected


def require_head(client, expected):
    observed = client.head(require_synced=True)
    if (observed['height'],observed['hash']) != expected:
        raise NodeError('The blockchain changed. Refresh the balance and review the payment again.')


def confirm(store, client, owner, row, expected, require_context):
    require_head(client,expected)
    require_context()
    with store.con:
        store.con.execute('BEGIN IMMEDIATE')
        if anchor(store,owner) != expected:
            raise NodeError('The blockchain changed. Refresh payments again.')
        store._write_history(row)


def pending_rows(store, owner, limit=100):
    columns = [r[1] for r in store.con.execute('PRAGMA table_info(tx_history)')]
    rows = store.con.execute('SELECT h.* FROM tx_history h LEFT JOIN desktop_reconcile_checks c '
        "ON c.owner=h.wallet_address AND c.tx_hash=h.tx_hash WHERE h.network='mainnet' "
        "AND h.wallet_address=? AND h.payload!='' AND h.status NOT IN ('confirmed','rejected','expired') "
        'ORDER BY COALESCE(c.checked,0),h.timestamp DESC,h.tx_hash LIMIT ?',(owner,limit)).fetchall()
    return [dict(zip(columns,row)) for row in rows]


def checked(store, owner, tx_hash):
    with store.con:
        store.con.execute('INSERT OR REPLACE INTO desktop_reconcile_checks VALUES(?,?,?)',(owner,tx_hash,time.time_ns()))
