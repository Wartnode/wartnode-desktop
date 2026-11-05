"""Mining presets and validated per-miner resource ceilings."""
import os

PRESETS = {'light': 20, 'normal': 50, 'heavy': 80}
MIN_PERCENT, MAX_PERCENT = 5, 95


def validate(cpu, gpu, mode='custom'):
    if any(type(value) is not int or not MIN_PERCENT <= value <= MAX_PERCENT for value in (cpu, gpu)):
        raise ValueError('CPU and GPU limits must be between 5% and 95%')
    if mode not in (*PRESETS, 'custom') or (mode in PRESETS and (cpu != PRESETS[mode] or gpu != PRESETS[mode])):
        raise ValueError('The mining preset does not match its limits')


def worker_threads(cpu, cores=None):
    validate(cpu, 50)
    cores = max(1, cores or os.cpu_count() or 1)
    return max(1, (cores * cpu + 99) // 100)
