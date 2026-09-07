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

set "PROJECT_VERSION=2026.09.07.3"
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

call :RecoverPreparedOutput
if errorlevel 1 exit /b 1

call :CopyProjectInputs
if errorlevel 1 exit /b 1

call :ValidateOrResetMutableTrees
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

call :SealMutableRuntimeTrees
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
set "APP=%ROOT%\app"
set "POWERSHELL_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%POWERSHELL_EXE%" (
    echo [ERROR] Windows PowerShell 5.1 is required: %POWERSHELL_EXE%
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
call :AuditExistingManagedPaths
if errorlevel 1 exit /b 1
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
set "HASH_MANIFEST=%PROJECT%\artifacts.sha256"

for %%D in (
    "%SRC%"
    "%PROJECT%"
    "%LOGS%"
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
    echo [WARN] Recovering the previous SHA-256-verified prepared bundle set.
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
"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $root=Get-Item -LiteralPath $env:VALIDATE_PREPARED_DIR -Force; if(-not $root.PSIsContainer -or (($root.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)){throw 'Invalid prepared root'}; $payload=@('00-bootstrap-tools.7z','10-python-rag-runtime.7z','20-python-ocr-gpu-runtime.7z','21-paddle-models.7z','30-ragflow-backend.7z','31-ragflow-web-dist.7z','40-services.7z','41-config-seed.7z','50-llama-vulkan-runtime.7z','7zr.exe','LOCAL-LLM.bat','README.md'); $metadata=@('PORTABILITY-AUDIT.json','SHA256SUMS.txt','SOURCE-SHA256SUMS.txt','build-info.txt','prepared.ok'); $allowed=@{}; foreach($name in $payload+$metadata){$allowed[$name.ToLowerInvariant()]=$true}; foreach($item in Get-ChildItem -LiteralPath $root.FullName -Force){if(($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $item.PSIsContainer -or -not $allowed.ContainsKey($item.Name.ToLowerInvariant())){throw ('Unexpected prepared entry: '+$item.Name)}}; if(@(Get-ChildItem -LiteralPath $root.FullName -Force).Count -ne $allowed.Count){throw 'Prepared file count mismatch'}; if((Get-Content -LiteralPath (Join-Path $root.FullName 'prepared.ok') -Raw).Trim() -ne 'prepared=1'){throw 'Invalid prepared marker'}; $lines=@(Get-Content -LiteralPath (Join-Path $root.FullName 'SHA256SUMS.txt')); if($lines.Count -ne $payload.Count){throw 'Payload hash count mismatch'}; $seen=@{}; foreach($line in $lines){if($line -notmatch '^([0-9a-fA-F]{64})  ([^\\/]+)$'){throw ('Malformed payload hash: '+$line)}; $expected=$matches[1].ToLowerInvariant(); $name=$matches[2]; $key=$name.ToLowerInvariant(); if($seen.ContainsKey($key) -or -not ($payload -contains $name)){throw ('Unexpected or duplicate payload hash: '+$name)}; $seen[$key]=$true; $path=Join-Path $root.FullName $name; if(-not (Test-Path -LiteralPath $path -PathType Leaf)){throw ('Missing payload: '+$name)}; if((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected){throw ('Payload SHA256 mismatch: '+$name)}}; if($seen.Count -ne $payload.Count){throw 'Incomplete payload hash set'}; $sourceLines=@(Get-Content -LiteralPath (Join-Path $root.FullName 'SOURCE-SHA256SUMS.txt')); if($sourceLines.Count -ne 23){throw 'Source hash count mismatch'}; $sourceSeen=@{}; foreach($line in $sourceLines){if($line -notmatch '^([0-9a-fA-F]{64})  ([^\\/]+)$'){throw ('Malformed source hash: '+$line)}; $name=$matches[2].ToLowerInvariant(); if($sourceSeen.ContainsKey($name)){throw ('Duplicate source hash: '+$name)}; $sourceSeen[$name]=$true}; $audit=Get-Content -LiteralPath (Join-Path $root.FullName 'PORTABILITY-AUDIT.json') -Raw | ConvertFrom-Json; if($audit.passed -ne $true){throw 'Portability audit did not pass'}; if(-not (Select-String -LiteralPath (Join-Path $root.FullName 'build-info.txt') -Pattern '^local_llm_prepare_version=' -Quiet)){throw 'Missing build version'}" >nul 2>&1
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
set "PIP_CACHE_DIR=%APP%\cache\pip"
set "UV_CACHE_DIR=%APP%\cache\uv"
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
set "OCR_WHEELHOUSE=%APP%\build\wheelhouse\ocr"
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
    "artifacts.sha256"
    "prepare_ragflow_assets.py"
    "prepare_ragflow_windows.py"
    "graphrag_native_adapter.py"
    "verify_ragflow_runtime.py"
    "sanitize_python_runtime.py"
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

:ValidateOrResetMutableTrees
set "RAG_RUNTIME_RESET="
set "RAG_SOURCE_RESET="
set "OCR_RUNTIME_RESET="

call :MutableTreeMatches "%RAG_PY_DIR%" "%RAG_PY_TREE_MARKER%" "%RAG_PY_TREE_FINGERPRINT%"
if errorlevel 1 (
    if exist "%RAG_PY_DIR%\." echo [WARN] RAGFlow Python tree is unsealed or changed; restoring its clean base.
    call :RemoveTreeChecked "%RAG_PY_DIR%"
    if errorlevel 1 exit /b 1
    set "RAG_RUNTIME_RESET=1"
)
call :MutableTreeMatches "%RAGFLOW_DIR%" "%RAGFLOW_TREE_MARKER%" "%RAGFLOW_TREE_FINGERPRINT%" "%RAGFLOW_TREE_EXCLUDES%"
if errorlevel 1 (
    if exist "%RAGFLOW_DIR%\." echo [WARN] RAGFlow source tree is unsealed or changed; restoring its clean base.
    call :RemoveTreeChecked "%RAGFLOW_DIR%"
    if errorlevel 1 exit /b 1
    set "RAG_SOURCE_RESET=1"
)
call :MutableTreeMatches "%OCR_PY_DIR%" "%OCR_PY_TREE_MARKER%" "%OCR_PY_TREE_FINGERPRINT%"
if errorlevel 1 (
    if exist "%OCR_PY_DIR%\." echo [WARN] OCR Python tree is unsealed or changed; restoring its clean base.
    call :RemoveTreeChecked "%OCR_PY_DIR%"
    if errorlevel 1 exit /b 1
    set "OCR_RUNTIME_RESET=1"
)

if defined RAG_RUNTIME_RESET if exist "%RAG_PY_TREE_MARKER%" del /f /q "%RAG_PY_TREE_MARKER%" >nul 2>&1
if defined RAG_SOURCE_RESET if exist "%RAGFLOW_TREE_MARKER%" del /f /q "%RAGFLOW_TREE_MARKER%" >nul 2>&1
if defined OCR_RUNTIME_RESET if exist "%OCR_PY_TREE_MARKER%" del /f /q "%OCR_PY_TREE_MARKER%" >nul 2>&1
if defined RAG_RUNTIME_RESET if exist "%APP%\config\ragflow-python.ok" del /f /q "%APP%\config\ragflow-python.ok" >nul 2>&1
if defined RAG_SOURCE_RESET if exist "%APP%\config\ragflow-python.ok" del /f /q "%APP%\config\ragflow-python.ok" >nul 2>&1
if defined RAG_SOURCE_RESET if exist "%APP%\config\ragflow-windows-compat.json" del /f /q "%APP%\config\ragflow-windows-compat.json" >nul 2>&1
if defined OCR_RUNTIME_RESET if exist "%APP%\config\ocr-python.ok" del /f /q "%APP%\config\ocr-python.ok" >nul 2>&1

if defined RAG_RUNTIME_RESET if exist "%RAG_PY_TREE_MARKER%" exit /b 1
if defined RAG_SOURCE_RESET if exist "%RAGFLOW_TREE_MARKER%" exit /b 1
if defined OCR_RUNTIME_RESET if exist "%OCR_PY_TREE_MARKER%" exit /b 1
if defined RAG_RUNTIME_RESET if exist "%APP%\config\ragflow-python.ok" exit /b 1
if defined RAG_SOURCE_RESET if exist "%APP%\config\ragflow-python.ok" exit /b 1
if defined RAG_SOURCE_RESET if exist "%APP%\config\ragflow-windows-compat.json" exit /b 1
if defined OCR_RUNTIME_RESET if exist "%APP%\config\ocr-python.ok" exit /b 1
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

call :VerifyArtifactHash "!ART_SOURCE!" "!ART_NAME!"
if errorlevel 2 (
    echo Replace the incorrect file, then press any key to check it again...
    pause >nul
    goto :ENSURE_SOURCE_WAIT
)
if errorlevel 1 (
    endlocal
    exit /b 1
)
echo [OK] Source file is present: !ART_NAME!

set "ART_KIND=file"
if /i "!ART_NAME:~-4!"==".zip" set "ART_KIND=archive"
if /i "!ART_NAME:~-3!"==".7z" set "ART_KIND=archive"
if /i "!ART_NAME:~-4!"==".tar" set "ART_KIND=archive"
if /i "!ART_NAME:~-7!"==".tar.gz" set "ART_KIND=archive"
if /i "!ART_NAME:~-4!"==".tgz" set "ART_KIND=archive"
set "ART_SEAL_TREE=0"
if /i "!ART_KIND!"=="archive" set "ART_SEAL_TREE=1"
REM These three archive destinations are intentionally modified later.
if /i "!DEST_REL!"=="runtime\python-rag" set "ART_SEAL_TREE=0"
if /i "!DEST_REL!"=="runtime\python-ocr" set "ART_SEAL_TREE=0"
if /i "!DEST_REL!"=="ragflow" set "ART_SEAL_TREE=0"

set "ART_MARKER=!ART_DEST!\.local-llm-artifact.txt"
set "MARKER_ARTIFACT="
set "MARKER_SHA256="
set "MARKER_TREE_SHA256="
set "MARKER_TREE_FILE_COUNT="
set "ART_HAVE_EXPECTED_TREE=0"
call :MakeDirChecked "!ART_DEST!"
if errorlevel 1 goto :ENSURE_MANUAL_WAIT

if exist "!ART_MARKER!" (
    for /f "usebackq tokens=1,* delims==" %%A in ("!ART_MARKER!") do (
        if /i "%%A"=="artifact" set "MARKER_ARTIFACT=%%B"
        if /i "%%A"=="sha256" set "MARKER_SHA256=%%B"
        if /i "%%A"=="tree_sha256" set "MARKER_TREE_SHA256=%%B"
        if /i "%%A"=="tree_file_count" set "MARKER_TREE_FILE_COUNT=%%B"
    )
)
if /i "!MARKER_ARTIFACT!"=="!ART_NAME!" if /i "!MARKER_SHA256!"=="!VERIFIED_ARTIFACT_HASH!" if defined MARKER_TREE_SHA256 if defined MARKER_TREE_FILE_COUNT set "ART_HAVE_EXPECTED_TREE=1"

if exist "!ART_KEY!" if /i "!MARKER_ARTIFACT!"=="!ART_NAME!" if /i "!MARKER_SHA256!"=="!VERIFIED_ARTIFACT_HASH!" (
    if /i "!ART_KIND!"=="file" (
        fc /b "!ART_SOURCE!" "!ART_KEY!" >nul 2>&1
        if not errorlevel 1 (
            call :DiscardStaleArtifactBackup
            if errorlevel 1 (endlocal & exit /b 1)
            echo [OK] !ART_NAME! -- verified source and destination already match
            endlocal & exit /b 0
        )
    ) else (
        if "!ART_SEAL_TREE!"=="1" (
            call :TreeMatchesMarker "!ART_DEST!" "!ART_MARKER!" ".local-llm-artifact.txt"
            if not errorlevel 1 (
                call :DiscardStaleArtifactBackup
                if errorlevel 1 (endlocal & exit /b 1)
                echo [OK] !ART_NAME! -- verified source and sealed destination match
                endlocal & exit /b 0
            )
            echo [WARN] Immutable destination tree changed; restoring !ART_NAME!.
        ) else (
            call :DiscardStaleArtifactBackup
            if errorlevel 1 (endlocal & exit /b 1)
            echo [OK] !ART_NAME! -- verified source and key file already exist
            endlocal & exit /b 0
        )
    )
)
if exist "!ART_KEY!" echo [INFO] Replacing stale or untracked destination for !ART_NAME!.

REM Never let a failed upgrade re-label an older destination as the new
REM artifact. Preserve the old generated tree before attempting replacement;
REM manual acceptance starts from an empty destination.
if exist "!ART_STALE_BACKUP!" (
    echo [ERROR] A preserved destination from an interrupted attempt exists:
    echo [ERROR]   !ART_STALE_BACKUP!
    echo [INFO] Inspect and move it before retrying this artifact.
    endlocal & exit /b 1
)
move "!ART_DEST!" "!ART_STALE_BACKUP!" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Could not preserve the previous destination: !ART_DEST!
    endlocal & exit /b 1
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
    call :WriteArtifactMarker "!ART_MARKER!" "!ART_NAME!" "!VERIFIED_ARTIFACT_HASH!" "" ""
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
if exist "!ART_KEY!" (
    set "ART_TREE_SHA256="
    set "ART_TREE_FILE_COUNT="
    if "!ART_SEAL_TREE!"=="1" (
        call :ComputeTreeFingerprint "!ART_DEST!" ".local-llm-artifact.txt" ART_TREE_SHA256 ART_TREE_FILE_COUNT
        if errorlevel 1 goto :ENSURE_AUTO_FAILED
    )
    call :WriteArtifactMarker "!ART_MARKER!" "!ART_NAME!" "!VERIFIED_ARTIFACT_HASH!" "!ART_TREE_SHA256!" "!ART_TREE_FILE_COUNT!"
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
if exist "!ART_KEY!" (
    if /i "!ART_KIND!"=="file" (
        fc /b "!ART_SOURCE!" "!ART_KEY!" >nul 2>&1
        if errorlevel 1 (
            echo [WARN] The copied file does not match the verified source.
            goto :ENSURE_MANUAL_WAIT
        )
    )
    set "ART_TREE_SHA256="
    set "ART_TREE_FILE_COUNT="
    if "!ART_SEAL_TREE!"=="1" (
        call :ComputeTreeFingerprint "!ART_DEST!" ".local-llm-artifact.txt" ART_TREE_SHA256 ART_TREE_FILE_COUNT
        if errorlevel 1 (
            echo [WARN] Could not fingerprint the manually prepared destination.
            goto :ENSURE_MANUAL_WAIT
        )
        if "!ART_HAVE_EXPECTED_TREE!"=="1" if /i not "!ART_TREE_SHA256!"=="!MARKER_TREE_SHA256!" (
            echo [WARN] The manually prepared tree still differs from the verified seal.
            goto :ENSURE_MANUAL_WAIT
        )
        if "!ART_HAVE_EXPECTED_TREE!"=="1" if not "!ART_TREE_FILE_COUNT!"=="!MARKER_TREE_FILE_COUNT!" (
            echo [WARN] The manually prepared tree file count still differs from the verified seal.
            goto :ENSURE_MANUAL_WAIT
        )
    ) else if /i "!ART_KIND!"=="archive" (
        REM Mutable archives are sealed after dependency installation, but a
        REM manually supplied tree must still be free of reparse points now.
        call :ComputeTreeFingerprint "!ART_DEST!" ".local-llm-artifact.txt" ART_MANUAL_TREE_SHA256 ART_MANUAL_TREE_FILE_COUNT
        if errorlevel 1 (
            echo [WARN] The manually prepared destination contains an invalid filesystem object.
            goto :ENSURE_MANUAL_WAIT
        )
    )
    call :WriteArtifactMarker "!ART_MARKER!" "!ART_NAME!" "!VERIFIED_ARTIFACT_HASH!" "!ART_TREE_SHA256!" "!ART_TREE_FILE_COUNT!"
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

:VerifyArtifactHash
setlocal EnableDelayedExpansion
set "HASH_FILE=%~1"
set "HASH_NAME=%~2"
set "EXPECTED_HASH="
if exist "%HASH_MANIFEST%" (
    for /f "usebackq tokens=1,*" %%H in ("%HASH_MANIFEST%") do (
        if /i "%%I"=="!HASH_NAME!" set "EXPECTED_HASH=%%H"
    )
)
set "HASH_TARGET=!HASH_FILE!"
set "ACTUAL_HASH="
for /f "usebackq delims=" %%H in (`"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "(Get-FileHash -LiteralPath $env:HASH_TARGET -Algorithm SHA256).Hash.ToLowerInvariant()"`) do set "ACTUAL_HASH=%%H"
if not defined ACTUAL_HASH (
    echo [ERROR] Could not calculate SHA-256 for !HASH_FILE!
    endlocal & exit /b 1
)
if not defined EXPECTED_HASH (
    echo [ERROR] No trusted SHA-256 is recorded for !HASH_NAME!.
    endlocal & exit /b 1
) else if /i not "!ACTUAL_HASH!"=="!EXPECTED_HASH!" (
    echo [ERROR] SHA-256 mismatch for !HASH_NAME!
    echo [ERROR] Expected: !EXPECTED_HASH!
    echo [ERROR] Actual  : !ACTUAL_HASH!
    endlocal & exit /b 2
)
if defined EXPECTED_HASH echo [OK] SHA-256 verified: !HASH_NAME!
endlocal & set "VERIFIED_ARTIFACT_HASH=%ACTUAL_HASH%" & exit /b 0

:WriteArtifactMarker
setlocal
set "ARTIFACT_MARKER_TEMP=%~1.tmp-%RANDOM%-%RANDOM%"
if exist "%ARTIFACT_MARKER_TEMP%" (
    endlocal & exit /b 1
)
>"%ARTIFACT_MARKER_TEMP%" (
    echo artifact=%~2
    echo sha256=%~3
    if not "%~4"=="" echo tree_sha256=%~4
    if not "%~5"=="" echo tree_file_count=%~5
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
set "TREE_OUTPUT=%WORK%\tree-fingerprint-%RANDOM%-%RANDOM%.txt"
set "TREE_SHA256_VALUE="
set "TREE_FILE_COUNT_VALUE="
if not exist "%~1\." (
    endlocal & exit /b 1
)
"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $root=Get-Item -LiteralPath $env:TREE_ROOT -Force; if(-not $root.PSIsContainer){throw 'Tree root is not a directory'}; if(($root.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw 'Tree root is a reparse point'}; $rootPath=$root.FullName.TrimEnd([char[]]'\/'); $excludes=@([string]$env:TREE_EXCLUDE_REL -split ';' | ForEach-Object {$_.Replace('\','/').TrimStart('/') } | Where-Object {$_}); $rows=New-Object 'System.Collections.Generic.List[string]'; foreach($item in Get-ChildItem -LiteralPath $rootPath -Force -Recurse -ErrorAction Stop){if(($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw ('Reparse point in sealed tree: '+$item.FullName)}; if($item.PSIsContainer){continue}; $rel=$item.FullName.Substring($rootPath.Length).TrimStart([char[]]'\/').Replace('\','/'); $skip=$false; foreach($exclude in $excludes){if($exclude.EndsWith('/')){if($rel.StartsWith($exclude,[StringComparison]::OrdinalIgnoreCase)){$skip=$true;break}}elseif([StringComparer]::OrdinalIgnoreCase.Equals($rel,$exclude)){$skip=$true;break}}; if($skip){continue}; $fileHash=(Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant(); [void]$rows.Add($rel+"`0"+$item.Length+"`0"+$fileHash)}; [string[]]$ordered=$rows.ToArray(); [Array]::Sort($ordered,[StringComparer]::Ordinal); $body=[String]::Join("`n",$ordered); if($ordered.Length -gt 0){$body+="`n"}; $bytes=(New-Object Text.UTF8Encoding($false)).GetBytes($body); $sha=[Security.Cryptography.SHA256]::Create(); try{$digest=$sha.ComputeHash($bytes)}finally{$sha.Dispose()}; $hex=-join($digest | ForEach-Object {$_.ToString('x2')}); @('TREE_SHA256='+$hex,'TREE_FILE_COUNT='+$ordered.Length) | Set-Content -LiteralPath $env:TREE_OUTPUT -Encoding ascii" >nul 2>&1
if errorlevel 1 (
    if exist "%TREE_OUTPUT%" del /f /q "%TREE_OUTPUT%" >nul 2>&1
    endlocal & exit /b 1
)
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
setlocal DisableDelayedExpansion
set "TREE_EXPECTED_SHA256="
set "TREE_EXPECTED_FILE_COUNT="
if not exist "%~2" (
    endlocal & exit /b 1
)
for /f "usebackq tokens=1,* delims==" %%A in ("%~2") do (
    if /i "%%A"=="tree_sha256" set "TREE_EXPECTED_SHA256=%%B"
    if /i "%%A"=="tree_file_count" set "TREE_EXPECTED_FILE_COUNT=%%B"
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
    endlocal & exit /b 1
)
if not "%TREE_CURRENT_FILE_COUNT%"=="%TREE_EXPECTED_FILE_COUNT%" (
    endlocal & exit /b 1
)
endlocal & exit /b 0

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
echo [STEP] Verify pinned versions and archive layouts
if not exist "%RAG_PY%" exit /b 1
if not exist "%OCR_PY%" exit /b 1
if not exist "%UV_EXE%" exit /b 1
if not exist "%NODE_DIR%\node.exe" exit /b 1
if not exist "%PORTABLE_GIT_DIR%\cmd\git.exe" exit /b 1

"%RAG_PY%" -c "import sys; assert sys.version.split()[0] == '%PY_RAG_VERSION%', sys.version; print(sys.version)" >"%LOGS%\python-rag-version.log" 2>&1
if errorlevel 1 (
    echo [ERROR] RAGFlow Python must be exactly %PY_RAG_VERSION%. See %LOGS%\python-rag-version.log
    exit /b 1
)
"%OCR_PY%" -c "import sys; assert sys.version.split()[0] == '%PY_OCR_VERSION%', sys.version; print(sys.version)" >"%LOGS%\python-ocr-version.log" 2>&1
if errorlevel 1 (
    echo [ERROR] OCR Python must be exactly %PY_OCR_VERSION%. See %LOGS%\python-ocr-version.log
    exit /b 1
)
"%UV_EXE%" --version >"%LOGS%\uv-version.log" 2>&1
if errorlevel 1 exit /b 1
findstr /b /c:"uv %PIN_UV_VERSION%" "%LOGS%\uv-version.log" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] uv version mismatch. See %LOGS%\uv-version.log
    exit /b 1
)
"%NODE_DIR%\node.exe" --version >"%LOGS%\node-version.log" 2>&1
if errorlevel 1 exit /b 1
findstr /x /c:"v%NODE_VERSION%" "%LOGS%\node-version.log" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node version mismatch. See %LOGS%\node-version.log
    exit /b 1
)
"%PORTABLE_GIT_DIR%\cmd\git.exe" --version >"%LOGS%\git-version.log" 2>&1
if errorlevel 1 exit /b 1
findstr /x /c:"git version %MINGIT_GIT_VERSION%" "%LOGS%\git-version.log" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Portable Git version mismatch. See %LOGS%\git-version.log
    exit /b 1
)

set "RAGFLOW_CHECK_FILE=%RAGFLOW_DIR%\pyproject.toml"
set "RAGFLOW_CHECK_VERSION=%RAGFLOW_VERSION%"
"%RAG_PY%" -c "import os,pathlib,tomllib; p=tomllib.loads(pathlib.Path(os.environ['RAGFLOW_CHECK_FILE']).read_text(encoding='utf-8')); assert p['project']['version']==os.environ['RAGFLOW_CHECK_VERSION'], p['project']['version']" >"%LOGS%\ragflow-version.log" 2>&1
if errorlevel 1 (
    echo [ERROR] The expanded RAGFlow source is not version %RAGFLOW_VERSION%.
    echo [HINT] Delete %RAGFLOW_DIR% and rerun this script.
    exit /b 1
)
exit /b 0

:PrepareSharedRuntimeDlls
echo [STEP] Extract the pinned Microsoft VC runtime for app-local deployment
set "VC_REDIST_EXE=%APP%\build\vendor-exe\vcredist\VC_redist.x64.exe"
set "VC_STAGE=%WORK%\vcredist-%RANDOM%-%RANDOM%"
set "VC_PARTS=%VC_STAGE%\parts"
set "VC_PAYLOAD=%VC_STAGE%\payload"
set "VC_DLLS=%VC_STAGE%\dlls"
set "VC_LOG=%LOGS%\vcredist-extract.log"
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

"%SEVEN_ZIP%" x -y -t# "-o%VC_PARTS%" "%VC_REDIST_EXE%" >"%VC_LOG%" 2>&1
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

:VC_EXTRACT_FAILED
echo [ERROR] Could not extract the app-local Microsoft VC runtime.
echo [INFO] No installer was executed and no registry/system directory was changed.
echo [INFO] See %VC_LOG%
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
set "RAG_MARKER=%APP%\config\ragflow-python.ok"
set "RAG_FINGERPRINT=project=%PROJECT_VERSION%;ragflow=%RAGFLOW_VERSION%;python=%PY_RAG_VERSION%;numpy=%NUMPY_VERSION%;xgboost=%XGBOOST_VERSION%;datrie=%DATRIE_VERSION%;graspologic-native=%GRASPOLOGIC_NATIVE_VERSION%;addition=%RAG_ADDITION_HASH%;override=%RAG_OVERRIDE_HASH%;exclude=%RAG_EXCLUDE_HASH%"
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
set "RAG_UPSTREAM_REQUIREMENTS=%APP%\config\locks\ragflow-%RAGFLOW_VERSION%-upstream.txt"
set "RAG_REQUIREMENTS=%APP%\config\locks\ragflow-%RAGFLOW_VERSION%-windows.txt"
pushd "%RAGFLOW_DIR%" >nul 2>&1
if errorlevel 1 exit /b 1

REM Prune every package intentionally overridden/excluded below. An exact pin
REM left in a constraint file wins over uv's override and makes the graph
REM unsatisfiable (notably NumPy 1.26.4 and XGBoost 1.6.0).
"%UV_EXE%" export --frozen --no-group test --no-emit-project --no-emit-package numpy --no-emit-package xgboost --no-emit-package datrie --no-emit-package graspologic --no-emit-package infinity-emb --no-emit-package unclecode-litellm --no-emit-package agentrun-mem0ai --no-header --no-annotate --format requirements-txt --output-file "%RAG_UPSTREAM_REQUIREMENTS%" >"%LOGS%\ragflow-export.log" 2>&1
if errorlevel 1 (
    popd >nul
    echo [ERROR] uv could not export the RAGFlow lock. See %LOGS%\ragflow-export.log
    exit /b 1
)

"%UV_EXE%" pip compile "%RAGFLOW_DIR%\pyproject.toml" "%PROJECT%\ragflow-windows-additions.txt" --python "%RAG_PY%" --constraints "%RAG_UPSTREAM_REQUIREMENTS%" --overrides "%PROJECT%\ragflow-windows-overrides.txt" --excludes "%PROJECT%\ragflow-windows-excludes.txt" --generate-hashes --no-header --no-annotate --output-file "%RAG_REQUIREMENTS%" >"%LOGS%\ragflow-compile-windows.log" 2>&1
if errorlevel 1 (
    popd >nul
    echo [ERROR] Could not compile the pinned Windows dependency graph.
    echo [INFO] See %LOGS%\ragflow-compile-windows.log
    exit /b 1
)
popd >nul

call :RemoveTreeChecked "%RAG_PY_DIR%\Lib\site-packages"
if errorlevel 1 exit /b 1
call :MakeDirChecked "%RAG_PY_DIR%\Lib\site-packages"
if errorlevel 1 exit /b 1
call :RemoveTreeChecked "%RAG_PY_DIR%\Scripts"
if errorlevel 1 exit /b 1

"%UV_EXE%" pip sync --python "%RAG_PY%" --require-hashes "%RAG_REQUIREMENTS%" >"%LOGS%\ragflow-python-install.log" 2>&1
if errorlevel 1 (
    echo [ERROR] RAGFlow dependency installation failed.
    echo [INFO] Native Windows is not an upstream-supported RAGFlow target.
    echo [INFO] The known datrie compiled gap is supplied as a pinned MSVC wheel.
    echo [INFO] See %LOGS%\ragflow-python-install.log
    exit /b 1
)

"%UV_EXE%" pip install --python "%RAG_PY%" --no-deps "%APP%\build\vendor-wheels\rag\datrie-%DATRIE_VERSION%-cp313-cp313-win_amd64.whl" >"%LOGS%\ragflow-datrie-install.log" 2>&1
if errorlevel 1 (
    echo [ERROR] The pinned datrie wheel could not be installed. See %LOGS%\ragflow-datrie-install.log
    exit /b 1
)

call :FinalizeRagflowRuntime
if errorlevel 1 exit /b 1
call :WriteFingerprintMarker "%RAG_MARKER%" "%RAG_FINGERPRINT%"
if errorlevel 1 exit /b 1
exit /b 0

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
echo [STEP] Verify pinned RAGFlow models, NLTK, tiktoken and Tika assets
"%RAG_PY%" "%PROJECT%\prepare_ragflow_assets.py" --ragflow-dir "%RAGFLOW_DIR%" --nltk-dir "%NLTK_DATA%" --record "%RAG_ASSET_RECORD%" >"%LOGS%\ragflow-assets.log" 2>&1
if errorlevel 1 (
    echo [ERROR] RAGFlow asset preparation failed. See %LOGS%\ragflow-assets.log
    exit /b 1
)
exit /b 0

:VerifyRagflowRuntime
echo [STEP] Exercise the Windows-compatible RAGFlow worker, tokenizer and XGBoost model
"%RAG_PY%" "%PROJECT%\verify_ragflow_runtime.py" --ragflow-dir "%RAGFLOW_DIR%" >"%LOGS%\ragflow-runtime-smoke.log" 2>&1
if errorlevel 1 (
    echo [ERROR] RAGFlow runtime smoke failed. See %LOGS%\ragflow-runtime-smoke.log
    exit /b 1
)
exit /b 0

:BuildRagflowWeb
if not exist "%RAGFLOW_DIR%\web\package-lock.json" (
    echo [ERROR] RAGFlow web package-lock.json is missing.
    exit /b 1
)
call :FileSha256 "%RAGFLOW_DIR%\web\package-lock.json" WEB_LOCK_HASH
if errorlevel 1 exit /b 1
set "WEB_MARKER=%APP%\config\ragflow-web.ok"
set "WEB_FINGERPRINT=project=%PROJECT_VERSION%;ragflow=%RAGFLOW_VERSION%;node=%NODE_VERSION%;lock=%WEB_LOCK_HASH%"
call :MarkerMatches "%WEB_MARKER%" "%WEB_FINGERPRINT%"
if errorlevel 1 goto :WEB_BUILD
if not exist "%APP%\web\index.html" goto :WEB_BUILD
call :TreeMatchesMarker "%APP%\web" "%WEB_MARKER%" ""
if errorlevel 1 (
    echo [WARN] The fingerprinted web dist changed; rebuilding it.
    goto :WEB_BUILD
)
echo [SKIP] RAGFlow production web build already prepared and fingerprinted.
exit /b 0

:WEB_BUILD
echo [STEP] Build production RAGFlow web assets with Node %NODE_VERSION%
set "NODE_OPTIONS=--max-old-space-size=6144"
set "npm_config_scripts_prepend_node_path=true"
set "NPM_CONFIG_SCRIPTS_PREPEND_NODE_PATH=true"
set "VITE_BUILD_SOURCEMAP=false"
set "VITE_MINIFY=esbuild"
call :RemoveTreeChecked "%RAGFLOW_DIR%\web\dist"
if errorlevel 1 exit /b 1
pushd "%RAGFLOW_DIR%\web" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Could not enter the RAGFlow web source directory.
    exit /b 1
)
call "%NODE_DIR%\npm.cmd" ci --no-audit --no-fund >"%LOGS%\ragflow-web-npm-ci.log" 2>&1
if errorlevel 1 (
    popd >nul
    echo [ERROR] npm ci failed. See %LOGS%\ragflow-web-npm-ci.log
    exit /b 1
)
call "%NODE_DIR%\npm.cmd" run build >"%LOGS%\ragflow-web-build.log" 2>&1
if errorlevel 1 (
    popd >nul
    echo [ERROR] RAGFlow web build failed. See %LOGS%\ragflow-web-build.log
    exit /b 1
)
popd >nul
if not exist "%RAGFLOW_DIR%\web\dist\index.html" (
    echo [ERROR] Web build did not produce dist\index.html.
    exit /b 1
)
call :RemoveTreeChecked "%APP%\web"
if errorlevel 1 exit /b 1
call :CopyTree "%RAGFLOW_DIR%\web\dist" "%APP%\web"
if errorlevel 1 exit /b 1
call :RemoveTreeChecked "%RAGFLOW_DIR%\web\node_modules"
if errorlevel 1 exit /b 1
call :RemoveTreeChecked "%RAGFLOW_DIR%\web\dist"
if errorlevel 1 exit /b 1
call :ComputeTreeFingerprint "%APP%\web" "" WEB_TREE_SHA256 WEB_TREE_FILE_COUNT
if errorlevel 1 exit /b 1
call :WriteSealedFingerprintMarker "%WEB_MARKER%" "%WEB_FINGERPRINT%" "%WEB_TREE_SHA256%" "%WEB_TREE_FILE_COUNT%"
if errorlevel 1 exit /b 1
exit /b 0

