@echo off
rem Double-click to install what is needed (Python) and start the MIDI Footswitch Programmer.
cd /d "%~dp0"
set PYCMD=
py -3 -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1 && set PYCMD=py -3
if not defined PYCMD python -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1 && set PYCMD=python
if not defined PYCMD (
  echo Python 3.11+ not found. Installing it with winget...
  winget install -e --id Python.Python.3.13 --accept-package-agreements --accept-source-agreements
  if errorlevel 1 (
    echo.
    echo Automatic install failed. Install Python from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^), then run this file again.
    pause
    exit /b 1
  )
  echo.
  echo Python installed. Close this window and double-click this file once more.
  pause
  exit /b 0
)
%PYCMD% tools\dev.py run
if errorlevel 1 pause
