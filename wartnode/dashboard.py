"""Bounded, public presentation data shared by the native dashboard pages."""
from collections import deque
from dataclasses import dataclass
import json
import math
import time

MAX_METRIC_BYTES = 4096
MAX_COUNTER = (1 << 63) - 1


@dataclass(frozen=True)
class ConnectionStatus:
    name: str
    detail: str
    local: bool
    ready: bool
    progress: float | None = None

    @classmethod
    def from_runtime(cls, state, mode):
        local = mode == 'local'
        raw = state.get('state', 'Stopped')
        if not local:
            return cls('Lite' if raw == 'Connected' else 'Offline',
                       state.get('error') or ('Remote node connected' if raw == 'Connected'
                                             else 'Lite service unavailable'), False, raw == 'Connected')
        if raw == 'Ready':
            return cls('Synced', 'Local node verified and ready', True, True, 1.0)
        if raw in ('Synchronizing', 'Starting / loading chain'):
            return cls('Syncing', 'Loading blockchain' if raw == 'Starting / loading chain'
                       else 'Verifying the blockchain', True, False)
        return cls('Offline', state.get('error') or raw, True, False, 0.0)


@dataclass(frozen=True)
class ProfileBalance:
    """A complete snapshot is required before advertising a profile total."""
    rows: tuple
    complete: bool
    available: int | None
    reserved: int | None
    checked: float
    source: str

    @classmethod
    def from_rows(cls, addresses, rows, source, checked=None):
        expected = {address.lower() for address in addresses}
        by_address = {}
        for row in rows:
            address = row.get('address', '').lower()
            if address not in expected:
                raise ValueError('Balance does not belong to this profile')
            if address in by_address:
                raise ValueError('Duplicate address balance')
            if row.get('error'):
                by_address[address] = dict(row, address=address)
                continue
            for key in ('balance', 'reserved', 'available'):
                if type(row.get(key)) is not int or row[key] < 0:
                    raise ValueError('Invalid address balance')
            if row['available'] != max(0, row['balance'] - row['reserved']):
                raise ValueError('Available balance disagrees with reservations')
            by_address[address] = dict(row, address=address)
        complete = expected == set(by_address) and all(not r.get('error') for r in by_address.values())
        return cls(tuple(by_address.values()), complete,
                   sum(r['available'] for r in by_address.values()) if complete else None,
                   sum(r['reserved'] for r in by_address.values()) if complete else None,
                   time.monotonic() if checked is None else checked, source)


def _number(value, minimum=0, maximum=1e30, optional=False):
    if value is None and optional:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError('Invalid miner measurement')
    return float(value)


@dataclass(frozen=True)
class GpuSample:
    identity: str
    name: str
    temperature_c: float | None
    utilization_pct: float | None
    power_w: float | None
    core_mhz: float | None
    memory_mhz: float | None

    @classmethod
    def from_dict(cls, value):
        identity, name = value.get('id'), value.get('name')
        if not all(isinstance(s, str) and 0 < len(s) <= 128 and not any(ord(c) < 32 for c in s)
                   for s in (identity, name)):
            raise ValueError('Invalid GPU identity')
        limits = {'temperature_c': 150, 'utilization_pct': 100, 'power_w': 3000,
                  'core_mhz': 20000, 'memory_mhz': 50000}
        return cls(identity, name, **{key: _number(value.get(key), maximum=limit, optional=True)
                                     for key, limit in limits.items()})


@dataclass(frozen=True)
class MinerSample:
    session: str
    elapsed_s: float
    janus_hps: float
    average_janus_hps: float
    gpu_sha_hps: float
    verus_hps: float
    accepted: int
    rejected: int
    blocks: int
    gpus: tuple
    received: float

    @classmethod
    def from_wire(cls, line, received=None):
        if not isinstance(line, str) or len(line.encode('utf-8')) > MAX_METRIC_BYTES:
            raise ValueError('Miner sample exceeds the size limit')
        try:
            value = json.loads(line)
            if type(value) is not dict or type(value.get('schema')) is not int or value['schema'] != 1:
                raise ValueError('Unsupported miner sample')
            session = value['session']
            if not isinstance(session, str) or not 1 <= len(session) <= 64 or not session.isascii():
                raise ValueError('Invalid miner session')
            rates = {key: _number(value[key]) for key in
                     ('elapsed_s', 'janus_hps', 'average_janus_hps', 'gpu_sha_hps', 'verus_hps')}
            counts = {key: value[key] for key in ('accepted', 'rejected', 'blocks')}
            if any(type(n) is not int or not 0 <= n <= MAX_COUNTER for n in counts.values()):
                raise ValueError('Invalid miner counter')
            devices = value.get('gpus', [])
            if not isinstance(devices, list) or len(devices) > 8:
                raise ValueError('Invalid miner device list')
            gpus = tuple(GpuSample.from_dict(gpu) for gpu in devices)
            if len({gpu.identity for gpu in gpus}) != len(gpus):
                raise ValueError('Duplicate miner device')
            return cls(session, **rates, **counts, gpus=gpus,
                       received=time.monotonic() if received is None else received)
        except (KeyError, TypeError, AttributeError, json.JSONDecodeError) as exc:
            raise ValueError('Malformed miner sample') from exc

    def fresh(self, now=None, maximum_age=20):
        age = (time.monotonic() if now is None else now) - self.received
        return 0 <= age <= maximum_age


class SampleHistory:
    def __init__(self, maximum=720):
        if type(maximum) is not int or not 2 <= maximum <= 3600:
            raise ValueError('Invalid history capacity')
        self.samples = deque(maxlen=maximum)
        self.session = None

    def append(self, sample):
        if sample.session != self.session:
            self.samples.clear()
            self.session = sample.session
        if self.samples and sample.elapsed_s <= self.samples[-1].elapsed_s:
            return False
        self.samples.append(sample)
        return True

    def clear(self):
        self.samples.clear()
        self.session = None
