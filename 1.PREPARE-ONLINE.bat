@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "LOCK_HELD="
set "SCRIPT_RC="
set "PACKAGE_STAGE="

REM ============================================================================
REM local_llm - ONLINE PREPARATION ONLY
REM Native Windows x64, portable files, no Docker, no admin operations.
REM
REM Core vendor files are NEVER downloaded by this BAT. When one is missing,
REM the script prints its fixed URL and waits until the user puts it in _src.
REM pip/npm/Hugging Face are used only to resolve transitive dependencies while
REM this online build is being prepared. Their completed outputs are archived.
REM ============================================================================

set "PROJECT_VERSION=2026.09.11.1"
REM Step resume markers intentionally use a component graph version instead of
REM PROJECT_VERSION so launcher-only fixes do not invalidate completed runtimes.
set "RESUME_GRAPH_VERSION=2026.09.08.8"
set "SEVEN_ZIP_VERSION=26.02"
set "SEVEN_ZIP_TAG=2602"
set "RAGFLOW_VERSION=0.27.1"
set "PY_RAG_VERSION=3.13.15"
set "PY_OCR_VERSION=3.11.16"
set "PY_STANDALONE_RELEASE=20260901"
set "PIN_UV_VERSION=0.12.9"
set "PIN_PIP_VERSION=26.2.1"
set "NODE_VERSION=24.20.0"
set "MINGIT_PACKAGE_VERSION=2.55.0.5"
set "MINGIT_GIT_VERSION=2.55.0.windows.5"
set "MINGIT_TAG=2.55.0.windows.5"
set "PADDLE_VERSION=3.3.1"
set "PADDLEOCR_VERSION=3.7.0"
set "PADDLEX_VERSION=3.7.2"
set "DEJAVU_SANS_VERSION=2.37"
set "DATRIE_VERSION=0.8.3"
set "NUMPY_VERSION=2.3.5"
set "XGBOOST_VERSION=2.1.4"
set "SCIKIT_LEARN_VERSION=1.8.0"
set "GRASPOLOGIC_NATIVE_VERSION=1.2.5"
set "VC_REDIST_VERSION=14.44.35211"
set "MYSQL_VERSION=8.0.40"
set "ELASTIC_VERSION=8.11.3"
set "VALKEY_VERSION=8.1.6"
set "CADDY_VERSION=2.11.4"
set "LLAMA_BUILD=b10786"

call :Init
if errorlevel 1 goto :SCRIPT_END

call :Main "%~1"
set "SCRIPT_RC=%ERRORLEVEL%"
goto :SCRIPT_END

REM ============================================================================
REM MAIN
REM ============================================================================

:Main
set "PREPARE_MODE=%~1"
if defined PREPARE_MODE if /i not "%PREPARE_MODE%"=="artifacts-only" if /i not "%PREPARE_MODE%"=="gpu-test" (
    echo [ERROR] Unknown argument: %PREPARE_MODE%
    echo [INFO] Supported arguments: artifacts-only, gpu-test
    exit /b 2
)
set "GPU_VALIDATION=not-run"

echo.
echo ============================================================================
echo local_llm - ONLINE PREPARATION
echo Root: %ROOT%
echo ============================================================================
echo.
echo This script does not install services, drivers, registry keys or system PATH.
echo Every generated file stays below this project directory.
echo.

call :AcquireLock
if errorlevel 1 exit /b 1

if defined NOTEST_MODE (
    echo [WARN] notest is present: packaging the current app tree without tests,
    echo [WARN] cleanup, probes, audits, tree seals, archive tests or rehydration.
    set "LOCAL_LLM_LIVE_LOGS=0"
    call :PackagePreparedOutput
    if errorlevel 1 exit /b 1
    echo.
    echo [OK] NOTEST PACKAGING COMPLETED
    echo [WARN] This prepared set is intentionally unverified.
    echo Offline bundles: %PREPARED%
    exit /b 0
)

call :CleanupInterruptedRehydrateTrees
if errorlevel 1 exit /b 1

call :RecoverPreparedOutput
if errorlevel 1 exit /b 1

call :CopyProjectInputs
if errorlevel 1 exit /b 1

call :ValidateMutableResumeState
if errorlevel 1 exit /b 1

call :AcquireCoreArtifacts
if errorlevel 1 exit /b 1

call :VerifyArtifactVersions
if errorlevel 1 exit /b 1

if /i "%~1"=="artifacts-only" (
    echo.
    echo [OK] All manually supplied artifacts are present and expanded.
    echo [INFO] Run this BAT without arguments to build and package the runtimes.
    exit /b 0
)

call :PrepareSharedRuntimeDlls
if errorlevel 1 exit /b 1

call :PrepareRagflowPython
if errorlevel 1 exit /b 1

call :PrepareRagflowAssets
if errorlevel 1 exit /b 1

call :VerifyRagflowRuntime
if errorlevel 1 exit /b 1

call :BuildRagflowWeb
if errorlevel 1 exit /b 1

call :PrepareOcrPython
if errorlevel 1 exit /b 1

call :RunOcrContract
if errorlevel 1 exit /b 1

if /i "%PREPARE_MODE%"=="gpu-test" (
    call :RunStrictGpuE2E
    if errorlevel 1 exit /b 1
    set "GPU_VALIDATION=passed"
) else (
    if exist "%APP%\config\ocr-gpu-smoke.json" del /f /q "%APP%\config\ocr-gpu-smoke.json" >nul 2>&1
    if exist "%APP%\config\ocr-gpu-smoke.json" (
        echo [ERROR] Could not remove a stale target GPU validation record.
        exit /b 1
    )
    echo [INFO] Target GPU inference was not run during dependency preparation.
    echo [INFO] Run 1.PREPARE-ONLINE.bat gpu-test on the GTX 1080 Windows PC,
    echo [INFO] or require the same E2E test in 2.INSTALL-OFFLINE.bat before install.ok.
)

call :ProbePortableBinaries
if errorlevel 1 exit /b 1

call :AuditPortableRuntimes
if errorlevel 1 exit /b 1

call :PreparePortableSeed
if errorlevel 1 exit /b 1

call :SealMutableRuntimeTrees
if errorlevel 1 exit /b 1

call :SealImmutableArtifactTrees
if errorlevel 1 exit /b 1

call :PackagePreparedOutput
if errorlevel 1 exit /b 1

echo.
echo ============================================================================
echo [OK] ONLINE PREPARATION COMPLETED
echo Ready runtime : %APP%
echo Offline bundles: %PREPARED%
echo ============================================================================
echo.
exit /b 0

REM ============================================================================
REM INITIALIZATION AND PORTABLE ENVIRONMENT
REM ============================================================================

:Init
for %%I in ("%~dp0.") do set "ROOT=%%~fI"
set "NOTEST_MODE="
if exist "%ROOT%\notest" set "NOTEST_MODE=1"
set "APP=%ROOT%\app"
set "POWERSHELL_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%POWERSHELL_EXE%" (
    echo [ERROR] Windows PowerShell 5.1 is required: %POWERSHELL_EXE%
    exit /b 1
)
if not defined ComSpec set "ComSpec=%SystemRoot%\System32\cmd.exe"
if not exist "%ComSpec%" (
    echo [ERROR] Windows command processor is required: %ComSpec%
    exit /b 1
)
REM Before touching managed children, run PowerShell with neutral roots that
REM point only at the already-open project directory.
set "HOME=%ROOT%"
set "USERPROFILE=%ROOT%"
set "APPDATA=%ROOT%"
set "LOCALAPPDATA=%ROOT%"
set "TEMP=%ROOT%"
set "TMP=%ROOT%"
set "POWERSHELL_TELEMETRY_OPTOUT=1"
set "PSModuleAnalysisCachePath=NUL"
set "DOTNET_CLI_HOME=%ROOT%"
set "NUGET_PACKAGES=%ROOT%"
if not defined NOTEST_MODE (
    call :AuditExistingManagedPaths
    if errorlevel 1 exit /b 1
)
set "ROOT_TO_VALIDATE=%ROOT%"
"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "$bad=[char[]](32,33,35,38,40,41,37,59,94,60,62,124); if ($env:ROOT_TO_VALIDATE.IndexOfAny($bad) -ge 0) { exit 1 }" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] The project path contains a CMD/URI-unsafe character or a space.
    echo [ERROR] Use a short path such as X:\local_llm.
    exit /b 1
)
call :SetEarlyPortableEnv
if errorlevel 1 exit /b 1

set "SRC=%ROOT%\_src"
set "PROJECT=%SRC%\project"
set "LOGS=%SRC%\logs"
set "PREPARED=%SRC%\prepared"
set "PREPARED_BACKUP=%SRC%\prepared.previous"
set "WORK=%ROOT%\_work"
set "LOCK_DIR=%WORK%\prepare.lock"

for %%D in (
    "%SRC%"
    "%PROJECT%"
    "%LOGS%"
    "%SRC%\package-cache\pip"
    "%SRC%\package-cache\uv"
    "%SRC%\wheelhouse\rag"
    "%SRC%\wheelhouse\ocr"
    "%APP%"
    "%APP%\assets"
    "%APP%\build"
    "%APP%\build\project"
    "%APP%\cache"
    "%APP%\cache\huggingface"
    "%APP%\cache\paddlex"
    "%APP%\cache\pip"
    "%APP%\cache\uv"
    "%APP%\config"
    "%APP%\config\locks"
    "%APP%\config\tree-manifests"
    "%APP%\data"
    "%APP%\data\nltk"
    "%APP%\data\profile\AppData\Local"
    "%APP%\data\profile\AppData\Roaming"
    "%APP%\models\embed"
    "%APP%\models\llm"
    "%APP%\runtime"
    "%APP%\services"
    "%APP%\services\ocr"
    "%APP%\temp"
    "%APP%\tools"
    "%WORK%"
) do (
    if not exist "%%~D\." mkdir "%%~D" >nul 2>&1
    if not exist "%%~D\." (
        echo [ERROR] Could not create portable directory: %%~D
        exit /b 1
    )
)

call :SetPortableEnv
exit /b %ERRORLEVEL%

:AuditExistingManagedPaths
set "MANAGED_ROOT=%ROOT%"
"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $base=Get-Item -LiteralPath $env:MANAGED_ROOT -Force; if(-not $base.PSIsContainer){throw 'Project root is not a directory'}; if(($base.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw 'Project root is a reparse point'}; $pending=New-Object System.Collections.Stack; foreach($name in @('app','_src','_work')){$path=Join-Path $base.FullName $name; if(Test-Path -LiteralPath $path){$item=Get-Item -LiteralPath $path -Force; if(($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw ('Managed path is a reparse point: '+$item.FullName)}; if(-not $item.PSIsContainer){throw ('Managed root is not a directory: '+$item.FullName)}; $pending.Push($item)}}; while($pending.Count -gt 0){$dir=$pending.Pop(); foreach($item in Get-ChildItem -LiteralPath $dir.FullName -Force -ErrorAction Stop){if(($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw ('Reparse point in managed tree: '+$item.FullName)}; if($item.PSIsContainer){$pending.Push($item)}}}" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] app, _src or _work contains a reparse point or invalid managed root.
    echo [ERROR] Refusing to write through a symlink or junction.
    exit /b 1
)
exit /b 0

:SetEarlyPortableEnv
call :MakeDirChecked "%APP%\data\profile\AppData\Local"
if errorlevel 1 exit /b 1
call :MakeDirChecked "%APP%\data\profile\AppData\Roaming"
if errorlevel 1 exit /b 1
call :MakeDirChecked "%APP%\temp"
if errorlevel 1 exit /b 1
call :MakeDirChecked "%APP%\cache\powershell"
if errorlevel 1 exit /b 1
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
exit /b 0

:RecoverPreparedOutput
setlocal EnableDelayedExpansion
set "BROKEN_PREPARED=%WORK%\prepared-incomplete"
set "BROKEN_BACKUP=%WORK%\prepared-backup-invalid"
set "CURRENT_EXISTS=0"
set "CURRENT_VALID=0"
set "BACKUP_EXISTS=0"
set "BACKUP_VALID=0"

if exist "%PREPARED%" if not exist "%PREPARED%\." (
    echo [ERROR] Prepared output path is not a directory: %PREPARED%
    endlocal & exit /b 1
)
if exist "%PREPARED_BACKUP%" if not exist "%PREPARED_BACKUP%\." (
    echo [ERROR] Prepared backup path is not a directory: %PREPARED_BACKUP%
    endlocal & exit /b 1
)
if exist "%PREPARED%\." (
    set "CURRENT_EXISTS=1"
    call :ValidatePreparedSet "%PREPARED%"
    if not errorlevel 1 set "CURRENT_VALID=1"
)
if exist "%PREPARED_BACKUP%\." (
    set "BACKUP_EXISTS=1"
    call :ValidatePreparedSet "%PREPARED_BACKUP%"
    if not errorlevel 1 set "BACKUP_VALID=1"
)

if "!CURRENT_VALID!"=="1" goto :RECOVER_CURRENT_VALID

if "!CURRENT_EXISTS!"=="1" (
    if exist "!BROKEN_PREPARED!" (
        echo [ERROR] Recovery target already exists: !BROKEN_PREPARED!
        echo [INFO] Inspect and move it before rerunning preparation.
        endlocal & exit /b 1
    )
    echo [WARN] Preserving an invalid prepared bundle set for inspection.
    move "%PREPARED%" "!BROKEN_PREPARED!" >nul 2>&1
    if errorlevel 1 (endlocal & exit /b 1)
)
if "!BACKUP_VALID!"=="1" (
    echo [WARN] Recovering the previous complete prepared bundle set.
    move "%PREPARED_BACKUP%" "%PREPARED%" >nul 2>&1
    if errorlevel 1 (endlocal & exit /b 1)
    endlocal & exit /b 0
)
if "!BACKUP_EXISTS!"=="1" (
    echo [ERROR] Neither prepared output nor its backup is verifiably complete.
    echo [ERROR] Both invalid sets were preserved for inspection.
    endlocal & exit /b 1
)
endlocal & exit /b 0

:RECOVER_CURRENT_VALID
if "!BACKUP_EXISTS!"=="0" (endlocal & exit /b 0)
if "!BACKUP_VALID!"=="1" (
    call :RemoveTreeChecked "%PREPARED_BACKUP%"
    if errorlevel 1 (endlocal & exit /b 1)
    endlocal & exit /b 0
)
if exist "!BROKEN_BACKUP!" (
    echo [ERROR] Invalid-backup recovery target already exists: !BROKEN_BACKUP!
    endlocal & exit /b 1
)
echo [WARN] Current prepared output is valid; preserving its invalid old backup.
move "%PREPARED_BACKUP%" "!BROKEN_BACKUP!" >nul 2>&1
if errorlevel 1 (endlocal & exit /b 1)
endlocal & exit /b 0

:ValidatePreparedSet
setlocal
set "VALIDATE_PREPARED_DIR=%~1"
if not exist "%~1\." (endlocal & exit /b 1)
"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $root=Get-Item -LiteralPath $env:VALIDATE_PREPARED_DIR -Force; if(-not $root.PSIsContainer -or (($root.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)){throw 'Invalid prepared root'}; $required=@('00-bootstrap-tools.7z','10-python-rag-runtime.7z','20-python-ocr-gpu-runtime.7z','21-paddle-models.7z','30-ragflow-backend.7z','31-ragflow-web-dist.7z','40-services.7z','41-config-seed.7z','50-llama-vulkan-runtime.7z','7zr.exe','LOCAL-LLM.bat','README.md','PORTABILITY-AUDIT.json','build-info.txt','prepared.ok'); $optional=@('local_llm_ctl.py'); $allowed=@{}; foreach($name in $required){$allowed[$name.ToLowerInvariant()]=$true}; foreach($name in $optional){$allowed[$name.ToLowerInvariant()]=$true}; foreach($item in Get-ChildItem -LiteralPath $root.FullName -Force){if(($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $item.PSIsContainer -or -not $allowed.ContainsKey($item.Name.ToLowerInvariant())){throw ('Unexpected prepared entry: '+$item.Name)}}; foreach($name in $required){if(-not (Test-Path -LiteralPath (Join-Path $root.FullName $name) -PathType Leaf)){throw ('Missing prepared file: '+$name)}}; if((Get-Content -LiteralPath (Join-Path $root.FullName 'prepared.ok') -Raw).Trim() -ne 'prepared=1'){throw 'Invalid prepared marker'}; $audit=Get-Content -LiteralPath (Join-Path $root.FullName 'PORTABILITY-AUDIT.json') -Raw | ConvertFrom-Json; if($audit.passed -ne $true){throw 'Portability audit did not pass'}; if(-not (Select-String -LiteralPath (Join-Path $root.FullName 'build-info.txt') -Pattern '^local_llm_prepare_version=' -Quiet)){throw 'Missing build version'}" >nul 2>&1
set "VALIDATE_RC=%ERRORLEVEL%"
endlocal & exit /b %VALIDATE_RC%

:SetPortableEnv
set "HOME=%APP%\data\profile"
set "USERPROFILE=%APP%\data\profile"
set "APPDATA=%APP%\data\profile\AppData\Roaming"
set "LOCALAPPDATA=%APP%\data\profile\AppData\Local"
set "TEMP=%APP%\temp"
set "TMP=%APP%\temp"

REM Remove inherited per-machine/user package-manager switches before defining
REM this build's explicit portable settings. General HTTP(S) proxy variables are
REM intentionally retained for managed networks.
for /f "tokens=1 delims==" %%V in ('set PIP_ 2^>nul') do set "%%V="
for /f "tokens=1 delims==" %%V in ('set UV_ 2^>nul') do set "%%V="
for /f "tokens=1 delims==" %%V in ('set npm_config_ 2^>nul') do set "%%V="

set "XDG_CACHE_HOME=%APP%\cache"
set "XDG_CONFIG_HOME=%APP%\config\xdg"
set "XDG_DATA_HOME=%APP%\data\xdg"
set "HF_HOME=%APP%\cache\huggingface"
set "HUGGINGFACE_HUB_CACHE=%APP%\cache\huggingface\hub"
set "TRANSFORMERS_CACHE=%APP%\cache\huggingface\transformers"
set "PIP_CACHE_DIR=%SRC%\package-cache\pip"
set "UV_CACHE_DIR=%SRC%\package-cache\uv"
set "UV_PYTHON_INSTALL_DIR=%APP%\runtime\uv-python"
set "UV_PYTHON_BIN_DIR=%APP%\runtime\uv-python-bin"
set "UV_PYTHON_NO_REGISTRY=1"
set "UV_LINK_MODE=copy"
set "npm_config_cache=%APP%\cache\npm"
set "NPM_CONFIG_CACHE=%APP%\cache\npm"
set "PADDLE_HOME=%APP%\cache\paddle"
set "PADDLE_PDX_CACHE_HOME=%APP%\cache\paddlex"
set "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=1"
set "PADDLE_PDX_DISABLE_DEVICE_FALLBACK=1"
set "PADDLE_PDX_SERVING_SERIAL_PIPELINE_CALLS=1"
if not defined LOCAL_OCR_GPU_INDEX set "LOCAL_OCR_GPU_INDEX=0"
set "NLTK_DATA=%APP%\data\nltk"
set "TIKTOKEN_CACHE_DIR=%APP%\cache\tiktoken"
set "CUDA_CACHE_PATH=%APP%\cache\nvidia"
set "PYTHONPYCACHEPREFIX=%APP%\cache\python-bytecode"
set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
set "PIP_NO_WARN_SCRIPT_LOCATION=1"
set "PIP_CONFIG_FILE=NUL"
set "PIP_INDEX_URL="
set "PIP_EXTRA_INDEX_URL="
set "PIP_FIND_LINKS="
set "PIP_NO_INDEX="
set "PIP_REQUIRE_VIRTUALENV="
set "PIP_TARGET="
set "PIP_PREFIX="
set "PIP_USER="
set "UV_NO_CONFIG=1"
set "UV_NO_MANAGED_PYTHON=1"
set "UV_DEFAULT_INDEX="
set "UV_INDEX="
set "UV_EXTRA_INDEX_URL="
set "UV_FIND_LINKS="
set "UV_OFFLINE="
set "UV_OVERRIDE="
set "UV_EXCLUDE="
set "UV_CONSTRAINT="
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONSTARTUP="
set "PYTHONUSERBASE="
set "VIRTUAL_ENV="
set "CONDA_PREFIX="
set "CONDA_DEFAULT_ENV="
set "NODE_PATH="
set "NODE_OPTIONS="
set "CLASSPATH="
set "JAVA_TOOL_OPTIONS="
set "_JAVA_OPTIONS="
set "JDK_JAVA_OPTIONS="
set "npm_config_registry=https://registry.npmjs.org/"
set "NPM_CONFIG_REGISTRY=https://registry.npmjs.org/"
set "npm_config_userconfig=%APP%\config\npmrc"
set "npm_config_globalconfig=NUL"
set "npm_config_prefix=%APP%\build\npm-prefix"
set "NPM_CONFIG_USERCONFIG=%APP%\config\npmrc"
set "NPM_CONFIG_GLOBALCONFIG=NUL"
set "NPM_CONFIG_PREFIX=%APP%\build\npm-prefix"
set "GIT_CONFIG_GLOBAL=%APP%\config\gitconfig"
set "GIT_CONFIG_NOSYSTEM=1"
set "GIT_TERMINAL_PROMPT=0"
set "GIT_ASKPASS="
set "NO_PROXY=127.0.0.1,localhost"

