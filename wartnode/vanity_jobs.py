"""Public durable-job contracts. No wallet private key is needed here."""
import hashlib
import json
import math
import uuid
from coincurve import PublicKey
from .compat import wallet_utils as crypto
from .vanity import pattern

ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
LAMBDA = bytes.fromhex('5363ad4cc05c30e0a5261c028812645a122e22ea20816678df02967c1b23bd72')
TRANSFORMS = ('identity', 'endo1', 'endo2', 'negate', 'negate-endo1', 'negate-endo2')
STATES = frozenset(('paused', 'running', 'found', 'saved', 'failed'))
MAX_JOBS = 10000
COLUMNS = ('job_id', 'state', 'job', 'seed', 'checkpoint', 'candidate', 'reservation',
           'address', 'label', 'backend', 'run_id', 'error', 'created', 'updated')
TABLE_SQL = ('CREATE TABLE desktop_vanity_jobs('
    'job_id TEXT PRIMARY KEY,state TEXT NOT NULL,job TEXT NOT NULL,seed TEXT NOT NULL,'
    'checkpoint TEXT NOT NULL,candidate TEXT,reservation TEXT,address TEXT,label TEXT NOT NULL,'
    'backend TEXT NOT NULL,run_id TEXT,error TEXT NOT NULL,created INTEGER NOT NULL,updated INTEGER NOT NULL)')


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def decode(value, limit=16384):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError('Invalid vanity job record')
    try:
        return json.loads(value)
    except (ValueError, TypeError) as error:
        raise ValueError('Invalid vanity job record') from error


def identity(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, AttributeError) as error:
        raise ValueError('Invalid vanity job identity') from error
    return value


def validate_job(job):
    if (not isinstance(job, dict) or set(job) != {'schema','session_id','public_key','pattern','strategy'}
            or type(job['schema']) is not int or job['schema'] != 3
            or job['strategy'] not in ('linear', 'endomorphism-v1')):
        raise ValueError('Unsupported vanity job')
    identity(job['session_id'])
    value = job['pattern']
    if (not isinstance(value, dict) or set(value) != {'profile','prefix','suffix'}
            or value['profile'] != dict(chain='warthog', network='mainnet', address_type='account')):
        raise ValueError('Only Warthog mainnet vanity jobs are supported')
    compiled = pattern(value['prefix'], value['suffix'])
    if compiled.prefix != value['prefix'] or compiled.suffix != value['suffix']:
        raise ValueError('Noncanonical vanity pattern')
    public = job['public_key']
    try:
        if (not isinstance(public, str) or len(public) != 66
                or PublicKey(bytes.fromhex(public)).format(compressed=True).hex() != public):
            raise ValueError()
    except (ValueError, TypeError) as error:
        raise ValueError('Invalid public vanity key') from error
    return job


def width(job):
    return 1 if job['strategy'] == 'linear' else 6


def index(number):
    if type(number) is not int or not 0 <= number <= ORDER*6:
        raise ValueError('Invalid vanity work index')
    return f'{number:064x}' if number <= ORDER else f'{number:066x}'


def parse_index(value):
    if (not isinstance(value, str) or len(value) not in (64, 66)
            or any(c not in '0123456789abcdef' for c in value)):
        raise ValueError('Invalid vanity work index')
    number = int(value, 16)
    if index(number) != value:
        raise ValueError('Noncanonical vanity work index')
    return number


def initial_checkpoint():
    return dict(next=index(0), evaluated=index(0), elapsed_seconds=0.0)


def validate_checkpoint(value, job, previous=None):
    if not isinstance(value, dict) or set(value) != {'next', 'evaluated', 'elapsed_seconds'}:
        raise ValueError('Invalid vanity checkpoint')
    cursor, count = parse_index(value['next']), parse_index(value['evaluated'])
    elapsed = value['elapsed_seconds']
    if (cursor > ORDER*width(job) or type(elapsed) not in (int, float)
            or not math.isfinite(elapsed) or not 0 <= elapsed <= 1e12):
        raise ValueError('Invalid vanity checkpoint bounds')
    if previous is not None and (cursor < parse_index(previous['next'])
            or count < parse_index(previous['evaluated']) or elapsed < previous['elapsed_seconds']):
        raise ValueError('Vanity checkpoint moved backwards')
    return dict(next=index(cursor), evaluated=index(count), elapsed_seconds=float(elapsed))


