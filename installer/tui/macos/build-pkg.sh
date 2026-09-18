#!/usr/bin/env bash
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Build the macOS installer package for the GAIA terminal hub.
#
#   ./build-pkg.sh --version <x.y.z> --arch <arm64|x64> --payload <dir> --out <dir>
#
# Produces <out>/gaia-<version>-darwin-<arch>.pkg, installing gaia-tui and
# gaia-agent to /usr/local/bin, plus LICENSE.md to /usr/local/share/doc/gaia-tui.
#
# Signing and notarization are opt-in via the environment — see README.md.
#
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# One source of truth for the identifiers: distribution.xml takes them as
# placeholders rather than repeating them.
readonly PKG_IDENTIFIER="ai.amd.gaia.terminal-hub"
readonly DOCS_IDENTIFIER="ai.amd.gaia.terminal-hub.docs"
readonly COMPONENT_PKG="gaia-terminal-hub-component.pkg"
readonly DOCS_COMPONENT_PKG="gaia-terminal-hub-docs-component.pkg"
readonly MIN_OS_VERSION="11.0"

# Two components so that /usr/local* is only ever an --install-location, never a
# payload entry: `--ownership recommended` would stamp such an entry root:wheel,
# which breaks `brew` on an Intel Mac where Homebrew owns /usr/local as the user.
readonly BIN_INSTALL_LOCATION="/usr/local/bin"
# Mirrors the .deb/.rpm layout (usr/share/doc/gaia-tui), under /usr/local
# because that is where this package installs.
readonly DOC_INSTALL_LOCATION="/usr/local/share/doc/gaia-tui"
readonly LICENSE_NAME="LICENSE.md"
# Not `readonly`: bash 3.2, which is what /bin/bash still is on macOS, rejects
# `readonly name=(...)`.
PAYLOAD_BINARIES=(gaia-tui gaia-agent)
PKG_SCRIPTS=(postinstall)

readonly DISTRIBUTION_TEMPLATE="${SCRIPT_DIR}/distribution.xml"
readonly SCRIPTS_DIR="${SCRIPT_DIR}/scripts"

usage() {
    cat >&2 <<'EOF'
Usage: build-pkg.sh --version <x.y.z> --arch <arm64|x64> --payload <dir> --out <dir>
                    [--allow-unsigned]

  --version         Release version, e.g. 1.4.0. Embedded in the package and filename.
  --arch            Architecture of the staged binaries: arm64 or x64.
  --payload         Directory containing the executables gaia-tui and gaia-agent, plus LICENSE.md.
  --out             Directory to write gaia-<version>-darwin-<arch>.pkg into (created if absent).
  --allow-unsigned  Proceed without Apple credentials. Required to build an
                    unsigned package, so that shipping one is always a decision
                    someone made rather than a secret that happened to be absent.

The first four arguments are required. See installer/tui/macos/README.md for the
signing and notarization environment variables.
EOF
}

die() {
    printf 'build-pkg.sh: %s\n' "$1" >&2
    shift
    for line in "$@"; do
        printf '  %s\n' "$line" >&2
    done
    exit 1
}

# ── Arguments ───────────────────────────────────────────────────────────────

VERSION=""
ARCH=""
PAYLOAD_DIR=""
OUT_DIR=""
ALLOW_UNSIGNED=0

while [ $# -gt 0 ]; do
    case "$1" in
        --allow-unsigned) ALLOW_UNSIGNED=1; shift ;;
        --version) [ $# -ge 2 ] || die "--version requires a value." "Example: --version 1.4.0"; VERSION="$2"; shift 2 ;;
        --arch)    [ $# -ge 2 ] || die "--arch requires a value." "Expected: --arch arm64  or  --arch x64"; ARCH="$2"; shift 2 ;;
        --payload) [ $# -ge 2 ] || die "--payload requires a value." "Example: --payload ./stage/darwin-arm64"; PAYLOAD_DIR="$2"; shift 2 ;;
        --out)     [ $# -ge 2 ] || die "--out requires a value." "Example: --out ./dist"; OUT_DIR="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *)         usage; die "unknown argument: $1" ;;
    esac
done

MISSING_ARGS=()
[ -n "$VERSION" ]     || MISSING_ARGS+=(--version)
[ -n "$ARCH" ]        || MISSING_ARGS+=(--arch)
[ -n "$PAYLOAD_DIR" ] || MISSING_ARGS+=(--payload)
[ -n "$OUT_DIR" ]     || MISSING_ARGS+=(--out)

if [ ${#MISSING_ARGS[@]} -gt 0 ]; then
    usage
    die "missing required argument(s): ${MISSING_ARGS[*]}"
fi

if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.-]+)?$ ]]; then
    die "--version '${VERSION}' is not a valid version." \
        "Expected MAJOR.MINOR.PATCH with an optional -prerelease or +build suffix, e.g. 1.4.0 or 1.4.0-rc.1."
