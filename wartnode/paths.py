"""Stable user paths and verified distribution assets."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time


def asset_root():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))


def legacy_path():
    return asset_root() / "vendor" / "wart_dapp"


def data_root(override=None):
    base = Path(override) if override else Path(os.environ.get("LOCALAPPDATA", Path.home())) / "WartNode"
    base.mkdir(parents=True, exist_ok=True)
    for name in ("node", "logs", "backups", "runtime"):
        (base / name).mkdir(exist_ok=True)
    return base.resolve()


def atomic_json(path, value):
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=path.name+'.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        # Windows readers/antivirus can briefly deny delete sharing. Retry the
        # same fsynced temporary file; never delete the previous journal first.
        deadline=time.monotonic()+2
        while True:
            try:
                os.replace(temporary,path)
                break
            except PermissionError as error:
                if os.name!='nt' or getattr(error,'winerror',None) not in (5,32,33) or time.monotonic()>=deadline:
                    raise
                time.sleep(.02)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def verified_component(name):
    manifest = json.loads((asset_root() / "components" / "manifest.json").read_text(encoding="utf-8"))
    entry = manifest["components"][name]
    filename = Path(entry["file"])
    if filename.name != str(filename) or not filename.name.endswith(".exe"):
        raise ValueError("Invalid component path")
    path = asset_root() / "components" / filename
    if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
        raise ValueError(f"{name.title()} verification failed. Reinstall Wartnode-Desktop.")
    return path
