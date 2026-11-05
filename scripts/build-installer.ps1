param([string]$Version = '', [string]$BundleDir = '', [string]$PythonExecutable = '')
$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectPath
if (-not $PythonExecutable) { $PythonExecutable = Join-Path $projectPath '.venv\Scripts\python.exe' }
if (-not $Version) { $Version = & $PythonExecutable -c 'from wartnode import __version__; print(__version__)' }
if (-not $BundleDir) { $BundleDir = Join-Path $projectPath 'dist\WartNode' }
$basePython = & $PythonExecutable -c 'import sys; print(sys.base_prefix)'
$env:PATH = @((Split-Path -Parent $PythonExecutable),$basePython,(Join-Path $env:SystemRoot 'System32'),$env:SystemRoot) -join ';'
$compilerPath = & "$PSScriptRoot\ci\install-inno.ps1"
& $PythonExecutable -m PyInstaller --noconfirm --clean Launcher.spec *> work/launcher-build.log
if ($LASTEXITCODE) { Get-Content work/launcher-build.log -Tail 20; throw 'Launcher build failed' }
$numericVersion = ($Version -replace '-(dev|beta|rc)\.', '.')
& $compilerPath ("/DAppVersion="+$Version) ("/DFileVersion="+$numericVersion) ("/DBundleDir="+$BundleDir) 'packaging/windows/wartnode.iss' *> work/installer-build.log
if ($LASTEXITCODE) { Get-Content work/installer-build.log -Tail 25; throw 'Installer build failed' }
$installerPath = Join-Path $projectPath ('dist\Wartnode-Desktop-'+$Version+'-windows-x64-setup.exe')
$digest = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLower()
"$digest  $([System.IO.Path]::GetFileName($installerPath))" | Set-Content -LiteralPath ($installerPath+'.sha256') -Encoding ascii
Write-Output $installerPath
