#!/usr/bin/env bash
# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Checks an installed GAIA Flatpak without opening a window: its sandbox
# permissions, the frozen `gaia` core, and the bundled model server's shared
# libraries. The last two link against the Flatpak runtime, not the host.
#
# Usage: flatpak-smoke.sh [app-id]   (install the .flatpak first)

set -euo pipefail

APP_ID="${1:-ai.amd.gaia}"
RESOURCES="/app/lib/${APP_ID}/resources"

fail() {
  echo "::error::${1}" >&2
  exit 1
}

flatpak info "${APP_ID}" >/dev/null 2>&1 ||
  fail "Flatpak ${APP_ID} is not installed. Install the built .flatpak first: flatpak install --user <file>.flatpak"

echo "=== Sandbox permissions ==="
PERMISSIONS=$(flatpak info --show-permissions "${APP_ID}")
echo "${PERMISSIONS}"
# Each of these is a feature that silently stops working if the line is lost:
# saved data, downloaded models, the GPU and NPU, the network, and documents.
for want in ".gaia;" ".cache;" "devices=all;" "network" "xdg-documents"; do
  grep -qF -- "${want}" <<<"${PERMISSIONS}" ||
    fail "Flatpak ${APP_ID} is missing the '${want}' permission. Check flatpak.finishArgs in src/gaia/apps/webui/electron-builder.yml."
done

echo "=== Frozen core ==="
VERSION=$(flatpak run --command="${RESOURCES}/core/gaia" "${APP_ID}" --version) ||
  fail "The frozen gaia core does not run inside the ${APP_ID} sandbox. It links against the Flatpak runtime named in electron-builder.yml (flatpak.runtimeVersion)."
echo "gaia ${VERSION}"

echo "=== Model server libraries ==="
# shellcheck disable=SC2016  # expanded by the sandbox's shell, not this one
flatpak run --command=sh "${APP_ID}" -c '
  set -eu
  archive=$(ls "$1"/vendor/lemonade/lemonade-embeddable-*.tar.gz)
  work=$(mktemp -d)
  tar -xzf "${archive}" -C "${work}"
  lemond=$(find "${work}" -type f -name lemond | head -n1)
  [ -n "${lemond}" ] || { echo "no lemond in ${archive}" >&2; exit 1; }
  ldd "${lemond}"
  if ldd "${lemond}" | grep -q "not found"; then
    echo "lemond needs a library the Flatpak runtime does not have" >&2
    exit 1
  fi
' sh "${RESOURCES}" ||
  fail "The bundled model server cannot load inside the ${APP_ID} sandbox. See the library list above; the runtime is set by flatpak.runtimeVersion in electron-builder.yml."

echo "Flatpak smoke passed."
