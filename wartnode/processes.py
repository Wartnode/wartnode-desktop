"""Owned process lifecycle, private signer transport and Windows graceful stop."""
import collections
import ctypes
from datetime import datetime, timezone
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
from .fees import NODE_MINIMUM_FEE
from .dashboard import MinerSample


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
        self.sample = None
        self.stopping = False
        self.job = None
        self.rpc_ready_at = float('inf')
        self.started_at = None
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
        self.sample = None
        self.rpc_ready_at = float('inf')
        environment = {k: v for k, v in os.environ.items() if not k.startswith(("BRMINER_", "WART_", "CONNECTCOIN_"))}
        environment["LOCALAPPDATA"] = str(self.directory / "runtime")
        self.process = subprocess.Popen([str(a) for a in arguments], cwd=cwd, env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **hidden_options(console=True))
        if self.job:
            ctypes.WinDLL('kernel32').CloseHandle(ctypes.c_void_p(self.job))
            self.job=None
        self.job = guard_process(self.process,cpu_limit=cpu_limit)
        self.started_at = time.monotonic()
        process = self.process
        self.sample=None;self.metrics={};self.lines.clear()

        def read():
            with log.open("a", encoding="utf-8") as stream:
                for raw in iter(process.stdout.readline, b""):
                    if self.process is not process:break
                    line = raw.decode("utf-8", "replace").rstrip()[:4096]
                    self.lines.append(line)
                    if line.startswith('WARTNODE_METRICS '):
                        try:
                            sample=MinerSample.from_wire(line[len('WARTNODE_METRICS '):])
                            if self.process is process:self.sample=sample
                        except ValueError:
                            pass
                    if 'RPC endpoint is ' in line:self.rpc_ready_at = time.monotonic()+.5
                    stream.write(line + "\n")
                    stream.flush()
                    if stream.tell() > 5 * 1024 * 1024:
                        stream.seek(0)
                        stream.truncate()
                    if "BRMINER_METRICS " in line:
                        try:
                            values = line.split("BRMINER_METRICS ", 1)[1].split()
                            metrics = {key:float(value) for key,value in (token.split("=",1) for token in values if "=" in token)}
                            if self.process is process:self.metrics=metrics
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


class MiningNotReady(ValueError):
    """Fresh readiness validation failed; a waiting session may retry later."""


