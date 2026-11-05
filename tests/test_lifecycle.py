"""Lifecycle failures must preserve data and must not leave owned work running."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
from wartnode.paths import data_root, atomic_json
from wartnode.processes import Runtime, OwnedProcess


class RecoveryTests(unittest.TestCase):
    def test_signal_success_does_not_hide_aborted_node(self):
        process = Mock(returncode=3)
        process.poll.return_value = None
        owned = OwnedProcess('node', '.')
        owned.process = process
        with patch('wartnode.processes.subprocess.run', return_value=Mock(returncode=0)):
            result = owned.stop()
        self.assertFalse(result['graceful'])
        self.assertEqual(result['exit_code'], 3)
        process.poll.return_value = 3
        self.assertFalse(owned.stop()['graceful'])

    def test_node_stop_failure_is_visible(self):
        runtime = Runtime('.')
        with patch.object(runtime.node, 'stop', return_value={'graceful':False, 'exit_code':3}):
            runtime.stop_node()
        self.assertIn('did not shut down cleanly', runtime.stop_reason)

    def test_interrupted_archive_resumes_without_touching_wallet(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=data_root(temporary)
            (directory/'wallet.db').write_bytes(b'preserved wallet')
            for name in ('chain.db3','chain.db3-wal','chain.db3-shm'):
                (directory/'node'/name).write_bytes(name.encode())
            runtime=Runtime(directory)
            import shutil
            move=shutil.move
            calls=[]
            def interrupted(source,destination):
                calls.append(source)
                if len(calls)==2:raise OSError('Controlled interruption')
                return move(source,destination)
            with patch('wartnode.processes.shutil.move',side_effect=interrupted):
                with self.assertRaises(OSError):runtime.archive_chain()
            Runtime(directory).resume_archives()
            archive=next((directory/'backups').glob('chain-*'))
            for name in ('chain.db3','chain.db3-wal','chain.db3-shm'):
                self.assertEqual((archive/name).read_bytes(),name.encode())
                self.assertFalse((directory/'node'/name).exists())
            self.assertEqual(json.loads((archive/'recovery.json').read_text())['pending'],[])
            self.assertEqual((directory/'wallet.db').read_bytes(),b'preserved wallet')

    def test_conflicting_archive_preserves_both_copies(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=data_root(temporary)
            archive=directory/'backups/chain-interrupted'
            archive.mkdir()
            atomic_json(archive/'recovery.json',{'pending':['chain.db3']})
            for parent,content in ((archive,b'old'),(directory/'node',b'new')):
                (parent/'chain.db3').write_bytes(content)
            with self.assertRaises(ValueError):Runtime(directory).resume_archives()
            self.assertEqual((archive/'chain.db3').read_bytes(),b'old')
            self.assertEqual((directory/'node/chain.db3').read_bytes(),b'new')

    def test_shutdown_rejects_queued_start_operations(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime=Runtime(data_root(temporary))
            runtime.shutdown()
            with self.assertRaisesRegex(ValueError,'shutting down'):runtime.start_node()
            with self.assertRaisesRegex(ValueError,'shutting down'):runtime.start_miner('',1,'auto')

    @unittest.skipUnless(os.name=='nt','Windows job ownership')
    def test_parent_crash_terminates_owned_child(self):
        import ctypes
        from ctypes import wintypes
        with tempfile.TemporaryDirectory() as temporary:
            code='''import sys,time
from wartnode.paths import data_root
from wartnode.processes import OwnedProcess
p=OwnedProcess('child',data_root(sys.argv[1]))
p.start([sys.executable,'-c','import time; time.sleep(120)'],sys.argv[1])
print(p.process.pid,flush=True)
time.sleep(120)
'''
            parent=subprocess.Popen([sys.executable,'-c',code,temporary],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
            kernel.OpenProcess.restype=wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
            kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            handle=None
            try:
                child=int(parent.stdout.readline())
                handle=kernel.OpenProcess(0x100000,False,child)
                self.assertTrue(handle)
                parent.kill()
                parent.wait(timeout=5)
                self.assertEqual(kernel.WaitForSingleObject(handle,5000),0)
            finally:
                if parent.poll() is None:parent.kill();parent.wait(timeout=5)
                if handle:kernel.CloseHandle(handle)


if __name__=='__main__':unittest.main()
