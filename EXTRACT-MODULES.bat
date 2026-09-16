@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "POWERSHELL_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%POWERSHELL_EXE%" echo [ERROR] Windows PowerShell 5.1 is required.& exit /b 1
"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0stack\extract.ps1" -ArchiveDirectory "%~1"
exit /b %ERRORLEVEL%