REM ============================================================================
REM OCR WHEELHOUSE, INSTALL AND STRICT GPU WARMUP
REM ============================================================================

:PrepareOcrPython
call :FileSha256 "%PROJECT%\requirements-ocr.txt" OCR_REQUIREMENTS_HASH
if errorlevel 1 exit /b 1
set "OCR_MARKER=%APP%\config\ocr-python.ok"
set "OCR_FINGERPRINT=project=%PROJECT_VERSION%;python=%PY_OCR_VERSION%;paddle=%PADDLE_VERSION%;paddleocr=%PADDLEOCR_VERSION%;paddlex=%PADDLEX_VERSION%;requirements=%OCR_REQUIREMENTS_HASH%"
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
set "OCR_WHEEL_STAGE=%WORK%\ocr-wheelhouse-%RANDOM%-%RANDOM%"
if exist "%OCR_WHEEL_STAGE%" (
    echo [ERROR] Temporary OCR wheelhouse collision: %OCR_WHEEL_STAGE%
    exit /b 1
)
call :MakeDirChecked "%OCR_WHEEL_STAGE%"
if errorlevel 1 exit /b 1
call :RemoveTreeChecked "%OCR_PY_DIR%\Lib\site-packages"
if errorlevel 1 exit /b 1
call :MakeDirChecked "%OCR_PY_DIR%\Lib\site-packages"
if errorlevel 1 exit /b 1
call :RemoveTreeChecked "%OCR_PY_DIR%\Scripts"
if errorlevel 1 exit /b 1

