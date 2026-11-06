"""Fresh-profile vault. Private keys are encrypted by a password-wrapped key.

There is deliberately no converter for the former per-address password format.
Only the private signer owns an unlocked profile key.
"""
import base64
import json
import os
from pathlib import Path
import sqlite3
import time
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from .compat import WalletDB, wallet_utils as crypto
from . import vanity_jobs

PROFILE_FORMAT = 2
READABLE_PROFILE_FORMATS = (1, PROFILE_FORMAT)
KEY_MARKER = 'wartnode-profile-v1'
KDF_ITERATIONS = 480000
MAX_LABEL = 80


def validate_password(password):
    if not isinstance(password, str) or not 8 <= len(password) <= 1024:
        raise ValueError('Use a profile password of 8 to 1024 characters')


def label_text(value):
    if not isinstance(value, str):
        raise ValueError('Enter a label of 1 to 80 characters')
    value = value.strip()
    if not 1 <= len(value) <= MAX_LABEL or any(ord(c) < 32 for c in value):
        raise ValueError('Enter a label of 1 to 80 characters')
    return value


def wrapping_cipher(password, salt):
    validate_password(password)
    if not isinstance(salt, bytes) or len(salt) != 16:
        raise ValueError('Invalid profile key record')
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                    iterations=KDF_ITERATIONS)
    return Fernet(base64.urlsafe_b64encode(kdf.derive(password.encode('utf-8'))))


def guard_profile(path):
    """Read before the inherited store can change schema, settings, or WAL state."""
    path = Path(path)
    if not path.exists():
        return
    if path.is_symlink() or path.is_junction() or not path.is_file():
        raise ValueError('Choose a regular, local profile database')
    con = None
    try:
        con = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)
        con.execute('PRAGMA trusted_schema=OFF')
        con.execute('PRAGMA query_only=ON')
        kinds = dict(con.execute('SELECT name,type FROM sqlite_master'))
        if kinds.get('desktop_profile') != 'table':
            raise ValueError('This folder uses the old wallet format. Choose a fresh profile and import your addresses manually. The original wallet has not been changed.')
        rows = con.execute('SELECT id,format,salt,wrapped_key,name FROM desktop_profile LIMIT 2').fetchall()
        if len(rows) > 1:
            raise ValueError('Invalid profile metadata')
        if rows:
            ident, version, salt, token, name = rows[0]
            if (ident != 1 or type(version) is not int or version not in READABLE_PROFILE_FORMATS
                    or not isinstance(salt, bytes) or len(salt) != 16
                    or not isinstance(token, bytes) or not 80 <= len(token) <= 512):
                raise ValueError('Unsupported or damaged profile format')
            label_text(name)
            job_table = con.execute("SELECT sql FROM sqlite_master WHERE name='desktop_vanity_jobs'").fetchone()
            if (version == 1 and job_table is not None) or (version == PROFILE_FORMAT and job_table != (vanity_jobs.TABLE_SQL,)):
                raise ValueError('Unsupported or damaged vanity profile schema')
        elif kinds.get('wallets') == 'table' and con.execute('SELECT 1 FROM wallets LIMIT 1').fetchone():
            raise ValueError('Profile key metadata is missing. Keep the database and restore a profile backup.')
    except sqlite3.Error as exc:
        raise ValueError('The profile database is damaged or unsupported. Keep the original file.') from exc
    finally:
        if con is not None:
            con.close()