for %%D in (
    "%HUGGINGFACE_HUB_CACHE%"
    "%TRANSFORMERS_CACHE%"
    "%npm_config_cache%"
    "%PADDLE_HOME%"
    "%PADDLE_PDX_CACHE_HOME%"
    "%NLTK_DATA%"
    "%TIKTOKEN_CACHE_DIR%"
    "%CUDA_CACHE_PATH%"
    "%PYTHONPYCACHEPREFIX%"
    "%APP%\temp\java"
) do (
    call :MakeDirChecked "%%~D"
    if errorlevel 1 exit /b 1
)

REM 7zr.exe is only a bootstrap extractor for .7z files. 7za.exe handles the
REM ZIP/TAR/GZIP formats used by the rest of this preparation workflow.
set "SEVEN_ZIP_BOOTSTRAP=%APP%\tools\7zip-bootstrap\7zr.exe"
set "SEVEN_ZIP=%APP%\tools\7zip\x64\7za.exe"
set "UV_EXE=%APP%\build\uv\uv.exe"
set "NODE_DIR=%APP%\build\node"
set "PORTABLE_GIT_DIR=%APP%\build\git"
set "RAG_PY_DIR=%APP%\runtime\python-rag"
set "OCR_PY_DIR=%APP%\runtime\python-ocr"
set "RAG_PY=%RAG_PY_DIR%\python.exe"
set "OCR_PY=%OCR_PY_DIR%\python.exe"
set "RAGFLOW_DIR=%APP%\ragflow"
set "RAGFLOW_DIR_URI=%RAGFLOW_DIR:\=/%"
set "TIKA_SERVER_JAR=file:///%RAGFLOW_DIR_URI%/tika-server-standard-3.3.0.jar"
set "RAG_WHEELHOUSE=%SRC%\wheelhouse\rag"
set "OCR_WHEELHOUSE=%SRC%\wheelhouse\ocr"
set "SHARED_DLL_DIR=%APP%\runtime\shared-dll"
set "RAG_PY_TREE_MARKER=%APP%\config\python-rag-tree.ok"
set "OCR_PY_TREE_MARKER=%APP%\config\python-ocr-tree.ok"
set "RAGFLOW_TREE_MARKER=%APP%\config\ragflow-tree.ok"
set "RAG_PY_TREE_FINGERPRINT=tree-schema=1;project=%PROJECT_VERSION%;component=python-rag"
set "OCR_PY_TREE_FINGERPRINT=tree-schema=1;project=%PROJECT_VERSION%;component=python-ocr"
set "RAGFLOW_TREE_FINGERPRINT=tree-schema=1;project=%PROJECT_VERSION%;component=ragflow"
set "RAGFLOW_TREE_EXCLUDES=conf/local.service_conf.yaml;logs/"
set "PATH=%SHARED_DLL_DIR%;%NODE_DIR%;%PORTABLE_GIT_DIR%\cmd;%PORTABLE_GIT_DIR%\mingw64\bin;%PATH%"
exit /b 0

:AcquireLock
2>nul mkdir "%LOCK_DIR%"
if errorlevel 1 (
    echo [ERROR] Another preparation process may be running.
    echo [ERROR] Lock directory: %LOCK_DIR%
    echo [HINT] Remove that directory only after confirming no other run is active.
    exit /b 1
)
set "LOCK_HELD=1"
exit /b 0

:CleanupInterruptedRehydrateTrees
for /d %%D in ("%WORK%\rehydrate-*") do if exist "%%~fD\." (
    echo [INFO] Removing interrupted rehydrate tree: %%~fD
    call :RemoveTreeChecked "%%~fD"
    if errorlevel 1 exit /b 1
)
exit /b 0

:CopyProjectInputs
call :RemoveTreeChecked "%APP%\build\project"
if errorlevel 1 exit /b 1
call :MakeDirChecked "%APP%\build\project"
if errorlevel 1 exit /b 1
for %%F in (
    "pp-structure-v3-8gb.yaml"
    "requirements-ocr.txt"
    "ragflow-windows-additions.txt"
    "ragflow-windows-excludes.txt"
    "ragflow-windows-overrides.txt"
    "prepare_ragflow_assets.py"
    "prepare_ragflow_windows.py"
    "prepare_wheelhouse_lock.py"
    "graphrag_native_adapter.py"
    "verify_ragflow_runtime.py"
    "sanitize_python_runtime.py"
    "tree_fingerprint.ps1"
    "remove_tree.ps1"
    "ocr_job_gateway.py"
    "test_ocr_job_gateway_contract.py"
    "ocr_ragflow_e2e.py"
    "audit_portability.py"
    "local_llm_ctl.py"
    "local_llm_supervisor.py"
) do (
    if not exist "%PROJECT%\%%~F" (
        echo [ERROR] Project input is missing: %PROJECT%\%%~F
        exit /b 1
    )
    copy /y "%PROJECT%\%%~F" "%APP%\build\project\%%~F" >nul
    if errorlevel 1 exit /b 1
)
call :RemoveTreeChecked "%APP%\config\project"
if errorlevel 1 exit /b 1
call :CopyTree "%APP%\build\project" "%APP%\config\project"
if errorlevel 1 exit /b 1
copy /y "%PROJECT%\pp-structure-v3-8gb.yaml" "%APP%\config\pp-structure-v3-8gb.yaml" >nul
if errorlevel 1 exit /b 1
copy /y "%PROJECT%\requirements-ocr.txt" "%APP%\config\requirements-ocr.txt" >nul
if errorlevel 1 exit /b 1
for %%F in (ocr_job_gateway.py test_ocr_job_gateway_contract.py ocr_ragflow_e2e.py) do (
    copy /y "%PROJECT%\%%F" "%APP%\services\ocr\%%F" >nul
    if errorlevel 1 exit /b 1
)
exit /b 0

:ValidateMutableResumeState
call :ReportMutableResumeTree "%RAG_PY_DIR%" "%RAG_PY_TREE_MARKER%" "%RAG_PY_TREE_FINGERPRINT%" "" "RAGFlow Python runtime"
if errorlevel 1 exit /b 1
call :ReportMutableResumeTree "%RAGFLOW_DIR%" "%RAGFLOW_TREE_MARKER%" "%RAGFLOW_TREE_FINGERPRINT%" "%RAGFLOW_TREE_EXCLUDES%" "RAGFlow source tree"
if errorlevel 1 exit /b 1
call :ReportMutableResumeTree "%OCR_PY_DIR%" "%OCR_PY_TREE_MARKER%" "%OCR_PY_TREE_FINGERPRINT%" "" "OCR Python runtime"
if errorlevel 1 exit /b 1
exit /b 0

:ReportMutableResumeTree
if not exist "%~1\." exit /b 0
call :MarkerMatches "%~2" "%~3"
if not errorlevel 1 (
    echo [INFO] %~5 has a current final tree seal marker; preserving it for validation before packaging.
    exit /b 0
)
if exist "%~2" (
    echo [INFO] %~5 final tree seal is stale or incomplete; preserving it for resumable prepare.
) else (
    echo [INFO] %~5 is not final-sealed yet; preserving completed step outputs for resumable prepare.
)
exit /b 0

:MutableTreeMatches
if not exist "%~1\." exit /b 1
call :MarkerMatches "%~2" "%~3"
if errorlevel 1 exit /b 1
call :TreeMatchesMarker "%~1" "%~2" "%~4"
exit /b %ERRORLEVEL%

REM ============================================================================
REM MANUALLY SUPPLIED CORE ARTIFACTS
REM ============================================================================

:AcquireCoreArtifacts
echo [STEP] Validate and expand manually supplied vendor artifacts

set "SEVEN_ZIP=%SEVEN_ZIP_BOOTSTRAP%"
call :EnsureArtifact "7zr.exe" "tools\7zip-bootstrap" "7zr.exe" "https://github.com/ip7z/7zip/releases/download/%SEVEN_ZIP_VERSION%/7zr.exe"
if errorlevel 1 exit /b 1
call :EnsureArtifact "7z%SEVEN_ZIP_TAG%-extra.7z" "tools\7zip" "x64\7za.exe" "https://github.com/ip7z/7zip/releases/download/%SEVEN_ZIP_VERSION%/7z%SEVEN_ZIP_TAG%-extra.7z"
if errorlevel 1 exit /b 1
set "SEVEN_ZIP=%APP%\tools\7zip\x64\7za.exe"

call :EnsureArtifact "uv-x86_64-pc-windows-msvc.zip" "build\uv" "uv.exe" "https://github.com/astral-sh/uv/releases/download/%PIN_UV_VERSION%/uv-x86_64-pc-windows-msvc.zip"
if errorlevel 1 exit /b 1

call :EnsureArtifact "cpython-%PY_RAG_VERSION%+%PY_STANDALONE_RELEASE%-x86_64-pc-windows-msvc-install_only.tar.gz" "runtime\python-rag" "python.exe" "https://github.com/astral-sh/python-build-standalone/releases/download/%PY_STANDALONE_RELEASE%/cpython-%PY_RAG_VERSION%+%PY_STANDALONE_RELEASE%-x86_64-pc-windows-msvc-install_only.tar.gz"
if errorlevel 1 exit /b 1

call :EnsureArtifact "cpython-%PY_OCR_VERSION%+%PY_STANDALONE_RELEASE%-x86_64-pc-windows-msvc-install_only.tar.gz" "runtime\python-ocr" "python.exe" "https://github.com/astral-sh/python-build-standalone/releases/download/%PY_STANDALONE_RELEASE%/cpython-%PY_OCR_VERSION%+%PY_STANDALONE_RELEASE%-x86_64-pc-windows-msvc-install_only.tar.gz"
if errorlevel 1 exit /b 1

call :EnsureArtifact "node-v%NODE_VERSION%-win-x64.zip" "build\node" "node.exe" "https://nodejs.org/dist/v%NODE_VERSION%/node-v%NODE_VERSION%-win-x64.zip"
if errorlevel 1 exit /b 1

call :EnsureArtifact "MinGit-%MINGIT_PACKAGE_VERSION%-64-bit.zip" "build\git" "cmd\git.exe" "https://github.com/git-for-windows/git/releases/download/v%MINGIT_TAG%/MinGit-%MINGIT_PACKAGE_VERSION%-64-bit.zip"
if errorlevel 1 exit /b 1

call :EnsureArtifact "ragflow-%RAGFLOW_VERSION%.zip" "ragflow" "pyproject.toml" "https://github.com/infiniflow/ragflow/archive/refs/tags/v%RAGFLOW_VERSION%.zip"
if errorlevel 1 exit /b 1

call :EnsureArtifact "datrie-%DATRIE_VERSION%-cp313-cp313-win_amd64.whl" "build\vendor-wheels\rag" "datrie-%DATRIE_VERSION%-cp313-cp313-win_amd64.whl" "https://github.com/gav998/local-llm/releases/download/dependencies-2026.09.03.1/datrie-%DATRIE_VERSION%-cp313-cp313-win_amd64.whl"
if errorlevel 1 exit /b 1

call :EnsureArtifact "paddlepaddle_gpu-%PADDLE_VERSION%-cp311-cp311-win_amd64.whl" "build\vendor-wheels\ocr" "paddlepaddle_gpu-%PADDLE_VERSION%-cp311-cp311-win_amd64.whl" "https://paddle-whl.cdn.bcebos.com/stable/cu118/paddlepaddle-gpu/paddlepaddle_gpu-%PADDLE_VERSION%-cp311-cp311-win_amd64.whl"
if errorlevel 1 exit /b 1

call :EnsureArtifact "PP-DocLayout-L_infer.tar" "cache\paddlex\official_models\PP-DocLayout-L" "inference.json" "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-DocLayout-L_infer.tar"
if errorlevel 1 exit /b 1
call :EnsureArtifact "PP-DocBlockLayout_infer.tar" "cache\paddlex\official_models\PP-DocBlockLayout" "inference.json" "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-DocBlockLayout_infer.tar"
if errorlevel 1 exit /b 1
call :EnsureArtifact "PP-OCRv6_medium_det_infer.tar" "cache\paddlex\official_models\PP-OCRv6_medium_det" "inference.json" "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-OCRv6_medium_det_infer.tar"
if errorlevel 1 exit /b 1
call :EnsureArtifact "eslav_PP-OCRv5_mobile_rec_infer.tar" "cache\paddlex\official_models\eslav_PP-OCRv5_mobile_rec" "inference.json" "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/eslav_PP-OCRv5_mobile_rec_infer.tar"
if errorlevel 1 exit /b 1
call :EnsureArtifact "SLANet_plus_infer.tar" "cache\paddlex\official_models\SLANet_plus" "inference.json" "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/SLANet_plus_infer.tar"
if errorlevel 1 exit /b 1
call :VerifyPaddleModelFiles
if errorlevel 1 exit /b 1

call :EnsureArtifact "dejavu-sans-ttf-%DEJAVU_SANS_VERSION%.zip" "services\ocr\font" "ttf\DejaVuSans.ttf" "https://github.com/dejavu-fonts/dejavu-fonts/releases/download/version_2_37/dejavu-sans-ttf-%DEJAVU_SANS_VERSION%.zip"
if errorlevel 1 exit /b 1

call :EnsureArtifact "VC_redist.x64.exe" "build\vendor-exe\vcredist" "VC_redist.x64.exe" "https://download.visualstudio.microsoft.com/download/pr/9d270333-8b7b-4f96-9458-6fcdb2ec0b25/CC0FF0EB1DC3F5188AE6300FAEF32BF5BEEBA4BDD6E8E445A9184072096B713B/VC_redist.x64.exe"
if errorlevel 1 exit /b 1

call :EnsureArtifact "mysql-%MYSQL_VERSION%-winx64.zip" "services\mysql" "bin\mysqld.exe" "https://cdn.mysql.com/archives/mysql-8.0/mysql-%MYSQL_VERSION%-winx64.zip"
if errorlevel 1 exit /b 1
call :EnsureArtifact "elasticsearch-%ELASTIC_VERSION%-windows-x86_64.zip" "services\elasticsearch" "bin\elasticsearch.bat" "https://artifacts.elastic.co/downloads/elasticsearch/elasticsearch-%ELASTIC_VERSION%-windows-x86_64.zip"
if errorlevel 1 exit /b 1
call :EnsureArtifact "silo_20260806000000.0.0_windows_amd64.tar.gz" "services\silo" "silo.exe" "https://github.com/pgsty/silo/releases/download/RELEASE.2026-08-06T00-00-00Z/silo_20260806000000.0.0_windows_amd64.tar.gz"
if errorlevel 1 exit /b 1
call :EnsureArtifact "Valkey-%VALKEY_VERSION%-Windows-x64-msys2.zip" "services\valkey" "valkey-server.exe" "https://github.com/valkey-windows/valkey-windows/releases/download/%VALKEY_VERSION%/Valkey-%VALKEY_VERSION%-Windows-x64-msys2.zip"
if errorlevel 1 exit /b 1
call :EnsureArtifact "caddy_%CADDY_VERSION%_windows_amd64.zip" "services\caddy" "caddy.exe" "https://github.com/caddyserver/caddy/releases/download/v%CADDY_VERSION%/caddy_%CADDY_VERSION%_windows_amd64.zip"
if errorlevel 1 exit /b 1
call :EnsureArtifact "llama-%LLAMA_BUILD%-bin-win-vulkan-x64.zip" "runtime\llama" "llama-server.exe" "https://github.com/ggml-org/llama.cpp/releases/download/%LLAMA_BUILD%/llama-%LLAMA_BUILD%-bin-win-vulkan-x64.zip"
if errorlevel 1 exit /b 1

exit /b 0

:VerifyPaddleModelFiles
for %%M in (
    "PP-DocLayout-L"
    "PP-DocBlockLayout"
    "PP-OCRv6_medium_det"
    "eslav_PP-OCRv5_mobile_rec"
    "SLANet_plus"
) do for %%F in (inference.json inference.yml inference.pdiparams) do (
    if not exist "%APP%\cache\paddlex\official_models\%%~M\%%F" (
        echo [ERROR] Paddle model %%~M is incomplete: %%F is missing.
        exit /b 1
    )
)
exit /b 0

REM EnsureArtifact has exactly the four public arguments requested:
REM   1 file name in _src
REM   2 destination relative to app
REM   3 key file relative to destination
REM   4 fixed vendor URL
:EnsureArtifact
setlocal EnableDelayedExpansion
set "ART_NAME=%~1"
set "DEST_REL=%~2"
set "KEY_REL=%~3"
set "ART_URL=%~4"

if not "%~5"=="" (
    echo [ERROR] EnsureArtifact accepts exactly four arguments.
    endlocal & exit /b 1
)

if not defined ART_NAME (
    endlocal
    exit /b 1
)
if not defined DEST_REL (
    endlocal
    exit /b 1
)
if not defined KEY_REL (
    endlocal
    exit /b 1
)
if not defined ART_URL (
    endlocal
    exit /b 1
)

if not "!ART_NAME:\=!"=="!ART_NAME!" (
    echo [ERROR] Artifact name must be a plain file name: !ART_NAME!
    endlocal & exit /b 1
)
if not "!ART_NAME:/=!"=="!ART_NAME!" (
    echo [ERROR] Artifact name must be a plain file name: !ART_NAME!
    endlocal & exit /b 1
)
if not "!ART_NAME::=!"=="!ART_NAME!" (
    echo [ERROR] Artifact name must be a plain file name: !ART_NAME!
    endlocal & exit /b 1
)
if not "!DEST_REL:..=!"=="!DEST_REL!" (
    echo [ERROR] Unsafe destination: !DEST_REL!
    endlocal & exit /b 1
)
if not "!DEST_REL::=!"=="!DEST_REL!" (
    echo [ERROR] Unsafe destination: !DEST_REL!
    endlocal & exit /b 1
)
if not "!KEY_REL:..=!"=="!KEY_REL!" (
    echo [ERROR] Unsafe key path: !KEY_REL!
    endlocal & exit /b 1
)
if not "!KEY_REL::=!"=="!KEY_REL!" (
    echo [ERROR] Unsafe key path: !KEY_REL!
    endlocal & exit /b 1
)
if "!DEST_REL:~0,1!"=="\" (
    echo [ERROR] Destination must be relative to app: !DEST_REL!
    endlocal & exit /b 1
)
if "!KEY_REL:~0,1!"=="\" (
    echo [ERROR] Key file must be relative to the destination: !KEY_REL!
    endlocal & exit /b 1
)

set "ART_SOURCE=%SRC%\!ART_NAME!"
set "ART_DEST=%APP%\!DEST_REL!"
set "ART_KEY=%APP%\!DEST_REL!\!KEY_REL!"
set "ART_STALE_BACKUP=%WORK%\artifact-stale-!ART_NAME!"

:ENSURE_SOURCE_WAIT
if not exist "!ART_SOURCE!" (
    echo.
    echo Required file is missing: !ART_NAME!
    echo Download it from:
    echo   !ART_URL!
    echo Place it here:
    echo   !ART_SOURCE!
    echo Press any key after the file has been copied...
    pause >nul
    goto :ENSURE_SOURCE_WAIT
)

echo [OK] Source file is present: !ART_NAME!

set "ART_KIND=file"
if /i "!ART_NAME:~-4!"==".zip" set "ART_KIND=archive"
if /i "!ART_NAME:~-3!"==".7z" set "ART_KIND=archive"
if /i "!ART_NAME:~-4!"==".tar" set "ART_KIND=archive"
if /i "!ART_NAME:~-7!"==".tar.gz" set "ART_KIND=archive"
if /i "!ART_NAME:~-4!"==".tgz" set "ART_KIND=archive"
set "ART_MARKER=!ART_DEST!\.local-llm-artifact.txt"
set "MARKER_ARTIFACT="
call :MakeDirChecked "!ART_DEST!"
if errorlevel 1 goto :ENSURE_MANUAL_WAIT

