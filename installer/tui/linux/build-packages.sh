#!/usr/bin/env bash
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Build the Linux .deb and .rpm packages for the GAIA terminal hub.
#
#   ./build-packages.sh --version <x.y.z> --payload <dir> --out <dir>
#
# Produces BOTH of:
#   <out>/gaia_<version>_amd64.deb
#   <out>/gaia-<version>.x86_64.rpm
#
# x86-64 only. Requires fpm and rpmbuild — see README.md for the container.
#
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../../.." && pwd)"

readonly PKG_NAME="gaia"
readonly MAINTAINER="GAIA maintainers <gaia@amd.com>"
readonly VENDOR="Advanced Micro Devices, Inc."
readonly HOMEPAGE="https://amd-gaia.ai"
readonly LICENSE_NAME="MIT"
readonly DOC_DIR="usr/share/doc/gaia-tui"
readonly ICON_DIR="usr/share/icons/hicolor/256x256/apps"
readonly SUMMARY="AMD GAIA terminal hub - local AI assistant"
# Not `readonly`: bash 3.2 rejects `readonly name=(...)`.
PAYLOAD_BINARIES=(gaia-tui gaia-agent)

readonly DESKTOP_FILE="${SCRIPT_DIR}/gaia-tui.desktop"
readonly ICON_FILE="${SCRIPT_DIR}/gaia-tui.png"
readonly LICENSE_FILE="${REPO_ROOT}/LICENSE.md"

usage() {
    cat >&2 <<'EOF'
Usage: build-packages.sh --version <x.y.z> --payload <dir> --out <dir>

  --version   Release version, e.g. 1.4.0. MAJOR.MINOR.PATCH only (see below).
  --payload   Directory containing exactly the executables gaia-tui and gaia-agent.
  --out       Directory to write the .deb and .rpm into (created if absent).

All three arguments are required. Both packages are always built; there is no
flag to produce only one. See installer/tui/linux/README.md.
EOF
}

die() {
    printf 'build-packages.sh: %s\n' "$1" >&2
    shift
    for line in "$@"; do
        printf '  %s\n' "$line" >&2
    done
    exit 1
}

# ── Arguments ───────────────────────────────────────────────────────────────

VERSION=""
PAYLOAD_DIR=""
OUT_DIR=""

