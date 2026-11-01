; Build through build_installer.ps1. Never include workspace, models or .env.
#ifndef AppVersion
  #error AppVersion must be supplied by the build script
#endif
#ifndef AppExe
  #error AppExe must be supplied by the build script
#endif
#ifndef ProjectRoot
  #define ProjectRoot ".."
#endif
#ifndef ChineseMessages
  #define ChineseMessages "compiler:Languages\ChineseSimplified.isl"
#endif

[Setup]
; Keep this identifier unchanged across versions so upgrades reuse the directory.
AppId={{E2B0188A-9456-48D6-82B4-21BB2D20FB80}
AppName=AI Video Note
AppVersion={#AppVersion}
AppPublisher=hehe1885
AppSupportURL=https://ko-fi.com/hehe1885
DefaultDirName={localappdata}\Programs\AI Video Note
DefaultGroupName=AI Video Note
DisableProgramGroupPage=yes
DisableDirPage=no
UsePreviousAppDir=yes
UsePreviousLanguage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#ProjectRoot}\dist
OutputBaseFilename=AI-Video-Note-{#AppVersion}-Setup-x64
SetupIconFile={#ProjectRoot}\sources\icon.ico
UninstallDisplayIcon={app}\AI-Video-Note.exe
LicenseFile={#ProjectRoot}\LICENSE
WizardStyle=modern
Compression=lzma2/fast
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
AllowRootDirectory=no
AllowNetworkDrive=no
AllowUNCPath=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "{#ChineseMessages}"

[CustomMessages]
english.DesktopShortcut=Create a desktop shortcut
english.LaunchApp=Launch AI Video Note
english.DataTitle=Your settings and models
english.DataDescription=Updates preserve your personal data.
english.DataBody=Configuration, account credentials, notes and models are stored in the workspace folder inside the installation directory, unless you choose a custom location in the app.%n%nThis installer only replaces program files. It does not include preinstalled models or personal data. Updates and uninstalling preserve the workspace and custom data folders.%n%nTo update, quit the app from its system tray menu and install the new version into the same directory.
chinesesimplified.DesktopShortcut=创建桌面快捷方式
chinesesimplified.LaunchApp=启动 AI Video Note
chinesesimplified.DataTitle=配置与模型的保存位置
chinesesimplified.DataDescription=升级时保留你的个人数据。
chinesesimplified.DataBody=配置、账号凭据、笔记和模型默认保存在安装目录内的 workspace 文件夹中；软件内自定义的目录按你的设置保存。%n%n安装包只替换程序文件，不包含预装模型或个人数据。升级和卸载均保留 workspace 及自定义数据目录。%n%n更新前请从系统托盘退出软件，并将新版本安装到相同目录。

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopShortcut}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Explicit allowlist: no recursive copying of project or user directories.
Source: "{#AppExe}"; DestDir: "{app}"; DestName: "AI-Video-Note.exe"; Flags: ignoreversion
Source: "{#ProjectRoot}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectRoot}\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectRoot}\README.en.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\AI Video Note"; Filename: "{app}\AI-Video-Note.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\AI Video Note"; Filename: "{app}\AI-Video-Note.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\AI-Video-Note.exe"; Description: "{cm:LaunchApp}"; Flags: nowait postinstall skipifsilent

[Code]
procedure InitializeWizard;
begin
  CreateOutputMsgPage(wpSelectDir, CustomMessage('DataTitle'),
    CustomMessage('DataDescription'), CustomMessage('DataBody'));
end;

procedure RemoveMatchingStartupEntries(RootKey: Integer);
var
  Names: TArrayOfString;
  Index: Integer;
  Value, ProgramPath, QuietCommand: String;
begin
  { Remove only startup entries that point exactly to this installed executable. }
  ProgramPath := ExpandConstant('{app}\AI-Video-Note.exe');
  { Inno Setup 7 may expand local paths with the extended-length prefix. }
  StringChangeEx(ProgramPath, '\\?\', '', True);
  QuietCommand := '"' + ProgramPath + '" --no-browser';
  if RegGetValueNames(RootKey, 'Software\Microsoft\Windows\CurrentVersion\Run', Names) then
  begin
    Log(Format('Checking %d startup entries for this installation', [GetArrayLength(Names)]));
    for Index := 0 to GetArrayLength(Names) - 1 do
      if Pos('AI-Video-Note-', Names[Index]) = 1 then
        if RegQueryStringValue(RootKey, 'Software\Microsoft\Windows\CurrentVersion\Run', Names[Index], Value) then
        begin
          StringChangeEx(Value, '\\?\', '', True);
          if (CompareText(Value, QuietCommand) = 0) or
             (CompareText(Value, '"' + ProgramPath + '"') = 0) or
             (CompareText(Value, ProgramPath + ' --no-browser') = 0) or
             (CompareText(Value, ProgramPath) = 0) then
          begin
            RegDeleteValue(RootKey, 'Software\Microsoft\Windows\CurrentVersion\Run', Names[Index]);
            Log('Removed a matching AI Video Note startup entry');
          end;
        end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep <> usPostUninstall then Exit;
  { The application writes from a 64-bit process; inspect both registry views. }
  if IsWin64 then RemoveMatchingStartupEntries(HKCU64);
  RemoveMatchingStartupEntries(HKCU32);
end;

{ No UninstallDelete or InstallDelete entries: user data is never removed. }
