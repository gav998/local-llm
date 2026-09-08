@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "CONTROL_VERSION=2026.09.08.6"
set "SCRIPT_RC=0"
set "LOCK_HELD="
set "INSTALL_STAGE="

for %%I in ("%~dp0.") do set "ROOT=%%~fI"
set "APP=%ROOT%\app"
set "WORK=%ROOT%\_work"
set "INSTALL_LOCK=%WORK%\offline-install.lock"
set "POWERSHELL_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"

if not exist "%POWERSHELL_EXE%" (
    echo [ERROR] Windows PowerShell 5.1 is required: "%POWERSHELL_EXE%"
    endlocal & exit /b 1
)
set "LOCAL_LLM_ROOT_TO_VALIDATE=%ROOT%"
"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "$bad=@(33,37,38,60,62,94,124) | ForEach-Object {[char]$_}; if($env:LOCAL_LLM_ROOT_TO_VALIDATE.IndexOfAny([char[]]$bad) -ge 0){exit 1}" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] The project path contains a CMD metacharacter.
    echo [HINT] Move it to a path without: exclamation, percent, ampersand, caret,
    echo [HINT] less-than, greater-than or vertical-bar characters.
    endlocal & exit /b 1
)

if "%~1"=="" goto :MENU
if /i "%~1"=="install" goto :INSTALL
if /i "%~1"=="start" goto :COMMAND_START
if /i "%~1"=="stop" goto :COMMAND_STOP
if /i "%~1"=="status" goto :COMMAND_STATUS
if /i "%~1"=="verify" goto :COMMAND_VERIFY
if /i "%~1"=="config" goto :COMMAND_CONFIG
if /i "%~1"=="devices" goto :COMMAND_DEVICES
if /i "%~1"=="help" goto :USAGE
if /i "%~1"=="--help" goto :USAGE
if /i "%~1"=="-h" goto :USAGE

echo [ERROR] Unknown command: "%~1"
goto :USAGE_ERROR

:MENU
cls
echo ============================================================================
echo local_llm portable control %CONTROL_VERSION%
echo Root: "%ROOT%"
echo ============================================================================
echo.
echo   1. Install / revalidate installation ^(strict GPU gate^)
echo   2. Start core ^(databases, RAGFlow API, web^)
echo   3. Start ingestion ^(core + OCR + embeddings + worker^)
echo   4. Start chat ^(core + chat + embeddings; OCR stopped^)
echo   5. Status
echo   6. Stop all
echo   7. Verify installed payload
echo   8. Edit configuration and model paths
echo   9. List llama.cpp Vulkan devices
echo   0. Exit
echo.
set "MENU_CHOICE="
set /p "MENU_CHOICE=Select: "
if "%MENU_CHOICE%"=="1" call :MENU_RUN install
if "%MENU_CHOICE%"=="2" call :MENU_RUN start core
if "%MENU_CHOICE%"=="3" call :MENU_RUN start ingestion
if "%MENU_CHOICE%"=="4" call :MENU_RUN start chat
if "%MENU_CHOICE%"=="5" call :MENU_RUN status
if "%MENU_CHOICE%"=="6" call :MENU_RUN stop
if "%MENU_CHOICE%"=="7" call :MENU_RUN verify
if "%MENU_CHOICE%"=="8" call :MENU_RUN config edit
if "%MENU_CHOICE%"=="9" call :MENU_RUN devices
if "%MENU_CHOICE%"=="0" goto :SCRIPT_END
goto :MENU

:MENU_RUN
echo.
call "%~f0" %*
set "MENU_RC=%ERRORLEVEL%"
echo.
if not "%MENU_RC%"=="0" echo [ERROR] Command failed with exit code %MENU_RC%.
pause
exit /b 0

:COMMAND_START
if not "%~3"=="" goto :ARGUMENT_ERROR
if "%~2"=="" (
    call :RUN_CONTROL start core
) else (
    call :RUN_CONTROL start "%~2"
)
set "SCRIPT_RC=%ERRORLEVEL%"
goto :SCRIPT_END

:COMMAND_STOP
if not "%~2"=="" goto :ARGUMENT_ERROR
call :RUN_CONTROL stop
set "SCRIPT_RC=%ERRORLEVEL%"
goto :SCRIPT_END

