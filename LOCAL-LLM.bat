@echo off
setlocal EnableExtensions DisableDelayedExpansion

if not "%~1"=="" goto run_args

:menu
cls
echo.
echo ==============================
echo        LOCAL LLM MENU
echo ==============================
echo.
echo   1. Start core
echo   2. Start ingestion
echo   3. Start chat
echo   4. Stop all
echo   5. Status
echo   6. Verify
echo   7. Devices
echo   8. Install
echo   9. Start ingestion CPU test
echo   0. Exit
echo.
"%SystemRoot%\System32\choice.exe" /C 1234567890 /N /M "Select action: "
set "MENU_CHOICE=%ERRORLEVEL%"

if "%MENU_CHOICE%"=="10" exit /b 0
if "%MENU_CHOICE%"=="1" call :run_control start core
if "%MENU_CHOICE%"=="2" call :run_control start ingestion
if "%MENU_CHOICE%"=="3" call :run_control start chat
if "%MENU_CHOICE%"=="4" call :run_control stop
if "%MENU_CHOICE%"=="5" call :run_control status
if "%MENU_CHOICE%"=="6" call :run_control verify
if "%MENU_CHOICE%"=="7" call :run_control devices
if "%MENU_CHOICE%"=="8" call :run_control install
if "%MENU_CHOICE%"=="9" call :run_control start ingestion-cpu
goto menu

:run_control
echo.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0stack\control.ps1" -CommandName "%~1" -Profile "%~2"
set "CONTROL_EXIT=%ERRORLEVEL%"
echo.
if not "%CONTROL_EXIT%"=="0" echo Command finished with error code %CONTROL_EXIT%.
pause
exit /b %CONTROL_EXIT%

:run_args
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0stack\control.ps1" -CommandName "%~1" -Profile "%~2"
exit /b %ERRORLEVEL%
