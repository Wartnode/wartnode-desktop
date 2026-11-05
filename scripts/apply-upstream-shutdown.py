"""Apply the exact locked shutdown-only patches to the pinned official core."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.ci.core_inputs import load
item, patches, originals, patched = load(ROOT)
for name, after in patched.items():
    target = ROOT / item['directory'] / name
    assert target.read_bytes() in (originals[name], after), 'Refusing unrelated source changes'
for name, after in patched.items():
    (ROOT / item['directory'] / name).write_bytes(after)
print('Exact locked shutdown patches applied and verified')
