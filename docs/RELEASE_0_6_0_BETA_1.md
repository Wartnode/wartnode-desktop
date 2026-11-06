# 0.6.0-beta.1: vanity addresses in Wallet

[Published prerelease](https://github.com/Wartnode/wartnode-desktop/releases/tag/v0.6.0-beta.1) · 2026-11-06T15:24:39Z

Create a Warthog vanity address from **Wallet → My addresses → Vanity address**.
Choose **Prefix**, **Suffix**, or **Prefix + suffix**, using `0–9` and `a–f`.
Searches run locally with CPU or OpenCL GPU support. No node connection is needed.

The dialog shows search difficulty and can measure this computer for an estimated
average time. Each extra character usually means 16 times more work. Estimates
are probabilities, exclude hardware preparation, and cannot promise a finish time.
Very long patterns show a warning when they are impractical or may have no match.

Mining must be stopped for searches with **more than eight combined prefix and
suffix characters**. Time measurement always requires mining to be stopped.
Mining cannot start while either exclusive operation is running.

Closing the dialog pauses the search. Unfinished searches are saved securely in
the profile and require **Resume search** after restarting. A match must be saved
with **Save to wallet** before using its address. Recovery uses an encrypted
**profile backup**, including pending searches and saved vanity addresses; vanity
addresses do not have recovery words. Make a new backup after saving an address.

## Upgrade from 0.5.0-beta.3

1. In the old version, make an encrypted **Back up profile** backup and keep its
   password. Keep this backup separately from later backups.
2. Exit Wartnode-Desktop completely, then run the new installer against the same
   application installation. Keep the existing wallet data folder separate from
   the application folder.
3. Open the existing profile. Supported shared-password profiles migrate
   automatically. Addresses, labels, contacts and payment records are retained;
   no wallet transfer or blockchain resync is required for this migration.
4. Check the wallet and make a new profile backup for this version.

Use the installer for this transition. Automatic update discovery remains disabled.
The older per-address-password wallet format still requires a fresh profile and
manual address import; this release does not add a converter for that format.

## Downgrade limits

This release changes **profile format 1 to 2**, backup format 1 to 2, and the
installation data contract from **4 to 5**. Opening an old shared-password profile
performs the migration, even before unlocking it. Keep the pre-upgrade backup.

An older app cannot open the migrated profile or restore a new format-2 backup.
The installer and launcher also refuse incompatible downgrade/rollback. Do not
edit format numbers or copy an old executable over the installed application.

To return to an older beta, use its separate compatible installation and restore
the **pre-upgrade format-1 backup into a fresh empty profile**. Keep the newer
profile intact. That old backup cannot contain addresses, searches or payments
created after it was made. Continue using this release or a newer compatible
version for those records and keys. Do not use an old backup to assume a newer
payment was never sent; reconcile its status before sending again.

## Beta qualification limits

CPU and NVIDIA OpenCL searches have been exercised. AMD/Intel GPU coverage,
additional hardware qualification, extended soak, and physical suspend/driver
reset testing are deferred for this beta at the release owner's direction.
Unsupported or unstable OpenCL configurations can use CPU mode.

The installer uses project Ed25519 release/update signatures and checksums. It
does not carry a Microsoft Authenticode publisher certificate. Verify downloads
using the included release verification instructions. Existing beta limitations
continue to apply; do not treat this beta as broad hardware certification.

## Published delivery

The tag identifies source `72dd0523275498dca9fd51ba72b6024f25474d25` from
[passing CI 42228951349](https://github.com/Wartnode/wartnode-desktop/actions/runs/42228951349).
All six release assets were downloaded anonymously, matched to the signed
delivery and verified by the packaged verifier. The actual 0.5.0-beta.3 to
0.6.0-beta.1 installer rehearsal passed 11 scenarios on Windows 11 Pro build 26300 with
synthetic offline profiles. This is a prerelease, not marked latest; automatic
discovery remains disabled. Previous releases and their assets are unchanged.