:COMMAND_STATUS
if not "%~2"=="" goto :ARGUMENT_ERROR
call :RUN_CONTROL status
set "SCRIPT_RC=%ERRORLEVEL%"
goto :SCRIPT_END

:COMMAND_VERIFY
if not "%~3"=="" goto :ARGUMENT_ERROR
if /i "%~2"=="--gpu" (
    call :RUN_CONTROL verify --gpu
) else if "%~2"=="" (
    call :RUN_CONTROL verify
) else (
    echo [ERROR] Supported verify option: --gpu
    set "SCRIPT_RC=2"
    goto :SCRIPT_END
)
set "SCRIPT_RC=%ERRORLEVEL%"
goto :SCRIPT_END

:COMMAND_CONFIG
if not "%~3"=="" goto :ARGUMENT_ERROR
if /i "%~2"=="edit" (
    call :RUN_CONTROL config edit
) else if "%~2"=="" (
    call :RUN_CONTROL config show
) else (
    echo [ERROR] Supported config actions: show, edit
    set "SCRIPT_RC=2"
    goto :SCRIPT_END
)
set "SCRIPT_RC=%ERRORLEVEL%"
goto :SCRIPT_END

:COMMAND_DEVICES
if not "%~2"=="" goto :ARGUMENT_ERROR
call :RUN_CONTROL devices
set "SCRIPT_RC=%ERRORLEVEL%"
goto :SCRIPT_END

:RUN_CONTROL
if not exist "%APP%\runtime\python-rag\python.exe" (
    echo [ERROR] local_llm is not installed below this BAT.
    echo [HINT] Run: LOCAL-LLM.bat install
    exit /b 1
)
if not exist "%APP%\config\project\local_llm_ctl.py" (
    echo [ERROR] Installed control helper is missing.
    echo [HINT] Use a prepared bundle set created by version %CONTROL_VERSION% or newer.
    exit /b 1
)
call :SET_PORTABLE_ENV
"%APP%\runtime\python-rag\python.exe" "%APP%\config\project\local_llm_ctl.py" --root "%ROOT%" version --expect "%CONTROL_VERSION%" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Launcher/controller version mismatch.
    echo [HINT] Do not mix files from different prepared bundle sets.
    exit /b 1
)
"%APP%\runtime\python-rag\python.exe" "%APP%\config\project\local_llm_ctl.py" --root "%ROOT%" %*
exit /b %ERRORLEVEL%

:INSTALL
if not "%~2"=="" goto :ARGUMENT_ERROR
echo.
echo ============================================================================
echo local_llm - OFFLINE INSTALLATION
echo Root: "%ROOT%"
echo ============================================================================
echo.
call :ACQUIRE_INSTALL_LOCK
if errorlevel 1 (
    set "SCRIPT_RC=1"
    goto :SCRIPT_END
)

if exist "%APP%\runtime\python-rag\python.exe" (
    if not exist "%APP%\config\project\local_llm_ctl.py" (
        echo [ERROR] An incompatible or partial app directory already exists:
        echo [ERROR]   "%APP%"
        echo [INFO] It was not changed. Move it aside manually after preserving data.
        set "SCRIPT_RC=1"
        goto :SCRIPT_END
    )
    echo [INFO] Payload already exists. Regenerating location-specific configuration
    echo [INFO] and repeating the strict install verification.
    goto :FINALIZE_INSTALL
)
if exist "%APP%" (
    echo [ERROR] A partial app path already exists and was not changed:
    echo [ERROR]   "%APP%"
    echo [INFO] Inspect or move it aside manually before retrying installation.
    set "SCRIPT_RC=1"
    goto :SCRIPT_END
)

call :FIND_BUNDLES
if errorlevel 1 (
    set "SCRIPT_RC=1"
    goto :SCRIPT_END
)
call :CHECK_BUNDLES
if errorlevel 1 (
    set "SCRIPT_RC=1"
    goto :SCRIPT_END
)

