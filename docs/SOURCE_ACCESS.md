# Source and component notices

Wartnode-Desktop is distributed under GPL-3.0-or-later. The release includes the
exact matching application source ZIP beside the installer and portable ZIP,
bound by the signed release manifest. The public `main` branch provides the
maintained source; use the matching release ZIP or tag for a particular binary.

## Application and upstream inputs

The matching source ZIP includes application/native code, automated regression
test code, build recipes, lock files, patches and notices. `vendor/sources`
contains exact source archives for official Warthog core, the BRminer-derived
engine, wallet primitives and ConnectCoin/Janus hashing dependencies. Their
versions, hashes and materialization paths are in `scripts/ci/sources.lock.json`.
The Warthog Python SDK archive and metadata are also retained under
`vendor/sources`; see `vendor/sources/warthog-python-sdk.json`. Its coincurve
20.0.0 source and exact native secp256k1 commit/archive hashes are recorded there.
The new dependencies retain their notices in `notices/dependencies`, with the
upstream secp256k1 license in `notices/libsecp256k1-MIT.txt`.
No private repository access is needed to obtain these inputs.

Collected test results, VM images, wallet databases, logs, screenshots and private
development history are excluded. Minimal generated build identity metadata is
included to connect the source ZIP to the exact CI-produced binary hashes.

## Unmodified runtime and build dependencies

The exact Python package versions and wheel hashes are pinned in the CI lock
files. [`notices/source-access.json`](../notices/source-access.json) identifies
official source URLs, versions and SHA-256 hashes, including the source
distributions for Python dependencies. The following official archives cover
the unmodified Qt/PySide/Shiboken modules and interpreter:

| Input | Version | Official source |
| --- | --- | --- |
| Qt Core, GUI, Widgets and Network | 6.12.0 | [Qt Base source](https://download.qt.io/official_releases/qt/6.12/6.12.0/submodules/qtbase-everywhere-src-6.12.0.tar.xz) |
| Qt SVG and SVG icon/image plugins | 6.12.0 | [Qt SVG source](https://download.qt.io/official_releases/qt/6.12/6.12.0/submodules/qtsvg-everywhere-src-6.12.0.tar.xz) |
| Additional image plugins | 6.12.0 | [Qt Image Formats source](https://download.qt.io/official_releases/qt/6.12/6.12.0/submodules/qtimageformats-everywhere-src-6.12.0.tar.xz) |
| PySide6 and Shiboken | 6.12.0 | [PySide setup source](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.12.0-src/pyside-setup-everywhere-src-6.12.0.tar.xz) |
| CPython | 3.14.10 | [CPython source](https://www.python.org/ftp/python/3.14.10/Python-3.14.10.tar.xz) |

The Qt archives retain upstream build files and their third-party sources.
PySide's source includes its build configuration and binding generator. The
application uses the GPL distribution option for its dynamically linked Qt
modules; the libraries remain separately replaceable. It ships no BzMiner,
CUDA Toolkit or NVRTC binary.

Qt's unmodified Windows software OpenGL fallback (`opengl32sw.dll`) carries
[Mesa llvmpipe](https://doc.qt.io/qt-6/qt-attribution-llvmpipe.html) and
[LLVM attributions](https://doc.qt.io/qt-6/qt-attribution-llvm.html), retained in
`notices`. This graphics fallback is distinct from the miner's driver OpenCL
backend. Native compiler/toolchain inputs are pinned separately in
`scripts/ci/toolchains.lock.json`.

License and copyright texts accompany the package in `notices`, including
dependency and Qt third-party notices. Maintainers must preserve equivalent
access to matching source while offering binaries; if an upstream source URL
becomes unavailable, mirror the retained exact source before continuing to offer
the affected release. The source inventory hashes identify the retained bytes.

See [build instructions](BUILD.md), the [GPL](../LICENSE) and
[Qt's open-source distribution guidance](https://www.qt.io/development/open-source-lgpl-obligations).
