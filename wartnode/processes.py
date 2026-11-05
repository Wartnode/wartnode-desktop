"""Owned process lifecycle, private signer transport and Windows graceful stop."""
import collections
import ctypes
from functools import wraps
import json
import os
from pathlib import Path
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
from .paths import verified_component
from .settings import chain_directory, resume_move, move_chain, storage_info


def serialized(function):
    @wraps(function)
    def locked(self, *args, **kwargs):
        with self.operation_lock:
            return function(self, *args, **kwargs)
    return locked


from .process_guard import guard_process


def application_command(*arguments):
    if getattr(sys,'frozen',False):
        return [str(Path(sys.executable).parent/'WartNodeHelper.exe'),*arguments]
    return [sys.executable,"-m","wartnode",*arguments]


def hidden_options(console=False):
    if os.name != "nt":
        return {"start_new_session": True}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    return {"creationflags": subprocess.CREATE_NEW_CONSOLE if console else subprocess.CREATE_NO_WINDOW, "startupinfo": startup}


def signal_console(pid):
    if os.name != "nt":
        os.kill(pid, 2)
        return True
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.FreeConsole()
    if not kernel.AttachConsole(int(pid)):
        return False
    kernel.SetConsoleCtrlHandler(None, True)
    result = bool(kernel.GenerateConsoleCtrlEvent(0, 0))
    time.sleep(.2)
    kernel.FreeConsole()
    return result


class OwnedProcess:
    def __init__(self, name, directory):
        self.name, self.directory = name, Path(directory)
        self.process = None
        self.lines = collections.deque(maxlen=250)
        self.metrics = {}
        self.stopping = False
        self.job = None
        self.rpc_ready_at = float('inf')
        self.operation_lock = threading.RLock()

    @property
    def running(self):
        return self.process is not None and self.process.poll() is None

    @serialized
    def start(self, arguments, cwd, cpu_limit=None):
        if self.running:
            raise ValueError(f"{self.name} is already running")
        log = self.directory / "logs" / (self.name + ".log")
        if log.exists() and log.stat().st_size > 5 * 1024 * 1024:
            os.replace(log, log.with_suffix(".previous.log"))
        self.stopping = False
        self.cwd = Path(cwd).resolve()
        self.lines.clear()
        self.metrics = {}
        self.rpc_ready_at = float('inf')
        environment = {k: v for k, v in os.environ.items() if not k.startswith(("BRMINER_", "WART_", "CONNECTCOIN_"))}
        environment["LOCALAPPDATA"] = str(self.directory / "runtime")
        self.process = subprocess.Popen([str(a) for a in arguments], cwd=cwd, env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **hidden_options(console=True))
        if self.job:
            ctypes.WinDLL('kernel32').CloseHandle(ctypes.c_void_p(self.job))
            self.job=None
        self.job = guard_process(self.process,cpu_limit=cpu_limit)
        process = self.process

        def read():
            with log.open("a", encoding="utf-8") as stream:
                for raw in iter(process.stdout.readline, b""):
                    line = raw.decode("utf-8", "replace").rstrip()[:4096]
                    self.lines.append(line)
                    if 'RPC endpoint is ' in line:self.rpc_ready_at = time.monotonic()+.5
                    stream.write(line + "\n")
                    stream.flush()
                    if stream.tell() > 5 * 1024 * 1024:
                        stream.seek(0)
                        stream.truncate()
                    if "BRMINER_METRICS " in line:
                        try:
                            values = line.split("BRMINER_METRICS ", 1)[1].split()
                            self.metrics = {key:float(value) for key,value in (token.split("=",1) for token in values if "=" in token)}
                        except (ValueError, TypeError):
                            pass
        threading.Thread(target=read, daemon=True).start()

    @serialized
    def stop(self, timeout=20):
        if not self.running:
            code = self.process.returncode if self.process else None
            return {"graceful": code in (None, 0), "exit_code": code}
        self.stopping = True
        process = self.process
        helper = subprocess.run(application_command("--signal", str(process.pid)), timeout=5,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **hidden_options())
        try:
            process.wait(timeout=timeout)
            return {"graceful": helper.returncode == 0 and process.returncode == 0, "exit_code": process.returncode}
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            return {"graceful": False, "forced": True}


