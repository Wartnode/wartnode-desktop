# 0.4.0-beta.1

The first public beta provides a local wallet and explorer, explicit local node
initialization with optional chain-file import, Lite access through
`mainnet.wartnode.com`, and local-node-only solo mining through driver OpenCL.
The CPU VerusHash stage retains the clang-cl build. There is no CPU-only mode,
external pool option or mining fee.

## Scope

The owner approved publishing this beta while deferring additional physical
GPU/driver qualification, the remaining Windows/accessibility matrix and the
72-hour integrated hardware soak. These checks are deferred, not passed. They
remain requirements for broader stable-release claims.

The development baseline passed clean Windows 11 Pro VM functional checks,
wallet/payment recovery, node initialization and standalone chain import,
Lite/explorer behavior, installed update/rollback/repair, standard-user isolation,
Defender scanning and native 100%/150% display review. The full VM baseline was
26H2 build 26300.9457; post-update data/launcher/signature recovery checks passed
on 26300.9711. Those results describe the development baseline, not a new clean
Windows certification of every beta build. The beta's exact source and artifacts
are bound to its GitHub Actions run and signed manifests.

## Known limits

- Windows 11 x64 24H2 or later is required; the complete edition/build and
  hardware matrix has not been qualified.
- Earlier physical mining work covered Ryzen 7 5800X and RTX 4060. Broader
  CPU/GPU/driver combinations and extended load/thermal behavior are deferred.
- Actual 200% display scaling and full human keyboard/Narrator review remain
  unqualified. Geometry regressions do not replace those checks.
- Full genesis replay and remaining physical power/storage failure and
  reference-node propagation coverage are not certified by this beta.
- The installer is not Authenticode signed. Project signatures authenticate
  release files but do not give them Windows publisher reputation.
- Automatic update discovery is disabled. Upgrade using the installer from this
  repository's Releases page.
- Lite mode depends on hosted data and does not independently validate the chain.
  Mining is available only through the managed local node.
- Imported chain files require a trusted source. Structural and integrity checks
  do not independently validate every historical block.

## Reporting a problem

Include the beta version, Windows build, connection mode, reproduction steps and
redacted diagnostics. For mining reports, include the CPU, GPU and driver
version. Keep recovery words, passwords, private keys and wallet files out of
reports. Collected internal test data and VM files are not published in this
repository or the matching source ZIP.
