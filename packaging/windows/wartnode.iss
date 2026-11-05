#ifndef MinimumWindowsBuild
  #error Build through scripts/build-installer.ps1 to select the shared Windows policy
#endif
#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef BundleDir
  #define BundleDir "..\..\dist\WartNode"
#endif
#ifndef LauncherFile
  #define LauncherFile "..\..\dist\WartNodeLauncher.exe"
#endif
#ifndef FileVersion
  #define FileVersion "0.3.0.1"
#endif

[Setup]
AppId={{AD317E15-0C4D-4586-BD4C-84C6551A2B8E}
AppName=Wartnode-Desktop
AppVersion={#AppVersion}
AppPublisher=Wartnode contributors
VersionInfoVersion={#FileVersion}
VersionInfoTextVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\WartNode
DefaultGroupName=Wartnode-Desktop
UsePreviousGroup=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.{#MinimumWindowsBuild}
OutputDir=..\..\dist
OutputBaseFilename=Wartnode-Desktop-{#AppVersion}-windows-x64-setup
WizardStyle=modern
Compression=lzma2/fast
SolidCompression=yes
UninstallDisplayIcon={app}\WartNode.exe
LicenseFile=..\..\LICENSE
CloseApplications=no
RestartApplications=no
SetupMutex=Local\WartNodeSetup
AppMutex=Local\WartNodeDesktop
AlwaysRestart=no
ChangesAssociations=no

[Files]
Source: "{#LauncherFile}"; Flags: dontcopy
Source: "{#LauncherFile}"; DestDir: "{app}"; DestName: "WartNode.exe"; Flags: ignoreversion
Source: "installation.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#BundleDir}\*"; DestDir: "{app}\versions\{#AppVersion}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
Type: files; Name: "{userprograms}\WartNode\WartNode.lnk"
Type: files; Name: "{userprograms}\WartNode\Uninstall WartNode.lnk"
Type: files; Name: "{userdesktop}\WartNode.lnk"

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked

[Icons]
Name: "{group}\Wartnode-Desktop"; Filename: "{app}\WartNode.exe"; WorkingDir: "{app}"
Name: "{group}\Uninstall Wartnode-Desktop"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Wartnode-Desktop"; Filename: "{app}\WartNode.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\WartNode.exe"; Description: "Open Wartnode-Desktop"; Flags: postinstall nowait skipifsilent

[Code]
function InitializeSetup: Boolean;
var Version: TWindowsVersion;
begin
  GetWindowsVersionEx(Version);
  Result := (Version.ProductType = VER_NT_WORKSTATION) and (Version.Build >= {#MinimumWindowsBuild}) and IsX64OS;
  if not Result then
    SuppressibleMsgBox('Wartnode-Desktop requires Windows 11 x64 (build {#MinimumWindowsBuild} or newer). ARM and Windows Server are not supported.', mbCriticalError, MB_OK, IDOK);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var Code: Integer; Tool: String;
begin
  Result := '';
  ExtractTemporaryFile('WartNodeLauncher.exe');
  Tool := ExpandConstant('{tmp}\WartNodeLauncher.exe');
  if not Exec(Tool, '--root "' + ExpandConstant('{app}') + '" --check-installation --target-version "{#AppVersion}"', '', SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
    Result := 'Exit Wartnode-Desktop before installing. Choose an empty application folder or the existing installation, separate from your wallet data. A newer installed version requires its matching installer.';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var Code: Integer;
begin
  if CurStep = ssPostInstall then begin
    if not Exec(ExpandConstant('{app}\WartNode.exe'), '--register "{#AppVersion}"', '', SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
      RaiseException('Wartnode-Desktop files could not be verified. Keep your data folder and run this installer again to repair the installation.');
  end;
end;

function InitializeUninstall: Boolean;
var Code: Integer;
begin
  Result := Exec(ExpandConstant('{app}\WartNode.exe'), '--check-installation', '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
  if not Result then SuppressibleMsgBox('Use Exit Wartnode-Desktop before uninstalling. Wallets and payment history will be kept.', mbError, MB_OK, IDOK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var Code: Integer;
begin
  if CurUninstallStep = usUninstall then begin
    if not Exec(ExpandConstant('{app}\WartNode.exe'), '--uninstall-files', '', SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
      RaiseException('Wartnode-Desktop is still running. Exit the app, then uninstall again.');
  end;
end;
