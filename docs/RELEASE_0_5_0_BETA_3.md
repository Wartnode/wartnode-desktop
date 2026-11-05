# 0.5.0-beta.3: message and file signing

**Wallet → My addresses → Sign / Verify** can now prove control of a Warthog
address by signing a message or file fingerprint. It works locally in Full node
and Lite modes. File contents are read locally and are never uploaded.

- Select an owned address and review the exact message or filename, byte length
  and SHA-256 before approving. Signing requires an unlocked profile.
- Copy the public proof or save a `.wartproof` document. For files, share the
  original file separately.
- Verify pasted or saved proofs against an independently obtained expected
  address. Verification works with the profile locked and no running node.
- Editing inputs clears previous results. Cancelling or locking during review
  prevents that approval from being used. Reviews expire after two minutes.

New proofs use the pinned Warthog Python SDK and native libsecp256k1 through
coincurve. The v2 format carries a 65-byte recoverable signature. Earlier v1
prototype proofs remain readable; new signing always produces v2. The complete
[format, examples and verifier instructions](SIGNED_PROOFS.md) are public.
This is a WartNode application format, not a Warthog consensus or cross-wallet
standard. It creates no transaction, fee or on-chain address-name record.

Messages are limited to 16,384 UTF-8 bytes; files to 16 GiB. A valid signature
proves access to the private key for that signed content, without establishing
legal identity, current or exclusive custody, trusted time, or file safety.
Wallet-claim services must issue and consume their own fresh challenges.

## Upgrade and recovery

Exit Wartnode-Desktop before installing. Upgrades from 0.5.0-beta.1 or beta.2
retain data contract 4 and profile format 1: no fresh profile, wallet transfer,
chain migration or resync is required. Preserve your encrypted profile backup.
The earlier 0.4.0-beta.1 manual fresh-profile transfer remains necessary.

Existing Dashboard, mining recovery, payment reservation and backup behavior
remain included. Automatic update discovery stays disabled; use the installer
from this repository's Releases page.

## Verification and beta scope

Download the matching source archive, both project-signed manifests and
`SHA256SUMS` with the installer or portable ZIP. Follow
[release verification](RELEASE_SIGNING.md). Project signatures do not establish
Windows Authenticode publisher trust; the installer remains Authenticode unsigned.

The signing integration received a focused source review and 22 manual packaged
UI checks covering Full node/Lite, message/file signing, cancellation, locked
verification and invalid inputs. That review is not a full native cryptographic
or side-channel audit. Physical Windows session lock and maximum-size file reads
were not manually exercised; automated tests cover lock-during-review and
cancelled hashing.

Broader hardware/driver, Windows/accessibility and integrated soak qualification
remain existing beta deferrals. Public CI and signed manifests identify this
release's exact source and files; historical qualification is not relabeled as
fresh physical hardware or clean Windows certification.
