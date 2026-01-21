@echo off
REM Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
REM SPDX-License-Identifier: MIT
REM
REM Start Lemonade Server for Windows CMD environments
REM Usage: start-lemonade.bat [model-name] [port] [ctx-size]

setlocal EnableDelayedExpansion

set MODEL_NAME=%1
set PORT=%2
set CTX_SIZE=%3

if "%MODEL_NAME%"=="" set MODEL_NAME=Qwen3-0.6B-GGUF
if "%PORT%"=="" set PORT=8000
if "%CTX_SIZE%"=="" set CTX_SIZE=8192

echo === Lemonade Server Setup ===
echo Model: %MODEL_NAME%
echo Port: %PORT%
echo.

REM Kill any process using the port
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
    echo [WARN] Port %PORT% in use by PID: %%a - killing...
    taskkill /F /PID %%a >nul 2>&1
    timeout /t 2 >nul
)

REM Start server in background
set GGML_VK_DISABLE_COOPMAT=1
start /B "" ".venv\Scripts\lemonade-server-dev.exe" serve --port %PORT% --ctx-size %CTX_SIZE% --no-tray > lemonade-server.log 2>&1
echo [OK] Server started (logs: lemonade-server.log)

REM Wait for server
set MAX_WAIT=60
set WAITED=0
:WAIT_LOOP
ping 127.0.0.1 -n 3 >nul
set /a WAITED+=2
curl -s "http://localhost:%PORT%/api/v1/health" >nul 2>&1
if %ERRORLEVEL%==0 (
    echo [OK] Server ready (%WAITED%s)
    goto SERVER_READY
)
echo Waiting... (%WAITED%/%MAX_WAIT%s)
if %WAITED% LSS %MAX_WAIT% goto WAIT_LOOP
echo [ERROR] Server failed to start within %MAX_WAIT%s
type lemonade-server.log
exit /b 1

:SERVER_READY

REM Pull model
echo.
echo --- Pulling Model ---
".venv\Scripts\lemonade-server-dev.exe" pull %MODEL_NAME%
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to pull model
    exit /b 1
)

REM Wait for server to stabilize after pull (retry health check)
set RETRY=0
set MAX_RETRY=10
:HEALTH_RETRY
ping 127.0.0.1 -n 3 >nul
curl -s "http://localhost:%PORT%/api/v1/health" >nul 2>&1
if %ERRORLEVEL%==0 goto HEALTH_OK
set /a RETRY+=1
echo Waiting for server after pull... (%RETRY%/%MAX_RETRY%)
if %RETRY% LSS %MAX_RETRY% goto HEALTH_RETRY
echo [ERROR] Server not responding after pull
type lemonade-server.log
exit /b 1
:HEALTH_OK

REM Load model
echo.
echo --- Loading Model ---
curl -s -X POST "http://localhost:%PORT%/api/v1/load" ^
    -H "Content-Type: application/json" ^
    -d "{\"model_name\": \"%MODEL_NAME%\"}" ^
    --max-time 120 > load-response.txt 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to load model
    type load-response.txt
    type lemonade-server.log
    exit /b 1
)
echo [OK] Model loaded

REM Wait for initialization
ping 127.0.0.1 -n 11 >nul

REM Final health check
curl -s "http://localhost:%PORT%/api/v1/health" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Server not responding after load
    type lemonade-server.log
    exit /b 1
)

echo.
echo === Lemonade Server Ready ===
echo Model: %MODEL_NAME%
echo Port: %PORT%
exit /b 0
