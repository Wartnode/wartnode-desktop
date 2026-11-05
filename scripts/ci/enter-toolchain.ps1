param([string]$PythonExecutable = 'python', [int]$Jobs = 4)
$ErrorActionPreference = 'Stop'
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$installation = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $installation) { throw 'Visual Studio x64 C++ Build Tools are required' }
$developer = Join-Path $installation 'VC\Auxiliary\Build\vcvars64.bat'
$lines = & cmd.exe /d /c "`"$developer`" >nul && set"
if ($LASTEXITCODE) { throw 'Could not initialize the x64 toolchain' }
foreach ($line in $lines) {
    if ($line -match '^(PATH|INCLUDE|LIB|LIBPATH|VSINSTALLDIR|VisualStudioVersion|VCToolsVersion|VCToolsInstallDir|WindowsSDKVersion|WindowsSdkDir)=(.*)$') { Set-Item -Path "Env:$($Matches[1])" -Value $Matches[2] }
}
$cmake = Join-Path $installation 'Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin'
$ninja = Join-Path $installation 'Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja'
$llvm = Join-Path $env:ProgramFiles 'LLVM\bin'
$env:PATH = @($env:WARTNODE_LLVM_BIN,$llvm,$cmake,$ninja,$env:PATH) -join ';'
& $PythonExecutable (Join-Path $PSScriptRoot 'build_miner.py') --jobs $Jobs
if ($LASTEXITCODE) { throw 'Source miner build failed' }
