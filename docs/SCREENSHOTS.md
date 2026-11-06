# Wartnode-Desktop screenshots

A tour of the six main pages and the Sign / Verify workspace, captured on
November 5, 2026. The signing screenshots show **v0.5.0-beta.3**, Mine shows
**v0.5.0-beta.2**, and the remaining main-page screenshots show **v0.5.0-beta.1**.
Balances, block heights, network statistics and mining readings reflect the
capture sessions.

[Overview](#overview) · [Node](#node) · [Wallet](#wallet) · [Sign and Verify](#sign-and-verify) · [Mine](#mine) · [Explorer](#explorer) · [Settings](#settings)

## Overview

See the current connection status, combined available WART across your profile,
and solo mining performance at a glance. Network cards show estimated hashrate,
average block time, difficulty in GH/TH and the latest block reward. A mining
performance chart and profile activity complete the dashboard.

![Overview showing a synced connection, combined balance, active solo mining, network statistics and a hashrate chart](screenshots/overview.png)

## Node

Choose between Full node and Lite wallet connections, and manage your local
node with initialization, start, stop and resync controls. Block height, peers,
uptime, storage, memory and connection details are shown together, with the
node log available in a separate window.

![Node page showing Full node mode, ready status, node controls, peer count, uptime and resource statistics](screenshots/node.png)

## Wallet

Manage labeled addresses under one profile password and view their combined
available balance. Create or import addresses, select a default or mining payout
address, and access Send, Receive, encrypted profile backups, the address book
and activity history.

![Wallet page showing the combined balance, labeled address list, address book and activity tabs, backup controls and address actions](screenshots/wallet.png)

## Sign and Verify

Open **Wallet → My addresses → Sign / Verify** to sign a message or file locally,
or verify a proof against an independently obtained expected address. This works
in Full node and Lite modes; public verification needs no wallet unlock.

### Sign a message

Choose an owned address, enter the message, then review and approve it. Copy the
resulting public signature document or save it as a `.wartproof` file. This
capture shows a signed message with its proof copied for sharing.

![Sign tab showing a message, the signing address, a copied public signature document, and save and verification controls](screenshots/sign-message.png)

### Verify a message

Enter the address you expect and paste or open the signature document. A
successful check displays **Verified for this address** and the signed content.

![Verify tab showing the independently expected address, signature document, successful verification and the signed beta.3 message](screenshots/verify-message.png)

See the [format and usage guide](SIGNED_PROOFS.md) for file proofs, compatibility
and wallet-claim challenges.

## Mine

Choose Eco 30%, Balanced 60%, Heavy 90% or Custom settings, with configuration
and the mining console in separate windows. While mining, the page presents
Janus, GPU SHA and CPU Verus hashrates, a performance chart, available GPU
sensors, and miner CPU and memory readings. Reward history for the payout
address appears below. This capture shows an active mining session with live
hashrates, session counters, a performance chart and supported telemetry.

![Mine page showing an active Balanced mining session, live Janus, GPU SHA and CPU Verus hashrates, a performance chart, telemetry and payout-address reward history](screenshots/mine.png)

## Explorer

Browse the latest 25 blocks and pause updates while inspecting a selected
block. Search by block height, transaction hash or address, and view block
details below the list. Open wartnode.com with the website button, by
double-clicking a height, or with the selected-height action.

![Explorer showing the latest blocks, search and pause controls, selected block details and links to wartnode.com](screenshots/explorer.png)

## Settings

General preferences control Windows startup, closing to the system tray, the
start page and appearance. Security, Storage, Updates and About group the
remaining profile and application preferences.

![Settings page showing General preferences for startup, the system tray, start page and appearance, with category navigation](screenshots/settings.png)

[Back to README](../README.md) · [User guide](MVP_GUIDE.md)
