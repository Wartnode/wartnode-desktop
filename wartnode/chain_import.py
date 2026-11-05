"""User-selected cold chain import. Only owned staging files may be removed."""
from contextlib import closing
import hashlib
import os
from pathlib import Path
import re
import shutil
import sqlite3
import time
import uuid
from . import settings
from .paths import atomic_json
from .rpc import GENESIS
from .node_history import MAINNET_BLOCK_ONE


class ImportCancelled(ValueError):
    pass


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():raise ImportCancelled('Chain import cancelled. The original file was kept.')


def source_connection(source):
    source=Path(source)
    if source.is_symlink() or source.is_junction() or not source.is_file():
        raise ValueError('Choose a regular chain database file')
    for suffix in ('-wal','-journal'):
        sidecar=Path(str(source)+suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError('The source has pending journal data. Stop its node cleanly or export a standalone SQLite backup first.')
    with source.open('rb') as stream:
        if stream.read(16)!=b'SQLite format 3\x00':raise ValueError('This file is not a SQLite chain database')
    connection=sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True,timeout=.5)
    connection.execute('PRAGMA query_only=ON')
    connection.execute('PRAGMA trusted_schema=OFF')
    return connection


def inspect_chain(source,cancel=None):
    try:
        with closing(source_connection(source)) as db:
            return inspect_connection(db,cancel)
    except sqlite3.Error as error:
        check_cancel(cancel)
        raise ValueError('The chain database is corrupt, busy or incompatible. Use a clean standalone backup.') from error


def inspect_connection(db,cancel=None):
    end=time.monotonic()+120
    db.set_progress_handler(lambda: int((cancel is not None and cancel.is_set()) or time.monotonic()>end),10000)
    if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('The chain database failed its integrity check')
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'Blocks','Consensus','History','State','AccountHistory'}.issubset(tables):
        raise ValueError('This is not a supported Warthog chain database')
    first=db.execute('SELECT b.hash,b.header FROM Consensus c JOIN Blocks b ON b.rowid=c.block_id WHERE c.height=1').fetchone()
    head=db.execute('SELECT c.height,b.hash FROM Consensus c JOIN Blocks b ON b.rowid=c.block_id WHERE c.height>0 ORDER BY c.height DESC LIMIT 1').fetchone()
    if (not first or not isinstance(first[0],bytes) or first[0].hex()!=MAINNET_BLOCK_ONE
            or not isinstance(first[1],bytes) or len(first[1])!=80 or first[1][:32].hex()!=GENESIS
            or not head or type(head[0]) is not int or head[0]<1 or not isinstance(head[1],bytes) or len(head[1])!=32):
        raise ValueError('The chain database has an unexpected chain anchor or head')
    check_cancel(cancel)
    return {'height':head[0],'hash':head[1].hex(),'integrity':'ok'}


def destination(directory,requested):
    if not isinstance(requested,(str,os.PathLike)):raise ValueError('Invalid blockchain folder')
    root=Path(directory).resolve();requested=Path(requested).expanduser()
    if (not requested.is_absolute() or str(requested).startswith('\\\\')
            or any(p.is_symlink() or p.is_junction() for p in (requested,*requested.parents))):
        raise ValueError('Choose a local absolute blockchain folder without a link')
    target=requested.resolve()
    if target!=root/'node' and (target==root or target in root.parents or root in target.parents):
        raise ValueError('Choose a blockchain folder separate from your wallet data')
    return target


