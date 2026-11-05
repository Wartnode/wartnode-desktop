"""Verify every official input and both exact shutdown-only source deltas."""
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.ci.core_inputs import verify
item, patches, originals, patched = verify(ROOT)
receipt = {'repository': item['repository'], 'commit': item['commit'], 'archive_sha256': item['tar_sha256'],
           'checked_files': len(originals), 'patches': patches, 'line_endings_normalized': list(patched),
           'consensus_changes': False, 'all_passed': True}
(ROOT / 'evidence/upstream-source.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
print(json.dumps(receipt, indent=2))