fi

case "$ARCH" in
    arm64) HOST_ARCHITECTURES="arm64" ;;
    x64)   HOST_ARCHITECTURES="x86_64" ;;
    *)     die "--arch '${ARCH}' is not supported." "Expected arm64 or x64." ;;
esac

# ── Inputs ──────────────────────────────────────────────────────────────────

[ -d "$PAYLOAD_DIR" ] || die "--payload directory does not exist: ${PAYLOAD_DIR}" \
    "Stage the inputs there first; the directory must contain gaia-tui, gaia-agent and LICENSE.md."

for binary in "${PAYLOAD_BINARIES[@]}"; do
    [ -f "${PAYLOAD_DIR}/${binary}" ] || die \
        "payload is missing '${binary}': ${PAYLOAD_DIR}/${binary} not found." \
        "The staging directory must contain both gaia-tui and gaia-agent (no extension)." \
        "Check the build step that copies the Go binaries into ${PAYLOAD_DIR}."
done

[ -f "${PAYLOAD_DIR}/${LICENSE_NAME}" ] || die \
    "payload is missing '${LICENSE_NAME}': ${PAYLOAD_DIR}/${LICENSE_NAME} not found." \
    "Every package ships the licence, so this one refuses to build without it." \
    "Copy the repo's LICENSE.md into ${PAYLOAD_DIR} before building."

[ -f "$DISTRIBUTION_TEMPLATE" ] || die "distribution template not found: ${DISTRIBUTION_TEMPLATE}" \
    "It ships alongside this script; the checkout looks incomplete."
for script in "${PKG_SCRIPTS[@]}"; do
    [ -f "${SCRIPTS_DIR}/${script}" ] || die "${script} script not found: ${SCRIPTS_DIR}/${script}" \
        "It ships alongside this script; the checkout looks incomplete."
done

mkdir -p "$OUT_DIR"
OUT_DIR="$(cd -- "$OUT_DIR" && pwd)"
PAYLOAD_DIR="$(cd -- "$PAYLOAD_DIR" && pwd)"

readonly PRODUCT_PKG="${OUT_DIR}/gaia-${VERSION}-darwin-${ARCH}.pkg"

# ── Signing configuration ───────────────────────────────────────────────────
#
# Signing is all-or-nothing on purpose. A half-configured keychain that quietly
# emits an unsigned package is exactly the failure this rejects.

APPLE_ID="${APPLE_ID:-}"
APPLE_APP_SPECIFIC_PASSWORD="${APPLE_APP_SPECIFIC_PASSWORD:-}"
APPLE_TEAM_ID="${APPLE_TEAM_ID:-}"
APPLE_INSTALLER_IDENTITY="${APPLE_INSTALLER_IDENTITY:-}"

SIGNING_VARS=(APPLE_ID APPLE_APP_SPECIFIC_PASSWORD APPLE_TEAM_ID APPLE_INSTALLER_IDENTITY)
SET_VARS=()
UNSET_VARS=()
for var in "${SIGNING_VARS[@]}"; do
    if [ -n "${!var}" ]; then SET_VARS+=("$var"); else UNSET_VARS+=("$var"); fi
done

