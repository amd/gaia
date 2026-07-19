#!/usr/bin/env bash
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# LEGACY / DISCOURAGED. This rclone hand-upload writes straight to the bucket and
# therefore BYPASSES the Agent Hub Worker — so index.json is NOT rebuilt and the
# upload is not server-side checksummed. The supported path (CI and by hand) is
# publish_to_r2.py -> the Worker's POST /publish. Use this only for an emergency
# direct-to-bucket upload, and run a Worker index rebuild afterwards.
#
# Manually upload frozen email-agent binaries to the GAIA hub R2 bucket and
# regenerate binaries.lock.json with their real hashes — the "I run rclone
# myself" path. Same bucket/prefix/origin as the CI release, so the lock baseUrl
# lines up byte-for-byte.
#
# One-time rclone setup (remote name, creds, endpoint): see
#   scripts/video-demo/R2-SETUP.md
# Default remote is 'gaia' (override with R2_REMOTE).
#
# Usage:
#   hub/agents/email/python/packaging/upload_to_r2.sh <version> [staging-dir]
#
#   <version>      release version; MUST match package.json + gaia-agent.yaml.
#   [staging-dir]  dir holding the per-platform binaries (default: ./staging).
#                  Binaries are named email-agent-<platform>[.exe], e.g.
#                  email-agent-win32-x64.exe, email-agent-linux-x64. Any number
#                  of platforms may be present — absent ones keep their existing
#                  lock entry (so a Windows-only hand-upload won't wipe mac/linux).
#
# Env overrides: R2_REMOTE (default gaia), R2_BUCKET (default gaia-hub),
#                GAIA_HUB_BASE_URL (default https://hub.amd-gaia.ai).
#
# No silent fallback: a missing rclone/remote, a version mismatch, or no
# binaries found is a hard error.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../../.." && pwd)"

REMOTE="${R2_REMOTE:-gaia}"
BUCKET="${R2_BUCKET:-gaia-hub}"
HUB_PREFIX="agents/email"
GAIA_HUB_BASE_URL="${GAIA_HUB_BASE_URL:-https://hub.amd-gaia.ai}"

PKG_JSON="${REPO_ROOT}/hub/agents/email/npm/package.json"
LOCK="${REPO_ROOT}/hub/agents/email/npm/binaries.lock.json"
MANIFEST="${REPO_ROOT}/hub/agents/email/python/gaia-agent.yaml"

VERSION="${1:-}"
STAGING="${2:-./staging}"

die() { echo "error: $*" >&2; exit 1; }

[ -n "${VERSION}" ] || die "version required. Usage: $(basename "$0") <version> [staging-dir]"
command -v rclone >/dev/null 2>&1 || die "rclone not found — install it (see scripts/video-demo/R2-SETUP.md)."
# Prefer 'python', fall back to 'python3' (stock macOS/Linux often ship only python3).
if command -v python >/dev/null 2>&1; then PY=python
elif command -v python3 >/dev/null 2>&1; then PY=python3
else die "python or python3 not found — needed to hash binaries + regenerate the lock."; fi
[ -d "${STAGING}" ] || die "staging dir not found: ${STAGING}"

# rclone remote must exist. This helper targets an 'rclone config' remote (default
# 'gaia', see scripts/video-demo/R2-SETUP.md); CI uses an env-only remote instead.
if ! rclone listremotes 2>/dev/null | grep -qx "${REMOTE}:"; then
  die "rclone remote '${REMOTE}:' not configured. Run 'rclone config' (see scripts/video-demo/R2-SETUP.md) or set R2_REMOTE."
fi

# Version must agree across the npm package and the agent manifest.
PKG_VER="$("${PY}" -c "import json;print(json.load(open(r'${PKG_JSON}'))['version'])")"
MAN_VER="$("${PY}" -c "import yaml;print(yaml.safe_load(open(r'${MANIFEST}'))['version'])")"
if [ "${VERSION}" != "${PKG_VER}" ] || [ "${VERSION}" != "${MAN_VER}" ]; then
  die "version mismatch — given=${VERSION}, package.json=${PKG_VER}, gaia-agent.yaml=${MAN_VER}. Align all three first."
fi

# Discover the binaries to upload (top-level email-agent-*, excluding *.json
# sidecars). Plain glob loop — portable to bash 3.2 (macOS) unlike mapfile.
BINS=()
for f in "${STAGING}"/email-agent-*; do
  [ -f "${f}" ] || continue          # literal glob (no match) or non-file -> skip
  case "${f}" in *.json) continue ;; esac
  BINS+=("${f}")
done
[ "${#BINS[@]}" -gt 0 ] || die "no email-agent-<platform> binaries found in ${STAGING}."

DEST="${REMOTE}:${BUCKET}/${HUB_PREFIX}/${VERSION}"
META_DIR="$(mktemp -d)"
trap 'rm -rf "${META_DIR}"' EXIT

echo "==> uploading ${#BINS[@]} binary(ies) + manifest to ${DEST}/"
for bin in "${BINS[@]}"; do
  name="$(basename "${bin}")"
  # Hash the exact bytes we upload so the lock can never drift from the object.
  "${PY}" - "${bin}" "${name}" "${META_DIR}" <<'PY'
import hashlib, json, sys
from pathlib import Path
path, name, meta_dir = sys.argv[1:4]
platform = name[len("email-agent-"):]
is_exe = platform.endswith(".exe")
platform = platform[:-4] if is_exe else platform
data = Path(path).read_bytes()
rec = {
    "platform": platform,
    "filename": name,
    "executable": "email-agent.exe" if is_exe else "email-agent",
    "sha256": hashlib.sha256(data).hexdigest(),
    "size": len(data),
}
Path(meta_dir, f"{platform}.meta.json").write_text(json.dumps([rec], indent=2))
print(f"    {platform:<14} {rec['sha256'][:12]}  {rec['size']} bytes")
PY
  rclone copyto "${bin}" "${DEST}/${name}" --s3-no-check-bucket
done
# The manifest rides along as hub metadata (matches the CI upload).
rclone copyto "${MANIFEST}" "${DEST}/gaia-agent.yaml" --s3-no-check-bucket

echo "==> R2 listing"
rclone lsl "${DEST}/" --s3-no-check-bucket

echo "==> regenerating ${LOCK}"
"${PY}" "${SCRIPT_DIR}/gen_binaries_lock.py" \
  --base-url "${GAIA_HUB_BASE_URL}/${HUB_PREFIX}/${VERSION}" \
  --version "${VERSION}" \
  --lock "${LOCK}" \
  $(for m in "${META_DIR}"/*.meta.json; do echo --meta "${m}"; done)

cat <<EOF

Done. ${#BINS[@]} binary(ies) live at:
  ${GAIA_HUB_BASE_URL}/${HUB_PREFIX}/${VERSION}/

Next — verify the published bytes match the lock, then publish npm:
  cd hub/agents/email/npm
  npm ci && npm run build
  node dist/cli.js fetch --out ./verify --platform win32-x64   # repeat per platform
  npm publish --access public                                  # if not using CI/OIDC
EOF
