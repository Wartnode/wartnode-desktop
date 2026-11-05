"""Verify and extract the distributable into a new folder for acceptance."""
import hashlib
import json
from pathlib import Path
import zipfile
import sys

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from wartnode import __version__
archive=root/f'dist/Wartnode-Desktop-{__version__}-windows-x64.zip'
digest=hashlib.sha256(archive.read_bytes()).hexdigest()
expected=archive.with_suffix('.zip.sha256').read_text().split()[0]
if digest!=expected:raise SystemExit('Archive checksum does not match')
destination=root/'work'/('extracted-'+digest[:12])
destination.mkdir(exist_ok=False)
with zipfile.ZipFile(archive) as package:
    for member in package.infolist():
        target=(destination/member.filename).resolve()
        if not target.is_relative_to(destination.resolve()):raise SystemExit('Unsafe package member')
    package.extractall(destination)
record={'archive_sha256':digest,'directory':str(destination/'WartNode')}
(root/'evidence/extracted-package.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
print(json.dumps(record,indent=2))
