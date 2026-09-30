@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"

set "VSCODE_EXE=%~dp0..\Code.exe"
if not exist "%VSCODE_EXE%" (
  echo [Portable VS Code] Code.exe not found: "%VSCODE_EXE%" 1>&2
  echo Copy the whole vscode folder next to Code.exe, then run vscode\start.bat. 1>&2
  exit /b 1
)

if not exist "%~dp0user-data\User" mkdir "%~dp0user-data\User"
if not exist "%~dp0extensions" mkdir "%~dp0extensions"
if not exist "%~dp0profile" mkdir "%~dp0profile"

set "USERPROFILE=%~dp0profile"
set "HOME=%~dp0profile"
set "COPILOT_HOME=%~dp0profile\.copilot"

start "Portable VS Code" "%VSCODE_EXE%" --user-data-dir "%~dp0user-data" --extensions-dir "%~dp0extensions" %*
exit /b 0