if exist "!ART_MARKER!" (
    for /f "usebackq tokens=1,* delims==" %%A in ("!ART_MARKER!") do (
        if /i "%%A"=="artifact" set "MARKER_ARTIFACT=%%B"
    )
)
call :IsRegularFile "!ART_KEY!"
if not errorlevel 1 if /i "!MARKER_ARTIFACT!"=="!ART_NAME!" (
    call :DiscardStaleArtifactBackup
    if errorlevel 1 (endlocal & exit /b 1)
    echo [OK] !ART_NAME! -- source and required destination file are present
    endlocal & exit /b 0
)
call :IsRegularFile "!ART_KEY!"
if not errorlevel 1 (
    call :WriteArtifactMarker "!ART_MARKER!" "!ART_NAME!"
    if errorlevel 1 goto :ENSURE_MANUAL_WAIT
    call :DiscardStaleArtifactBackup
    if errorlevel 1 (endlocal & exit /b 1)
    echo [OK] Accepted manually prepared destination for !ART_NAME!: !ART_KEY!
    endlocal & exit /b 0
)
if exist "!ART_KEY!" echo [INFO] Replacing stale or untracked destination for !ART_NAME!.

REM Never let a failed upgrade re-label an older destination as the new
REM artifact. Preserve the old generated tree before attempting replacement;
REM manual acceptance starts from an empty destination.
if exist "!ART_STALE_BACKUP!" (
    call :MoveInterruptedArtifactBackupAside
    if errorlevel 1 (endlocal & exit /b 1)
)
call :DirectoryIsEmpty "!ART_DEST!"
if not errorlevel 1 (
    call :RemoveTreeChecked "!ART_DEST!"
    if errorlevel 1 (endlocal & exit /b 1)
) else (
    move "!ART_DEST!" "!ART_STALE_BACKUP!" >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Could not preserve the previous destination: !ART_DEST!
        endlocal & exit /b 1
    )
)
call :MakeDirChecked "!ART_DEST!"
if errorlevel 1 (
    move "!ART_STALE_BACKUP!" "!ART_DEST!" >nul 2>&1
    endlocal & exit /b 1
)

if /i "!ART_KIND!"=="file" (
    for %%D in ("!ART_KEY!") do call :MakeDirChecked "%%~dpD"
    if errorlevel 1 goto :ENSURE_FILE_FAILED
    set "ART_COPY_TEMP=!ART_KEY!.partial-!RANDOM!-!RANDOM!"
    copy /y "!ART_SOURCE!" "!ART_COPY_TEMP!" >nul 2>&1
    if errorlevel 1 goto :ENSURE_FILE_FAILED
    fc /b "!ART_SOURCE!" "!ART_COPY_TEMP!" >nul 2>&1
    if errorlevel 1 goto :ENSURE_FILE_FAILED
    move /y "!ART_COPY_TEMP!" "!ART_KEY!" >nul 2>&1
    if errorlevel 1 goto :ENSURE_FILE_FAILED
    fc /b "!ART_SOURCE!" "!ART_KEY!" >nul 2>&1
    if errorlevel 1 goto :ENSURE_FILE_FAILED
    call :WriteArtifactMarker "!ART_MARKER!" "!ART_NAME!"
    if errorlevel 1 goto :ENSURE_MANUAL_WAIT
    call :DiscardStaleArtifactBackup
    if errorlevel 1 (endlocal & exit /b 1)
    echo [OK] Copied and verified: !ART_NAME!
    endlocal & exit /b 0
)

if not exist "%SEVEN_ZIP%" (
    echo [ERROR] A working 7-Zip command-line extractor must be prepared first.
    call :ResetArtifactManualDestination
    if errorlevel 1 (endlocal & exit /b 1)
    goto :ENSURE_MANUAL_WAIT
)

set "ART_STAGE_BASE=%WORK%\extract-!RANDOM!-!RANDOM!"
set "ART_STAGE=!ART_STAGE_BASE!"
set "ART_UNPACK_LOG=%LOGS%\artifact-!ART_NAME!.log"
if exist "!ART_STAGE!" (
    echo [ERROR] Temporary extraction directory collision: !ART_STAGE!
    endlocal & exit /b 1
)
call :MakeDirChecked "!ART_STAGE!"
if errorlevel 1 goto :ENSURE_AUTO_FAILED

"%SEVEN_ZIP%" t "!ART_SOURCE!" >"!ART_UNPACK_LOG!" 2>&1
if errorlevel 1 goto :ENSURE_AUTO_FAILED

if /i "!ART_NAME:~-7!"==".tar.gz" goto :ENSURE_TARGZ
if /i "!ART_NAME:~-4!"==".tgz" goto :ENSURE_TARGZ

"%SEVEN_ZIP%" x -y "-o!ART_STAGE!" "!ART_SOURCE!" >>"!ART_UNPACK_LOG!" 2>&1
if errorlevel 1 goto :ENSURE_AUTO_FAILED
goto :ENSURE_STAGE_READY

:ENSURE_TARGZ
set "ART_GZIP_STAGE=!ART_STAGE_BASE!\_gzip"
call :MakeDirChecked "!ART_GZIP_STAGE!"
if errorlevel 1 goto :ENSURE_AUTO_FAILED
"%SEVEN_ZIP%" x -y "-o!ART_GZIP_STAGE!" "!ART_SOURCE!" >>"!ART_UNPACK_LOG!" 2>&1
if errorlevel 1 goto :ENSURE_AUTO_FAILED
set "ART_TAR="
for /f "delims=" %%T in ('dir /b /a-d "!ART_GZIP_STAGE!\*.tar" 2^>nul') do if not defined ART_TAR set "ART_TAR=!ART_GZIP_STAGE!\%%T"
if not defined ART_TAR goto :ENSURE_AUTO_FAILED
"%SEVEN_ZIP%" x -y "-o!ART_STAGE_BASE!\_content" "!ART_TAR!" >>"!ART_UNPACK_LOG!" 2>&1
if errorlevel 1 goto :ENSURE_AUTO_FAILED
rmdir /s /q "!ART_GZIP_STAGE!" >nul 2>&1
set "ART_STAGE=!ART_STAGE_BASE!\_content"

:ENSURE_STAGE_READY
call :CopyExtractedTree "!ART_STAGE!" "!ART_DEST!" "!KEY_REL!"
if errorlevel 1 goto :ENSURE_AUTO_FAILED
call :IsRegularFile "!ART_KEY!"
if not errorlevel 1 (
    call :WriteArtifactMarker "!ART_MARKER!" "!ART_NAME!"
    if errorlevel 1 goto :ENSURE_MANUAL_WAIT
    call :DiscardStaleArtifactBackup
    if errorlevel 1 (endlocal & exit /b 1)
    echo [OK] Expanded: !ART_NAME!
    if exist "!ART_STAGE_BASE!" rmdir /s /q "!ART_STAGE_BASE!" >nul 2>&1
    endlocal & exit /b 0
)

:ENSURE_AUTO_FAILED
echo [WARN] Automatic processing did not produce the required key file.
echo [WARN] Details: !ART_UNPACK_LOG!
if exist "!ART_STAGE_BASE!" rmdir /s /q "!ART_STAGE_BASE!" >nul 2>&1
call :ResetArtifactManualDestination
if errorlevel 1 (endlocal & exit /b 1)
goto :ENSURE_MANUAL_WAIT

:ENSURE_FILE_FAILED
if exist "!ART_COPY_TEMP!" del /f /q "!ART_COPY_TEMP!" >nul 2>&1
call :ResetArtifactManualDestination
if errorlevel 1 (endlocal & exit /b 1)

:ENSURE_MANUAL_WAIT
echo.
echo Automatic extraction or copy failed.
echo Extract or copy:
echo   SOURCE:      !ART_SOURCE!
echo   DESTINATION: !ART_DEST!
echo The following file must exist when finished:
echo   KEY FILE:    !ART_KEY!
if defined ART_STALE_BACKUP if exist "!ART_STALE_BACKUP!" echo Previous generated destination preserved at: !ART_STALE_BACKUP!
echo Press any key after completing the operation...
pause >nul
call :MakeDirChecked "!ART_DEST!"
if errorlevel 1 (
    echo [WARN] Destination is not a regular project directory.
    goto :ENSURE_MANUAL_WAIT
)
call :IsRegularFile "!ART_KEY!"
if not errorlevel 1 (
    call :WriteArtifactMarker "!ART_MARKER!" "!ART_NAME!"
    if errorlevel 1 (
        echo [WARN] Could not write the artifact marker.
        goto :ENSURE_MANUAL_WAIT
    )
    call :DiscardStaleArtifactBackup
    if errorlevel 1 (endlocal & exit /b 1)
    echo [OK] Required key file found: !ART_KEY!
    endlocal & exit /b 0
)
echo [WARN] The required key file is still missing.
goto :ENSURE_MANUAL_WAIT

:ResetArtifactManualDestination
call :RemoveTreeChecked "%ART_DEST%"
if errorlevel 1 exit /b 1
call :MakeDirChecked "%ART_DEST%"
exit /b %ERRORLEVEL%

:DiscardStaleArtifactBackup
if not defined ART_STALE_BACKUP exit /b 0
call :RemoveTreeChecked "%ART_STALE_BACKUP%"
if errorlevel 1 exit /b 1
set "ART_STALE_BACKUP="
exit /b 0

:MoveInterruptedArtifactBackupAside
if not defined ART_STALE_BACKUP exit /b 0
if not exist "%ART_STALE_BACKUP%" exit /b 0
set "ART_ORPHAN_BACKUP=%WORK%\artifact-orphaned-%ART_NAME%-%RANDOM%-%RANDOM%"
if exist "%ART_ORPHAN_BACKUP%" (
    echo [ERROR] Could not allocate a unique orphaned artifact backup path:
    echo [ERROR]   %ART_ORPHAN_BACKUP%
    exit /b 1
)
move "%ART_STALE_BACKUP%" "%ART_ORPHAN_BACKUP%" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Could not move the preserved destination aside:
    echo [ERROR]   %ART_STALE_BACKUP%
    exit /b 1
)
echo [WARN] Previous interrupted artifact backup moved aside:
echo [WARN]   %ART_ORPHAN_BACKUP%
exit /b 0

:WriteArtifactMarker
setlocal
set "ARTIFACT_MARKER_TEMP=%~1.tmp-%RANDOM%-%RANDOM%"
if exist "%ARTIFACT_MARKER_TEMP%" (
    endlocal & exit /b 1
)
>"%ARTIFACT_MARKER_TEMP%" (
    echo artifact=%~2
    if not "%~3"=="" echo tree_sha256=%~3
    if not "%~4"=="" echo tree_file_count=%~4
)
if errorlevel 1 (
    if exist "%ARTIFACT_MARKER_TEMP%" del /f /q "%ARTIFACT_MARKER_TEMP%" >nul 2>&1
    endlocal & exit /b 1
)
if not exist "%ARTIFACT_MARKER_TEMP%" (
    endlocal & exit /b 1
)
move /y "%ARTIFACT_MARKER_TEMP%" "%~1" >nul 2>&1
if errorlevel 1 (
    if exist "%ARTIFACT_MARKER_TEMP%" del /f /q "%ARTIFACT_MARKER_TEMP%" >nul 2>&1
    endlocal & exit /b 1
)
if not exist "%~1" (
    endlocal & exit /b 1
)
endlocal & exit /b 0

:ComputeTreeFingerprint
setlocal DisableDelayedExpansion
set "TREE_ROOT=%~1"
set "TREE_EXCLUDE_REL=%~2"
set "TREE_MANIFEST_OUTPUT=%~5"
set "TREE_OUTPUT=%WORK%\tree-fingerprint-%RANDOM%-%RANDOM%.txt"
set "TREE_ERROR_OUTPUT=%TREE_OUTPUT%.err"
set "TREE_SHA256_VALUE="
set "TREE_FILE_COUNT_VALUE="
if not exist "%~1\." (
    endlocal & exit /b 1
)
if defined TREE_MANIFEST_OUTPUT (
    "%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -File "%PROJECT%\tree_fingerprint.ps1" -Root "%TREE_ROOT%" -ExcludeRel "%TREE_EXCLUDE_REL%" -Output "%TREE_OUTPUT%" -ManifestOutput "%TREE_MANIFEST_OUTPUT%" >"%TREE_ERROR_OUTPUT%" 2>&1
) else (
    "%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -File "%PROJECT%\tree_fingerprint.ps1" -Root "%TREE_ROOT%" -ExcludeRel "%TREE_EXCLUDE_REL%" -Output "%TREE_OUTPUT%" >"%TREE_ERROR_OUTPUT%" 2>&1
)
if errorlevel 1 (
    if exist "%TREE_ERROR_OUTPUT%" type "%TREE_ERROR_OUTPUT%"
    if exist "%TREE_ERROR_OUTPUT%" del /f /q "%TREE_ERROR_OUTPUT%" >nul 2>&1
    if exist "%TREE_OUTPUT%" del /f /q "%TREE_OUTPUT%" >nul 2>&1
    endlocal & exit /b 1
)
if exist "%TREE_ERROR_OUTPUT%" del /f /q "%TREE_ERROR_OUTPUT%" >nul 2>&1
for /f "usebackq tokens=1,* delims==" %%A in ("%TREE_OUTPUT%") do (
    if /i "%%A"=="TREE_SHA256" set "TREE_SHA256_VALUE=%%B"
    if /i "%%A"=="TREE_FILE_COUNT" set "TREE_FILE_COUNT_VALUE=%%B"
)
if exist "%TREE_OUTPUT%" del /f /q "%TREE_OUTPUT%" >nul 2>&1
if not defined TREE_SHA256_VALUE (
    endlocal & exit /b 1
)
if not defined TREE_FILE_COUNT_VALUE (
    endlocal & exit /b 1
)
endlocal & set "%~3=%TREE_SHA256_VALUE%" & set "%~4=%TREE_FILE_COUNT_VALUE%" & exit /b 0

:TreeMatchesMarker
setlocal EnableDelayedExpansion
set "TREE_EXPECTED_SHA256="
set "TREE_EXPECTED_FILE_COUNT="
set "TREE_EXPECTED_MANIFEST_REL="
if not exist "%~2" (
    endlocal & exit /b 1
)
for /f "usebackq tokens=1,* delims==" %%A in ("%~2") do (
    if /i "%%A"=="tree_sha256" set "TREE_EXPECTED_SHA256=%%B"
    if /i "%%A"=="tree_file_count" set "TREE_EXPECTED_FILE_COUNT=%%B"
    if /i "%%A"=="tree_manifest" set "TREE_EXPECTED_MANIFEST_REL=%%B"
)
if not defined TREE_EXPECTED_SHA256 (
    endlocal & exit /b 1
)
if not defined TREE_EXPECTED_FILE_COUNT (
    endlocal & exit /b 1
)
call :ComputeTreeFingerprint "%~1" "%~3" TREE_CURRENT_SHA256 TREE_CURRENT_FILE_COUNT
if errorlevel 1 (
    endlocal & exit /b 1
)
if /i not "%TREE_CURRENT_SHA256%"=="%TREE_EXPECTED_SHA256%" (
    goto :TREE_MATCH_FAILED
)
if not "%TREE_CURRENT_FILE_COUNT%"=="%TREE_EXPECTED_FILE_COUNT%" (
    goto :TREE_MATCH_FAILED
)
endlocal & exit /b 0

:TREE_MATCH_FAILED
echo [ERROR] Tree seal mismatch: %~1
echo [ERROR] Expected: files=%TREE_EXPECTED_FILE_COUNT% sha256=%TREE_EXPECTED_SHA256%
echo [ERROR] Actual  : files=%TREE_CURRENT_FILE_COUNT% sha256=%TREE_CURRENT_SHA256%
if not defined TREE_EXPECTED_MANIFEST_REL (
    echo [INFO] This older seal has no per-file manifest, so an exact diff is unavailable.
    endlocal & exit /b 1
)
if not "!TREE_EXPECTED_MANIFEST_REL:..=!"=="!TREE_EXPECTED_MANIFEST_REL!" (
    echo [ERROR] Unsafe tree diagnostic manifest path: !TREE_EXPECTED_MANIFEST_REL!
    endlocal & exit /b 1
)
if not "!TREE_EXPECTED_MANIFEST_REL::=!"=="!TREE_EXPECTED_MANIFEST_REL!" (
    echo [ERROR] Unsafe tree diagnostic manifest path: !TREE_EXPECTED_MANIFEST_REL!
    endlocal & exit /b 1
)
if "!TREE_EXPECTED_MANIFEST_REL:~0,1!"=="\" (
    echo [ERROR] Unsafe tree diagnostic manifest path: !TREE_EXPECTED_MANIFEST_REL!
    endlocal & exit /b 1
)
if "!TREE_EXPECTED_MANIFEST_REL:~0,1!"=="/" (
    echo [ERROR] Unsafe tree diagnostic manifest path: !TREE_EXPECTED_MANIFEST_REL!
    endlocal & exit /b 1
)
for %%M in ("%~2") do set "TREE_EXPECTED_MANIFEST=%%~dpM!TREE_EXPECTED_MANIFEST_REL!"
if not exist "!TREE_EXPECTED_MANIFEST!" (
    echo [ERROR] Tree diagnostic manifest is missing: !TREE_EXPECTED_MANIFEST!
    endlocal & exit /b 1
)
set "TREE_DIAGNOSTIC_OUTPUT=%LOGS%\tree-fingerprint-mismatch.log"
set "TREE_DIAGNOSTIC_RESULT=%WORK%\tree-diagnostic-%RANDOM%-%RANDOM%.txt"
set "TREE_DIAGNOSTIC_ERROR=!TREE_DIAGNOSTIC_RESULT!.err"
"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -File "%PROJECT%\tree_fingerprint.ps1" -Root "%~1" -ExcludeRel "%~3" -Output "!TREE_DIAGNOSTIC_RESULT!" -ExpectedManifest "!TREE_EXPECTED_MANIFEST!" -ExpectedTreeSha256 "!TREE_EXPECTED_SHA256!" -ExpectedTreeFileCount "!TREE_EXPECTED_FILE_COUNT!" -DiagnosticOutput "!TREE_DIAGNOSTIC_OUTPUT!" >"!TREE_DIAGNOSTIC_ERROR!" 2>&1
if errorlevel 1 (
    echo [ERROR] Could not create the per-file tree diff.
    if exist "!TREE_DIAGNOSTIC_ERROR!" type "!TREE_DIAGNOSTIC_ERROR!"
) else (
    echo [INFO] Per-file tree diff: !TREE_DIAGNOSTIC_OUTPUT!
    "%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "Get-Content -LiteralPath $env:TREE_DIAGNOSTIC_OUTPUT -TotalCount 50" 2^>nul
)
if exist "!TREE_DIAGNOSTIC_RESULT!" del /f /q "!TREE_DIAGNOSTIC_RESULT!" >nul 2>&1
if exist "!TREE_DIAGNOSTIC_ERROR!" del /f /q "!TREE_DIAGNOSTIC_ERROR!" >nul 2>&1
endlocal & exit /b 1

:CopyExtractedTree
setlocal EnableDelayedExpansion
set "TREE_SOURCE=%~1"
set "TREE_DEST=%~2"
set "TREE_KEY_REL=%~3"
set /a TREE_DIRS=0
set /a TREE_FILES=0
set "TREE_FIRST_DIR="

for /f "delims=" %%D in ('dir /b /ad "!TREE_SOURCE!" 2^>nul') do (
    set /a TREE_DIRS+=1
    if not defined TREE_FIRST_DIR set "TREE_FIRST_DIR=%%D"
)
for /f "delims=" %%F in ('dir /b /a-d "!TREE_SOURCE!" 2^>nul') do set /a TREE_FILES+=1

set "TREE_COPY_ROOT=!TREE_SOURCE!"
if "!TREE_DIRS!"=="1" if "!TREE_FILES!"=="0" set "TREE_COPY_ROOT=!TREE_SOURCE!\!TREE_FIRST_DIR!"

REM Validate the archive layout before copying anything into the final tree.
if not exist "!TREE_COPY_ROOT!\!TREE_KEY_REL!" (
    endlocal & exit /b 2
)

set "TREE_BACKUP=%WORK%\artifact-previous-!RANDOM!-!RANDOM!"
if exist "!TREE_BACKUP!" (
    endlocal & exit /b 1
)
if exist "!TREE_DEST!" (
    move "!TREE_DEST!" "!TREE_BACKUP!" >nul 2>&1
    if errorlevel 1 (
        endlocal
        exit /b 1
    )
)
move "!TREE_COPY_ROOT!" "!TREE_DEST!" >nul 2>&1
if errorlevel 1 (
    if exist "!TREE_BACKUP!" (
        move "!TREE_BACKUP!" "!TREE_DEST!" >nul 2>&1
        if errorlevel 1 (
            echo [ERROR] Automatic rollback failed. Preserved tree: !TREE_BACKUP!
        )
    )
    endlocal & exit /b 1
)
if exist "!TREE_BACKUP!" rmdir /s /q "!TREE_BACKUP!" >nul 2>&1
if exist "!TREE_BACKUP!" (
    echo [ERROR] Could not remove preserved artifact tree: !TREE_BACKUP!
    endlocal & exit /b 1
)
endlocal & exit /b 0

