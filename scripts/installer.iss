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
english.DataBody=Configuration, credentials, notes and models default to the workspace folder inside the installation directory.%n%nUpdates only replace program files and preserve configuration, credentials, notes, models and custom data folders.%n%nUninstalling exits this installation's background processes and deletes configuration, credentials, notes and models inside the installation directory. Custom folders outside it are preserved. Back up any data you need before uninstalling.
chinesesimplified.DesktopShortcut=创建桌面快捷方式
chinesesimplified.LaunchApp=启动 AI Video Note
chinesesimplified.DataTitle=配置与模型的保存位置
chinesesimplified.DataDescription=升级时保留你的个人数据。
chinesesimplified.DataBody=配置、账号凭据、笔记和模型默认保存在安装目录内的 workspace 文件夹中；软件内自定义的目录按你的设置保存。%n%n升级只替换程序文件，保留配置、凭据、笔记、模型和自定义数据目录。%n%n卸载会退出本次安装的后台进程，并删除安装目录内的配置、凭据、笔记和模型。安装目录外的自定义数据保留。卸载前请自行备份需要的数据。
english.UninstallFailure=Uninstall could not complete. Background processes or files may remain. Installation directory: %1%nLog: %2
chinesesimplified.UninstallFailure=卸载未完成，后台进程或文件可能仍有残留。安装目录：%1%n日志：%2

[Messages]
english.ConfirmUninstall=Remove %1? Settings, credentials, notes and models inside the installation directory will also be deleted. Back up anything you need first. Updates preserve this data.
chinesesimplified.ConfirmUninstall=确定卸载 %1 吗？安装目录内的设置、凭据、笔记和模型也会删除，请先备份需要的数据。更新安装会保留这些数据。

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopShortcut}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Explicit allowlist: no recursive copying of project or user directories.
Source: "{#AppExe}"; DestDir: "{app}"; DestName: "AI-Video-Note.exe"; Flags: ignoreversion
Source: "{#ProjectRoot}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectRoot}\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectRoot}\README.en.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectRoot}\scripts\uninstall_helper.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectRoot}\scripts\install-owner.json"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\AI Video Note"; Filename: "{app}\AI-Video-Note.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\AI Video Note"; Filename: "{app}\AI-Video-Note.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\AI-Video-Note.exe"; Description: "{cm:LaunchApp}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Never recursively delete {app}; only the validated helper removes owned data.
Type: dirifempty; Name: "{app}"

[Code]
var
  UninstallHelper: String;

procedure ExitUninstallProcess(Code: Integer);
  external 'ExitProcess@kernel32.dll stdcall';

procedure FailUninstall;
begin
  SuppressibleMsgBox(FmtMessage(CustomMessage('UninstallFailure'), [ExpandConstant('{app}'), ExpandConstant('{tmp}\ai-video-note-uninstall-helper.log')]), mbError, MB_OK, IDOK);
  { Stop before showing a successful completion, including in silent mode. }
  ExitUninstallProcess(2);
end;

procedure RunUninstallHelper(Action: String);
var
  Code: Integer;
  Parameters: String;
begin
  Parameters := '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' +
    UninstallHelper + '" -Action ' + Action + ' -InstallDirectory "' + ExpandConstant('{app}') +
    '" -LogPath "' + ExpandConstant('{tmp}\ai-video-note-uninstall-helper.log') + '"';
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Parameters,
    ExpandConstant('{tmp}'), SW_HIDE, ewWaitUntilTerminated, Code) then FailUninstall;
  if Code <> 0 then FailUninstall;
end;

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
  if CurUninstallStep = usUninstall then
  begin
    { This event runs only after the user confirms an actual uninstall. }
    UninstallHelper := ExpandConstant('{tmp}\ai-video-note-uninstall-helper.ps1');
    if not FileCopy(ExpandConstant('{app}\uninstall_helper.ps1'), UninstallHelper, False) then FailUninstall;
    if IsWin64 then RemoveMatchingStartupEntries(HKCU64);
    RemoveMatchingStartupEntries(HKCU32);
    RunUninstallHelper('Prepare');
  end;
  if CurUninstallStep = usPostUninstall then
  begin
    RunUninstallHelper('Verify');
    if DirExists(ExpandConstant('{app}')) then RemoveDir(ExpandConstant('{app}'));
  end;
end;
