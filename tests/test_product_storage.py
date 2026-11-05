import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from wartnode.paths import data_root,atomic_json
from wartnode import settings
from wartnode.preflight import windows_compatibility
from wartnode.processes import Runtime


class ProductStorageTests(unittest.TestCase):
    def test_windows_preflight_rejects_old_arm_and_server(self):
        for build,arch,workstation in ((19045,'AMD64',True),(21999,'AMD64',True),
                                     (26100,'ARM64',True),(26100,'AMD64',False)):
            self.assertFalse(windows_compatibility(build,arch,workstation)['supported'])
        for build in (22000,22621,22631,26100,26200,26300,28000):
            with self.subTest(build=build):
                self.assertTrue(windows_compatibility(build,'AMD64',True)['supported'])

    def test_settings_replace_failure_preserves_previous_value_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=data_root(tmp)
            settings.save(root,setup_complete=True)
            with patch('wartnode.paths.os.replace',side_effect=OSError('disk full')):
                with self.assertRaises(OSError):settings.save(root,cpu_threads=2)
            self.assertTrue(settings.load(root)['setup_complete'])
            self.assertEqual(list(root.glob('settings.json.*.tmp')),[])

    def test_corrupt_settings_recovery_preserves_original_and_wallet_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=data_root(tmp)
            (root/'wallet.db').write_bytes(b'wallet and pending payment')
            settings.save(root,setup_complete=True)
            settings.save(root,cpu_threads=2)
            (root/'settings.json').write_bytes(b'corrupt settings')
            with self.assertRaises(settings.SettingsError):settings.load(root)
            saved=settings.recover_settings(root,True)
            self.assertTrue(settings.load(root)['setup_complete'])
            self.assertEqual(Path(saved).read_bytes(),b'corrupt settings')
            self.assertEqual((root/'wallet.db').read_bytes(),b'wallet and pending payment')

    def test_every_storage_commit_point_resumes_without_touching_wallet_or_payments(self):
        for point in ('journal','copied:chain.db3','moved:chain.db3','settings'):
            with self.subTest(point=point),tempfile.TemporaryDirectory() as tmp:
                root=data_root(Path(tmp)/'profile')
                wallet=root/'wallet.db'
                wallet.write_bytes(b'wallet and uncertain payment reservation')
                (root/'node/chain.db3').write_bytes(b'chain'*4096)
                target=Path(tmp)/'存储 space'
                def interrupt(phase):
                    if phase==point:raise OSError('power loss')
                with self.assertRaises(OSError):settings.move_chain(root,target,interrupt)
                settings.resume_move(root)
                self.assertEqual(settings.chain_directory(root),target.resolve())
                self.assertEqual((target/'chain.db3').read_bytes(),b'chain'*4096)
                self.assertFalse((root/'node/chain.db3').exists())
                self.assertEqual(wallet.read_bytes(),b'wallet and uncertain payment reservation')
                self.assertFalse((root/'storage-move.json').exists())

    def test_storage_conflict_preserves_both_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=data_root(Path(tmp)/'profile')
            old=root/'node/chain.db3';old.write_bytes(b'old')
            target=Path(tmp)/'chain'
            def interrupt(phase):
                if phase=='copied:chain.db3':raise OSError('power loss')
            with self.assertRaises(OSError):settings.move_chain(root,target,interrupt)
            (target/'chain.db3').write_bytes(b'new conflict')
            with self.assertRaisesRegex(ValueError,'conflict'):settings.resume_move(root)
            self.assertEqual(old.read_bytes(),b'old')
            self.assertEqual((target/'chain.db3').read_bytes(),b'new conflict')

    def test_storage_rejects_nonempty_and_wallet_folders_and_running_node(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            root=data_root(Path(tmp)/'profile')
            target=Path(tmp)/'occupied';target.mkdir();(target/'keep.txt').write_text('keep')
            for destination in (root,root/'wallet-folder',target):
                with self.assertRaises(ValueError):settings.move_chain(root,destination)
            runtime=Runtime(root);runtime.node=SimpleNamespace(running=True)
            with self.assertRaisesRegex(ValueError,'Stop the node'):runtime.change_storage(Path(tmp)/'new')
            self.assertEqual((target/'keep.txt').read_text(),'keep')

    def test_low_disk_move_fails_before_any_source_is_removed(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            root=data_root(Path(tmp)/'profile')
            (root/'node/chain.db3').write_bytes(b'chain')
            with patch('wartnode.settings.shutil.disk_usage',return_value=SimpleNamespace(free=1)):
                with self.assertRaisesRegex(ValueError,'space'):settings.move_chain(root,Path(tmp)/'new')
            self.assertEqual((root/'node/chain.db3').read_bytes(),b'chain')
            self.assertFalse((root/'storage-move.json').exists())

    def test_low_disk_stops_mining_and_node_and_blocks_readiness(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            runtime=Runtime(data_root(tmp))
            runtime.node=SimpleNamespace(running=True,rpc_ready_at=0)
            with patch('wartnode.processes.storage_info',return_value={'free':1}),patch.object(runtime,'stop_node') as stop:
                state=runtime.probe()
            stop.assert_called_once()
            self.assertFalse(runtime.ready)
            self.assertEqual(state['state'],'Low disk space')

    def test_failed_node_is_reported_without_claiming_readiness(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            runtime=Runtime(data_root(tmp))
            runtime.node.process=SimpleNamespace(poll=lambda:1)
            state=runtime.probe()
            self.assertEqual(state['state'],'Failed')
            self.assertIn('Node log',state['error'])
            self.assertFalse(runtime.ready)

    def test_contact_validation_and_replacement(self):
        from wartnode.compat import wallet_utils
        address,_=wallet_utils.wallet_from_pkhex('00'*31+'01')
        with tempfile.TemporaryDirectory() as tmp:
            root=data_root(tmp)
            with self.assertRaises(ValueError):settings.save_contact(root,'name','bad address')
            settings.save_contact(root,'First',address)
            settings.save_contact(root,'Renamed',address)
            self.assertEqual(settings.load(root)['contacts'],[{'name':'Renamed','address':address}])
