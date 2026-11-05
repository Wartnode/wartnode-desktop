"""Qualify the explicitly selected fee through the real managed node launcher."""
from pathlib import Path
import socket
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from wartnode.paths import data_root
from wartnode.processes import Runtime
from wartnode.rpc import NodeRPC


@unittest.skipUnless(sys.platform == 'win32', 'Windows native component qualification')
class NativeFeePolicy(unittest.TestCase):
    def test_production_launch_sets_the_selected_minimum_fee(self):
        root = Path(__file__).resolve().parents[1]
        temporary = root / 'work/test-tmp'
        temporary.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='node-fee-policy-', dir=temporary) as name:
            directory = Path(name).resolve()
            self.assertTrue(directory.is_relative_to((root / 'work').resolve()))
            runtime = Runtime(data_root(directory))
            holders = [socket.socket() for _ in range(3)]
            for holder in holders:
                holder.bind(('127.0.0.1', 0))
            runtime.RPC_PORT, runtime.STRATUM_PORT, runtime.P2P_PORT = [s.getsockname()[1] for s in holders]
            for holder in holders:
                holder.close()
            actual_start = runtime.node.start

            def start_disposable(arguments, cwd):
                # Change only fixture ports/connectivity. Keep fee arguments
                # exactly as produced by the real application launcher.
                self.assertEqual([str(arg) for arg in arguments if str(arg).startswith('--minfee')],
                                 ['--minfee=0.00009992'])
                ports = {'--rpc=': runtime.RPC_PORT, '--stratum=': runtime.STRATUM_PORT,
                         '--bind=': runtime.P2P_PORT}
                command = []
                for argument in arguments:
                    if str(argument).startswith('--connect='):
                        command.append('--isolated')
                    else:
                        key = next((key for key in ports if str(argument).startswith(key)), None)
                        command.append(key + '127.0.0.1:' + str(ports[key]) if key else argument)
                actual_start(command, cwd)

            try:
                with patch.object(runtime.node, 'start', side_effect=start_disposable):
                    runtime.initialize_node(runtime.directory/"node")
                    runtime.start_node()
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    self.assertTrue(runtime.node.running, '\n'.join(runtime.node.lines))
                    if not runtime.rpc_available:
                        time.sleep(.1)
                        continue
                    try:
                        fee = NodeRPC(f'http://127.0.0.1:{runtime.RPC_PORT}').minimum_fee()
                        break
                    except ValueError:
                        time.sleep(.1)
                else:
                    self.fail('Managed node did not expose its configured fee')
                self.assertEqual(fee, 9992)
            finally:
                result = runtime.stop_node()
                self.assertTrue(result['graceful'])
                if runtime.node.process is not None:
                    self.assertEqual(runtime.node.process.returncode, 0)