class ProfileStore(WalletDB):
    def __init__(self, path):
        guard_profile(path)
        super().__init__(path)
        with self.con:
            self.con.execute('BEGIN IMMEDIATE')
            self.con.execute('CREATE TABLE IF NOT EXISTS desktop_profile('
                             'id INTEGER PRIMARY KEY CHECK(id=1),format INTEGER NOT NULL,'
                             'salt BLOB NOT NULL,wrapped_key BLOB NOT NULL,name TEXT NOT NULL)')
            self.con.execute('CREATE UNIQUE INDEX IF NOT EXISTS desktop_unique_address '
                             'ON wallets(address COLLATE NOCASE)')
            self.con.execute('CREATE TABLE IF NOT EXISTS desktop_address_meta('
                             'address TEXT PRIMARY KEY,label TEXT NOT NULL,'
                             'is_default INTEGER NOT NULL,created INTEGER NOT NULL)')
            self.con.execute('CREATE TABLE IF NOT EXISTS desktop_contacts('
                             'address TEXT PRIMARY KEY,label TEXT NOT NULL)')
            if not self.con.execute("SELECT 1 FROM sqlite_master WHERE name='desktop_vanity_jobs'").fetchone():
                self.con.execute(vanity_jobs.TABLE_SQL)
            self.con.execute('UPDATE desktop_profile SET format=? WHERE format=1', (PROFILE_FORMAT,))

    @property
    def configured(self):
        return self.con.execute('SELECT 1 FROM desktop_profile WHERE id=1').fetchone() is not None

    @property
    def name(self):
        row = self.con.execute('SELECT name FROM desktop_profile WHERE id=1').fetchone()
        return row[0] if row else 'Personal'

    def setup(self, password, name='Personal'):
        name = label_text(name)
        salt = os.urandom(16)
        cipher = wrapping_cipher(password, salt)
        key = Fernet.generate_key()
        wrapped = cipher.encrypt(key)
        with self.con:
            self.con.execute('BEGIN IMMEDIATE')
            if self.configured:
                raise ValueError('This profile already has a password')
            self.con.execute('INSERT INTO desktop_profile VALUES(1,?,?,?,?)',
                             (PROFILE_FORMAT, salt, wrapped, name))
        return key

    def unlock(self, password):
        row = self.con.execute('SELECT salt,wrapped_key FROM desktop_profile WHERE id=1').fetchone()
        if not row:
            raise ValueError('Set up the profile password first')
        try:
            key = wrapping_cipher(password, row[0]).decrypt(row[1])
            Fernet(key)
            return key
        except (InvalidToken, TypeError) as exc:
            raise ValueError('The profile password is incorrect or the key record is damaged') from exc

    def change_password(self, old_password, new_password):
        key = self.unlock(old_password)
        salt = os.urandom(16)
        token = wrapping_cipher(new_password, salt).encrypt(key)
        with self.con:
            # Serial helper operations cannot race a key rewrap.
            self.con.execute('UPDATE desktop_profile SET salt=?,wrapped_key=? WHERE id=1', (salt, token))
        return key

    def add_address(self, profile_key, private_key, label):
        with self.con:
            self.con.execute('BEGIN IMMEDIATE')
            return self._insert_address(profile_key, private_key, label)

    def get_wallet_by_address(self, address):
        # The inherited read helper commits its connection context. Reads here
        # must preserve the caller's wallet + finalization transaction.
        return self.con.execute('SELECT * FROM wallets WHERE address=? COLLATE NOCASE', (address,)).fetchone()

    def _insert_address(self, profile_key, private_key, label):
        """Caller owns the transaction, including any durable finalization record."""
        if not self.con.in_transaction:
            raise ValueError('Address insertion requires a profile transaction')
        label = label_text(label)
        address, key = crypto.wallet_from_pkhex(private_key)
        del key
        payload = json.dumps({'address': address, 'private_key': private_key}, separators=(',', ':')).encode()
        token = Fernet(profile_key).encrypt(payload).decode('ascii')
        if not self.configured:
            raise ValueError('Set up the profile password first')
        if self.con.execute('SELECT 1 FROM wallets WHERE address=? COLLATE NOCASE', (address,)).fetchone():
            raise ValueError('This address is already in the profile')
        first = self.con.execute('SELECT 1 FROM wallets LIMIT 1').fetchone() is None
        self.con.execute('INSERT INTO wallets(address,pk,salt) VALUES(?,?,?)',
                         (address, token, KEY_MARKER))
        self.con.execute('INSERT INTO desktop_address_meta VALUES(?,?,?,?)',
                         (address, label, int(first), int(time.time())))
        return address

    def private_key(self, profile_key, address):
        address = crypto.validate_address(address)
        row = self.get_wallet_by_address(address)
        if not row or row[3] != KEY_MARKER:
            raise ValueError('This address is not in the profile')
        try:
            value = json.loads(Fernet(profile_key).decrypt(row[2].encode('ascii')))
            owner, key = crypto.wallet_from_pkhex(value['private_key'])
            if value['address'] != address or owner != address:
                raise ValueError('The encrypted address does not match its key')
            return owner, key
        except (InvalidToken, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError('The encrypted address record is damaged') from exc

    def public_wallets(self):
        rows = [dict(id=row[0], address=row[1], balance=row[2], label=row[3],
                     default=bool(row[4]), created=row[5]) for row in self.con.execute(
                         'SELECT w.id,w.address,w.last_balance,m.label,m.is_default,m.created '
                         'FROM wallets w JOIN desktop_address_meta m ON m.address=w.address '
                         'ORDER BY m.is_default DESC,w.id')]
        if len(rows) != self.con.execute('SELECT COUNT(*) FROM wallets').fetchone()[0]:
            raise ValueError('Profile address metadata is damaged. Restore a profile backup.')
        return rows

    def edit_address(self, address, label=None, default=False):
        address = crypto.validate_address(address)
        if not self.get_wallet_by_address(address):
            raise ValueError('This address is not in the profile')
        value = label_text(label) if label is not None else None
        with self.con:
            if value is not None:
                self.con.execute('UPDATE desktop_address_meta SET label=? WHERE address=?', (value, address))
            if default:
                self.con.execute('UPDATE desktop_address_meta SET is_default=0')
                self.con.execute('UPDATE desktop_address_meta SET is_default=1 WHERE address=?', (address,))

    def contacts(self):
        return [dict(address=a, name=n) for a, n in self.con.execute(
            'SELECT address,label FROM desktop_contacts ORDER BY label COLLATE NOCASE,address')]

    def save_contact(self, name, address, previous=None):
        name, address = label_text(name), crypto.validate_address(address.strip())
        if previous is not None:
            previous = crypto.validate_address(previous)
        with self.con:
            if previous and previous != address:
                self.con.execute('DELETE FROM desktop_contacts WHERE address=?', (previous,))
            self.con.execute('INSERT INTO desktop_contacts VALUES(?,?) '
                             'ON CONFLICT(address) DO UPDATE SET label=excluded.label', (address, name))

    def delete_contact(self, address):
        address = crypto.validate_address(address)
        with self.con:
            self.con.execute('DELETE FROM desktop_contacts WHERE address=?', (address,))

    def restore_profile(self, snapshot, password):
        """Restore only into an empty profile, never execute a supplied schema."""
        if (self.con.execute('SELECT 1 FROM wallets LIMIT 1').fetchone()
                or self.con.execute('SELECT 1 FROM tx_history LIMIT 1').fetchone()
                or self.con.execute('SELECT 1 FROM archived_tx_history LIMIT 1').fetchone()
                or self.con.execute('SELECT 1 FROM desktop_contacts LIMIT 1').fetchone()
                or self.con.execute('SELECT 1 FROM desktop_vanity_jobs LIMIT 1').fetchone()):
            raise ValueError('Restore into a fresh empty profile so existing addresses and payments are kept')
        if not isinstance(snapshot, bytes) or len(snapshot) > 32*1024*1024:
            raise ValueError('Profile backup exceeds the size limit')
        source = sqlite3.connect(':memory:')
        try:
            source.deserialize(snapshot)
            source.execute('PRAGMA trusted_schema=OFF')
            source.execute('PRAGMA query_only=ON')
            schema = "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            expected = self.con.execute(schema).fetchall()
            observed = source.execute(schema).fetchall()
            legacy = [row for row in expected if row[1] != 'desktop_vanity_jobs']
            if observed not in (expected, legacy):
                raise ValueError('This is not a supported profile backup format')
            if source.execute('PRAGMA integrity_check').fetchone() != ('ok',):
                raise ValueError('The profile backup is damaged')
            metadata = source.execute('SELECT id,format,salt,wrapped_key,name FROM desktop_profile LIMIT 2').fetchall()
            if len(metadata) != 1:
                raise ValueError('Invalid profile backup metadata')
            ident, version, salt, wrapped, name = metadata[0]
            if (ident != 1 or type(version) is not int or version not in READABLE_PROFILE_FORMATS
                    or (version == 1) != (observed == legacy)):
                raise ValueError('Unsupported profile backup format')
            label_text(name)
            if not isinstance(wrapped, bytes) or not 80 <= len(wrapped) <= 512:
                raise ValueError('Invalid profile key record')
            key = wrapping_cipher(password, salt).decrypt(wrapped)
            cipher = Fernet(key)
            rows = source.execute('SELECT address,pk,salt,last_balance FROM wallets LIMIT 10001').fetchall()
            if len(rows) > 10000:
                raise ValueError('Profile backup contains too many addresses')
            owners = set()
            for address, token, marker, balance in rows:
                owner = crypto.validate_address(address)
                if owner in owners or owner != address or marker != KEY_MARKER or not isinstance(token, str) or len(token) > 2048:
                    raise ValueError('Invalid encrypted address record')
                value = json.loads(cipher.decrypt(token.encode('ascii')))
                derived, private = crypto.wallet_from_pkhex(value['private_key'])
                del private
                if derived != owner or value['address'] != owner:
                    raise ValueError('The backup address does not match its key')
                crypto.parse_e8(balance, allow_zero=True)
                owners.add(owner)
            meta = source.execute('SELECT address,label,is_default,created FROM desktop_address_meta LIMIT 10001').fetchall()
            if len(meta) != len(owners) or {r[0] for r in meta} != owners:
                raise ValueError('Address labels do not match the profile')
            for address, label, default, created in meta:
                label_text(label)
                if type(default) is not int or default not in (0, 1) or type(created) is not int or created < 0:
                    raise ValueError('Invalid address metadata')
            if owners and sum(r[2] for r in meta) != 1:
                raise ValueError('Invalid default address')
            contacts = source.execute('SELECT address,label FROM desktop_contacts LIMIT 10001').fetchall()
            if len(contacts) > 10000:
                raise ValueError('Profile backup contains too many contacts')
            for address, label in contacts:
                crypto.validate_address(address)
                label_text(label)
            from history import validate_saved_record
            for table in ('tx_history', 'archived_tx_history'):
                columns = [r[1] for r in source.execute('PRAGMA table_info('+table+')')]
                records = source.execute('SELECT * FROM '+table+' LIMIT 50001').fetchall()
                if len(records) > 50000:
                    raise ValueError('Profile backup contains too many payments')
                for record in records:
                    row = validate_saved_record(dict(zip(columns, record)))
                    if row['wallet_address'] not in owners or row['network'] != 'mainnet':
                        raise ValueError('Payment does not belong to this profile')
            if version == PROFILE_FORMAT:
                from .vanity_keys import VanityKeys
                job_rows = source.execute('SELECT '+','.join(vanity_jobs.COLUMNS)+' FROM desktop_vanity_jobs LIMIT 10001').fetchall()
                if len(job_rows) > vanity_jobs.MAX_JOBS:
                    raise ValueError('Profile backup contains too many vanity jobs')
                pending = 0
                for row in job_rows:
                    job = vanity_jobs.row_value(row)
                    VanityKeys.validate_private(job, key)
                    if job['state'] != 'saved':
                        pending += 1
                    elif job['address'] not in owners:
                        raise ValueError('Saved vanity address is missing from this backup')
                if pending > 1:
                    raise ValueError('Profile backup contains multiple pending vanity jobs')
            # Imported confirmations must be revalidated against the active node.
            source.execute('PRAGMA query_only=OFF')
            if version == 1:
                source.execute(vanity_jobs.TABLE_SQL)
                source.execute('UPDATE desktop_profile SET format=? WHERE id=1', (PROFILE_FORMAT,))
            source.execute("UPDATE desktop_vanity_jobs SET state='paused',run_id=NULL,error='' WHERE state='running'")
            source.execute('DELETE FROM desktop_settlement_anchor')
            source.commit()
            source.backup(self.con)
            self.con.execute('PRAGMA journal_mode=WAL')
            self.con.execute('PRAGMA synchronous=FULL')
            return len(owners)
        except (sqlite3.Error, InvalidToken, KeyError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError('The profile backup is damaged, unsupported, or has an incorrect password') from exc
        finally:
            source.close()
