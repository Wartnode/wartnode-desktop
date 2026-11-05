# Building Wartnode-Desktop

The supported application target is Windows 11 x64. GitHub Actions builds the
official node on Ubuntu and the miner/desktop package on Windows Server 2025;
the hosted runner is a build environment, not Windows 11 certification.

## Inputs

- `scripts/ci/sources.lock.json` pins upstream source archives and patches.
- `vendor/sources` contains the exact compressed source inputs needed by the
  build, including wallet and mining dependencies. No private repository access
  is needed to materialize these inputs.
- `scripts/ci/toolchains.lock.json` pins Python, LLVM/clang-cl and Inno Setup.
- `scripts/ci/requirements.lock` and `build-requirements.lock` pin Python packages
  with hashes. Native runtime versions are checked before packaging.

## GitHub Actions

The [`build.yml`](../.github/workflows/build.yml) pipeline runs on pushes to
`main`, pull requests and manual dispatch. It verifies input archives, builds
official core and native regressions, rebuilds the miner's libraries with
clang-cl, runs app/wallet/frozen/installed checks, and prepares matching Windows
installer, portable and source archives.

Only a passing push from this repository's `main` can prepare a release request.
Signing happens on the maintainer's Windows PC using its protected key store;
private signing keys are not stored in GitHub. Publication is a separate action.

## Local development

Start with a clean checkout and the versions pinned in the lock files. Use
PowerShell 7, Git, the Visual Studio C++ build tools and Windows SDK, and a Linux
container/runner for the node cross-build. Follow the workflow's exact order:

```powershell
git clone https://github.com/Wartnode/wartnode-desktop.git
cd wartnode-desktop
./scripts/ci/bootstrap-python.ps1
python scripts/ci/bootstrap.py
./scripts/ci/install-python-inputs.ps1
```

The Python bootstrap installs the verified interpreter in the workspace and
updates the current PowerShell session. The node recipe is
`scripts/ci/build_node.py` on Linux; its output is transferred to
`work/upstream-node-build` on Windows. On Windows, install the pinned LLVM
toolchain with `scripts/ci/install-llvm.ps1`, then use
`scripts/ci/enter-toolchain.ps1` to enter the C++ build environment and rebuild
the miner. The workflow documents artifact transfer and exact commands.

After both native components are available:

```powershell
python scripts/prepare-components.py
python scripts/verify-upstream-source.py
python scripts/ci/run_checks.py
python scripts/ci/deliver.py
```

`run_checks.py` packages the desktop and creates a disposable version-only
upgrade fixture. Test output is generated under ignored `evidence/` and `work/`
directories and is excluded from public source archives. It uses synthetic
payment fixtures; it does not send real funds or wait to discover a block.

For source-only unit checks after bootstrap and component preparation:

```powershell
python scripts/run-tests.py
python scripts/run-tests.py --legacy
```

Application versions and Windows release versions are independent. Keep the
matching source ZIP, lock files and notices beside distributed binaries. The
build records exact input hashes; it does not claim bit-for-bit reproducibility.

## Message proof SDK inputs

The Python SDK source archive and its commit/hash metadata are retained in
`vendor/sources/warthog-python-sdk.json`. Runtime and native build inputs are
hash locked; `install-python-inputs.ps1` initializes x64 MSVC and uses pinned
Ninja before the coincurve build. Do not replace this with an unpinned SDK install.
The proof adapter uses the SDK Account API locally, without network API calls.

CI inventories every runtime/build/release package and SDK repository advisory.
These results require applicability review and do not certify the pinned source
as vulnerability-free. Frozen helper checks cover message/file proofs, locked
verification, single-use approvals, tampering and v1 compatibility.
