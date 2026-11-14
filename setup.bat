@echo off
rem Setup: creates the Python environment and installs packages.
rem Safe to run again: it skips whatever is already done.
rem The environment lives outside this folder (%LOCALAPPDATA%\Cladia\venv), so
rem replacing the program files with a new version never removes it.
cd /d "%~dp0"
set "VENV=%LOCALAPPDATA%\Cladia\venv"

rem 1. Python environment
if exist "%VENV%\Scripts\python.exe" (
  echo Python environment: already there.
  goto packages
)

rem Python 3.11 or newer: the one the py launcher finds, else python.org's
rem installer, run quietly for this user only (no administrator needed)
set "PYVER=3.14.4"
set "PY="
for /f "delims=" %%i in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PY=%%i"
if defined PY "%PY%" -c "import sys; sys.exit(sys.version_info < (3, 11))" || set "PY="
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
if defined PY goto venv

echo Python 3.11 or newer was not found: installing Python %PYVER% from python.org...
set "INSTALLER=%TEMP%\python-%PYVER%-amd64.exe"
curl -L --fail -o "%INSTALLER%" https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-amd64.exe
if errorlevel 1 (
  echo.
  echo Downloading Python failed. Install it from python.org, then run setup.bat again.
  if /i not "%~1"=="installer" pause
  exit /b 1
)
"%INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0
if errorlevel 1 (
  echo.
  echo Installing Python failed. Install it from python.org, then run setup.bat again.
  if /i not "%~1"=="installer" pause
  exit /b 1
)
del "%INSTALLER%"
set "PY=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"

:venv
echo Creating the Python environment in %VENV% with %PY% ...
"%PY%" -m venv "%VENV%"
if errorlevel 1 (
  echo.
  echo Could not create the environment. Is Python installed from python.org?
  if /i not "%~1"=="installer" pause
  exit /b 1
)

:packages

rem 2. Packages: only when requirements.txt changed since the last install
if exist "%VENV%\requirements.installed" (
  fc /b requirements.txt "%VENV%\requirements.installed" >nul 2>&1
  if not errorlevel 1 (
    echo Packages: already up to date.
    goto done
  )
)

echo Installing packages...
"%VENV%\Scripts\python" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Installing packages failed, see the messages above.
  if /i not "%~1"=="installer" pause
  exit /b 1
)
copy /y requirements.txt "%VENV%\requirements.installed" >nul

:done
rem 3. WSL 2: Cladia runs its tools in a Linux of its own (WSL distributions in
rem    %LOCALAPPDATA%\Cladia\linux, made the first time a tool runs). Only WSL itself
rem    is needed. The current WSL lives in Program Files\WSL; Windows' old one
rem    (System32\wsl.exe alone) is updated or installed by full path.
set "WSLDIR=%ProgramFiles%\WSL"
if exist "%WSLDIR%\wsl.exe" (
  "%WSLDIR%\wsl.exe" --version >nul 2>&1
  if not errorlevel 1 (
    echo WSL: ready, %WSLDIR%\wsl.exe
    goto ready
  )
)
echo.
echo Cladia needs WSL ^(Windows Subsystem for Linux^) to run its tools ^(MAFFT, annotation, trees, mapping, BLAST^).
if /i "%~1"=="installer" goto installwsl
choice /c yn /n /m "Install or update WSL now? Windows asks for permission. [y/n] "
if errorlevel 2 goto ready
:installwsl
"%SystemRoot%\System32\wsl.exe" --install --no-distribution
if errorlevel 1 "%SystemRoot%\System32\wsl.exe" --update
if exist "%WSLDIR%\wsl.exe" (
  "%WSLDIR%\wsl.exe" --version >nul 2>&1
  if not errorlevel 1 (
    echo WSL: ready. If Windows asks you to restart, do so before the first job.
    goto ready
  )
)
echo.
echo WSL is not ready yet. Restart the PC and run setup.bat again.
echo If it is still missing, install the latest WSL from
echo https://github.com/microsoft/WSL/releases ^(the .msi for x64^), then run setup.bat again.

if /i not "%~1"=="installer" pause
exit /b 1

:ready
echo.
echo Setup done. Start Cladia with run.bat; check.bat tests the Linux side.
if /i not "%~1"=="installer" pause
exit /b 0
