#!/usr/bin/env bash
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Build the one GAIA Windows setup -- desktop app, terminal, or both -- with
# standalone makensis.
#
# Mirrors the CLI of installer/tui/macos/build-pkg.sh and
# installer/tui/linux/build-packages.sh so the three read the same way.
#
#   build-setup.sh --version 0.23.0 \
#                  --payload dist/payload \
#                  --out dist \
#                  --lemonade-msi installer/lemonade-server-minimal.msi \
#                  --fonts dist/fonts \
#                  --agent-ui-setup dist/gaia-agent-ui-0.23.0-x64-setup.exe
#
# --payload must already hold gaia-tui.exe, gaia-agent.exe and LICENSE.md.
# --fonts must hold the faces installer/tui/fetch_fonts.py staged and verified.
# --agent-ui-setup is the electron-builder setup for the desktop app component,
# as installer/tui/fetch_agent_ui_setup.py downloads it.
# Produces <out>/gaia-<version>-win-x64-setup.exe.

set -euo pipefail

usage() {
  cat >&2 <<'EOF'
usage: build-setup.sh --version <x.y.z> --payload <dir> --out <dir> --lemonade-msi <path> --fonts <dir> --agent-ui-setup <path>

  --version       version to stamp into the installer and its filename
  --payload       directory holding gaia-tui.exe, gaia-agent.exe and LICENSE.md
  --out           directory the setup .exe is written to
  --lemonade-msi  the pinned lemonade-server-minimal.msi to bundle
  --fonts         IBM Plex Mono faces staged by installer/tui/fetch_fonts.py
  --agent-ui-setup  gaia-agent-ui-<x.y.z>-x64-setup.exe, the desktop app component
  --icon          .ico to use (default: <repo>/src/gaia/img/gaia.ico)
EOF
  exit 2
}

die() {
  echo "build-setup.sh: $1" >&2
  shift
  for line in "$@"; do echo "  ${line}" >&2; done
  exit 1
}

VERSION="" PAYLOAD="" OUT="" LEMONADE_MSI="" ICON="" FONTS="" AGENT_UI_SETUP=""

# Each flag guards its own `shift 2`: without the guard a trailing --version
# makes shift fail, which under `set -e` aborts with no message at all.
while [ $# -gt 0 ]; do
  case "$1" in
    --version)      [ $# -ge 2 ] || die "--version requires a value." "Example: --version 0.23.0";                                 VERSION="$2";      shift 2 ;;
    --payload)      [ $# -ge 2 ] || die "--payload requires a value." "Example: --payload dist/payload";                           PAYLOAD="$2";      shift 2 ;;
    --out)          [ $# -ge 2 ] || die "--out requires a value." "Example: --out dist";                                           OUT="$2";          shift 2 ;;
    --lemonade-msi) [ $# -ge 2 ] || die "--lemonade-msi requires a value." "Example: --lemonade-msi installer/lemonade-server-minimal.msi"; LEMONADE_MSI="$2"; shift 2 ;;
    --fonts)        [ $# -ge 2 ] || die "--fonts requires a value." "Example: --fonts dist/fonts";                               FONTS="$2";        shift 2 ;;
    --agent-ui-setup) [ $# -ge 2 ] || die "--agent-ui-setup requires a value." "Example: --agent-ui-setup dist/gaia-agent-ui-0.23.0-x64-setup.exe"; AGENT_UI_SETUP="$2"; shift 2 ;;
    --icon)         [ $# -ge 2 ] || die "--icon requires a value." "Example: --icon src/gaia/img/gaia.ico";                        ICON="$2";         shift 2 ;;
    -h|--help)      usage ;;
    *) echo "build-setup.sh: unknown argument '$1'" >&2; usage ;;
  esac
done

missing=""
[ -n "${VERSION}" ]      || missing="${missing} --version"
[ -n "${PAYLOAD}" ]      || missing="${missing} --payload"
[ -n "${OUT}" ]          || missing="${missing} --out"
[ -n "${LEMONADE_MSI}" ] || missing="${missing} --lemonade-msi"
[ -n "${FONTS}" ]        || missing="${missing} --fonts"
[ -n "${AGENT_UI_SETUP}" ] || missing="${missing} --agent-ui-setup"
if [ -n "${missing}" ]; then
  echo "build-setup.sh: missing required argument(s):${missing}" >&2
  usage
fi

