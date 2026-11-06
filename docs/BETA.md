# 0.6.0-beta.2: private keys in Wallet

[Published prerelease](https://github.com/Wartnode/wartnode-desktop/releases/tag/v0.6.0-beta.2) · 2026-11-06T20:47:59Z

## Per-address private keys

In **Wallet → My addresses**, select an address and choose **Private key…**.
Unlock the profile if needed, then confirm the profile password to reveal that
address's private key. **Copy private key** copies the complete 64-character
hexadecimal key for another Warthog wallet that accepts private-key imports.
This works for imported, normally generated, phrase-derived and saved vanity
addresses.

Anyone with the key can spend funds from that address. Closing the dialog or
locking the profile clears its display and the current matching clipboard copy.
The matching clipboard copy also expires after 60 seconds. Clipboard history,
sync and copies retained by other apps remain outside Wartnode's control.

Exporting a key leaves the address in this profile. Both wallets can control the
same address after import. It does not recreate the original recovery words or
export labels, contacts, payments or unfinished searches; retain encrypted
profile backups for complete recovery. Verify the imported address matches the
original before relying on the destination wallet.

## Wallet lock correction

RDP session disconnect now locks the wallet and clears open key, recovery and
password dialogs through the existing session-lock path.

## Upgrade and recovery

Back up the profile in the existing app before upgrading, then exit Wartnode
completely before running the installer. This release keeps **data contract 5,
profile format 2 and backup format 2**, matching 0.6.0-beta.1. No database
migration, wallet transfer or chain resync is required for a beta.1 upgrade.
The older installer is still refused after a newer version is installed;
compatible launcher rollback must preserve the version high-water mark.

Users upgrading from a 0.5.0 beta still need the
[format-1 upgrade and downgrade guidance](https://github.com/Wartnode/wartnode-desktop/blob/v0.6.0-beta.2/docs/RELEASE_0_6_0_BETA_1.md).
Keep the pre-upgrade backup separately: versions older than 0.6 cannot read
format 2. Follow the existing manual-transfer guidance for older per-address
password wallets.

## Verified release scope

- [Trusted public CI](https://github.com/Wartnode/wartnode-desktop/actions/runs/42287350031)
  passed all three jobs at `36fdb85b6e9d8b759a7881bdbf311f910bc1ca79`:
  349 application tests (two existing opt-in GPU skips), 132 wallet tests,
  native/runtime, layout, package, frozen-key, recovery, payment, proof, vanity,
  installer and storage checks.
- The exact CI installer, portable ZIP and matching source ZIP were
  project-signed without rebuilding. Both signatures passed independent
  OpenSSL verification and tamper-rejection checks; 416 tracked source files,
  48 runtime-source hashes and 462 notice files were checked.
- The final packaged signer exported and imported four address types with no
  Python on its PATH. Password, ownership, context and clipboard lifecycle
  checks passed. Source review found no reportable findings in its defined
  scope; the RDP correction received follow-up review and a regression that
  failed before the fix and passed after it.
- All eight actual beta.1-to-beta.2 installer, key-transfer, backup/restore,
  downgrade, rollback, GUI/repair and uninstall scenarios passed in a
  disposable Windows 11 Pro build 26300 VM using synthetic offline profiles.
  No funds or live-chain submissions were used.
- Extended soak, physical suspend/driver-reset and additional GPU hardware
  qualification remain deferred under the agreed beta scope. Existing
  python-ecdsa timing risk remains; this feature adds no dependency.

This is a prerelease, not marked latest. Automatic update discovery remains
disabled. The installer has project signatures in its accompanying manifests
and remains Windows Authenticode unsigned under the existing beta policy.

Download all six assets from this release and follow the
[verification instructions](https://github.com/Wartnode/wartnode-desktop/blob/v0.6.0-beta.2/docs/RELEASE_SIGNING.md).
For data contract 5, use a trusted 0.6 source checkout or compatible trusted
verifier; a 0.5 verifier may reject the new contract. The pinned project key
fingerprints are unchanged.

## Published delivery

All six anonymous downloads matched the accepted staged files and passed the
packaged signature verifier. The release tag points to tested source
`36fdb85b6e9d8b759a7881bdbf311f910bc1ca79`. Prior releases and assets are
unchanged. This web documentation was updated after publication; the tested
installer, portable and matching source archive were not rebuilt or replaced.

## Included earlier features

Vanity addresses with Prefix, Suffix and Prefix + suffix remain on Wallet,
including estimates, encrypted search persistence and manual resume. Mining
must stay stopped above eight combined search characters. Dashboard,
Full node/Lite, mining recovery, payment reservations and message/file
Sign / Verify remain included. [Message and file proofs](SIGNED_PROOFS.md)
retain their documented v1-reading/v2-signing behavior and verification limits.

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

[Previous beta notes](https://github.com/Wartnode/wartnode-desktop/blob/v0.6.0-beta.1/docs/BETA.md)
