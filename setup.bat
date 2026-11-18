@echo off
setlocal
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
rem 3. Check whether WSL can list distributions. Cladia creates its own on first use.
"%VENV%\Scripts\python.exe" -X utf8 -m cladia.wsl_setup
if not errorlevel 1 goto ready
echo.
echo Cladia needs WSL ^(Windows Subsystem for Linux^) to run its tools ^(MAFFT, annotation, trees, mapping, BLAST^).
if /i "%~1"=="installer" goto installwsl
choice /c yn /n /m "Install or update WSL now? Windows asks for permission. [y/n] "
if errorlevel 2 goto ready
:installwsl
"%SystemRoot%\System32\wsl.exe" --install --no-distribution
set "WSL_INSTALL_EXIT=%errorlevel%"
if "%WSL_INSTALL_EXIT%"=="3010" goto restartwsl
if not "%WSL_INSTALL_EXIT%"=="0" "%SystemRoot%\System32\wsl.exe" --update
"%VENV%\Scripts\python.exe" -X utf8 -m cladia.wsl_setup
set "WSL_CHECK_EXIT=%errorlevel%"
if "%WSL_CHECK_EXIT%"=="0" goto ready
if "%WSL_CHECK_EXIT%"=="2" if "%WSL_INSTALL_EXIT%"=="0" goto restartwsl
echo.
echo WSL is not ready. In PowerShell as administrator, run:
echo     wsl.exe --install --no-distribution
echo Restart Windows and run setup.bat again.

if /i not "%~1"=="installer" pause
exit /b 1

:restartwsl
echo Restart Windows to finish setting up WSL before running Cladia's analysis tools.
if /i not "%~1"=="installer" pause
exit /b 3010

:ready
echo.
echo Setup done. Start Cladia with run.bat; check.bat tests the Linux side.
if /i not "%~1"=="installer" pause
exit /b 0
