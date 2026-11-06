"""Owned public worker with deadlines, strict telemetry and durable publication."""
import copy
import ctypes
from dataclasses import dataclass, field
import hashlib
import json
import os
import subprocess
import threading
import time
import uuid
from .paths import verified_component
from .process_guard import guard_process
from .vanity import pattern
from . import vanity_jobs as jobs
from .vanity_protocol import ProtocolState, ProtocolError, ERRORS, MAX_LINE, PROTOCOL
from .vanity_rates import Measurements, Samples, MEASURE_SECONDS, PUBLIC_POINT


@dataclass(frozen=True)
class Timeouts:
    startup: float = 180
    heartbeat: float = 10
    stalled: float = 60
    exit: float = 3
    pause: float = 3


@dataclass
class Session:
    process: object
    run: dict
    persist: object
    generation: int
    purpose: str
    backend: str
    match_mode: str
    component: str
    seconds: float | None
    protocol: ProtocolState
    samples: Samples = field(default_factory=Samples)
    stop: threading.Event = field(default_factory=threading.Event)
    born: float = field(default_factory=time.monotonic)
    heard: float = field(default_factory=time.monotonic)
    advanced: float = field(default_factory=time.monotonic)
    ready: float | None = None
    terminal: float | None = None
    count: int = 0
    failure: str | None = None
    closing: bool = False
    reader: object = None
    watchdog: object = None