set "INSTALL_STAGE=%WORK%\offline-install-%RANDOM%-%RANDOM%"
if exist "%INSTALL_STAGE%" (
    echo [ERROR] Random staging path already exists: "%INSTALL_STAGE%"
    set "SCRIPT_RC=1"
    goto :SCRIPT_END
)
mkdir "%INSTALL_STAGE%" >nul 2>&1
if not exist "%INSTALL_STAGE%\." (
    echo [ERROR] Could not create staging directory: "%INSTALL_STAGE%"
    set "SCRIPT_RC=1"
    goto :SCRIPT_END
)

echo [STEP] Extract bootstrap tools into a private staging directory
"%BUNDLE_DIR%\7zr.exe" x -y "-o%INSTALL_STAGE%" "%BUNDLE_DIR%\00-bootstrap-tools.7z" >"%WORK%\offline-install-extract.log" 2>&1
if errorlevel 1 goto :EXTRACT_FAILED
set "SEVEN_ZIP=%INSTALL_STAGE%\app\tools\7zip\x64\7za.exe"
if not exist "%SEVEN_ZIP%" goto :EXTRACT_FAILED

echo [STEP] Test and extract all nine payload archives
for %%B in (
    "00-bootstrap-tools.7z"
    "10-python-rag-runtime.7z"
    "20-python-ocr-gpu-runtime.7z"
    "21-paddle-models.7z"
    "30-ragflow-backend.7z"
    "31-ragflow-web-dist.7z"
    "40-services.7z"
    "41-config-seed.7z"
    "50-llama-vulkan-runtime.7z"
) do (
    "%SEVEN_ZIP%" t "%BUNDLE_DIR%\%%~B" >>"%WORK%\offline-install-extract.log" 2>&1
    if errorlevel 1 goto :EXTRACT_FAILED
    "%SEVEN_ZIP%" x -y "-o%INSTALL_STAGE%" "%BUNDLE_DIR%\%%~B" >>"%WORK%\offline-install-extract.log" 2>&1
    if errorlevel 1 goto :EXTRACT_FAILED
)

for %%K in (
    "runtime\python-rag\python.exe"
    "runtime\python-ocr\python.exe"
    "config\project\local_llm_ctl.py"
    "config\project\local_llm_supervisor.py"
    "ragflow\api\ragflow_server.py"
    "services\mysql\bin\mysqld.exe"
    "services\elasticsearch\bin\elasticsearch.bat"
    "services\silo\silo.exe"
    "services\valkey\valkey-server.exe"
    "services\caddy\caddy.exe"
    "runtime\llama\llama-server.exe"
) do if not exist "%INSTALL_STAGE%\app\%%~K" (
    echo [ERROR] Extracted payload is missing app\%%~K
    goto :EXTRACT_FAILED
)

echo [STEP] Atomically publish the installed app directory
move "%INSTALL_STAGE%\app" "%APP%" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Could not move the staged app into "%APP%"
    set "SCRIPT_RC=1"
    goto :SCRIPT_END
)
rmdir "%INSTALL_STAGE%" >nul 2>&1
if exist "%INSTALL_STAGE%\." echo [WARN] Empty staging parent could not be removed: "%INSTALL_STAGE%"
set "INSTALL_STAGE="

:FINALIZE_INSTALL
call :RUN_CONTROL install-finalize
set "SCRIPT_RC=%ERRORLEVEL%"
if not "%SCRIPT_RC%"=="0" (
    echo.
    echo [ERROR] Files were preserved, but install.ok was not written.
    echo [INFO] Fix the reported cause and run LOCAL-LLM.bat install again.
)
goto :SCRIPT_END

:EXTRACT_FAILED
echo [ERROR] Archive test or extraction failed.
echo [INFO] Detailed log: "%WORK%\offline-install-extract.log"
echo [INFO] The unpublished staging directory was preserved for inspection:
echo [INFO]   "%INSTALL_STAGE%"
set "SCRIPT_RC=1"
goto :SCRIPT_END

:ACQUIRE_INSTALL_LOCK
if not exist "%WORK%\." mkdir "%WORK%" >nul 2>&1
if not exist "%WORK%\." (
    echo [ERROR] Could not create: "%WORK%"
    exit /b 1
)
2>nul mkdir "%INSTALL_LOCK%"
if errorlevel 1 (
    echo [ERROR] Another install may be running.
    echo [ERROR] Lock: "%INSTALL_LOCK%"
    echo [HINT] Remove it only after confirming that no installation is active.
    exit /b 1
)
set "LOCK_HELD=1"
exit /b 0

