#!/bin/bash
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Start the GAIA Agent UI (backend + frontend dev server)
# Usage: ./scripts/start-agent-ui.sh [--backend-only] [--frontend-only] [--port PORT] [--dev-port PORT]

set -e

# ── Defaults ──────────────────────────────────────────────────────────
BACKEND_PORT=4200
FRONTEND_PORT=5174
RUN_BACKEND=true
RUN_FRONTEND=true
DEBUG=true

# ── Parse arguments ───────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case $1 in
        --backend-only)  RUN_FRONTEND=false; shift ;;
        --frontend-only) RUN_BACKEND=false;  shift ;;
        --port)          BACKEND_PORT="$2";  shift 2 ;;
        --dev-port)      FRONTEND_PORT="$2"; shift 2 ;;
        --no-debug)      DEBUG=false;        shift ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Start the GAIA Agent UI backend and/or frontend dev server."
            echo ""
            echo "Options:"
            echo "  --backend-only   Start only the FastAPI backend"
            echo "  --frontend-only  Start only the Vite dev server"
            echo "  --port PORT      Backend port (default: 4200)"
            echo "  --dev-port PORT  Frontend dev port (default: 5174)"
            echo "  --no-debug       Disable debug logging"
            echo "  -h, --help       Show this help"
            echo ""
            echo "Prerequisite: Lemonade Server must be running (lemonade-server serve)"
            exit 0
            ;;
        *)
            echo "Unknown option: $1 (use -h for help)"
            exit 1
            ;;
    esac
done

# ── Resolve project root ─────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WEBUI_DIR="$PROJECT_ROOT/src/gaia/apps/webui"

echo "=========================================="
echo "   GAIA Agent UI"
echo "=========================================="
echo "  Project: $PROJECT_ROOT"
echo "  Backend: http://localhost:$BACKEND_PORT"
if [ "$RUN_FRONTEND" = true ]; then
    echo "  Frontend: http://localhost:$FRONTEND_PORT"
fi
echo ""

# ── Check prerequisites ──────────────────────────────────────────────
if [ "$RUN_BACKEND" = true ]; then
    if ! command -v uv &> /dev/null; then
        echo "[ERROR] 'uv' not found. Install it: https://docs.astral.sh/uv/"
        exit 1
    fi

    # Check that gaia is installed (editable install)
    if ! (cd "$PROJECT_ROOT" && uv run python -c "import gaia" 2>/dev/null); then
        echo "[ERROR] GAIA is not installed. Run the following from the project root:"
        echo ""
        echo "  cd $PROJECT_ROOT"
        echo "  uv venv && uv pip install -e \".[dev,rag]\""
        echo ""
        echo "See docs/reference/dev.mdx for full setup instructions."
        exit 1
    fi
fi

if [ "$RUN_FRONTEND" = true ]; then
    if ! command -v npm &> /dev/null; then
        echo "[ERROR] 'npm' not found. Install Node.js: https://nodejs.org/"
        exit 1
    fi
    if [ ! -d "$WEBUI_DIR/node_modules" ]; then
        echo "[INFO] Installing frontend dependencies..."
        (cd "$WEBUI_DIR" && npm install)
    fi
fi

# ── Cleanup function ─────────────────────────────────────────────────
BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
    echo ""
    echo "Shutting down..."
    if [ -n "$BACKEND_PID" ] && kill -0 "$BACKEND_PID" 2>/dev/null; then
        echo "  Stopping backend (PID $BACKEND_PID)"
        kill "$BACKEND_PID" 2>/dev/null || true
    fi
    if [ -n "$FRONTEND_PID" ] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
        echo "  Stopping frontend (PID $FRONTEND_PID)"
        kill "$FRONTEND_PID" 2>/dev/null || true
    fi
    echo "Done."
    exit 0
}

trap cleanup SIGINT SIGTERM

# ── Start backend ────────────────────────────────────────────────────
if [ "$RUN_BACKEND" = true ]; then
    echo "=== Starting Backend ==="

    # Kill existing process on the port
    if command -v lsof &> /dev/null; then
        EXISTING_PID=$(lsof -ti ":$BACKEND_PORT" 2>/dev/null || true)
        if [ -n "$EXISTING_PID" ]; then
            echo "[WARN] Port $BACKEND_PORT in use (PID $EXISTING_PID) — killing"
            kill "$EXISTING_PID" 2>/dev/null || true
            sleep 1
        fi
    fi

    DEBUG_FLAG=""
    if [ "$DEBUG" = true ]; then
        DEBUG_FLAG="--debug"
    fi

    (cd "$PROJECT_ROOT" && uv run python -m gaia.ui.server $DEBUG_FLAG --port "$BACKEND_PORT") &
    BACKEND_PID=$!
    echo "[OK] Backend started (PID $BACKEND_PID)"

    # Wait for health check
    echo "  Waiting for backend..."
    MAX_WAIT=30
    WAITED=0
    while [ $WAITED -lt $MAX_WAIT ]; do
        sleep 1
        WAITED=$((WAITED + 1))
        if curl -s "http://localhost:$BACKEND_PORT/api/health" > /dev/null 2>&1; then
            echo "[OK] Backend ready (${WAITED}s)"
            break
        fi
    done

    if [ $WAITED -ge $MAX_WAIT ]; then
        echo "[ERROR] Backend failed to start within ${MAX_WAIT}s"
        cleanup
        exit 1
    fi
    echo ""
fi

# ── Start frontend ───────────────────────────────────────────────────
if [ "$RUN_FRONTEND" = true ]; then
    echo "=== Starting Frontend ==="
    (cd "$WEBUI_DIR" && npm run dev -- --port "$FRONTEND_PORT") &
    FRONTEND_PID=$!
    echo "[OK] Frontend started (PID $FRONTEND_PID)"

    # Wait briefly for Vite to spin up
    sleep 2
    if curl -s -o /dev/null "http://localhost:$FRONTEND_PORT/" 2>/dev/null; then
        echo "[OK] Frontend ready"
    fi
    echo ""
fi

# ── Summary ──────────────────────────────────────────────────────────
echo "=========================================="
echo "  GAIA Agent UI is running!"
echo "=========================================="
if [ "$RUN_FRONTEND" = true ]; then
    echo "  Open: http://localhost:$FRONTEND_PORT"
else
    echo "  API:  http://localhost:$BACKEND_PORT"
fi
echo "  Press Ctrl+C to stop"
echo ""

# ── Wait for processes ───────────────────────────────────────────────
wait
