@echo off
REM Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
REM SPDX-License-Identifier: MIT

REM Start Lemonade Server in background for Windows CMD environments
REM Usage: start-lemonade.bat [model-name] [port] [ctx-size]

setlocal EnableDelayedExpansion

set MODEL_NAME=%1
set PORT=%2
set CTX_SIZE=%3

if "%MODEL_NAME%"=="" set MODEL_NAME=Qwen3-0.6B-GGUF
if "%PORT%"=="" set PORT=8000
if "%CTX_SIZE%"=="" set CTX_SIZE=8192

echo ==========================================
echo    LEMONADE SERVER SETUP
echo ==========================================
echo.

REM Kill any process using the port
echo === Checking Port %PORT% ===
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
    echo [WARN] Port %PORT% in use by PID: %%a - killing...
    taskkill /F /PID %%a >nul 2>&1
    timeout /t 2 >nul
)
echo [OK] Port %PORT% available
echo.

REM Start server in background
echo === Starting Lemonade Server ===
set GGML_VK_DISABLE_COOPMAT=1
start /B "" ".venv\Scripts\lemonade-server-dev.exe" serve --port %PORT% --ctx-size %CTX_SIZE% --no-tray > lemonade-server.log 2>&1
echo [OK] Server started in background
echo      Logs: lemonade-server.log
echo.

REM Wait for server
echo === Waiting for Server ===
set MAX_WAIT=60
set WAITED=0
:WAIT_LOOP
timeout /t 2 /nobreak >nul
set /a WAITED+=2

REM Try health check
curl -s "http://localhost:%PORT%/api/v1/health" >nul 2>&1
if %ERRORLEVEL%==0 (
    echo [OK] Server ready (waited %WAITED%s^)
    goto SERVER_READY
)

echo Waiting... (%WAITED%/%MAX_WAIT%s^)
if %WAITED% LSS %MAX_WAIT% goto WAIT_LOOP

echo [ERROR] Server failed to start
exit /b 1

:SERVER_READY
echo.

REM Pull model
echo === Pulling Model: %MODEL_NAME% ===
".venv\Scripts\lemonade-server-dev.exe" pull %MODEL_NAME%
echo.

REM Wait for files
echo Waiting 5 seconds for model files...
timeout /t 5 /nobreak >nul

REM Load model
echo === Loading Model: %MODEL_NAME% ===
curl -s -X POST "http://localhost:%PORT%/api/v1/load" ^
    -H "Content-Type: application/json" ^
    -d "{\"model_name\": \"%MODEL_NAME%\"}" ^
    --max-time 120
echo.
echo.

REM Wait for initialization
echo Waiting 10 seconds for initialization...
timeout /t 10 /nobreak >nul

echo ==========================================
echo OK LEMONADE SERVER READY
echo ==========================================
echo Model: %MODEL_NAME%
echo Port: %PORT%
echo.

exit /b 0
