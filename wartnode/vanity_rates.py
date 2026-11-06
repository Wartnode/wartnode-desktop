"""Session-local speed measurements; no secrets or synthetic results are saved."""
from collections import deque
import copy
import hashlib
import statistics
import time
from . import vanity_jobs as jobs

WARMUP_SECONDS = 1.0
MEASURE_SECONDS = 8.0
MAX_AGE_SECONDS = 15*60
PUBLIC_POINT = '0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798'


def mode(value):
    return 'both' if value.prefix and value.suffix else 'prefix' if value.prefix else 'suffix'


def automatic_backend(backend, value):
    return 'cpu' if backend == 'auto' and value.length <= 6 else backend


def fingerprint(hardware, names, component):
    return hashlib.sha256(jobs.encode(dict(hardware=hardware, workers=names, component=component)).encode()).hexdigest()


class Samples:
    def __init__(self):
        self.baseline = None
        self.samples = deque(maxlen=24)
        self.durations = deque(maxlen=24)
        self.span = 0.0

    def add(self, seconds, count):
        if seconds < WARMUP_SECONDS:
            return
        if self.baseline is None:
            self.baseline = (seconds, count)
            return
        old_seconds, old_count = self.baseline
        delta = seconds-old_seconds
        if delta < 1.5:
            return
        if count < old_count:
            raise ValueError('Search counters moved backwards')
        if len(self.durations) == self.durations.maxlen:
            self.span -= self.durations[0]
        self.durations.append(delta)
        self.samples.append((count-old_count)/delta)
        self.span += delta
        self.baseline = (seconds, count)

    def summary(self):
        if len(self.samples) < 4 or self.span < 3:
            return None
        median = statistics.median(self.samples)
        low, high = min(self.samples), max(self.samples)
        if not 0 < low <= median <= high <= 1e15:
            return None
        return dict(rate=median, low=low, high=high, samples=len(self.samples),
                    seconds=self.span, variable=high/low > 2)


class Measurements:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.records = {}

    def observe(self, backend, hardware, names, component):
        for key, previous in list(self.records.items()):
            if (previous['component'] != component or (key[0] == backend
                    and (previous['hardware'] != hardware or previous['workers'] != names))):
                del self.records[key]

    def put(self, backend, match_mode, summary, hardware, names, component):
        if summary is None:
            return None
        record = dict(summary, backend=backend, mode=match_mode, hardware=copy.deepcopy(hardware),
                      workers=list(names), component=component,
                      fingerprint=fingerprint(hardware, names, component), observed=self.clock())
        self.observe(backend, hardware, names, component)
        self.records[(backend, match_mode)] = record
        return copy.deepcopy(record)

    def get(self, backend, match_mode, component=None):
        value = self.records.get((backend, match_mode))
        if (value is None or not 0 <= self.clock()-value['observed'] <= MAX_AGE_SECONDS
                or (component is not None and value['component'] != component)):
            return None
        return copy.deepcopy(value)
