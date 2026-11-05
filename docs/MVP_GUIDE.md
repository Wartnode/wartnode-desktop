# Wartnode-Desktop quick start

Run the **Wartnode-Desktop setup** installer, then open Wartnode-Desktop from the Start menu. Installation is per user and does not need administrator access. Windows 11 x64, version 24H2 or later (build 26100 or newer), is required. ARM and Windows Server are not supported. This beta is not Windows Authenticode signed; verify its accompanying project-signed manifests. You do not need Python, Git, Docker or a CUDA Toolkit.

The portable ZIP is also available: extract the full folder and open **WartNode.exe**. Keep `_internal` and `WartNodeHelper.exe` beside it. In-app updates require the installed version.

If security software quarantines a file, keep the report for troubleshooting. Do not disable protection or add an exclusion to run Wartnode-Desktop.

## Wallet and node

Opening the app never starts a node or mining. You can create, import or restore a wallet immediately.

Choose **Node → Initialize node…** to select an empty blockchain folder and either **Sync from genesis** or **Import chain database**. Import accepts a standalone SQLite chain database from a cleanly stopped compatible node or a SQLite backup. Choose the file, check its detected height, and confirm that you trust its source. The app checks the format, chain anchor and integrity, then copies it with progress and cancellation. These checks do not independently validate all imported history. The original file and wallet records are kept. Allow room for the database plus 1 GiB of headroom; a full chain needs more space as it grows. Select **Start node** after initialization to sync or catch up.

For a wallet without local chain storage, choose **Settings → Connection → Lite wallet**, then **Apply connection**. Lite connects over HTTPS to **mainnet.wartnode.com** and uses the Wartnode explorer service for transaction discovery. Your keys and signing stay on this computer. The services can see queried public addresses and supply balances, fees and confirmations; this mode does not independently validate the chain. Solo mining is unavailable. Changing connections locks the wallet and requires a fresh payment review. No connection changes happen automatically after a service failure.

Wallets, payment history, settings and backups stay in `%LOCALAPPDATA%\WartNode` by default. Only blockchain storage moves. Under **Settings → Storage**, stop the node and miner, then choose **Change blockchain folder…** and select an empty local folder. Keep the app open while it moves data. An interrupted move resumes before the node starts. The node stops if free space falls below 512 MiB; free space and restart it.

If settings become unreadable, Wartnode-Desktop offers **Restore previous settings** or **Reset setup** and keeps the original file. Resetting uses the default blockchain folder and does not remove existing chain files. Encrypted wallet backups protect wallet records; contacts and preferences are stored separately in the settings file.

Open **Wallet** to create a wallet, import a private key or Warthog Desktop recovery phrase, or restore a backup. You can do this while the node syncs. Check that an imported wallet's address matches the address you used before.

Write down a new wallet's 24 recovery words and store them offline. Anyone with the phrase can access the wallet. Use **Back up** to save an encrypted backup. Keep the backup password and the original wallet passwords; both are needed to restore and unlock the wallets.

Some older Warthog Desktop phrases fail the current checksum check. Wartnode-Desktop asks before importing one. Keep the spelling and spacing exactly as entered in the original wallet, and check the address afterward. **Restore backup** also accepts an older wallet database and keeps the original file.

Use **Receive** to copy your address or show its QR code. Under **Send**, choose **Review payment** to check the recipient, amount, fee and total before entering your wallet password. **Send Max** deducts the fee and funds reserved for other payments.

Use **Save contact…** to keep a recipient address. **Choose contact…** fills the recipient field; always check the full address during payment review. **Payments** shows outgoing payments. **View activity and rewards** opens incoming transfers and mining rewards in Explorer.

A submitted payment still needs a block confirmation. If submission is uncertain, check **Payments** before sending again. Funds stay reserved while the result is uncertain. Wartnode-Desktop does not retry payments automatically.

## Solo mining

Mining uses a GPU and CPU together. Select a mode, GPU and payout address, then choose **Start mining**. **Normal** is the default, with CPU and GPU limits of 50%. **Light** sets both to 20%; **Heavy** sets both to 80%. Override either percentage from 5% to 95% to use **Custom**. Changes are saved; stop mining before changing limits. CPU limits cover the miner process, including its GPU driver work. GPU limits pace mining activity. Actual usage may be lower when one stage waits for the other; these settings do not limit other apps or node synchronization. A graphics driver with OpenCL support is required. Wartnode-Desktop does not change your clocks, voltages or drivers.

The included miner works only with Wartnode-Desktop's local node and charges no developer fee. It has no CPU-only mode or external pool option. Hardware testing so far covers a Ryzen 7 5800X and RTX 4060; other combinations still need testing.

Janus is the combined mining rate. GPU SHA and CPU Verus show individual stages. Accepted work is not a mining reward; a reward must appear in a confirmed block.

## Explorer and recovery

Search **Explorer** by block height, block hash, transaction hash or address. Use **Older results** for the next page. Different transfers can share a transaction hash, so check the sender and recipient. If the chain changes during a search, search again.

Double-click a height, address or transaction hash in a result table to open its details.

Older records load from the selected node. Lite transaction searches also use the hosted explorer and show when coverage is incomplete. The explorer keeps a small cache of recent blocks rather than building another index of the whole chain. The cache is limited to 128 blocks, 16 MiB of compressed data and a 32 MiB database. Temporary files and old-cache migration can need extra disk space. Full node mode still stores the full blockchain; Lite does not create a local chain database.

**Settings → Clear explorer cache** removes cached blocks while keeping wallets, payment history and node data. Offline results show only what is cached.

**Node** shows sync progress, peers and logs. **Resync blockchain…** stops the node and miner and backs up the existing chain files. Start the node afterward to download the chain again. Wallets and payment history are kept. An interrupted backup resumes the next time the node starts.

Logs follow new entries by default. Scroll up to pause following; return to the bottom to resume. The screen shows the latest 250 lines. Recent log files are also kept in the data folder.

## Closing and support

Closing the window locks the wallet and leaves Wartnode-Desktop running in the tray. Choose **Exit Wartnode-Desktop** to stop the app, node and miner. The wallet also locks with Windows and after inactivity. Startup with Windows and automatic mining are not available.

**Settings → About** shows local connection ports. **Export diagnostics…** saves version, OS, CPU/GPU, storage and node-status information without wallet keys, passwords, addresses, transaction data, raw logs or personal folder paths. **Component details** shows bundled versions, source revisions and file hashes.

## Installation, updates and recovery

Back up your wallets before changing versions. Use **Exit Wartnode-Desktop** before running an installer to upgrade or repair it. Running the same installer repairs application files. Uninstall keeps wallet data, payment history and custom blockchain storage; reinstall to use them again.

**Settings → Updates** provides an explicit check and **Update and restart…** when publisher settings are configured. This first beta leaves automatic discovery disabled. Upgrade using the installer from https://github.com/Wartnode/wartnode-desktop/releases. Do not replace those settings with a key supplied by a download.

Configured updates verify the publisher signature, version, Windows/data compatibility and every file before stopping the node and miner. The previous compatible application is retained. The 0.4 connection and startup contract prevents rollback to the 0.3 series, which ignores Lite settings. Failed or interrupted startup returns to it. **Return to previous version…** is available after a compatible upgrade. Wallets and pending or uncertain payments remain in the data folder.

An older installer is rejected when a newer version is installed. Do not manually downgrade across incompatible wallet, node or cache formats. Incompatible data changes require a matching backup and a separately qualified migration; this updater accepts only the current data contract.
