# Verify a release

Download the installer or portable ZIP, matching source ZIP, `SHA256SUMS`,
`release-envelope.json` and `update-envelope.json` from the same release in
[Wartnode/wartnode-desktop](https://github.com/Wartnode/wartnode-desktop/releases).

## Checksums

PowerShell can show a file's SHA-256 value:

```powershell
Get-FileHash .\Wartnode-Desktop-0.4.0-beta.1-windows-x64-setup.exe -Algorithm SHA256
```

Compare it with `SHA256SUMS`. A matching checksum detects corruption; project
signatures establish that the files were approved by the publisher.

## Project signatures

Release and update manifests use separate Ed25519 keys. The SHA-256 fingerprints
of the trusted raw public keys are:

| Role | Fingerprint |
| --- | --- |
| Release | `d5a20006f0fc5b850e8de7c8836df1c33af54c1fa240a2026f5dd878a9f2bd52` |
| Update | `b5a47f74a13d9178fc348a1a48deefb376989345a4d6bad1aaa8bfaeac1884a9` |

Use a trusted checkout or an already trusted installation to verify a download.
Do not establish trust by taking replacement keys from an unverified download.
With the locked Python dependencies installed in a trusted checkout:

```powershell
python scripts/verify-release.py .\downloads\release-envelope.json --directory .\downloads
```

An already trusted installation can use:

```powershell
WartNodeHelper.exe --verify-release .\downloads\release-envelope.json --release-directory .\downloads
```

Verification checks both signatures, exact installer/portable/source hashes,
version and Windows/data compatibility, matching source and CI identities,
freshness and replay protection. Manifests are time-limited; use current release
metadata if verification reports expiry. Expiry does not disable an installed
wallet or remove its data.

## Windows publisher identity

This beta's executable and installer are not Windows Authenticode signed.
Windows may report an unknown publisher or show SmartScreen. Project signatures
do not provide Microsoft publisher trust. Keep protection enabled and obtain
files from this repository's Releases page.

## Maintainer process

Only passing `main` push builds from this repository can be signed. CI prepares
exact unsigned files and a request. On the signing Windows PC:

```powershell
python scripts/sign-release-run.py --run-id RUN_ID --expected-commit FULL_COMMIT_SHA --out work/release-RUN_ID
```

Release/update private keys stay in a current-user DPAPI-protected store outside
the repository. They are not GitHub secrets or build inputs. Preserve the
encrypted recovery backup and its password separately. Rotation requires an
explicit new trust decision; a download cannot replace pinned trust on its own.

The first public beta leaves automatic update discovery disabled. A beta
signature is a beta publication decision, not stable-release certification.