class VanityWorker:
    def __init__(self, directory, timeouts=None):
        self.directory = directory
        self.process = None
        self.job_handle = None
        self.reader = None
        self.lock = threading.Lock()
        self.state = dict(status='idle', progress={}, error='', purpose='search')
        self.exclusive = False
        self.persist = None
        self.run = None
        self.generation = 0
        self.session = None
        self.timeouts = timeouts or Timeouts()
        self.measurements = Measurements()
        self.component = None
        self.component_path = None
        self.component_stamp = None

    @property
    def running(self):
        return self.process is not None and self.process.poll() is None

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.state)

    def measurement(self, backend, match_mode):
        with self.lock:
            try:
                if self.component_path:
                    info = self.component_path.stat()
                    if (info.st_size, info.st_mtime_ns) != self.component_stamp:
                        self.measurements.records.clear()
            except OSError:
                self.measurements.records.clear()
            return self.measurements.get(backend, match_mode, self.component)

    def _publish(self, generation, **changes):
        with self.lock:
            if generation == self.generation:
                self.state.update(changes)

    def calibrate(self, backend, match_mode):
        if match_mode not in ('prefix','suffix','both'):
            raise ValueError('Choose a prefix, suffix, or combined search')
        # Short suffix searches must hash the checksum for every payload. A
        # longer suffix would add a cheap payload rejection and overstate speed.
        # Combined calibration uses the least selective nonempty prefix.
        prefix, suffix = {'prefix':('0'*16,''), 'suffix':('','0'*8), 'both':('0','0'*8)}[match_mode]
        job_id, run_id = str(uuid.uuid4()), str(uuid.uuid4())
        job = dict(schema=3, session_id=job_id, public_key=PUBLIC_POINT,
                   pattern=dict(profile=dict(chain='warthog', network='mainnet', address_type='account'),
                                prefix=prefix, suffix=suffix), strategy='endomorphism-v1')
        run = dict(job_id=job_id, run_id=run_id, job=job, checkpoint=jobs.initial_checkpoint())
        self._start(run, backend, 'calibration', match_mode, MEASURE_SECONDS)

    def start(self, run, backend):
        value = pattern(run['job']['pattern']['prefix'], run['job']['pattern']['suffix'])
        match_mode = 'both' if value.prefix and value.suffix else 'prefix' if value.prefix else 'suffix'
        self._start(run, backend, 'search', match_mode, None)

    def _start(self, run, backend, purpose, match_mode, seconds):
        if self.running:
            raise ValueError('A vanity search or speed measurement is already running')
        if purpose == 'search' and not callable(self.persist):
            raise ValueError('The durable vanity store is not connected')
        job = jobs.validate_job(run['job'])
        jobs.identity(run['run_id'])
        checkpoint = jobs.validate_checkpoint(run['checkpoint'], job)
        if run['job_id'] != job['session_id']:
            raise ValueError('Invalid vanity job identity')
        if backend not in ('auto','cpu','opencl'):
            raise ValueError('Choose Automatic, CPU or GPU')
        self.close()
        try:
            executable = verified_component('vanity')
        except (OSError, KeyError, ValueError) as error:
            raise ValueError('The vanity worker is unavailable. Rebuild or reinstall this development package.') from error
        self.component = hashlib.sha256(executable.read_bytes()).hexdigest()
        self.component_path = executable
        info = executable.stat()
        self.component_stamp = (info.st_size, info.st_mtime_ns)
        from .processes import hidden_options
        environment = dict(os.environ, TMP=str(self.directory/'runtime'), TEMP=str(self.directory/'runtime'))
        process = subprocess.Popen([str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=environment, **hidden_options())
        self.process = process
        try:
            self.job_handle = guard_process(process)
            self.exclusive = purpose == 'calibration' or pattern(job['pattern']['prefix'], job['pattern']['suffix']).exclusive
            self.run = copy.deepcopy(run)
            self.generation += 1
            session = Session(process, copy.deepcopy(run), self.persist, self.generation,
                              purpose, backend, match_mode, self.component, seconds, ProtocolState(run, purpose))
            session.count = jobs.parse_index(checkpoint['evaluated'])
            self.session = session
            with self.lock:
                self.state = dict(status='initializing', job_id=run['job_id'], run_id=run['run_id'],
                                  progress={}, error='', error_code='', purpose=purpose, measurement=None)
            request = dict(protocol=PROTOCOL, purpose=purpose, seconds=seconds, job=job,
                           run_id=run['run_id'], checkpoint=checkpoint, backend=backend,
                           threads=max(1, min(8, (os.cpu_count() or 1)-1)))
            process.stdin.write((json.dumps(request)+'\n').encode())
            process.stdin.flush()
            session.reader = self.reader = threading.Thread(target=self._read, args=(session,), daemon=True)
            session.watchdog = threading.Thread(target=self._watch, args=(session,), daemon=True)
            session.reader.start()
            session.watchdog.start()
        except Exception:
            self.close()
            raise

    @staticmethod
    def _kill(process):
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass

    def _watch(self, session):
        while not session.stop.wait(.1):
            now, limits = time.monotonic(), self.timeouts
            with self.lock:
                if session.terminal is not None:
                    code = 'exit_timeout' if session.process.poll() is None and now-session.terminal > limits.exit else None
                elif session.ready is None:
                    code = 'startup_timeout' if now-session.born > limits.startup else None
                elif now-session.heard > limits.heartbeat:
                    code = 'heartbeat_timeout'
                elif now-session.advanced > limits.stalled:
                    code = 'work_stalled'
                elif session.seconds and now-session.ready > session.seconds+limits.exit+2:
                    code = 'exit_timeout'
                else:
                    code = None
                if code:
                    session.failure = code
            if code:
                self._kill(session.process)
                self._publish(session.generation, status='error', error=ERRORS[code], error_code=code)
                return

    def _persist(self, session, kind, data):
        args = dict(job_id=session.run['job_id'], run_id=session.run['run_id'])
        if 'checkpoint' in data:
            args['checkpoint'] = data['checkpoint']
        if kind == 'found':
            args['candidate'] = data['candidate']
        elif kind == 'error':
            args['error'] = ERRORS[data['code']]
        action = {'progress':'vanity_checkpoint','found':'vanity_found','paused':'vanity_pause','error':'vanity_fail'}[kind]
        try:
            result = session.persist(action, **args)
            if not result['accepted']:
                raise ValueError('Stale vanity run')
            return result
        except Exception as error:
            raise PersistenceError() from error

    def _read(self, session):
        process, terminal = session.process, None
        failure = None
        try:
            while True:
                line = process.stdout.readline(MAX_LINE+1)
                if not line:
                    break
                kind, data = session.protocol.consume(line)
                now = time.monotonic()
                with self.lock:
                    session.heard = now
                    if kind == 'ready':
                        session.ready = session.advanced = now
                    if kind in ('progress','found','paused','calibrated'):
                        count = jobs.parse_index(data['checkpoint']['evaluated'])
                        if count > session.count:
                            session.count, session.advanced = count, now
                    if kind in ('found','paused','calibrated','error'):
                        session.terminal = now
                if kind == 'initializing':
                    continue
                if kind == 'ready':
                    if ((session.backend == 'cpu' and any(not name.startswith('cpu:') for name in data['workers']))
                            or (session.backend == 'opencl' and any(not name.startswith('opencl:') for name in data['workers']))):
                        raise ProtocolError('The requested hardware was replaced')
                    with self.lock:
                        self.measurements.observe(session.backend,data['hardware'],data['workers'],session.component)
                    self._publish(session.generation, hardware=data['hardware'], workers=data['workers'])
                    continue
                if session.purpose == 'search':
                    self._persist(session, kind, data)
                if kind in ('progress','found','paused','calibrated'):
                    session.samples.add(data['search_seconds'], jobs.parse_index(data['checkpoint']['evaluated']))
                    summary = session.samples.summary()
                    data = dict(data, speed=summary)
                if kind == 'progress':
                    self._publish(session.generation, status='searching', progress=data)
                else:
                    terminal = kind
                    measurement = None
                    if kind == 'calibrated':
                        if data['search_seconds'] < MEASURE_SECONDS:
                            raise ProtocolError('Truncated measurement')
                        with self.lock:
                            self.measurements.records.pop((session.backend,session.match_mode),None)
                            measurement = self.measurements.put(session.backend, session.match_mode, data['speed'],
                                session.protocol.hardware, session.protocol.names, session.component)
                    self._publish(session.generation, progress=data, measurement=measurement,
                                  error=ERRORS[data['code']] if kind == 'error' else '',
                                  error_code=data.get('code',''))
            code = process.wait(timeout=self.timeouts.exit)
            if code != 0 and terminal != 'error' and not (session.closing and terminal is None):
                raise ChildProcessError()
            if terminal is None:
                if session.closing:
                    terminal = 'paused'
                else:
                    raise ChildProcessError()
            if session.failure:
                failure = session.failure
            else:
                self._publish(session.generation, status='error' if terminal == 'error' else terminal)
        except ProtocolError:
            failure = session.failure or 'protocol_error'
        except PersistenceError:
            failure = session.failure or 'persistence_error'
        except Exception:
            failure = session.failure or 'worker_crashed'
        finally:
            session.stop.set()
            self._kill(process)
            try:
                process.wait(timeout=self.timeouts.exit)
            except subprocess.TimeoutExpired:
                pass
            try:
                process.stdout.close()
            except OSError:
                pass
            if failure:
                if session.purpose == 'search':
                    try:
                        session.persist('vanity_fail', job_id=session.run['job_id'],
                                        run_id=session.run['run_id'], error=ERRORS[failure])
                    except Exception:
                        pass  # Startup recovery retains the last committed checkpoint.
                self._publish(session.generation, status='error', error=ERRORS[failure], error_code=failure)

    def close(self):
        session, process = self.session, self.process
        if session:
            session.closing = True
            session.stop.set()
        if process is not None:
            if process.poll() is None:
                try:
                    process.stdin.write(b'{"command":"pause"}\n')
                    process.stdin.flush()
                    process.wait(timeout=self.timeouts.pause)
                except (OSError, subprocess.TimeoutExpired):
                    self._kill(process)
                    try:
                        process.wait(timeout=self.timeouts.exit)
                    except subprocess.TimeoutExpired:
                        pass
            if self.reader:
                self.reader.join(timeout=1)
            if session and session.watchdog:
                session.watchdog.join(timeout=1)
            for stream in (process.stdin, process.stdout):
                if stream and (stream is process.stdin or not self.reader or not self.reader.is_alive()):
                    try:
                        stream.close()
                    except OSError:
                        pass
        if self.job_handle and os.name == 'nt':
            ctypes.WinDLL('kernel32').CloseHandle(ctypes.c_void_p(self.job_handle))
        self.process, self.job_handle, self.reader, self.run, self.session = None, None, None, None, None
        self.exclusive = False
        if session and session.purpose == 'search':
            try:
                result = session.persist('vanity_pause', job_id=session.run['job_id'], run_id=session.run['run_id'])
                self._publish(session.generation, status=result['record']['state'])
            except Exception:
                self._publish(session.generation, status='error', error=ERRORS['persistence_error'],
                              error_code='persistence_error')


class PersistenceError(Exception):
    pass