if [ ${#UNSET_VARS[@]} -eq 0 ]; then
    SIGN_PKG=1
elif [ ${#SET_VARS[@]} -eq 0 ]; then
    # A missing secret must not be the thing that decides a release ships
    # unsigned. Without --allow-unsigned this is a failure, so a CI job whose
    # Apple credentials were never wired up goes red instead of green.
    if [ "$ALLOW_UNSIGNED" -ne 1 ]; then
        die "no Apple credentials in the environment and --allow-unsigned was not passed." \
            "Set ${SIGNING_VARS[*]} to sign and notarize," \
            "or pass --allow-unsigned to deliberately build a package that macOS Gatekeeper" \
            "will flag as coming from an unidentified developer." \
            "See installer/tui/macos/README.md."
    fi
    SIGN_PKG=0
    echo "build-pkg.sh: WARNING — building an UNSIGNED, un-notarized package (--allow-unsigned). macOS Gatekeeper will show users an \"unidentified developer\" prompt." >&2
else
    die "signing is partially configured: ${#SET_VARS[@]} of ${#SIGNING_VARS[@]} variables are set." \
        "Set: ${SET_VARS[*]}" \
        "Missing: ${UNSET_VARS[*]}" \
        "Set all four to sign and notarize, or none to build an unsigned package." \
        "See installer/tui/macos/README.md."
fi

# ── macOS toolchain ─────────────────────────────────────────────────────────
#
# Checked only now, so argument and environment errors surface on any host.

REQUIRED_TOOLS=(pkgbuild productbuild pkgutil)
if [ "$SIGN_PKG" -eq 1 ]; then
    REQUIRED_TOOLS+=(xcrun codesign spctl plutil)
fi
for tool in "${REQUIRED_TOOLS[@]}"; do
    command -v "$tool" >/dev/null 2>&1 || die \
        "required tool '${tool}' not found." \
        "This script builds a macOS .pkg and must run on macOS with the Xcode command line tools installed." \
        "Install them with 'xcode-select --install'."
done

# ── Staging ─────────────────────────────────────────────────────────────────

WORK_DIR="$(mktemp -d)"
cleanup() { rm -rf "$WORK_DIR"; }
trap cleanup EXIT

BIN_ROOT="${WORK_DIR}/bin-root"
DOC_ROOT="${WORK_DIR}/doc-root"
STAGED_SCRIPTS="${WORK_DIR}/scripts"

# Each root's own mode becomes the BOM's "." entry, which is what the installer
# creates the install location with when it is absent. Pin it rather than
# inherit the build host's umask.
mkdir -p "$BIN_ROOT" "$DOC_ROOT"
chmod 0755 "$BIN_ROOT" "$DOC_ROOT"

for binary in "${PAYLOAD_BINARIES[@]}"; do
    cp "${PAYLOAD_DIR}/${binary}" "${BIN_ROOT}/${binary}"
    chmod 0755 "${BIN_ROOT}/${binary}"
done

cp "${PAYLOAD_DIR}/${LICENSE_NAME}" "${DOC_ROOT}/${LICENSE_NAME}"
chmod 0644 "${DOC_ROOT}/${LICENSE_NAME}"

# Copy rather than point pkgbuild at the checkout: the executable bit on
# scripts/postinstall does not survive every clone (Windows checkouts drop it),
# and pkgbuild silently ignores a non-executable script.
mkdir -p "$STAGED_SCRIPTS"
for script in "${PKG_SCRIPTS[@]}"; do
    cp "${SCRIPTS_DIR}/${script}" "${STAGED_SCRIPTS}/${script}"
    chmod 0755 "${STAGED_SCRIPTS}/${script}"
done

if [ "$SIGN_PKG" -eq 1 ]; then
    # Apple's notary service requires every Mach-O inside the payload to carry a
    # Developer ID Application signature AND the hardened runtime — signing the
    # enclosing pkg is not enough (WWDC19 703). Catch it here rather than ten
    # minutes into the notary round trip.
    CODESIGN_HINT="  codesign --force --options runtime --timestamp --sign \"Developer ID Application: … (${APPLE_TEAM_ID})\" <binary>"

    for binary in "${PAYLOAD_BINARIES[@]}"; do
        # Captured, not piped: `grep -q` exits on first match and would SIGPIPE
        # codesign, which `pipefail` then reports as a failure on a good binary.
        CODESIGN_INFO="$(codesign --display --verbose=2 "${BIN_ROOT}/${binary}" 2>&1 || true)"

        case "$CODESIGN_INFO" in
            *"Authority=Developer ID Application"*) ;;
            *)
                die "payload binary '${binary}' is not signed with a Developer ID Application certificate." \
                    "Notarization rejects packages containing unsigned or ad-hoc-signed executables." \
                    "Sign both binaries before staging them:" \
                    "$CODESIGN_HINT" \
                    "codesign reported: ${CODESIGN_INFO}"
                ;;
        esac

        # Hardened runtime shows up as `flags=0x10000(runtime)` on the
        # CodeDirectory line. Its absence is a guaranteed notary rejection.
        case "$CODESIGN_INFO" in
            *"(runtime)"*) ;;
            *)
                die "payload binary '${binary}' was signed without the hardened runtime." \
                    "Notarization requires --options runtime on every executable." \
                    "Re-sign it with:" \
                    "$CODESIGN_HINT" \
                    "codesign reported: ${CODESIGN_INFO}"
                ;;
        esac
    done
fi

# ── Component packages ──────────────────────────────────────────────────────

