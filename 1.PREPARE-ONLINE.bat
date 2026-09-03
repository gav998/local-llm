@echo off
setlocal EnableExtensions DisableDelayedExpansion

REM ============================================================================
REM local_llm - ONLINE PREPARATION ONLY
REM Native Windows x64, portable files, no Docker, no admin operations.
REM
REM Core vendor files are NEVER downloaded by this BAT. When one is missing,
REM the script prints its fixed URL and waits until the user puts it in _src.
REM pip/npm/Hugging Face are used only to resolve transitive dependencies while
REM this online build is being prepared. Their completed outputs are archived.
REM ============================================================================

set "PROJECT_VERSION=1"
set "SEVEN_ZIP_VERSION=26.02"
set "SEVEN_ZIP_TAG=2602"
set "RAGFLOW_VERSION=0.27.1"
set "PY_RAG_VERSION=3.13.15"
set "PY_OCR_VERSION=3.11.16"
set "PY_STANDALONE_RELEASE=20260901"
set "UV_VERSION=0.12.9"
set "PIP_VERSION=26.2.1"
set "NODE_VERSION=22.23.2"
set "MINGIT_VERSION=2.55.0.5"
set "MINGIT_TAG=2.55.0.windows.5"
set "PADDLE_VERSION=3.3.1"
set "PADDLEOCR_VERSION=3.7.0"
set "PADDLEX_VERSION=3.7.2"
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

call :CopyProjectInputs
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

call :BuildRagflowWeb
if errorlevel 1 exit /b 1

call :PrepareOcrPython
if errorlevel 1 exit /b 1

call :RunStrictGpuSmoke
if errorlevel 1 exit /b 1

call :ProbePortableBinaries
if errorlevel 1 exit /b 1

call :AuditPortableRuntimes
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
set "ROOT_TO_VALIDATE=%ROOT%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "if ($env:ROOT_TO_VALIDATE.IndexOfAny([char[]]'!&()^%%') -ge 0) { exit 1 }" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] The project path contains a CMD metacharacter.
    echo [ERROR] Do not use any of these characters in the path: ! ^& ^( ^) ^^ %%
    exit /b 1
)

set "SRC=%ROOT%\_src"
set "PROJECT=%SRC%\project"
set "LOGS=%SRC%\logs"
set "PREPARED=%SRC%\prepared"
set "APP=%ROOT%\app"
set "WORK=%ROOT%\_work"
set "LOCK_DIR=%WORK%\prepare.lock"
set "HASH_MANIFEST=%PROJECT%\artifacts.sha256"

for %%D in (
    "%SRC%"
    "%PROJECT%"
    "%LOGS%"
    "%PREPARED%"
    "%APP%"
    "%APP%\assets"
    "%APP%\build"
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
    "%APP%\temp"
    "%APP%\tools"
    "%WORK%"
) do if not exist "%%~D" mkdir "%%~D" >nul 2>&1

call :SetPortableEnv
exit /b %ERRORLEVEL%

:SetPortableEnv
set "HOME=%APP%\data\profile"
set "USERPROFILE=%APP%\data\profile"
set "APPDATA=%APP%\data\profile\AppData\Roaming"
set "LOCALAPPDATA=%APP%\data\profile\AppData\Local"
set "TEMP=%APP%\temp"
set "TMP=%APP%\temp"

set "XDG_CACHE_HOME=%APP%\cache"
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
set "NLTK_DATA=%APP%\data\nltk"
set "TIKTOKEN_CACHE_DIR=%APP%\cache\tiktoken"
set "CUDA_CACHE_PATH=%APP%\cache\nvidia"
set "PYTHONPYCACHEPREFIX=%APP%\cache\python-bytecode"
set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
set "PIP_NO_WARN_SCRIPT_LOCATION=1"
set "GIT_CONFIG_GLOBAL=%APP%\config\gitconfig"
set "GIT_CONFIG_NOSYSTEM=1"

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
) do if not exist "%%~D" mkdir "%%~D" >nul 2>&1

