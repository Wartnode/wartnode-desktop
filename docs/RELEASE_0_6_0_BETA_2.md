# Wartnode-Desktop 0.6.0-beta.2 candidate

This candidate is unpublished. The current public beta remains 0.6.0-beta.1.

## Per-address private keys

In **Wallet → My addresses**, select an address and choose **Private key…**.
Unlock the profile if needed, then confirm the profile password to reveal that
address's private key. **Copy private key** copies the complete 64-character
hexadecimal key for another Warthog wallet that accepts private-key imports.
This works for imported, normally generated and saved vanity addresses.

Anyone with the key can spend funds from that address. Closing the dialog or
locking the profile clears its display and the current matching clipboard copy.
The matching clipboard copy also expires after 60 seconds. Clipboard history,
sync and copies retained by other apps remain outside Wartnode's control.

Exporting a key leaves the address in this profile. Both wallets can control the
same address after import. It does not recreate the original recovery words or
export labels, contacts, payments or unfinished searches; retain encrypted
profile backups for complete profile recovery. Verify the imported address
matches the original before relying on the destination wallet.

## Upgrade and recovery

Back up the profile in the existing app before upgrading, then exit the app.
The candidate keeps **data contract 5, profile format 2 and backup format 2**,
matching 0.6.0-beta.1. This feature adds no database migration. Compatible
application rollback must preserve the installed version high-water mark;
an older installer is still refused after a newer version has been installed.

Users upgrading from a 0.5.0 beta still need the
[format-1 upgrade and downgrade guidance](RELEASE_0_6_0_BETA_1.md). Keep its
pre-upgrade backup separately: versions older than 0.6 cannot read format 2.

Automatic update discovery remains disabled. Candidate validation and eventual
release signing/publication are separate steps; these notes do not identify a
published or signed download.
