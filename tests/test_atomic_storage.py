"""Windows sharing locks and concurrent application settings/journal writes."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from wartnode.paths import atomic_json

class AtomicStorageTests(unittest.TestCase):
    @unittest.skipUnless(os.name=='nt','Windows sharing behavior')
    def test_settings_commit_survives_a_real_reader_without_delete_sharing(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'progress.json';atomic_json(path,{'stage':'before'})
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
            kernel.CreateFileW.restype=wintypes.HANDLE
            kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            handle=kernel.CreateFileW(str(path),0x80000000,3,None,3,0x80,None)
            self.assertNotIn(handle,(None,ctypes.c_void_p(-1).value))
            errors=[];began=time.monotonic()
            def publish():
                try:atomic_json(path,{'stage':'committed','payload':'x'*10000})
                except Exception as error:errors.append(error)
            writer=threading.Thread(target=publish);writer.start()
            try:
                time.sleep(.15)
                self.assertTrue(writer.is_alive())
                self.assertEqual(json.loads(path.read_text()),{'stage':'before'})
            finally:kernel.CloseHandle(handle)
            writer.join(timeout=4)
            self.assertFalse(writer.is_alive());self.assertFalse(errors)
            self.assertGreaterEqual(time.monotonic()-began,.15)
            self.assertEqual(json.loads(path.read_text())['stage'],'committed')
            self.assertEqual([entry.name for entry in Path(directory).iterdir()],['progress.json'])

    def test_competing_writers_leave_one_complete_document_without_temp_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'progress.json';barrier=threading.Barrier(3);errors=[]
            def publish(tag):
                barrier.wait()
                try:
                    for sequence in range(10):atomic_json(path,{'tag':tag,'sequence':sequence,'payload':tag*10000})
                except Exception as error:errors.append(error)
            writers=[threading.Thread(target=publish,args=(tag,)) for tag in ('a','b')]
            for writer in writers:writer.start()
            barrier.wait()
            for writer in writers:writer.join(timeout=5)
            self.assertFalse(errors);self.assertFalse(any(writer.is_alive() for writer in writers))
            value=json.loads(path.read_text());self.assertEqual(value['sequence'],9)
            self.assertEqual(value['payload'],value['tag']*10000)
            self.assertEqual([entry.name for entry in Path(directory).iterdir()],['progress.json'])
