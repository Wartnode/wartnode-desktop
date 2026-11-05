"""Mining configuration and real Windows job CPU ceilings."""
import ctypes
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch,Mock
from wartnode import mining,settings
from wartnode.paths import data_root,atomic_json
from wartnode.processes import Runtime
from wartnode.process_guard import guard_process

class MiningPolicyTests(unittest.TestCase):
    def test_existing_profiles_default_to_normal_without_reusing_old_thread_count(self):
        with tempfile.TemporaryDirectory() as directory:
            atomic_json(Path(directory)/'settings.json',{'schema':1,'cpu_threads':1})
            value=settings.load(directory)
            self.assertEqual((value['mining_mode'],value['cpu_limit'],value['gpu_limit']),('normal',50,50))
            self.assertEqual(mining.worker_threads(50,16),8)
            self.assertEqual(mining.worker_threads(5,4),1)
            self.assertEqual(mining.worker_threads(95,16),16)
    def test_limits_are_integer_percentages_and_presets_cannot_lie(self):
        for cpu,gpu in ((5,95),(95,5),(20,20),(50,50),(80,80)):
            mining.validate(cpu,gpu)
        for value in (0,4,96,100,True,50.5,'50',None):
            with self.subTest(value=value),self.assertRaises(ValueError):mining.validate(value,50)
            with self.assertRaises(ValueError):mining.validate(50,value)
        with self.assertRaises(ValueError):mining.validate(50,50,'heavy')
    def test_invalid_saved_limits_do_not_replace_valid_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            settings.save(directory,mining_mode='custom',cpu_limit=5,gpu_limit=95)
            with self.assertRaises(ValueError):settings.save(directory,cpu_limit=100)
            value=settings.load(directory);self.assertEqual((value['cpu_limit'],value['gpu_limit']),(5,95))
            atomic_json(Path(directory)/'settings.json',{'schema':1,'cpu_limit':100})
            with self.assertRaises(settings.SettingsError):settings.load(directory)
    def test_runtime_passes_both_limits_to_the_actual_control_boundaries(self):
        from test_payment_transport import OWNER
        with tempfile.TemporaryDirectory() as directory:
            runtime=Runtime(data_root(directory));runtime.ready=True;runtime.checked=time.monotonic()
            with patch.object(runtime,'probe'),patch.object(runtime,'detect_devices',return_value={'opencl':['GPU']}),patch.object(runtime.miner,'start') as start:
                runtime.start_miner(OWNER,os.cpu_count() or 1,'opencl',cpu_limit=20,gpu_limit=80)
            args,kwargs=start.call_args
            command=list(map(str,args[0]))
            self.assertEqual(command[command.index('--gpu-limit')+1],'80')
            self.assertEqual(command[command.index('--threads')+1],str(mining.worker_threads(20)))
            self.assertEqual(kwargs['cpu_limit'],20)
    def test_windows_refusal_never_leaves_an_uncapped_miner_running(self):
        if os.name!='nt':self.skipTest('Windows job API')
        process=Mock();kernel=Mock();kernel.CreateJobObjectW.return_value=123
        kernel.SetInformationJobObject.return_value=0
        with patch('ctypes.WinDLL',return_value=kernel):
            with self.assertRaisesRegex(ValueError,'CPU limit'):guard_process(process,cpu_limit=20)
        process.kill.assert_called_once();kernel.CloseHandle.assert_called_once_with(123)
        kernel.AssignProcessToJobObject.assert_not_called()

    def test_invalid_runtime_limits_are_rejected_before_work_or_hardware_queries(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=Runtime(data_root(directory))
            with patch.object(runtime,'probe') as probe,patch.object(runtime.miner,'start') as start:
                for value in (4,96,True):
                    with self.assertRaises(ValueError):runtime.start_miner('',1,'opencl',cpu_limit=value)
            probe.assert_not_called();start.assert_not_called()

if __name__=='__main__':unittest.main()
