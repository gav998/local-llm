@echo off
setlocal EnableExtensions DisableDelayedExpansion
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0stack\control.ps1" -CommandName "%~1" -Profile "%~2"
exit /b %ERRORLEVEL%
