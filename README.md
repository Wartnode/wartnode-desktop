# Wartnode-Desktop

A Warthog wallet, explorer, full node and solo miner for Windows 11.

[Download the beta](https://github.com/Wartnode/wartnode-desktop/releases/tag/v0.4.0-beta.1) · [User guide](docs/MVP_GUIDE.md) · [Build from source](docs/BUILD.md) · [Report an issue](https://github.com/Wartnode/wartnode-desktop/issues)

Wartnode-Desktop supports two connection modes. **Lite wallet** connects to
`mainnet.wartnode.com` without downloading the blockchain. **Full node** stores
and validates the chain locally and enables solo mining. Wallet keys and signing
stay on your computer in both modes. Opening the application starts neither a
node nor a miner.

## Requirements

- Windows 11 x64, version 24H2 or later, build 26100 or newer.
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
| `Wartnode-Desktop-0.4.0-beta.1-windows-x64-setup.exe` | Per-user installation with a Start menu shortcut. |
| `Wartnode-Desktop-0.4.0-beta.1-windows-x64.zip` | Portable application; extract the complete folder and run `WartNode.exe`. |
| `Wartnode-Desktop-0.4.0-beta.1-source.zip` | Matching application source, locked upstream inputs and build recipes. |
| `SHA256SUMS` and signed manifests | Download integrity and project-signature verification. |

1. Download the installer and review the [verification instructions](docs/RELEASE_SIGNING.md).
2. Run the installer. Administrator access is not required.
3. Open **Wartnode-Desktop** from the Start menu.
4. Choose a connection mode and create, import or restore a wallet.

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

Open **Settings → Storage → Connection**, select **Lite wallet**, then choose
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

## Use the wallet

- **Create or restore:** open **Wallet** to create a wallet, import a private key
  or recovery phrase, or restore an encrypted backup. Record new recovery words
  offline and keep encrypted backups and their passwords separately.
- **Receive:** choose **Receive** to copy your address or display its QR code.
- **Send:** enter the recipient and amount, then choose **Review payment**. Check
  the address, amount and fee before entering the wallet password.
- **Send Max:** sends the available balance after fees and funds reserved for
  other payments.
- **Check a payment:** use **Payments**. An uncertain submission stays reserved
  and is not retried automatically; check its status before sending again.

See the [user guide](docs/MVP_GUIDE.md) for backup restoration, contacts, payment
states and recovery procedures. Never include recovery words, private keys,
wallet passwords or wallet databases in an issue report.

## Solo mining

Solo mining requires Full node mode and a ready managed local node. Select your
payout address and GPU under **Mine**, choose a resource mode, then select
**Start mining**. Mining uses the CPU and GPU together; there is no CPU-only
mode, pool option or developer-fee redirection.

| Mode | CPU limit | GPU activity limit |
| --- | --- | --- |
| Light | 20% | 20% |
| Normal — default | 50% | 50% |
| Heavy | 80% | 80% |
| Custom | 5–95% | 5–95% |

CPU limits apply to the miner process. GPU limits pace mining work; observed
utilization depends on the hardware and workload. Stop mining before changing
these settings. The application does not alter drivers, clocks or voltages.

## Explorer

Search by block height, block hash, transaction hash or address. Open linked
records to inspect transfers, confirmations and mining rewards. The explorer
queries the selected backend on demand and keeps a bounded cache; it does not
build another full-chain index. Offline results cover only cached records.

## Data, updates and closing

Wallets, settings and payment history are stored in `%LOCALAPPDATA%\WartNode`.
Blockchain storage can be placed in a separate directory during initialization.
Uninstalling the application preserves these data files.

Use **Exit Wartnode-Desktop** before installing an update or repairing the
application. Closing the window locks the wallet and leaves the application in
the tray. Explicit Exit stops its managed node and miner.

Automatic update discovery is disabled for this first beta. Download a newer
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
