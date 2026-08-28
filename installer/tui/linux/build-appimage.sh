#!/usr/bin/env bash
# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Packages the flagship GAIA agent as an AppImage — the distro-independent
# option for anyone not on a Debian-family system.
#
#   ./installer/tui/linux/build-appimage.sh \
#       --payload installer/tui/payload --version 0.1.1 --arch x64 --out dist-installer
#
# --payload must already hold the SHA-256-verified gaia-tui and gaia-agent that
# installer/tui/fetch_payload.py stages. This script packages; it never fetches
# the payload, so there is exactly one place a binary can enter the installer.
#
# Two build inputs ARE fetched here, and both are pinned by version and SHA-256:
# appimagetool, and the AppImage type-2 runtime it stamps into the output. The
# runtime matters most — it is the ELF that executes first on the user's machine,
# before a single byte of our verified payload runs. appimagetool downloads one
# itself when --runtime-file is absent, which would leave exactly one unverified
# link in an otherwise hash-pinned chain, so we supply it explicitly.
#
# The runtime is pinned to a DATED tag, not `continuous`: `continuous` is a
# moving target whose bytes change under a fixed URL, so a digest pinned against
# it would start failing for no reason anyone could act on.

set -euo pipefail

APPIMAGETOOL_VERSION="1.9.1"
APPIMAGETOOL_SHA256="ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"
RUNTIME_TAG="20251108"
RUNTIME_SHA256="2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d"

PAYLOAD=""
TARGET_ARCH=""
VERSION=""
OUT=""

usage() {
  echo "usage: $0 --payload DIR --version X.Y.Z --arch x64 --out DIR" >&2
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
    --arch) need_value "$@"; TARGET_ARCH="$2"; shift 2 ;;
    --out) need_value "$@"; OUT="$2";     shift 2 ;;
    *) echo "error: unknown argument '$1'." >&2; usage; exit 2 ;;
  esac
done

[ -n "${PAYLOAD}" ] || { echo "error: --payload is required." >&2; usage; exit 2; }
[ -n "${VERSION}" ] || { echo "error: --version is required." >&2; usage; exit 2; }
[ -n "${TARGET_ARCH}" ] || { echo "error: --arch is required." >&2; usage; exit 2; }
[ -n "${OUT}" ]     || { echo "error: --out is required." >&2; usage; exit 2; }

case "${TARGET_ARCH}" in
  x64) TOOL_ARCH="x86_64" ;;
  *) echo "error: --arch must be x64; the sidecar is not frozen for any other Linux architecture (see binaries.lock.json)." >&2; exit 2 ;;
esac

for bin in gaia-tui gaia-agent; do
  if [ ! -f "${PAYLOAD}/${bin}" ]; then
    echo "error: ${PAYLOAD}/${bin} is missing. Stage the payload first: python installer/tui/fetch_payload.py --lock binaries.lock.json --platform linux-${TARGET_ARCH} --dest ${PAYLOAD}" >&2
    exit 1
  fi
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
ICON="${REPO_ROOT}/installer/tui/gaia-256.png"
[ -f "${ICON}" ] || { echo "error: icon not found at ${ICON}." >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

# --- appimagetool, pinned ---------------------------------------------
TOOL="${WORK}/appimagetool"
TOOL_URL="https://github.com/AppImage/appimagetool/releases/download/${APPIMAGETOOL_VERSION}/appimagetool-${TOOL_ARCH}.AppImage"
echo "Fetching appimagetool ${APPIMAGETOOL_VERSION}"
curl -fsSL --retry 3 --retry-delay 5 -o "${TOOL}" "${TOOL_URL}"
echo "${APPIMAGETOOL_SHA256}  ${TOOL}" | sha256sum -c - || {
  echo "error: appimagetool ${APPIMAGETOOL_VERSION} does not match its pinned SHA-256. Either upstream re-cut the release or the download was tampered with — do not build against it. Verify at ${TOOL_URL} and update APPIMAGETOOL_SHA256 deliberately." >&2
  exit 1
}
chmod 0755 "${TOOL}"

# --- the AppImage runtime, pinned -------------------------------------
RUNTIME="${WORK}/runtime-${TOOL_ARCH}"
RUNTIME_URL="https://github.com/AppImage/type2-runtime/releases/download/${RUNTIME_TAG}/runtime-${TOOL_ARCH}"
echo "Fetching AppImage runtime ${RUNTIME_TAG}"
curl -fsSL --retry 3 --retry-delay 5 -o "${RUNTIME}" "${RUNTIME_URL}"
echo "${RUNTIME_SHA256}  ${RUNTIME}" | sha256sum -c - || {
  echo "error: the AppImage runtime at ${RUNTIME_URL} does not match its pinned SHA-256. This ELF runs before anything else on the user's machine — do not ship an unverified one. Confirm upstream re-tagged, then update RUNTIME_SHA256 deliberately." >&2
  exit 1
}

# --- AppDir -----------------------------------------------------------
APPDIR="${WORK}/GAIA.AppDir"
install -D -m 0755 "${PAYLOAD}/gaia-tui"   "${APPDIR}/usr/bin/gaia-tui"
install -D -m 0755 "${PAYLOAD}/gaia-agent" "${APPDIR}/usr/bin/gaia-agent"
install -D -m 0644 "${ICON}" "${APPDIR}/gaia.png"
install -D -m 0644 "${ICON}" "${APPDIR}/usr/share/icons/hicolor/256x256/apps/gaia.png"

# gaia-tui spawns gaia-agent as a subprocess by name, so the AppDir's bin
# directory has to be on PATH before the exec — otherwise the TUI starts and
# then fails to find its own agent.
cat > "${APPDIR}/AppRun" <<'APPRUN'
#!/bin/bash
set -euo pipefail
HERE="$(dirname "$(readlink -f "${0}")")"
export PATH="${HERE}/usr/bin:${PATH}"
exec "${HERE}/usr/bin/gaia-tui" "$@"
APPRUN
chmod 0755 "${APPDIR}/AppRun"

cat > "${APPDIR}/gaia.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=GAIA
GenericName=Local AI agent
Comment=Talk to the GAIA agent in your terminal — runs entirely on your machine
Exec=gaia-tui
Icon=gaia
Terminal=true
Categories=Utility;Development;
Keywords=ai;agent;llm;amd;ryzen;
DESKTOP

# --- Build ------------------------------------------------------------
mkdir -p "${OUT}"
APPIMAGE_NAME="gaia-${VERSION}-${TARGET_ARCH}.AppImage"
rm -f "${OUT}/${APPIMAGE_NAME}"

# CI containers have no FUSE, so appimagetool cannot mount itself; extracting
# and running is the standard way to use it there and produces the same output.
# ARCH is appimagetool's OWN input — it picks the runtime from it — which is why
# the CLI argument is held in TARGET_ARCH and never in ARCH.
export APPIMAGE_EXTRACT_AND_RUN=1
export ARCH="${TOOL_ARCH}"
"${TOOL}" --no-appstream --runtime-file "${RUNTIME}" "${APPDIR}" "${OUT}/${APPIMAGE_NAME}"

chmod 0755 "${OUT}/${APPIMAGE_NAME}"
echo "built ${OUT}/${APPIMAGE_NAME} ($(wc -c < "${OUT}/${APPIMAGE_NAME}") bytes)"
