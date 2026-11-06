"""Strict public worker protocol and bounded per-run telemetry."""
import json
import math
import time
from . import vanity_jobs as jobs

PROTOCOL = 3
MAX_LINE = 65536
ERRORS = {
    'invalid_request':'The search request was rejected. Reopen Wartnode and try again.',
    'initialization_failed':'Search hardware could not initialize. Try CPU or check your OpenCL driver.',
    'integrity_failed':'Search verification failed. The checkpoint is preserved; no address was imported.',
    'search_failed':'The search hardware stopped. The checkpoint is preserved; resume manually to try again.',
    'startup_timeout':'Search hardware took too long to initialize. Try CPU or check your OpenCL driver.',
    'heartbeat_timeout':'The search worker stopped responding. Its checkpoint is preserved.',
    'work_stalled':'The search stopped making progress. Its checkpoint is preserved.',
    'exit_timeout':'The search worker did not exit. It was stopped; saved results and checkpoints are preserved.',
    'protocol_error':'The search worker returned an invalid response. Its checkpoint is preserved.',
    'persistence_error':'The search could not save its progress. Restart Wartnode to recover the last saved checkpoint.',
    'worker_crashed':'The search worker stopped unexpectedly. Its checkpoint is preserved.',
}


class ProtocolError(ValueError):
    pass


def number(value, maximum=1e12):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= maximum:
        raise ProtocolError('Invalid telemetry number')
    return float(value)


def _object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ProtocolError('Duplicate worker field')
        result[name] = value
    return result


def _constant(_):
    raise ProtocolError('Nonfinite worker value')


def workers(value):
    if (not isinstance(value, list) or not 1 <= len(value) <= 256
            or any(not isinstance(name, str) or not 1 <= len(name) <= 256
                   or any(ord(c) < 32 for c in name) for name in value)
            or len(set(value)) != len(value)):
        raise ProtocolError('Invalid search workers')
    return value


class ProtocolState:
    def __init__(self, run, purpose='search', clock=time.monotonic):
        self.run = run
        self.purpose = purpose
        self.sequence = 0
        self.phase = 'new'
        self.checkpoint = jobs.validate_checkpoint(run['checkpoint'], run['job'])
        self.seconds = 0.0
        self.progress_seconds = 0.0
        self.names = None
        self.hardware = None
        self.clock = clock
        self.arrival = clock()
        self.tokens = 24.0

    def consume(self, line):
        try:
            if not isinstance(line, bytes) or len(line) > MAX_LINE or not line.endswith(b'\n'):
                raise ProtocolError('Invalid worker line size')
            event = json.loads(line, object_pairs_hook=_object, parse_constant=_constant)
            if (not isinstance(event, dict)
                    or set(event) != {'protocol','sequence','event','job_id','run_id','data'}
                    or type(event['protocol']) is not int or event['protocol'] != PROTOCOL
                    or type(event['sequence']) is not int or event['sequence'] != self.sequence+1
                    or event['job_id'] != self.run['job_id'] or event['run_id'] != self.run['run_id']
                    or not isinstance(event['data'], dict) or self.phase == 'terminal'):
                raise ProtocolError('Worker envelope mismatch')
            kind, data = event['event'], event['data']
            if kind == 'initializing':
                if self.phase != 'new' or set(data) != {'backend'} or data['backend'] not in ('auto','cpu','opencl'):
                    raise ProtocolError('Unexpected initialization')
                self.phase = 'initializing'
            elif kind == 'ready':
                if self.phase != 'initializing' or set(data) != {'workers','hardware','initialization_seconds'}:
                    raise ProtocolError('Unexpected ready response')
                self.names = workers(data['workers'])
                hardware = data['hardware']
                if (not isinstance(hardware, dict) or set(hardware) != {'cpu','gpus','threads'}
                        or not isinstance(hardware['cpu'], dict) or not isinstance(hardware['gpus'], list)
                        or len(hardware['gpus']) > 32 or type(hardware['threads']) is not int
                        or not 1 <= hardware['threads'] <= 256
                        or any(not isinstance(device, dict) for device in hardware['gpus'])
                        or len(jobs.encode(hardware)) > 16384):
                    raise ProtocolError('Invalid search hardware')
                number(data['initialization_seconds'])
                self.hardware = hardware
                self.phase = 'ready'
            elif kind in ('progress','found','paused','calibrated'):
                if self.phase != 'ready' or (kind == 'calibrated' and self.purpose != 'calibration'):
                    raise ProtocolError('Unexpected search progress')
                expected = {'elapsed_seconds','search_seconds','candidates_exact','workers','checkpoint'}
                if kind != 'progress':
                    expected.add('candidate')
                if set(data) != expected or workers(data['workers']) != self.names:
                    raise ProtocolError('Invalid progress fields')
                checkpoint = jobs.validate_checkpoint(data['checkpoint'], self.run['job'], self.checkpoint)
                elapsed, seconds = number(data['elapsed_seconds']), number(data['search_seconds'])
                if (seconds < self.seconds or elapsed != checkpoint['elapsed_seconds']
                        or abs(elapsed-(self.run['checkpoint']['elapsed_seconds']+seconds)) > .001
                        or data['candidates_exact'] != str(jobs.parse_index(checkpoint['evaluated']))):
                    raise ProtocolError('Inconsistent search counters')
                if kind == 'progress':
                    if seconds-self.progress_seconds < .1:
                        raise ProtocolError('Worker progress flood')
                    now = self.clock()
                    self.tokens = min(24.0,self.tokens+max(0,now-self.arrival)*8)
                    self.arrival = now
                    if self.tokens < 1:
                        raise ProtocolError('Worker response flood')
                    self.tokens -= 1
                    self.progress_seconds = seconds
                elif kind == 'found':
                    data['candidate'] = jobs.validate_candidate(data['candidate'], self.run['job'])
                    if jobs.parse_index(checkpoint['next']) > jobs.candidate_index(data['candidate'], self.run['job']):
                        raise ProtocolError('Checkpoint skips match')
                elif kind != 'progress' and data['candidate'] is not None:
                    raise ProtocolError('Unexpected candidate')
                self.checkpoint, self.seconds = checkpoint, seconds
                if kind != 'progress':
                    self.phase = 'terminal'
            elif kind == 'error':
                if (set(data)-{'code','checkpoint'} or data.get('code') not in
                        ('invalid_request','initialization_failed','integrity_failed','search_failed')):
                    raise ProtocolError('Invalid error response')
                if 'checkpoint' in data:
                    self.checkpoint = jobs.validate_checkpoint(data['checkpoint'], self.run['job'], self.checkpoint)
                self.phase = 'terminal'
            else:
                raise ProtocolError('Unknown worker event')
            self.sequence = event['sequence']
            return kind, data
        except (ValueError, TypeError, KeyError, OverflowError, UnicodeError) as error:
            raise ProtocolError('Invalid public search response') from error
