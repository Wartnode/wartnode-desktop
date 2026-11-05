# Wartnode-Desktop quick start

Install Wartnode-Desktop per user on Windows 11 x64, starting with 21H2/build 22000. ARM and Windows Server are unsupported. This beta has no Authenticode signature; project release signatures are verified separately. You do not need Python, Git, Docker, or a CUDA Toolkit. For the portable ZIP, extract the whole folder and run WartNode.exe with its helper and _internal folder beside it.

[Windows compatibility policy and measured coverage](WINDOWS_COMPATIBILITY.md) distinguish the supported Windows 11 family from the specific builds tested.

## Profile and addresses

Opening the app starts neither the node nor the miner. On first use, set one profile password of at least eight characters. It protects every address you create or import. Unlock once to manage addresses or send payments. Each new address has its own 24 recovery words; store these offline and check imported addresses against your original records.

Wallet → My addresses provides labels, a default address, copy/receive actions, and a mining payout shortcut. Wallet → Address book stores labeled recipients. Public addresses, QR codes, recorded activity, and last-known balances remain viewable while the profile is locked. Available WART combines all unique owned addresses after pending/uncertain payment reservations; an incomplete refresh never appears as a complete total.

This version uses a new profile format. It leaves old-format wallet databases untouched and asks you to select a fresh folder. There is no automated wallet migration or old backup importer. Export your recovery phrases/private keys with the original application and import them manually into the fresh profile. Verify every address before receiving funds. The default launch remembers the selected fresh folder; an explicit --data-dir continues to select that folder directly.

Wallet → Back up profile saves a .wartprofile file encrypted with the current profile password. It includes all addresses, labels, contacts, and durable payment state. Restore into an empty profile and unlock with the password used for that backup. Settings → Security can change the profile password; make a new backup afterwards. Older backups continue to use their original password. Machine preferences are separate; public activity is rediscovered from the selected node after restoration.

## Payments

Choose Send, select an owned address, enter a recipient (or choose a contact), and review the recipient, amount, fee, and total before confirming. The selected transaction fee is **0.00009992 WART**. Send Max deducts that fee and funds reserved for other payments. A selected backend requiring a higher fee is refused with an explanation rather than silently increasing the fee.

A submitted payment still needs confirmation. Pending and uncertain payments keep their funds reserved; check Wallet → Activity before sending again. Wartnode-Desktop never retries a payment automatically. Payment review explicitly reveals amounts even when Hide balances is enabled so spending can be authorized accurately.

## Full node or Lite

Choose the connection on **Node**, then Apply connection. Full node uses the managed local node. Initialize node selects an empty chain folder and either syncs from genesis or imports a standalone chain database from a cleanly stopped compatible node or SQLite backup. Import checks format, chain anchor, and integrity, copies with progress/cancellation, and keeps the original. These checks do not independently validate all imported history. Select Start node explicitly afterwards. The managed node minimum fee is **0.00009992 WART**.

Lite uses HTTPS mainnet.wartnode.com and the Wartnode explorer service. Keys and signing stay on this computer. The services can see public addresses you query and supply balances, fees, and confirmations; Lite does not independently validate the chain and cannot solo mine. Switching locks the profile, stops managed mining/node work, and invalidates payment reviews. Failures never cause an automatic connection switch.

Node shows height, peers, uptime, storage, and available process resources. Node log opens separately. Network statistics on Overview use the selected connection, label estimates and stale data, and display difficulty in GH or TH with the exact value in a tooltip.

## Mining

The miner uses GPU + CPU through driver OpenCL. Choose **Eco 30%**, **Balanced 60%** (default), or **Heavy 90%**. Preset buttons open the settings popup for review. Custom allows independent CPU/GPU ceilings from 5% to 95%. Save applies an idle configuration; Apply and restart stops a running miner before applying reviewed changes. Cancel discards pending edits. Old preset preferences retain their original effective limits as Custom.

CPU quotas cover the entire miner process, including GPU driver work. GPU pacing covers this miner's scans. Actual usage can be lower when one stage waits for the other. The app does not change clocks, voltages, drivers, or other applications. It has no CPU-only, remote pool, or developer-fee mode.

Mine emphasizes Janus/GPU SHA/CPU Verus rates, bounded history charts, session counters, uptime, and supported telemetry. Missing/stale sensors show unavailable. CPU temperature/power are unavailable on this build. On ambiguous identical GPUs, sensor readings stay unavailable instead of being assigned to the wrong card. The mining console opens separately.

Blocks mined by payout address persists public reward occurrences across app sessions, including an external payout address. Rewards are checked against canonical blocks and marked confirmed, orphaned, or unverified. Totals say Partial history until address-history loading is complete; session accepted-work counters are separate from confirmed rewards. Clearing Explorer's cache keeps this reward store. Hardware qualification currently covers Ryzen 7 5800X / RTX 4060; other configurations require testing.

## Explorer

Explorer shows the latest 25 blocks, newest first. New blocks preserve the selected block and scroll position. Pause stops the displayed list from moving; Resume catches up. Search height, block/transaction hash, or address. Older results continues a search page. Different transfers can share a transaction hash, so check the full occurrence.

The wartnode.com button opens the mainnet explorer. Double-click a height to open its block there; select a row for in-app details. Keyboard users can use Open selected height on wartnode.com. Hosted/offline/incomplete results identify their scope. Reorganizations require a fresh search.

The disposable cache is bounded to 128 blocks, 16 MiB compressed payload, and a 32 MiB database. It does not build a second full-chain index. Lite needs no local chain storage. Settings → Storage → Clear explorer cache preserves profiles, payment reservations, reward history, labels, and node data.

## Preferences and recovery

Settings uses General, Security, Storage, Updates, and About categories. Launch at Windows startup is an installed-only, per-user opt-in that opens the desktop without starting a node/miner. Close to system tray defaults on and locks the profile; turn it off for orderly Exit on close. If the tray is unavailable, close exits. Start page applies on the next launch. Dark/System follows Windows appearance, with native contrast handling.

Automatic lock supports 5 minutes, 15 minutes (fresh default), 1 hour, or Never. Explicit locking, Windows lock/disconnect/logoff, closing, and mode changes always lock. Hide balances masks amounts in profile views, activity, rewards, and Explorer without changing accounting.

Storage shows the actual active profile folder and opens it in the file manager. Blockchain moves require stopped mining/node, an empty separate local folder, and free-space headroom. Interrupted moves resume. Resync on Node backs up chain files before a fresh sync. Low disk stops mining and the node; free space before restarting. Profile/payment data stay in the profile folder.

Unreadable settings offer Restore previous settings or Reset setup, preserving the original file and wallet/chain data. Reset uses the default chain folder, so retain the original storage location. Logs keep the latest 250 lines, with follow/filter/copy/export controls in separate popups.

Updates preserve authenticated downloads, file verification, version/replay checks, staged explicit installation/restart, and compatible rollback. Automatic checking never installs or restarts. Channel choices appear only for actual configured signed feeds. This beta has discovery disabled and a read-only Beta channel. The new data contract prevents rollback to pre-profile versions; no incompatible wallet converter is run.

Exit Wartnode-Desktop stops owned processes. About shows version/components and privacy-filtered diagnostics, excluding keys, passwords, addresses, contacts, transactions, raw logs, and personal paths. Uninstall keeps profile and chain data and removes this installation's startup registration. Reinstall a compatible version to use the profile again.
