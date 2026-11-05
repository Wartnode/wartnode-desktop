"""Read-only node/network statistics and Windows counters for owned processes."""
import ctypes
from dataclasses import dataclass
import math
import os
import time
from .rpc import NodeError


def finite(value, minimum=0, maximum=1e30):
    return float(value) if type(value) in (int,float) and math.isfinite(value) and minimum<=value<=maximum else None


def network_statistics(client):
    from .compat import wallet_utils as crypto
    client.require_network()
    head=client.head(require_synced=True)
    height=head['height']
    if height<1:raise NodeError('Network statistics require a non-genesis head')
    block=client.block(height)
    if block.get('header',{}).get('hash')!=head['hash']:
        raise NodeError('The chain changed while reading network statistics')
    reward=None
    rewards=block.get('body',{}).get('rewards')
    if isinstance(rewards,list) and len(rewards)==1:
        reward=sum(crypto.parse_e8(r['amount'],allow_zero=True) for r in rewards)
    hashrate=None;window=100
    try:
        estimate=client.request('/chain/hashrate/100')
        hashrate=finite(estimate.get('lastNBlocksEstimate'))
        n=estimate.get('N')
        if type(n) is not int or not 1<=n<=1000:hashrate=None
        else:window=n
    except NodeError:
        pass
    interval=None;interval_blocks=min(100,height-1)
    timestamp=block.get('header',{}).get('timestamp')
    if interval_blocks and type(timestamp) is int:
        older=client.request(f'/chain/block/{height-interval_blocks}/header').get('header',{}).get('timestamp')
        if type(older) is int and timestamp>older:interval=(timestamp-older)/interval_blocks
    newer=client.head(require_synced=True)
    if (newer['height'],newer['hash'])!=(height,head['hash']):
        raise NodeError('The chain changed while reading network statistics')
    return dict(height=height,difficulty=finite(head.get('difficulty')),hashrate=hashrate,
                hashrate_window=window,interval=interval,interval_blocks=interval_blocks,
                reward=reward,minimum_fee=client.minimum_fee(),checked=time.monotonic(),source=client.peer)


@dataclass(frozen=True)
class ProcessCounters:
    created: int
    cpu_ticks: int
    working_set: int | None
    private_bytes: int | None


def windows_process_counters(pid):
    """GetProcessTimes / K32GetProcessMemoryInfo, with query-only access.

    https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes
    https://learn.microsoft.com/windows/win32/api/psapi/nf-psapi-getprocessmemoryinfo
    """
    if os.name!='nt' or type(pid) is not int or not 0<pid<2**32:return None
    from ctypes import wintypes
    class Memory(ctypes.Structure):
        _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[
            (name,ctypes.c_size_t) for name in ('PeakWorkingSetSize','WorkingSetSize',
             'QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage',
             'QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage','PrivateUsage')]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    kernel.CloseHandle.restype=wintypes.BOOL
    kernel.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
    kernel.GetProcessTimes.restype=wintypes.BOOL
    kernel.K32GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(Memory),wintypes.DWORD]
    kernel.K32GetProcessMemoryInfo.restype=wintypes.BOOL
    handle=kernel.OpenProcess(0x1000,False,pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:return None
    try:
        created,exited,cpu_kernel,cpu_user=(wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle,ctypes.byref(created),ctypes.byref(exited),
                                      ctypes.byref(cpu_kernel),ctypes.byref(cpu_user)):return None
        count=lambda value:value.dwLowDateTime+(value.dwHighDateTime<<32)
        memory=Memory();memory.cb=ctypes.sizeof(memory)
        valid=kernel.K32GetProcessMemoryInfo(handle,ctypes.byref(memory),ctypes.sizeof(memory))
        return ProcessCounters(count(created),count(cpu_kernel)+count(cpu_user),
                               memory.WorkingSetSize if valid else None,memory.PrivateUsage if valid else None)
    finally:
        kernel.CloseHandle(handle)


class ProcessSampler:
    def __init__(self,reader=windows_process_counters,clock=time.monotonic,cores=None):
        self.reader,self.clock=reader,clock
        self.cores=cores or os.cpu_count() or 1
        self.previous=None

    def sample(self,pid):
        value=self.reader(pid) if pid else None
        now=self.clock()
        if value is None:
            self.previous=None
            return dict(pid=pid,cpu_percent=None,memory_bytes=None,private_bytes=None)
        cpu=None
        if self.previous:
            old_pid,old_created,ticks,when=self.previous
            if (old_pid,old_created)==(pid,value.created) and now>when and value.cpu_ticks>=ticks:
                cpu=finite((value.cpu_ticks-ticks)/(now-when)/1e7/self.cores*100,maximum=100.5)
                if cpu is not None:cpu=min(100,cpu)
        self.previous=(pid,value.created,value.cpu_ticks,now)
        return dict(pid=pid,cpu_percent=cpu,memory_bytes=value.working_set,private_bytes=value.private_bytes)


def hashrate_text(rate):
    if rate is None:return '—'
    for scale,unit in ((1e12,'TH/s'),(1e9,'GH/s'),(1e6,'MH/s'),(1e3,'kH/s')):
        if rate>=scale:return f'{rate/scale:.2f} {unit}'
    return f'{rate:.2f} H/s'


def difficulty_text(value):
    """Display hash-work magnitude, not a hash rate (there is no /s)."""
    number=finite(value)
    if number is None:return '—'
    if number>=1e12:return f'{number/1e12:.2f} TH'
    return f'{number/1e9:.2f} GH' if number==0 or number>=1e7 else f'{number/1e9:.3g} GH'