"%UV_EXE%" pip install --python "%OCR_PY%" "pip==%PIN_PIP_VERSION%" >"%LOGS%\ocr-pip-bootstrap.log" 2>&1
if errorlevel 1 (
    echo [ERROR] OCR pip bootstrap failed. See %LOGS%\ocr-pip-bootstrap.log
    exit /b 1
)

"%OCR_PY%" -m pip download --dest "%OCR_WHEEL_STAGE%" --only-binary=:all: "%APP%\build\vendor-wheels\ocr\paddlepaddle_gpu-%PADDLE_VERSION%-cp311-cp311-win_amd64.whl" --requirement "%PROJECT%\requirements-ocr.txt" >"%LOGS%\ocr-wheelhouse.log" 2>&1
if errorlevel 1 (
    echo [ERROR] Could not create a binary-only OCR wheelhouse.
    echo [INFO] See %LOGS%\ocr-wheelhouse.log
    exit /b 1
)

"%UV_EXE%" pip install --python "%OCR_PY%" --no-index --find-links "%OCR_WHEEL_STAGE%" "%OCR_WHEEL_STAGE%\paddlepaddle_gpu-%PADDLE_VERSION%-cp311-cp311-win_amd64.whl" --requirements "%PROJECT%\requirements-ocr.txt" >"%LOGS%\ocr-offline-install.log" 2>&1
if errorlevel 1 (
    echo [ERROR] OCR installation from the local wheelhouse failed.
    echo [INFO] See %LOGS%\ocr-offline-install.log
    exit /b 1
)

