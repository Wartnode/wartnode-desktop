"""Create an owned fake search worker, then abruptly terminate its parent."""
import json
import os
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from wartnode.process_guard import guard_process
from wartnode.processes import hidden_options

process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('vanity_worker_faults.py')), 'silent'],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **hidden_options())
handle = guard_process(process)
process.stdin.write(sys.stdin.buffer.readline())
process.stdin.flush()
print(json.dumps(dict(pid=process.pid, guarded=bool(handle))), flush=True)
os._exit(0)