# Read the BOM back: a component that shipped no files, or one that reached
# outside its install location, is only visible here -- both install "fine".
assert_payload() {
    local pkg="$1" identifier="$2" location="$3"
    shift 3

    local listing entries expected
    listing="$(pkgutil --payload-files "$pkg")" \
        || die "could not read back the payload listing for ${identifier}."
    echo "build-pkg.sh: ${identifier} payload (install location ${location})"
    printf '%s\n' "$listing" | sed 's/^/  /'

    # Compare on content, not on pkgutil's "./" path spelling.
    entries="$(printf '%s\n' "$listing" | sed -e 's|^\./||' -e 's|^/||')"

    # Here-strings, not pipes: `grep -q` exits on the first match and would
    # SIGPIPE the writer, which `pipefail` then reports as a failed check.
    for expected in "$@"; do
        grep -Fqx "$expected" <<<"$entries" \
            || die "${identifier} does not carry ${location}/${expected}." \
                   "pkgbuild produced a package with nothing in it, or with the file under a" \
                   "different path. The payload listing above is the whole BOM."
    done

    if grep -qE '^usr(/|$)' <<<"$entries"; then
        die "${identifier} carries a directory entry under /usr." \
            "Its root must hold only the files it installs -- the parent directories are the" \
            "--install-location (${location}), not payload. Installing a /usr/local entry resets" \
            "that directory to root:wheel, which breaks Homebrew on an Intel Mac where the user owns it."
    fi
}

echo "build-pkg.sh: building component package (${PKG_IDENTIFIER} ${VERSION}, ${ARCH})"
pkgbuild \
    --root "$BIN_ROOT" \
    --identifier "$PKG_IDENTIFIER" \
    --version "$VERSION" \
    --install-location "$BIN_INSTALL_LOCATION" \
    --scripts "$STAGED_SCRIPTS" \
    --ownership recommended \
    --min-os-version "$MIN_OS_VERSION" \
    "${WORK_DIR}/${COMPONENT_PKG}" \
    || die "pkgbuild failed for ${PKG_IDENTIFIER}." \
           "Re-run with the pkgbuild output above; the usual causes are an unreadable payload staged at ${BIN_ROOT} or a malformed --scripts directory."

assert_payload "${WORK_DIR}/${COMPONENT_PKG}" "$PKG_IDENTIFIER" "$BIN_INSTALL_LOCATION" \
    "${PAYLOAD_BINARIES[@]}"

echo "build-pkg.sh: building component package (${DOCS_IDENTIFIER} ${VERSION})"
pkgbuild \
    --root "$DOC_ROOT" \
    --identifier "$DOCS_IDENTIFIER" \
    --version "$VERSION" \
    --install-location "$DOC_INSTALL_LOCATION" \
    --ownership recommended \
    --min-os-version "$MIN_OS_VERSION" \
    "${WORK_DIR}/${DOCS_COMPONENT_PKG}" \
    || die "pkgbuild failed for ${DOCS_IDENTIFIER}." \
           "Re-run with the pkgbuild output above; the usual cause is an unreadable payload staged at ${DOC_ROOT}."

assert_payload "${WORK_DIR}/${DOCS_COMPONENT_PKG}" "$DOCS_IDENTIFIER" "$DOC_INSTALL_LOCATION" \
    "$LICENSE_NAME"

echo "build-pkg.sh: both payloads own only their own files (no /usr/local directory entries)"

# ── Product archive ─────────────────────────────────────────────────────────

DISTRIBUTION="${WORK_DIR}/distribution.xml"
sed \
    -e "s|@VERSION@|${VERSION}|g" \
    -e "s|@HOST_ARCHITECTURES@|${HOST_ARCHITECTURES}|g" \
    -e "s|@PKG_IDENTIFIER@|${PKG_IDENTIFIER}|g" \
    -e "s|@DOCS_IDENTIFIER@|${DOCS_IDENTIFIER}|g" \
    -e "s|@COMPONENT_PKG@|${COMPONENT_PKG}|g" \
    -e "s|@DOCS_COMPONENT_PKG@|${DOCS_COMPONENT_PKG}|g" \
    "$DISTRIBUTION_TEMPLATE" > "$DISTRIBUTION"

PRODUCTBUILD_ARGS=(
    --distribution "$DISTRIBUTION"
    --package-path "$WORK_DIR"
)
if [ "$SIGN_PKG" -eq 1 ]; then
    echo "build-pkg.sh: signing with '${APPLE_INSTALLER_IDENTITY}'"
    PRODUCTBUILD_ARGS+=(--sign "$APPLE_INSTALLER_IDENTITY" --timestamp)
