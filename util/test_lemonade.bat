@echo off
setlocal enabledelayedexpansion

:: Default server address
set SERVER=http://localhost:8000
set WS_SERVER=ws://localhost:8000/ws

echo.
echo ============================================
echo  Testing Lemonade Server Endpoints
echo ============================================
echo Server: %SERVER%
echo WebSocket: %WS_SERVER%
echo.
echo.

:: Test health endpoint
echo [1/4] Testing /health endpoint...
curl -s %SERVER%/health > health_response.json
if %ERRORLEVEL% EQU 0 (
    findstr /C:"model_loaded" health_response.json >nul
    if %ERRORLEVEL% EQU 0 (
        echo [SUCCESS] Health check passed
        echo.
        echo Response:
        type health_response.json
        echo.
    ) else (
        echo [FAILED] Health check failed - model_loaded field not found
        echo.
        echo Response:
        type health_response.json
        echo.
    )
) else (
    echo [FAILED] Health check failed
    echo.
)
echo.

:: Test HTTP completion endpoint
echo [2/4] Testing HTTP /completion endpoint...
curl -s -X POST %SERVER%/completion ^
  -H "Content-Type: application/json" ^
  -d "{\"prompt\":\"Say hello!\",\"max_tokens\":10,\"temperature\":0.7}" ^
  > completion_response.json
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] Completion request sent
    echo.
    echo Response:
    type completion_response.json
    echo.
) else (
    echo [FAILED] Completion request failed
    echo.
)
echo.

:: Test HTTP chat completion endpoint
echo [3/4] Testing HTTP /chat/completion endpoint...
curl -s -X POST %SERVER%/chat/completion ^
  -H "Content-Type: application/json" ^
  -d "{\"messages\":[{\"role\":\"user\",\"content\":\"Say hello!\"}],\"max_tokens\":10,\"temperature\":0.7}" ^
  > chat_response.json
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] Chat completion request sent
    echo.
    echo Response:
    type chat_response.json
    echo.
) else (
    echo [FAILED] Chat completion request failed
    echo.
)
echo.

:: Test WebSocket completion endpoint
echo [4/4] Testing WebSocket completion...
python -c "import websocket; ws = websocket.create_connection('%WS_SERVER%'); ws.send('{\"prompt\":\"Say hello!\",\"max_tokens\":10,\"temperature\":0.7}'); print(ws.recv()); ws.close()" > ws_completion_response.json
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] WebSocket completion request sent
    echo.
    echo Response:
    type ws_completion_response.json
    echo.
) else (
    echo [FAILED] WebSocket completion request failed
    echo.
)
echo.

:: Clean up temporary files
del health_response.json 2>nul
del completion_response.json 2>nul
del chat_response.json 2>nul
del ws_completion_response.json 2>nul

echo.
echo ============================================
echo  Test Summary
echo ============================================
echo.
echo Note: Both HTTP and WebSocket endpoints are tested.
echo HTTP endpoints will be implemented later.
echo WebSocket endpoint is the primary interface for text generation.
echo.