REM ============================================================================
REM VERSION AND NATIVE-RUNTIME CHECKS
REM ============================================================================

:VerifyArtifactVersions
echo [STEP] Verify artifact executables and archive layouts
if not exist "%RAG_PY%" exit /b 1
if not exist "%OCR_PY%" exit /b 1
if not exist "%UV_EXE%" exit /b 1
if not exist "%NODE_DIR%\node.exe" exit /b 1
if not exist "%PORTABLE_GIT_DIR%\cmd\git.exe" exit /b 1

"%RAG_PY%" -c "import sys; print(sys.version)" >"%LOGS%\python-rag-version.log" 2>&1
if errorlevel 1 (
    echo [ERROR] RAGFlow Python executable failed. See %LOGS%\python-rag-version.log
    exit /b 1
)
"%OCR_PY%" -c "import sys; print(sys.version)" >"%LOGS%\python-ocr-version.log" 2>&1
if errorlevel 1 (
    echo [ERROR] OCR Python executable failed. See %LOGS%\python-ocr-version.log
    exit /b 1
)
"%UV_EXE%" --version >"%LOGS%\uv-version.log" 2>&1
if errorlevel 1 exit /b 1
"%NODE_DIR%\node.exe" --version >"%LOGS%\node-version.log" 2>&1
if errorlevel 1 exit /b 1
"%PORTABLE_GIT_DIR%\cmd\git.exe" --version >"%LOGS%\git-version.log" 2>&1
if errorlevel 1 exit /b 1

set "RAGFLOW_CHECK_FILE=%RAGFLOW_DIR%\pyproject.toml"
"%RAG_PY%" -c "import os,pathlib,tomllib; p=tomllib.loads(pathlib.Path(os.environ['RAGFLOW_CHECK_FILE']).read_text(encoding='utf-8')); print(p['project'].get('version',''))" >"%LOGS%\ragflow-version.log" 2>&1
if errorlevel 1 (
    echo [ERROR] The expanded RAGFlow source layout could not be inspected.
    echo [HINT] Delete %RAGFLOW_DIR% and rerun this script.
    exit /b 1
)
exit /b 0

:PrepareSharedRuntimeDlls
set "VC_REDIST_EXE=%APP%\build\vendor-exe\vcredist\VC_redist.x64.exe"
call :FileSha256 "%VC_REDIST_EXE%" VC_REDIST_HASH
if errorlevel 1 exit /b 1
set "VC_MARKER=%APP%\config\shared-runtime-dlls.ok"
set "VC_FINGERPRINT=project=%RESUME_GRAPH_VERSION%;vc=%VC_REDIST_VERSION%;source=%VC_REDIST_HASH%"
call :MarkerMatches "%VC_MARKER%" "%VC_FINGERPRINT%"
if errorlevel 1 goto :VC_EXTRACT
call :VerifySharedRuntimeDlls
if errorlevel 1 (
    echo [WARN] The fingerprinted Microsoft VC runtime is incomplete; extracting it again.
    if exist "%VC_MARKER%" del /f /q "%VC_MARKER%" >nul 2>&1
    goto :VC_EXTRACT
)
set "PATH=%SHARED_DLL_DIR%;%PATH%"
echo [SKIP] App-local Microsoft VC runtime already extracted.
exit /b 0

:VC_EXTRACT
echo [STEP] Extract the pinned Microsoft VC runtime for app-local deployment
set "VC_STAGE=%WORK%\vcredist-%RANDOM%-%RANDOM%"
set "VC_PARTS=%VC_STAGE%\parts"
set "VC_PAYLOAD=%VC_STAGE%\payload"
set "VC_DLLS=%VC_STAGE%\dlls"
set "VC_LOG=%LOGS%\vcredist-extract.log"
call :BeginStepLog "%VC_LOG%" "local_llm VC runtime extract log"
if errorlevel 1 exit /b 1
if exist "%VC_STAGE%" (
    echo [ERROR] Temporary VC runtime directory collision: %VC_STAGE%
    exit /b 1
)
call :MakeDirChecked "%VC_PARTS%"
if errorlevel 1 goto :VC_EXTRACT_FAILED
call :MakeDirChecked "%VC_PAYLOAD%"
if errorlevel 1 goto :VC_EXTRACT_FAILED
call :MakeDirChecked "%VC_DLLS%"
if errorlevel 1 goto :VC_EXTRACT_FAILED

"%SEVEN_ZIP%" x -y -t# "-o%VC_PARTS%" "%VC_REDIST_EXE%" >>"%VC_LOG%" 2>&1
if errorlevel 1 goto :VC_EXTRACT_FAILED
if not exist "%VC_PARTS%\4.cab" goto :VC_EXTRACT_FAILED
"%SEVEN_ZIP%" x -y "-o%VC_PAYLOAD%" "%VC_PARTS%\4.cab" >>"%VC_LOG%" 2>&1
if errorlevel 1 goto :VC_EXTRACT_FAILED
if not exist "%VC_PAYLOAD%\a12" goto :VC_EXTRACT_FAILED
"%SEVEN_ZIP%" x -y "-o%VC_DLLS%" "%VC_PAYLOAD%\a12" >>"%VC_LOG%" 2>&1
if errorlevel 1 goto :VC_EXTRACT_FAILED

call :RemoveTreeChecked "%SHARED_DLL_DIR%"
if errorlevel 1 goto :VC_EXTRACT_FAILED
call :MakeDirChecked "%SHARED_DLL_DIR%"
if errorlevel 1 goto :VC_EXTRACT_FAILED
for %%F in ("%VC_DLLS%\*_amd64") do (
    call :CopyVcRuntimeDll "%%~fF"
    if errorlevel 1 goto :VC_EXTRACT_FAILED
)
for %%N in (vcruntime140.dll vcruntime140_1.dll msvcp140.dll msvcp140_1.dll msvcp140_2.dll concrt140.dll vcomp140.dll) do (
    if not exist "%SHARED_DLL_DIR%\%%N" (
        echo [ERROR] Required app-local runtime DLL is missing: %%N
        goto :VC_EXTRACT_FAILED
    )
    copy /y "%SHARED_DLL_DIR%\%%N" "%RAG_PY_DIR%\%%N" >nul 2>&1
    if errorlevel 1 goto :VC_EXTRACT_FAILED
    copy /y "%SHARED_DLL_DIR%\%%N" "%OCR_PY_DIR%\%%N" >nul 2>&1
    if errorlevel 1 goto :VC_EXTRACT_FAILED
)
if exist "%VC_STAGE%" rmdir /s /q "%VC_STAGE%" >nul 2>&1
set "PATH=%SHARED_DLL_DIR%;%PATH%"
call :WriteFingerprintMarker "%VC_MARKER%" "%VC_FINGERPRINT%"
if errorlevel 1 exit /b 1
exit /b 0

:CopyVcRuntimeDll
setlocal
set "VC_DLL_NAME=%~nx1"
set "VC_DLL_NAME=%VC_DLL_NAME:_amd64=%"
copy /y "%~1" "%SHARED_DLL_DIR%\%VC_DLL_NAME%" >nul 2>&1
if errorlevel 1 (
    endlocal & exit /b 1
)
endlocal & exit /b 0

:VerifySharedRuntimeDlls
for %%N in (vcruntime140.dll vcruntime140_1.dll msvcp140.dll msvcp140_1.dll msvcp140_2.dll concrt140.dll vcomp140.dll) do (
    if not exist "%SHARED_DLL_DIR%\%%N" exit /b 1
    if not exist "%RAG_PY_DIR%\%%N" exit /b 1
    if not exist "%OCR_PY_DIR%\%%N" exit /b 1
)
exit /b 0

:VC_EXTRACT_FAILED
echo [ERROR] Could not extract the app-local Microsoft VC runtime.
echo [INFO] No installer was executed and no registry/system directory was changed.
echo [INFO] See %VC_LOG%
call :PrintLogTail "%VC_LOG%"
if exist "%VC_STAGE%" rmdir /s /q "%VC_STAGE%" >nul 2>&1
exit /b 1

REM ============================================================================
REM RAGFLOW PYTHON, ASSETS AND WEB BUILD
REM ============================================================================

:PrepareRagflowPython
call :FileSha256 "%PROJECT%\ragflow-windows-overrides.txt" RAG_OVERRIDE_HASH
if errorlevel 1 exit /b 1
call :FileSha256 "%PROJECT%\ragflow-windows-additions.txt" RAG_ADDITION_HASH
if errorlevel 1 exit /b 1
call :FileSha256 "%PROJECT%\ragflow-windows-excludes.txt" RAG_EXCLUDE_HASH
if errorlevel 1 exit /b 1
call :FileSha256 "%PROJECT%\prepare_wheelhouse_lock.py" RAG_WHEEL_LOCKER_HASH
if errorlevel 1 exit /b 1
set "RAG_MARKER=%APP%\config\ragflow-python.ok"
set "RAG_FINGERPRINT=project=%RESUME_GRAPH_VERSION%;ragflow=%RAGFLOW_VERSION%;python=%PY_RAG_VERSION%;numpy=%NUMPY_VERSION%;xgboost=%XGBOOST_VERSION%;scikit-learn=%SCIKIT_LEARN_VERSION%;datrie=%DATRIE_VERSION%;graspologic-native=%GRASPOLOGIC_NATIVE_VERSION%;addition=%RAG_ADDITION_HASH%;override=%RAG_OVERRIDE_HASH%;exclude=%RAG_EXCLUDE_HASH%;wheel-locker=%RAG_WHEEL_LOCKER_HASH%"
call :MarkerMatches "%RAG_MARKER%" "%RAG_FINGERPRINT%"
if errorlevel 1 goto :RAG_BUILD_RUNTIME
call :FinalizeRagflowRuntime
if errorlevel 1 goto :RAG_REBUILD_RUNTIME
echo [SKIP] RAGFlow Python runtime already prepared, patched and verified.
exit /b 0

:RAG_REBUILD_RUNTIME
echo [WARN] The fingerprinted RAGFlow runtime failed validation; rebuilding it.
if exist "%RAG_MARKER%" del /f /q "%RAG_MARKER%" >nul 2>&1

:RAG_BUILD_RUNTIME

echo [STEP] Resolve the pinned RAGFlow graph for native Windows CPython 3.13
set "RAG_PREPARE_LOG=%LOGS%\ragflow-python-prepare.log"
call :BeginStepLog "%RAG_PREPARE_LOG%" "local_llm RAGFlow Python preparation log"
if errorlevel 1 exit /b 1
set "RAG_UPSTREAM_REQUIREMENTS=%APP%\config\locks\ragflow-%RAGFLOW_VERSION%-upstream.txt"
set "RAG_REQUIREMENTS=%APP%\config\locks\ragflow-%RAGFLOW_VERSION%-windows.txt"
pushd "%RAGFLOW_DIR%" >nul 2>&1
if errorlevel 1 exit /b 1

REM Prune every package intentionally overridden/excluded below. An exact pin
REM left in a constraint file wins over uv's override and makes the graph
REM unsatisfiable (notably NumPy 1.26.4 and XGBoost 1.6.0).
call :AppendLogBanner "%RAG_PREPARE_LOG%" "uv export RAGFlow lock"
"%UV_EXE%" export --frozen --no-group test --no-emit-project --no-emit-package numpy --no-emit-package xgboost --no-emit-package datrie --no-emit-package graspologic --no-emit-package infinity-emb --no-emit-package unclecode-litellm --no-emit-package agentrun-mem0ai --no-header --no-annotate --format requirements-txt --output-file "%RAG_UPSTREAM_REQUIREMENTS%" >>"%RAG_PREPARE_LOG%" 2>&1
if errorlevel 1 (
    popd >nul
    echo [ERROR] uv could not export the RAGFlow lock. See %RAG_PREPARE_LOG%
    call :PrintLogTail "%RAG_PREPARE_LOG%"
    exit /b 1
)

call :AppendLogBanner "%RAG_PREPARE_LOG%" "uv pip compile RAGFlow Windows graph"
"%UV_EXE%" pip compile "%RAGFLOW_DIR%\pyproject.toml" "%PROJECT%\ragflow-windows-additions.txt" --python "%RAG_PY%" --constraints "%RAG_UPSTREAM_REQUIREMENTS%" --overrides "%PROJECT%\ragflow-windows-overrides.txt" --excludes "%PROJECT%\ragflow-windows-excludes.txt" --generate-hashes --no-header --no-annotate --output-file "%RAG_REQUIREMENTS%" >>"%RAG_PREPARE_LOG%" 2>&1
if errorlevel 1 (
    popd >nul
    echo [ERROR] Could not compile the pinned Windows dependency graph.
    echo [INFO] See %RAG_PREPARE_LOG%
    call :PrintLogTail "%RAG_PREPARE_LOG%"
    exit /b 1
)
popd >nul

call :FileSha256 "%RAG_REQUIREMENTS%" RAG_REQUIREMENTS_HASH
if errorlevel 1 exit /b 1
set "RAG_WHEEL_REQUIREMENTS=%RAG_WHEELHOUSE%\requirements.lock"
set "RAG_WHEEL_MARKER=%RAG_WHEELHOUSE%\.local-llm-wheelhouse.ok"
set "RAG_WHEEL_FINGERPRINT=source=%RAG_REQUIREMENTS_HASH%;python=%PY_RAG_VERSION%;pip=%PIN_PIP_VERSION%;locker=%RAG_WHEEL_LOCKER_HASH%"

call :RemoveTreeChecked "%RAG_PY_DIR%\Lib\site-packages"
if errorlevel 1 exit /b 1
call :MakeDirChecked "%RAG_PY_DIR%\Lib\site-packages"
if errorlevel 1 exit /b 1
call :RemoveTreeChecked "%RAG_PY_DIR%\Scripts"
if errorlevel 1 exit /b 1

call :AppendLogBanner "%RAG_PREPARE_LOG%" "bootstrap pip for persistent RAGFlow wheelhouse"
call :BootstrapPipForWheelhouse "%RAG_PY%" "%RAG_WHEELHOUSE%" "%RAG_PREPARE_LOG%"
if errorlevel 1 (
    echo [ERROR] Could not bootstrap pip for the RAGFlow wheelhouse.
    echo [INFO] See %RAG_PREPARE_LOG%
    call :PrintLogTail "%RAG_PREPARE_LOG%"
    exit /b 1
)

call :MarkerMatches "%RAG_WHEEL_MARKER%" "%RAG_WHEEL_FINGERPRINT%"
if errorlevel 1 goto :RAG_WHEELHOUSE_BUILD
if not exist "%RAG_WHEEL_REQUIREMENTS%" goto :RAG_WHEELHOUSE_BUILD
call :AppendLogBanner "%RAG_PREPARE_LOG%" "probe persistent RAGFlow wheelhouse without network"
"%RAG_PY%" -m pip install --dry-run --ignore-installed --no-index --find-links "%RAG_WHEELHOUSE%" --only-binary=:all: --require-hashes --requirement "%RAG_WHEEL_REQUIREMENTS%" >>"%RAG_PREPARE_LOG%" 2>&1
if errorlevel 1 (
    echo [INFO] RAGFlow wheelhouse is incomplete or failed hash validation; rebuilding it.
    goto :RAG_WHEELHOUSE_BUILD
)
echo [SKIP] RAGFlow wheelhouse is complete; no package download was needed.
goto :RAG_WHEELHOUSE_READY

:RAG_WHEELHOUSE_BUILD
set "RAG_WHEEL_STAGE=%WORK%\rag-wheelhouse-%RANDOM%-%RANDOM%"
if exist "%RAG_WHEEL_STAGE%" (
    echo [ERROR] Temporary RAGFlow wheelhouse collision: %RAG_WHEEL_STAGE%
    exit /b 1
)
call :MakeDirChecked "%RAG_WHEEL_STAGE%"
if errorlevel 1 exit /b 1

call :AppendLogBanner "%RAG_PREPARE_LOG%" "cache pinned pip wheel in staged RAGFlow wheelhouse"
"%RAG_PY%" -m pip download --dest "%RAG_WHEEL_STAGE%" --only-binary=:all: "pip==%PIN_PIP_VERSION%" >>"%RAG_PREPARE_LOG%" 2>&1
if errorlevel 1 (
    set "RAG_WHEELHOUSE_ERROR=Could not cache the pinned pip wheel for RAGFlow rebuilds."
    goto :RAG_WHEELHOUSE_BUILD_FAILED
)

call :AppendLogBanner "%RAG_PREPARE_LOG%" "build complete binary RAGFlow wheelhouse from hash-locked inputs"
"%RAG_PY%" -m pip wheel --wheel-dir "%RAG_WHEEL_STAGE%" --require-hashes --requirement "%RAG_REQUIREMENTS%" >>"%RAG_PREPARE_LOG%" 2>&1
if errorlevel 1 (
    set "RAG_WHEELHOUSE_ERROR=Could not build the persistent binary RAGFlow wheelhouse."
    goto :RAG_WHEELHOUSE_BUILD_FAILED
)

copy /y "%APP%\build\vendor-wheels\rag\datrie-%DATRIE_VERSION%-cp313-cp313-win_amd64.whl" "%RAG_WHEEL_STAGE%\datrie-%DATRIE_VERSION%-cp313-cp313-win_amd64.whl" >nul 2>&1
if errorlevel 1 (
    set "RAG_WHEELHOUSE_ERROR=Could not stage the pinned datrie wheel."
    goto :RAG_WHEELHOUSE_BUILD_FAILED
)

set "RAG_WHEEL_SEED=%RAG_WHEEL_STAGE%\requirements.in"
call :AppendLogBanner "%RAG_PREPARE_LOG%" "match source lock to locally built RAGFlow wheels"
"%RAG_PY%" "%PROJECT%\prepare_wheelhouse_lock.py" --requirements "%RAG_REQUIREMENTS%" --wheelhouse "%RAG_WHEEL_STAGE%" --output "%RAG_WHEEL_SEED%" >>"%RAG_PREPARE_LOG%" 2>&1
if errorlevel 1 (
    set "RAG_WHEELHOUSE_ERROR=Could not match the RAGFlow source lock to built wheels."
    goto :RAG_WHEELHOUSE_BUILD_FAILED
)

call :AppendLogBanner "%RAG_PREPARE_LOG%" "compile hash lock for staged RAGFlow wheels"
"%UV_EXE%" pip compile "%RAG_WHEEL_SEED%" --python "%RAG_PY%" --no-index --find-links "%RAG_WHEEL_STAGE%" --generate-hashes --no-header --no-annotate --output-file "%RAG_WHEEL_STAGE%\requirements.lock" >>"%RAG_PREPARE_LOG%" 2>&1
if errorlevel 1 (
    set "RAG_WHEELHOUSE_ERROR=Could not create the hash lock for built RAGFlow wheels."
    goto :RAG_WHEELHOUSE_BUILD_FAILED
)
call :RemoveRegularFileChecked "%RAG_WHEEL_SEED%"
if errorlevel 1 (
    set "RAG_WHEELHOUSE_ERROR=Could not remove the temporary RAGFlow wheel seed."
    goto :RAG_WHEELHOUSE_BUILD_FAILED
)
call :WriteFingerprintMarker "%RAG_WHEEL_STAGE%\.local-llm-wheelhouse.ok" "%RAG_WHEEL_FINGERPRINT%"
if errorlevel 1 (
    set "RAG_WHEELHOUSE_ERROR=Could not seal the staged RAGFlow wheelhouse."
    goto :RAG_WHEELHOUSE_BUILD_FAILED
)
call :CopyExtractedTree "%RAG_WHEEL_STAGE%" "%RAG_WHEELHOUSE%" "requirements.lock"
if errorlevel 1 (
    set "RAG_WHEELHOUSE_ERROR=Could not publish the staged RAGFlow wheelhouse."
    goto :RAG_WHEELHOUSE_BUILD_FAILED
)

:RAG_WHEELHOUSE_READY

