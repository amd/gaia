#!/usr/bin/env bash
# Guarded pytest runner: refuses to run unless `gaia` resolves to THIS worktree.
# A mixed/MSYS PYTHONPATH silently falls through to the main checkout, and every
# result then measures unmodified code (cost an hour once — see the plan doc).
W='C:\Users\14255\Work\gaia\.claudia-worktrees\claudia-task-25e62f25'
PY='C:\Users\14255\Work\gaia\.venv\Scripts\python.exe'
export PYTHONPATH="$W\src;$W\hub\agents\chat\python;$W\hub\agents\gaia\python"
export PYTHONIOENCODING=utf-8
resolved=$("$PY" -c "import gaia,sys;sys.stdout.write(gaia.__file__)")
case "$resolved" in
  *claudia-task-25e62f25*) ;;
  *) echo "ABORT: gaia resolves to $resolved (not this worktree)"; exit 2;;
esac
echo "[guard] gaia -> $resolved"
exec "$PY" -m pytest "$@"
