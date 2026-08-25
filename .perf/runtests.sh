#!/usr/bin/env bash
# Guarded pytest runner: refuses to run unless `gaia` resolves to THIS checkout.
# A mixed/MSYS PYTHONPATH silently falls through to another checkout, and every
# result then measures unmodified code.
set -uo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

# Windows needs native paths and ';' — an MSYS '/c/...' entry on sys.path matches nothing.
case "${MSYSTEM:-}${OS:-}" in
  MINGW* | MSYS* | *Windows_NT)
    root=$(cd "$root" && { pwd -W 2>/dev/null || pwd; })
    sep=';'
    ;;
  *) sep=':' ;;
esac

PY=${GAIA_PYTHON:-python}
export PYTHONPATH="$root/src$sep$root/hub/agents/chat/python$sep$root/hub/agents/gaia/python"
export PYTHONIOENCODING=utf-8

resolved=$("$PY" -c "import gaia,sys;sys.stdout.write(gaia.__file__)") || exit 2

norm() { printf '%s' "$1" | tr 'A-Z\\' 'a-z/'; }
case "$(norm "$resolved")" in
  "$(norm "$root")"/*) ;;
  *) echo "ABORT: gaia resolves to $resolved (not under $root)"; exit 2 ;;
esac

echo "[guard] gaia -> $resolved"
exec "$PY" -m pytest "$@"