copy /y "%APP%\build\vendor-wheels\rag\datrie-%DATRIE_VERSION%-cp313-cp313-win_amd64.whl" "%RAG_WHEELHOUSE%\datrie-%DATRIE_VERSION%-cp313-cp313-win_amd64.whl" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Could not preserve the pinned datrie wheel in %RAG_WHEELHOUSE%
    exit /b 1
)
fc /b "%APP%\build\vendor-wheels\rag\datrie-%DATRIE_VERSION%-cp313-cp313-win_amd64.whl" "%RAG_WHEELHOUSE%\datrie-%DATRIE_VERSION%-cp313-cp313-win_amd64.whl" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Cached datrie wheel differs from its pinned source.
    exit /b 1
)

call :AppendLogBanner "%RAG_PREPARE_LOG%" "uv pip sync RAGFlow runtime from persistent wheelhouse"
"%UV_EXE%" pip sync --python "%RAG_PY%" --no-index --find-links "%RAG_WHEELHOUSE%" --require-hashes "%RAG_WHEEL_REQUIREMENTS%" >>"%RAG_PREPARE_LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] RAGFlow dependency installation failed.
    echo [INFO] Native Windows is not an upstream-supported RAGFlow target.
    echo [INFO] The known datrie compiled gap is supplied as a pinned MSVC wheel.
    echo [INFO] See %RAG_PREPARE_LOG%
    call :PrintLogTail "%RAG_PREPARE_LOG%"
    exit /b 1
)

call :AppendLogBanner "%RAG_PREPARE_LOG%" "uv pip install pinned datrie wheel"
"%UV_EXE%" pip install --python "%RAG_PY%" --no-index --no-deps "%RAG_WHEELHOUSE%\datrie-%DATRIE_VERSION%-cp313-cp313-win_amd64.whl" >>"%RAG_PREPARE_LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] The pinned datrie wheel could not be installed. See %RAG_PREPARE_LOG%
    call :PrintLogTail "%RAG_PREPARE_LOG%"
    exit /b 1
)

call :FinalizeRagflowRuntime
if errorlevel 1 exit /b 1
call :WriteFingerprintMarker "%RAG_MARKER%" "%RAG_FINGERPRINT%"
if errorlevel 1 exit /b 1
exit /b 0

:RAG_WHEELHOUSE_BUILD_FAILED
echo [ERROR] %RAG_WHEELHOUSE_ERROR%
echo [INFO] See %RAG_PREPARE_LOG%
call :PrintLogTail "%RAG_PREPARE_LOG%"
if defined RAG_WHEEL_STAGE if exist "%RAG_WHEEL_STAGE%" call :RemoveTreeChecked "%RAG_WHEEL_STAGE%"
exit /b 1

:FinalizeRagflowRuntime
if not exist "%RAG_PY_DIR%\Lib\site-packages\xgboost\lib\xgboost.dll" (
    echo [ERROR] XGBoost runtime is missing after dependency installation.
    exit /b 1
)
copy /y "%SHARED_DLL_DIR%\vcomp140.dll" "%RAG_PY_DIR%\Lib\site-packages\xgboost\lib\vcomp140.dll" >nul 2>&1
if errorlevel 1 exit /b 1

"%RAG_PY%" "%PROJECT%\prepare_ragflow_windows.py" --ragflow-dir "%RAGFLOW_DIR%" --record "%APP%\config\ragflow-windows-compat.json" >"%LOGS%\ragflow-windows-compat.log" 2>&1
if errorlevel 1 (
    echo [ERROR] RAGFlow Windows compatibility preparation failed.
    echo [INFO] See %LOGS%\ragflow-windows-compat.log
    exit /b 1
)

"%RAG_PY%" "%PROJECT%\sanitize_python_runtime.py" --runtime "%RAG_PY_DIR%" --record "%APP%\config\sanitize-python-rag.json" >"%LOGS%\sanitize-python-rag.log" 2>&1
if errorlevel 1 (
    echo [ERROR] RAGFlow runtime metadata sanitization failed.
    echo [INFO] See %LOGS%\sanitize-python-rag.log
    exit /b 1
)

"%UV_EXE%" pip check --python "%RAG_PY%" >"%LOGS%\ragflow-pip-check.log" 2>&1
if errorlevel 1 (
    echo [ERROR] RAGFlow dependency check failed. See %LOGS%\ragflow-pip-check.log
    exit /b 1
)
"%RAG_PY%" -c "import cv2, elasticsearch, flask, minio, peewee, quart, valkey; print('RAGFlow dependency smoke OK')" >"%LOGS%\ragflow-import-smoke.log" 2>&1
if errorlevel 1 (
    echo [ERROR] RAGFlow import smoke failed. See %LOGS%\ragflow-import-smoke.log
    exit /b 1
)
"%UV_EXE%" pip freeze --python "%RAG_PY%" >"%APP%\config\locks\ragflow-freeze.txt" 2>"%LOGS%\ragflow-freeze.log"
if errorlevel 1 exit /b 1
call :RemoveTreeChecked "%RAG_PY_DIR%\Scripts"
if errorlevel 1 exit /b 1
exit /b 0

:PrepareRagflowAssets
set "RAG_ASSET_RECORD=%APP%\config\ragflow-assets.json"
set "RAG_ASSET_LOG=%LOGS%\ragflow-assets.log"
set "RAG_ASSET_MARKER=%APP%\config\ragflow-assets.ok"
call :FileSha256 "%PROJECT%\prepare_ragflow_assets.py" RAG_ASSET_SCRIPT_HASH
if errorlevel 1 exit /b 1
set "RAG_ASSET_FINGERPRINT=project=%RESUME_GRAPH_VERSION%;ragflow=%RAGFLOW_VERSION%;script=%RAG_ASSET_SCRIPT_HASH%"
call :MarkerMatches "%RAG_ASSET_MARKER%" "%RAG_ASSET_FINGERPRINT%"
if errorlevel 1 goto :RAG_ASSETS_VERIFY
call :ValidateRagflowAssetsPrepared
if errorlevel 1 (
    echo [WARN] The fingerprinted RAGFlow asset set is incomplete; verifying it again.
    if exist "%RAG_ASSET_MARKER%" del /f /q "%RAG_ASSET_MARKER%" >nul 2>&1
    goto :RAG_ASSETS_VERIFY
)
echo [SKIP] RAGFlow models, NLTK, tiktoken and Tika assets already prepared.
exit /b 0

:RAG_ASSETS_VERIFY
echo [STEP] Verify pinned RAGFlow models, NLTK, tiktoken and Tika assets
call :BeginStepLog "%RAG_ASSET_LOG%" "local_llm RAGFlow asset log"
if errorlevel 1 exit /b 1
"%RAG_PY%" "%PROJECT%\prepare_ragflow_assets.py" --ragflow-dir "%RAGFLOW_DIR%" --nltk-dir "%NLTK_DATA%" --record "%RAG_ASSET_RECORD%" --manual-assets-dir "%SRC%" >>"%RAG_ASSET_LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] RAGFlow asset preparation failed. See %RAG_ASSET_LOG%
    call :PrintLogTail "%RAG_ASSET_LOG%"
    exit /b 1
)
call :ValidateRagflowAssetsPrepared
if errorlevel 1 (
    echo [ERROR] RAGFlow asset preparation finished but required files are missing.
    call :PrintLogTail "%RAG_ASSET_LOG%"
    exit /b 1
)
call :WriteFingerprintMarker "%RAG_ASSET_MARKER%" "%RAG_ASSET_FINGERPRINT%"
if errorlevel 1 exit /b 1
exit /b 0

:ValidateRagflowAssetsPrepared
if not exist "%RAG_ASSET_RECORD%" exit /b 1
for %%F in (
    "%RAGFLOW_DIR%\rag\res\deepdoc\det.onnx"
    "%RAGFLOW_DIR%\rag\res\deepdoc\layout.onnx"
    "%RAGFLOW_DIR%\rag\res\deepdoc\updown_concat_xgb.model"
    "%RAGFLOW_DIR%\ragflow_deps\cl100k_base.tiktoken"
    "%RAGFLOW_DIR%\tika-server-standard-3.3.0.jar"
    "%RAGFLOW_DIR%\tika-server-standard-3.3.0.jar.md5"
    "%NLTK_DATA%\corpora\wordnet.zip"
    "%NLTK_DATA%\tokenizers\punkt.zip"
    "%NLTK_DATA%\tokenizers\punkt_tab.zip"
) do (
    if not exist "%%~F" exit /b 1
)
exit /b 0

:BootstrapPipForWheelhouse
setlocal DisableDelayedExpansion
set "BOOTSTRAP_PYTHON=%~1"
set "BOOTSTRAP_WHEELHOUSE=%~2"
set "BOOTSTRAP_LOG=%~3"
set "BOOTSTRAP_PIP_WHEEL=%~2\pip-%PIN_PIP_VERSION%-py3-none-any.whl"
if exist "%BOOTSTRAP_PIP_WHEEL%" (
    "%UV_EXE%" pip install --python "%BOOTSTRAP_PYTHON%" --no-index --find-links "%BOOTSTRAP_WHEELHOUSE%" "pip==%PIN_PIP_VERSION%" >>"%BOOTSTRAP_LOG%" 2>&1
    if not errorlevel 1 (
        endlocal & exit /b 0
    )
    >>"%BOOTSTRAP_LOG%" echo [WARN] Cached pip bootstrap failed; retrying from the package index.
)
"%UV_EXE%" pip install --python "%BOOTSTRAP_PYTHON%" "pip==%PIN_PIP_VERSION%" >>"%BOOTSTRAP_LOG%" 2>&1
set "BOOTSTRAP_RC=%ERRORLEVEL%"
endlocal & exit /b %BOOTSTRAP_RC%

:BeginStepLog
setlocal
set "BEGIN_LOG_PATH=%~1"
if "%BEGIN_LOG_PATH%"=="" (endlocal & exit /b 1)
for %%D in ("%BEGIN_LOG_PATH%") do if not exist "%%~dpD." (
    echo [ERROR] Log directory does not exist: %%~dpD
    endlocal & exit /b 1
)
if exist "%BEGIN_LOG_PATH%" del /f /q "%BEGIN_LOG_PATH%" >nul 2>&1
copy /y nul "%BEGIN_LOG_PATH%" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Could not initialize log file: %BEGIN_LOG_PATH%
    endlocal & exit /b 1
)
call :StartLiveLog "%BEGIN_LOG_PATH%" "%~2"
endlocal & exit /b %ERRORLEVEL%

:StartLiveLog
setlocal
if /i "%LOCAL_LLM_LIVE_LOGS%"=="0" endlocal & exit /b 0
if /i "%LOCAL_LLM_LIVE_LOGS%"=="off" endlocal & exit /b 0
if /i "%LOCAL_LLM_LIVE_LOGS%"=="false" endlocal & exit /b 0
set "LOCAL_LLM_LIVE_LOG=%~1"
echo [INFO] Live log window: %LOCAL_LLM_LIVE_LOG%
start "%~2" "%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -NoExit -Command "Write-Host ('Watching log: ' + $env:LOCAL_LLM_LIVE_LOG); Get-Content -LiteralPath $env:LOCAL_LLM_LIVE_LOG -Wait -Tail 200"
endlocal & exit /b 0

:AppendLogBanner
>>"%~1" echo.
>>"%~1" echo [CMD] %~2
exit /b %ERRORLEVEL%

:PrintLogTail
setlocal
set "TAIL_LOG=%~1"
if not exist "%TAIL_LOG%" (endlocal & exit /b 0)
echo [INFO] Last log lines:
"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='SilentlyContinue'; Get-Content -LiteralPath $env:TAIL_LOG -Tail 40" 2>nul
endlocal & exit /b 0

:VerifyRagflowRuntime
set "RAG_RUNTIME_MARKER=%APP%\config\ragflow-runtime-smoke.ok"
call :FileSha256 "%PROJECT%\verify_ragflow_runtime.py" RAG_RUNTIME_SCRIPT_HASH
if errorlevel 1 exit /b 1
set "RAG_RUNTIME_FINGERPRINT=project=%RESUME_GRAPH_VERSION%;ragflow=%RAGFLOW_VERSION%;python=%PY_RAG_VERSION%;script=%RAG_RUNTIME_SCRIPT_HASH%;runtime=%RAG_FINGERPRINT%;assets=%RAG_ASSET_FINGERPRINT%"
call :MarkerMatches "%RAG_RUNTIME_MARKER%" "%RAG_RUNTIME_FINGERPRINT%"
if errorlevel 1 goto :RAG_RUNTIME_VERIFY
if not exist "%LOGS%\ragflow-runtime-smoke.log" goto :RAG_RUNTIME_VERIFY
echo [SKIP] RAGFlow runtime smoke already passed.
exit /b 0

:RAG_RUNTIME_VERIFY
echo [STEP] Exercise the Windows-compatible RAGFlow worker, tokenizer and XGBoost model
set "RAG_RUNTIME_LOG=%LOGS%\ragflow-runtime-smoke.log"
call :BeginStepLog "%RAG_RUNTIME_LOG%" "local_llm RAGFlow runtime smoke log"
if errorlevel 1 exit /b 1
"%RAG_PY%" "%PROJECT%\verify_ragflow_runtime.py" --ragflow-dir "%RAGFLOW_DIR%" >>"%RAG_RUNTIME_LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] RAGFlow runtime smoke failed. See %RAG_RUNTIME_LOG%
    call :PrintLogTail "%RAG_RUNTIME_LOG%"
    exit /b 1
)
call :WriteFingerprintMarker "%RAG_RUNTIME_MARKER%" "%RAG_RUNTIME_FINGERPRINT%"
if errorlevel 1 exit /b 1
exit /b 0

:BuildRagflowWeb
if not exist "%RAGFLOW_DIR%\web\package-lock.json" (
    echo [ERROR] RAGFlow web package-lock.json is missing.
    exit /b 1
)
call :FileSha256 "%RAGFLOW_DIR%\web\package-lock.json" WEB_LOCK_HASH
if errorlevel 1 exit /b 1
set "WEB_MARKER=%APP%\config\ragflow-web.ok"
set "WEB_FINGERPRINT=project=%RESUME_GRAPH_VERSION%;ragflow=%RAGFLOW_VERSION%;node=%NODE_VERSION%;lock=%WEB_LOCK_HASH%"
call :MarkerMatches "%WEB_MARKER%" "%WEB_FINGERPRINT%"
if errorlevel 1 goto :WEB_BUILD
if not exist "%APP%\web\index.html" goto :WEB_BUILD
call :TreeMatchesMarker "%APP%\web" "%WEB_MARKER%" ""
if errorlevel 1 (
    echo [WARN] The fingerprinted web dist changed; rebuilding it.
    goto :WEB_BUILD
)
findstr /b /i /c:"tree_manifest=" "%WEB_MARKER%" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Adding a per-file diagnostic manifest to the existing web seal.
    call :ComputeTreeFingerprint "%APP%\web" "" WEB_TREE_SHA256 WEB_TREE_FILE_COUNT "%APP%\config\tree-manifests\ragflow-web.manifest"
    if errorlevel 1 (
        echo [ERROR] Could not create the RAGFlow web diagnostic manifest.
        exit /b 1
    )
    call :WriteSealedFingerprintMarker "%WEB_MARKER%" "%WEB_FINGERPRINT%" "%WEB_TREE_SHA256%" "%WEB_TREE_FILE_COUNT%" "tree-manifests/ragflow-web.manifest"
    if errorlevel 1 (
        echo [ERROR] Could not update the RAGFlow web dist marker: %WEB_MARKER%
        exit /b 1
    )
)
echo [SKIP] RAGFlow production web build already prepared and fingerprinted.
exit /b 0

:WEB_BUILD
echo [STEP] Build production RAGFlow web assets with Node %NODE_VERSION%
set "WEB_BUILD_LOG=%LOGS%\ragflow-web-build.log"
call :BeginStepLog "%WEB_BUILD_LOG%" "local_llm RAGFlow web build log"
if errorlevel 1 exit /b 1
set "NODE_OPTIONS=--max-old-space-size=6144"
set "npm_config_scripts_prepend_node_path=true"
set "NPM_CONFIG_SCRIPTS_PREPEND_NODE_PATH=true"
set "VITE_BUILD_SOURCEMAP=false"
set "VITE_MINIFY=esbuild"
call :RemoveTreeChecked "%RAGFLOW_DIR%\web\dist"
if errorlevel 1 (
    echo [ERROR] Could not remove stale RAGFlow web dist before build.
    exit /b 1
)
pushd "%RAGFLOW_DIR%\web" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Could not enter the RAGFlow web source directory.
    exit /b 1
)
call :AppendLogBanner "%WEB_BUILD_LOG%" "npm ci --no-audit --no-fund"
"%ComSpec%" /d /s /c ""%NODE_DIR%\npm.cmd" ci --no-audit --no-fund" >>"%WEB_BUILD_LOG%" 2>&1
set "WEB_NPM_CI_RC=%ERRORLEVEL%"
if not "%WEB_NPM_CI_RC%"=="0" (
    popd >nul
    echo [ERROR] npm ci failed with exit code %WEB_NPM_CI_RC%. See %WEB_BUILD_LOG%
    call :PrintLogTail "%WEB_BUILD_LOG%"
    exit /b 1
)
call :AppendLogBanner "%WEB_BUILD_LOG%" "npm run build"
"%ComSpec%" /d /s /c ""%NODE_DIR%\npm.cmd" run build" >>"%WEB_BUILD_LOG%" 2>&1
set "WEB_NPM_BUILD_RC=%ERRORLEVEL%"
if not "%WEB_NPM_BUILD_RC%"=="0" (
    popd >nul
    echo [ERROR] RAGFlow web build failed with exit code %WEB_NPM_BUILD_RC%. See %WEB_BUILD_LOG%
    call :PrintLogTail "%WEB_BUILD_LOG%"
    exit /b 1
)
popd >nul
if not exist "%RAGFLOW_DIR%\web\dist\index.html" (
    echo [ERROR] Web build did not produce dist\index.html.
    exit /b 1
)
call :AppendLogBanner "%WEB_BUILD_LOG%" "move built web dist into app web"
call :RemoveTreeChecked "%APP%\web"
if errorlevel 1 (
    echo [ERROR] Could not remove the previous app web dist: %APP%\web
    call :PrintLogTail "%WEB_BUILD_LOG%"
    exit /b 1
)
move "%RAGFLOW_DIR%\web\dist" "%APP%\web" >>"%WEB_BUILD_LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] Could not move the built RAGFlow web dist into app\web.
    echo [INFO] See %WEB_BUILD_LOG%
    call :PrintLogTail "%WEB_BUILD_LOG%"
    exit /b 1
)
if not exist "%APP%\web\index.html" (
    echo [ERROR] Moved RAGFlow web dist is missing index.html: %APP%\web\index.html
    call :PrintLogTail "%WEB_BUILD_LOG%"
    exit /b 1
)
call :AppendLogBanner "%WEB_BUILD_LOG%" "remove temporary web node_modules"
call :RemoveTreeChecked "%RAGFLOW_DIR%\web\node_modules"
if errorlevel 1 (
    echo [ERROR] Could not remove temporary RAGFlow web node_modules.
    call :PrintLogTail "%WEB_BUILD_LOG%"
    exit /b 1
)
call :AppendLogBanner "%WEB_BUILD_LOG%" "fingerprint app web dist"
call :ComputeTreeFingerprint "%APP%\web" "" WEB_TREE_SHA256 WEB_TREE_FILE_COUNT "%APP%\config\tree-manifests\ragflow-web.manifest"
if errorlevel 1 (
    echo [ERROR] Could not fingerprint the prepared RAGFlow web dist.
    call :PrintLogTail "%WEB_BUILD_LOG%"
    exit /b 1
)
call :WriteSealedFingerprintMarker "%WEB_MARKER%" "%WEB_FINGERPRINT%" "%WEB_TREE_SHA256%" "%WEB_TREE_FILE_COUNT%" "tree-manifests/ragflow-web.manifest"
if errorlevel 1 (
    echo [ERROR] Could not write the RAGFlow web dist marker: %WEB_MARKER%
    call :PrintLogTail "%WEB_BUILD_LOG%"
    exit /b 1
)
exit /b 0

REM ============================================================================
REM OCR WHEELHOUSE, INSTALL AND STRICT GPU WARMUP
REM ============================================================================

:PrepareOcrPython
call :FileSha256 "%PROJECT%\requirements-ocr.txt" OCR_REQUIREMENTS_HASH
if errorlevel 1 exit /b 1
set "OCR_MARKER=%APP%\config\ocr-python.ok"
set "OCR_FINGERPRINT=project=%RESUME_GRAPH_VERSION%;python=%PY_OCR_VERSION%;paddle=%PADDLE_VERSION%;paddleocr=%PADDLEOCR_VERSION%;paddlex=%PADDLEX_VERSION%;requirements=%OCR_REQUIREMENTS_HASH%"
call :MarkerMatches "%OCR_MARKER%" "%OCR_FINGERPRINT%"
if errorlevel 1 goto :OCR_BUILD_RUNTIME
call :FinalizeOcrRuntime
if errorlevel 1 goto :OCR_REBUILD_RUNTIME
echo [SKIP] OCR Python runtime already prepared, sanitized and verified.
exit /b 0