call :FinalizeOcrRuntime
if errorlevel 1 exit /b 1
call :RemoveTreeChecked "%APP%\build\wheelhouse\ocr"
if errorlevel 1 exit /b 1
call :RemoveTreeChecked "%OCR_WHEEL_STAGE%"
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
"%OCR_PY%" -c "import paddle,paddleocr,paddlex; assert paddle.__version__ == '%PADDLE_VERSION%', paddle.__version__; assert paddleocr.__version__ == '%PADDLEOCR_VERSION%', paddleocr.__version__; assert paddlex.__version__ == '%PADDLEX_VERSION%', paddlex.__version__; print('OCR imports and versions OK')" >"%LOGS%\ocr-import-smoke.log" 2>&1
if errorlevel 1 (
    echo [ERROR] OCR import/version smoke failed. See %LOGS%\ocr-import-smoke.log
    exit /b 1
)
exit /b 0

:RunOcrContract
echo [STEP] Validate the local OCR job API contract without loading GPU models
"%OCR_PY%" "%APP%\services\ocr\test_ocr_job_gateway_contract.py" >"%LOGS%\ocr-gateway-contract.log" 2>&1
if errorlevel 1 (
    echo [ERROR] OCR gateway contract test failed. See %LOGS%\ocr-gateway-contract.log
    exit /b 1
)

