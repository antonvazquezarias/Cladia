@echo off
rem Starts Cladia without a console window (double-click this file).
cd /d %~dp0
set "VENV=%LOCALAPPDATA%\Cladia\venv"

if not exist "%VENV%\Scripts\pythonw.exe" (
  echo Cladia is not set up yet: double-click setup.bat first.
  pause
  exit /b 1
)

start "" "%VENV%\Scripts\pythonw.exe" -m cladia
