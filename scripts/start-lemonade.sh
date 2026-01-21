#!/bin/bash
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

set -e

# Parse arguments
MODEL_NAME=""
ADDITIONAL_MODELS=""
PORT=8000
CTX_SIZE=32768
INIT_WAIT_TIME=10
CLEAR_CACHE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --model-name)
            MODEL_NAME="$2"
            shift 2
            ;;
        --additional-models)
            ADDITIONAL_MODELS="$2"
            shift 2
            ;;
        --port)
            PORT="$2"
            shift 2
            ;;
        --ctx-size)
            CTX_SIZE="$2"
            shift 2
            ;;
        --init-wait-time)
            INIT_WAIT_TIME="$2"
            shift 2
            ;;
        --clear-cache)
            CLEAR_CACHE=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

if [ -z "$MODEL_NAME" ]; then
    echo "Error: --model-name is required"
    exit 1
fi

echo "=========================================="
echo "   LEMONADE SERVER SETUP"
echo "=========================================="
echo ""

# Check installation
echo "=== Checking Installation ==="
LEMONADE_EXE=".venv/bin/lemonade-server-dev"
if [ ! -f "$LEMONADE_EXE" ]; then
    echo "[ERROR] lemonade-server-dev not found at: $LEMONADE_EXE"
    exit 1
fi
echo "[OK] Found: $LEMONADE_EXE"
echo ""

# Clear cache if requested
if [ "$CLEAR_CACHE" = true ]; then
    echo "=== Clearing Model Cache ==="
    CACHE_DIR="$HOME/.cache/lemonade-server/models"
    if [ -d "$CACHE_DIR" ]; then
        echo "Removing: $CACHE_DIR"
        rm -rf "$CACHE_DIR"
    fi
    echo ""
fi

# Start server
echo "=== Starting Server ==="
export GGML_VK_DISABLE_COOPMAT=1
nohup "$LEMONADE_EXE" serve --port "$PORT" --ctx-size "$CTX_SIZE" --no-tray \
    > lemonade-server-stdout.log 2> lemonade-server-stderr.log &
SERVER_PID=$!
echo "[OK] Started server PID: $SERVER_PID"
echo "     Logs: lemonade-server-stdout.log, lemonade-server-stderr.log"

# Export process ID for cleanup
if [ -n "$GITHUB_OUTPUT" ]; then
    echo "lemonade-process-id=$SERVER_PID" >> "$GITHUB_OUTPUT"
fi
if [ -n "$GITHUB_ENV" ]; then
    echo "LEMONADE_PROCESS_ID=$SERVER_PID" >> "$GITHUB_ENV"
fi
echo ""

# Wait for server
echo "=== Waiting for Server ==="
MAX_WAIT=60
WAITED=0
READY=false

while [ $WAITED -lt $MAX_WAIT ] && [ "$READY" = false ]; do
    sleep 2
    WAITED=$((WAITED + 2))

    if curl -s "http://localhost:${PORT}/api/v1/health" > /dev/null 2>&1; then
        echo "[OK] Server ready (waited ${WAITED}s)"
        READY=true
    else
        echo "Waiting... (${WAITED}/${MAX_WAIT}s)"
    fi
done

if [ "$READY" = false ]; then
    echo "[ERROR] Server failed to start"
    exit 1
fi
echo ""

# Pull primary model
echo "=== Pulling Primary Model: $MODEL_NAME ==="
"$LEMONADE_EXE" pull "$MODEL_NAME"
echo ""

# Pull additional models
if [ -n "$ADDITIONAL_MODELS" ]; then
    echo "=== Pulling Additional Models ==="
    IFS=',' read -ra MODELS <<< "$ADDITIONAL_MODELS"
    for model in "${MODELS[@]}"; do
        model=$(echo "$model" | xargs)  # trim whitespace
        echo "Pulling: $model"
        "$LEMONADE_EXE" pull "$model" || echo "  [WARN] Pull failed"
    done
    echo ""
fi

# Wait for server to stabilize after pull (retry health check)
echo "Waiting for server to stabilize after pull..."
MAX_RETRY=10
RETRY=0
HEALTHY=false

while [ $RETRY -lt $MAX_RETRY ] && [ "$HEALTHY" = false ]; do
    sleep 2
    RETRY=$((RETRY + 1))

    if curl -s "http://localhost:${PORT}/api/v1/health" > /dev/null 2>&1; then
        echo "[OK] Server responsive after pull"
        HEALTHY=true
    else
        echo "Waiting for server after pull... (${RETRY}/${MAX_RETRY})"
    fi
done

if [ "$HEALTHY" = false ]; then
    echo "[ERROR] Server not responding after pull"
    exit 1
fi
echo ""

# Load model
echo "=== Loading Model: $MODEL_NAME ==="
LOAD_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "http://localhost:${PORT}/api/v1/load" \
    -H "Content-Type: application/json" \
    -d "{\"model_name\": \"$MODEL_NAME\"}" \
    --max-time 120)

HTTP_CODE=$(echo "$LOAD_RESPONSE" | tail -n1)
RESPONSE_BODY=$(echo "$LOAD_RESPONSE" | head -n-1)

if [ "$HTTP_CODE" != "200" ]; then
    echo "[ERROR] Load failed with HTTP $HTTP_CODE"
    echo "Response: $RESPONSE_BODY"
    echo ""
    echo "=== Server Logs (last 100 lines) ==="
    tail -100 lemonade-server-stdout.log 2>/dev/null || true
    tail -100 lemonade-server-stderr.log 2>/dev/null || true
    exit 1
fi

echo "[OK] Model loaded: $RESPONSE_BODY"
echo ""

# Wait for initialization
echo "Waiting $INIT_WAIT_TIME seconds for model initialization..."
sleep "$INIT_WAIT_TIME"

echo "=========================================="
echo "✅ LEMONADE SERVER READY"
echo "=========================================="
echo "Model: $MODEL_NAME"
echo "Port: $PORT"
echo "Process ID: $SERVER_PID"
echo ""

exit 0