:OCR_REBUILD_RUNTIME
echo [WARN] The fingerprinted OCR runtime failed validation; rebuilding it.
if exist "%OCR_MARKER%" del /f /q "%OCR_MARKER%" >nul 2>&1

:OCR_BUILD_RUNTIME

echo [STEP] Resolve an offline-complete binary OCR wheelhouse
set "OCR_PREPARE_LOG=%LOGS%\ocr-python-prepare.log"
call :BeginStepLog "%OCR_PREPARE_LOG%" "local_llm OCR Python preparation log"
if errorlevel 1 exit /b 1
call :RemoveTreeChecked "%OCR_PY_DIR%\Lib\site-packages"
if errorlevel 1 exit /b 1
call :MakeDirChecked "%OCR_PY_DIR%\Lib\site-packages"
if errorlevel 1 exit /b 1
call :RemoveTreeChecked "%OCR_PY_DIR%\Scripts"
if errorlevel 1 exit /b 1

call :AppendLogBanner "%OCR_PREPARE_LOG%" "bootstrap pip for persistent OCR wheelhouse"
call :BootstrapPipForWheelhouse "%OCR_PY%" "%OCR_WHEELHOUSE%" "%OCR_PREPARE_LOG%"
if errorlevel 1 (
    echo [ERROR] OCR pip bootstrap failed. See %OCR_PREPARE_LOG%
    call :PrintLogTail "%OCR_PREPARE_LOG%"
    exit /b 1
)

if not exist "%OCR_WHEELHOUSE%\pip-%PIN_PIP_VERSION%-py3-none-any.whl" (
    call :AppendLogBanner "%OCR_PREPARE_LOG%" "cache pinned pip wheel for OCR rebuilds"
    "%OCR_PY%" -m pip download --dest "%OCR_WHEELHOUSE%" --only-binary=:all: "pip==%PIN_PIP_VERSION%" >>"%OCR_PREPARE_LOG%" 2>&1
    if errorlevel 1 (
        echo [ERROR] Could not cache the pinned pip wheel for OCR rebuilds.
        echo [INFO] See %OCR_PREPARE_LOG%
        call :PrintLogTail "%OCR_PREPARE_LOG%"
        exit /b 1
    )
)

copy /y "%APP%\build\vendor-wheels\ocr\paddlepaddle_gpu-%PADDLE_VERSION%-cp311-cp311-win_amd64.whl" "%OCR_WHEELHOUSE%\paddlepaddle_gpu-%PADDLE_VERSION%-cp311-cp311-win_amd64.whl" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Could not preserve the pinned Paddle GPU wheel in %OCR_WHEELHOUSE%
    exit /b 1
)
fc /b "%APP%\build\vendor-wheels\ocr\paddlepaddle_gpu-%PADDLE_VERSION%-cp311-cp311-win_amd64.whl" "%OCR_WHEELHOUSE%\paddlepaddle_gpu-%PADDLE_VERSION%-cp311-cp311-win_amd64.whl" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Cached Paddle GPU wheel differs from its pinned source.
    exit /b 1
)

call :AppendLogBanner "%OCR_PREPARE_LOG%" "probe persistent OCR wheelhouse without network"
"%OCR_PY%" -m pip download --dest "%OCR_WHEELHOUSE%" --no-index --find-links "%OCR_WHEELHOUSE%" --only-binary=:all: "%OCR_WHEELHOUSE%\paddlepaddle_gpu-%PADDLE_VERSION%-cp311-cp311-win_amd64.whl" --requirement "%PROJECT%\requirements-ocr.txt" >>"%OCR_PREPARE_LOG%" 2>&1
if errorlevel 1 (
    echo [INFO] OCR wheelhouse is incomplete; downloading only missing wheels.
    call :AppendLogBanner "%OCR_PREPARE_LOG%" "complete persistent OCR wheelhouse from package index"
    "%OCR_PY%" -m pip download --dest "%OCR_WHEELHOUSE%" --find-links "%OCR_WHEELHOUSE%" --only-binary=:all: "%APP%\build\vendor-wheels\ocr\paddlepaddle_gpu-%PADDLE_VERSION%-cp311-cp311-win_amd64.whl" --requirement "%PROJECT%\requirements-ocr.txt" >>"%OCR_PREPARE_LOG%" 2>&1
    if errorlevel 1 (
        echo [ERROR] Could not create the persistent binary OCR wheelhouse.
        echo [INFO] See %OCR_PREPARE_LOG%
        call :PrintLogTail "%OCR_PREPARE_LOG%"
        exit /b 1
    )
) else (
    echo [SKIP] OCR wheelhouse is complete; no package download was needed.
)

call :AppendLogBanner "%OCR_PREPARE_LOG%" "uv pip install OCR from persistent wheelhouse"
"%UV_EXE%" pip install --python "%OCR_PY%" --no-index --find-links "%OCR_WHEELHOUSE%" "%OCR_WHEELHOUSE%\paddlepaddle_gpu-%PADDLE_VERSION%-cp311-cp311-win_amd64.whl" --requirements "%PROJECT%\requirements-ocr.txt" >>"%OCR_PREPARE_LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] OCR installation from the local wheelhouse failed.
    echo [INFO] See %OCR_PREPARE_LOG%
    call :PrintLogTail "%OCR_PREPARE_LOG%"
    exit /b 1
)

call :FinalizeOcrRuntime
if errorlevel 1 exit /b 1
call :WriteFingerprintMarker "%OCR_MARKER%" "%OCR_FINGERPRINT%"
if errorlevel 1 exit /b 1
exit /b 0

:FinalizeOcrRuntime
"%OCR_PY%" "%PROJECT%\sanitize_python_runtime.py" --runtime "%OCR_PY_DIR%" --record "%APP%\config\sanitize-python-ocr.json" >"%LOGS%\sanitize-python-ocr.log" 2>&1
if errorlevel 1 (
    echo [ERROR] OCR runtime metadata sanitization failed.
    echo [INFO] See %LOGS%\sanitize-python-ocr.log
    exit /b 1
)
"%UV_EXE%" pip check --python "%OCR_PY%" >"%LOGS%\ocr-pip-check.log" 2>&1
if errorlevel 1 (
    echo [ERROR] OCR dependency check failed. See %LOGS%\ocr-pip-check.log
    exit /b 1
)
"%UV_EXE%" pip freeze --python "%OCR_PY%" >"%APP%\config\locks\ocr-freeze.txt" 2>"%LOGS%\ocr-freeze.log"
if errorlevel 1 exit /b 1
call :ProbeOcrImports
if errorlevel 1 exit /b 1
call :RemoveTreeChecked "%OCR_PY_DIR%\Scripts"
if errorlevel 1 exit /b 1
exit /b 0

:ProbeOcrImports
"%OCR_PY%" -c "import paddle,paddleocr,paddlex; print('paddle='+paddle.__version__); print('paddleocr='+paddleocr.__version__); print('paddlex='+paddlex.__version__); print('OCR imports OK')" >"%LOGS%\ocr-import-smoke.log" 2>&1
if errorlevel 1 (
    echo [ERROR] OCR import smoke failed. See %LOGS%\ocr-import-smoke.log
    exit /b 1
)
exit /b 0

:RunOcrContract
set "OCR_CONTRACT_MARKER=%APP%\config\ocr-gateway-contract.ok"
call :FileSha256 "%PROJECT%\ocr_job_gateway.py" OCR_GATEWAY_HASH
if errorlevel 1 exit /b 1
call :FileSha256 "%PROJECT%\test_ocr_job_gateway_contract.py" OCR_CONTRACT_TEST_HASH
if errorlevel 1 exit /b 1
set "OCR_CONTRACT_FINGERPRINT=project=%RESUME_GRAPH_VERSION%;ocr=%OCR_FINGERPRINT%;gateway=%OCR_GATEWAY_HASH%;test=%OCR_CONTRACT_TEST_HASH%"
call :MarkerMatches "%OCR_CONTRACT_MARKER%" "%OCR_CONTRACT_FINGERPRINT%"
if errorlevel 1 goto :OCR_CONTRACT_VERIFY
if not exist "%LOGS%\ocr-gateway-contract.log" goto :OCR_CONTRACT_VERIFY
echo [SKIP] OCR gateway contract already passed.
exit /b 0

:OCR_CONTRACT_VERIFY
echo [STEP] Validate the local OCR job API contract without loading GPU models
set "OCR_CONTRACT_LOG=%LOGS%\ocr-gateway-contract.log"
call :BeginStepLog "%OCR_CONTRACT_LOG%" "local_llm OCR gateway contract log"
if errorlevel 1 exit /b 1
"%OCR_PY%" "%APP%\services\ocr\test_ocr_job_gateway_contract.py" >>"%OCR_CONTRACT_LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] OCR gateway contract test failed. See %OCR_CONTRACT_LOG%
    call :PrintLogTail "%OCR_CONTRACT_LOG%"
    exit /b 1
)
call :WriteFingerprintMarker "%OCR_CONTRACT_MARKER%" "%OCR_CONTRACT_FINGERPRINT%"
if errorlevel 1 exit /b 1

exit /b 0

:RunStrictGpuE2E
set "GPU_E2E_MARKER=%APP%\config\ocr-gpu-smoke.ok"
call :FileSha256 "%PROJECT%\ocr_ragflow_e2e.py" GPU_E2E_SCRIPT_HASH
if errorlevel 1 exit /b 1
call :FileSha256 "%PROJECT%\ocr_job_gateway.py" GPU_E2E_GATEWAY_HASH
if errorlevel 1 exit /b 1
call :FileSha256 "%PROJECT%\pp-structure-v3-8gb.yaml" GPU_E2E_CONFIG_HASH
if errorlevel 1 exit /b 1
set "GPU_E2E_FINGERPRINT=project=%RESUME_GRAPH_VERSION%;rag=%RAG_FINGERPRINT%;assets=%RAG_ASSET_FINGERPRINT%;ocr=%OCR_FINGERPRINT%;script=%GPU_E2E_SCRIPT_HASH%;gateway=%GPU_E2E_GATEWAY_HASH%;config=%GPU_E2E_CONFIG_HASH%;gpu=%LOCAL_OCR_GPU_INDEX%"
call :MarkerMatches "%GPU_E2E_MARKER%" "%GPU_E2E_FINGERPRINT%"
if errorlevel 1 goto :GPU_E2E_VERIFY
if not exist "%APP%\config\ocr-gpu-smoke.json" goto :GPU_E2E_VERIFY
echo [SKIP] Strict GPU OCR/RAGFlow E2E already passed for LOCAL_OCR_GPU_INDEX=%LOCAL_OCR_GPU_INDEX%.
exit /b 0

:GPU_E2E_VERIFY
echo [STEP] Run strict CUDA, OCR warmup and RAGFlow parser E2E on the selected GPU
set "GPU_E2E_LOG=%LOGS%\ocr-gpu-smoke.log"
call :BeginStepLog "%GPU_E2E_LOG%" "local_llm strict GPU OCR/RAGFlow E2E log"
if errorlevel 1 exit /b 1
where nvidia-smi >nul 2>&1
if errorlevel 1 (
    echo [ERROR] nvidia-smi is not available.
    echo [ERROR] The NVIDIA driver must already be installed by an administrator.
    echo [ERROR] This project never falls back to CPU inference.
    exit /b 1
)
nvidia-smi --query-gpu=index,name,memory.total,driver_version --format=csv,noheader >>"%GPU_E2E_LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] Could not query the NVIDIA driver. See %GPU_E2E_LOG%
    call :PrintLogTail "%GPU_E2E_LOG%"
    exit /b 1
)

"%RAG_PY%" "%APP%\services\ocr\ocr_ragflow_e2e.py" --ocr-python "%OCR_PY%" --gateway-script "%APP%\services\ocr\ocr_job_gateway.py" --config "%APP%\config\pp-structure-v3-8gb.yaml" --model-root "%PADDLE_PDX_CACHE_HOME%\official_models" --font "%APP%\services\ocr\font\ttf\DejaVuSans.ttf" --ragflow-dir "%RAGFLOW_DIR%" --work-dir "%WORK%\ocr-e2e" --record "%APP%\config\ocr-gpu-smoke.json" --log "%LOGS%\ocr-gateway-e2e-server.log" >>"%GPU_E2E_LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] Strict GPU OCR warmup failed. No CPU fallback was attempted.
    echo [INFO] GTX 1080 requires the pinned cu118 wheel with sm_61 and a working driver.
    echo [INFO] The E2E test also requires actual text from RAGFlow's stock PaddleOCR parser.
    echo [INFO] See %GPU_E2E_LOG%
    call :PrintLogTail "%GPU_E2E_LOG%"
    exit /b 1
)
echo [OK] Strict GPU OCR warmup passed.
call :WriteFingerprintMarker "%GPU_E2E_MARKER%" "%GPU_E2E_FINGERPRINT%"
if errorlevel 1 exit /b 1
exit /b 0

REM ============================================================================
REM PROBES, PORTABILITY AUDIT AND PACKAGING
REM ============================================================================

:ProbePortableBinaries
set "PROBE_MARKER=%APP%\config\probe-portable-binaries.ok"
set "PROBE_FINGERPRINT=project=%RESUME_GRAPH_VERSION%;vc=%VC_FINGERPRINT%;mysql=%MYSQL_VERSION%;elastic=%ELASTIC_VERSION%;valkey=%VALKEY_VERSION%;caddy=%CADDY_VERSION%;llama=%LLAMA_BUILD%"
call :MarkerMatches "%PROBE_MARKER%" "%PROBE_FINGERPRINT%"
if errorlevel 1 goto :PROBE_RUN
call :PortableBinaryKeysPresent
if errorlevel 1 (
    echo [WARN] The fingerprinted portable binary set is incomplete; probing again.
    if exist "%PROBE_MARKER%" del /f /q "%PROBE_MARKER%" >nul 2>&1
    goto :PROBE_RUN
)
echo [SKIP] Portable service and inference executable probes already passed.
exit /b 0

:PROBE_RUN
echo [STEP] Probe portable service and inference executables
set "PROBE_LOG=%LOGS%\probe-portable-binaries.log"
call :BeginStepLog "%PROBE_LOG%" "local_llm portable binary probe log"
if errorlevel 1 exit /b 1
if not exist "%APP%\services\elasticsearch\jdk\bin\java.exe" goto :PROBE_FAILED
set "ES_JAVA_HOME=%APP%\services\elasticsearch\jdk"
set "JAVA_HOME=%ES_JAVA_HOME%"
set "PATH=%JAVA_HOME%\bin;%PATH%"
call :AppendLogBanner "%PROBE_LOG%" "java -version"
"%JAVA_HOME%\bin\java.exe" -version >>"%PROBE_LOG%" 2>&1
if errorlevel 1 goto :PROBE_FAILED
call :AppendLogBanner "%PROBE_LOG%" "mysqld --version"
"%APP%\services\mysql\bin\mysqld.exe" --version >>"%PROBE_LOG%" 2>&1
if errorlevel 1 goto :PROBE_FAILED
findstr /c:"%MYSQL_VERSION%" "%PROBE_LOG%" >nul 2>&1
if errorlevel 1 goto :PROBE_FAILED
call :AppendLogBanner "%PROBE_LOG%" "elasticsearch.bat -V"
call "%APP%\services\elasticsearch\bin\elasticsearch.bat" -V >>"%PROBE_LOG%" 2>&1
if errorlevel 1 goto :PROBE_FAILED
findstr /c:"%ELASTIC_VERSION%" "%PROBE_LOG%" >nul 2>&1
if errorlevel 1 goto :PROBE_FAILED
call :AppendLogBanner "%PROBE_LOG%" "valkey-server --version"
"%APP%\services\valkey\valkey-server.exe" --version >>"%PROBE_LOG%" 2>&1
if errorlevel 1 goto :PROBE_FAILED
findstr /c:"%VALKEY_VERSION%" "%PROBE_LOG%" >nul 2>&1
if errorlevel 1 goto :PROBE_FAILED
call :AppendLogBanner "%PROBE_LOG%" "caddy version"
"%APP%\services\caddy\caddy.exe" version >>"%PROBE_LOG%" 2>&1
if errorlevel 1 goto :PROBE_FAILED
findstr /c:"%CADDY_VERSION%" "%PROBE_LOG%" >nul 2>&1
if errorlevel 1 goto :PROBE_FAILED
call :AppendLogBanner "%PROBE_LOG%" "llama-server --version"
"%APP%\runtime\llama\llama-server.exe" --version >>"%PROBE_LOG%" 2>&1
if errorlevel 1 goto :PROBE_FAILED
findstr /c:"%LLAMA_BUILD:~1%" "%PROBE_LOG%" >nul 2>&1
if errorlevel 1 goto :PROBE_FAILED
call :AppendLogBanner "%PROBE_LOG%" "silo --version"
"%APP%\services\silo\silo.exe" --version >>"%PROBE_LOG%" 2>&1
if errorlevel 1 goto :PROBE_FAILED
call :WriteFingerprintMarker "%PROBE_MARKER%" "%PROBE_FINGERPRINT%"
if errorlevel 1 exit /b 1
exit /b 0

:PortableBinaryKeysPresent
for %%F in (
    "%APP%\services\elasticsearch\jdk\bin\java.exe"
    "%APP%\services\mysql\bin\mysqld.exe"
    "%APP%\services\elasticsearch\bin\elasticsearch.bat"
    "%APP%\services\valkey\valkey-server.exe"
    "%APP%\services\caddy\caddy.exe"
    "%APP%\runtime\llama\llama-server.exe"
    "%APP%\services\silo\silo.exe"
) do (
    if not exist "%%~F" exit /b 1
)
exit /b 0

:PROBE_FAILED
echo [ERROR] A portable executable could not start. See %PROBE_LOG%
echo [INFO] A missing Microsoft runtime DLL is a common cause on clean Windows PCs.
call :PrintLogTail "%PROBE_LOG%"
exit /b 1

:AuditPortableRuntimes
set "AUDIT_MARKER=%APP%\config\audit-portable-runtimes.ok"
call :FileSha256 "%PROJECT%\audit_portability.py" AUDIT_SCRIPT_HASH
if errorlevel 1 exit /b 1
set "AUDIT_FINGERPRINT=project=%RESUME_GRAPH_VERSION%;script=%AUDIT_SCRIPT_HASH%;rag=%RAG_FINGERPRINT%;ocr=%OCR_FINGERPRINT%"
call :MarkerMatches "%AUDIT_MARKER%" "%AUDIT_FINGERPRINT%"
if errorlevel 1 goto :AUDIT_RUN
if not exist "%APP%\config\audit-python-rag.json" goto :AUDIT_RUN
if not exist "%APP%\config\audit-python-ocr.json" goto :AUDIT_RUN
echo [SKIP] Portable runtime audit already passed.
exit /b 0

:AUDIT_RUN
echo [STEP] Check critical launch/config files for build-path leaks and symlinks
set "AUDIT_LOG=%LOGS%\audit-portable-runtimes.log"
call :BeginStepLog "%AUDIT_LOG%" "local_llm portable runtime audit log"
if errorlevel 1 exit /b 1
call :AppendLogBanner "%AUDIT_LOG%" "audit RAGFlow Python runtime"
"%RAG_PY%" "%PROJECT%\audit_portability.py" --root "%RAG_PY_DIR%" --build-root "%ROOT%" --record "%APP%\config\audit-python-rag.json" >>"%AUDIT_LOG%" 2>&1
if errorlevel 1 goto :AUDIT_FAILED
call :AppendLogBanner "%AUDIT_LOG%" "audit OCR Python runtime"
"%RAG_PY%" "%PROJECT%\audit_portability.py" --root "%OCR_PY_DIR%" --build-root "%ROOT%" --record "%APP%\config\audit-python-ocr.json" >>"%AUDIT_LOG%" 2>&1
if errorlevel 1 goto :AUDIT_FAILED
call :WriteFingerprintMarker "%AUDIT_MARKER%" "%AUDIT_FINGERPRINT%"
if errorlevel 1 exit /b 1
exit /b 0

:AUDIT_FAILED
echo [ERROR] Portability audit failed. See %AUDIT_LOG%
call :PrintLogTail "%AUDIT_LOG%"
exit /b 1

:PreparePortableSeed
echo [STEP] Remove install-local configuration, secrets and logs from the portable seed
call :RemoveTreeChecked "%APP%\config\runtime"
if errorlevel 1 exit /b 1
call :RemoveRegularFileChecked "%RAGFLOW_DIR%\conf\local.service_conf.yaml"
if errorlevel 1 exit /b 1
call :RemoveTreeChecked "%RAGFLOW_DIR%\logs"
if errorlevel 1 exit /b 1
exit /b 0

