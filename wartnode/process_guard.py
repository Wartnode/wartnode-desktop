"""Windows kill-on-close ownership shared by the runtime and installed launcher."""
import ctypes
import os


def guard_process(process, cpu_limit=None):
    """A parent crash must not leave owned hashing/node processes behind."""
    if cpu_limit is not None:
        from .mining import validate
        try:validate(cpu_limit,50)
        except ValueError:
            process.kill()
            raise
    if os.name != 'nt':
        if cpu_limit is not None:
            process.kill()
            raise ValueError('Mining CPU limits require Windows')
        return None
    from ctypes import wintypes
    size_t = ctypes.c_size_t
    class Basic(ctypes.Structure):
        _fields_ = [('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),('flags',wintypes.DWORD),
            ('minimum',size_t),('maximum',size_t),('active',wintypes.DWORD),('affinity',size_t),
            ('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]
    class Extended(ctypes.Structure):
        _fields_ = [('basic',Basic),('io',ctypes.c_uint64*6),('process_memory',size_t),('job_memory',size_t),
            ('peak_process',size_t),('peak_job',size_t)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateJobObjectW.restype=wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.CreateJobObjectW(None,None)
    limits=Extended()
    limits.basic.flags=0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if cpu_limit is not None and handle:
        class CpuRate(ctypes.Structure):
            _fields_=[('flags',wintypes.DWORD),('rate',wintypes.DWORD)]
        # ENABLE | HARD_CAP, in hundredths of total CPU capacity.
        cpu=CpuRate(0x1|0x4,cpu_limit*100)
        if not kernel.SetInformationJobObject(handle,15,ctypes.byref(cpu),ctypes.sizeof(cpu)):
            process.kill();kernel.CloseHandle(handle)
            raise ValueError('Windows could not apply the mining CPU limit. Mining was not started.')
    if (not handle or not kernel.SetInformationJobObject(handle,9,ctypes.byref(limits),ctypes.sizeof(limits))
            or not kernel.AssignProcessToJobObject(handle,int(process._handle))):
        process.kill()
        if handle:kernel.CloseHandle(handle)
        raise ValueError('Could not set up automatic cleanup for the node and miner')
    return handle
