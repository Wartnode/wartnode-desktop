"""Per-user settings and resumable, stopped-node storage moves."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
import time
from .paths import atomic_json

LOCK = threading.RLock()
NODE_FILES = {'chain.db3', 'chain.db3-wal', 'chain.db3-shm', 'peers.db3',
              'peers.db3-wal', 'peers.db3-shm', 'config.toml'}
DEFAULTS = {'schema': 1, 'setup_complete': False, 'chain_directory': None,
            'connection_mode': 'local', 'connection_revision': 0,
            'mining_mode': 'normal', 'cpu_limit': 50, 'gpu_limit': 50,
            'cpu_threads': max(1, (os.cpu_count() or 2)//2), 'gpu_device': 0,
            'payout_address': '', 'contacts': []}


class SettingsError(ValueError):
    pass


def read_settings(path):
    with Path(path).open('rb') as stream:
        raw=stream.read(1024*1024+1)
    if len(raw)>1024*1024:raise ValueError('Settings exceed the size limit')
    value=json.loads(raw)
    if (not isinstance(value,dict) or value.get('schema')!=1
            or value.get('connection_mode','local') not in ('local','lite')
            or type(value.get('connection_revision',0)) is not int or value.get('connection_revision',0)<0
            or type(value.get('setup_complete',False)) is not bool
            or not isinstance(value.get('contacts',[]),list)
            or len(value.get('contacts',[]))>200
            or not isinstance(value.get('payout_address',''),str)
            or type(value.get('cpu_threads',1)) is not int or not 1<=value.get('cpu_threads',1)<=4096
            or type(value.get('gpu_device',0)) is not int or not 0<=value.get('gpu_device',0)<=63):
        raise ValueError('Invalid settings format')
    if value.get('chain_directory') is not None and (not isinstance(value['chain_directory'],str) or not Path(value['chain_directory']).is_absolute()):
        raise ValueError('Invalid blockchain storage path')
    for contact in value.get('contacts',[]):
        if not isinstance(contact,dict) or not all(isinstance(contact.get(k),str) for k in ('name','address')):
            raise ValueError('Invalid contact list')
    from .mining import validate
    validate(value.get('cpu_limit',50),value.get('gpu_limit',50),value.get('mining_mode','normal'))
    return {**DEFAULTS,**value}


def load(directory):
    path = Path(directory)/'settings.json'
    if not path.exists():
        return {**DEFAULTS, 'contacts': []}
    try:
        return read_settings(path)
    except (ValueError, TypeError, AttributeError) as exc:
        raise SettingsError('Settings could not be read. Restore the previous settings or reset setup. Your wallets and existing chain files will be kept.') from exc


def save(directory, **changes):
    with LOCK:
        value = load(directory)
        if set(changes)-set(DEFAULTS):
            raise ValueError('Unknown setting')
        if (Path(directory)/'settings.json').exists():
            atomic_json(Path(directory)/'settings.previous.json',value)
        value.update(changes)
        from .mining import validate
        validate(value['cpu_limit'],value['gpu_limit'],value['mining_mode'])
        if (value['connection_mode'] not in ('local','lite')
                or type(value['connection_revision']) is not int or value['connection_revision']<0):
            raise ValueError('Invalid connection setting')
        atomic_json(Path(directory)/'settings.json', value)
        return value


def recover_settings(directory,use_previous):
    root=Path(directory)
    value=read_settings(root/'settings.previous.json') if use_previous else {**DEFAULTS,'contacts':[]}
    source=root/'settings.json'
    saved=root/f'settings.saved-{time.time_ns()}.json'
    if source.stat().st_size>1024*1024:
        raise ValueError('The settings file is too large to preserve here. Move it to a backup folder before resetting setup.')
    shutil.copy2(source,saved)
    atomic_json(source,value)
    return str(saved)


def chain_directory(directory):
    root = Path(directory).resolve()
    value = load(root)['chain_directory']
    if value is None:
        return root/'node'
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError('The blockchain storage path must be an absolute folder path')
    path = Path(value).resolve()
    if path != root/'node' and (path == root or path in root.parents or root in path.parents):
        raise ValueError('Choose a blockchain folder outside the wallet data folder')
    return path


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            result.update(block)
    return result.hexdigest()


def storage_info(directory):
    chain = chain_directory(directory)
    parent = chain
    while not parent.exists():
        parent = parent.parent
    used = sum(p.stat().st_size for p in chain.iterdir() if p.is_file()) if chain.is_dir() else 0
    return {'path': str(chain), 'used': used, 'free': shutil.disk_usage(parent).free}


def move_chain(directory, destination, checkpoint=lambda _: None):
    """Caller holds the runtime operation lock and has stopped node and miner."""
    root = Path(directory).resolve()
    receipt = root/'storage-move.json'
    if receipt.exists():
        resume_move(root, checkpoint)
    source = chain_directory(root)
    requested = Path(destination).expanduser()
    if requested.is_symlink() or requested.is_junction() or str(requested).startswith('\\\\'):
        raise ValueError('Choose a local folder without a link or network share')
    target = requested.resolve()
    if target == source:
        return str(target)
    if (target == root or target in root.parents or root in target.parents
            or source in target.parents or target in source.parents or target.is_symlink()):
        raise ValueError('Choose a separate empty folder for blockchain storage')
    target.mkdir(parents=True, exist_ok=True)
    if any(target.iterdir()):
        raise ValueError('The blockchain destination must be empty')
    files = []
    for path in source.iterdir():
        if path.name not in NODE_FILES or not path.is_file() or path.is_symlink():
            raise ValueError('Unexpected files in blockchain storage. Keep them and contact support.')
        files.append({'name': path.name, 'size': path.stat().st_size, 'sha256': digest(path)})
    if shutil.disk_usage(target).free < sum(item['size'] for item in files)+1024**3:
        raise ValueError('The destination needs space for the blockchain plus 1 GiB free')
    atomic_json(receipt, {'schema': 1, 'source': str(source), 'destination': str(target), 'files': files})
    checkpoint('journal')
    return resume_move(root, checkpoint)


def resume_move(directory, checkpoint=lambda _: None):
    root = Path(directory).resolve()
    receipt = root/'storage-move.json'
    if not receipt.exists():
        return str(chain_directory(root))
    value = json.loads(receipt.read_text(encoding='utf-8'))
    source, target = Path(value['source']), Path(value['destination'])
    current = chain_directory(root)
    if (value.get('schema') != 1 or not source.is_absolute() or not target.is_absolute()
            or current not in (source, target) or source == target
            or target == root or target in root.parents or root in target.parents
            or source in target.parents or target in source.parents):
        raise ValueError('The blockchain move record is invalid. Keep both folders.')
    for item in value['files']:
        if item['name'] not in NODE_FILES:
            raise ValueError('The blockchain move record contains an unexpected file')
        old, new = source/item['name'], target/item['name']
        expected = item['sha256']
        if new.exists():
            if new.is_symlink() or digest(new) != expected:
                raise ValueError('Blockchain files conflict. Both copies have been kept.')
        else:
            if old.is_symlink() or digest(old) != expected:
                raise ValueError('A blockchain file changed during the move. Both folders have been kept.')
            temporary = new.with_name(new.name+'.moving')
            if temporary.is_symlink():raise ValueError('The blockchain copy path is a link. Keep both folders.')
            with old.open('rb') as incoming, temporary.open('wb') as outgoing:
                shutil.copyfileobj(incoming, outgoing, 1024*1024)
                outgoing.flush()
                os.fsync(outgoing.fileno())
            if digest(temporary) != expected:
                raise ValueError('The blockchain copy could not be verified')
            os.replace(temporary, new)
        checkpoint('copied:'+item['name'])
        if old.exists():
            if digest(old) != expected:
                raise ValueError('The source blockchain file changed. Both copies have been kept.')
            old.unlink()
        checkpoint('moved:'+item['name'])
    save(root, chain_directory=str(target))
    checkpoint('settings')
    receipt.unlink()
    return str(target)


def save_contact(directory, name, address):
    from .compat import wallet_utils
    address = wallet_utils.validate_address(address.strip())
    name = name.strip()
    if not name or len(name)>80 or any(ord(c)<32 for c in name):
        raise ValueError('Enter a contact name of 1 to 80 characters')
    with LOCK:
        contacts = [c for c in load(directory)['contacts'] if c['address'] != address]
        if len(contacts)>=200:
            raise ValueError('The contact list is full')
        contacts.append({'name': name, 'address': address})
        save(directory, contacts=contacts)
