#!/usr/bin/env bash
# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# The frozen gaia, freshly built, sets up and talks to a REAL Lemonade on this
# host: no fake model server, no source checkout. rc13 passed every check and
# still could not start its engine on users' Linux PCs, because every real
# Lemonade in CI ran from a source install.
#
#   real_engine_check.sh <path to the frozen gaia> <scratch dir>
#
# Runs in a cold GAIA_HOME under <scratch dir>, with the tiny chat model so a
# hosted runner finishes in minutes. Prints lemond.log on any failure.

set -euo pipefail

GAIA="$1"
SCRATCH="$2"
CHAT_MODEL="${CHAT_MODEL:-Qwen3-0.6B-GGUF}"

[ -x "${GAIA}" ] || { echo "::error::no frozen gaia at ${GAIA}"; exit 1; }
export GAIA_HOME="${SCRATCH}/gaia-home"
rm -rf "${GAIA_HOME}"
mkdir -p "${GAIA_HOME}"
unset LEMONADE_BASE_URL GAIA_LEMONADE_EMBEDDED || true

dump() {
  echo "--- lemond.log (last 80 lines) ---"
  tail -n 80 "${GAIA_HOME}/lemonade/lemond.log" 2>/dev/null || echo "(no lemond.log)"
  # The daemon first: it would restart the Lemonade stopped below.
  "${GAIA}" daemon stop >/dev/null 2>&1 || true
  "${GAIA}" lemonade embedded stop >/dev/null 2>&1 || true
}
fail() { echo "::error::$*"; dump; exit 1; }

step() { echo; echo "=== $* ==="; }

step "frozen gaia"
"${GAIA}" --version || fail "the frozen gaia does not run"

step "first run: choose the small chat model"
"${GAIA}" config set default_model "${CHAT_MODEL}" || fail "gaia config set failed"

step "gaia init (real Lemonade, real download)"
"${GAIA}" init --profile gaia --yes --skip-webui-build \
  || fail "gaia init could not set up the real Lemonade from the frozen gaia"

step "embedded Lemonade is running"
"${GAIA}" lemonade embedded status || fail "gaia lemonade embedded status failed"

step "one real chat turn through the frozen gaia"
answer="$("${GAIA}" llm --model "${CHAT_MODEL}" "Reply with the single word OK." 2>&1)" \
  || { echo "${answer}"; fail "gaia llm failed against the real Lemonade"; }
echo "${answer}"
[ -n "$(echo "${answer}" | tr -d '[:space:]')" ] || fail "gaia llm answered nothing"

step "the daemon supervises the same Lemonade"
"${GAIA}" daemon start || fail "gaia daemon start failed"
"${GAIA}" daemon status || fail "gaia daemon status failed"
"${GAIA}" daemon stop || fail "gaia daemon stop failed"

step "stop"
"${GAIA}" lemonade embedded stop || fail "gaia lemonade embedded stop failed"
echo "real engine check passed"
