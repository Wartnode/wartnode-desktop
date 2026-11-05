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

## Existing beta limits


- Windows 10, Windows Server, ARM and 32-bit Windows are outside the target.
  Earlier Windows 11 feature releases have not all been executed individually.
- Mining requires a supported CPU and GPU together, driver OpenCL and the managed
  local node. There is no CPU-only mode, external pool option or developer fee.
- CPU temperature/power are unavailable. Ambiguous identical GPU models may have
  unavailable sensors instead of readings assigned to the wrong device.
- Accepted miner work is separate from chain-confirmed rewards. Reward totals
  remain partial until address-history coverage is complete.
- Qt layout checks at logical 100/150/200% sizes do not certify physical Windows
  200% scaling, a full human keyboard/Narrator review or multi-monitor transitions.
- Full genesis replay and remaining physical power/storage failure and reference-
  node propagation coverage are not certified by this beta.
- Windows Authenticode is absent. Project signatures authenticate release files
  without establishing Microsoft publisher identity or reputation.
- Automatic update discovery stays disabled. Use this repository's Releases page
  for manual installer upgrades.
- Lite relies on hosted data and does not independently validate the chain.
  Imported chain files require a trusted source; structural checks do not replay
  every historical block.


## Reporting a problem


Include the app version, Windows build, connection mode, reproduction steps and
redacted diagnostics. For mining, include CPU/GPU/driver versions. Keep passwords,
recovery words, private keys, addresses and wallet files out of public reports.
Collected qualification data, VM files and private development history are not
included in the public repository or matching source archive.


[Previous beta notes](https://github.com/Wartnode/wartnode-desktop/blob/v0.5.0-beta.2/docs/BETA.md)