def candidate_index(candidate, job):
    branch = TRANSFORMS.index(candidate['transform'])
    return int(candidate['offset'], 16)*width(job)+branch


def validate_candidate(value, job):
    if (not isinstance(value, dict) or set(value)-{'offset','address','transform'}
            or not isinstance(value.get('offset'), str) or len(value['offset']) != 64
            or any(c not in '0123456789abcdef' for c in value['offset'])
            or value.get('transform','identity') not in TRANSFORMS):
        raise ValueError('Invalid vanity search result')
    offset = int(value['offset'], 16)
    branch = TRANSFORMS.index(value.get('transform','identity'))
    if offset >= ORDER or branch >= width(job):
        raise ValueError('Invalid vanity search offset or transformation')
    try:
        point = PublicKey(bytes.fromhex(job['public_key']))
        if offset:
            point = point.add(offset.to_bytes(32, 'big'))
        for _ in range(branch % 3):
            point = point.multiply(LAMBDA)
        if branch >= 3:
            point = point.multiply((ORDER-1).to_bytes(32, 'big'))
        payload = crypto.RIPEMD160.RIPEMD160Hash(hashlib.sha256(point.format(compressed=True)).digest()).digest()
        address = (payload+hashlib.sha256(payload).digest()[:4]).hex()
    except ValueError as error:
        raise ValueError('Invalid vanity public point') from error
    if address != value.get('address') or not pattern(job['pattern']['prefix'], job['pattern']['suffix']).matches(address):
        raise ValueError('The vanity result failed public address verification')
    return dict(offset=value['offset'], transform=TRANSFORMS[branch], address=address)


def row_value(row):
    from .profile import label_text
    value = dict(zip(COLUMNS, row))
    identity(value['job_id'])
    if value['state'] not in STATES:
        raise ValueError('Invalid vanity job state')
    job = validate_job(decode(value['job']))
    if job['session_id'] != value['job_id']:
        raise ValueError('Vanity job identity mismatch')
    checkpoint = validate_checkpoint(decode(value['checkpoint']), job)
    candidate = validate_candidate(decode(value['candidate']), job) if value['candidate'] is not None else None
    if ((value['state'] in ('found','saved')) != (candidate is not None)
            or not isinstance(value['seed'], str) or len(value['seed']) > 16384
            or (value['state'] == 'saved') != (value['seed'] == '')
            or (value['state'] == 'saved') != (value['reservation'] is not None)
            or (value['state'] == 'saved') != (value['address'] is not None)):
        raise ValueError('Inconsistent vanity job state')
    if value['state'] == 'saved' and (value['address'] != candidate['address']
            or not isinstance(value['reservation'], str) or len(value['reservation']) > 16384):
        raise ValueError('Invalid vanity finalization receipt')
    if candidate is not None and parse_index(checkpoint['next']) > candidate_index(candidate, job):
        raise ValueError('Vanity checkpoint skips its found result')
    if value['run_id'] is not None:
        identity(value['run_id'])
    if value['state'] == 'running' and value['run_id'] is None:
        raise ValueError('Missing vanity run identity')
    label_text(value['label'])
    if (value['backend'] not in ('auto','cpu','opencl') or not isinstance(value['error'], str)
            or len(value['error']) > 300 or type(value['created']) is not int
            or type(value['updated']) is not int or not 0 <= value['created'] <= value['updated']):
        raise ValueError('Invalid vanity job metadata')
    value.update(job=job, checkpoint=checkpoint, candidate=candidate)
    return value


def read(con, job_id):
    identity(job_id)
    row = con.execute('SELECT '+','.join(COLUMNS)+' FROM desktop_vanity_jobs WHERE job_id=?', (job_id,)).fetchone()
    if row is None:
        raise ValueError('This vanity job is no longer available')
    return row_value(row)


def public(value):
    return {k:v for k,v in value.items() if k not in ('seed','reservation')}