class Runtime:
    RPC_PORT, STRATUM_PORT, P2P_PORT = 19400, 19456, 19187
    MINING_SYNC_GRACE = 5.0
    MINING_READY_CHECKS = 2

    def __init__(self, directory):
        self.directory = Path(directory)
        self.node = OwnedProcess("node", directory)
        self.miner = OwnedProcess("miner", directory)
        self.ready = False
        self.checked = 0
        self.lite_connected_at = None
        self.state = {"state": "Stopped", "height": 0, "peers": 0}
        self.operation_lock = threading.RLock()
        self.closed = False
        self.stop_reason = ''
        self.recovery_error=''
        # Intent lives only for this application session. UI cancellation does
        # not wait for an in-flight RPC or process operation to finish.
        self._mining_lock = threading.RLock()
        self._mining_generation = 0
        self._mining_request = None
        self._mining_waiting = False
        self._mining_unready_since = None
        self._mining_ready_checks = 0
        self._mining_editing = False
        self.mining_error = ''
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
            "--minfee="+NODE_MINIMUM_FEE,
            "--config="+str(config.resolve()),
            "--connect=45.79.140.195:9186,193.70.81.165:9186,139.99.16.105:9186"], chain)

    @serialized
    def probe(self):
        try:
            return self._probe()
        except Exception:
            self._publish_state({'state':'Starting / unavailable','height':0,'peers':0,
                'reason':'readiness_check_failed','recoverable':False,'error':'The node readiness check failed.'})
            raise

    def _probe(self):
        from .rpc import NodeRPC, NodeUnavailable
        from . import settings
        if settings.load(self.directory)['connection_mode']=='lite':
            from .connection import client
            try:
                remote=client(self.directory)
                remote.require_network()
                head=remote.head(require_synced=True)
                if self.lite_connected_at is None:self.lite_connected_at=time.monotonic()
                return self._publish_state({'state':'Connected','height':head['height'],'peers':0,'head':head,'mode':'lite'},ready=True)
            except ValueError as error:
                self.lite_connected_at=None
                return self._publish_state({'state':'Service unavailable','height':0,'peers':0,'mode':'lite','error':str(error)})
        if not self.node.running:
            process=getattr(self.node,'process',None)
            if process is not None and process.poll() not in (None,0) and not self.node.stopping:
                return self._publish_state({'state':'Failed','height':0,'peers':0,'reason':'node_failed',
                    'error':f'The node stopped unexpectedly (exit code {process.poll()}). Check Node log, then restart or resync.'})
            return self._publish_state({"state": "Stopped", "height": 0, "peers": 0, 'reason':'node_stopped','error': self.stop_reason})
        if storage_info(self.directory)['free'] < 512*1024*1024:
            self.stop_node()
            self.stop_reason = 'The node stopped because blockchain storage has less than 512 MiB free. Free space, then start the node again.'
            return self._publish_state({'state': 'Low disk space', 'height': 0, 'peers': 0, 'reason':'low_disk','error': self.stop_reason})
        if not self.rpc_available:
            return self._publish_state({'state':'Starting / loading chain','height':0,'peers':0,'reason':'rpc_starting','recoverable':True})
        try:
            client = NodeRPC()
            # All attempts share the client's eight-second deadline. A tip
            # update between RPCs is inconclusive; retry a fresh snapshot.
            for attempt in range(3):
                head = client.head()
                peers = client.connected_peers()
                reason = 'syncing' if not head['synced'] or not head['height'] else 'no_peers' if not peers else 'ready'
                if reason == 'no_peers':break
                if reason == 'syncing':
                    if attempt < 2:
                        self._lifecycle_event('node_probe_retry',reason=reason,attempt=attempt+1,height=head['height'])
                        time.sleep(.15)
                        continue
                    break
                template = client.template()
                header = template.get('header','')
                if (not isinstance(header,str) or len(header)!=160
                        or any(c not in '0123456789abcdefABCDEF' for c in header)
                        or type(template.get('height')) is not int or type(template.get('synced')) is not bool):
                    reason='invalid_template'
                    break
                newer = client.head()
                if newer['hash'] != head['hash']:
                    reason='head_changed'
                elif not template['synced'] or not newer['synced']:
                    reason='syncing'
                elif template['height'] != head['height']+1 or header[:64].lower()!=head['hash'].lower():
                    reason='template_changed'
                else:
                    reason='ready'
                    break
                if attempt < 2:
                    self._lifecycle_event('node_probe_retry',reason=reason,attempt=attempt+1,height=head['height'])
                    time.sleep(.15)
            state={"state": "Ready" if reason=='ready' else "Synchronizing", "height": head["height"],
                "peers": len(peers), "head": head, 'reason':reason,'recoverable':reason!='invalid_template'}
            if reason=='invalid_template':
                state.update(state='Starting / unavailable',error='The node returned an invalid mining template.')
            return self._publish_state(state,ready=reason=='ready')
        except NodeUnavailable as exc:
            return self._publish_state({**self.state,'state':'Starting / unavailable','reason':'rpc_unavailable','recoverable':True,'error':str(exc)})
        except ValueError as exc:
            return self._publish_state({**self.state, "state": "Starting / unavailable", 'reason':'invalid_response','recoverable':False,"error": str(exc)})

    def _publish_state(self,state,*,ready=False):
        signature=(state['state'],state.get('reason'))
        if signature != (self.state['state'],self.state.get('reason')):
            self._lifecycle_event('node_readiness',state=state['state'],reason=state.get('reason',''),height=state.get('height',0))
        self.checked=time.monotonic()
        self.state={**state,'checked':self.checked}
        self.ready=ready
        return self.state

    def _lifecycle_event(self,event,**fields):
        """Bounded, timestamped control decisions without addresses or secrets."""
        try:
            path=self.directory/'logs'/'lifecycle.log'
            path.parent.mkdir(parents=True,exist_ok=True)
            if path.exists() and path.stat().st_size>5*1024*1024:
                os.replace(path,path.with_suffix('.previous.log'))
            with path.open('a',encoding='utf-8') as stream:
                stream.write(json.dumps({'time':datetime.now(timezone.utc).isoformat(),'event':event,**fields})+'\n')
        except OSError:
            pass  # A logging failure must never prevent stopping owned work.

    @property
    def mining_generation(self):
        with self._mining_lock:return self._mining_generation

    @property
    def mining_requested(self):
        with self._mining_lock:return self._mining_request is not None

    @property
    def mining_waiting(self):
        with self._mining_lock:return self._mining_waiting

    def set_mining_editing(self,value):
        with self._mining_lock:self._mining_editing=bool(value)

    def cancel_mining(self):
        with self._mining_lock:
            self._mining_generation+=1
            self._mining_request=None
            self._mining_waiting=False
            self._mining_unready_since=None
            self._mining_ready_checks=0

    @serialized
    def stop_miner(self,reason='user_stop',*,cancel=True):
        if cancel:self.cancel_mining()
        self._lifecycle_event('mining_stop',reason=reason)
        return self.miner.stop()

    @serialized
    def poll(self):
        try:
            self.probe()
            self._maintain_mining()
        except Exception:
            self.mining_error='Mining stopped because node readiness could not be checked. Check the node, then start mining again.'
            self.stop_miner('readiness_check_failed')
            raise
        return self.state

    def _maintain_mining(self):
        with self._mining_lock:
            request=self._mining_request
            generation=self._mining_generation
            waiting=self._mining_waiting
        if request is None:return
        if self.closed or not self.node.running or not self.state.get('recoverable',False):
            self.mining_error='Mining stopped: '+self.state.get('error','the node is unavailable or returned invalid work.')
            self.stop_miner(self.state.get('reason','node_unavailable'))
            return
        if not self.miner.running and not waiting:
            self.mining_error='The miner exited unexpectedly. Check the mining console, then start mining again.'
            self.stop_miner('miner_exited')
            return
        with self._mining_lock:
            if generation!=self._mining_generation:return
            if self.ready:
                if not waiting:
                    if self._mining_unready_since is not None:
                        self._lifecycle_event('mining_continued',reason='node_ready_within_grace')
                    self._mining_unready_since=None
                    return
                if self._mining_editing:
                    self._mining_ready_checks=0
                    return
                self._mining_ready_checks+=1
                if self._mining_ready_checks<self.MINING_READY_CHECKS:return
            else:
                self._mining_ready_checks=0
                if waiting:return
                if self._mining_unready_since is None:
                    self._mining_unready_since=time.monotonic()
                    self._lifecycle_event('mining_grace',reason=self.state.get('reason'),seconds=self.MINING_SYNC_GRACE)
                if time.monotonic()-self._mining_unready_since<self.MINING_SYNC_GRACE:return
        if not self.ready:
            self.miner.stop()
            with self._mining_lock:
                if generation==self._mining_generation:
                    self._mining_waiting=True
                    self._lifecycle_event('mining_waiting',reason=self.state.get('reason'))
            return
        payout,threads,backend,device,cpu,gpu=request
        try:
            self.start_miner(payout,threads,backend,device,cpu_limit=cpu,gpu_limit=gpu,generation=generation,automatic=True)
        except MiningNotReady:
            if generation!=self.mining_generation:return
            with self._mining_lock:self._mining_ready_checks=0
            if not self.state.get('recoverable',False):
                self.mining_error='Mining recovery stopped: the node returned an invalid response.'
                self.stop_miner('invalid_recovery_response')
        except (ValueError,OSError,subprocess.SubprocessError) as error:
            # No restart loop for GPU/configuration/process failures.
            if generation!=self.mining_generation:return
            self.mining_error='Mining could not resume. Check the mining console, then start mining again.'
            self._lifecycle_event('mining_resume_failed',error_type=type(error).__name__)
            self.stop_miner('restart_failed')

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
    def start_miner(self, payout, threads, backend, device=0, *, cpu_limit=60, gpu_limit=60,generation=None,automatic=False):
        if self.closed:
            raise ValueError('Application is shutting down')
        if generation is None:generation=self.mining_generation
        if generation!=self.mining_generation:raise ValueError('Mining start was cancelled')
        from . import settings
        if settings.load(self.directory)['connection_mode']=='lite':raise ValueError('Solo mining requires the managed local node')
        from .mining import validate,worker_threads
        validate(cpu_limit,gpu_limit)
        from .compat import wallet_utils
        payout = wallet_utils.validate_address(payout)
        self.probe()
        if not self.ready or time.monotonic()-self.checked > 10:
            raise MiningNotReady("Wait for the mainnet node to sync and connect before starting mining.")
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
        if time.monotonic()-self.checked>10:
            self.probe()
            if not self.ready:raise MiningNotReady('Wait for the mainnet node to sync and connect before starting mining.')
        with self._mining_lock:
            if generation!=self._mining_generation:raise ValueError('Mining start was cancelled')
            if automatic and self._mining_editing:raise MiningNotReady('Finish reviewing mining settings before resuming.')
            self.miner.metrics = {}
            self.miner.start([verified_component("miner"), "--payout", payout, "--threads", str(threads),
                "--backend", backend, "--device", str(device), "--port", str(self.STRATUM_PORT),
                "--gpu-limit",str(gpu_limit),
                "--profile-dir", f"mining-profiles/{backend}-{device}"], self.directory,cpu_limit=cpu_limit)
            self._mining_request=(payout,threads,backend,device,cpu_limit,gpu_limit)
            self._mining_waiting=False
            self._mining_unready_since=None
            self._mining_ready_checks=0
            self.mining_error=''
        self._lifecycle_event('mining_resumed' if automatic else 'mining_started',cpu_limit=cpu_limit,gpu_limit=gpu_limit,device=device)

    @serialized
    def stop_node(self):
        self.ready = False
        self.stop_miner('node_stop')
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
        self.lite_connected_at=None
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
            try:
                for line in iter(self.process.stdout.readline, b""):
                    try:
                        self.responses.put(json.loads(line), timeout=1)
                    except (ValueError, queue.Full):
                        self.process.kill()
                        break
            finally:
                try:self.responses.put_nowait({'closed':True})
                except queue.Full:pass
        self.reader=threading.Thread(target=read, daemon=True)
        self.reader.start()

    def call(self, action, _valid=None, **args):
        with self.lock:
            if _valid is not None and not _valid():return None
            result=self._exchange(action,args)
            if _valid is not None and not _valid():
                # Keep the transport lock until the helper has discarded the
                # stale authorization. A later unlock cannot overtake this lock.
                self._exchange('lock',{})
                return None
            return result

    def _exchange(self,action,args):
        """Caller holds self.lock across the complete request/response."""
        if self.process.poll() is not None:
            raise ValueError('The signing helper stopped. Restart Wartnode-Desktop; saved payments remain reserved.')
        self.counter += 1
        request = {"id": self.counter, "action": action, "args": args}
        self.process.stdin.write((json.dumps(request) + "\n").encode())
        self.process.stdin.flush()
        try:
            response = self.responses.get(timeout=40)
        except queue.Empty as exc:
            self.process.kill()
            raise ValueError("Wallet operation timed out. Restart the application; any saved payment remains reserved.") from exc
        if response.get('closed') is True:
            raise ValueError('The signing helper stopped. Restart Wartnode-Desktop; saved payments remain reserved.')
        if response.get("id") != self.counter:
            self.process.kill()
            raise ValueError("Wallet protocol mismatch")
        if "error" in response:
            raise ValueError(response["error"])
        return response["result"]

    def close(self):
        try:
            try:self.process.stdin.close()
            except OSError:pass  # A crashed helper can leave buffered pipe data.
            if self.process.poll() is None:
                try:self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill();self.process.wait(timeout=3)
        finally:
            reader=getattr(self,'reader',None)
            if reader:reader.join(timeout=2)
            for stream in (self.process.stdout,self.process.stderr):
                if stream:
                    try:stream.close()
                    except OSError:pass
            if self.job and os.name=='nt':
                ctypes.WinDLL('kernel32').CloseHandle(ctypes.c_void_p(self.job))
                self.job=None