while [ $# -gt 0 ]; do
    case "$1" in
        --version) [ $# -ge 2 ] || die "--version requires a value." "Example: --version 1.4.0"; VERSION="$2"; shift 2 ;;
        --payload) [ $# -ge 2 ] || die "--payload requires a value." "Example: --payload ./stage/linux-x64"; PAYLOAD_DIR="$2"; shift 2 ;;
        --out)     [ $# -ge 2 ] || die "--out requires a value." "Example: --out ./dist"; OUT_DIR="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *)         usage; die "unknown argument: $1" ;;
    esac
done

MISSING_ARGS=()
[ -n "$VERSION" ]     || MISSING_ARGS+=(--version)
[ -n "$PAYLOAD_DIR" ] || MISSING_ARGS+=(--payload)
[ -n "$OUT_DIR" ]     || MISSING_ARGS+=(--out)

if [ ${#MISSING_ARGS[@]} -gt 0 ]; then
    usage
    die "missing required argument(s): ${MISSING_ARGS[*]}"
fi

# RPM forbids '-' in the Version field, so a semver prerelease suffix cannot be
# passed through unchanged. Reject it here with the reason rather than letting
# rpmbuild fail three minutes later with a less obvious message.
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    EXTRA=()
    case "$VERSION" in
        *-*) EXTRA=("RPM does not allow '-' in a version; a prerelease suffix has to become an rpm Release." \
                    "Pass the release version alone, e.g. --version ${VERSION%%-*}.") ;;
    esac
    die "--version '${VERSION}' is not a valid version." \
        "Expected MAJOR.MINOR.PATCH with digits only, e.g. 1.4.0." \
        "${EXTRA[@]+"${EXTRA[@]}"}"
fi

# ── Inputs ──────────────────────────────────────────────────────────────────

[ -d "$PAYLOAD_DIR" ] || die "--payload directory does not exist: ${PAYLOAD_DIR}" \
    "Stage the built binaries there first; the directory must contain gaia-tui and gaia-agent."

for binary in "${PAYLOAD_BINARIES[@]}"; do
    [ -f "${PAYLOAD_DIR}/${binary}" ] || die \
        "payload is missing '${binary}': ${PAYLOAD_DIR}/${binary} not found." \
        "The staging directory must contain both gaia-tui and gaia-agent (no extension)." \
        "Check the build step that copies the binaries into ${PAYLOAD_DIR}."
done

for required in "$DESKTOP_FILE" "$ICON_FILE"; do
    [ -f "$required" ] || die "packaging asset not found: ${required}" \
        "It ships alongside this script; the checkout looks incomplete."
done

[ -f "$LICENSE_FILE" ] || die "LICENSE.md not found at ${LICENSE_FILE}" \
    "Every package must ship the licence at /${DOC_DIR}/LICENSE.md." \
    "This script expects to run from inside a GAIA checkout."

mkdir -p "$OUT_DIR"
OUT_DIR="$(cd -- "$OUT_DIR" && pwd)"
PAYLOAD_DIR="$(cd -- "$PAYLOAD_DIR" && pwd)"

readonly DEB_PATH="${OUT_DIR}/${PKG_NAME}_${VERSION}_amd64.deb"
readonly RPM_PATH="${OUT_DIR}/${PKG_NAME}-${VERSION}.x86_64.rpm"

# ── Toolchain ───────────────────────────────────────────────────────────────
#
# Checked only now, so argument and payload errors surface on any host.

command -v fpm >/dev/null 2>&1 || die \
    "'fpm' not found on PATH." \
    "Both packages are built with fpm; there is no fallback path that emits only one." \
    "Install it with 'gem install --no-document fpm', or use the container recipe in" \
    "installer/tui/linux/README.md."

command -v rpmbuild >/dev/null 2>&1 || die \
    "'rpmbuild' not found on PATH." \
    "fpm shells out to rpmbuild to produce the .rpm; without it only the .deb could be built," \
    "and a half-built release is not an acceptable outcome." \
    "Install the 'rpm' package (Debian/Ubuntu) or 'rpm-build' (Fedora/RHEL)."

# ── Staging ─────────────────────────────────────────────────────────────────

STAGE="$(mktemp -d)"
cleanup() { rm -rf "$STAGE"; }
trap cleanup EXIT

# mktemp -d gives 0700, and fpm archives the staging root itself as `./` — which
# unpacks to `/`. dpkg happens to ignore the mode on `/`, but shipping 0700 for
# the filesystem root in a package is not something to leave to that.
chmod 0755 "$STAGE"

install -d -m 0755 "${STAGE}/usr/bin" "${STAGE}/usr/share/applications" \
                   "${STAGE}/${ICON_DIR}" "${STAGE}/${DOC_DIR}"

for binary in "${PAYLOAD_BINARIES[@]}"; do
    install -m 0755 "${PAYLOAD_DIR}/${binary}" "${STAGE}/usr/bin/${binary}"
done

install -m 0644 "$DESKTOP_FILE"  "${STAGE}/usr/share/applications/gaia-tui.desktop"
install -m 0644 "$ICON_FILE"     "${STAGE}/${ICON_DIR}/gaia-tui.png"
install -m 0644 "$LICENSE_FILE"  "${STAGE}/${DOC_DIR}/LICENSE.md"

# ── Package ─────────────────────────────────────────────────────────────────
#
# Shared across both targets. Ownership is forced to root:root because the
# build runs as an unprivileged CI user whose uid would otherwise be baked in.

FPM_COMMON=(
    -s dir
    -C "$STAGE"
    --name "$PKG_NAME"
    --version "$VERSION"
    --maintainer "$MAINTAINER"
    --vendor "$VENDOR"
    --url "$HOMEPAGE"
    --license "$LICENSE_NAME"
    --description "$SUMMARY"
    --deb-user root  --deb-group root
    --rpm-user root  --rpm-group root
    # No pre/post-removal scripts anywhere in this build: uninstalling must
    # never reach into ~/.gaia, and the surest way to guarantee that is to
    # ship no removal hook at all.
    --force
)

# rpmbuild otherwise emits /usr/lib/.build-id/** symlinks keyed on the binary's
# build ID. They serve debuginfo lookups we do not ship, and two packages that
# ever share a build ID collide on those paths at install time.
RPM_ONLY=(--rpm-rpmbuild-define '_build_id_links none')

build_one() {
    target="$1"; arch="$2"; output="$3"
    extra=()
    [ "$target" = "rpm" ] && extra=("${RPM_ONLY[@]}")

    echo "build-packages.sh: building $(basename "$output")"
    fpm "${FPM_COMMON[@]}" "${extra[@]+"${extra[@]}"}" \
        -t "$target" --architecture "$arch" --package "$output" . \
        || die "fpm failed to build the ${target} package." \
               "The fpm output above names the cause. Common ones: a payload file the build user" \
               "cannot read under ${PAYLOAD_DIR}, or a missing rpmbuild for the rpm target." \
               "Nothing was written to ${output}; the release is incomplete without it."

    [ -s "$output" ] || die "fpm reported success but ${output} is missing or empty." \
        "Treat this as a build failure; do not publish the other package on its own."
}

build_one deb amd64  "$DEB_PATH"
build_one rpm x86_64 "$RPM_PATH"

# ── Done ────────────────────────────────────────────────────────────────────
#
# Both or neither. A green build that quietly produced one package is exactly
# the outcome the explicit re-check above exists to prevent.

echo "build-packages.sh: wrote"
for artifact in "$DEB_PATH" "$RPM_PATH"; do
    printf '  %s (%s bytes)\n' "$artifact" "$(stat -c %s "$artifact")"
done