def resume(directory):
    root=Path(directory);receipt=root/'chain-import.json'
    if not receipt.exists():return
    from .updates import read_json
    value=read_json(receipt)
    if not isinstance(value,dict):raise ValueError('Invalid chain import record')
    target=destination(root,value.get('destination'))
    name=value.get('stage','')
    if not isinstance(name,str) or not re.fullmatch(r'\.wartnode-import-[0-9a-f]{32}\.db3',name):raise ValueError('Invalid chain import record')
    stage=target/name;final=target/'chain.db3'
    if stage.is_symlink() or final.is_symlink() or target.is_junction():raise ValueError('A chain import path is a link')
    if value.get('phase')=='copying':
        if stage.exists():stage.unlink()
        receipt.unlink()
        return
    if value.get('phase')!='prepared' or not re.fullmatch('[0-9a-f]{64}',str(value.get('sha256',''))):
        raise ValueError('Invalid chain import record')
    if any(p.name not in (name,'chain.db3') for p in target.iterdir()):
        raise ValueError('The chain destination changed. Existing files were kept.')
    if final.exists():
        if settings.digest(final)!=value['sha256']:raise ValueError('The chain destination changed. Both files were kept.')
    else:
        if not stage.is_file() or settings.digest(stage)!=value['sha256']:raise ValueError('The staged chain changed. The original file was kept.')
        if os.name=='nt':os.rename(stage,final)  # Windows rename refuses an existing destination.
        else:os.link(stage,final);stage.unlink()
    settings.save(root,chain_directory=str(target),setup_complete=True)
    if stage.exists():stage.unlink()
    receipt.unlink()


def initialize(directory,requested,source=None,cancel=None,progress=lambda *_:None,checkpoint=lambda _:None):
    root=Path(directory).resolve();resume(root)
    target=destination(root,requested)
    if source is None:
        target.mkdir(parents=True,exist_ok=True)
        if shutil.disk_usage(target).free<1024**3:raise ValueError('The destination needs at least 1 GiB of free space')
        if any(target.iterdir()):
            raise ValueError('Choose an empty blockchain folder. To use an existing database, import it into an empty folder.')
        return settings.save(root,chain_directory=str(target),setup_complete=True)
    source=Path(source)
    if source.resolve()==(root/'wallet.db').resolve():raise ValueError('Choose a chain database, not a wallet database')
    target.mkdir(parents=True,exist_ok=True)
    if any(target.iterdir()):raise ValueError('Chain import requires an empty destination; existing files were kept')
    size=source.stat().st_size
    if shutil.disk_usage(target).free<size+1024**3:raise ValueError('The destination needs space for the database plus 1 GiB free')
    stage=target/('.wartnode-import-'+uuid.uuid4().hex+'.db3');receipt=root/'chain-import.json'
    prepared=False
    try:
        check_cancel(cancel)
        progress(0,size,'Checking source')
        with closing(source_connection(source)) as db:
            db.execute('BEGIN')  # Hold the source read lock through copying.
            inspect_connection(db,cancel)
            before=source.stat()
            atomic_json(receipt,{'phase':'copying','destination':str(target),'stage':stage.name})
            digest=hashlib.sha256();copied=0
            with source.open('rb') as incoming,stage.open('xb') as outgoing:
                while block:=incoming.read(1024*1024):
                    check_cancel(cancel);outgoing.write(block);digest.update(block);copied+=len(block)
                    progress(copied,size,'Copying chain')
                    if copied>size:raise ValueError('The source changed during import. Stop its node and try again.')
                outgoing.flush();os.fsync(outgoing.fileno())
            after=source.stat()
            if (copied!=size or (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns)):
                raise ValueError('The source changed during import. Stop its node and try again.')
            with closing(source_connection(source)):pass  # Recheck journal state before committing.
        progress(size,size,'Checking copy')
        # This private, fsynced copy has no writer or pending journal. Immutable
        # inspection prevents SQLite from creating WAL/SHM files beside it.
        # The user-supplied source above keeps its normal read transaction lock.
        with closing(sqlite3.connect(stage.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)) as copied_db:
            copied_db.execute('PRAGMA query_only=ON')
            copied_db.execute('PRAGMA trusted_schema=OFF')
            observed=inspect_connection(copied_db,cancel)
        check_cancel(cancel)
        atomic_json(receipt,{'phase':'prepared','destination':str(target),'stage':stage.name,'sha256':digest.hexdigest()})
        prepared=True;checkpoint('prepared');resume(root);checkpoint('settings')
        return {**settings.load(root),'imported':observed}
    except sqlite3.Error as error:
        check_cancel(cancel)
        raise ValueError('The source is busy, corrupt or incompatible. Use a clean standalone backup.') from error
    finally:
        if not prepared:
            if stage.exists():stage.unlink()
            receipt.unlink(missing_ok=True)