exit /b 0

:RunStrictGpuE2E
echo [STEP] Run strict CUDA, OCR warmup and RAGFlow parser E2E on the selected GPU
where nvidia-smi >nul 2>&1
if errorlevel 1 (
    echo [ERROR] nvidia-smi is not available.
    echo [ERROR] The NVIDIA driver must already be installed by an administrator.
    echo [ERROR] This project never falls back to CPU inference.
    exit /b 1
)
nvidia-smi --query-gpu=index,name,memory.total,driver_version --format=csv,noheader >"%LOGS%\nvidia-smi.log" 2>&1
if errorlevel 1 (
    echo [ERROR] Could not query the NVIDIA driver. See %LOGS%\nvidia-smi.log
    exit /b 1
)

"%RAG_PY%" "%APP%\services\ocr\ocr_ragflow_e2e.py" --ocr-python "%OCR_PY%" --gateway-script "%APP%\services\ocr\ocr_job_gateway.py" --config "%APP%\config\pp-structure-v3-8gb.yaml" --model-root "%PADDLE_PDX_CACHE_HOME%\official_models" --font "%APP%\services\ocr\font\ttf\DejaVuSans.ttf" --ragflow-dir "%RAGFLOW_DIR%" --work-dir "%WORK%\ocr-e2e" --record "%APP%\config\ocr-gpu-smoke.json" --log "%LOGS%\ocr-gateway-e2e-server.log" >"%LOGS%\ocr-gpu-smoke.log" 2>&1
if errorlevel 1 (
    echo [ERROR] Strict GPU OCR warmup failed. No CPU fallback was attempted.
    echo [INFO] GTX 1080 requires the pinned cu118 wheel with sm_61 and a working driver.
    echo [INFO] The E2E test also requires actual text from RAGFlow's stock PaddleOCR parser.
    echo [INFO] See %LOGS%\ocr-gpu-smoke.log
    exit /b 1
)
echo [OK] Strict GPU OCR warmup passed.
exit /b 0