REM 7zr.exe is only a bootstrap extractor for .7z files. 7za.exe handles the
REM ZIP/TAR/GZIP formats used by the rest of this preparation workflow.
set "SEVEN_ZIP_BOOTSTRAP=%APP%\tools\7zip-bootstrap\7zr.exe"
set "SEVEN_ZIP=%APP%\tools\7zip\x64\7za.exe"
set "UV_EXE=%APP%\build\uv\uv.exe"
set "NODE_DIR=%APP%\build\node"
set "GIT_DIR=%APP%\build\git"
set "RAG_PY_DIR=%APP%\runtime\python-rag"
set "OCR_PY_DIR=%APP%\runtime\python-ocr"
set "RAG_PY=%RAG_PY_DIR%\python.exe"
set "OCR_PY=%OCR_PY_DIR%\python.exe"
set "RAGFLOW_DIR=%APP%\ragflow"
set "OCR_WHEELHOUSE=%APP%\build\wheelhouse\ocr"
set "SHARED_DLL_DIR=%APP%\runtime\shared-dll"
set "PATH=%SHARED_DLL_DIR%;%NODE_DIR%;%GIT_DIR%\cmd;%GIT_DIR%\mingw64\bin;%PATH%"
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
for %%F in (
    "pp-structure-v3-8gb.yaml"
    "requirements-ocr.txt"
    "artifacts.sha256"
    "prepare_ragflow_assets.py"
    "ocr_job_gateway.py"
    "test_ocr_job_gateway_contract.py"
    "ocr_ragflow_e2e.py"
    "audit_portability.py"
) do (
    if not exist "%PROJECT%\%%~F" (
        echo [ERROR] Project input is missing: %PROJECT%\%%~F
        exit /b 1
    )
    copy /y "%PROJECT%\%%~F" "%APP%\config\%%~F" >nul
    if errorlevel 1 exit /b 1
)
exit /b 0

REM ============================================================================
REM MANUALLY SUPPLIED CORE ARTIFACTS
REM ============================================================================

:AcquireCoreArtifacts
echo [STEP] Validate and expand manually supplied vendor artifacts

set "SEVEN_ZIP=%SEVEN_ZIP_BOOTSTRAP%"
call :EnsureArtifact "7zr.exe" "tools\7zip-bootstrap" "7zr.exe" "https://www.7-zip.org/a/7zr.exe"
if errorlevel 1 exit /b 1
call :EnsureArtifact "7z%SEVEN_ZIP_TAG%-extra.7z" "tools\7zip" "x64\7za.exe" "https://www.7-zip.org/a/7z%SEVEN_ZIP_TAG%-extra.7z"
if errorlevel 1 exit /b 1
set "SEVEN_ZIP=%APP%\tools\7zip\x64\7za.exe"

call :EnsureArtifact "uv-x86_64-pc-windows-msvc.zip" "build\uv" "uv.exe" "https://github.com/astral-sh/uv/releases/download/%UV_VERSION%/uv-x86_64-pc-windows-msvc.zip"
if errorlevel 1 exit /b 1

call :EnsureArtifact "cpython-%PY_RAG_VERSION%+%PY_STANDALONE_RELEASE%-x86_64-pc-windows-msvc-install_only.tar.gz" "runtime\python-rag" "python.exe" "https://github.com/astral-sh/python-build-standalone/releases/download/%PY_STANDALONE_RELEASE%/cpython-%PY_RAG_VERSION%+%PY_STANDALONE_RELEASE%-x86_64-pc-windows-msvc-install_only.tar.gz"
if errorlevel 1 exit /b 1

call :EnsureArtifact "cpython-%PY_OCR_VERSION%+%PY_STANDALONE_RELEASE%-x86_64-pc-windows-msvc-install_only.tar.gz" "runtime\python-ocr" "python.exe" "https://github.com/astral-sh/python-build-standalone/releases/download/%PY_STANDALONE_RELEASE%/cpython-%PY_OCR_VERSION%+%PY_STANDALONE_RELEASE%-x86_64-pc-windows-msvc-install_only.tar.gz"
if errorlevel 1 exit /b 1

call :EnsureArtifact "node-v%NODE_VERSION%-win-x64.zip" "build\node" "node.exe" "https://nodejs.org/dist/v%NODE_VERSION%/node-v%NODE_VERSION%-win-x64.zip"
if errorlevel 1 exit /b 1

call :EnsureArtifact "MinGit-%MINGIT_VERSION%-64-bit.zip" "build\git" "cmd\git.exe" "https://github.com/git-for-windows/git/releases/download/v%MINGIT_TAG%/MinGit-%MINGIT_VERSION%-64-bit.zip"
if errorlevel 1 exit /b 1

call :EnsureArtifact "ragflow-v%RAGFLOW_VERSION%.zip" "ragflow" "pyproject.toml" "https://github.com/infiniflow/ragflow/archive/refs/tags/v%RAGFLOW_VERSION%.zip"
if errorlevel 1 exit /b 1

call :EnsureArtifact "paddlepaddle_gpu-%PADDLE_VERSION%-cp311-cp311-win_amd64.whl" "build\vendor-wheels\ocr" "paddlepaddle_gpu-%PADDLE_VERSION%-cp311-cp311-win_amd64.whl" "https://paddle-whl.cdn.bcebos.com/stable/cu118/paddlepaddle-gpu/paddlepaddle_gpu-%PADDLE_VERSION%-cp311-cp311-win_amd64.whl"
if errorlevel 1 exit /b 1

