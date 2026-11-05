import base64
import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from wartnode import installed,updates
from wartnode.paths import atomic_json


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)/'installed';self.root.mkdir()
        self.key=Ed25519PrivateKey.generate()
        self.trust={'channel':'development','public_key':self.key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex(),
                    'manifest_url':'https://publisher.example/updates.json'}
        old=self.root/'versions/0.3.0-dev.1';old.mkdir(parents=True)
        (old/'WartNode.exe').write_bytes(b'old')
        installed.inventory(old,'0.3.0-dev.1')
        installed.register(self.root,'0.3.0-dev.1')
        self.folder=Path(self.temp.name)/'app';self.folder.mkdir()
        (self.folder/'WartNode.exe').write_bytes(b'new')
        (self.folder/'WartNodeHelper.exe').write_bytes(b'helper')
        (self.folder/'_internal/components').mkdir(parents=True)
        atomic_json(self.folder/'_internal/components/manifest.json',{'application':'0.3.0-dev.2','network':'mainnet'})
        installed.inventory(self.folder,'0.3.0-dev.2')
        self.archive=Path(self.temp.name)/'update.zip'
        with zipfile.ZipFile(self.archive,'w',zipfile.ZIP_DEFLATED) as z:
            for p in self.folder.rglob('*'):
                if p.is_file():z.write(p,'WartNode/'+p.relative_to(self.folder).as_posix())
        files={p.relative_to(self.folder).as_posix():{'size':p.stat().st_size,'sha256':updates.sha(p)} for p in self.folder.rglob('*') if p.is_file()}
        now=int(time.time())
        self.payload={'schema':1,'application':'wartnode','channel':'development','platform':'windows-x64',
                      'version':'0.3.0-dev.2','data_schema':updates.DATA_SCHEMA,'rollback_schema':updates.DATA_SCHEMA,'issued':now-10,'expires':now+86400,
                      'minimum_windows_build':26100,'asset':{'url':'https://publisher.example/update.zip',
                       'size':self.archive.stat().st_size,'sha256':updates.sha(self.archive)},'files':files}
        self.wallet=Path(self.temp.name)/'wallet.db';self.wallet.write_bytes(b'keys and uncertain reservation')

    def tearDown(self):self.temp.cleanup()

    def envelope(self,payload=None,key=None):
        raw=json.dumps(payload or self.payload,separators=(',',':')).encode()
        return {'payload':base64.b64encode(raw).decode(),'signature':base64.b64encode((key or self.key).sign(raw)).decode()}

    def prepare(self):return updates.prepare(self.root,self.envelope(),self.trust,'0.3.0-dev.1',self.archive)

    @unittest.skipUnless(os.name=='nt','Windows delete-sharing semantics')
    def test_journal_publication_survives_a_reader_without_delete_sharing(self):
        import ctypes
        from ctypes import wintypes
        import threading
        path=self.root/'update-journal.json';atomic_json(path,{'phase':'activating'})
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
        kernel.CreateFileW.restype=wintypes.HANDLE;kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.CreateFileW(str(path),0x80000000,3,None,3,0,None)
        self.assertNotEqual(handle,ctypes.c_void_p(-1).value)
        closed=threading.Event()
        def release_reader():kernel.CloseHandle(handle);closed.set()
        timer=threading.Timer(.15,release_reader);timer.start()
        try:
            atomic_json(path,{'phase':'awaiting_health'})
            self.assertTrue(closed.is_set())
            self.assertEqual(updates.read_json(path),{'phase':'awaiting_health'})
            self.assertFalse(list(self.root.glob('update-journal.json.*.tmp')))
        finally:timer.join()

    def test_rejects_wrong_key_expiry_version_network_platform_and_schema(self):
        with self.assertRaisesRegex(ValueError,'signature'):
            updates.verify_envelope(self.envelope(key=Ed25519PrivateKey.generate()),self.trust,'0.3.0-dev.1')
        mutations={'expires':int(time.time())-1,'version':'0.3.0-dev.1','application':'other',
                   'channel':'production','platform':'windows-arm64','data_schema':999,'rollback_schema':999,'minimum_windows_build':99999}
        for field,value in mutations.items():
            with self.subTest(field=field):
                payload={**self.payload,field:value}
                with self.assertRaises(ValueError):updates.verify_envelope(self.envelope(payload),self.trust,'0.3.0-dev.1')
        with self.assertRaisesRegex(ValueError,'not configured'):
            updates.verify_envelope(self.envelope(),{'public_key':None},'0.3.0-dev.1')

    def test_rejects_unsafe_signed_paths_and_download_origins(self):
        for name in ('../evil.exe','/evil.exe','C:/evil','folder\\evil','CON.txt','file.','folder/../evil','file:stream'):
            payload=copy.deepcopy(self.payload);payload['files'][name]={'size':0,'sha256':'0'*64}
            with self.subTest(name=name),self.assertRaises(ValueError):
                updates.verify_envelope(self.envelope(payload),self.trust,'0.3.0-dev.1')
        for url in ('http://publisher.example/x','https://other.example/x','https://user:pass@publisher.example/x'):
            payload=copy.deepcopy(self.payload);payload['asset']['url']=url
            with self.assertRaises(ValueError):updates.verify_envelope(self.envelope(payload),self.trust,'0.3.0-dev.1')

    def test_tampered_or_incomplete_archive_never_switches_version(self):
        self.archive.write_bytes(self.archive.read_bytes()[:-20])
        with self.assertRaisesRegex(ValueError,'checksum'):self.prepare()
        self.assertEqual(updates.active(self.root)['version'],'0.3.0-dev.1')
        self.assertFalse((self.root/'update-request.json').exists())

    def test_success_health_manual_rollback_and_high_water_replay(self):
        self.prepare()
        journal=updates.activate_request(self.root,self.trust)
        self.assertEqual(updates.active(self.root)['version'],'0.3.0-dev.2')
        self.assertFalse(updates.confirm_health(self.root,journal))
        with self.assertRaises(ValueError):updates.mark_healthy(self.root,'0.3.0-dev.2','wrong')
        updates.mark_healthy(self.root,'0.3.0-dev.2',journal['token'])
        self.assertTrue(updates.confirm_health(self.root,journal))
        updates.rollback(self.root)
        current=updates.active(self.root)
        self.assertEqual(current['version'],'0.3.0-dev.1')
        self.assertEqual(current['highest'],'0.3.0-dev.2')
        installed.register(self.root,'0.3.0-dev.1')  # Repair must retain the high-water mark.
        self.assertEqual(updates.active(self.root)['highest'],'0.3.0-dev.2')
        with self.assertRaises(ValueError):updates.verify_envelope(self.envelope(),self.trust,current['version'],highest=current['highest'])
        self.assertEqual(self.wallet.read_bytes(),b'keys and uncertain reservation')

    def test_every_activation_commit_point_recovers_previous_without_data_changes(self):
        for phase in ('prepared','activating','pointer','awaiting_health'):
            with self.subTest(phase=phase):
                # Each attempt is a separate installation with the same signed candidate.
                root=self.root/phase;root.mkdir()
                old=root/'versions/0.3.0-dev.1';old.mkdir(parents=True)
                (old/'WartNode.exe').write_bytes(b'old')
                installed.inventory(old,'0.3.0-dev.1');installed.register(root,'0.3.0-dev.1')
                updates.prepare(root,self.envelope(),self.trust,'0.3.0-dev.1',self.archive)
                def interrupt(point):
                    if point==phase:raise OSError('power loss')
                with self.assertRaises(OSError):updates.activate_request(root,self.trust,checkpoint=interrupt)
                updates.recover(root)
                self.assertEqual(updates.active(root)['version'],'0.3.0-dev.1')
                self.assertEqual(updates.active(root)['highest'],'0.3.0-dev.2')
                self.assertFalse((root/'update-request.json').exists())
                self.assertEqual(self.wallet.read_bytes(),b'keys and uncertain reservation')

    def test_tampering_after_staging_is_rechecked_before_activation(self):
        self.prepare()
        staged=next((self.root/'staging').glob('*/app'))
        (staged/'WartNode.exe').write_bytes(b'tampered')
        with self.assertRaises(ValueError):updates.activate_request(self.root,self.trust)
        self.assertEqual(updates.active(self.root)['version'],'0.3.0-dev.1')

    def test_installer_migrates_legacy_contract_but_cannot_roll_back_to_it(self):
        current=updates.read_json(self.root/'active.json');current['data_schema']=1
        atomic_json(self.root/'active.json',current)
        installed.check_target(self.root,'0.3.0-dev.2')
        new=self.root/'versions/0.3.0-dev.2'
        import shutil
        shutil.copytree(self.folder,new)
        installed.register(self.root,'0.3.0-dev.2')
        self.assertEqual(updates.active(self.root)['data_schema'],updates.DATA_SCHEMA)
        with self.assertRaises(ValueError):updates.rollback(self.root)
        self.assertEqual(self.wallet.read_bytes(),b'keys and uncertain reservation')

    def test_incompatible_rollback_and_missing_previous_version_fail_closed(self):
        self.prepare();journal=updates.activate_request(self.root,self.trust)
        updates.mark_healthy(self.root,journal['version'],journal['token']);updates.confirm_health(self.root,journal)
        record=updates.read_json(self.root/'update-journal.json');record['previous']['data_schema']=1
        atomic_json(self.root/'update-journal.json',record)
        with self.assertRaises(ValueError):updates.rollback(self.root)
        self.assertEqual(updates.active(self.root)['version'],'0.3.0-dev.2')

    def test_disk_full_and_concurrent_installation_preserve_active_version(self):
        from types import SimpleNamespace
        with patch('wartnode.updates.shutil.disk_usage',return_value=SimpleNamespace(free=1)):
            with self.assertRaisesRegex(ValueError,'space'):self.prepare()
        with updates.install_lock(self.root):
            with self.assertRaises(ValueError):
                with updates.install_lock(self.root):pass
        self.assertEqual(updates.active(self.root)['version'],'0.3.0-dev.1')

    def test_concurrent_staging_is_refused_before_download_or_activation(self):
        staging=self.root/'staging';staging.mkdir()
        with updates.install_lock(staging):
            with self.assertRaisesRegex(ValueError,'in progress'):self.prepare()
        self.assertFalse((self.root/'update-request.json').exists())
        self.assertEqual(updates.active(self.root)['version'],'0.3.0-dev.1')

    def test_installed_file_corruption_and_unknown_uninstall_files_are_preserved(self):
        old=self.root/'versions/0.3.0-dev.1'
        (old/'keep.txt').write_text('personal file')
        with self.assertRaises(ValueError):installed.verify_bundle(old,'0.3.0-dev.1')
        installed.uninstall_versions(self.root)
        self.assertEqual((old/'keep.txt').read_text(),'personal file')
        self.assertEqual(self.wallet.read_bytes(),b'keys and uncertain reservation')

    def test_signed_archive_rejects_duplicate_and_unlisted_files(self):
        for index,name in enumerate(('WartNode/WartNode.exe','WartNode/unlisted.exe','WartNode/../escape.exe')):
            with self.subTest(name=name):
                archive=Path(self.temp.name)/('bad-'+str(index)+'.zip')
                with zipfile.ZipFile(self.archive) as source,zipfile.ZipFile(archive,'w') as target:
                    for item in source.infolist():target.writestr(item,source.read(item))
                    target.writestr(name,b'bad')
                payload=copy.deepcopy(self.payload)
                payload['asset'].update(size=archive.stat().st_size,sha256=updates.sha(archive))
                verified=updates.verify_envelope(self.envelope(payload),self.trust,'0.3.0-dev.1')
                destination=self.root/('bad-'+str(index))
                with self.assertRaises(ValueError):updates.stage_archive(archive,destination,verified)
        self.assertEqual(updates.active(self.root)['version'],'0.3.0-dev.1')

    def test_redirect_and_oversized_download_are_rejected_without_leaving_a_file(self):
        import io
        with self.assertRaisesRegex(ValueError,'redirect'):
            updates.NoRedirect().redirect_request(None,None,None,None,None,None)
        response=io.BytesIO(b'123456789');response.status=200
        with patch('wartnode.updates.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value=response
            destination=Path(self.temp.name)/'download'
            with self.assertRaises(ValueError):updates.download('https://publisher.example/x',destination,8)
            self.assertFalse(destination.exists())
