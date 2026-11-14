; Cladia's installer (Inno Setup 6). Built by scripts\build_installer.py, which
; stages the committed code and generates optional-tool includes from its plugins.
;
; Per user, no administrator rights: the program in %LOCALAPPDATA%\Cladia\app,
; then setup.bat (Python and its packages in %LOCALAPPDATA%\Cladia\venv; WSL
; checked, installed if missing: only that asks Windows for permission). Cladia's
; own Linux is made the first time a tool runs (%LOCALAPPDATA%\Cladia\linux).
; Updating = running a newer installer over it: the program files are replaced,
; the packages updated when requirements.txt changed. The app moves an older
; library into its new named folder on first launch, preserving its contents.
; Uninstalling removes the program, its Python and its Linux, never the library
; (%USERPROFILE%\Cladia: the user's sequences).

#ifndef AppVersion
  #define AppVersion "0"
#endif
#ifndef OutputName
  #define OutputName "Cladia-setup-" + AppVersion
#endif
#ifndef Stage
  #define Stage "..\build\stage"
#endif

[Setup]
AppId={{6C1F3A52-5B0E-4E3B-9F2C-5E4C0D7A9B11}
AppName=Cladia
AppVersion={#AppVersion}
AppPublisher=Algas Galicia DNA
DefaultDirName={localappdata}\Cladia\app
UsePreviousAppDir=no
UsePreviousSetupType=no
DisableDirPage=yes
DefaultGroupName=Cladia
UsePreviousGroup=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\build
OutputBaseFilename={#OutputName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=Cladia
SetupIconFile={#Stage}\cladia\static\cladia.ico
UninstallDisplayIcon={app}\cladia\static\cladia.ico
; Cladia must be closed to replace its files (its Python runs from the venv)
CloseApplications=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "A shortcut on the desktop"; GroupDescription: "Shortcuts:"

[Types]
; Default installs no optional tools (they can be added later in Settings > Tools);
; Custom lets you tick the ones to install now.
Name: "default"; Description: "Default - Cladia and standard tools"
Name: "custom"; Description: "Custom: choose optional tools"; Flags: iscustom

[Components]
#include AddBackslash(Stage) + "optional-tool-tasks.iss"

[InstallDelete]
; the previous version's program files (not Python, not the Linux, not the library)
Type: filesandordirs; Name: "{app}\cladia"

[Files]
Source: "{#Stage}\*"; DestDir: "{app}"; Excludes: "*.pyc,*.pyo,__pycache__,.pytest_cache"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
; AppUserModelID: the same as window.py's, so a pinned taskbar button is Cladia's
Name: "{group}\Cladia"; Filename: "{localappdata}\Cladia\venv\Scripts\pythonw.exe"; Parameters: "-m cladia"; WorkingDir: "{app}"; IconFilename: "{app}\cladia\static\cladia.ico"; AppUserModelID: "Cladia.App"; Comment: "Sequences, alignments, trees, annotation, mapping"
Name: "{group}\Check Cladia's tools"; Filename: "{app}\check.bat"; WorkingDir: "{app}"; IconFilename: "{app}\cladia\static\cladia.ico"
Name: "{group}\Uninstall Cladia"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Cladia"; Filename: "{localappdata}\Cladia\venv\Scripts\pythonw.exe"; Parameters: "-m cladia"; WorkingDir: "{app}"; IconFilename: "{app}\cladia\static\cladia.ico"; AppUserModelID: "Cladia.App"; Tasks: desktopicon

[Run]
; Python and WSL are checked in CurStepChanged before queuing optional tools.
Filename: "{localappdata}\Cladia\venv\Scripts\pythonw.exe"; Parameters: "-m cladia"; WorkingDir: "{app}"; Description: "Start Cladia"; Flags: postinstall nowait skipifsilent

[UninstallRun]
; Only Cladia's managed Linux. An unmigrated older installation remains intact.
Filename: "{sys}\wsl.exe"; Parameters: "--unregister Cladia"; Flags: runhidden; RunOnceId: "UnregisterCladia"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
Type: filesandordirs; Name: "{localappdata}\Cladia\venv"
Type: filesandordirs; Name: "{localappdata}\Cladia\linux"

[Code]
var
  ToolProgress: TOutputProgressWizardPage;
  ToolNumber, ToolCount, SkippedTools: Integer;
  CurrentTool: String;
  SetupLog: String;

function CladiaIsRunning: Boolean;
var
  Locator, Service, Processes, Process: Variant;
  I: Integer;
  Executable, CurrentVenv, LegacyVenv: String;
begin
  Result := False;
  CurrentVenv := Lowercase(ExpandConstant('{localappdata}\Cladia\venv\Scripts\'));
  LegacyVenv := Lowercase(ExpandConstant('{localappdata}\SeqLab\venv\Scripts\'));
  Locator := CreateOleObject('WbemScripting.SWbemLocator');
  Service := Locator.ConnectServer('', 'root\CIMV2');
  Processes := Service.ExecQuery('SELECT ExecutablePath FROM Win32_Process WHERE Name=''python.exe'' OR Name=''pythonw.exe''');
  for I := 0 to Processes.Count - 1 do
  begin
    Process := Processes.ItemIndex(I);
    if not VarIsNull(Process.ExecutablePath) then
    begin
      Executable := Process.ExecutablePath;
      Executable := Lowercase(Executable);
      if (Pos(CurrentVenv, Executable) = 1) or (Pos(LegacyVenv, Executable) = 1) then
      begin
        Result := True;
        Exit;
      end;
    end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  try
    if CladiaIsRunning then
      Result := 'Close Cladia and wait for its jobs to finish, then click Next to retry. Setup cannot replace Python or application files while they are in use.';
  except
    Result := 'Could not check whether Cladia is running. Close Cladia and retry setup. ' + GetExceptionMessage;
  end;
end;

function InitializeUninstall: Boolean;
begin
  Result := False;
  try
    if CladiaIsRunning then
    begin
      MsgBox('Close Cladia and wait for its jobs to finish before uninstalling. This also protects its running Linux tools.', mbError, MB_OK);
      Exit;
    end;
    Result := True;
  except
    MsgBox('Could not check whether Cladia is running. Close Cladia and retry uninstalling. ' + GetExceptionMessage, mbError, MB_OK);
  end;
end;

procedure SetupOutput(const S: String; const Error, FirstLine: Boolean);
var
  Value, Detail: String;
  Separator, Amount: Integer;
begin
  Log(S);
  SaveStringToFile(SetupLog, S + #13#10, True);
  if Pos('CLADIA_PROGRESS:', S) = 1 then
  begin
    Value := Copy(S, 17, Length(S));
    Separator := Pos('|', Value);
    if Separator > 0 then
    begin
      Amount := StrToIntDef(Copy(Value, 1, Separator - 1), 0);
      Detail := Copy(Value, Separator + 1, Length(Value));
      if Amount < 0 then ToolProgress.ProgressBar.Style := npbstMarquee
      else
      begin
        ToolProgress.ProgressBar.Style := npbstNormal;
        ToolProgress.SetProgress(ToolNumber * 1000 + Amount, ToolCount * 1000);
      end;
      ToolProgress.SetText(CurrentTool, Detail);
    end;
  end
  else if Trim(S) <> '' then ToolProgress.SetText(CurrentTool, Copy(S, 1, 180));
end;

procedure InstallOptionalTool(Arguments, ToolLabel: String);
var
  ResultCode, Answer: Integer;
begin
  CurrentTool := 'Tool ' + IntToStr(ToolNumber + 1) + ' of ' + IntToStr(ToolCount) + ': ' + ToolLabel;
  ToolProgress.SetText(CurrentTool, 'Preparing download...');
  ToolProgress.SetProgress(ToolNumber * 1000, ToolCount * 1000);
  repeat
    if not ExecAndLogOutput(ExpandConstant('{localappdata}\Cladia\venv\Scripts\python.exe'),
      '-u -m cladia.installer_downloads ' + Arguments, ExpandConstant('{app}'),
      SW_SHOWNORMAL, ewWaitUntilTerminated, ResultCode, @SetupOutput) then ResultCode := -1;
    if ResultCode = 0 then Break;
    if WizardSilent then Answer := IDIGNORE
    else Answer := SuppressibleMsgBox('Could not install ' + ToolLabel + '.' + #13#10 +
      'Retry to try again, Ignore to skip this tool and install it later in Settings > Tools, or Abort to stop setup.' + #13#10 +
      'Details: ' + SetupLog,
      mbError, MB_ABORTRETRYIGNORE, IDIGNORE);
    if Answer = IDABORT then RaiseException('Optional tool installation stopped.');
    if Answer = IDIGNORE then
    begin
      SkippedTools := SkippedTools + 1;
      Break;
    end;
  until False;
  ToolNumber := ToolNumber + 1;
  ToolProgress.ProgressBar.Style := npbstNormal;
  ToolProgress.SetProgress(ToolNumber * 1000, ToolCount * 1000);
end;

procedure InstallSelectedTools;
var
  Arguments: String;
  I: Integer;
begin
  ToolCount := 0;
  ToolNumber := 0;
  for I := 0 to WizardForm.ComponentsList.Items.Count - 1 do
    if WizardForm.ComponentsList.Checked[I] and
      (WizardForm.ComponentsList.ItemLevel[I] = 0) then ToolCount := ToolCount + 1;
  // The required application is not a downloaded optional tool.
  ToolCount := ToolCount - 1;
  if ToolCount <= 0 then Exit;
  ToolProgress.Show;
  try
    #include AddBackslash(Stage) + "optional-tool-runs.iss"
  finally
    ToolProgress.Hide;
  end;
end;

// The folder this installer was run from (the lab's shared folder): Cladia looks
// there for a newer Cladia-setup-<date>.exe and says when one is waiting (updates.py).
procedure CurStepChanged(CurStep: TSetupStep);
var
  Lines: TArrayOfString;
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    SetupLog := ExpandConstant('{localappdata}\Cladia\setup.log');
    ForceDirectories(ExtractFileDir(SetupLog));
    SaveStringToFile(SetupLog, 'Cladia setup' + #13#10, False);
    CurrentTool := 'Preparing Python and Windows Subsystem for Linux';
    ToolProgress.Show;
    ToolProgress.SetText(CurrentTool, 'Downloading and preparing application dependencies...');
    ToolProgress.ProgressBar.Style := npbstMarquee;
    try
      if not ExecAndLogOutput(ExpandConstant('{cmd}'), '/c ""' + ExpandConstant('{app}\setup.bat') + '" installer"',
        ExpandConstant('{app}'), SW_SHOWNORMAL, ewWaitUntilTerminated, ResultCode, @SetupOutput) then
        RaiseException('Could not start application setup. Details: ' + SetupLog);
      if ResultCode <> 0 then
        RaiseException('Application setup failed. Run setup.bat to retry. Details: ' + SetupLog);
    finally
      ToolProgress.ProgressBar.Style := npbstNormal;
      ToolProgress.Hide;
    end;
    InstallSelectedTools;
    SetArrayLength(Lines, 1);
    Lines[0] := ExpandConstant('{src}');
    SaveStringsToUTF8File(ExpandConstant('{app}\UPDATES_FROM'), Lines, False);
  end;
end;

procedure InitializeWizard;
begin
  ToolProgress := CreateOutputProgressPage('Preparing Cladia',
    'Setup downloads dependencies and prepares your selected tools before finishing.');
  WizardForm.SelectComponentsLabel.Caption :=
    'Choose Custom for extras, downloaded during setup. Sizes are estimates; Linux and caches need extra space.';
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID = wpFinished) and (SkippedTools > 0) then
    WizardForm.FinishedLabel.Caption := 'Cladia is installed. Some optional tools were skipped. Add them later in Settings > Tools.';
end;

[Messages]
ComponentsDiskSpaceMBLabel=Estimated selection: [mb] MB, excluding Python, Linux, caches and databases.
ComponentsDiskSpaceGBLabel=Estimated selection: [gb] GB, excluding Python, Linux, caches and databases.
FinishedLabel=Cladia is installed. The first time a tool runs (Align, Annotate, a tree...), Cladia prepares its own Linux: a few minutes, once. Your library is in %USERPROFILE%\Cladia.
