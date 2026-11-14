@echo off
rem Checks the container side: which program Cladia uses, tools image, engine images.
cd /d %~dp0
set "VENV=%LOCALAPPDATA%\Cladia\venv"

if not exist "%VENV%\Scripts\python.exe" (
  echo Cladia is not set up yet: double-click setup.bat first.
  pause
  exit /b 1
)

"%VENV%\Scripts\python" scripts\check_windows.py
pause
