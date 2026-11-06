"""Offline vanity calibration and repeated lifecycle qualification on real hardware."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wartnode import __version__, vanity_jobs as jobs
from wartnode.paths import data_root, atomic_json
from wartnode.processes import Runtime, SignerClient
from wartnode.statistics import windows_process_counters


def resources():
    handles = None
    if os.name == 'nt':
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.GetProcessHandleCount.argtypes = [wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
        count = wintypes.DWORD()
        if kernel.GetProcessHandleCount(kernel.GetCurrentProcess(),ctypes.byref(count)):
            handles = count.value
    memory = windows_process_counters(os.getpid())
    return dict(handles=handles, threads=threading.active_count(),
                private_bytes=memory.private_bytes if memory else None)


def wait(runtime, predicate, timeout=210):
    until = time.monotonic()+timeout
    while time.monotonic() < until:
        state = runtime.vanity.snapshot()
        if predicate(state):
            return state
        if state['status'] == 'error':
            raise ValueError('Search failed: '+state['error'])
        time.sleep(.05)
    raise ValueError('Qualification timed out: '+str(runtime.vanity.snapshot()))


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--gpu',action='store_true',help='Include physical OpenCL qualification; failures are fatal')
    parser.add_argument('--cycles',type=int,default=20)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'work/vanity-m4-6-qualification')
    args = parser.parse_args()
    if not 5 <= args.cycles <= 1000:
        parser.error('Use 5 to 1000 lifecycle cycles')
    args.output_dir.mkdir(parents=True,exist_ok=True)
    result = dict(application=__version__, all_passed=False, synthetic_offline_data=True,
                  calibrations=[], lifecycle=[], transactions_broadcast=0,
                  unqualified=[])
    try:
        with tempfile.TemporaryDirectory(prefix='vanity-qualification-',dir=ROOT/'work') as temporary:
            directory = data_root(temporary)
            signer = SignerClient(directory)
            runtime = Runtime(directory)
            runtime.vanity.persist = signer.vanity_call
            try:
                signer.call('setup',password='public-synthetic-qualification-password')
                run = signer.call('vanity_prepare',prefix='0'*16)
                signer.call('vanity_pause',job_id=run['job_id'],run_id=run['run_id'])
                before = signer.call('vanity_status')['record']
                signer.call('lock')
                for backend in (('cpu','opencl') if args.gpu else ('cpu',)):
                    for mode in ('prefix','suffix','both'):
                        print('Measuring '+backend+' '+mode,flush=True)
                        runtime.calibrate_vanity(backend,mode)
                        state = wait(runtime,lambda s:s['status']=='calibrated')
                        record = runtime.vanity.measurement(backend,mode)
                        if record is None:
                            raise ValueError('Measurement was not stable enough')
                        result['calibrations'].append(record)
                        if signer.call('vanity_status')['record'] != before or signer.call('list')['wallets']:
                            raise ValueError('Calibration changed the pending search or wallet')
                        runtime.stop_vanity()
                        atomic_json(args.output_dir/'qualification.json',result)
                signer.call('unlock',password='public-synthetic-qualification-password')
                for cycle in range(args.cycles):
                    if cycle in (args.cycles//2,):
                        signer.close()
                        signer = SignerClient(directory)
                        runtime.vanity.persist = signer.vanity_call
                        signer.call('unlock',password='public-synthetic-qualification-password')
                    run = signer.call('vanity_resume',job_id=run['job_id'],backend='cpu')
                    runtime.start_vanity(run,'cpu')
                    session = runtime.vanity.session
                    wait(runtime,lambda s:jobs.parse_index(s.get('progress',{}).get('checkpoint',run['checkpoint'])['next'])
                         > jobs.parse_index(run['checkpoint']['next']),timeout=30)
                    runtime.stop_vanity()
                    if (session.process.poll() is None or session.reader.is_alive() or session.watchdog.is_alive()):
                        raise ValueError('A cycle left an owned process or thread running')
                    row = signer.call('vanity_status')['record']
                    if row['state'] != 'paused' or row['job'] != run['job']:
                        raise ValueError('A cycle changed the durable job')
                    result['lifecycle'].append(dict(cycle=cycle+1,checkpoint=row['checkpoint'],resources=resources()))
                    atomic_json(args.output_dir/'qualification.json',result)
                warmed = [row['resources'] for row in result['lifecycle'][3:]]
                if warmed:
                    first, last = warmed[0], warmed[-1]
                    if first['handles'] is not None and last['handles'] > first['handles']+12:
                        raise ValueError('Process handle growth exceeded the cycle allowance')
                    if last['threads'] > first['threads']+1:
                        raise ValueError('Reader thread growth exceeded the cycle allowance')
                    if first['private_bytes'] is not None and last['private_bytes'] > first['private_bytes']+32*1024*1024:
                        raise ValueError('Private memory growth exceeded the cycle allowance')
                if signer.call('list')['wallets']:
                    raise ValueError('Synthetic long searches unexpectedly created a wallet')
                result['all_passed'] = True
            finally:
                runtime.shutdown()
                signer.close()
    except Exception as error:
        result['error'] = str(error)
        raise
    finally:
        vendors = ' '.join(device['vendor'].lower() for record in result['calibrations']
                           for device in record['hardware']['gpus'])
        families = [('NVIDIA GPU OpenCL',('nvidia',)),('AMD GPU OpenCL',('amd','advanced micro devices')),
                    ('Intel GPU OpenCL',('intel',))]
        result['unqualified'] = [name for name,tokens in families if not any(token in vendors for token in tokens)]
        result['unqualified'] += ['physical Windows suspend/resume','driver removal/reset','long beta soak']
        atomic_json(args.output_dir/'qualification.json',result)
    print(json.dumps(dict(all_passed=True,calibrations=len(result['calibrations']),cycles=len(result['lifecycle']),
                          receipt=str(args.output_dir/'qualification.json'))))


if __name__ == '__main__':
    main()
