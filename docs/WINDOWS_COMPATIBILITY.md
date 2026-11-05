# Windows compatibility

Wartnode-Desktop targets **all Windows 11 x64 client releases**, starting with
21H2/build **22000**. The installer, portable desktop and signed metadata use the
same minimum. Windows 10, Windows Server, ARM and 32-bit Windows are outside this
target. Mining additionally needs a supported CPU/GPU and OpenCL driver.

Support for the Windows 11 family uses dependency/API compatibility review,
automated boundary tests and representative real systems. It does not require a
separate qualification on every feature release or monthly patch. Earlier Windows
11 versions are accepted by the policy and have not all been individually tested.
Exact beta source/artifacts are bound to the public CI run and signed manifests.

The Dashboard development baseline exercised Windows 11 x64 builds 26100 and
26300, shared-profile recovery, actual installation/recovery and physical mining
limits on Ryzen 7 5800X / RTX 4060. These are development-baseline results; they do
not certify every beta build on every OS, GPU or driver. See [beta scope](BETA.md).

The shared floor is defined in wartnode/preflight.py and passed to Inno Setup by
the installer builders. Manifest checks use the actual host build, so a future
package can require more without bypassing its requirement on an older host.
Signature, replay, channel and data-contract checks remain mandatory.

Reviewed November 5, 2026: pinned Python 3.14.10 supports Windows 10 and newer;
pinned Qt/PySide 6.12.0 lists Windows 11 x64 without a 24H2 cutoff. This supports
our compatibility target. Microsoft identifies the first Windows 11 release as
21H2/build 22000.

- [Python supported Windows versions](https://docs.python.org/3.14/using/windows.html#supported-windows-versions)
- [Qt Windows configurations](https://doc.qt.io/qt-6/windows.html)
- [Microsoft Windows 11 release history](https://learn.microsoft.com/en-us/windows/release-health/windows11-release-information)