:SealMutableRuntimeTrees
echo [STEP] Seal final Python and RAGFlow trees after all online mutations
call :SealOneTree "%RAG_PY_DIR%" "%RAG_PY_TREE_MARKER%" "%RAG_PY_TREE_FINGERPRINT%" "" "tree-manifests/python-rag.manifest"
if errorlevel 1 exit /b 1
call :SealOneTree "%OCR_PY_DIR%" "%OCR_PY_TREE_MARKER%" "%OCR_PY_TREE_FINGERPRINT%" "" "tree-manifests/python-ocr.manifest"
if errorlevel 1 exit /b 1
call :SealOneTree "%RAGFLOW_DIR%" "%RAGFLOW_TREE_MARKER%" "%RAGFLOW_TREE_FINGERPRINT%" "%RAGFLOW_TREE_EXCLUDES%" "tree-manifests/ragflow.manifest"
if errorlevel 1 exit /b 1
exit /b 0

:SealOneTree
set "FINAL_TREE_SHA256="
set "FINAL_TREE_FILE_COUNT="
call :ComputeTreeFingerprint "%~1" "%~4" FINAL_TREE_SHA256 FINAL_TREE_FILE_COUNT "%APP%\config\%~5"
if errorlevel 1 exit /b 1
call :WriteSealedFingerprintMarker "%~2" "%~3" "%FINAL_TREE_SHA256%" "%FINAL_TREE_FILE_COUNT%" "%~5"
if errorlevel 1 exit /b 1
call :MutableTreeMatches "%~1" "%~2" "%~3" "%~4"
exit /b %ERRORLEVEL%

:SealImmutableArtifactTrees
echo [STEP] Seal immutable vendor trees after all preparation probes
for %%D in (
    "tools\7zip"
    "cache\paddlex\official_models\PP-DocLayout-L"
    "cache\paddlex\official_models\PP-DocBlockLayout"
    "cache\paddlex\official_models\PP-OCRv6_medium_det"
    "cache\paddlex\official_models\eslav_PP-OCRv5_mobile_rec"
    "cache\paddlex\official_models\SLANet_plus"
    "services\ocr\font"
    "services\mysql"
    "services\silo"
    "services\valkey"
    "services\caddy"
    "runtime\llama"
) do (
    call :SealArtifactTree "%APP%\%%~D"
    if errorlevel 1 exit /b 1
)
call :SealArtifactTree "%APP%\services\elasticsearch" ".local-llm-artifact.txt;logs/"
if errorlevel 1 exit /b 1
exit /b 0

:SealArtifactTree
setlocal DisableDelayedExpansion
set "ARTIFACT_MARKER=%~1\.local-llm-artifact.txt"
set "ARTIFACT_NAME="
set "ARTIFACT_TREE_SHA256="
set "ARTIFACT_TREE_FILE_COUNT="
if not exist "%ARTIFACT_MARKER%" (
    echo [ERROR] Immutable artifact marker is missing: %ARTIFACT_MARKER%
    endlocal & exit /b 1
)
for /f "usebackq tokens=1,* delims==" %%A in ("%ARTIFACT_MARKER%") do if /i "%%A"=="artifact" set "ARTIFACT_NAME=%%B"
if not defined ARTIFACT_NAME (
    echo [ERROR] Immutable artifact marker has no artifact identity: %ARTIFACT_MARKER%
    endlocal & exit /b 1
)
set "ARTIFACT_TREE_EXCLUDES=%~2"
if not defined ARTIFACT_TREE_EXCLUDES set "ARTIFACT_TREE_EXCLUDES=.local-llm-artifact.txt"
call :ComputeTreeFingerprint "%~1" "%ARTIFACT_TREE_EXCLUDES%" ARTIFACT_TREE_SHA256 ARTIFACT_TREE_FILE_COUNT
if errorlevel 1 (endlocal & exit /b 1)
call :WriteArtifactMarker "%ARTIFACT_MARKER%" "%ARTIFACT_NAME%" "%ARTIFACT_TREE_SHA256%" "%ARTIFACT_TREE_FILE_COUNT%"
if errorlevel 1 (endlocal & exit /b 1)
call :TreeMatchesMarker "%~1" "%ARTIFACT_MARKER%" "%ARTIFACT_TREE_EXCLUDES%"
if errorlevel 1 (endlocal & exit /b 1)
endlocal & exit /b 0

:PackagePreparedOutput
echo [STEP] Create solid component archives for the offline installation stage
set "PACKAGE_STAGE=%WORK%\prepared-next-%RANDOM%-%RANDOM%"
set "BUNDLE_TARGET_DIR=%PACKAGE_STAGE%"
if exist "%PACKAGE_STAGE%" (
    echo [ERROR] Temporary package directory collision: %PACKAGE_STAGE%
    exit /b 1
)
call :MakeDirChecked "%PACKAGE_STAGE%"
if errorlevel 1 exit /b 1

copy /y "%SEVEN_ZIP_BOOTSTRAP%" "%PACKAGE_STAGE%\7zr.exe" >nul 2>&1
if errorlevel 1 exit /b 1
if not defined NOTEST_MODE (
    fc /b "%SEVEN_ZIP_BOOTSTRAP%" "%PACKAGE_STAGE%\7zr.exe" >nul 2>&1
    if errorlevel 1 exit /b 1
)
copy /y "%ROOT%\LOCAL-LLM.bat" "%PACKAGE_STAGE%\LOCAL-LLM.bat" >nul 2>&1
if errorlevel 1 exit /b 1
copy /y "%ROOT%\README.md" "%PACKAGE_STAGE%\README.md" >nul 2>&1
if errorlevel 1 exit /b 1
copy /y "%PROJECT%\local_llm_ctl.py" "%PACKAGE_STAGE%\local_llm_ctl.py" >nul 2>&1
if errorlevel 1 exit /b 1
if defined NOTEST_MODE (
    copy /y "%ROOT%\notest" "%PACKAGE_STAGE%\notest" >nul 2>&1
    if errorlevel 1 exit /b 1
) else (
    fc /b "%PROJECT%\local_llm_ctl.py" "%PACKAGE_STAGE%\local_llm_ctl.py" >nul 2>&1
    if errorlevel 1 exit /b 1
)

call :WriteBundleList "00-bootstrap-tools.lst" "app\tools\7zip-bootstrap"
if errorlevel 1 exit /b 1
call :AppendBundleList "00-bootstrap-tools.lst" "app\tools\7zip"
if errorlevel 1 exit /b 1
call :MakeBundle "00-bootstrap-tools.7z" "00-bootstrap-tools.lst"
if errorlevel 1 exit /b 1

call :WriteBundleList "10-python-rag-runtime.lst" "app\runtime\python-rag"
if errorlevel 1 exit /b 1
call :AppendBundleList "10-python-rag-runtime.lst" "app\runtime\shared-dll"
if errorlevel 1 exit /b 1
call :MakeBundle "10-python-rag-runtime.7z" "10-python-rag-runtime.lst"
if errorlevel 1 exit /b 1

call :WriteBundleList "20-python-ocr-gpu-runtime.lst" "app\runtime\python-ocr"
if errorlevel 1 exit /b 1
call :MakeBundle "20-python-ocr-gpu-runtime.7z" "20-python-ocr-gpu-runtime.lst"
if errorlevel 1 exit /b 1

call :WriteBundleList "21-paddle-models.lst" "app\cache\paddlex\official_models"
if errorlevel 1 exit /b 1
call :MakeBundle "21-paddle-models.7z" "21-paddle-models.lst"
if errorlevel 1 exit /b 1

call :WriteBundleList "30-ragflow-backend.lst" "app\ragflow"
if errorlevel 1 exit /b 1
call :AppendBundleList "30-ragflow-backend.lst" "app\data\nltk"
if errorlevel 1 exit /b 1
call :MakeBundle "30-ragflow-backend.7z" "30-ragflow-backend.lst"
if errorlevel 1 exit /b 1

call :WriteBundleList "31-ragflow-web-dist.lst" "app\web"
if errorlevel 1 exit /b 1
call :MakeBundle "31-ragflow-web-dist.7z" "31-ragflow-web-dist.lst"
if errorlevel 1 exit /b 1

call :WriteBundleList "40-services.lst" "app\services"
if errorlevel 1 exit /b 1
call :MakeBundle "40-services.7z" "40-services.lst"
if errorlevel 1 exit /b 1

call :WriteBundleList "41-config-seed.lst" "app\config"
if errorlevel 1 exit /b 1
call :MakeBundle "41-config-seed.7z" "41-config-seed.lst"
if errorlevel 1 exit /b 1

call :WriteBundleList "50-llama-vulkan-runtime.lst" "app\runtime\llama"
if errorlevel 1 exit /b 1
call :MakeBundle "50-llama-vulkan-runtime.7z" "50-llama-vulkan-runtime.lst"
if errorlevel 1 exit /b 1

if not defined NOTEST_MODE (
    call :VerifyPreparedStage
    if errorlevel 1 exit /b 1
)
call :WriteBuildRecords
if errorlevel 1 exit /b 1
if not defined NOTEST_MODE (
    call :ValidatePreparedSet "%PACKAGE_STAGE%"
    if errorlevel 1 (
        echo [ERROR] The unpublished prepared set failed its final completeness validation.
        exit /b 1
    )
)
call :PromotePreparedOutput
if errorlevel 1 exit /b 1
exit /b 0

:WriteBundleList
>"%WORK%\%~1" echo %~2
if errorlevel 1 exit /b 1
exit /b 0

:AppendBundleList
>>"%WORK%\%~1" echo %~2
if errorlevel 1 exit /b 1
exit /b 0

:MakeBundle
set "BUNDLE_OUT=%BUNDLE_TARGET_DIR%\%~1"
set "BUNDLE_LIST=%WORK%\%~2"
set "BUNDLE_LOG=%LOGS%\bundle-%~1.log"
if defined NOTEST_MODE set "BUNDLE_LOG=%LOGS%\bundle-%~1-notest-%RANDOM%-%RANDOM%.log"
call :BeginStepLog "%BUNDLE_LOG%" "local_llm bundle %~1 log"
if errorlevel 1 exit /b 1
pushd "%ROOT%" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Could not enter the project root while creating %~1.
    exit /b 1
)
"%SEVEN_ZIP%" a -t7z -mx=7 -ms=on -mmt=on "-xr!app\config\runtime\*" "-xr!app\ragflow\logs\*" "-x!app\ragflow\conf\local.service_conf.yaml" "%BUNDLE_OUT%" @"%BUNDLE_LIST%" >>"%BUNDLE_LOG%" 2>&1
set "BUNDLE_RC=%ERRORLEVEL%"
popd >nul
if not "%BUNDLE_RC%"=="0" (
    echo [ERROR] Could not create %~1. See %BUNDLE_LOG%
    call :PrintLogTail "%BUNDLE_LOG%"
    exit /b 1
)
if not exist "%BUNDLE_OUT%" exit /b 1
if not defined NOTEST_MODE (
    "%SEVEN_ZIP%" t "%BUNDLE_OUT%" >>"%BUNDLE_LOG%" 2>&1
    if errorlevel 1 (
        echo [ERROR] Integrity test failed for %~1. See %BUNDLE_LOG%
        call :PrintLogTail "%BUNDLE_LOG%"
        exit /b 1
    )
)
echo [OK] Created %~1
exit /b 0

:VerifyPreparedStage
echo [STEP] Rehydrate every bundle and verify the offline payload
set "REHYDRATE_ROOT=%WORK%\rehydrate-%RANDOM%-%RANDOM%"
set "REHYDRATE_APP=%REHYDRATE_ROOT%\app"
set "REHYDRATE_LOG=%LOGS%\prepared-rehydrate.log"
call :BeginStepLog "%REHYDRATE_LOG%" "local_llm prepared rehydrate log"
if errorlevel 1 exit /b 1
if exist "%REHYDRATE_ROOT%" (
    echo [ERROR] Temporary rehydrate directory collision: %REHYDRATE_ROOT%
    exit /b 1
)
call :MakeDirChecked "%REHYDRATE_ROOT%"
if errorlevel 1 exit /b 1
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
    "%SEVEN_ZIP%" x -y "-o%REHYDRATE_ROOT%" "%BUNDLE_TARGET_DIR%\%%~B" >>"%REHYDRATE_LOG%" 2>&1
    if errorlevel 1 goto :REHYDRATE_FAILED
)

for %%K in (
    "tools\7zip-bootstrap\7zr.exe"
    "tools\7zip\x64\7za.exe"
    "runtime\python-rag\python.exe"
    "runtime\python-ocr\python.exe"
    "runtime\shared-dll\vcomp140.dll"
    "services\elasticsearch\jdk\bin\java.exe"
    "cache\paddlex\official_models\PP-DocLayout-L\inference.json"
    "cache\paddlex\official_models\PP-DocLayout-L\inference.yml"
    "cache\paddlex\official_models\PP-DocLayout-L\inference.pdiparams"
    "cache\paddlex\official_models\PP-DocBlockLayout\inference.json"
    "cache\paddlex\official_models\PP-DocBlockLayout\inference.yml"
    "cache\paddlex\official_models\PP-DocBlockLayout\inference.pdiparams"
    "cache\paddlex\official_models\PP-OCRv6_medium_det\inference.json"
    "cache\paddlex\official_models\PP-OCRv6_medium_det\inference.yml"
    "cache\paddlex\official_models\PP-OCRv6_medium_det\inference.pdiparams"
    "cache\paddlex\official_models\eslav_PP-OCRv5_mobile_rec\inference.json"
    "cache\paddlex\official_models\eslav_PP-OCRv5_mobile_rec\inference.yml"
    "cache\paddlex\official_models\eslav_PP-OCRv5_mobile_rec\inference.pdiparams"
    "cache\paddlex\official_models\SLANet_plus\inference.json"
    "cache\paddlex\official_models\SLANet_plus\inference.yml"
    "cache\paddlex\official_models\SLANet_plus\inference.pdiparams"
    "ragflow\api\ragflow_server.py"
    "ragflow\tika-server-standard-3.3.0.jar"
    "ragflow\tika-server-standard-3.3.0.jar.md5"
    "ragflow\ragflow_deps\cl100k_base.tiktoken"
    "web\index.html"
    "services\mysql\bin\mysqld.exe"
    "services\elasticsearch\bin\elasticsearch.bat"
    "services\silo\silo.exe"
    "services\valkey\valkey-server.exe"
    "services\caddy\caddy.exe"
    "services\ocr\ocr_job_gateway.py"
    "services\ocr\font\ttf\DejaVuSans.ttf"
    "config\project\audit_portability.py"
    "config\project\local_llm_ctl.py"
    "config\project\local_llm_supervisor.py"
    "config\project\tree_fingerprint.ps1"
    "config\pp-structure-v3-8gb.yaml"
    "runtime\llama\llama-server.exe"
) do if not exist "%REHYDRATE_APP%\%%~K" (
    echo [ERROR] Rehydrated payload is missing app\%%~K
    goto :REHYDRATE_FAILED
)

fc /b "%BUNDLE_TARGET_DIR%\local_llm_ctl.py" "%REHYDRATE_APP%\config\project\local_llm_ctl.py" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Prepared control helper differs from the archived controller.
    goto :REHYDRATE_FAILED
)

call :VerifyRehydratedMutableSeals
if errorlevel 1 goto :REHYDRATE_FAILED

call :VerifyRehydratedImmutableSeals
if errorlevel 1 goto :REHYDRATE_FAILED
call :TreeMatchesMarker "%REHYDRATE_APP%\web" "%REHYDRATE_APP%\config\ragflow-web.ok" ""
if errorlevel 1 (
    echo [ERROR] Rehydrated web dist does not match its seal.
    goto :REHYDRATE_FAILED
)

call :VerifyRehydratedPayload "%REHYDRATE_APP%" "%REHYDRATE_LOG%" "%BUNDLE_TARGET_DIR%\PORTABILITY-AUDIT.json"
if errorlevel 1 goto :REHYDRATE_FAILED
call :VerifyRehydratedMutableSeals
if errorlevel 1 goto :REHYDRATE_FAILED
call :VerifyRehydratedImmutableSeals
if errorlevel 1 goto :REHYDRATE_FAILED
call :RemoveTreeChecked "%REHYDRATE_ROOT%"
if errorlevel 1 exit /b 1
echo [OK] All component archives rehydrate into a portable payload.
exit /b 0

:REHYDRATE_FAILED
echo [ERROR] Rehydrated bundle verification failed. See %REHYDRATE_LOG%
call :PrintLogTail "%REHYDRATE_LOG%"
call :RemoveTreeChecked "%REHYDRATE_ROOT%"
exit /b 1

:VerifyRehydratedMutableSeals
call :MutableTreeMatches "%REHYDRATE_APP%\runtime\python-rag" "%REHYDRATE_APP%\config\python-rag-tree.ok" "%RAG_PY_TREE_FINGERPRINT%"
if errorlevel 1 (
    echo [ERROR] Rehydrated RAGFlow Python tree does not match its final seal.
    exit /b 1
)
call :MutableTreeMatches "%REHYDRATE_APP%\runtime\python-ocr" "%REHYDRATE_APP%\config\python-ocr-tree.ok" "%OCR_PY_TREE_FINGERPRINT%"
if errorlevel 1 (
    echo [ERROR] Rehydrated OCR Python tree does not match its final seal.
    exit /b 1
)
call :MutableTreeMatches "%REHYDRATE_APP%\ragflow" "%REHYDRATE_APP%\config\ragflow-tree.ok" "%RAGFLOW_TREE_FINGERPRINT%" "%RAGFLOW_TREE_EXCLUDES%"
if errorlevel 1 (
    echo [ERROR] Rehydrated RAGFlow source tree does not match its final seal.
    exit /b 1
)
exit /b 0

:VerifyRehydratedImmutableSeals
for %%D in (
    "tools\7zip"
    "cache\paddlex\official_models\PP-DocLayout-L"
    "cache\paddlex\official_models\PP-DocBlockLayout"
    "cache\paddlex\official_models\PP-OCRv6_medium_det"
    "cache\paddlex\official_models\eslav_PP-OCRv5_mobile_rec"
    "cache\paddlex\official_models\SLANet_plus"
    "services\ocr\font"
    "services\mysql"
    "services\silo"
    "services\valkey"
    "services\caddy"
    "runtime\llama"
) do (
    call :TreeMatchesMarker "%REHYDRATE_APP%\%%~D" "%REHYDRATE_APP%\%%~D\.local-llm-artifact.txt" ".local-llm-artifact.txt"
    if errorlevel 1 (
        echo [ERROR] Rehydrated immutable tree does not match its seal: app\%%~D
        exit /b 1
    )
)
call :TreeMatchesMarker "%REHYDRATE_APP%\services\elasticsearch" "%REHYDRATE_APP%\services\elasticsearch\.local-llm-artifact.txt" ".local-llm-artifact.txt;logs/"
if errorlevel 1 (
    echo [ERROR] Rehydrated immutable tree does not match its seal: app\services\elasticsearch
    exit /b 1
)
exit /b 0

:VerifyRehydratedPayload
setlocal DisableDelayedExpansion
set "VERIFY_APP=%~1"
set "VERIFY_LOG=%~2"
set "VERIFY_AUDIT_RECORD=%~3"
set "HOME=%~1\data\profile"
set "USERPROFILE=%~1\data\profile"
set "APPDATA=%~1\data\profile\AppData\Roaming"
set "LOCALAPPDATA=%~1\data\profile\AppData\Local"
set "TEMP=%~1\temp\verify"
set "TMP=%~1\temp\verify"
set "XDG_CACHE_HOME=%~1\cache"
set "HF_HOME=%~1\cache\huggingface"
set "HUGGINGFACE_HUB_CACHE=%~1\cache\huggingface\hub"
set "TRANSFORMERS_CACHE=%~1\cache\huggingface\transformers"
set "PADDLE_HOME=%~1\cache\paddle"
set "PADDLE_PDX_CACHE_HOME=%~1\cache\paddlex"
set "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=1"
set "PADDLE_PDX_DISABLE_DEVICE_FALLBACK=1"
set "NLTK_DATA=%~1\data\nltk"
set "TIKTOKEN_CACHE_DIR=%~1\ragflow"
set "PYTHONPYCACHEPREFIX=%~1\cache\python-bytecode"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONNOUSERSITE=1"
set "PYTHONHOME="
set "PYTHONPATH="
set "VIRTUAL_ENV="
set "CONDA_PREFIX="
set "HF_HUB_OFFLINE=1"
set "TRANSFORMERS_OFFLINE=1"
set "VERIFY_RAGFLOW_URI=%~1\ragflow"
set "VERIFY_RAGFLOW_URI=%VERIFY_RAGFLOW_URI:\=/%"
set "TIKA_SERVER_JAR=file:///%VERIFY_RAGFLOW_URI%/tika-server-standard-3.3.0.jar"
set "PATH=%~1\runtime\shared-dll;%~1\runtime\python-rag;%~1\runtime\python-ocr;%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem"
call :MakeDirChecked "%TEMP%"
if errorlevel 1 (endlocal & exit /b 1)
call :ProbeRehydratedBinaries "%~1" "%~2"
if errorlevel 1 (endlocal & exit /b 1)

