param([string]$PythonExecutable = 'python', [int]$Jobs = 4)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'msvc-environment.ps1')
$installation = Initialize-WartnodeMsvc
$cmake = Join-Path $installation 'Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin'
$ninja = Join-Path $installation 'Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja'
$llvm = Join-Path $env:ProgramFiles 'LLVM\bin'
$env:PATH = @($env:WARTNODE_LLVM_BIN,$llvm,$cmake,$ninja,$env:PATH) -join ';'
& $PythonExecutable (Join-Path $PSScriptRoot 'build_miner.py') --jobs $Jobs
if ($LASTEXITCODE) { throw 'Source miner build failed' }