class Runtime:
    RPC_PORT, STRATUM_PORT, P2P_PORT = 19400, 19456, 19187

    def __init__(self, directory):
        self.directory = Path(directory)
        self.node = OwnedProcess("node", directory)
        self.miner = OwnedProcess("miner", directory)
        self.ready = False
        self.checked = 0
        self.state = {"state": "Stopped", "height": 0, "peers": 0}
        self.operation_lock = threading.RLock()
        self.closed = False
        self.stop_reason = ''
        self.recovery_error=''
        from .chain_import import resume
        try:resume(self.directory)
        except (ValueError,OSError) as error:self.recovery_error=str(error)

    @serialized
    def start_node(self):
        if self.closed:
            raise ValueError('Application is shutting down')
        from .chain_import import resume
        resume(self.directory)
        from . import settings
        prefs=settings.load(self.directory)
        if prefs['connection_mode']=='lite':raise ValueError('Switch to Full node to start a local node')
        if not prefs['setup_complete']:raise ValueError('Initialize the node before starting it')
        resume_move(self.directory)
        self.resume_archives()
        chain = chain_directory(self.directory)
        chain.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(chain).free < 1024 * 1024 * 1024:
            raise ValueError("At least 1 GiB of free space is required to start; a full chain requires additional storage")
        self.stop_reason = ''
        for port in (self.RPC_PORT, self.STRATUM_PORT, self.P2P_PORT):
            with socket.socket() as probe:
                try:
                    probe.bind(("127.0.0.1", port))
                except OSError as exc:
                    raise ValueError(f"Port {port} is already in use. Stop the other instance or application.") from exc
        self.ready = False
        # Chain folders may retain an upstream config with optional public RPC
        # or signing settings. Use a product-owned config, preserving that file.
        config = self.directory / 'runtime' / 'managed-node.toml'
        config.write_text('# Managed by Wartnode-Desktop.\n', encoding='utf-8')
        self.node.start([verified_component("node"), "--rpc=127.0.0.1:19400", "--stratum=127.0.0.1:19456",
            "--bind=127.0.0.1:19187", "--chain-db=chain.db3", "--peers-db=peers.db3",
            "--config="+str(config.resolve()),
            "--connect=45.79.140.195:9186,193.70.81.165:9186,139.99.16.105:9186"], chain)

    @serialized
    def probe(self):
        from .rpc import NodeRPC
        self.ready = False
        from . import settings
        if settings.load(self.directory)['connection_mode']=='lite':
            from .connection import client
            try:
                remote=client(self.directory)
                remote.require_network()
                head=remote.head(require_synced=True)
                self.ready=True;self.checked=time.monotonic()
                self.state={'state':'Connected','height':head['height'],'peers':0,'head':head,'checked':self.checked,'mode':'lite'}
            except ValueError as error:
                self.state={'state':'Service unavailable','height':0,'peers':0,'mode':'lite','error':str(error)}
            return self.state
        if not self.node.running:
            process=getattr(self.node,'process',None)
            if process is not None and process.poll() not in (None,0) and not self.node.stopping:
                self.state={'state':'Failed','height':0,'peers':0,
                    'error':f'The node stopped unexpectedly (exit code {process.poll()}). Check Node log, then restart or resync.'}
                return self.state
            self.state = {"state": "Stopped", "height": 0, "peers": 0, 'error': self.stop_reason}
            return self.state
        if storage_info(self.directory)['free'] < 512*1024*1024:
            self.stop_node()
            self.stop_reason = 'The node stopped because blockchain storage has less than 512 MiB free. Free space, then start the node again.'
            self.state = {'state': 'Low disk space', 'height': 0, 'peers': 0, 'error': self.stop_reason}
            return self.state
        if not self.rpc_available:
            self.state = {'state':'Starting / loading chain','height':0,'peers':0}
            return self.state
        try:
            client = NodeRPC()
            head = client.head()
            peers = client.connected_peers()
            eligible = head["synced"] and head["height"] > 0 and bool(peers)
            if eligible:
                template = client.template()
                header = template.get('header','')
                eligible = (template.get('synced') is True and template.get('height') == head['height']+1
                    and isinstance(header,str) and len(header)==160 and header[:64].lower()==head['hash'].lower())
                # Recheck the head after template retrieval to tolerate a head/template race.
                newer = client.head()
                eligible = eligible and newer["hash"] == head["hash"] and newer["synced"]
            self.ready = bool(eligible)
            self.checked = time.monotonic()
            self.state = {"state": "Ready" if self.ready else "Synchronizing", "height": head["height"],
                "peers": len(peers), "head": head, "checked": self.checked}
        except ValueError as exc:
            self.state = {**self.state, "state": "Starting / unavailable", "error": str(exc)}
        return self.state

    @property
    def rpc_available(self):
        from . import settings
        if settings.load(self.directory)['connection_mode']=='lite':return True
        return self.node.running and time.monotonic()>=self.node.rpc_ready_at

    def detect_devices(self):
        result = subprocess.run([str(verified_component('miner')), '--devices'], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=25, **hidden_options())
        if result.returncode:
            raise ValueError('Could not detect mining hardware. Check your graphics driver and try again.')
        return json.loads(result.stdout)

    @serialized
    def start_miner(self, payout, threads, backend, device=0, *, cpu_limit=50, gpu_limit=50):
        if self.closed:
            raise ValueError('Application is shutting down')
        from . import settings
        if settings.load(self.directory)['connection_mode']=='lite':raise ValueError('Solo mining requires the managed local node')
        from .mining import validate,worker_threads
        validate(cpu_limit,gpu_limit)
        from .compat import wallet_utils
        payout = wallet_utils.validate_address(payout)
        self.probe()
        if not self.ready or time.monotonic()-self.checked > 10:
            raise ValueError("Wait for the mainnet node to sync and connect before starting mining.")
        if type(threads) is not int or not 1 <= threads <= (os.cpu_count() or 1):
            raise ValueError("Invalid CPU thread count")
        threads=min(threads,worker_threads(cpu_limit))
        if backend != 'opencl':
            raise ValueError("Invalid mining backend")
        if type(device) is not int or not 0 <= device <= 63:
            raise ValueError('Invalid GPU device index')
        hardware = self.detect_devices()
        available = hardware.get('opencl',[])
        if not available or device >= len(available):
            raise ValueError('Mining requires a supported GPU and CPU.')
        self.miner.metrics = {}
        self.miner.start([verified_component("miner"), "--payout", payout, "--threads", str(threads),
            "--backend", backend, "--device", str(device), "--port", str(self.STRATUM_PORT),
            "--gpu-limit",str(gpu_limit),
            "--profile-dir", f"mining-profiles/{backend}-{device}"], self.directory,cpu_limit=cpu_limit)

    @serialized
    def stop_node(self):
        self.ready = False
        self.miner.stop()
        result = self.node.stop()
        if not result['graceful']:
            self.stop_reason = 'The node did not shut down cleanly. Review its logs before restarting.'
        return result

    @serialized
    def shutdown(self):
        self.closed = True
        return self.stop_node()

    @serialized
    def archive_chain(self):
        from . import settings
        if settings.load(self.directory)['connection_mode']=='lite':raise ValueError('Choose Full node to manage the local blockchain')
        self.stop_node()
        archive = self.directory / "backups" / time.strftime("chain-%Y%m%d-%H%M%S")
        archive.mkdir()
        receipt = archive / "recovery.json"
        from .paths import atomic_json
        names = ["chain.db3", "chain.db3-wal", "chain.db3-shm"]
        atomic_json(receipt, {"pending": names})
        for name in names:
            source = chain_directory(self.directory) / name
            if source.exists():
                shutil.move(str(source), str(archive / name))
            atomic_json(receipt, {"pending": names[names.index(name)+1:]})
        return str(archive)

    @serialized
    def resume_archives(self):
        from .paths import atomic_json
        allowed={'chain.db3','chain.db3-wal','chain.db3-shm'}
        for receipt in (self.directory/'backups').glob('chain-*/recovery.json'):
            pending=json.loads(receipt.read_text(encoding='utf-8')).get('pending')
            if not isinstance(pending,list) or any(name not in allowed for name in pending):
                raise ValueError('The blockchain backup record is invalid. Check the backup files.')
            for index,name in enumerate(pending):
                source=chain_directory(self.directory)/name
                destination=receipt.parent/name
                if source.exists() and destination.exists():
                    raise ValueError('The blockchain backup contains conflicting files. Both copies have been kept.')
                if source.exists():
                    shutil.move(str(source),str(destination))
                atomic_json(receipt,{'pending':pending[index+1:]})

    @serialized
    def change_storage(self, destination):
        if self.node.running or self.miner.running:
            raise ValueError('Stop the node and mining before changing blockchain storage')
        self.resume_archives()
        return move_chain(self.directory, destination)

    @serialized
    def initialize_node(self,destination,source=None,cancel=None,progress=lambda *_:None):
        if self.closed:raise ValueError('Application is shutting down')
        if self.node.running or self.miner.running:raise ValueError('Stop the node and mining before initialization')
        from . import settings
        if settings.load(self.directory)['connection_mode']=='lite':raise ValueError('Choose Full node before initialization')
        from .chain_import import initialize
        return initialize(self.directory,destination,source,cancel,progress)

    @serialized
    def set_mode(self,mode):
        from . import settings
        if mode not in ('local','lite'):raise ValueError('Invalid connection mode')
        result=self.stop_node()
        if self.node.running or self.miner.running:raise ValueError('Owned processes have not stopped')
        value=settings.load(self.directory)
        self.ready=False;self.state={'state':'Stopped','height':0,'peers':0}
        return settings.save(self.directory,connection_mode=mode,connection_revision=value['connection_revision']+1)


class SignerClient:
    def __init__(self, directory):
        self.process = subprocess.Popen(application_command("--signer", str(directory)), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **hidden_options())
        self.job = guard_process(self.process)
        self.lock = threading.Lock()
        self.responses = queue.Queue(maxsize=2)
        self.counter = 0

        def read():
            for line in iter(self.process.stdout.readline, b""):
                try:
                    self.responses.put(json.loads(line), timeout=1)
                except (ValueError, queue.Full):
                    self.process.kill()
                    break
        threading.Thread(target=read, daemon=True).start()

    def call(self, action, **args):
        with self.lock:
            self.counter += 1
            request = {"id": self.counter, "action": action, "args": args}
            self.process.stdin.write((json.dumps(request) + "\n").encode())
            self.process.stdin.flush()
            try:
                response = self.responses.get(timeout=40)
            except queue.Empty as exc:
                self.process.kill()
                raise ValueError("Wallet operation timed out. Restart the application; any saved payment remains reserved.") from exc
            if response.get("id") != self.counter:
                self.process.kill()
                raise ValueError("Wallet protocol mismatch")
            if "error" in response:
                raise ValueError(response["error"])
            return response["result"]

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