:FIND_BUNDLES
set "BUNDLE_DIR="
if exist "%ROOT%\7zr.exe" set "BUNDLE_DIR=%ROOT%"
if not defined BUNDLE_DIR if exist "%ROOT%\_src\prepared\7zr.exe" set "BUNDLE_DIR=%ROOT%\_src\prepared"
if not defined BUNDLE_DIR (
    echo [ERROR] Could not find 7zr.exe and the offline archives.
    echo [INFO] Put LOCAL-LLM.bat beside the prepared archives, or keep them in:
    echo [INFO]   "%ROOT%\_src\prepared"
    exit /b 1
)
echo [INFO] Bundle directory: "%BUNDLE_DIR%"
exit /b 0

:CHECK_BUNDLES
echo [STEP] Check that every required offline payload file is present
for %%F in (
    "00-bootstrap-tools.7z"
    "10-python-rag-runtime.7z"
    "20-python-ocr-gpu-runtime.7z"
    "21-paddle-models.7z"
    "30-ragflow-backend.7z"
    "31-ragflow-web-dist.7z"
    "40-services.7z"
    "41-config-seed.7z"
    "50-llama-vulkan-runtime.7z"
    "7zr.exe"
    "LOCAL-LLM.bat"
    "README.md"
) do (
    if not exist "%BUNDLE_DIR%\%%~F" (
        echo [ERROR] Required offline payload is missing: %%~F
        exit /b 1
    )
    if exist "%BUNDLE_DIR%\%%~F\." (
        echo [ERROR] Required offline payload is not a file: %%~F
        exit /b 1
    )
)
echo [OK] All required offline payload files are present.
exit /b 0

:SET_PORTABLE_ENV
set "LOCAL_LLM_ROOT_TO_VALIDATE="
set "HOME=%APP%\data\profile"
set "USERPROFILE=%APP%\data\profile"
set "APPDATA=%APP%\data\profile\AppData\Roaming"
set "LOCALAPPDATA=%APP%\data\profile\AppData\Local"
set "TEMP=%APP%\temp"
set "TMP=%APP%\temp"
set "POWERSHELL_TELEMETRY_OPTOUT=1"
set "PSModuleAnalysisCachePath=%APP%\cache\powershell\ModuleAnalysisCache"
set "DOTNET_CLI_HOME=%APP%\data\profile"
set "NUGET_PACKAGES=%APP%\cache\nuget"
set "PYTHONNOUSERSITE=1"
set "PYTHONHOME="
set "PYTHONPATH="
set "VIRTUAL_ENV="
set "CONDA_PREFIX="
set "HF_HUB_OFFLINE=1"
set "TRANSFORMERS_OFFLINE=1"
set "NO_PROXY=127.0.0.1,localhost"
set "PATH=%APP%\runtime\shared-dll;%APP%\runtime\python-rag;%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem"
exit /b 0

:USAGE
echo Usage:
echo   LOCAL-LLM.bat                         Interactive menu
echo   LOCAL-LLM.bat install                 Offline install + mandatory GPU E2E
echo   LOCAL-LLM.bat start [core^|ingestion^|chat]
echo   LOCAL-LLM.bat status
echo   LOCAL-LLM.bat stop
echo   LOCAL-LLM.bat verify [--gpu]
echo   LOCAL-LLM.bat config [show^|edit]
echo   LOCAL-LLM.bat devices
set "SCRIPT_RC=0"
goto :SCRIPT_END

:USAGE_ERROR
set "SCRIPT_RC=2"
goto :SCRIPT_END

:ARGUMENT_ERROR
echo [ERROR] Unexpected command arguments.
set "SCRIPT_RC=2"
goto :SCRIPT_END

:SCRIPT_END
if defined LOCK_HELD if exist "%INSTALL_LOCK%" (
    rmdir "%INSTALL_LOCK%" >nul 2>&1
    if exist "%INSTALL_LOCK%" (
        echo [ERROR] Could not release install lock: "%INSTALL_LOCK%"
        set "SCRIPT_RC=1"
    )
)
endlocal & exit /b %SCRIPT_RC%
