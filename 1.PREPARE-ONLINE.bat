@echo off
setlocal EnableExtensions DisableDelayedExpansion

:menu
cls
echo ========================================
echo          LOCAL LLM ONLINE PREPARE
echo ========================================
echo.
echo   1. MySQL
echo   2. Elasticsearch
echo   3. Silo
echo   4. Valkey
echo   5. llama.cpp
echo   6. PaddleOCR
echo   7. RAGFlow
echo   8. Web
echo   0. Exit
echo.
choice /C 123456780 /N /M "Select a module: "

if errorlevel 9 exit /b 0
if errorlevel 8 goto web
if errorlevel 7 goto ragflow
if errorlevel 6 goto paddleocr
if errorlevel 5 goto llama_cpp
if errorlevel 4 goto valkey
if errorlevel 3 goto silo
if errorlevel 2 goto elasticsearch
if errorlevel 1 goto mysql

:mysql
set "MODULE=mysql"
goto run
:elasticsearch
set "MODULE=elasticsearch"
goto run
:silo
set "MODULE=silo"
goto run
:valkey
set "MODULE=valkey"
goto run
:llama_cpp
set "MODULE=llama-cpp"
goto run
:paddleocr
set "MODULE=paddleocr"
goto run
:ragflow
set "MODULE=ragflow"
goto run
:web
set "MODULE=web"
goto run

:run
echo.
call "%~dp0modules\%MODULE%\PREPARE-ONLINE.bat"
set "RESULT=%ERRORLEVEL%"
echo.
if not "%RESULT%"=="0" echo [ERROR] %MODULE% preparation failed with exit code %RESULT%.
if "%RESULT%"=="0" echo [OK] %MODULE% preparation completed.
echo.
pause
goto menu
