@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "POWERSHELL_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%POWERSHELL_EXE%" echo [ERROR] Windows PowerShell 5.1 is required.& exit /b 1
"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0control.ps1" -CommandName "%~1" -Target "%~2"
exit /b %ERRORLEVEL%
