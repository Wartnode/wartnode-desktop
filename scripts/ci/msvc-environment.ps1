function Initialize-WartnodeMsvc {
    $ErrorActionPreference = 'Stop'
    $wartnodeVswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    $wartnodeInstallation = & $wartnodeVswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if (-not $wartnodeInstallation) { throw 'Visual Studio x64 C++ Build Tools are required' }
    $wartnodeDeveloper = Join-Path $wartnodeInstallation 'VC\Auxiliary\Build\vcvars64.bat'
    $wartnodeLines = & cmd.exe /d /c "`"$wartnodeDeveloper`" >nul && set"
    if ($LASTEXITCODE) { throw 'Could not initialize the x64 toolchain' }
    foreach ($wartnodeLine in $wartnodeLines) {
        if ($wartnodeLine -match '^(PATH|INCLUDE|LIB|LIBPATH|VSINSTALLDIR|VisualStudioVersion|VCToolsVersion|VCToolsInstallDir|WindowsSDKVersion|WindowsSdkDir)=(.*)$') {
            Set-Item -Path "Env:$($Matches[1])" -Value $Matches[2]
        }
    }
    return $wartnodeInstallation
}
