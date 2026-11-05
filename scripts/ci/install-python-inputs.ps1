param([string]$PythonExecutable = 'python')
$ErrorActionPreference = 'Stop'
$PythonExecutable = (Get-Command $PythonExecutable -ErrorAction Stop).Source
. (Join-Path $PSScriptRoot 'msvc-environment.ps1')
$wartnodeInstallation = Initialize-WartnodeMsvc
# Ninja uses the explicitly initialized compiler instead of CMake's Visual Studio
# instance discovery. Resolve Python first: vcvars may change executable precedence.
$env:PATH = @((Split-Path -Parent $PythonExecutable), (Join-Path (Split-Path -Parent $PythonExecutable) 'Scripts'), $env:PATH) -join ';'
$env:CMAKE_GENERATOR = 'Ninja'
$env:CMAKE_GENERATOR_INSTANCE = $null
$env:CMAKE_GENERATOR_PLATFORM = $null
$env:CC = 'cl'
$env:CXX = 'cl'
Write-Host "Python native build: $wartnodeInstallation; MSVC $env:VCToolsVersion; SDK $env:WindowsSDKVersion"
& $PythonExecutable -m pip install --require-hashes -r (Join-Path $PSScriptRoot 'build-requirements.lock')
if ($LASTEXITCODE) { throw 'Hash-locked Python build input installation failed' }
& $PythonExecutable -m pip install --require-hashes --no-build-isolation -r (Join-Path $PSScriptRoot 'requirements.lock')
if ($LASTEXITCODE) { throw 'Hash-locked Python runtime installation failed' }