fi

echo "build-pkg.sh: building product archive $(basename "$PRODUCT_PKG")"
productbuild "${PRODUCTBUILD_ARGS[@]}" "$PRODUCT_PKG" \
    || die "productbuild failed for $(basename "$PRODUCT_PKG")." \
           "If the failure mentions the signing identity, confirm it is in the keychain with:" \
           "  security find-identity -v | grep 'Developer ID Installer'" \
           "and that APPLE_INSTALLER_IDENTITY matches that name exactly."

# ── Notarization ────────────────────────────────────────────────────────────

if [ "$SIGN_PKG" -eq 1 ]; then
    echo "build-pkg.sh: submitting to the Apple notary service (this can take several minutes)"
    NOTARY_JSON="${WORK_DIR}/notarytool-submit.json"

    # `notarytool submit --wait` exits 0 for a terminal status of Invalid or
    # Rejected — completion, not acceptance. The status must be read back.
    if ! xcrun notarytool submit "$PRODUCT_PKG" \
            --apple-id "$APPLE_ID" \
            --password "$APPLE_APP_SPECIFIC_PASSWORD" \
            --team-id "$APPLE_TEAM_ID" \
            --wait \
            --output-format json > "$NOTARY_JSON"; then
        cat "$NOTARY_JSON" >&2 || true
        die "notarytool could not complete the submission for $(basename "$PRODUCT_PKG")." \
            "The output above is from notarytool. Check network access to Apple's notary service and that" \
            "APPLE_ID / APPLE_APP_SPECIFIC_PASSWORD / APPLE_TEAM_ID are a valid app-specific-password triple."
    fi

    # `-o -` pins output to stdout. A parse failure leaves the value empty,
    # which the not-Accepted branch below turns into a loud failure.
    NOTARY_STATUS="$(/usr/bin/plutil -extract status raw -o - "$NOTARY_JSON" 2>/dev/null || true)"
    NOTARY_ID="$(/usr/bin/plutil -extract id raw -o - "$NOTARY_JSON" 2>/dev/null || true)"

    if [ "$NOTARY_STATUS" != "Accepted" ]; then
        echo "build-pkg.sh: notarization status is '${NOTARY_STATUS:-unknown}'; fetching the notary log" >&2
        if [ -n "$NOTARY_ID" ]; then
            # Redirect rather than passing /dev/stderr as notarytool's output
            # path — that positional is an ordinary file path.
            xcrun notarytool log "$NOTARY_ID" \
                --apple-id "$APPLE_ID" \
                --password "$APPLE_APP_SPECIFIC_PASSWORD" \
                --team-id "$APPLE_TEAM_ID" >&2 || true
        fi
        rm -f "$PRODUCT_PKG"
        die "Apple rejected $(basename "$PRODUCT_PKG") (status: ${NOTARY_STATUS:-unknown}); the package was deleted rather than shipped unsigned." \
            "The notary log above names the offending path. The usual cause is a payload binary built without" \
            "--options runtime or signed with the wrong certificate." \
            "Re-inspect any time with: xcrun notarytool log ${NOTARY_ID:-<submission-id>} --apple-id … --team-id …"
    fi

    echo "build-pkg.sh: notarization accepted (submission ${NOTARY_ID}); stapling the ticket"
    xcrun stapler staple "$PRODUCT_PKG" \
        || die "stapler could not attach the notarization ticket to $(basename "$PRODUCT_PKG")." \
               "Notarization was accepted, so this is usually a transient CDN delay — retry the staple with:" \
               "  xcrun stapler staple '${PRODUCT_PKG}'"

    # spctl is the gate the user's Mac actually applies, so it is the one worth
    # failing on — `pkgutil --check-signature` reports the certificate but Apple
    # documents no exit codes for it.
    spctl --assess --type install -vv "$PRODUCT_PKG" \
        || die "Gatekeeper rejects the finished package ('spctl --assess --type install')." \
               "Do not publish it — users would hit the same rejection." \
               "Re-read the productbuild and notarytool output above; the usual cause is a stapled" \
               "ticket that does not match the signature the package was ultimately signed with."
fi

# ── Done ────────────────────────────────────────────────────────────────────

if [ "$SIGN_PKG" -eq 1 ]; then
    echo "build-pkg.sh: wrote signed + notarized ${PRODUCT_PKG}"
else
    echo "build-pkg.sh: wrote UNSIGNED ${PRODUCT_PKG}"
fi
