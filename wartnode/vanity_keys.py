"""Signer-only durable vanity custody and transactional wallet finalization."""
import json
import time
import uuid
from coincurve import PrivateKey
from cryptography.fernet import Fernet, InvalidToken
from .compat import wallet_utils as crypto
from .profile import label_text
from . import vanity_jobs as jobs
from .vanity_jobs import ORDER, LAMBDA, TRANSFORMS
from .vanity import pattern


class VanityKeys:
    def __init__(self, store):
        self.store = store
        # A new signer never assumes an old process is still hashing.
        with store.con:
            store.con.execute("UPDATE desktop_vanity_jobs SET state='paused',run_id=NULL WHERE state='running'")

    def _write(self, value, insert=False):
        value['updated'] = max(value['updated'], int(time.time()))
        data = dict(value)
        for key in ('job','checkpoint','candidate'):
            data[key] = jobs.encode(data[key]) if data[key] is not None else None
        if insert:
            self.store.con.execute('INSERT INTO desktop_vanity_jobs('+','.join(jobs.COLUMNS)+') VALUES('
                +','.join('?' for _ in jobs.COLUMNS)+')', tuple(data[k] for k in jobs.COLUMNS))
        else:
            self.store.con.execute('UPDATE desktop_vanity_jobs SET '+','.join(k+'=?' for k in jobs.COLUMNS[1:])
                +' WHERE job_id=?', tuple(data[k] for k in jobs.COLUMNS[1:])+(data['job_id'],))

    def info(self, job_id=None):
        if job_id is not None:
            return jobs.public(jobs.read(self.store.con, job_id))
        rows = self.store.con.execute('SELECT '+','.join(jobs.COLUMNS)
            +" FROM desktop_vanity_jobs WHERE state!='saved' ORDER BY created LIMIT 2").fetchall()
        if len(rows) > 1:
            raise ValueError('The profile contains multiple pending vanity jobs')
        return jobs.public(jobs.row_value(rows[0])) if rows else None

    @staticmethod
    def validate_private(value, profile_key):
        """Authenticate the public job against the seed or finalization receipt."""
        try:
            cipher = Fernet(profile_key)
            if value['state'] == 'saved':
                receipt = json.loads(cipher.decrypt(value['reservation'].encode('ascii')))
                expected = dict(schema=1, job=value['job'], candidate=value['candidate'], address=value['address'])
                if receipt != expected:
                    raise ValueError()
                return None
            clear = json.loads(cipher.decrypt(value['seed'].encode('ascii')))
            if (not isinstance(clear, dict) or set(clear) != {'schema','job','secret'}
                    or clear['schema'] != 1 or clear['job'] != value['job']
                    or not isinstance(clear['secret'], str) or len(clear['secret']) != 64
                    or any(c not in '0123456789abcdef' for c in clear['secret'])):
                raise ValueError()
            secret = PrivateKey(bytes.fromhex(clear['secret']))
            if secret.public_key.format(compressed=True).hex() != value['job']['public_key']:
                raise ValueError()
            return secret
        except (InvalidToken, ValueError, TypeError, KeyError, UnicodeError) as error:
            raise ValueError('The vanity job belongs to a different or damaged profile') from error

    def prepare(self, profile_key, prefix, suffix, label='Vanity address', backend='auto'):
        value = pattern(prefix, suffix)
        label = label_text(label)
        if backend not in ('auto','cpu','opencl'):
            raise ValueError('Choose Automatic, CPU or GPU')
        secret = PrivateKey()
        job = dict(schema=3, session_id=str(uuid.uuid4()),
                   public_key=secret.public_key.format(compressed=True).hex(),
                   pattern=dict(profile=dict(chain='warthog', network='mainnet', address_type='account'),
                                prefix=value.prefix, suffix=value.suffix), strategy='endomorphism-v1')
        sealed = Fernet(profile_key).encrypt(jobs.encode(dict(schema=1, secret=secret.secret.hex(), job=job)).encode()).decode('ascii')
        del secret
        now = int(time.time())
        row = dict(job_id=job['session_id'], state='running', job=job, seed=sealed,
                   checkpoint=jobs.initial_checkpoint(), candidate=None, reservation=None, address=None,
                   label=label, backend=backend, run_id=str(uuid.uuid4()), error='', created=now, updated=now)
        with self.store.con:
            self.store.con.execute('BEGIN IMMEDIATE')
            if self.info() is not None:
                raise ValueError('Finish or discard the pending vanity search first')
            if self.store.con.execute('SELECT COUNT(*) FROM desktop_vanity_jobs').fetchone()[0] >= jobs.MAX_JOBS:
                raise ValueError('The profile contains too many vanity jobs')
            self._write(row, insert=True)
        return jobs.public(row)

    def resume(self, profile_key, job_id, backend='auto'):
        if backend not in ('auto','cpu','opencl'):
            raise ValueError('Choose Automatic, CPU or GPU')
        with self.store.con:
            self.store.con.execute('BEGIN IMMEDIATE')
            row = jobs.read(self.store.con, job_id)
            if row['state'] not in ('paused','failed'):
                raise ValueError('Only a paused or failed vanity search can resume')
            key = self.validate_private(row, profile_key)
            del key
            row.update(state='running', run_id=str(uuid.uuid4()), backend=backend, error='')
            self._write(row)
        return jobs.public(row)

    def checkpoint(self, job_id, run_id, checkpoint):
        with self.store.con:
            self.store.con.execute('BEGIN IMMEDIATE')
            row = jobs.read(self.store.con, job_id)
            if row['state'] != 'running' or row['run_id'] != run_id:
                return dict(accepted=False, record=jobs.public(row))
            row['checkpoint'] = jobs.validate_checkpoint(checkpoint, row['job'], row['checkpoint'])
            self._write(row)
        return dict(accepted=True, record=jobs.public(row))

    def found(self, job_id, run_id, checkpoint, candidate):
        with self.store.con:
            self.store.con.execute('BEGIN IMMEDIATE')
            row = jobs.read(self.store.con, job_id)
            if row['state'] == 'found' and row['run_id'] == run_id:
                same = jobs.validate_candidate(candidate, row['job']) == row['candidate']
                return dict(accepted=same, record=jobs.public(row))
            if row['state'] != 'running' or row['run_id'] != run_id:
                return dict(accepted=False, record=jobs.public(row))
            checkpoint = jobs.validate_checkpoint(checkpoint, row['job'], row['checkpoint'])
            candidate = jobs.validate_candidate(candidate, row['job'])
            if jobs.parse_index(checkpoint['next']) > jobs.candidate_index(candidate, row['job']):
                raise ValueError('The checkpoint would skip the found vanity result')
            row.update(state='found', checkpoint=checkpoint, candidate=candidate, error='')
            self._write(row)
        return dict(accepted=True, record=jobs.public(row))

    def pause(self, job_id, run_id=None, checkpoint=None, error=None):
        with self.store.con:
            self.store.con.execute('BEGIN IMMEDIATE')
            row = jobs.read(self.store.con, job_id)
            if run_id is not None and row['run_id'] != run_id:
                return dict(accepted=False, record=jobs.public(row))
            if row['state'] == 'running':
                if checkpoint is not None:
                    row['checkpoint'] = jobs.validate_checkpoint(checkpoint, row['job'], row['checkpoint'])
                message = str(error)[:300] if error else ''
                row.update(state='failed' if message else 'paused', error=message)
                self._write(row)
        return dict(accepted=True, record=jobs.public(row))

    def discard(self, profile_key, job_id):
        with self.store.con:
            self.store.con.execute('BEGIN IMMEDIATE')
            row = jobs.read(self.store.con, job_id)
            if row['state'] == 'running':
                raise ValueError('Pause the vanity search before discarding it')
            if row['state'] == 'saved':
                raise ValueError('This vanity address is already saved in the wallet')
            key = self.validate_private(row, profile_key)
            del key
            self.store.con.execute('DELETE FROM desktop_vanity_jobs WHERE job_id=?', (job_id,))
        return dict(discarded=True)

    def finalize(self, profile_key, store, job_id, candidate=None, label='Vanity address'):
        label = label_text(label)
        row = jobs.read(store.con, job_id)
        if row['state'] not in ('found','saved'):
            raise ValueError('This vanity search has no durably saved result')
        if candidate is not None and jobs.validate_candidate(candidate, row['job']) != row['candidate']:
            raise ValueError('This vanity job is reserved for a different result')
        candidate = row['candidate']
        secret = self.validate_private(row, profile_key)
        if row['state'] == 'saved':
            owner, key = store.private_key(profile_key, row['address'])
            del key
            if owner != candidate['address']:
                raise ValueError('The saved vanity address could not be verified')
            return dict(address=owner, recovery='profile-backup', already_saved=True)
        secret = secret.add(bytes.fromhex(candidate['offset']))
        branch = TRANSFORMS.index(candidate['transform'])
        for _ in range(branch % 3):
            secret = secret.multiply(LAMBDA)
        if branch >= 3:
            secret = secret.multiply((ORDER-1).to_bytes(32, 'big'))
        private_hex = secret.secret.hex()
        address, independent_key = crypto.wallet_from_pkhex(private_hex)
        del secret, independent_key
        if address != candidate['address']:
            raise ValueError('The vanity result failed independent address verification')
        with store.con:
            store.con.execute('BEGIN IMMEDIATE')
            current = jobs.read(store.con, job_id)
            if current != row:
                raise ValueError('The vanity job changed before saving')
            if not store.get_wallet_by_address(address):
                store._insert_address(profile_key, private_hex, label)
            owner, key = store.private_key(profile_key, address)
            same = key.to_string().hex() == private_hex
            del private_hex, key
            if owner != address or not same:
                raise ValueError('The saved vanity address could not be verified')
            receipt = dict(schema=1, job=row['job'], candidate=candidate, address=address)
            row.update(state='saved', seed='', address=address, label=label,
                       reservation=Fernet(profile_key).encrypt(jobs.encode(receipt).encode()).decode('ascii'), error='')
            self._write(row)
        return dict(address=address, recovery='profile-backup', already_saved=False)