REM ============================================================================
REM PROBES, PORTABILITY AUDIT AND PACKAGING
REM ============================================================================

:ProbePortableBinaries
echo [STEP] Probe portable service and inference executables
if not exist "%APP%\services\elasticsearch\jdk\bin\java.exe" goto :PROBE_FAILED
set "ES_JAVA_HOME=%APP%\services\elasticsearch\jdk"
set "JAVA_HOME=%ES_JAVA_HOME%"
set "PATH=%JAVA_HOME%\bin;%PATH%"
"%JAVA_HOME%\bin\java.exe" -version >"%LOGS%\probe-java.log" 2>&1
if errorlevel 1 goto :PROBE_FAILED
"%APP%\services\mysql\bin\mysqld.exe" --version >"%LOGS%\probe-mysql.log" 2>&1
if errorlevel 1 goto :PROBE_FAILED
findstr /c:"%MYSQL_VERSION%" "%LOGS%\probe-mysql.log" >nul 2>&1
if errorlevel 1 goto :PROBE_FAILED
call "%APP%\services\elasticsearch\bin\elasticsearch.bat" -V >"%LOGS%\probe-elasticsearch.log" 2>&1
if errorlevel 1 goto :PROBE_FAILED
findstr /c:"%ELASTIC_VERSION%" "%LOGS%\probe-elasticsearch.log" >nul 2>&1
if errorlevel 1 goto :PROBE_FAILED
"%APP%\services\valkey\valkey-server.exe" --version >"%LOGS%\probe-valkey.log" 2>&1
if errorlevel 1 goto :PROBE_FAILED
findstr /c:"%VALKEY_VERSION%" "%LOGS%\probe-valkey.log" >nul 2>&1
if errorlevel 1 goto :PROBE_FAILED
"%APP%\services\caddy\caddy.exe" version >"%LOGS%\probe-caddy.log" 2>&1
if errorlevel 1 goto :PROBE_FAILED
findstr /c:"%CADDY_VERSION%" "%LOGS%\probe-caddy.log" >nul 2>&1
if errorlevel 1 goto :PROBE_FAILED
"%APP%\runtime\llama\llama-server.exe" --version >"%LOGS%\probe-llama.log" 2>&1
if errorlevel 1 goto :PROBE_FAILED
findstr /c:"%LLAMA_BUILD:~1%" "%LOGS%\probe-llama.log" >nul 2>&1
if errorlevel 1 goto :PROBE_FAILED
"%APP%\services\silo\silo.exe" --version >"%LOGS%\probe-silo.log" 2>&1
if errorlevel 1 goto :PROBE_FAILED
exit /b 0

