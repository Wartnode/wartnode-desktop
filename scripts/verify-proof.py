"""Verify a WartNode proof without starting the desktop or unlocking a wallet."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wartnode import proofs


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('proof', help='WartNode .wartproof signature document')
    parser.add_argument('--address', required=True, help='Expected Warthog address from a trusted source')
    parser.add_argument('--file', help='Original file, required for a file proof')
    args = parser.parse_args()
    try:
        result = proofs.verify(proofs.read(args.proof), args.address, args.file)
    except (OSError, ValueError) as error:
        print('NOT VERIFIED: ' + str(error), file=sys.stderr)
        return 1
    print('VERIFIED: ' + result['address'])
    if result['kind'] == 'message':
        print('Signed message follows (exact text):')
        print(result['content']['message'])
    else:
        print('File contents match SHA-256: ' + result['content']['sha256'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