"%~1\runtime\python-rag\python.exe" -c "import sys; print(sys.version); import cv2,elasticsearch,quart,valkey" >>"%~2" 2>&1
if errorlevel 1 (endlocal & exit /b 1)
"%~1\runtime\python-rag\python.exe" "%~1\config\project\prepare_ragflow_assets.py" --ragflow-dir "%~1\ragflow" --nltk-dir "%~1\data\nltk" --record "%~1\config\ragflow-assets.json" --verify-only >>"%~2" 2>&1
if errorlevel 1 (endlocal & exit /b 1)
"%~1\runtime\python-rag\python.exe" "%~1\config\project\verify_ragflow_runtime.py" --ragflow-dir "%~1\ragflow" >>"%~2" 2>&1
if errorlevel 1 (endlocal & exit /b 1)
"%~1\runtime\python-ocr\python.exe" -c "import sys,paddle,paddleocr,paddlex; print(sys.version); print('paddle='+paddle.__version__); print('paddleocr='+paddleocr.__version__); print('paddlex='+paddlex.__version__)" >>"%~2" 2>&1
if errorlevel 1 (endlocal & exit /b 1)
"%~1\runtime\python-ocr\python.exe" "%~1\services\ocr\test_ocr_job_gateway_contract.py" >>"%~2" 2>&1
if errorlevel 1 (endlocal & exit /b 1)
call :RemoveTreeChecked "%~1\cache\python-bytecode"
if errorlevel 1 (endlocal & exit /b 1)
"%~1\runtime\python-rag\python.exe" "%~1\config\project\audit_portability.py" --root "%~1" --build-root "%ROOT%" --record "%~3" >>"%~2" 2>&1
if errorlevel 1 (endlocal & exit /b 1)
endlocal & exit /b 0

:ProbeRehydratedBinaries
setlocal DisableDelayedExpansion
set "REPROBE_APP=%~1"
set "REPROBE_LOG=%~2"
set "REPROBE_ONE=%TEMP%\binary-probe.txt"
set "ES_JAVA_HOME=%~1\services\elasticsearch\jdk"
set "JAVA_HOME=%~1\services\elasticsearch\jdk"
set "JAVA_TOOL_OPTIONS="
set "_JAVA_OPTIONS="
set "JDK_JAVA_OPTIONS="
set "CLASSPATH="
set "ES_JAVA_OPTS=-Xms64m -Xmx64m"
set "PATH=%~1\runtime\shared-dll;%~1\services\elasticsearch\jdk\bin;%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem"
pushd "%TEMP%" >nul 2>&1
if errorlevel 1 goto :REPROBE_FAILED_NO_PUSHD

"%~1\tools\7zip-bootstrap\7zr.exe" i >"%REPROBE_ONE%" 2>&1
if errorlevel 1 goto :REPROBE_FAILED
type "%REPROBE_ONE%" >>"%REPROBE_LOG%"
"%~1\tools\7zip\x64\7za.exe" i >"%REPROBE_ONE%" 2>&1
if errorlevel 1 goto :REPROBE_FAILED
type "%REPROBE_ONE%" >>"%REPROBE_LOG%"
"%~1\services\elasticsearch\jdk\bin\java.exe" -version >"%REPROBE_ONE%" 2>&1
if errorlevel 1 goto :REPROBE_FAILED
type "%REPROBE_ONE%" >>"%REPROBE_LOG%"
"%~1\services\mysql\bin\mysqld.exe" --version >"%REPROBE_ONE%" 2>&1
if errorlevel 1 goto :REPROBE_FAILED
findstr /c:"%MYSQL_VERSION%" "%REPROBE_ONE%" >nul 2>&1
if errorlevel 1 goto :REPROBE_FAILED
type "%REPROBE_ONE%" >>"%REPROBE_LOG%"
call "%~1\services\elasticsearch\bin\elasticsearch.bat" -V >"%REPROBE_ONE%" 2>&1
if errorlevel 1 goto :REPROBE_FAILED
findstr /c:"%ELASTIC_VERSION%" "%REPROBE_ONE%" >nul 2>&1
if errorlevel 1 goto :REPROBE_FAILED
type "%REPROBE_ONE%" >>"%REPROBE_LOG%"
"%~1\services\valkey\valkey-server.exe" --version >"%REPROBE_ONE%" 2>&1
if errorlevel 1 goto :REPROBE_FAILED
findstr /c:"%VALKEY_VERSION%" "%REPROBE_ONE%" >nul 2>&1
if errorlevel 1 goto :REPROBE_FAILED
type "%REPROBE_ONE%" >>"%REPROBE_LOG%"
"%~1\services\caddy\caddy.exe" version >"%REPROBE_ONE%" 2>&1
if errorlevel 1 goto :REPROBE_FAILED
findstr /c:"%CADDY_VERSION%" "%REPROBE_ONE%" >nul 2>&1
if errorlevel 1 goto :REPROBE_FAILED
type "%REPROBE_ONE%" >>"%REPROBE_LOG%"
"%~1\runtime\llama\llama-server.exe" --version >"%REPROBE_ONE%" 2>&1
if errorlevel 1 goto :REPROBE_FAILED
findstr /c:"%LLAMA_BUILD:~1%" "%REPROBE_ONE%" >nul 2>&1
if errorlevel 1 goto :REPROBE_FAILED
type "%REPROBE_ONE%" >>"%REPROBE_LOG%"
"%~1\services\silo\silo.exe" --version >"%REPROBE_ONE%" 2>&1
if errorlevel 1 goto :REPROBE_FAILED
type "%REPROBE_ONE%" >>"%REPROBE_LOG%"

if exist "%REPROBE_ONE%" del /f /q "%REPROBE_ONE%" >nul 2>&1
popd >nul
endlocal & exit /b 0

:REPROBE_FAILED
if exist "%REPROBE_ONE%" type "%REPROBE_ONE%" >>"%REPROBE_LOG%"
if exist "%REPROBE_ONE%" del /f /q "%REPROBE_ONE%" >nul 2>&1
popd >nul
:REPROBE_FAILED_NO_PUSHD
endlocal & exit /b 1

:WriteBuildRecords
>"%BUNDLE_TARGET_DIR%\build-info.txt" (
    echo local_llm_prepare_version=%PROJECT_VERSION%
    echo local_llm_control_version=%PROJECT_VERSION%
    echo prepared_date=%DATE%
    echo prepared_time=%TIME%
    echo target=windows-x86_64-portable
    echo ragflow=%RAGFLOW_VERSION%
    echo python_rag=%PY_RAG_VERSION%
    echo python_ocr=%PY_OCR_VERSION%
    echo paddlepaddle_gpu=%PADDLE_VERSION%-cu118-sm61
    echo paddleocr=%PADDLEOCR_VERSION%
    echo paddlex=%PADDLEX_VERSION%
    echo numpy_windows_override=%NUMPY_VERSION%
    echo xgboost_windows_override=%XGBOOST_VERSION%
    echo scikit_learn_windows_addition=%SCIKIT_LEARN_VERSION%
    echo datrie_windows_wheel=%DATRIE_VERSION%
    echo graphrag_backend=graspologic-native-%GRASPOLOGIC_NATIVE_VERSION%-audited-adapter
    echo vc_runtime=%VC_REDIST_VERSION%-app-local
    echo node=%NODE_VERSION%
    echo portable_git=%MINGIT_GIT_VERSION%-process-local-path
    echo mysql=%MYSQL_VERSION%
    echo elasticsearch=%ELASTIC_VERSION%
    echo valkey_windows=%VALKEY_VERSION%-community-unsupported
    echo llama_cpp=%LLAMA_BUILD%-vulkan
    echo ocr_profile=PP-DocLayout-L-PP-OCRv6-medium-det-eslav-PP-OCRv5-rec-SLANet-plus-table-gpu-only
    echo target_gpu_e2e=%GPU_VALIDATION%
    if defined NOTEST_MODE echo validation=skipped-notest
    echo graphrag=enabled
    echo embeddings=external-llama-cpp-server
)
if errorlevel 1 exit /b 1
REM The success marker is deliberately written after every bundle and record.
call :WriteAtomicLine "%BUNDLE_TARGET_DIR%\prepared.ok" "prepared=1"
if errorlevel 1 exit /b 1
if not exist "%BUNDLE_TARGET_DIR%\prepared.ok" exit /b 1
exit /b 0

:PromotePreparedOutput
if exist "%PREPARED_BACKUP%" (
    if defined NOTEST_MODE (
        call :MoveNotestBackupAside "%PREPARED_BACKUP%"
        if errorlevel 1 exit /b 1
    ) else (
        echo [ERROR] Stale prepared backup exists: %PREPARED_BACKUP%
        echo [INFO] Rerun the script so startup recovery can resolve it.
        exit /b 1
    )
)

if exist "%PREPARED%" (
    if not defined NOTEST_MODE (
        call :ValidatePreparedSet "%PREPARED%"
        if errorlevel 1 (
            echo [ERROR] Existing prepared output changed after startup validation.
            echo [ERROR] It was preserved; refusing to replace or delete it.
            exit /b 1
        )
    )
    move "%PREPARED%" "%PREPARED_BACKUP%" >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Could not preserve the previous prepared bundle set.
        exit /b 1
    )
)

move "%PACKAGE_STAGE%" "%PREPARED%" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Could not promote the new prepared bundle set.
    if exist "%PREPARED_BACKUP%" (
        move "%PREPARED_BACKUP%" "%PREPARED%" >nul 2>&1
        if errorlevel 1 (
            echo [ERROR] Rollback also failed. Previous output remains at:
            echo [ERROR]   %PREPARED_BACKUP%
        )
    )
    exit /b 1
)
set "PACKAGE_STAGE="

if exist "%PREPARED_BACKUP%" (
    if defined NOTEST_MODE (
        call :MoveNotestBackupAside "%PREPARED_BACKUP%"
        if errorlevel 1 exit /b 1
    ) else (
        rmdir /s /q "%PREPARED_BACKUP%" >nul 2>&1
        if exist "%PREPARED_BACKUP%" (
            echo [ERROR] New output is valid, but the previous backup could not be removed:
            echo [ERROR]   %PREPARED_BACKUP%
            exit /b 1
        )
    )
)
if defined NOTEST_MODE (
    echo [OK] Atomically replaced _src\prepared with the unverified notest bundle set.
) else (
    echo [OK] Atomically replaced _src\prepared with the verified bundle set.
)
exit /b 0

:MoveNotestBackupAside
setlocal DisableDelayedExpansion
set "NOTEST_BACKUP_TARGET=%WORK%\prepared-previous-notest-%RANDOM%-%RANDOM%"
if exist "%NOTEST_BACKUP_TARGET%" (
    echo [ERROR] Random preserved-backup path already exists: %NOTEST_BACKUP_TARGET%
    endlocal & exit /b 1
)
move "%~1" "%NOTEST_BACKUP_TARGET%" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Could not move the previous prepared set aside: %~1
    endlocal & exit /b 1
)
echo [WARN] Previous prepared set was preserved without validation or deletion:
echo [WARN]   %NOTEST_BACKUP_TARGET%
endlocal & exit /b 0

:FileSha256
setlocal
set "FILE_HASH_TARGET=%~1"
set "FILE_HASH_OUTPUT=%WORK%\file-hash-%RANDOM%-%RANDOM%.txt"
set "FILE_HASH_VALUE="
if not exist "%~1" (
    endlocal & exit /b 1
)
"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; (Get-FileHash -LiteralPath $env:FILE_HASH_TARGET -Algorithm SHA256).Hash.ToLowerInvariant() | Set-Content -LiteralPath $env:FILE_HASH_OUTPUT -Encoding ascii" >nul 2>&1
if errorlevel 1 (
    if exist "%FILE_HASH_OUTPUT%" del /f /q "%FILE_HASH_OUTPUT%" >nul 2>&1
    endlocal & exit /b 1
)
for /f "usebackq delims=" %%H in ("%FILE_HASH_OUTPUT%") do set "FILE_HASH_VALUE=%%H"
if exist "%FILE_HASH_OUTPUT%" del /f /q "%FILE_HASH_OUTPUT%" >nul 2>&1
if not defined FILE_HASH_VALUE (
    endlocal & exit /b 1
)
endlocal & set "%~2=%FILE_HASH_VALUE%" & exit /b 0

:MarkerMatches
if not exist "%~1" exit /b 1
findstr /x /l /c:"fingerprint=%~2" "%~1" >nul 2>&1
if errorlevel 1 exit /b 1
exit /b 0

:WriteFingerprintMarker
call :WriteAtomicLine "%~1" "fingerprint=%~2"
exit /b %ERRORLEVEL%

:WriteSealedFingerprintMarker
setlocal
set "SEALED_MARKER_TEMP=%~1.tmp-%RANDOM%-%RANDOM%"
if exist "%SEALED_MARKER_TEMP%" (
    endlocal & exit /b 1
)
>"%SEALED_MARKER_TEMP%" (
    echo fingerprint=%~2
    echo tree_sha256=%~3
    echo tree_file_count=%~4
    if not "%~5"=="" echo tree_manifest=%~5
)
if errorlevel 1 (
    if exist "%SEALED_MARKER_TEMP%" del /f /q "%SEALED_MARKER_TEMP%" >nul 2>&1
    endlocal & exit /b 1
)
move /y "%SEALED_MARKER_TEMP%" "%~1" >nul 2>&1
if errorlevel 1 (
    if exist "%SEALED_MARKER_TEMP%" del /f /q "%SEALED_MARKER_TEMP%" >nul 2>&1
    endlocal & exit /b 1
)
if not exist "%~1" (
    endlocal & exit /b 1
)
endlocal & exit /b 0

:WriteAtomicLine
setlocal
set "ATOMIC_LINE_TEMP=%~1.tmp-%RANDOM%-%RANDOM%"
if exist "%ATOMIC_LINE_TEMP%" (
    endlocal & exit /b 1
)
>"%ATOMIC_LINE_TEMP%" echo(%~2
if errorlevel 1 (
    if exist "%ATOMIC_LINE_TEMP%" del /f /q "%ATOMIC_LINE_TEMP%" >nul 2>&1
    endlocal & exit /b 1
)
if not exist "%ATOMIC_LINE_TEMP%" (
    endlocal & exit /b 1
)
move /y "%ATOMIC_LINE_TEMP%" "%~1" >nul 2>&1
if errorlevel 1 (
    if exist "%ATOMIC_LINE_TEMP%" del /f /q "%ATOMIC_LINE_TEMP%" >nul 2>&1
    endlocal & exit /b 1
)
if not exist "%~1" (
    endlocal & exit /b 1
)
endlocal & exit /b 0

:CopyTree
if not exist "%~1\." exit /b 1
call :MakeDirChecked "%~2"
if errorlevel 1 exit /b 1
robocopy "%~1" "%~2" /E /COPY:DAT /DCOPY:DAT /R:2 /W:1 /NFL /NDL /NJH /NJS /NP >nul
set "COPYTREE_RC=%ERRORLEVEL%"
if %COPYTREE_RC% GEQ 8 exit /b 1
exit /b 0

:IsRegularFile
setlocal
if "%~1"=="" (endlocal & exit /b 1)
if not exist "%~1" (endlocal & exit /b 1)
set "REGULAR_FILE_ATTRIBUTES="
for %%I in ("%~1") do set "REGULAR_FILE_ATTRIBUTES=%%~aI"
if not defined REGULAR_FILE_ATTRIBUTES (endlocal & exit /b 1)
if /i "%REGULAR_FILE_ATTRIBUTES:~0,1%"=="d" (endlocal & exit /b 1)
if /i not "%REGULAR_FILE_ATTRIBUTES:l=%"=="%REGULAR_FILE_ATTRIBUTES%" (endlocal & exit /b 1)
endlocal & exit /b 0

:RemoveRegularFileChecked
setlocal
if "%~1"=="" (endlocal & exit /b 1)
if not exist "%~1" (endlocal & exit /b 0)
call :IsRegularFile "%~1"
if errorlevel 1 (
    echo [ERROR] Refusing to remove a non-regular file: %~1
    endlocal & exit /b 1
)
del /f /q "%~1" >nul 2>&1
if exist "%~1" (
    echo [ERROR] Could not remove file: %~1
    endlocal & exit /b 1
)
endlocal & exit /b 0

:DirectoryIsEmpty
setlocal
if "%~1"=="" (endlocal & exit /b 1)
if not exist "%~1\." (endlocal & exit /b 1)
for /f "delims=" %%I in ('dir /a /b "%~1" 2^>nul') do (
    endlocal & exit /b 1
)
endlocal & exit /b 0

:MakeDirChecked
setlocal
if "%~1"=="" (endlocal & exit /b 1)
if exist "%~1" if not exist "%~1\." (
    echo [ERROR] A directory path is occupied by a file: %~1
    endlocal & exit /b 1
)
if not exist "%~1\." mkdir "%~1" >nul 2>&1
if not exist "%~1\." (
    echo [ERROR] Could not create directory: %~1
    endlocal & exit /b 1
)
set "DIRECTORY_ATTRIBUTES="
for %%I in ("%~1") do set "DIRECTORY_ATTRIBUTES=%%~aI"
if not defined DIRECTORY_ATTRIBUTES (
    echo [ERROR] Could not inspect directory attributes: %~1
    endlocal & exit /b 1
)
if /i not "%DIRECTORY_ATTRIBUTES:l=%"=="%DIRECTORY_ATTRIBUTES%" (
    echo [ERROR] Refusing a symlink or junction directory: %~1
    endlocal & exit /b 1
)
endlocal & exit /b 0

:RemoveTreeChecked
setlocal
if "%~1"=="" (endlocal & exit /b 1)
if exist "%~1" if not exist "%~1\." (
    echo [ERROR] Expected a directory but found another object: %~1
    endlocal & exit /b 1
)
if not exist "%~1\." (endlocal & exit /b 0)
set "DIRECTORY_ATTRIBUTES="
for %%I in ("%~1") do set "DIRECTORY_ATTRIBUTES=%%~aI"
if not defined DIRECTORY_ATTRIBUTES (
    echo [ERROR] Could not inspect directory attributes before removal: %~1
    endlocal & exit /b 1
)
if /i not "%DIRECTORY_ATTRIBUTES:l=%"=="%DIRECTORY_ATTRIBUTES%" (
    echo [ERROR] Refusing to remove through a symlink or junction: %~1
    endlocal & exit /b 1
)
rmdir /s /q "%~1" >nul 2>&1
if exist "%~1" (
    timeout /t 2 /nobreak >nul 2>&1
    rmdir /s /q "%~1" >nul 2>&1
)
if exist "%~1" (
    "%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -File "%PROJECT%\remove_tree.ps1" -Root "%~1" >nul 2>&1
)
if exist "%~1" (
    echo [ERROR] Could not remove directory: %~1
    endlocal & exit /b 1
)
endlocal & exit /b 0

REM ============================================================================
REM CLEAN EXIT
REM ============================================================================

:SCRIPT_END
if not defined SCRIPT_RC set "SCRIPT_RC=%ERRORLEVEL%"
if not "%SCRIPT_RC%"=="0" (
    echo.
    echo [ERROR] ONLINE PREPARATION FAILED with exit code %SCRIPT_RC%.
    if defined LOGS echo [INFO] Logs directory: %LOGS%
)
if defined PACKAGE_STAGE if exist "%PACKAGE_STAGE%" (
    echo [INFO] Removing unpublished package staging tree: %PACKAGE_STAGE%
    call :RemoveTreeChecked "%PACKAGE_STAGE%"
    if errorlevel 1 set "SCRIPT_RC=1"
)
if defined LOCK_HELD if exist "%LOCK_DIR%" (
    rmdir "%LOCK_DIR%" >nul 2>&1
    if exist "%LOCK_DIR%" (
        echo [ERROR] Could not release preparation lock: %LOCK_DIR%
        if "%SCRIPT_RC%"=="0" set "SCRIPT_RC=1"
    )
)
endlocal & exit /b %SCRIPT_RC%