:PROBE_FAILED
echo [ERROR] A portable executable could not start. See probe logs in %LOGS%
echo [INFO] A missing Microsoft runtime DLL is a common cause on clean Windows PCs.
exit /b 1

:AuditPortableRuntimes
echo [STEP] Check critical launch/config files for build-path leaks and symlinks
"%RAG_PY%" "%PROJECT%\audit_portability.py" --root "%RAG_PY_DIR%" --build-root "%ROOT%" --record "%APP%\config\audit-python-rag.json" >"%LOGS%\audit-python-rag.log" 2>&1
if errorlevel 1 goto :AUDIT_FAILED
"%RAG_PY%" "%PROJECT%\audit_portability.py" --root "%OCR_PY_DIR%" --build-root "%ROOT%" --record "%APP%\config\audit-python-ocr.json" >"%LOGS%\audit-python-ocr.log" 2>&1
if errorlevel 1 goto :AUDIT_FAILED
exit /b 0

:AUDIT_FAILED
echo [ERROR] Portability audit failed. See audit logs in %LOGS%
exit /b 1

:SealMutableRuntimeTrees
echo [STEP] Seal final Python and RAGFlow trees after all online mutations
call :SealOneTree "%RAG_PY_DIR%" "%RAG_PY_TREE_MARKER%" "%RAG_PY_TREE_FINGERPRINT%"
if errorlevel 1 exit /b 1
call :SealOneTree "%OCR_PY_DIR%" "%OCR_PY_TREE_MARKER%" "%OCR_PY_TREE_FINGERPRINT%"
if errorlevel 1 exit /b 1
call :SealOneTree "%RAGFLOW_DIR%" "%RAGFLOW_TREE_MARKER%" "%RAGFLOW_TREE_FINGERPRINT%" "%RAGFLOW_TREE_EXCLUDES%"
if errorlevel 1 exit /b 1
exit /b 0

:SealOneTree
set "FINAL_TREE_SHA256="
set "FINAL_TREE_FILE_COUNT="
call :ComputeTreeFingerprint "%~1" "%~4" FINAL_TREE_SHA256 FINAL_TREE_FILE_COUNT
if errorlevel 1 exit /b 1
call :WriteSealedFingerprintMarker "%~2" "%~3" "%FINAL_TREE_SHA256%" "%FINAL_TREE_FILE_COUNT%"
if errorlevel 1 exit /b 1
call :MutableTreeMatches "%~1" "%~2" "%~3" "%~4"
exit /b %ERRORLEVEL%

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
fc /b "%SEVEN_ZIP_BOOTSTRAP%" "%PACKAGE_STAGE%\7zr.exe" >nul 2>&1
if errorlevel 1 exit /b 1
copy /y "%ROOT%\LOCAL-LLM.bat" "%PACKAGE_STAGE%\LOCAL-LLM.bat" >nul 2>&1
if errorlevel 1 exit /b 1
copy /y "%ROOT%\README.md" "%PACKAGE_STAGE%\README.md" >nul 2>&1
if errorlevel 1 exit /b 1

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

call :VerifyPreparedStage
if errorlevel 1 exit /b 1
call :WriteBuildRecords
if errorlevel 1 exit /b 1
call :ValidatePreparedSet "%PACKAGE_STAGE%"
if errorlevel 1 (
    echo [ERROR] The unpublished prepared set failed its final manifest validation.
    exit /b 1
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
pushd "%ROOT%" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Could not enter the project root while creating %~1.
    exit /b 1
)
"%SEVEN_ZIP%" a -t7z -mx=7 -ms=on -mmt=on "%BUNDLE_OUT%" @"%BUNDLE_LIST%" >"%LOGS%\bundle-%~1.log" 2>&1
set "BUNDLE_RC=%ERRORLEVEL%"
popd >nul
if not "%BUNDLE_RC%"=="0" (
    echo [ERROR] Could not create %~1. See %LOGS%\bundle-%~1.log
    exit /b 1
)
if not exist "%BUNDLE_OUT%" exit /b 1
"%SEVEN_ZIP%" t "%BUNDLE_OUT%" >>"%LOGS%\bundle-%~1.log" 2>&1
if errorlevel 1 (
    echo [ERROR] Integrity test failed for %~1. See %LOGS%\bundle-%~1.log
    exit /b 1
)
echo [OK] Created %~1
exit /b 0

:VerifyPreparedStage
echo [STEP] Rehydrate every bundle and verify the offline payload
set "REHYDRATE_ROOT=%WORK%\rehydrate-%RANDOM%-%RANDOM%"
set "REHYDRATE_APP=%REHYDRATE_ROOT%\app"
set "REHYDRATE_LOG=%LOGS%\prepared-rehydrate.log"
if exist "%REHYDRATE_ROOT%" (
    echo [ERROR] Temporary rehydrate directory collision: %REHYDRATE_ROOT%
    exit /b 1
)
call :MakeDirChecked "%REHYDRATE_ROOT%"
if errorlevel 1 exit /b 1
type nul >"%REHYDRATE_LOG%"
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
    "config\pp-structure-v3-8gb.yaml"
    "runtime\llama\llama-server.exe"
) do if not exist "%REHYDRATE_APP%\%%~K" (
    echo [ERROR] Rehydrated payload is missing app\%%~K
    goto :REHYDRATE_FAILED
)

call :VerifyRehydratedMutableSeals
if errorlevel 1 goto :REHYDRATE_FAILED

