"""Installed transitions with synthetic funds and a separate ephemeral-trust launcher.

Windows 11 executes the actual installers and GUI/health handshake. Hosted Server
checks installer refusal, then verifies the same files/transition engine and
actual frozen signer. No clean-image or production-trust certification is made.
"""
import base64
import ctypes
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from wartnode import __version__,installed,updates
from wartnode.paths import atomic_json
from wartnode.preflight import windows_compatibility,MINIMUM_WINDOWS_BUILD
from wartnode.processes import hidden_options,guard_process
from test_payment_transport import Handler,OWNER,OTHER


def run(command,log,expected=0,cwd=None,env=None):
    with log.open('w',encoding='utf-8') as output:
        result=subprocess.run([str(c) for c in command],stdout=output,stderr=subprocess.STDOUT,
                              timeout=180,cwd=cwd,env=env,**hidden_options())
    if expected==0 and result.returncode!=0:raise RuntimeError('Command failed with exit code '+str(result.returncode)+'; see '+str(log))
    if expected!=0 and result.returncode==0:raise RuntimeError('A refused operation succeeded; see '+str(log))
    return result.returncode


class Client:
    def __init__(self,folder,profile):
        env={k:v for k,v in os.environ.items() if not k.startswith(('PYTHON','QT_'))}
        env['PATH']=str(Path(os.environ['SystemRoot'])/'System32')
        self.p=subprocess.Popen([str(folder/'WartNodeHelper.exe'),'--signer',str(profile)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,**hidden_options())
        self.job=guard_process(self.p);self.counter=0
    def call(self,action,**args):
        self.counter+=1
        self.p.stdin.write((json.dumps({'id':self.counter,'action':action,'args':args})+'\n').encode());self.p.stdin.flush()
        raw=self.p.stdout.readline()
        if not raw:raise RuntimeError('Frozen signer stopped unexpectedly: '+self.p.stderr.read().decode('utf-8','replace')[-2500:])
        result=json.loads(raw)
        if 'error' in result:raise ValueError(result['error'])
        return result['result']
    def close(self):
        self.p.stdin.close();self.p.wait(timeout=15)
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(self.job))
        assert self.p.returncode==0


def wait_for(check,timeout=45):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if check():return
        time.sleep(.025)
    raise RuntimeError('Installed application operation timed out')


