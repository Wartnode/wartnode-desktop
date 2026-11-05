# 0.5.0-beta.1

This beta introduces the Dashboard layout across Overview, Node, Wallet, Mine,
Explorer and Settings. Wallets use a fresh shared-password profile with labeled
addresses, an address book and complete encrypted profile backups. Overview shows
one current connection status, combined funds, mining performance and network
statistics. Difficulty uses GH/TH.

## Changes

- Full/Lite selection and detailed node statistics are on Node; logs open separately.
- Mining centers on hashrate charts, supported CPU/GPU readings, session counters
  and canonical rewards for the selected payout address across sessions. Settings
  and the console open in separate windows. Missing sensors and partial history
  are labeled.
- Presets are Eco 30%, Balanced 60% and Heavy 90%; Custom supports independent
  CPU/GPU ceilings from 5% to 95%. Existing saved limits retain their effective
  values as Custom.
- Explorer rolls the latest 25 blocks, supports Pause/Resume, and opens wartnode.com
  from its website button or a double-clicked height.
- General, Security, Storage, Updates and About provide working preferences,
  including installed-only startup, tray behavior, appearance, start page,
  balance privacy and configurable automatic lock.
- The wallet fee and managed node minimum are 0.00009992 WART.
- All Windows 11 x64 releases are targeted, starting at build 22000. Per-release
  OS certification is not claimed or required; see [compatibility](WINDOWS_COMPATIBILITY.md).

## Upgrade from 0.4.0-beta.1

The new data contract requires a **fresh profile**. Preserve your original backup
and export recovery phrases/private keys using the old application before
upgrading. The new app leaves old-format wallet databases untouched and asks you
to choose a fresh folder. Import addresses manually, verify them, and make a new
.wartprofile backup. Automatic migration and legacy backup import are not provided.
Binary rollback to an incompatible data format remains blocked. See the
[user guide](MVP_GUIDE.md) for backup and recovery details.

## Qualification and beta scope

The Dashboard development baseline passed the application/wallet/native suites,
Windows 11 host installation and recovery, a two-address frozen-profile backup/
restore/password-change flow in a Windows 11 guest, actual node fee/shutdown checks,
and physical 30/60/90 and independent Custom mining limits on Ryzen 7 5800X / RTX
4060. The exact public beta source and artifacts are bound to its public GitHub
Actions run and signed manifests; development-baseline results retain their
original scope and are not relabeled as fresh OS certification of every build.

As for the previous public beta, wider CPU/GPU/driver testing, remaining physical
display/accessibility coverage and the 72-hour integrated soak are deferred. They
are not marked passed and remain requirements for broader stable-release claims.

## Known limits

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

[Previous beta notes](https://github.com/Wartnode/wartnode-desktop/blob/v0.4.0-beta.1/docs/BETA.md)
