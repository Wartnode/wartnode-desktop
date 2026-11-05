"""Cold import safety, recovery and explicit process initialization."""
from contextlib import closing
from pathlib import Path
import json
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from wartnode import chain_import,settings,updates
from wartnode.node_history import MAINNET_BLOCK_ONE
from wartnode.paths import data_root,atomic_json
from wartnode.processes import Runtime
from wartnode.rpc import GENESIS


def chain(path,anchor=MAINNET_BLOCK_ONE):
    with closing(sqlite3.connect(path)) as db:
        db.executescript('CREATE TABLE Blocks(hash BLOB,header BLOB); CREATE TABLE Consensus(height INTEGER,block_id INTEGER); CREATE TABLE History(id INTEGER,hash BLOB,data BLOB); CREATE TABLE State(id INTEGER); CREATE TABLE AccountHistory(id INTEGER);')
        db.execute('INSERT INTO Blocks VALUES(?,?)',(bytes.fromhex(anchor),bytes.fromhex(GENESIS)+bytes(48)))
        db.execute('INSERT INTO Consensus VALUES(1,1)');db.commit()


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.profile=data_root(self.base/'wallet');self.target=self.base/'链 Blockchain'
        (self.profile/'wallet.db').write_bytes(b'wallet and uncertain payment sentinel')
        self.source=self.base/'source.db3';chain(self.source)
        self.source_hash=updates.sha(self.source);self.wallet_hash=updates.sha(self.profile/'wallet.db')
    def tearDown(self):
        self.assertEqual(updates.sha(self.source),self.source_hash)
        self.assertEqual(updates.sha(self.profile/'wallet.db'),self.wallet_hash)
        self.temp.cleanup()
    def test_import_activates_an_identical_copy_and_preserves_source_and_wallet(self):
        result=chain_import.initialize(self.profile,self.target,self.source)
        self.assertEqual(result['imported']['height'],1)
        self.assertEqual(updates.sha(self.target/'chain.db3'),self.source_hash)
        self.assertTrue(settings.load(self.profile)['setup_complete'])
        self.assertFalse((self.profile/'chain-import.json').exists())

    def test_wal_format_standalone_backup_imports_without_staged_sidecars(self):
        with closing(sqlite3.connect(self.source)) as db:
            self.assertEqual(db.execute('PRAGMA journal_mode=WAL').fetchone()[0],'wal')
        self.source_hash=updates.sha(self.source)
        self.assertFalse(Path(str(self.source)+'-wal').exists())
        result=chain_import.initialize(self.profile,self.target,self.source)
        self.assertEqual(result['imported']['height'],1)
        self.assertEqual(updates.sha(self.target/'chain.db3'),self.source_hash)
        self.assertEqual({p.name for p in self.target.iterdir()},{'chain.db3'})
    def test_fresh_initialization_is_explicit_and_never_starts_a_process(self):
        runtime=Runtime(self.profile)
        with patch.object(runtime.node,'start') as start:
            with self.assertRaisesRegex(ValueError,'Initialize'):runtime.start_node()
            runtime.initialize_node(self.target)
            start.assert_not_called()
        self.assertEqual(list(self.target.iterdir()),[])
    def test_rejects_wrong_chain_busy_source_wallet_and_nonempty_destination(self):
        wrong=self.base/'wrong.db3';chain(wrong,anchor='00'*32)
        with self.assertRaisesRegex(ValueError,'anchor'):chain_import.initialize(self.profile,self.target,wrong)
        journal=Path(str(self.source)+'-wal');journal.write_bytes(b'pending')
        with self.assertRaisesRegex(ValueError,'journal'):chain_import.initialize(self.profile,self.target,self.source)
        journal.unlink()
        with self.assertRaisesRegex(ValueError,'wallet'):chain_import.initialize(self.profile,self.target,self.profile/'wallet.db')
        marker=self.target/'existing.txt';marker.write_text('keep')
        with self.assertRaisesRegex(ValueError,'empty'):chain_import.initialize(self.profile,self.target,self.source)
        self.assertEqual(marker.read_text(),'keep')
        self.assertFalse(settings.load(self.profile)['setup_complete'])
    def test_corruption_and_invalid_schema_are_rejected(self):
        invalid=self.base/'invalid.db3';invalid.write_bytes(b'SQLite format 3\0'+bytes(512))
        with self.assertRaises(ValueError):chain_import.inspect_chain(invalid)
        invalid.unlink()
        with closing(sqlite3.connect(invalid)) as db:db.execute('CREATE TABLE wallets(secret BLOB)');db.commit()
        with self.assertRaisesRegex(ValueError,'supported'):chain_import.inspect_chain(invalid)
    def test_cancellation_and_low_space_leave_no_active_or_staged_database(self):
        cancel=threading.Event()
        def progress(copied,total,phase):
            if phase=='Copying chain':cancel.set()
        with self.assertRaises(chain_import.ImportCancelled):chain_import.initialize(self.profile,self.target,self.source,cancel,progress)
        self.assertEqual(list(self.target.iterdir()),[])
        self.assertFalse(settings.load(self.profile)['setup_complete'])
        with patch('wartnode.chain_import.shutil.disk_usage',return_value=SimpleNamespace(free=1)):
            with self.assertRaisesRegex(ValueError,'space'):chain_import.initialize(self.profile,self.target,self.source)
    def test_prepared_import_recovers_after_power_loss_without_recopied_source(self):
        def interrupt(phase):
            if phase=='prepared':raise OSError('power loss')
        with self.assertRaises(OSError):chain_import.initialize(self.profile,self.target,self.source,checkpoint=interrupt)
        self.assertFalse(settings.load(self.profile)['setup_complete'])
        Runtime(self.profile)
        self.assertEqual(updates.sha(self.target/'chain.db3'),self.source_hash)
        self.assertTrue(settings.load(self.profile)['setup_complete'])

    def test_source_becoming_live_during_copy_is_rejected_without_activation(self):
        journal=Path(str(self.source)+'-wal')
        def progress(copied,total,phase):
            if phase=='Copying chain':journal.write_bytes(b'new pending work')
        try:
            with self.assertRaisesRegex(ValueError,'journal'):chain_import.initialize(self.profile,self.target,self.source,progress=progress)
            self.assertEqual(list(self.target.iterdir()),[])
            self.assertFalse(settings.load(self.profile)['setup_complete'])
        finally:journal.unlink(missing_ok=True)

    def test_interrupted_copy_removes_only_its_owned_stage_on_restart(self):
        self.target.mkdir();stage=self.target/('.wartnode-import-'+'a'*32+'.db3');stage.write_bytes(b'partial')
        keep=self.target/'keep.txt';keep.write_text('unrelated')
        atomic_json(self.profile/'chain-import.json',{'phase':'copying','destination':str(self.target),'stage':stage.name})
        chain_import.resume(self.profile)
        self.assertFalse(stage.exists());self.assertEqual(keep.read_text(),'unrelated')
        self.assertFalse(settings.load(self.profile)['setup_complete'])
    def test_conflicting_recovery_never_overwrites_a_destination(self):
        def interrupt(phase):raise OSError('power loss')
        with self.assertRaises(OSError):chain_import.initialize(self.profile,self.target,self.source,checkpoint=interrupt)
        (self.target/'chain.db3').write_bytes(b'other chain')
        with self.assertRaisesRegex(ValueError,'changed'):chain_import.resume(self.profile)
        self.assertEqual((self.target/'chain.db3').read_bytes(),b'other chain')
        self.assertTrue((self.profile/'chain-import.json').exists())
    def test_malformed_recovery_fails_closed_and_keeps_unrelated_files(self):
        for record in ([],{'stage':'../wallet.db','phase':'copying'}):
            atomic_json(self.profile/'chain-import.json',record)
            runtime=Runtime(self.profile)
            self.assertTrue(runtime.recovery_error)
            with self.assertRaises(ValueError):runtime.initialize_node(self.target)

    def test_cli_refuses_a_profile_that_is_already_open(self):
        from PySide6.QtCore import QLockFile
        import subprocess,sys
        lock=QLockFile(str(self.profile/'desktop.lock'));self.assertTrue(lock.tryLock(0))
        try:
            result=subprocess.run([sys.executable,'-m','wartnode','--data-dir',str(self.profile),'--initialize-node',str(self.target)],capture_output=True,text=True,timeout=20)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('Close Wartnode-Desktop',result.stderr)
            self.assertFalse(settings.load(self.profile)['setup_complete'])
        finally:lock.unlock()
    def test_lite_disables_all_local_node_and_mining_actions(self):
        runtime=Runtime(self.profile);runtime.set_mode('lite')
        for action in (runtime.start_node,lambda:runtime.initialize_node(self.target),runtime.archive_chain,lambda:runtime.start_miner('',1,'opencl')):
            with self.assertRaises(ValueError):action()
        self.assertFalse(runtime.node.running);self.assertFalse(runtime.miner.running)
        self.assertEqual(settings.load(self.profile)['connection_revision'],1)
        runtime.set_mode('local')
        self.assertFalse(runtime.node.running)
        self.assertEqual(settings.load(self.profile)['connection_revision'],2)

if __name__=='__main__':unittest.main()
