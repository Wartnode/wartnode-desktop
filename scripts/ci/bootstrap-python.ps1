param()
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$lock = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'toolchains.lock.json') -Raw | ConvertFrom-Json
$tools = Join-Path $projectRoot 'work/toolchains'
$runtime = Join-Path $tools ("python-" + $lock.python)
$archive = Join-Path $tools ("python-" + $lock.python + '-amd64.zip')
foreach ($path in @((Join-Path $projectRoot 'work'), $tools, $runtime, $archive)) {
    if ((Test-Path -LiteralPath $path) -and ((Get-Item -LiteralPath $path).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Linked toolchain path: $path"
    }
}
New-Item -ItemType Directory -Path $tools -Force | Out-Null
if (-not (Test-Path -LiteralPath $archive)) {
    Invoke-WebRequest -Uri $lock.python_windows.url -OutFile $archive
}
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $lock.python_windows.sha256) {
    throw 'Official Python archive checksum mismatch'
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::OpenRead($archive)
try {
    foreach ($entry in $zip.Entries) {
        if ($entry.FullName -match '(^/|\\|:|(^|/)\.\.(/|$))' -or (($entry.ExternalAttributes -shr 16) -band 0xF000) -eq 0xA000) {
            throw 'Unsafe Python archive path'
        }
    }
    if (-not (Test-Path -LiteralPath $runtime)) {
        [IO.Compression.ZipFile]::ExtractToDirectory($archive, $runtime)
    }
    # Validate cached runtime bytes too, before executing anything in that runtime.
    foreach ($entry in $zip.Entries) {
        if ($entry.FullName.EndsWith('/')) { continue }
        $path = Join-Path $runtime $entry.FullName
        $check = $path
        while ($check -ne $runtime) {
            if (-not (Test-Path -LiteralPath $check) -or ((Get-Item -LiteralPath $check).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                throw "Missing or linked Python runtime member: $($entry.FullName)"
            }
            $check = Split-Path -Parent $check
        }
        $stream = $entry.Open()
        try { $expected = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($stream)).ToLowerInvariant() }
        finally { $stream.Dispose() }
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) {
            throw "Cached Python runtime differs from verified archive: $($entry.FullName)"
        }
    }
} finally { $zip.Dispose() }
if ($env:GITHUB_PATH) {
    $runtime | Out-File -FilePath $env:GITHUB_PATH -Encoding utf8 -Append
    (Join-Path $runtime 'Scripts') | Out-File -FilePath $env:GITHUB_PATH -Encoding utf8 -Append
}
$env:PATH = @($runtime, (Join-Path $runtime 'Scripts'), $env:PATH) -join ';'
& (Join-Path $runtime 'python.exe') -c 'import ssl,sys; print(sys.version); print(ssl.OPENSSL_VERSION)'
if ($LASTEXITCODE) { throw 'Isolated Python validation failed' }