# VIProductVersion is four integers, and the .nsi builds it as ${VERSION}.0 so
# Explorer sorts setups correctly. Anything else makes makensis reject it, so
# reject it here where the message can name the argument.
if ! [[ "${VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  die "--version '${VERSION}' is not a valid version." \
      "Expected MAJOR.MINOR.PATCH with digits only, e.g. 0.23.0." \
      "Windows stores the setup's version as four integers, so a prerelease or" \
      "'v' prefix has no numeric form and would leave the setup reporting 0.0.0.0."
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/../../.." && pwd)"
: "${ICON:=${REPO}/src/gaia/img/gaia.ico}"

# Every input checked before makensis runs, so a missing file is one clear
# message rather than an NSIS compile error pointing at a generated path.
for f in "${PAYLOAD}/gaia-tui.exe" "${PAYLOAD}/gaia-agent.exe" "${PAYLOAD}/LICENSE.md"; do
  [ -f "${f}" ] || {
    echo "build-setup.sh: payload is incomplete -- '${f}' is missing." >&2
    echo "  --payload must hold gaia-tui.exe (cross-compiled from tui/), gaia-agent.exe" >&2
    echo "  (installer/tui/fetch_sidecar.py --platform win-x64) and the repo's LICENSE.md." >&2
    exit 1
  }
done
[ -f "${LEMONADE_MSI}" ] || {
  echo "build-setup.sh: --lemonade-msi '${LEMONADE_MSI}' does not exist. This installer is" >&2
  echo "  offline by contract, so the MSI must be downloaded and size-checked by the build" >&2
  echo "  first -- see the 'Download the pinned Lemonade MSI' step in" >&2
  echo "  .github/workflows/windows_setup.yml." >&2
  exit 1
}
[ -f "${ICON}" ] || { echo "build-setup.sh: icon '${ICON}' does not exist" >&2; exit 1; }
# Every file the committed pin names, faces and licence -- not a directory
# listing, which would accept a staging dir missing one.
FONT_FILES="$(grep -oE '"filename": "[^"]+"' "${HERE}/../fonts/fonts.lock.json" | cut -d'"' -f4)"
[ -n "${FONT_FILES}" ] || { echo "build-setup.sh: no filenames found in installer/tui/fonts/fonts.lock.json" >&2; exit 1; }
for f in ${FONT_FILES}; do
  [ -f "${FONTS}/${f}" ] || {
    echo "build-setup.sh: --fonts '${FONTS}' has no ${f}." >&2
    echo "  Stage the pinned IBM Plex Mono faces first:" >&2
    echo "    python installer/tui/fetch_fonts.py --out ${FONTS}" >&2
    exit 1
  }
done

# The name carries the version the setup reports while installing it, and is
# what electron-builder.yml's artifactName produces -- anything else is not a
# desktop app setup.
UI_NAME="$(basename "${AGENT_UI_SETUP}")"
if ! [[ "${UI_NAME}" =~ ^gaia-agent-ui-([0-9]+\.[0-9]+\.[0-9]+)-x64-setup\.exe$ ]]; then
  die "--agent-ui-setup '${AGENT_UI_SETUP}' is not a desktop app setup." \
      "Expected gaia-agent-ui-<x.y.z>-x64-setup.exe, as electron-builder names it." \
      "Download one with: python installer/tui/fetch_agent_ui_setup.py --release v<x.y.z> --out <dir>"
fi
AGENT_UI_VERSION="${BASH_REMATCH[1]}"
[ -f "${AGENT_UI_SETUP}" ] || die "--agent-ui-setup '${AGENT_UI_SETUP}' does not exist."
[ "$(head -c 2 "${AGENT_UI_SETUP}")" = "MZ" ] || die "--agent-ui-setup '${AGENT_UI_SETUP}' is not a Windows executable."

LEMONADE_VERSION="$(grep -oE 'LEMONADE_VERSION = "[^"]+"' "${REPO}/src/gaia/version.py" | cut -d'"' -f2)"
[ -n "${LEMONADE_VERSION}" ] || {
  echo "build-setup.sh: could not parse LEMONADE_VERSION from ${REPO}/src/gaia/version.py." >&2
  echo "  It is the single source of truth for the pinned Lemonade version shown during install." >&2
  exit 1
}

# GitHub's windows-latest ships NSIS at the first path; the second covers a
# local winget/choco install. Not found is an error, not a skip.
MAKENSIS="${MAKENSIS:-}"
if [ -z "${MAKENSIS}" ]; then
  for c in makensis "/c/Program Files (x86)/NSIS/makensis.exe" "/c/Program Files/NSIS/makensis.exe"; do
    if command -v "${c}" >/dev/null 2>&1; then MAKENSIS="${c}"; break; fi
  done
fi
[ -n "${MAKENSIS}" ] || {
  echo "build-setup.sh: makensis not found. Install NSIS (winget install NSIS.NSIS, or" >&2
  echo "  choco install nsis), or point MAKENSIS at it. Skipping the Windows installer is" >&2
  echo "  not an option -- a release that silently omits it looks green and ships nothing." >&2
  exit 1
}

mkdir -p "${OUT}"
OUTFILE_NAME="gaia-${VERSION}-win-x64-setup.exe"

# makensis is a native Windows binary and does not understand MSYS paths, so
# every path it receives goes through cygpath when one is available.
winpath() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi
}

# -WX: an NSIS warning is a shipped bug (an unreachable label, a define that
# expanded to nothing), and makensis exits 0 with one by default.
"${MAKENSIS}" -WX \
  "-DVERSION=${VERSION}" \
  "-DPAYLOAD_DIR=$(winpath "$(cd "${PAYLOAD}" && pwd)")" \
  "-DLEMONADE_MSI=$(winpath "$(cd "$(dirname "${LEMONADE_MSI}")" && pwd)/$(basename "${LEMONADE_MSI}")")" \
  "-DLEMONADE_VERSION=${LEMONADE_VERSION}" \
  "-DICON=$(winpath "${ICON}")" \
  "-DFONTS_DIR=$(winpath "$(cd "${FONTS}" && pwd)")" \
  "-DAGENT_UI_SETUP=$(winpath "$(cd "$(dirname "${AGENT_UI_SETUP}")" && pwd)/${UI_NAME}")" \
  "-DAGENT_UI_VERSION=${AGENT_UI_VERSION}" \
  "-DOUTFILE=$(winpath "$(cd "${OUT}" && pwd)")\\${OUTFILE_NAME}" \
  "$(winpath "${HERE}/gaia-setup.nsi")"

RESULT="${OUT}/${OUTFILE_NAME}"
[ -s "${RESULT}" ] || {
  echo "build-setup.sh: makensis exited 0 but ${RESULT} was not written." >&2
  exit 1
}
echo "built ${RESULT} ($(wc -c < "${RESULT}" | tr -d ' ') bytes, desktop app ${AGENT_UI_VERSION})"
