#!/bin/sh
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Install gaia-native, the GAIA flagship agent as one static binary.
#
#   curl -fsSL https://raw.githubusercontent.com/amd/gaia/main/cpp/packaging/install.sh | sh
#
# GAIA_NATIVE_VERSION=0.1.0   install that version instead of the newest
# GAIA_NATIVE_DIR=~/bin       install there instead of ~/.local/bin

set -eu

repo="amd/gaia"
dir="${GAIA_NATIVE_DIR:-$HOME/.local/bin}"

fail() {
    echo "gaia-native install: $*" >&2
    exit 1
}

need() { command -v "$1" >/dev/null 2>&1 || fail "$1 is required but not installed."; }
need curl
need uname

case "$(uname -s)" in
    Linux) os=linux ;;
    Darwin) os=macos ;;
    *) fail "unsupported OS $(uname -s); on Windows use install.ps1." ;;
esac
case "$(uname -m)" in
    x86_64 | amd64) arch=x64 ;;
    aarch64 | arm64) arch=arm64 ;;
    *) fail "unsupported CPU $(uname -m)." ;;
esac
asset="gaia-native-$os-$arch"
[ "$asset" = "gaia-native-macos-x64" ] && fail "Intel Macs are not built yet; build from source with 'cmake --preset tiny'."

if [ -n "${GAIA_NATIVE_VERSION:-}" ]; then
    tag="gaia-native-v$GAIA_NATIVE_VERSION"
else
    # The repo's "latest" release is GAIA itself, so find the newest gaia-native tag,
    # paging because GAIA cuts many releases of its own.
    tag=""
    page=1
    while [ -z "$tag" ] && [ "$page" -le 10 ]; do
        json=$(curl -fsSL "https://api.github.com/repos/$repo/releases?per_page=100&page=$page") ||
            fail "could not list releases at api.github.com/repos/$repo/releases."
        printf '%s' "$json" | grep -q '"tag_name"' || break
        tag=$(printf '%s\n' "$json" | sed -n 's/.*"tag_name": *"\(gaia-native-v[^"]*\)".*/\1/p' | head -n 1)
        page=$((page + 1))
    done
    [ -n "$tag" ] || fail "no gaia-native release found at github.com/$repo/releases; set GAIA_NATIVE_VERSION to pick one."
fi
base="https://github.com/$repo/releases/download/$tag"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
echo "Downloading $asset ($tag)..."
curl -fsSL -o "$tmp/$asset" "$base/$asset" || fail "download failed: $base/$asset"
curl -fsSL -o "$tmp/SHA256SUMS" "$base/SHA256SUMS" || fail "download failed: $base/SHA256SUMS"

expected=$(grep " $asset\$" "$tmp/SHA256SUMS" | cut -d' ' -f1)
[ -n "$expected" ] || fail "SHA256SUMS has no entry for $asset."
if command -v sha256sum >/dev/null 2>&1; then
    actual=$(sha256sum "$tmp/$asset" | cut -d' ' -f1)
else
    actual=$(shasum -a 256 "$tmp/$asset" | cut -d' ' -f1)
fi
[ "$expected" = "$actual" ] || fail "checksum mismatch for $asset (expected $expected, got $actual); not installing."

mkdir -p "$dir"
mv "$tmp/$asset" "$dir/gaia-native"
chmod +x "$dir/gaia-native"
echo "Installed $dir/gaia-native ($("$dir/gaia-native" --version))."
case ":$PATH:" in
    *":$dir:"*) ;;
    *) echo "Add $dir to your PATH to run it as 'gaia-native'." ;;
esac
echo "It talks to a local Lemonade Server (https://lemonade-server.ai), or to any OpenAI-compatible"
echo "server via LEMONADE_BASE_URL and LEMONADE_API_KEY. Try: gaia-native \"what's in this folder?\""
