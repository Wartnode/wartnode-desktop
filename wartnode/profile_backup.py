"""Authenticated, versioned backups of the complete fresh-profile database."""
import os
from pathlib import Path
import tempfile
from cryptography.fernet import InvalidToken
from .profile import wrapping_cipher

MAGIC = b'WARTNODE-PROFILE\x02'
LEGACY_MAGIC = b'WARTNODE-PROFILE\x01'
MAX_SNAPSHOT = 32*1024*1024
MAX_BACKUP = 48*1024*1024


def encode(snapshot, password):
    if not isinstance(snapshot, bytes) or not snapshot.startswith(b'SQLite format 3\x00') or len(snapshot) > MAX_SNAPSHOT:
        raise ValueError('Invalid profile snapshot')
    salt = os.urandom(16)
    return MAGIC+salt+wrapping_cipher(password, salt).encrypt(snapshot)


def decode(content, password):
    if (not isinstance(content, bytes) or len(content) > MAX_BACKUP
            or not content.startswith((MAGIC, LEGACY_MAGIC)) or len(content) < len(MAGIC)+16+80):
        raise ValueError('Choose a supported Wartnode profile backup')
    offset = len(MAGIC)
    try:
        value = wrapping_cipher(password, content[offset:offset+16]).decrypt(content[offset+16:])
    except InvalidToken as exc:
        raise ValueError('The profile backup password is incorrect or the backup is damaged') from exc
    if not value.startswith(b'SQLite format 3\x00') or len(value) > MAX_SNAPSHOT:
        raise ValueError('Invalid profile backup contents')
    return value


def save(snapshot, password, destination):
    content = encode(snapshot, password)
    path = Path(destination)
    if path.is_symlink() or path.is_junction():
        raise ValueError('Choose a regular backup file')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.profile-backup-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load(path, password):
    with Path(path).open('rb') as stream:
        content = stream.read(MAX_BACKUP+1)
    return decode(content, password)
