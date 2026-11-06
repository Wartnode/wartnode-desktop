"""Public-only faulty worker fixtures. Never used by the product."""
import hashlib
import json
import sys
import time

request = json.loads(sys.stdin.readline())
scenario = sys.argv[1]
sequence = 0
job = request['job']
ident, run_id = job['session_id'], request['run_id']
base = request['checkpoint']
workers = ['cpu:0']


def emit(kind, data, **change):
    global sequence
    sequence += 1
    message = dict(protocol=3, sequence=sequence, event=kind, job_id=ident, run_id=run_id, data=data)
    message.update(change)
    print(json.dumps(message), flush=True)


def progress(seconds=.2, count=0):
    checkpoint = dict(next=f'{count:064x}', evaluated=f'{count:064x}',
                      elapsed_seconds=base['elapsed_seconds']+seconds)
    return dict(elapsed_seconds=checkpoint['elapsed_seconds'], search_seconds=seconds,
                checkpoint=checkpoint, workers=workers, candidates_exact=str(count))


if scenario == 'silent':
    time.sleep(30)
elif scenario == 'partial':
    sys.stdout.write('{"protocol":3'); sys.stdout.flush(); time.sleep(30)
elif scenario == 'crash':
    sys.exit(9)
elif scenario == 'bad_json':
    print('{untrusted C:/private/path}', flush=True)
elif scenario == 'oversized':
    print('x'*65537, flush=True)
elif scenario == 'duplicate':
    print('{"protocol":3,"protocol":3}', flush=True)
elif scenario == 'wrong_id':
    emit('initializing', dict(backend='cpu'), job_id='stale')
elif scenario == 'wrong_protocol':
    emit('initializing', dict(backend='cpu'), protocol=2)
elif scenario == 'wrong_sequence':
    emit('initializing', dict(backend='cpu'), sequence=2)
else:
    emit('initializing', dict(backend='cpu'))
    emit('ready', dict(workers=workers, hardware=dict(cpu={}, gpus=[], threads=1), initialization_seconds=.01))
    if scenario == 'ready_silent':
        time.sleep(30)
    elif scenario == 'stalled':
        for n in range(1,100):
            time.sleep(.2)
            emit('progress', progress(n*.2, 0))
    elif scenario == 'nan':
        emit('progress', progress(float('nan')))
    elif scenario == 'flood':
        emit('progress', progress(.2, 0))
        emit('progress', progress(.201, 0))
    elif scenario == 'reverse':
        emit('progress', progress(.2, 2))
        emit('progress', progress(.4, 1))
    elif scenario == 'checkpoint':
        emit('progress', progress(.2, 1))
        time.sleep(30)
    elif scenario in ('terminal_hang','after_terminal'):
        emit('paused', dict(progress(), candidate=None))
        if scenario == 'after_terminal':
            emit('progress', progress(.4))
        time.sleep(30)
    elif scenario == 'found_hang':
        from coincurve import PublicKey
        from Cryptodome.Hash import RIPEMD160
        key = PublicKey(bytes.fromhex(job['public_key']))
        for offset in range(1,10000):
            point = key.add(offset.to_bytes(32,'big')).format(compressed=True)
            payload = RIPEMD160.new(hashlib.sha256(point).digest()).digest()
            address = (payload+hashlib.sha256(payload).digest()[:4]).hex()
            if address.startswith(job['pattern']['prefix']):
                candidate = dict(offset=f'{offset:064x}', transform='identity', address=address)
                emit('found', dict(progress(.2, offset*6+1), checkpoint=dict(base,evaluated=f'{offset*6+1:064x}'),
                                   elapsed_seconds=base['elapsed_seconds'], search_seconds=0, candidate=candidate))
                break
        time.sleep(30)
    elif scenario == 'forged':
        emit('found', dict(progress(), candidate=dict(offset='0'*64, transform='identity', address='0'*48)))
    elif scenario == 'raw_error':
        emit('error', dict(code='search_failed', message='C:/private/path'))
    elif scenario == 'clean_pause':
        sys.stdin.readline()
        emit('paused', dict(progress(), candidate=None))
    elif scenario == 'stderr_flood':
        sys.stderr.write('x'*1000000); sys.stderr.flush()
        sys.exit(7)
