import hashlib
import json
from pathlib import Path

root=Path(__file__).resolve().parents[1]
package=root/'dist/WartNode'
files=list(package.rglob('*'))
for file in files:
    name=file.name.lower()
    if file.is_file() and (name.endswith(('.db','.db3','.wartbackup','.wartprofile','.log')) or name=='icuuc.dll' or name.startswith('icudt') or any(part in name for part in ('nvrtc','cudart','bzminer'))):
        raise SystemExit(f'Unexpected runtime or user data in package: {file.relative_to(package)}')
for required in ('WartNode.exe','WartNodeHelper.exe','_internal/components/manifest.json','_internal/assets/fonts/montserrat-variable.ttf'):
    if not (package/required).is_file():raise SystemExit('Missing package file: '+required)
manifest=json.loads((package/'_internal/components/manifest.json').read_text(encoding='utf-8'))
context=json.loads((package/'provenance/build-context.json').read_text(encoding='utf-8'))
if context['application']!=manifest['application'] or (package/'provenance/build.json').exists():
    raise SystemExit('Stale bundled build receipt')
for component in (name for name,entry in manifest['components'].items() if 'file' in entry):
    info=manifest['components'][component]
    data=(package/'_internal/components'/info['file']).read_bytes()
    if hashlib.sha256(data).hexdigest()!=info['sha256']:raise SystemExit('Component hash mismatch: '+component)
    if context['components'][component]!=info['sha256']:raise SystemExit('Build context component mismatch: '+component)
receipt={'files':sum(p.is_file() for p in files),'bytes':sum(p.stat().st_size for p in files if p.is_file()),
         'network':'mainnet','cuda_runtime_bundled':False,'bzminer_bundled':False,'user_data_bundled':False,
         'gui_sha256':hashlib.sha256((package/'WartNode.exe').read_bytes()).hexdigest()}
(root/'evidence/package-audit.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
print(json.dumps(receipt,indent=2))