call :EnsureArtifact "PP-DocLayout-S_infer.tar" "cache\paddlex\official_models\PP-DocLayout-S" "inference.json" "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-DocLayout-S_infer.tar"
if errorlevel 1 exit /b 1
call :EnsureArtifact "PP-DocBlockLayout_infer.tar" "cache\paddlex\official_models\PP-DocBlockLayout" "inference.json" "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-DocBlockLayout_infer.tar"
if errorlevel 1 exit /b 1
call :EnsureArtifact "PP-OCRv6_medium_det_infer.tar" "cache\paddlex\official_models\PP-OCRv6_medium_det" "inference.json" "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-OCRv6_medium_det_infer.tar"
if errorlevel 1 exit /b 1
call :EnsureArtifact "eslav_PP-OCRv5_mobile_rec_infer.tar" "cache\paddlex\official_models\eslav_PP-OCRv5_mobile_rec" "inference.json" "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/eslav_PP-OCRv5_mobile_rec_infer.tar"
if errorlevel 1 exit /b 1

call :EnsureArtifact "mysql-%MYSQL_VERSION%-winx64.zip" "services\mysql" "bin\mysqld.exe" "https://cdn.mysql.com/Downloads/MySQL-8.0/mysql-%MYSQL_VERSION%-winx64.zip"
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

if not exist "!ART_DEST!" mkdir "!ART_DEST!" >nul 2>&1
if not exist "!ART_DEST!" goto :ENSURE_MANUAL_WAIT

set "ART_MARKER=!ART_DEST!\.local-llm-artifact.txt"
set "MARKER_ARTIFACT="
set "MARKER_SHA256="
if exist "!ART_MARKER!" (
    for /f "usebackq tokens=1,* delims==" %%A in ("!ART_MARKER!") do (
        if /i "%%A"=="artifact" set "MARKER_ARTIFACT=%%B"
        if /i "%%A"=="sha256" set "MARKER_SHA256=%%B"
    )
)

if exist "!ART_KEY!" if /i "!MARKER_ARTIFACT!"=="!ART_NAME!" if /i "!MARKER_SHA256!"=="!VERIFIED_ARTIFACT_HASH!" (
    echo [OK] !ART_NAME! -- source is valid and key file already exists
    endlocal & exit /b 0
)
if exist "!ART_KEY!" echo [INFO] Replacing stale or untracked destination for !ART_NAME!.

set "ART_KIND=file"
if /i "!ART_NAME:~-4!"==".zip" set "ART_KIND=archive"
if /i "!ART_NAME:~-3!"==".7z" set "ART_KIND=archive"
if /i "!ART_NAME:~-4!"==".tar" set "ART_KIND=archive"
if /i "!ART_NAME:~-7!"==".tar.gz" set "ART_KIND=archive"
if /i "!ART_NAME:~-4!"==".tgz" set "ART_KIND=archive"

if /i "!ART_KIND!"=="file" (
    for %%D in ("!ART_KEY!") do if not exist "%%~dpD" mkdir "%%~dpD" >nul 2>&1
    set "ART_COPY_TEMP=!ART_KEY!.partial-!RANDOM!-!RANDOM!"
    copy /y "!ART_SOURCE!" "!ART_COPY_TEMP!" >nul 2>&1
    if exist "!ART_COPY_TEMP!" fc /b "!ART_SOURCE!" "!ART_COPY_TEMP!" >nul 2>&1
    if not errorlevel 1 move /y "!ART_COPY_TEMP!" "!ART_KEY!" >nul 2>&1
    if exist "!ART_COPY_TEMP!" del /f /q "!ART_COPY_TEMP!" >nul 2>&1
    if exist "!ART_KEY!" (
        call :WriteArtifactMarker "!ART_MARKER!" "!ART_NAME!" "!VERIFIED_ARTIFACT_HASH!"
        if errorlevel 1 goto :ENSURE_MANUAL_WAIT
        echo [OK] Copied: !ART_NAME!
        endlocal & exit /b 0
    )
    goto :ENSURE_MANUAL_WAIT
)

if not exist "%SEVEN_ZIP%" (
    echo [ERROR] A working 7-Zip command-line extractor must be prepared first.
    goto :ENSURE_MANUAL_WAIT
)

set "ART_STAGE_BASE=%WORK%\extract-!RANDOM!-!RANDOM!"
set "ART_STAGE=!ART_STAGE_BASE!"
set "ART_UNPACK_LOG=%LOGS%\artifact-!ART_NAME!.log"
if exist "!ART_STAGE!" rmdir /s /q "!ART_STAGE!" >nul 2>&1
mkdir "!ART_STAGE!" >nul 2>&1

