"""Public Warthog validation and probability-based difficulty estimates."""
from dataclasses import dataclass
import hashlib
import math

HEX = frozenset('0123456789abcdef')
MINING_THRESHOLD = 8


@dataclass(frozen=True)
class Pattern:
    prefix: str
    suffix: str

    @property
    def length(self):
        return len(self.prefix)+len(self.suffix)

    @property
    def exclusive(self):
        return self.length > MINING_THRESHOLD

    def matches(self, address):
        return address.startswith(self.prefix) and address.endswith(self.suffix)


def pattern(prefix='', suffix=''):
    if not isinstance(prefix, str) or not isinstance(suffix, str):
        raise ValueError('Enter a hexadecimal prefix or suffix')
    prefix, suffix = prefix.strip().lower(), suffix.strip().lower()
    value = Pattern(prefix, suffix)
    if not value.length:
        raise ValueError('Enter at least one prefix or suffix character')
    if any(c not in HEX for c in prefix+suffix):
        raise ValueError('Use only 0–9 and a–f; Warthog addresses do not include 0x')
    if value.length > 48:
        raise ValueError('Prefix and suffix together must contain at most 48 characters')
    specified = list(prefix+'?'*(48-value.length)+suffix)
    if '?' not in specified[:40]:
        checksum = hashlib.sha256(bytes.fromhex(''.join(specified[:40]))).hexdigest()[:8]
        if any(a != '?' and a != b for a, b in zip(specified[40:], checksum)):
            raise ValueError('This pattern fixes a payload with a different checksum; no address can match')
    return value


def duration(seconds):
    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return 'unavailable'
    if seconds < 1:
        return 'under a second'
    for unit, scale in [('years', 365.25*86400), ('days', 86400), ('hours', 3600), ('minutes', 60)]:
        if seconds >= scale:
            count = seconds/scale
            return f'{count:,.1f} {unit}' if count < 1000 else f'{count:.1e} {unit}'
    return '1 second' if round(seconds) == 1 else f'{seconds:.0f} seconds'


def estimate(value, rate=None):
    # Prefixes in the 160-bit payload are exact under the uniform hash model.
    # Checksum constraints are correlated: this is explicitly a rough model.
    specified_payload = len(value.prefix) >= 40 or value.length == 48
    bits = 160 if specified_payload else 4*value.length
    attempts = 2**bits
    approximate = not specified_payload and (bool(value.suffix) or len(value.prefix) > 40)
    practical = bits < 56
    if rate is not None and (type(rate) not in (float, int) or not math.isfinite(rate) or rate <= 0):
        rate = None
    log_miss = math.log1p(-1/attempts)
    median = math.ceil(math.log(.5)/log_miss)
    p95 = math.ceil(math.log(.05)/log_miss)
    return dict(attempts=attempts, approximate=approximate, practical=practical,
                cpu_seconds=attempts/1_000_000, gpu_seconds=attempts/100_000_000,
                mean_seconds=attempts/rate if rate else None,
                median_seconds=median/rate if rate else None,
                p95_seconds=p95/rate if rate else None,
                sparse=not specified_payload and value.length > 40)


def chance(value, seconds, rate):
    """Chance of a hit in future work under the same uniform/approximate model."""
    if (type(seconds) not in (float, int) or not math.isfinite(seconds) or seconds < 0
            or type(rate) not in (float, int) or not math.isfinite(rate) or rate <= 0):
        return None
    attempts = estimate(value)['attempts']
    return -math.expm1(seconds*rate*math.log1p(-1/attempts))