def main():
    fixture=updates.read_json(ROOT/'evidence/ci/upgrade-fixture.json')
    work=Path(tempfile.mkdtemp(prefix='installed-产品-',dir=ROOT/'work'))
    root=work/'Application folder';profile=work/'Wallet data';profile.mkdir()
    for name in ('node','logs','backups','runtime'):(profile/name).mkdir()
    log_folder=ROOT/'evidence/ci';log_folder.mkdir(exist_ok=True,parents=True)
    target_installer=ROOT/f'dist/Wartnode-Desktop-{__version__}-windows-x64-setup.exe'
    base_version=fixture['fixture_version'];base_installer=Path(fixture['installer'])
    host=windows_compatibility();native=host['supported']
    record={'application':__version__,'base_fixture_version':base_version,'windows':host,
        'actual_windows11_installer':native,'clean_windows11_certified':False,
        'production_trust':False,'test_trust':'ephemeral public key in a separate test launcher',
        'funds_used':False,'live_chain_submission':False,'scenarios':[]}
    def scenario(name):record['scenarios'].append(name);print(name,flush=True)
    def setup(path,name,refuse=False):
        try:
            return run([path,'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-','/DIR='+str(root),
                        '/LOG='+str(log_folder/(name+'-inno.log'))],log_folder/(name+'.log'),expected=1 if refuse else 0)
        except RuntimeError:
            version=base_version if path==base_installer else __version__
            diagnostic={'scenario':name,'root_launcher_exists':(root/'WartNode.exe').is_file(),
                'root_files':[p.name for p in root.iterdir()] if root.exists() else []}
            try:
                installed.verify_bundle(root/'versions'/version,version)
                diagnostic['bundle_verified']=True
            except Exception as error:diagnostic['bundle_error']=str(error)
            record['installer_failure_diagnostic']=diagnostic
            raise
    def uninstall(name):
        # Inno's original EXE exits before its temporary clone finishes. Waiting
        # for that PID or its removal races the next setup's initialization.
        # Start-Process -Wait waits for the whole owned process tree, including
        # the clone. Keep the real exit status and normal removal assertions.
        # https://jrsoftware.org/ishelp/topic_uninstexitcodes.htm
        def quote(value):return "'"+str(value).replace("'","''")+"'"
        args=subprocess.list2cmdline(['/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',
                                     '/LOG='+str(log_folder/(name+'-inno.log'))])
        script=("$ErrorActionPreference='Stop'; $uninstallProcess=Start-Process -FilePath "+
                quote(root/'unins000.exe')+' -ArgumentList '+quote(args)+
                ' -WindowStyle Hidden -Wait -PassThru; exit $uninstallProcess.ExitCode')
        encoded=base64.b64encode(script.encode('utf-16le')).decode('ascii')
        run([shutil.which('pwsh'),'--NoProfile','-EncodedCommand',encoded],log_folder/(name+'.log'))
        wait_for(lambda:not (root/'unins000.exe').exists())
        uninstall_log=(log_folder/(name+'-inno.log')).read_text(encoding='utf-8-sig')
        assert str(root) in uninstall_log and 'Uninstallation process succeeded.' in uninstall_log
        assert 'Log closed.' in uninstall_log
    if native:
        import winreg
        from wartnode import desktop_preferences
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,desktop_preferences.RUN_KEY) as key:
                winreg.QueryValueEx(key,desktop_preferences.RUN_NAME)
        except FileNotFoundError:pass
        else:raise RuntimeError('An existing desktop startup registration is present. Qualification will not replace it.')
        registry=r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{AD317E15-0C4D-4586-BD4C-84C6551A2B8E}_is1'
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,registry):pass
        except FileNotFoundError:pass
        else:raise RuntimeError('An existing WartNode installation is registered. The qualification will not change it.')
        setup(base_installer,'install-base');scenario('actual_per_user_install_in_space_and_unicode_path')
        desktop_preferences.set_startup(True,root,profile)
        assert desktop_preferences.startup_enabled(root,profile)
        desktop_preferences.set_startup(False,root,profile)
        assert not desktop_preferences.startup_enabled(root,profile)
        desktop_preferences.set_startup(True,root,profile)
        scenario('actual_per_user_startup_enable_disable_without_node_or_miner_autostart')
    else:
        setup(target_installer,'unsupported-server',refuse=True)
        assert not root.exists();scenario('hosted_server_refused_before_installation')
        root.mkdir()
        shutil.copytree(Path(fixture['bundle']),root/'versions'/base_version)
        installed.register(root,base_version)
    # A controlled HTTP fixture provides exact synthetic mainnet context only.
    server=ThreadingHTTPServer(('127.0.0.1',19400),Handler)
    server.payloads=[];server.verified=0;server.drop_reply=True
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    password='synthetic-installed-password'
    tx_hash=None
    def wallet_check():
        folder=root/'versions'/updates.active(root)['version']
        client=Client(folder,profile)
        try:
            assert client.call('unlock',address=OWNER,password=password)['address']==OWNER
            rows=client.call('history',address=OWNER)['rows']
            assert len(rows)==1 and rows[0]['status']=='uncertain' and rows[0]['tx_hash']==tx_hash
            assert client.call('balance')['reserved']==125009992
            client.call('lock')
        finally:client.close()
    def start_gui(seconds=5):
        env=dict(os.environ);env['QT_QPA_PLATFORM']='offscreen'
        return subprocess.Popen([str(root/'WartNode.exe'),'--data-dir',str(profile),'--no-node',
                                 '--smoke-seconds',str(seconds)],env=env,**hidden_options())
    try:
        client=Client(root/'versions'/base_version,profile)
        try:
            client.call('setup',password=password)
            client.call('import',kind='private',secret='00'*31+'01',label='Main')
            reviewed=client.call('review',recipient=OTHER,amount='1.25000000')
            result=client.call('send',token=reviewed['token'],password=password)
            assert result['status']=='uncertain';tx_hash=result['tx_hash']
        finally:client.close()
        wallet_check();scenario('actual_frozen_wallet_and_uncertain_payment_saved')
        if native:
            gui=start_gui(10)
            try:
                def busy():
                    try:
                        with updates.install_lock(root):return False
                    except ValueError:return True
                wait_for(busy)
                setup(target_installer,'running-install-refused',refuse=True)
                assert updates.active(root)['version']==base_version
            finally:
                gui.wait(timeout=45)
                assert gui.returncode==0
            scenario('running_installed_application_blocks_replacement')
        # Build a launcher with synthetic trust in an isolated source directory.
        key=Ed25519PrivateKey.generate()
        trust={'schema':1,'application':'wartnode','channel':'development',
               'public_key':key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex(),
               'manifest_url':'https://publisher.example/updates.json'}
        with zipfile.ZipFile(ROOT/f'dist/Wartnode-Desktop-{__version__}-windows-x64.zip') as z:
            import hashlib
            files={item.filename[len('WartNode/'):]:{'size':item.file_size,'sha256':hashlib.sha256(z.read(item)).hexdigest()}
                   for item in z.infolist() if not item.is_dir()}
        archive=ROOT/f'dist/Wartnode-Desktop-{__version__}-windows-x64.zip'
        now=int(time.time())
        payload={'schema':1,'application':'wartnode','channel':'development','platform':'windows-x64',
                 'version':__version__,'data_schema':updates.DATA_SCHEMA,'rollback_schema':updates.DATA_SCHEMA,'minimum_windows_build':MINIMUM_WINDOWS_BUILD,
                 'issued':now-10,'expires':now+86400,'files':files,
                 'asset':{'url':'https://publisher.example/update.zip','size':archive.stat().st_size,'sha256':updates.sha(archive)}}
        raw=json.dumps(payload,separators=(',',':')).encode()
        envelope={'payload':base64.b64encode(raw).decode(),'signature':base64.b64encode(key.sign(raw)).decode()}
        if native:
            launcher_source=work/'test-launcher';launcher_source.mkdir()
            shutil.copytree(ROOT/'wartnode',launcher_source/'wartnode',ignore=shutil.ignore_patterns('__pycache__'))
            (launcher_source/'assets').mkdir()
            shutil.copy2(ROOT/'assets/warthog.ico',launcher_source/'assets/warthog.ico')
            atomic_json(launcher_source/'assets/update-policy.json',trust)
            for name in ('run-launcher.py','Launcher.spec'):shutil.copy2(ROOT/name,launcher_source/name)
            build_env=dict(os.environ)
            build_env['PATH']=os.pathsep.join((str(Path(sys.executable).parent),sys.base_prefix,
                                              str(Path(os.environ['SystemRoot'])/'System32'),os.environ['SystemRoot']))
            run([sys.executable,'-m','PyInstaller','--noconfirm','--clean',str(launcher_source/'Launcher.spec'),
                 '--distpath',str(launcher_source/'dist'),'--workpath',str(launcher_source/'build')],log_folder/'test-launcher-build.log',cwd=launcher_source,env=build_env)
            test_launcher=launcher_source/'dist/WartNodeLauncher.exe'
            record['test_launcher_sha256']=updates.sha(test_launcher)
            shutil.copy2(test_launcher,root/'WartNode.exe')
            updates.prepare(root,envelope,trust,base_version,archive)
            interrupted=start_gui(10);job=guard_process(interrupted)
            try:
                def awaiting_health():
                    path=root/'update-journal.json'
                    journal=updates.read_json(path) if path.exists() else None
                    record['interruption_observation']={'launcher_exit':interrupted.poll(),
                        'journal_phase':journal.get('phase') if journal else None}
                    if interrupted.poll() is not None:
                        raise RuntimeError('Update launcher exited before interruption: '+str(record['interruption_observation']))
                    return journal and journal.get('phase')=='awaiting_health'
                wait_for(awaiting_health,timeout=120)
                ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(job));job=None
                interrupted.wait(timeout=30)
                recovery=start_gui(3);recovery.wait(timeout=45);assert recovery.returncode==0
            finally:
                if job:ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(job))
            assert updates.active(root)['version']==base_version
            wallet_check();scenario('actual_launcher_interruption_recovers_previous_version_and_reservations')
            uninstall('uninstall-after-interruption')
            assert not desktop_preferences.startup_enabled(root,profile)
            scenario('actual_uninstall_removes_its_startup_registration')
            setup(base_installer,'reinstall-base')
            shutil.copy2(test_launcher,root/'WartNode.exe')
        else:
            updates.prepare(root,envelope,trust,base_version,archive)
            def interrupt(phase):
                if phase=='pointer':raise OSError('simulated interruption')
            try:updates.activate_request(root,trust,checkpoint=interrupt)
            except OSError:pass
            updates.recover(root);wallet_check()
            scenario('hosted_transition_interruption_preserves_frozen_wallet')
            # A separate fresh installation exercises the successful transition.
            new_root=work/'Second application';new_root.mkdir()
            shutil.copytree(Path(fixture['bundle']),new_root/'versions'/base_version)
            root=new_root;installed.register(root,base_version)
        updates.prepare(root,envelope,trust,base_version,archive)
        if native:
            gui=start_gui(4);gui.wait(timeout=45);assert gui.returncode==0
            assert updates.read_json(root/'update-journal.json')['phase']=='complete'
        else:
            journal=updates.activate_request(root,trust)
            client=Client(root/'versions'/__version__,profile)
            try:assert client.call('list')['wallets'][0]['address']==OWNER
            finally:client.close()
            updates.mark_healthy(root,__version__,journal['token']);updates.confirm_health(root,journal)
        wallet_check();scenario('authenticated_full_package_upgrade_preserves_uncertain_payment')
        if native:
            atomic_json(root/'rollback-request.json',{'version':base_version})
            gui=start_gui(3);gui.wait(timeout=45);assert gui.returncode==0
        else:updates.rollback(root)
        assert updates.active(root)['version']==base_version
        wallet_check();scenario('compatible_rollback_retains_payment_and_anti_replay_high_water_mark')
        if native:
            setup(target_installer,'installer-upgrade')
            wallet_check();scenario('actual_installer_upgrade_preserves_wallet_and_payments')
            setup(base_installer,'downgrade-refused',refuse=True)
            damaged=root/'versions'/__version__/'START-HERE.md';damaged.write_text('damaged')
            setup(target_installer,'repair')
            installed.verify_bundle(root/'versions'/__version__,__version__)
            wallet_check();scenario('actual_repair_restores_corrupt_files_without_resetting_data')
            uninstall('uninstall');assert (profile/'wallet.db').exists()
            setup(target_installer,'reinstall');wallet_check()
            scenario('actual_uninstall_reinstall_retains_wallet_and_uncertain_payment')
            uninstall('final-uninstall')
        else:installed.uninstall_versions(root);assert (profile/'wallet.db').exists()
        assert server.verified==1 and len(server.payloads)==1
        record.update(all_passed=True,real_signatures_verified=server.verified,automatic_payment_retries=0,
                      candidate_installer_sha256=updates.sha(target_installer),workspace=str(work))
        (log_folder/'installed-product.json').write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(record,indent=2))
    finally:
        if not record.get('all_passed'):
            record['failure_install_state']={name:updates.read_json(root/name) for name in
                ('active.json','update-journal.json','update-request.json') if (root/name).exists()}
            (log_folder/'installed-product-failure.json').write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
        server.shutdown();server.server_close();thread.join()
        if native and (root/'unins000.exe').exists() and root.resolve().is_relative_to(work.resolve()):
            # The root was created by this harness and its original registration
            # was absent. Never leave a failed disposable install registered.
            try:uninstall('failure-cleanup')
            except Exception as error:print('Disposable installation cleanup failed: '+str(error),file=sys.stderr)


if __name__=='__main__':main()
