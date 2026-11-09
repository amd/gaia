#!/usr/bin/env bash
# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Upload test evidence (screenshots, logs, traces) for a PR or issue to the
# PUBLIC amd-gaia R2 bucket and print one https://assets.amd-gaia.ai URL per file.
#
# Usage:
#   scripts/testing/upload-evidence.sh <pr-or-issue-ref> <file>...
#
#   <pr-or-issue-ref>  e.g. pr-1234 or issue-2369; letters, digits, . _ -
#   <file>...          uploaded to testing/<ref>/<run-id>/<basename>, where
#                      <run-id> is the UTC time plus 4 random hex digits,
#                      e.g. 20261109T153012Z-3f2a, so reruns never overwrite.
#
# Every file passes a secret scan first (scan_evidence.py, using the patterns of
# gaia.llm.failure_record). One hit refuses the whole batch and uploads nothing;
# there is no flag to skip it. Images and video are not scanned: each gets a
# reminder to review it by eye.
#
# Credentials stay in your machine-local rclone config — remote 'gaia' by default
# (override with R2_REMOTE). One-time setup: scripts/video-demo/R2-SETUP.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

REMOTE="${R2_REMOTE:-gaia}"
BUCKET="amd-gaia"
PUBLIC_BASE="https://assets.amd-gaia.ai"
SETUP_DOC="scripts/video-demo/R2-SETUP.md"

die() { echo "error: $*" >&2; exit 1; }

[ "$#" -ge 2 ] || die "usage: $(basename "$0") <pr-or-issue-ref> <file>..."
REF="$1"
shift
case "${REF}" in
  *..*|[!A-Za-z0-9]*|*[!A-Za-z0-9._-]*)
    die "ref '${REF}' must be letters, digits, '.', '_' or '-' (e.g. pr-1234)." ;;
esac

NAMES=" "
for f in "$@"; do
  [ -f "${f}" ] || die "not a file: ${f}"
  name="$(basename "${f}")"
  case "${name}" in
    [!A-Za-z0-9]*|*[!A-Za-z0-9._-]*)
      die "rename '${name}' to letters, digits, '.', '_' or '-' so its URL needs no escaping." ;;
  esac
  case "${NAMES}" in
    *" ${name} "*) die "two files are named '${name}'; one would overwrite the other." ;;
  esac
  NAMES="${NAMES}${name} "
done

command -v rclone >/dev/null 2>&1 \
  || die "rclone not found. Install it and set up the '${REMOTE}' remote: see ${SETUP_DOC}."
if ! rclone listremotes 2>/dev/null | grep -qx "${REMOTE}:"; then
  die "rclone remote '${REMOTE}:' is not configured. Run 'rclone config' as described in ${SETUP_DOC}, or set R2_REMOTE."
fi

if command -v python >/dev/null 2>&1; then PY=python
elif command -v python3 >/dev/null 2>&1; then PY=python3
else die "python not found; the secret scan needs this repo's Python environment (see docs/reference/dev.mdx)."; fi

if ! "${PY}" "${SCRIPT_DIR}/scan_evidence.py" "$@"; then
  die "secret scan did not pass, so nothing was uploaded. Remove the secret from each blocked file and rerun; if gaia failed to import, run from this repo's environment (see docs/reference/dev.mdx)."
fi

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$(od -An -N2 -tx1 /dev/urandom | tr -d ' \n')"
KEY_PREFIX="testing/${REF}/${RUN_ID}"

for f in "$@"; do
  rclone copyto "${f}" "${REMOTE}:${BUCKET}/${KEY_PREFIX}/$(basename "${f}")" \
    --s3-no-check-bucket
done

for f in "$@"; do
  echo "${PUBLIC_BASE}/${KEY_PREFIX}/$(basename "${f}")"
done
