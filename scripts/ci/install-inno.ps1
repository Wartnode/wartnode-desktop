$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location -LiteralPath $projectPath
$tool = (Get-Content -LiteralPath 'scripts/ci/toolchains.lock.json' -Raw | ConvertFrom-Json).inno_setup
$toolFolder = Join-Path $projectPath 'work\ci\inno'
$downloadPath = Join-Path $projectPath 'work\ci\inno-setup.exe'
New-Item -ItemType Directory -Path (Split-Path -Parent $downloadPath) -Force | Out-Null
if (-not (Test-Path -LiteralPath $downloadPath)) { Invoke-WebRequest -Uri $tool.url -OutFile $downloadPath }
if ((Get-FileHash -LiteralPath $downloadPath -Algorithm SHA256).Hash.ToLower() -ne $tool.sha256) { throw 'Inno Setup download checksum mismatch' }
if (-not (Test-Path -LiteralPath (Join-Path $toolFolder 'ISCC.exe'))) {
    $installProcess = Start-Process -FilePath $downloadPath -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-','/CURRENTUSER','/NOICONS',('/DIR="'+$toolFolder+'"')) -WindowStyle Hidden -PassThru -Wait
    if ($installProcess.ExitCode) { throw 'Inno Setup compiler installation failed' }
}
$compiler = Join-Path $toolFolder 'ISCC.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw 'Inno Setup compiler is missing' }
$actualVersion = (& $compiler --version 2>&1 | Out-String).Trim()
if ($actualVersion -notmatch ('(?m)^'+[regex]::Escape($tool.version)+'$')) { throw "Unexpected Inno Setup compiler version: $actualVersion" }
Write-Output $compiler