for %%D in (
    "tools\7zip"
    "cache\paddlex\official_models\PP-DocLayout-L"
    "cache\paddlex\official_models\PP-DocBlockLayout"
    "cache\paddlex\official_models\PP-OCRv6_medium_det"
    "cache\paddlex\official_models\eslav_PP-OCRv5_mobile_rec"
    "cache\paddlex\official_models\SLANet_plus"
    "services\ocr\font"
    "services\mysql"
    "services\elasticsearch"
    "services\silo"
    "services\valkey"
    "services\caddy"
    "runtime\llama"
) do (
    call :TreeMatchesMarker "%REHYDRATE_APP%\%%~D" "%REHYDRATE_APP%\%%~D\.local-llm-artifact.txt" ".local-llm-artifact.txt"
    if errorlevel 1 (
        echo [ERROR] Rehydrated immutable tree does not match its seal: app\%%~D
        goto :REHYDRATE_FAILED
    )
)
call :TreeMatchesMarker "%REHYDRATE_APP%\web" "%REHYDRATE_APP%\config\ragflow-web.ok" ""
if errorlevel 1 (
    echo [ERROR] Rehydrated web dist does not match its seal.
    goto :REHYDRATE_FAILED
)

call :VerifyRehydratedPayload "%REHYDRATE_APP%" "%REHYDRATE_LOG%" "%BUNDLE_TARGET_DIR%\PORTABILITY-AUDIT.json"
if errorlevel 1 goto :REHYDRATE_FAILED
call :VerifyRehydratedMutableSeals
if errorlevel 1 goto :REHYDRATE_FAILED
call :RemoveTreeChecked "%REHYDRATE_ROOT%"
if errorlevel 1 exit /b 1
echo [OK] All component archives rehydrate into a portable payload.
exit /b 0

:REHYDRATE_FAILED
echo [ERROR] Rehydrated bundle verification failed. See %REHYDRATE_LOG%
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

"%~1\runtime\python-rag\python.exe" -c "import sys; assert sys.version.split()[0] == '%PY_RAG_VERSION%'; import cv2,elasticsearch,quart,valkey" >>"%~2" 2>&1
if errorlevel 1 (endlocal & exit /b 1)
"%~1\runtime\python-rag\python.exe" "%~1\config\project\prepare_ragflow_assets.py" --ragflow-dir "%~1\ragflow" --nltk-dir "%~1\data\nltk" --record "%~1\config\ragflow-assets.json" --verify-only >>"%~2" 2>&1
if errorlevel 1 (endlocal & exit /b 1)
"%~1\runtime\python-rag\python.exe" "%~1\config\project\verify_ragflow_runtime.py" --ragflow-dir "%~1\ragflow" >>"%~2" 2>&1
if errorlevel 1 (endlocal & exit /b 1)
"%~1\runtime\python-ocr\python.exe" -c "import sys,paddle,paddleocr,paddlex; assert sys.version.split()[0] == '%PY_OCR_VERSION%'; assert paddle.__version__ == '%PADDLE_VERSION%'; assert paddleocr.__version__ == '%PADDLEOCR_VERSION%'; assert paddlex.__version__ == '%PADDLEX_VERSION%'" >>"%~2" 2>&1
if errorlevel 1 (endlocal & exit /b 1)
"%~1\runtime\python-ocr\python.exe" "%~1\services\ocr\test_ocr_job_gateway_contract.py" >>"%~2" 2>&1
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
set "SOURCE_HASH_RECORD=%BUNDLE_TARGET_DIR%\SOURCE-SHA256SUMS.txt"
set "BUNDLE_HASH_RECORD=%BUNDLE_TARGET_DIR%\SHA256SUMS.txt"
set "HASH_OUTPUT_FILE=%SOURCE_HASH_RECORD%"
"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $rows=@(); foreach($line in Get-Content -LiteralPath $env:HASH_MANIFEST) { if($line -match '^([0-9a-fA-F]{64})\s{2}(.+)$') { $expected=$matches[1].ToLowerInvariant(); $name=$matches[2]; $path=Join-Path $env:SRC $name; if(-not (Test-Path -LiteralPath $path -PathType Leaf)){throw ('Missing source artifact: '+$name)}; $actual=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant(); if($actual -ne $expected){throw ('Source hash changed: '+$name)}; $rows+=($actual+'  '+$name) } }; if($rows.Count -ne 23){throw ('Expected 23 source artifacts, found '+$rows.Count)}; $rows | Set-Content -LiteralPath $env:HASH_OUTPUT_FILE -Encoding ascii" >"%LOGS%\source-hashes.log" 2>&1
if errorlevel 1 exit /b 1
set "HASH_OUTPUT_FILE=%BUNDLE_HASH_RECORD%"
"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $files=@(Get-ChildItem -LiteralPath $env:BUNDLE_TARGET_DIR -Filter '*.7z' -File); $files+=Get-Item -LiteralPath (Join-Path $env:BUNDLE_TARGET_DIR '7zr.exe'); $files+=Get-Item -LiteralPath (Join-Path $env:BUNDLE_TARGET_DIR 'LOCAL-LLM.bat'); $files+=Get-Item -LiteralPath (Join-Path $env:BUNDLE_TARGET_DIR 'README.md'); $files=@($files | Sort-Object Name); if($files.Count -ne 12){throw ('Expected 9 bundles, 7zr.exe, launcher and README; found '+$files.Count)}; $files | ForEach-Object { '{0}  {1}' -f (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant(), $_.Name } | Set-Content -LiteralPath $env:HASH_OUTPUT_FILE -Encoding ascii" >"%LOGS%\bundle-hashes.log" 2>&1
if errorlevel 1 exit /b 1

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
    echo [ERROR] Stale prepared backup exists: %PREPARED_BACKUP%
    echo [INFO] Rerun the script so startup recovery can resolve it.
    exit /b 1
)

if exist "%PREPARED%" (
    call :ValidatePreparedSet "%PREPARED%"
    if errorlevel 1 (
        echo [ERROR] Existing prepared output changed after startup validation.
        echo [ERROR] It was preserved; refusing to replace or delete it.
        exit /b 1
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

if exist "%PREPARED_BACKUP%" rmdir /s /q "%PREPARED_BACKUP%" >nul 2>&1
if exist "%PREPARED_BACKUP%" (
    echo [ERROR] New output is valid, but the previous backup could not be removed:
    echo [ERROR]   %PREPARED_BACKUP%
    exit /b 1
)
echo [OK] Atomically replaced _src\prepared with the verified bundle set.
exit /b 0

:FileSha256
setlocal
set "FILE_HASH_TARGET=%~1"
set "FILE_HASH_VALUE="
if not exist "%~1" (
    endlocal & exit /b 1
)
for /f "usebackq delims=" %%H in (`"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; (Get-FileHash -LiteralPath $env:FILE_HASH_TARGET -Algorithm SHA256).Hash.ToLowerInvariant()"`) do set "FILE_HASH_VALUE=%%H"
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
    echo [ERROR] Could not remove directory: %~1
    endlocal & exit /b 1
)
endlocal & exit /b 0

REM ============================================================================
REM CLEAN EXIT
REM ============================================================================

:SCRIPT_END
if not defined SCRIPT_RC set "SCRIPT_RC=%ERRORLEVEL%"
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
