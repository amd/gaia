# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Regenerate ``hub/agents/gaia/npm/binaries.lock.json`` from real artifacts
published to the GAIA hub R2 bucket.

Distribution goes through the Agent Hub Worker: the frozen per-platform binaries
are POSTed to ``hub.amd-gaia.ai`` ``/publish`` (see ``publish_to_r2.py``), which
stores them and serves them by a plain public GET. The npm ``fetch`` CLI
downloads each binary and verifies its SHA-256 against this lock -- the lock's
hashes are the integrity gate the CLI enforces on download.

Schema 2.0 (dual-component, the deliberate deviation from the email agent's
single-binary lock): this package ships TWO executables per platform -- the
frozen Python ``sidecar`` and the Go ``tui`` front-end -- so entries are nested
under ``components.<component>.<platform>`` instead of a flat ``binaries`` map::

    {
      "schemaVersion": "2.0",
      "agentVersion": "0.1.0",
      "baseUrl": "https://hub.amd-gaia.ai/agents/gaia/0.1.0",
      "components": {
        "sidecar": {"win32-x64": {filename, executable, sha256, size}, ...},
        "tui":     {"win32-x64": {filename, executable, sha256, size}, ...}
      }
    }

Usage::

    gen_binaries_lock.py --base-url URL --version X.Y.Z --lock PATH \\
        --meta FILE [--meta FILE ...]

Each ``--meta`` is a JSON array of
``{component, platform, filename, executable, sha256, size}`` records. Component
/ platform pairs NOT present in any meta keep their existing lock entry, so a
single-platform local run does not wipe the others; the CI release passes every
platform's meta and regenerates the whole lock.

NO silent fallback: an unknown component, an unsupported platform for that
component, a missing/placeholder sha256, or a base URL that is not http(s)
raises loudly.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# The sidecar is frozen by PyInstaller on the four platforms GAIA ships wheels
# for; the Go TUI cross-compiles to two more.
SIDECAR_PLATFORMS = {"win32-x64", "darwin-arm64", "darwin-x64", "linux-x64"}
TUI_PLATFORMS = SIDECAR_PLATFORMS | {"linux-arm64", "win32-arm64"}
COMPONENTS = {"sidecar": SIDECAR_PLATFORMS, "tui": TUI_PLATFORMS}

SCHEMA_VERSION = "2.0"
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def _load_metas(paths: list[Path]) -> dict[str, dict[str, dict]]:
    """Merge artifact metas into ``{component: {platform: record}}``."""
    merged: dict[str, dict[str, dict]] = {}
    for p in paths:
        if not p.exists():
            raise SystemExit(f"error: artifact meta not found: {p}")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SystemExit(f"error: {p} is not valid JSON: {e}") from e
        if not isinstance(data, list):
            raise SystemExit(f"error: {p} must be a JSON array of artifact records.")
        for rec in data:
            if not isinstance(rec, dict):
                raise SystemExit(f"error: {p} contains a non-object record: {rec!r}")
            component = rec.get("component")
            if component not in COMPONENTS:
                raise SystemExit(
                    f"error: {p} has unknown component {component!r}. "
                    f"Supported: {', '.join(sorted(COMPONENTS))}."
                )
            plat = rec.get("platform")
            if plat not in COMPONENTS[component]:
                raise SystemExit(
                    f"error: {p} has unsupported platform {plat!r} for component "
                    f"'{component}'. Supported: "
                    f"{', '.join(sorted(COMPONENTS[component]))}."
                )
            sha = str(rec.get("sha256", ""))
            if not _SHA_RE.match(sha):
                raise SystemExit(
                    f"error: {p} {component}/{plat} has a non-sha256 / placeholder "
                    f"hash {sha!r}. Refusing to write a lock with a bad hash -- the "
                    "npm fetch CLI verifies downloads against it."
                )
            try:
                merged.setdefault(component, {})[plat] = {
                    "filename": rec["filename"],
                    "executable": rec["executable"],
                    "sha256": sha,
                    "size": int(rec["size"]),
                }
            except (KeyError, TypeError, ValueError) as e:
                raise SystemExit(
                    f"error: {p} {component}/{plat} is missing or has an invalid "
                    f"filename/executable/size field: {e}."
                ) from e
    if not merged:
        raise SystemExit("error: no artifact records found in the given metas.")
    return merged


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate the dual-component binaries.lock.json."
    )
    parser.add_argument(
        "--base-url",
        required=True,
        help="Exact public directory the fetch CLI joins filenames onto, e.g. "
        "https://hub.amd-gaia.ai/agents/gaia/0.1.0",
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
        help="Artifact-meta JSON ([{component,platform,filename,executable,"
        "sha256,size}]). Repeatable.",
    )
    args = parser.parse_args(argv)

    base = args.base_url.rstrip("/")
    if not re.match(r"^https?://", base):
        raise SystemExit(f"error: --base-url must be an http(s) URL, got '{base}'.")

    if not args.lock.exists():
        raise SystemExit(f"error: lock file not found: {args.lock}")
    try:
        lock = json.loads(args.lock.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"error: {args.lock} is not valid JSON: {e}") from e
    if not isinstance(lock, dict):
        raise SystemExit(f"error: {args.lock} must contain a JSON object.")

    existing_schema = lock.get("schemaVersion")
    if existing_schema is not None and existing_schema != SCHEMA_VERSION:
        raise SystemExit(
            f"error: {args.lock} declares schemaVersion {existing_schema!r} but this "
            f"generator writes the dual-component schema {SCHEMA_VERSION!r}. Migrate "
            "the lock or use the generator that matches its schema."
        )

    metas = _load_metas(args.meta)
    lock["schemaVersion"] = SCHEMA_VERSION
    lock["agentVersion"] = args.version
    lock["baseUrl"] = base
    lock.pop("_comment", None)

    components = lock.setdefault("components", {})
    for component, per_platform in metas.items():
        target = components.setdefault(component, {})
        for plat, rec in per_platform.items():
            target[plat] = rec

    args.lock.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    print(f"[gen-lock] baseUrl={lock['baseUrl']}", flush=True)
    print(f"[gen-lock] agentVersion={lock['agentVersion']}", flush=True)
    for component in sorted(metas):
        print(
            f"[gen-lock] {component}: updated {', '.join(sorted(metas[component]))}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
