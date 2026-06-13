# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Regenerate ``hub/agents/npm/agent-email/binaries.lock.json`` from real published
artifacts (milestone #49, issue #1648).

Consumes one or more publish-summary JSON files emitted by publish_to_r2.py
(each a list of ``{platform, filename, executable, sha256, size}``) and a
``--base-url`` (the Worker origin) + ``--version``, and writes the lock with:

  * ``baseUrl`` = ``<base>/agents/email/<version>`` — the directory the fetch CLI
    joins each per-platform ``filename`` onto (matches the Worker's
    ``GET /agents/<id>/<version>/<filename>`` download route).
  * ``binaries.<platform>`` = the real ``filename``, ``executable``, ``sha256``,
    ``size`` for every platform present in the summaries.

Platforms NOT present in any summary keep their existing lock entry (so a
single-platform local run does not wipe the other platforms). The default CI run
passes all four summaries and regenerates every entry.

NO silent fallback: a summary referencing an unknown platform key, a missing
sha256, or a placeholder hash raises loudly.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SUPPORTED = {"win32-x64", "darwin-arm64", "darwin-x64", "linux-x64"}
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def _load_summaries(paths: list[Path]) -> dict[str, dict]:
    merged: dict[str, dict] = {}
    for p in paths:
        if not p.exists():
            raise SystemExit(f"error: summary not found: {p}")
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
            merged[plat] = {
                "filename": rec["filename"],
                "executable": rec["executable"],
                "sha256": sha,
                "size": int(rec["size"]),
            }
    if not merged:
        raise SystemExit("error: no artifact records found in the given summaries.")
    return merged


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Regenerate binaries.lock.json from R2 publishes.")
    parser.add_argument("--base-url", required=True, help="Worker origin, e.g. https://hub.example.")
    parser.add_argument("--version", required=True, help="Agent version, e.g. 0.1.0.")
    parser.add_argument("--lock", required=True, type=Path, help="Path to binaries.lock.json.")
    parser.add_argument(
        "--summary",
        action="append",
        required=True,
        type=Path,
        help="publish_to_r2.py summary JSON. Repeatable.",
    )
    args = parser.parse_args(argv)

    if not args.lock.exists():
        raise SystemExit(f"error: lock file not found: {args.lock}")
    lock = json.loads(args.lock.read_text(encoding="utf-8"))

    summaries = _load_summaries(args.summary)
    base = args.base_url.rstrip("/")
    lock["agentVersion"] = args.version
    lock["baseUrl"] = f"{base}/agents/email/{args.version}"
    lock.pop("_comment", None)

    binaries = lock.setdefault("binaries", {})
    for plat, rec in summaries.items():
        binaries[plat] = rec

    args.lock.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    updated = ", ".join(sorted(summaries))
    print(f"[gen-lock] baseUrl={lock['baseUrl']}", flush=True)
    print(f"[gen-lock] updated platforms: {updated}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