"%SEVEN_ZIP%" t "!ART_SOURCE!" >"!ART_UNPACK_LOG!" 2>&1
if errorlevel 1 goto :ENSURE_AUTO_FAILED

if /i "!ART_NAME:~-7!"==".tar.gz" goto :ENSURE_TARGZ
if /i "!ART_NAME:~-4!"==".tgz" goto :ENSURE_TARGZ

"%SEVEN_ZIP%" x -y "-o!ART_STAGE!" "!ART_SOURCE!" >>"!ART_UNPACK_LOG!" 2>&1
if errorlevel 1 goto :ENSURE_AUTO_FAILED
goto :ENSURE_STAGE_READY

:ENSURE_TARGZ
set "ART_GZIP_STAGE=!ART_STAGE_BASE!\_gzip"
mkdir "!ART_GZIP_STAGE!" >nul 2>&1
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
    call :WriteArtifactMarker "!ART_MARKER!" "!ART_NAME!" "!VERIFIED_ARTIFACT_HASH!"
    if errorlevel 1 goto :ENSURE_MANUAL_WAIT
    echo [OK] Expanded: !ART_NAME!
    if exist "!ART_STAGE_BASE!" rmdir /s /q "!ART_STAGE_BASE!" >nul 2>&1
    endlocal & exit /b 0
)

:ENSURE_AUTO_FAILED
echo [WARN] Automatic processing did not produce the required key file.
echo [WARN] Details: !ART_UNPACK_LOG!
if exist "!ART_STAGE_BASE!" rmdir /s /q "!ART_STAGE_BASE!" >nul 2>&1

:ENSURE_MANUAL_WAIT
echo.
echo Automatic extraction or copy failed.
echo Extract or copy:
echo   SOURCE:      !ART_SOURCE!
echo   DESTINATION: !ART_DEST!
echo The following file must exist when finished:
echo   KEY FILE:    !ART_KEY!
echo Press any key after completing the operation...
pause >nul
if exist "!ART_KEY!" (
    call :WriteArtifactMarker "!ART_MARKER!" "!ART_NAME!" "!VERIFIED_ARTIFACT_HASH!"
    if errorlevel 1 (
        echo [WARN] Could not write the artifact marker.
        goto :ENSURE_MANUAL_WAIT
    )
    echo [OK] Required key file found: !ART_KEY!
    endlocal & exit /b 0
)
echo [WARN] The required key file is still missing.
goto :ENSURE_MANUAL_WAIT

