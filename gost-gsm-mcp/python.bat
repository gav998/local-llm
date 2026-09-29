@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" --python %*
exit /b %ERRORLEVEL%
