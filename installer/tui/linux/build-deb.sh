#!/usr/bin/env bash
# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Packages the flagship GAIA agent as a Debian package.
#
#   ./installer/tui/linux/build-deb.sh \
#       --payload installer/tui/payload --version 0.1.1 --arch x64 --out dist-installer
#
# --payload must already hold the SHA-256-verified gaia-tui and gaia-agent that
# installer/tui/fetch_payload.py stages. This script packages; it never fetches,
# so there is exactly one place a binary can enter the installer.
#
# The package declares NO Depends. Both binaries are statically-linked-enough
# builds (a Go binary and a PyInstaller one-file freeze) that carry their own
# runtime — unlike the Agent UI's .deb, which needs python3/python3-venv because
# it provisions an environment at first launch. Declaring a dependency this
# package does not use would make it uninstallable on distros that name the
# package differently, for no benefit.

set -euo pipefail

PAYLOAD=""
VERSION=""
ARCH=""
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
  x64) DEB_ARCH="amd64" ;;
  *) echo "error: --arch must be x64; the sidecar is not frozen for any other Linux architecture (see binaries.lock.json)." >&2; exit 2 ;;
esac

command -v dpkg-deb >/dev/null 2>&1 || {
  echo "error: dpkg-deb is not installed. Install it (apt-get install -y dpkg-dev) or build the .deb on a Debian-family machine." >&2
  exit 1
}

for bin in gaia-tui gaia-agent; do
  if [ ! -f "${PAYLOAD}/${bin}" ]; then
    echo "error: ${PAYLOAD}/${bin} is missing. Stage the payload first: python installer/tui/fetch_payload.py --lock binaries.lock.json --platform linux-${ARCH} --dest ${PAYLOAD}" >&2
    exit 1
  fi
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
ICON="${REPO_ROOT}/installer/tui/gaia-256.png"
[ -f "${ICON}" ] || { echo "error: icon not found at ${ICON}." >&2; exit 1; }

DEB_NAME="gaia-${VERSION}-${ARCH}.deb"
PKG="$(mktemp -d)"
trap 'rm -rf "${PKG}"' EXIT

install -D -m 0755 "${PAYLOAD}/gaia-tui"   "${PKG}/opt/gaia/bin/gaia-tui"
install -D -m 0755 "${PAYLOAD}/gaia-agent" "${PKG}/opt/gaia/bin/gaia-agent"
install -D -m 0644 "${ICON}"               "${PKG}/usr/share/icons/hicolor/256x256/apps/gaia.png"

# Symlinks ship in the data archive rather than being made by a postinst, so
# dpkg owns them and `apt remove` takes them back out. A postinst-created
# symlink is untracked and survives removal as a dangling link.
mkdir -p "${PKG}/usr/bin"
ln -s /opt/gaia/bin/gaia-tui   "${PKG}/usr/bin/gaia-tui"
ln -s /opt/gaia/bin/gaia-agent "${PKG}/usr/bin/gaia-agent"

install -d "${PKG}/usr/share/applications"
cat > "${PKG}/usr/share/applications/gaia.desktop" <<DESKTOP
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

INSTALLED_KB=$(du -sk "${PKG}" | cut -f1)

install -d "${PKG}/DEBIAN"

# The one maintainer script this package has. Everything installed ships in the
# data archive so dpkg owns it -- but `gaia-tui update` writes files afterwards
# that dpkg has never heard of, and those survive even `apt purge`. postrm
# removes exactly those and prunes the directory if that leaves it empty.
POSTRM="$(dirname "${BASH_SOURCE[0]}")/postrm"
[ -f "${POSTRM}" ] || { echo "error: ${POSTRM} is missing; the package would leak update state on removal." >&2; exit 1; }
install -m 0755 "${POSTRM}" "${PKG}/DEBIAN/postrm"

cat > "${PKG}/DEBIAN/control" <<CONTROL
Package: gaia
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: ${DEB_ARCH}
Maintainer: AMD AI Group <gaia@amd.com>
Installed-Size: ${INSTALLED_KB}
Homepage: https://amd-gaia.ai
Description: GAIA — a local AI agent for AMD Ryzen AI
 The flagship GAIA agent: conversation, document Q&A, data analysis, and web
 research, with memory that persists between sessions and skills you can add.
 Every inference runs on this machine through Lemonade Server; nothing you type
 leaves it.
 .
 Installs two binaries: gaia-tui (the terminal UI) and gaia-agent (the agent
 itself). Run "gaia-tui" to start.
CONTROL

mkdir -p "${OUT}"
rm -f "${OUT}/${DEB_NAME}"
# --root-owner-group avoids needing fakeroot for correct 0:0 ownership.
dpkg-deb --root-owner-group --build "${PKG}" "${OUT}/${DEB_NAME}"

echo "built ${OUT}/${DEB_NAME} ($(wc -c < "${OUT}/${DEB_NAME}") bytes)"
dpkg-deb --contents "${OUT}/${DEB_NAME}"
