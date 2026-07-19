# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Regenerate ``hub/agents/email/npm/binaries.lock.json`` from real artifacts
published to the GAIA hub R2 bucket (milestone #49, issue #1648).

Distribution goes through the Agent Hub Worker: the frozen per-platform binaries
are POSTed to ``hub.amd-gaia.ai`` ``/publish`` (see ``publish_to_r2.py``), which
stores them in the ``gaia-hub`` bucket and serves them by a plain public GET. The
npm ``fetch`` CLI downloads each binary and verifies its SHA-256 against this
lock — the lock's hashes are the integrity gate the CLI enforces on download.

Consumes one or more artifact-meta JSON files (each a list of
``{platform, filename, executable, sha256, size}``, written by the build/staging
step) plus the exact ``--base-url`` (the public directory the fetch CLI joins
each per-platform ``filename`` onto) and ``--version``, and writes the lock with:

  * ``baseUrl``  = the ``--base-url`` verbatim, e.g.
      ``https://hub.amd-gaia.ai/agents/email/0.1.0``
  * ``agentVersion`` = ``--version``
  * ``binaries.<platform>`` = real ``filename``/``executable``/``sha256``/``size``
    for every platform present in the metas.

Platforms NOT present in any meta keep their existing lock entry (so a
single-platform local run does not wipe the others). The CI release passes all
four platform metas and regenerates every entry.

NO silent fallback: an unknown platform key, a missing/placeholder sha256, or a
base URL that is not http(s) raises loudly.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SUPPORTED = {"win32-x64", "darwin-arm64", "darwin-x64", "linux-x64"}
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def _load_metas(paths: list[Path]) -> dict[str, dict]:
    merged: dict[str, dict] = {}
    for p in paths:
        if not p.exists():
            raise SystemExit(f"error: artifact meta not found: {p}")
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise SystemExit(f"error: {p} must be a JSON array of artifact records.")
        for rec in data:
            plat = rec.get("platform")
            if plat not in SUPPORTED:
                raise SystemExit(
                    f"error: {p} has unsupported platform '{plat}'. "
                    f"Supported: {', '.join(sorted(SUPPORTED))}."
                )
            sha = str(rec.get("sha256", ""))
            if not _SHA_RE.match(sha):
                raise SystemExit(
                    f"error: {p} platform '{plat}' has a non-sha256 / placeholder "
                    f"hash '{sha}'. Refusing to write a lock with a bad hash."
                )
            try:
                merged[plat] = {
                    "filename": rec["filename"],
                    "executable": rec["executable"],
                    "sha256": sha,
                    "size": int(rec["size"]),
                }
            except (KeyError, TypeError, ValueError) as e:
                raise SystemExit(
                    f"error: {p} platform '{plat}' is missing or has an invalid "
                    f"filename/executable/size field: {e}."
                ) from e
    if not merged:
        raise SystemExit("error: no artifact records found in the given metas.")
    return merged


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate binaries.lock.json from the hub publish summary."
    )
    parser.add_argument(
        "--base-url",
        required=True,
        help="Exact public directory the fetch CLI joins filenames onto, e.g. "
        "https://hub.amd-gaia.ai/agents/email/0.1.0",
    )
    parser.add_argument("--version", required=True, help="Agent version, e.g. 0.1.0.")
    parser.add_argument(
        "--lock", required=True, type=Path, help="Path to binaries.lock.json."
    )
    parser.add_argument(
        "--meta",
        action="append",
        required=True,
        type=Path,
        help="Artifact-meta JSON ([{platform,filename,executable,sha256,size}]). Repeatable.",
    )
    args = parser.parse_args(argv)

    base = args.base_url.rstrip("/")
    if not re.match(r"^https?://", base):
        raise SystemExit(f"error: --base-url must be an http(s) URL, got '{base}'.")

    if not args.lock.exists():
        raise SystemExit(f"error: lock file not found: {args.lock}")
    lock = json.loads(args.lock.read_text(encoding="utf-8"))

    metas = _load_metas(args.meta)
    lock["agentVersion"] = args.version
    lock["baseUrl"] = base
    lock.pop("_comment", None)

    binaries = lock.setdefault("binaries", {})
    for plat, rec in metas.items():
        binaries[plat] = rec

    args.lock.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    print(f"[gen-lock] baseUrl={lock['baseUrl']}", flush=True)
    print(f"[gen-lock] updated platforms: {', '.join(sorted(metas))}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