:VerifyArtifactHash
setlocal EnableDelayedExpansion
set "HASH_FILE=%~1"
set "HASH_NAME=%~2"
set "EXPECTED_HASH="
if exist "%HASH_MANIFEST%" (
    for /f "usebackq tokens=1,2" %%H in ("%HASH_MANIFEST%") do (
        if /i "%%I"=="!HASH_NAME!" set "EXPECTED_HASH=%%H"
    )
)
set "HASH_TARGET=!HASH_FILE!"
set "ACTUAL_HASH="
for /f "usebackq delims=" %%H in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "(Get-FileHash -LiteralPath $env:HASH_TARGET -Algorithm SHA256).Hash.ToLowerInvariant()"`) do set "ACTUAL_HASH=%%H"
if not defined ACTUAL_HASH (
    echo [ERROR] Could not calculate SHA-256 for !HASH_FILE!
    endlocal & exit /b 1
)
if not defined EXPECTED_HASH (
    echo [WARN] No trusted upstream SHA-256 is recorded for !HASH_NAME!.
) else if /i not "!ACTUAL_HASH!"=="!EXPECTED_HASH!" (
    echo [ERROR] SHA-256 mismatch for !HASH_NAME!
    echo [ERROR] Expected: !EXPECTED_HASH!
    echo [ERROR] Actual  : !ACTUAL_HASH!
    endlocal & exit /b 2
)
if defined EXPECTED_HASH echo [OK] SHA-256 verified: !HASH_NAME!
endlocal & set "VERIFIED_ARTIFACT_HASH=%ACTUAL_HASH%" & exit /b 0

:WriteArtifactMarker
>"%~1" (
    echo artifact=%~2
    echo sha256=%~3
)
if not exist "%~1" exit /b 1
exit /b 0

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
    if exist "!TREE_BACKUP!" move "!TREE_BACKUP!" "!TREE_DEST!" >nul 2>&1
    endlocal & exit /b 1
)
if exist "!TREE_BACKUP!" rmdir /s /q "!TREE_BACKUP!" >nul 2>&1
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
if not exist "%GIT_DIR%\cmd\git.exe" exit /b 1

"%RAG_PY%" -c "import sys; assert sys.version_info[:2] == (3, 13), sys.version; print(sys.version)" >"%LOGS%\python-rag-version.log" 2>&1
if errorlevel 1 (
    echo [ERROR] RAGFlow Python must be Python 3.13.x. See %LOGS%\python-rag-version.log
    exit /b 1
)
"%OCR_PY%" -c "import sys; assert sys.version_info[:2] == (3, 11), sys.version; print(sys.version)" >"%LOGS%\python-ocr-version.log" 2>&1
if errorlevel 1 (
    echo [ERROR] OCR Python must be Python 3.11.x. See %LOGS%\python-ocr-version.log
    exit /b 1
)
"%UV_EXE%" --version >"%LOGS%\uv-version.log" 2>&1
if errorlevel 1 exit /b 1
"%NODE_DIR%\node.exe" --version >"%LOGS%\node-version.log" 2>&1
if errorlevel 1 exit /b 1
"%GIT_DIR%\cmd\git.exe" --version >"%LOGS%\git-version.log" 2>&1
if errorlevel 1 exit /b 1

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
echo [STEP] Collect app-local Microsoft runtime DLLs from portable runtimes
if not exist "%SHARED_DLL_DIR%" mkdir "%SHARED_DLL_DIR%" >nul 2>&1
for %%R in ("%RAG_PY_DIR%" "%OCR_PY_DIR%" "%NODE_DIR%") do (
    for %%N in (vcruntime140.dll vcruntime140_1.dll msvcp140.dll concrt140.dll) do (
        if exist "%%~R\%%N" copy /y "%%~R\%%N" "%SHARED_DLL_DIR%\%%N" >nul 2>&1
    )
)
set "PATH=%SHARED_DLL_DIR%;%PATH%"
exit /b 0

REM ============================================================================
REM RAGFLOW PYTHON, ASSETS AND WEB BUILD
REM ============================================================================

:PrepareRagflowPython
set "RAG_MARKER=%APP%\config\ragflow-python-%RAGFLOW_VERSION%.ok"
if exist "%RAG_MARKER%" (
    echo [SKIP] RAGFlow Python runtime already prepared.
    "%UV_EXE%" pip check --python "%RAG_PY%" >"%LOGS%\ragflow-pip-check.log" 2>&1
    if errorlevel 1 exit /b 1
    exit /b 0
)

echo [STEP] Install the official frozen RAGFlow dependency graph
set "RAG_REQUIREMENTS=%APP%\config\locks\ragflow-%RAGFLOW_VERSION%-windows.txt"
pushd "%RAGFLOW_DIR%" >nul 2>&1
if errorlevel 1 exit /b 1

"%UV_EXE%" export --frozen --no-group test --no-emit-project --format requirements-txt --output-file "%RAG_REQUIREMENTS%" >"%LOGS%\ragflow-export.log" 2>&1
if errorlevel 1 (
    popd >nul
    echo [ERROR] uv could not export the RAGFlow lock. See %LOGS%\ragflow-export.log
    exit /b 1
)

"%UV_EXE%" pip install --python "%RAG_PY%" "pip==%PIP_VERSION%" "setuptools==80.9.0" wheel >"%LOGS%\ragflow-bootstrap.log" 2>&1
if errorlevel 1 (
    popd >nul
    echo [ERROR] Python bootstrap failed. See %LOGS%\ragflow-bootstrap.log
    exit /b 1
)

"%UV_EXE%" pip install --python "%RAG_PY%" --requirements "%RAG_REQUIREMENTS%" >"%LOGS%\ragflow-python-install.log" 2>&1
if errorlevel 1 (
    popd >nul
    echo [ERROR] RAGFlow dependency installation failed.
    echo [INFO] Native Windows is not an upstream-supported RAGFlow target.
    echo [INFO] Some locked packages may require MSVC build tools on the online PC.
    echo [INFO] See %LOGS%\ragflow-python-install.log
    exit /b 1
)
popd >nul

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
>"%RAG_MARKER%" echo version=%RAGFLOW_VERSION%
exit /b 0

:PrepareRagflowAssets
set "RAG_ASSET_RECORD=%APP%\config\ragflow-assets.json"
if exist "%RAG_ASSET_RECORD%" (
    echo [SKIP] Platform-neutral RAGFlow assets already prepared.
    exit /b 0
)
echo [STEP] Download platform-neutral RAGFlow models, NLTK, tiktoken and Tika
"%RAG_PY%" "%PROJECT%\prepare_ragflow_assets.py" --ragflow-dir "%RAGFLOW_DIR%" --nltk-dir "%NLTK_DATA%" --record "%RAG_ASSET_RECORD%" >"%LOGS%\ragflow-assets.log" 2>&1
if errorlevel 1 (
    echo [ERROR] RAGFlow asset preparation failed. See %LOGS%\ragflow-assets.log
    exit /b 1
)
exit /b 0

:BuildRagflowWeb
if exist "%APP%\web\index.html" (
    echo [SKIP] RAGFlow production web build already prepared.
    exit /b 0
)
if not exist "%RAGFLOW_DIR%\web\package-lock.json" (
    echo [ERROR] RAGFlow web package-lock.json is missing.
    exit /b 1
)
echo [STEP] Build production RAGFlow web assets with Node %NODE_VERSION%
set "NODE_OPTIONS=--max-old-space-size=6144"
set "npm_config_scripts_prepend_node_path=true"
set "NPM_CONFIG_SCRIPTS_PREPEND_NODE_PATH=true"
pushd "%RAGFLOW_DIR%\web" >nul 2>&1
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
call :CopyTree "%RAGFLOW_DIR%\web\dist" "%APP%\web"
if errorlevel 1 exit /b 1
if exist "%RAGFLOW_DIR%\web\node_modules" rmdir /s /q "%RAGFLOW_DIR%\web\node_modules" >nul 2>&1
exit /b 0

REM ============================================================================
REM OCR WHEELHOUSE, INSTALL AND STRICT GPU WARMUP
REM ============================================================================

:PrepareOcrPython
set "OCR_MARKER=%APP%\config\ocr-python-%PADDLEOCR_VERSION%.ok"
if exist "%OCR_MARKER%" (
    echo [SKIP] OCR Python runtime already prepared.
    "%UV_EXE%" pip check --python "%OCR_PY%" >"%LOGS%\ocr-pip-check.log" 2>&1
    if errorlevel 1 exit /b 1
    exit /b 0
)

echo [STEP] Resolve an offline-complete binary OCR wheelhouse
if not exist "%OCR_WHEELHOUSE%" mkdir "%OCR_WHEELHOUSE%" >nul 2>&1

"%UV_EXE%" pip install --python "%OCR_PY%" "pip==%PIP_VERSION%" >"%LOGS%\ocr-pip-bootstrap.log" 2>&1
if errorlevel 1 (
    echo [ERROR] OCR pip bootstrap failed. See %LOGS%\ocr-pip-bootstrap.log
    exit /b 1
)

"%OCR_PY%" -m pip download --dest "%OCR_WHEELHOUSE%" --only-binary=:all: "pip==%PIP_VERSION%" "%APP%\build\vendor-wheels\ocr\paddlepaddle_gpu-%PADDLE_VERSION%-cp311-cp311-win_amd64.whl" --requirement "%PROJECT%\requirements-ocr.txt" >"%LOGS%\ocr-wheelhouse.log" 2>&1
if errorlevel 1 (
    echo [ERROR] Could not create a binary-only OCR wheelhouse.
    echo [INFO] See %LOGS%\ocr-wheelhouse.log
    exit /b 1
)

"%UV_EXE%" pip install --python "%OCR_PY%" --no-index --find-links "%OCR_WHEELHOUSE%" "%OCR_WHEELHOUSE%\paddlepaddle_gpu-%PADDLE_VERSION%-cp311-cp311-win_amd64.whl" --requirements "%PROJECT%\requirements-ocr.txt" >"%LOGS%\ocr-offline-install.log" 2>&1
if errorlevel 1 (
    echo [ERROR] OCR installation from the local wheelhouse failed.
    echo [INFO] See %LOGS%\ocr-offline-install.log
    exit /b 1
)

"%UV_EXE%" pip check --python "%OCR_PY%" >"%LOGS%\ocr-pip-check.log" 2>&1
if errorlevel 1 (
    echo [ERROR] OCR dependency check failed. See %LOGS%\ocr-pip-check.log
    exit /b 1
)
"%UV_EXE%" pip freeze --python "%OCR_PY%" >"%APP%\config\locks\ocr-freeze.txt" 2>"%LOGS%\ocr-freeze.log"
if errorlevel 1 exit /b 1
>"%OCR_MARKER%" echo paddleocr=%PADDLEOCR_VERSION%
exit /b 0

:RunStrictGpuSmoke
echo [STEP] Validate the local OCR job API contract without loading GPU models
"%OCR_PY%" "%PROJECT%\test_ocr_job_gateway_contract.py" >"%LOGS%\ocr-gateway-contract.log" 2>&1
if errorlevel 1 (
    echo [ERROR] OCR gateway contract test failed. See %LOGS%\ocr-gateway-contract.log
    exit /b 1
)

echo [STEP] Run strict CUDA, OCR warmup and RAGFlow parser E2E on GPU 0
where nvidia-smi >nul 2>&1
if errorlevel 1 (
    echo [ERROR] nvidia-smi is not available.
    echo [ERROR] The NVIDIA driver must already be installed by an administrator.
    echo [ERROR] This project never falls back to CPU inference.
    exit /b 1
)
nvidia-smi --query-gpu=index,name,compute_cap,memory.total,driver_version --format=csv,noheader >"%LOGS%\nvidia-smi.log" 2>&1
if errorlevel 1 (
    echo [ERROR] Could not query the NVIDIA driver. See %LOGS%\nvidia-smi.log
    exit /b 1
)

"%RAG_PY%" "%PROJECT%\ocr_ragflow_e2e.py" --ocr-python "%OCR_PY%" --gateway-script "%PROJECT%\ocr_job_gateway.py" --config "%APP%\config\pp-structure-v3-8gb.yaml" --model-root "%PADDLE_PDX_CACHE_HOME%\official_models" --ragflow-dir "%RAGFLOW_DIR%" --work-dir "%WORK%\ocr-e2e" --record "%APP%\config\ocr-gpu-smoke.json" --log "%LOGS%\ocr-gateway-e2e-server.log" >"%LOGS%\ocr-gpu-smoke.log" 2>&1
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
"%APP%\services\mysql\bin\mysqld.exe" --version >"%LOGS%\probe-mysql.log" 2>&1
if errorlevel 1 goto :PROBE_FAILED
call "%APP%\services\elasticsearch\bin\elasticsearch.bat" -V >"%LOGS%\probe-elasticsearch.log" 2>&1
if errorlevel 1 goto :PROBE_FAILED
"%APP%\services\valkey\valkey-server.exe" --version >"%LOGS%\probe-valkey.log" 2>&1
if errorlevel 1 goto :PROBE_FAILED
"%APP%\services\caddy\caddy.exe" version >"%LOGS%\probe-caddy.log" 2>&1
if errorlevel 1 goto :PROBE_FAILED
"%APP%\runtime\llama\llama-server.exe" --version >"%LOGS%\probe-llama.log" 2>&1
if errorlevel 1 goto :PROBE_FAILED
if not exist "%APP%\services\silo\silo.exe" goto :PROBE_FAILED
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

:PackagePreparedOutput
echo [STEP] Create solid component archives for the offline installation stage
set "PACKAGE_STAGE=%WORK%\prepared-next-%RANDOM%-%RANDOM%"
set "BUNDLE_TARGET_DIR=%PACKAGE_STAGE%"
if exist "%PACKAGE_STAGE%" rmdir /s /q "%PACKAGE_STAGE%" >nul 2>&1
mkdir "%PACKAGE_STAGE%" >nul 2>&1
if not exist "%PACKAGE_STAGE%" exit /b 1
type nul >"%PACKAGE_STAGE%\.gitkeep"

call :WriteBundleList "00-bootstrap-tools.lst" "app\tools\7zip-bootstrap"
if errorlevel 1 exit /b 1
call :AppendBundleList "00-bootstrap-tools.lst" "app\tools\7zip"
if errorlevel 1 exit /b 1
call :AppendBundleList "00-bootstrap-tools.lst" "app\build\uv"
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
call :AppendBundleList "21-paddle-models.lst" "app\config\pp-structure-v3-8gb.yaml"
if errorlevel 1 exit /b 1
call :AppendBundleList "21-paddle-models.lst" "app\config\ocr-gpu-smoke.json"
if errorlevel 1 exit /b 1
call :MakeBundle "21-paddle-models.7z" "21-paddle-models.lst"
if errorlevel 1 exit /b 1

call :WriteBundleList "22-ocr-wheelhouse.lst" "app\build\wheelhouse\ocr"
if errorlevel 1 exit /b 1
call :AppendBundleList "22-ocr-wheelhouse.lst" "app\config\requirements-ocr.txt"
if errorlevel 1 exit /b 1
call :MakeBundle "22-ocr-wheelhouse.7z" "22-ocr-wheelhouse.lst"
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

call :WriteBuildRecords
if errorlevel 1 exit /b 1
call :PromotePreparedOutput
if errorlevel 1 exit /b 1
exit /b 0

:WriteBundleList
>"%WORK%\%~1" echo %~2
exit /b %ERRORLEVEL%

:AppendBundleList
>>"%WORK%\%~1" echo %~2
exit /b %ERRORLEVEL%

:MakeBundle
set "BUNDLE_OUT=%BUNDLE_TARGET_DIR%\%~1"
set "BUNDLE_LIST=%WORK%\%~2"
pushd "%ROOT%" >nul 2>&1
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

:WriteBuildRecords
set "SOURCE_HASH_RECORD=%BUNDLE_TARGET_DIR%\SOURCE-SHA256SUMS.txt"
set "BUNDLE_HASH_RECORD=%BUNDLE_TARGET_DIR%\SHA256SUMS.txt"
set "HASH_INPUT_DIR=%SRC%"
set "HASH_OUTPUT_FILE=%SOURCE_HASH_RECORD%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath $env:HASH_INPUT_DIR -File | Sort-Object Name | ForEach-Object { '{0}  {1}' -f (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant(), $_.Name } | Set-Content -LiteralPath $env:HASH_OUTPUT_FILE -Encoding ascii" >"%LOGS%\source-hashes.log" 2>&1
if errorlevel 1 exit /b 1
set "HASH_INPUT_DIR=%BUNDLE_TARGET_DIR%"
set "HASH_OUTPUT_FILE=%BUNDLE_HASH_RECORD%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath $env:HASH_INPUT_DIR -Filter '*.7z' -File | Sort-Object Name | ForEach-Object { '{0}  {1}' -f (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant(), $_.Name } | Set-Content -LiteralPath $env:HASH_OUTPUT_FILE -Encoding ascii" >"%LOGS%\bundle-hashes.log" 2>&1
if errorlevel 1 exit /b 1

>"%BUNDLE_TARGET_DIR%\build-info.txt" (
    echo local_llm_prepare_version=%PROJECT_VERSION%
    echo prepared_date=%DATE%
    echo prepared_time=%TIME%
    echo target=windows-x86_64-portable
    echo ragflow=%RAGFLOW_VERSION%
    echo python_rag=%PY_RAG_VERSION%
    echo python_ocr=%PY_OCR_VERSION%
    echo paddlepaddle_gpu=%PADDLE_VERSION%-cu118-sm61
    echo paddleocr=%PADDLEOCR_VERSION%
    echo paddlex=%PADDLEX_VERSION%
    echo node=%NODE_VERSION%
    echo mysql=%MYSQL_VERSION%
    echo elasticsearch=%ELASTIC_VERSION%
    echo valkey_windows=%VALKEY_VERSION%-community-unsupported
    echo llama_cpp=%LLAMA_BUILD%-vulkan
    echo ocr_profile=PP-StructureV3-layout-PP-OCRv6-det-eslav-PP-OCRv5-rec-strict-gpu
)
if errorlevel 1 exit /b 1
REM The success marker is deliberately written after every bundle and record.
>"%BUNDLE_TARGET_DIR%\prepared.ok" echo prepared=1
if not exist "%BUNDLE_TARGET_DIR%\prepared.ok" exit /b 1
exit /b 0

:PromotePreparedOutput
set "PREPARED_BACKUP=%WORK%\prepared-previous-%RANDOM%-%RANDOM%"
if exist "%PREPARED_BACKUP%" rmdir /s /q "%PREPARED_BACKUP%" >nul 2>&1

if exist "%PREPARED%" (
    move "%PREPARED%" "%PREPARED_BACKUP%" >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Could not preserve the previous prepared bundle set.
        exit /b 1
    )
)

move "%PACKAGE_STAGE%" "%PREPARED%" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Could not promote the new prepared bundle set.
    if exist "%PREPARED_BACKUP%" move "%PREPARED_BACKUP%" "%PREPARED%" >nul 2>&1
    exit /b 1
)

if exist "%PREPARED_BACKUP%" rmdir /s /q "%PREPARED_BACKUP%" >nul 2>&1
echo [OK] Atomically replaced _src\prepared with the verified bundle set.
exit /b 0

:CopyTree
if not exist "%~1" exit /b 1
if not exist "%~2" mkdir "%~2" >nul 2>&1
robocopy "%~1" "%~2" /E /COPY:DAT /DCOPY:DAT /R:2 /W:1 /NFL /NDL /NJH /NJS /NP >nul
set "COPYTREE_RC=%ERRORLEVEL%"
if %COPYTREE_RC% GEQ 8 exit /b 1
exit /b 0

REM ============================================================================
REM CLEAN EXIT
REM ============================================================================

:SCRIPT_END
if not defined SCRIPT_RC set "SCRIPT_RC=%ERRORLEVEL%"
if defined LOCK_HELD if exist "%LOCK_DIR%" rmdir "%LOCK_DIR%" >nul 2>&1
endlocal & exit /b %SCRIPT_RC%
