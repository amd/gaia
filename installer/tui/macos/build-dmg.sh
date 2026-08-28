#!/usr/bin/env bash
# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Packages the flagship GAIA agent as a macOS disk image.
#
# The product is a terminal program, not a windowed app, so there is no .app to
# drag into Applications. The DMG carries the two verified binaries and a
# double-clickable installer that puts them on PATH — which is the thing a
# drag-to-Applications DMG would NOT do for a CLI.
#
#   ./installer/tui/macos/build-dmg.sh \
#       --payload installer/tui/payload --version 0.1.1 --arch arm64 --out dist-installer
#
# --payload must already hold the SHA-256-verified gaia-tui and gaia-agent that
# installer/tui/fetch_payload.py stages. This script packages; it never fetches,
# so there is exactly one place a binary can enter the installer.

set -euo pipefail

PAYLOAD=""
VERSION=""
ARCH=""
OUT=""

usage() {
  echo "usage: $0 --payload DIR --version X.Y.Z --arch arm64|x64 --out DIR" >&2
}

# Every flag takes a value. Without this guard `set -u` turns a value-less flag
# into "$2: unbound variable" — a bash internal error instead of one of ours.
need_value() {
  if [ "$#" -lt 2 ]; then
    echo "error: $1 needs a value." >&2
    usage
    exit 2
  fi
}

while [ $# -gt 0 ]; do
  case "$1" in
    --payload) need_value "$@"; PAYLOAD="$2"; shift 2 ;;
    --version) need_value "$@"; VERSION="$2"; shift 2 ;;
    --arch) need_value "$@"; ARCH="$2";    shift 2 ;;
    --out) need_value "$@"; OUT="$2";     shift 2 ;;
    *) echo "error: unknown argument '$1'." >&2; usage; exit 2 ;;
  esac
done

[ -n "${PAYLOAD}" ] || { echo "error: --payload is required." >&2; usage; exit 2; }
[ -n "${VERSION}" ] || { echo "error: --version is required." >&2; usage; exit 2; }
[ -n "${ARCH}" ]    || { echo "error: --arch is required." >&2; usage; exit 2; }
[ -n "${OUT}" ]     || { echo "error: --out is required." >&2; usage; exit 2; }

case "${ARCH}" in
  arm64|x64) ;;
  *) echo "error: --arch must be arm64 or x64, got '${ARCH}'. These are the only two the sidecar is frozen for." >&2; exit 2 ;;
esac

for bin in gaia-tui gaia-agent; do
  if [ ! -f "${PAYLOAD}/${bin}" ]; then
    echo "error: ${PAYLOAD}/${bin} is missing. Stage the payload first: python installer/tui/fetch_payload.py --lock binaries.lock.json --platform darwin-${ARCH} --dest ${PAYLOAD}" >&2
    exit 1
  fi
done

DMG_NAME="gaia-${VERSION}-${ARCH}.dmg"
STAGE="$(mktemp -d)"
VOLNAME="GAIA ${VERSION}"
trap 'rm -rf "${STAGE}"' EXIT

mkdir -p "${STAGE}/GAIA"
cp "${PAYLOAD}/gaia-tui" "${PAYLOAD}/gaia-agent" "${STAGE}/GAIA/"
chmod 0755 "${STAGE}/GAIA/gaia-tui" "${STAGE}/GAIA/gaia-agent"

# A .command file is what Finder runs on double-click. Everything it does is
# printed, and every failure names the fix — a silent PATH install that half
# worked is worse than a visible refusal.
cat > "${STAGE}/Install GAIA.command" <<'INSTALLER'
#!/bin/bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${HERE}/GAIA"
DEST="/usr/local/bin"

echo "Installing the GAIA agent into ${DEST}"

if [ ! -d "${DEST}" ] || [ ! -w "${DEST}" ]; then
  echo "${DEST} is not writable by $(whoami) — macOS will ask for your password."
  SUDO="sudo"
else
  SUDO=""
fi

${SUDO} mkdir -p "${DEST}"
for bin in gaia-tui gaia-agent; do
  ${SUDO} cp "${SRC}/${bin}" "${DEST}/${bin}"
  ${SUDO} chmod 0755 "${DEST}/${bin}"
  # A downloaded binary carries a quarantine attribute; without clearing it
  # Gatekeeper refuses the first exec with a dialog that has no "open anyway".
  ${SUDO} xattr -d com.apple.quarantine "${DEST}/${bin}" 2>/dev/null || true
  echo "  installed ${DEST}/${bin}"
done

echo ""
if command -v gaia-tui >/dev/null 2>&1; then
  echo "Done. Run:  gaia-tui"
else
  echo "Done, but ${DEST} is not on your PATH. Add it:"
  echo "  echo 'export PATH=\"${DEST}:\$PATH\"' >> ~/.zshrc && exec zsh"
fi
echo ""
echo "The agent needs Lemonade Server for local inference:"
echo "  https://amd-gaia.ai/docs/guides/install-agent"
echo ""
read -n 1 -s -r -p "Press any key to close."
INSTALLER
chmod 0755 "${STAGE}/Install GAIA.command"

cat > "${STAGE}/README.txt" <<READMEEOF
GAIA ${VERSION} — the flagship agent, for macOS (${ARCH})

Double-click "Install GAIA.command" to put gaia-tui and gaia-agent on your PATH,
then run "gaia-tui" in a terminal.

The agent runs entirely on this machine and needs Lemonade Server for local
inference. Setup: https://amd-gaia.ai/docs/guides/install-agent

Prefer to do it yourself? Copy GAIA/gaia-tui and GAIA/gaia-agent anywhere on your
PATH and clear their quarantine attribute:
    xattr -d com.apple.quarantine gaia-tui gaia-agent

This build is not notarized yet, so the first launch needs a right-click -> Open.
READMEEOF

mkdir -p "${OUT}"
rm -f "${OUT}/${DMG_NAME}"
hdiutil create \
  -volname "${VOLNAME}" \
  -srcfolder "${STAGE}" \
  -ov -format UDZO \
  "${OUT}/${DMG_NAME}"

echo "built ${OUT}/${DMG_NAME} ($(wc -c < "${OUT}/${DMG_NAME}") bytes)"
