#!/usr/bin/env bash
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Generate the Windows resource objects the Go linker embeds into the TUI.
#
# Go links no Windows resource unless a .syso sits next to the main package, so
# without this the shipped gaia-tui.exe has no icon and no version metadata --
# Explorer draws it with the generic console glyph and the file's Details tab is
# blank. The linker picks a .syso up by its GOOS_GOARCH filename suffix, so both
# targets are generated here and each build links only its own.
#
# Output is generated, never committed (see tui/.gitignore).

set -euo pipefail

usage() {
  cat >&2 <<'EOF'
usage: gen-winres.sh --version <version> [--icon <path>]

  --version  version string stamped into the resource (e.g. 0.23.0, v0.23.0, dev)
  --icon     .ico to embed (default: ../src/gaia/img/gaia.ico relative to tui/)
EOF
  exit 2
}

die() {
  echo "gen-winres.sh: $1" >&2
  shift
  for line in "$@"; do echo "  ${line}" >&2; done
  exit 1
}

VERSION=""
ICON=""

# Each flag guards its own `shift 2`: without the guard a trailing --version
# makes shift fail, which under `set -e` aborts with no message at all.
while [ $# -gt 0 ]; do
  case "$1" in
    --version) [ $# -ge 2 ] || die "--version requires a value." "Example: --version 0.23.0"; VERSION="$2"; shift 2 ;;
    --icon)    [ $# -ge 2 ] || die "--icon requires a value." "Example: --icon ../src/gaia/img/gaia.ico"; ICON="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "gen-winres.sh: unknown argument '$1'" >&2; usage ;;
  esac
done

[ -n "${VERSION}" ] || { echo "gen-winres.sh: --version is required" >&2; usage; }

# Resolve everything against tui/, so the script works from any cwd.
TUI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${TUI_DIR}"

: "${ICON:=../src/gaia/img/gaia.ico}"
if [ ! -f "${ICON}" ]; then
  echo "gen-winres.sh: icon not found at '${ICON}' (resolved from ${TUI_DIR}). The" \
       "GAIA robot icon lives at src/gaia/img/gaia.ico in the repo root; pass" \
       "--icon if you are building from a tree that does not have it." >&2
  exit 1
fi

CONFIG="cmd/gaia/versioninfo.json"
[ -f "${CONFIG}" ] || { echo "gen-winres.sh: missing ${CONFIG} in ${TUI_DIR}" >&2; exit 1; }

# The PE VERSIONINFO block stores the version as four integers, so a version
# that is not x.y.z has no numeric form. The human-readable StringFileInfo
# fields below still carry ${VERSION} verbatim, so nothing is hidden -- a `dev`
# build simply reports 0.0.0.0 in the numeric field Explorer sorts on.
QUAD="$(printf '%s' "${VERSION}" | sed -n 's/^v\{0,1\}\([0-9][0-9]*\)\.\([0-9][0-9]*\)\.\([0-9][0-9]*\).*$/\1 \2 \3/p')"
if [ -n "${QUAD}" ]; then
  # shellcheck disable=SC2086 # deliberate word split into three fields
  set -- ${QUAD}
  MAJOR="$1"; MINOR="$2"; PATCH="$3"
else
  MAJOR=0; MINOR=0; PATCH=0
fi

gen() {
  local goarch="$1"; shift
  local out="cmd/gaia/resource_windows_${goarch}.syso"
  rm -f "${out}"
  go tool goversioninfo \
    "$@" \
    -o "${out}" \
    -icon "${ICON}" \
    -product-version "${VERSION}" \
    -file-version "${VERSION}" \
    -ver-major "${MAJOR}" -ver-minor "${MINOR}" -ver-patch "${PATCH}" -ver-build 0 \
    -product-ver-major "${MAJOR}" -product-ver-minor "${MINOR}" \
    -product-ver-patch "${PATCH}" -product-ver-build 0 \
    "${CONFIG}"
  # goversioninfo exits 0 on some malformed inputs after writing nothing useful,
  # and a zero-byte .syso links silently -- the icon just never appears.
  [ -s "${out}" ] || { echo "gen-winres.sh: ${out} was not written or is empty" >&2; exit 1; }
  echo "generated ${out} ($(wc -c < "${out}" | tr -d ' ') bytes, version ${VERSION})"
}

gen amd64 -64
gen arm64 -64 -arm
