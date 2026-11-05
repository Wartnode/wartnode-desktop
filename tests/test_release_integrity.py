"""Release authority, exact build provenance and failure regressions; fake artifacts."""
import base64
import copy
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.request
import zipfile
from maintainer import github,keys,releases
from wartnode import installed,release,updates
from wartnode.paths import atomic_json


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.input=self.root/'input';self.input.mkdir()
        self.keys=keys.generate();self.trust=keys.public_record(self.keys);self.version=getattr(self, 'version_under_test', '0.4.0-dev.4');self.commit='ab'*20
        self.update_trust={'schema':1,'application':'wartnode','channel':('beta' if '-beta.' in self.version else 'development'),'enabled':False,
            'public_key':self.trust['keys']['update']['public_key'],
            'manifest_url':'https://github.com/Wartnode/wartnode-desktop/releases/latest/download/update-envelope.json'}
        folder=self.root/'app';(folder/'_internal/components').mkdir(parents=True);(folder/'_internal/assets').mkdir();(folder/'provenance').mkdir()
        (folder/'WartNode.exe').write_bytes(b'synthetic program');(folder/'WartNodeHelper.exe').write_bytes(b'synthetic helper')
        atomic_json(folder/'_internal/components/manifest.json',{'application':self.version,'network':'mainnet'})
        atomic_json(folder/'_internal/assets/release-policy.json',self.trust)
        atomic_json(folder/'_internal/assets/update-policy.json',self.update_trust)
        atomic_json(folder/'provenance/build-context.json',{'application':self.version,'source_commit':self.commit,'data_schema':updates.DATA_SCHEMA})
        installed.inventory(folder,self.version)
        names=release.names(self.version);(self.input/names['installer']).write_bytes(b'synthetic installer')
        with zipfile.ZipFile(self.input/names['portable'],'w') as archive:
            for path in folder.rglob('*'):
                if path.is_file():archive.write(path,'WartNode/'+path.relative_to(folder).as_posix())
        build={'commit':self.commit,'application':self.version,'checks':{'all_passed':True},
            'installer_sha256':updates.sha(self.input/names['installer']),'windows_sha256':updates.sha(self.input/names['portable'])}
        with zipfile.ZipFile(self.input/names['source'],'w') as archive:
            for name,value in (('provenance/build.json',build),('assets/release-policy.json',self.trust),('assets/update-policy.json',self.update_trust)):
                archive.writestr('WartNode-source/'+name,json.dumps(value))
        atomic_json(self.input/'delivery.json',{'all_passed':True,'production_release':False,'application':self.version,'commit':self.commit,
            'artifacts':{name:updates.sha(self.input/name) for name in names.values()}})
        self.ci={'repository':release.REPOSITORY,'run_id':123,'run_attempt':1,'head_sha':self.commit,'head_branch':'main',
            'event':'push','workflow_path':'.github/workflows/build.yml','workflow_id':99,'status':'completed','conclusion':'success',
            'delivery_artifact':{'id':11,'name':f'Wartnode-Desktop-{self.commit}-windows11-x64-development-attempt-1','digest':'sha256:'+'11'*32},
            'prepared_artifact':{'id':12,'name':f'release-preparation-{self.commit}-attempt-1','digest':'sha256:'+'22'*32}}
        self.out=self.root/'prepared'
        subset={k:v for k,v in self.ci.items() if k not in ('prepared_artifact','status','conclusion','workflow_id')}
        releases.prepare(self.input,self.out,subset)

    def tearDown(self):self.temp.cleanup()

    def signed(self):
        releases.sign(self.out,self.keys,self.ci)
        return updates.read_json(self.out/'release-envelope.json')

    def envelope(self,payload):
        raw=release.canonical(payload)
        return {'payload':base64.b64encode(raw).decode(),'signature':base64.b64encode(self.keys['release'].sign(release.DOMAIN+raw)).decode()}

    def test_exact_ci_files_and_both_key_roles_verify_with_discovery_disabled(self):
        envelope=self.signed();state=self.root/'verified.json'
        payload=release.verify_directory(self.out/'release-envelope.json',self.out,state=state,trust=self.trust,update_trust=self.update_trust)
        self.assertFalse(updates.configured(self.update_trust));self.assertFalse(payload['production_release'])
        self.assertEqual(payload['source_commit'],self.commit)
        self.assertEqual(release.verify_directory(self.out/'release-envelope.json',self.out,state=state,trust=self.trust,update_trust=self.update_trust),payload)
        with self.assertRaisesRegex(ValueError,'already'):
            release.verify(envelope,self.trust,highest=self.version)

    def test_metadata_tamper_wrong_key_expiry_replay_and_compatibility_fail_closed(self):
        envelope=self.signed();payload=release.verify(envelope,self.trust)
        wrong=keys.public_record(keys.generate())
        with self.assertRaisesRegex(ValueError,'signature'):release.verify(envelope,wrong)
        changed=copy.deepcopy(envelope);changed['payload']=base64.b64encode(b'{}').decode()
        with self.assertRaises(ValueError):release.verify(changed,self.trust)
        for field,value in (('expires',int(time.time())-1),('data_schema',2),('minimum_windows_build',99999),
                            ('production_release',True),('source_commit','not-a-commit'),('purpose','publish')):
            with self.subTest(field=field),self.assertRaises(ValueError):release.verify(self.envelope(dict(payload,**{field:value})),self.trust)
        with self.assertRaises(ValueError):release.verify(envelope,self.trust,highest='9.0.0')

    def test_file_tamper_and_changed_ci_request_are_never_signed_or_trusted(self):
        request=updates.read_json(self.out/'release-request.json');request['ci']['run_id']=456
        atomic_json(self.out/'release-request.json',request)
        with self.assertRaisesRegex(ValueError,'GitHub'):releases.sign(self.out,self.keys,self.ci)
        self.assertFalse((self.out/'release-envelope.json').exists())
        request['ci']['run_id']=123;atomic_json(self.out/'release-request.json',request);self.signed()
        (self.out/release.names(self.version)['installer']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'file verification'):
            release.verify_directory(self.out/'release-envelope.json',self.out,trust=self.trust,update_trust=self.update_trust)

    def test_unpinned_rotation_key_and_failed_build_cannot_sign_a_release(self):
        with self.assertRaisesRegex(ValueError,'pinned'):releases.sign(self.out,keys.generate(),self.ci)
        with self.assertRaises(ValueError):releases.sign(self.out,self.keys,dict(self.ci,conclusion='failure'))
        self.assertFalse((self.out/'release-envelope.json').exists())
        self.assertFalse((self.out/'update-envelope.json').exists())

    def test_github_only_allows_project_release_redirects_and_drops_credentials(self):
        initial='https://github.com/Wartnode/wartnode-desktop/releases/latest/download/update-envelope.json'
        request=urllib.request.Request(initial,headers={'Authorization':'synthetic-token','Cookie':'synthetic-cookie'})
        handler=updates.GitHubRedirect()
        redirected=handler.redirect_request(request,None,302,'',{},'https://github.com/Wartnode/wartnode-desktop/releases/download/v1.0.0/update-envelope.json')
        final=handler.redirect_request(redirected,None,302,'',{},'https://release-assets.githubusercontent.com/path?token=fixture')
        self.assertFalse(final.has_header('Authorization'));self.assertFalse(final.has_header('Cookie'))
        for url in ('http://release-assets.githubusercontent.com/x','https://evil.example/x','https://github.com/other/repo/releases/download/v1/x',
                    'https://release-assets.githubusercontent.com:444/x','https://release-assets.githubusercontent.com.evil.example/x'):
            with self.subTest(url=url),self.assertRaises(ValueError):updates.GitHubRedirect().redirect_request(request,None,302,'',{},url)
        with self.assertRaises(ValueError):handler.redirect_request(final,None,302,'',{},initial)

    def test_ci_requires_successful_same_repository_push_and_exact_current_attempt(self):
        run={'status':'completed','conclusion':'success','event':'push','head_sha':self.commit,'workflow_id':99,
            'path':'.github/workflows/build.yml','repository':{'full_name':release.REPOSITORY},'head_repository':{'full_name':release.REPOSITORY},
            'head_branch':'main','run_attempt':1}
        artifact_names=[f'Wartnode-Desktop-{self.commit}-windows11-x64-development-attempt-1',f'release-preparation-{self.commit}-attempt-1']
        artifacts=[{'id':i+1,'name':name,'expired':False,'digest':'sha256:'+'11'*32,
            'workflow_run':{'id':123,'head_sha':self.commit}} for i,name in enumerate(artifact_names)]
        def api(path):
            if '/workflows/' in path:return {'id':99}
            if '/artifacts?' in path:return {'artifacts':artifacts}
            return run
        with patch('maintainer.github.api',side_effect=api):
            self.assertEqual(github.identify(123,self.commit)['prepared_artifact']['id'],2)
            for field,value in (('event','pull_request'),('conclusion','failure'),('head_sha','00'*20),('head_repository',{'full_name':'fork/repo'})):
                previous=run[field];run[field]=value
                with self.subTest(field=field),self.assertRaises(ValueError):github.identify(123,self.commit)
                run[field]=previous
            artifacts[-1]['expired']=True
            with self.assertRaises(ValueError):github.identify(123,self.commit)

    def test_download_checks_transport_digest_before_extracting(self):
        raw=io.BytesIO()
        with zipfile.ZipFile(raw,'w') as archive:
            for file in self.out.iterdir():archive.write(file,file.name)
        data=raw.getvalue();ci=copy.deepcopy(self.ci);ci['prepared_artifact']['digest']='sha256:'+__import__('hashlib').sha256(data).hexdigest()
        def provide(path,output=None):output.write(data)
        with patch('maintainer.github.api',side_effect=provide):
            target=github.download(ci,self.root/'downloaded')
            self.assertEqual(updates.sha(target/'delivery.json'),updates.sha(self.out/'delivery.json'))
            ci['prepared_artifact']['digest']='sha256:'+'ff'*32
            with self.assertRaisesRegex(ValueError,'digest'):github.download(ci,self.root/'tampered')
            self.assertFalse((self.root/'tampered/release-request.json').exists())


class BetaReleaseTests(ReleaseTests):
    version_under_test = '0.4.0-beta.1'

    def test_beta_identity_requires_matching_version_channel_and_purpose(self):
        envelope = self.signed()
        payload = release.verify(envelope, self.trust)
        self.assertEqual(payload['purpose'], 'beta')
        self.assertEqual(payload['channel'], 'beta')
        self.assertEqual(payload['repository'], 'Wartnode/wartnode-desktop')
        for changes in ({'purpose':'rehearsal'}, {'channel':'development'}, {'version':'1.0.0'},
                        {'ci':dict(payload['ci'],head_branch='dev/hidden')}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                release.verify(self.envelope(dict(payload, **changes)), self.trust)
