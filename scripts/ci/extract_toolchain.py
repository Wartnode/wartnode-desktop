"""Extract the checksum-verified LLVM archive with its large Zstandard window."""
import argparse
from compression.zstd import DecompressionParameter, ZstdFile
import hashlib
from pathlib import Path
import tarfile


def extract_toolchain(archive, destination, expected_hash):
    with Path(archive).open('rb') as stream:
        actual_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual_hash != expected_hash:
        raise ValueError('LLVM archive checksum mismatch')
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    # The official LLVM distribution uses a window larger than Windows tar's
    # default limit. Stream it with an explicit bounded 2 GiB maximum window.
    with ZstdFile(archive, options={DecompressionParameter.window_log_max: 31}) as compressed:
        with tarfile.open(fileobj=compressed, mode='r|') as source:
            for member in source:
                source.extract(member, path=destination, filter='data')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('sha256')
    args = parser.parse_args()
    extract_toolchain(args.archive, args.destination, args.sha256)
