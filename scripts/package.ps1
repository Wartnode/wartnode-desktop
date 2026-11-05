param([string]$PythonExecutable = '')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not $PythonExecutable) { $PythonExecutable = Join-Path $projectRoot '.venv\Scripts\python.exe' }
$PythonExecutable = (Resolve-Path -LiteralPath $PythonExecutable).Path
& $PythonExecutable scripts/audit-runtime.py
if ($LASTEXITCODE) { throw 'Native runtime audit failed' }
& $PythonExecutable scripts/prepare-components.py
if ($LASTEXITCODE) { throw 'Component preparation failed' }
& $PythonExecutable scripts/collect-notices.py
if ($LASTEXITCODE) { throw 'Dependency notice preparation failed' }
$sourceCommit = & git rev-parse HEAD
if ($LASTEXITCODE) { throw 'Cannot identify package source commit' }
$basePython = & $PythonExecutable -c 'import sys; print(sys.base_prefix)'
$env:PATH = @((Split-Path -Parent $PythonExecutable),$basePython,(Join-Path $env:SystemRoot 'System32'),$env:SystemRoot) -join ';'
& $PythonExecutable -m PyInstaller --noconfirm --clean WartNode.spec *> work/package-build.log
if ($LASTEXITCODE) { Get-Content -LiteralPath work/package-build.log -Tail 40; throw 'Application packaging failed' }
Copy-Item -LiteralPath 'docs\MVP_GUIDE.md' -Destination 'dist\WartNode\START-HERE.md'
Copy-Item -LiteralPath 'LICENSE' -Destination 'dist\WartNode\LICENSE.txt'
Copy-Item -LiteralPath 'docs\RELEASE_SIGNING.md' -Destination 'dist\WartNode\RELEASE-VERIFICATION.md'
Copy-Item -LiteralPath 'docs\SOURCE_ACCESS.md' -Destination 'dist\WartNode\SOURCE-ACCESS.md'
Copy-Item -LiteralPath 'provenance' -Destination 'dist\WartNode\provenance' -Recurse -Force
Copy-Item -LiteralPath 'patches' -Destination 'dist\WartNode\provenance\patches' -Recurse -Force
& $PythonExecutable scripts/create-bundle.py --source-commit $sourceCommit
if ($LASTEXITCODE) { throw 'Bundle inventory failed' }
& $PythonExecutable scripts/audit-package.py
if ($LASTEXITCODE) { throw 'Package audit failed' }
$version = & $PythonExecutable -c 'from wartnode import __version__; print(__version__)'
$archive=Join-Path $projectRoot ("dist\Wartnode-Desktop-"+$version+'-windows-x64.zip')
Compress-Archive -Path 'dist\WartNode' -DestinationPath $archive -Force
$hash=(Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLower()
"$hash  $([System.IO.Path]::GetFileName($archive))" | Set-Content -LiteralPath ($archive+'.sha256') -Encoding ascii
Write-Output $archive
Write-Output $hash
