"""Unmodified wallet primitives reused from the pinned desktop wallet."""
import sys
import sqlite3
import tempfile
from pathlib import Path
from .paths import legacy_path

sys.path.insert(0, str(legacy_path()))
import utils as wallet_utils
from db import DB
import wallet_backup
from .rpc import NodeRPC

# All signing context requests use the product's stricter mainnet/loopback client.
wallet_utils.NodeClient = NodeRPC


class WalletDB(DB):
    def __init__(self, path):
        self.path = Path(path)
        super().__init__(str(path))
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA synchronous=FULL")
        with self.con:
            self.con.execute("DELETE FROM node_profiles")
        self.network = "mainnet"
        from .settlement import initialize
        initialize(self)

    def public_wallets(self):
        return [{"id": r[0], "address": r[1], "balance": r[4]} for r in self.get_wallets()]

    def restore_snapshot(self, content):
        # Imported settlement claims have no locally verified chain anchor.
        # Invalidate the cached evidence in the same transaction as the merge.
        with self.con:
            self.con.execute('BEGIN IMMEDIATE')
            self.con.execute('DELETE FROM desktop_settlement_anchor')
            return super().restore_snapshot(content)

    def snapshot(self):
        # SQLite serialize() retains a WAL header that deserialize(:memory:)
        # cannot open. Produce a consistent, portable DELETE-journal backup.
        with tempfile.TemporaryDirectory(prefix='.wallet-backup-',dir=self.path.parent) as directory:
            path = Path(directory)/'snapshot.db'
            target = sqlite3.connect(path)
            try:
                self.con.backup(target)
                target.execute('PRAGMA journal_mode=DELETE').fetchone()
            finally:
                target.close()
            return path.read_bytes()
