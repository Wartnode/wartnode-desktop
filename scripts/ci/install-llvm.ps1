$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$locked = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'toolchains.lock.json') -Raw | ConvertFrom-Json
$destination = Join-Path $projectRoot 'work\ci\llvm'
New-Item -ItemType Directory -Force -Path $destination | Out-Null
$archive = Join-Path $projectRoot 'work\ci\llvm.tar.zst'
Invoke-WebRequest -Uri $locked.llvm.url -OutFile $archive
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLower() -ne $locked.llvm.sha256) { throw 'LLVM archive checksum mismatch' }
& python (Join-Path $PSScriptRoot 'extract_toolchain.py') $archive $destination $locked.llvm.sha256
if ($LASTEXITCODE) { throw 'LLVM extraction failed' }
$binary = Join-Path $destination ('clang+llvm-' + $locked.llvm.version + '-x86_64-pc-windows-msvc\bin')
if (-not (Test-Path -LiteralPath (Join-Path $binary 'clang-cl.exe'))) { throw 'Locked clang-cl was not extracted' }
$env:WARTNODE_LLVM_BIN = $binary
if ($env:GITHUB_ENV) { Add-Content -LiteralPath $env:GITHUB_ENV -Value ('WARTNODE_LLVM_BIN=' + $binary) }
