# Wartnode-Desktop

The [**0.6.0-beta.1 release**](https://github.com/Wartnode/wartnode-desktop/releases/tag/v0.6.0-beta.1) adds vanity addresses in Wallet. See the
[feature, upgrade and downgrade notes](docs/RELEASE_0_6_0_BETA_1.md).

The unpublished [0.6.0-beta.2 candidate](docs/RELEASE_0_6_0_BETA_2.md) adds password-confirmed private-key reveal and copy for individual wallet addresses. Current public downloads remain 0.6.0-beta.1.

A Warthog wallet, explorer, full node and solo miner for Windows 11.

[Download the beta](https://github.com/Wartnode/wartnode-desktop/releases/tag/v0.6.0-beta.1) · [Screenshots](docs/SCREENSHOTS.md) · [User guide](docs/MVP_GUIDE.md) · [Build from source](docs/BUILD.md) · [Report an issue](https://github.com/Wartnode/wartnode-desktop/issues/new/choose)

Wartnode-Desktop supports two connection modes. **Lite wallet** connects to
`mainnet.wartnode.com` without downloading the blockchain. **Full node** stores
and validates the chain locally and enables solo mining. Wallet keys and signing
stay on your computer in both modes. Opening the application starts neither a
node nor a miner.

## Requirements

- Windows 11 x64, starting with 21H2/build 22000. All Windows 11 feature releases are targeted; see [compatibility and tested coverage](docs/WINDOWS_COMPATIBILITY.md).
- An internet connection for synchronization and live wallet/explorer data.
- For solo mining: a supported CPU and GPU together, with an OpenCL-capable
  graphics driver. Python, Git, Docker and the CUDA Toolkit are not required to
  use the packaged application.
- For Full node mode: enough local disk space for the growing blockchain.
  Lite mode does not store a local chain database.

Windows 10, Windows Server and Windows on ARM are not supported.

## Download and install

Download files from this repository's [Releases page](https://github.com/Wartnode/wartnode-desktop/releases).

| Download | Use |
| --- | --- |
| `Wartnode-Desktop-0.6.0-beta.1-windows-x64-setup.exe` | Per-user installation with a Start menu shortcut. |
| `Wartnode-Desktop-0.6.0-beta.1-windows-x64.zip` | Portable application; extract the complete folder and run `WartNode.exe`. |
| `Wartnode-Desktop-0.6.0-beta.1-source.zip` | Matching application source, locked upstream inputs and build recipes. |
| `SHA256SUMS` and signed manifests | Download integrity and project-signature verification. |

1. Download the installer and review the [verification instructions](docs/RELEASE_SIGNING.md).
2. Run the installer. Administrator access is not required.
3. Open **Wartnode-Desktop** from the Start menu.
4. Set one profile password, then create or import your addresses under **Wallet**.
5. Choose Full node or Lite on **Node**.

The beta installer has project-controlled signatures in its accompanying
manifests; it does not have a Windows Authenticode publisher certificate.
Windows may show an unknown-publisher or SmartScreen notice. Project signatures
do not establish Microsoft publisher reputation. Keep security protection enabled.

For the portable version, keep `_internal` and `WartNodeHelper.exe` beside
`WartNode.exe`; do not copy only the executable.

## Choose a connection mode

| | Lite wallet | Full node |
| --- | --- | --- |
| Wallet keys and signing | Local | Local |
| Chain data | Hosted node/explorer | Managed local node |
| Local blockchain storage | None | Full blockchain |
| Solo mining | Unavailable | Available with supported CPU + GPU |

### Lite wallet

Open **Node**, select **Lite wallet**, then choose
**Apply connection**. The application uses the hosted node at
`mainnet.wartnode.com` and the Wartnode explorer service. These services can see
queried public addresses and provide chain data; Lite mode does not independently
validate the blockchain. A service failure does not switch to another backend.

### Initialize a full node

1. Open **Node → Initialize node…**.
2. Choose an empty local directory for the blockchain.
3. Choose **Sync from genesis** or **Import chain database**.
4. After initialization, select **Start node**.

Import accepts a compatible standalone SQLite chain database from a cleanly
stopped node or a SQLite backup. The application checks its format, chain anchor
and database integrity, then copies it without changing the original. Import
requires trust in the file's source; these checks do not independently replay
all imported history. The node remains stopped until you start it.

## Dashboard and wallet

Overview shows the current connection status, combined available WART across your
owned addresses after payment reservations, mining performance, network statistics
and profile activity. Difficulty uses GH/TH; its exact value is available in a tooltip.
Node provides Full/Lite selection, chain/resource statistics and a separate log window.

- **One profile password:** set it on first use, then unlock once to create/import
  multiple addresses or send. **New address** provides recovery words; vanity
  addresses use encrypted profile backups.
- **Organize:** label owned addresses, choose a default or mining payout, and keep
  saved recipients in **Wallet → Address book**.
- **Receive:** select an address to copy it or display its QR code. Public address
  information and recorded balances remain viewable while the profile is locked.
- **Send:** choose the source address and recipient, then review the amount,
  **0.00009992 WART** fee and total before confirming. A backend requiring a higher
  fee is refused with an explanation. Combined profile funds do not mean a single
  transaction can spend multiple source addresses.
- **Send Max:** deducts the fee and funds reserved for other payments.
- **Activity:** uncertain payments stay reserved and are never retried automatically.
  Incoming transfers and rewards are reconciled against canonical blocks.
- **Sign / Verify:** prove address control with a message or file, then verify
  against an independently expected address. Works locally in Full node and Lite,
  including verification while locked. See the [format and guide](docs/SIGNED_PROOFS.md).
  [View signing and verification screenshots](docs/SCREENSHOTS.md#sign-and-verify).
- **Vanity address:** choose Prefix, Suffix or Prefix + suffix in Wallet. View
  difficulty and local time estimates; pause and resume saved searches. Mining
  must stay stopped above eight combined characters. See the [guide](docs/MVP_GUIDE.md#vanity-addresses).
- **Back up profile:** an encrypted .wartprofile backup covers every address,
  label, contact, durable payment record and pending/found vanity search. Restore
  into an empty profile. Vanity addresses have no recovery words.

### Upgrading from 0.5.0 betas

**Make an encrypted profile backup in the old version before upgrading and keep
it separately.** Exit the application completely, then run the new installer.
Shared-password profiles migrate automatically from format 1 to 2; addresses,
labels, contacts and payment records are retained. No wallet transfer or chain
resync is required. Make a new backup after upgrading.

Older versions cannot open the migrated profile or restore new format-2 backups.
Downgrade recovery requires the pre-upgrade backup and a fresh compatible
profile; keep the newer profile and any keys/payments created since that backup.
See the [upgrade and downgrade instructions](docs/RELEASE_0_6_0_BETA_1.md).

### Upgrading from 0.4.0-beta.1

This beta introduces a fresh shared-password profile. **There is no automatic wallet
migration or legacy backup importer.** Before upgrading, preserve your existing
backup and export the recovery phrases/private keys using the original application.
The new app leaves the old database untouched and asks you to choose a fresh folder.
Import your addresses manually, verify them against your originals, then create a new
profile backup. Compatible repair/reinstall preserves the new profile; rollback to
an older data format is blocked.

See the [user guide](docs/MVP_GUIDE.md) for password changes, backups, payment
states and recovery. Never include recovery words, private keys, wallet passwords
or wallet databases in an issue report.

## Solo mining

Solo mining requires Full node mode and a ready managed local node. Select your
payout address and GPU in the **Mine → Settings** popup, choose a resource mode, then select
**Start mining**. Mining uses the CPU and GPU together; there is no CPU-only
mode, pool option or developer-fee redirection.

| Mode | CPU limit | GPU activity limit |
| --- | --- | --- |
| Eco | 30% | 30% |
| Balanced — default | 60% | 60% |
| Heavy | 90% | 90% |
| Custom | 5–95% | 5–95% |

CPU limits apply to the miner process. GPU limits pace mining work; observed
utilization depends on the hardware and workload. Preset buttons open a review
popup; Apply and restart stops a running miner before changing its configuration.
Existing saved limits retain their original values as Custom. The application does
not alter drivers, clocks or voltages.

Mine emphasizes hashrate charts, session statistics, supported CPU/GPU telemetry and
chain-confirmed rewards for the selected payout address across sessions. Missing
sensors show unavailable; partial reward coverage is labeled. Accepted miner work
is separate from confirmed blocks. The mining console opens in its own window.

Brief node sync transitions no longer permanently stop mining. Running miners
have a five-second grace period; longer temporary interruptions show **Waiting
for node** and resume after two healthy checks using the reviewed payout and
limits. Explicit Stop actions cancel recovery. Mining does not resume after
reopening the application. See [mining recovery](docs/MINING_RECOVERY.md).

## Explorer

Browse the latest 25 blocks, newest first, with Pause/Resume and stable selection.
Search by height, block hash, transaction hash or address. Open wartnode.com with
the website button; double-click a height to open its block there. A labeled action
also supports keyboard navigation. Offline results identify their cached scope.
The bounded cache does not build another full-chain index.

## Data, updates and closing

Wallets, settings and payment history are stored in `%LOCALAPPDATA%\WartNode`.
Blockchain storage can be placed in a separate directory during initialization.
Uninstalling the application preserves these data files.

Settings groups General, Security, Storage, Updates and About into compact rows.
Startup launch is opt-in and starts no node/miner. Configure the start page,
Dark/System appearance, balance privacy and automatic lock period. Closing locks
the profile and follows your tray preference; explicit Exit stops owned processes.
Use **Exit Wartnode-Desktop** before installing an update or repairing the app.

Automatic update discovery is disabled for this beta. Download a newer
installer from Releases to upgrade. See [release verification](docs/RELEASE_SIGNING.md)
and the [user guide](docs/MVP_GUIDE.md) before changing versions.

## Beta status

This is a public beta, not a stable-release certification. Extended physical
GPU/driver testing, the remaining Windows/accessibility matrix and the 72-hour
integrated soak were deferred for this beta. See [beta notes](docs/BETA.md) for
the tested scope and known limitations.

## Development and contributions

The public repository uses `main`. [Build instructions](docs/BUILD.md) describe
the pinned sources, Windows toolchain and GitHub Actions pipeline. Automated
regression test code is included; collected qualification data, VM images,
wallets, logs and private development history are excluded.

Use [Issues](https://github.com/Wartnode/wartnode-desktop/issues) for reproducible
bugs and feature requests. Include the application version, Windows build,
reproduction steps and redacted diagnostics from **Settings → About**.

## License and components

Wartnode-Desktop is licensed under [GPL-3.0-or-later](LICENSE). It uses pinned
official [Warthog core](https://github.com/warthog-network/core), local wallet
primitives and an in-house clang-cl/OpenCL miner derived from BRminer. Component
and dependency notices are retained in [`notices`](notices). Matching source and
third-party source access are described in [SOURCE_ACCESS.md](docs/SOURCE_ACCESS.md).
