# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Regenerate ``hub/agents/gaia/npm/binaries.lock.json`` from real artifacts served
by the GAIA hub R2 catalog.

The npm ``fetch`` CLI downloads each binary and verifies its SHA-256 against this
lock -- the lock's hashes are the integrity gate the CLI enforces on download.

Schema 3.0: this package ships THREE components, published across TWO hub lanes
at DIFFERENT versions:

* ``sidecar`` -- the frozen Python agent speaking REST, published by this
  package's own release into ``agents/gaia/<agentVersion>/``.
* ``stdio`` -- the SAME agent frozen against a stdio/JSONL entry point. This is
  the transport the terminal UI spawns as a child process; the REST sidecar
  ignores stdin and cannot serve it. Published into the SAME
  ``agents/gaia/<agentVersion>/`` directory, so it shares the sidecar's version
  and base URL and differs only in filename.
* ``tui`` -- the Go terminal UI, which is the published ``terminal-hub``
  COMPONENT (``agents/terminal-hub/<tuiVersion>/``), built and published by the
  core release. This package consumes it; it does not build or republish it, so
  the binary a user gets from npm is the same one ``gaia tui`` runs.

That is why each component carries its own ``baseUrl`` and ``componentVersion``.
Schema 2.0's single top-level ``baseUrl`` could not express two lanes::

    {
      "schemaVersion": "3.0",
      "agentVersion": "0.1.0",
      "components": {
        "sidecar": {
          "componentVersion": "0.1.0",
          "baseUrl": "https://hub.amd-gaia.ai/agents/gaia/0.1.0",
          "platforms": {"win32-x64": {filename, executable, sha256, size}, ...}
        },
        "stdio": {
          "componentVersion": "0.1.0",
          "baseUrl": "https://hub.amd-gaia.ai/agents/gaia/0.1.0",
          "platforms": {"win32-x64": {filename, executable, sha256, size}, ...}
        },
        "tui": {
          "componentVersion": "0.23.0",
          "baseUrl": "https://hub.amd-gaia.ai/agents/terminal-hub/0.23.0",
          "platforms": {"win32-x64": {filename, executable, sha256, size}, ...}
        }
      }
    }

Usage::

    gen_binaries_lock.py --version X.Y.Z --sidecar-base-url URL \\
        --tui-version A.B.C --tui-base-url URL --lock PATH \\
        --meta FILE [--meta FILE ...]

Each ``--meta`` is a JSON array of
``{component, platform, filename, executable, sha256, size}`` records. Component
/ platform pairs NOT present in any meta keep their existing lock entry, so a
single-platform local run does not wipe the others; the CI release passes every
platform's meta and regenerates the whole lock.

Platform keys are the npm side's ``${process.platform}-${process.arch}``
(``win32-x64``), for every component. The terminal-hub lane spells its Windows
artifacts ``win-x64`` / ``win-arm64``; that difference is carried by the entry's
``filename``, and ``TUI_ARTIFACT_NAMES`` below is the authority both sides are
checked against -- a meta whose tui filename does not match is rejected, because
nothing else in this pipeline would catch it before a user hits a 404.

The gaia-lane names are checked the same way for a different reason: ``sidecar``
and ``stdio`` are published into ONE directory, so swapping their filenames
would still hash-verify at publish time and quietly hand the installer the wrong
transport.

NO silent fallback: an unknown component, an unsupported platform for that
component, an unexpected artifact name, a missing/placeholder sha256, a base URL
that is not http(s), or a base URL whose trailing segment disagrees with its
component's version raises loudly.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# Both frozen transports come off the same PyInstaller legs, on the four
# platforms GAIA ships wheels for; the Go TUI cross-compiles to two more.
SIDECAR_PLATFORMS = {"win32-x64", "darwin-arm64", "darwin-x64", "linux-x64"}
STDIO_PLATFORMS = SIDECAR_PLATFORMS
TUI_PLATFORMS = SIDECAR_PLATFORMS | {"linux-arm64", "win32-arm64"}
COMPONENTS = {
    "sidecar": SIDECAR_PLATFORMS,
    "stdio": STDIO_PLATFORMS,
    "tui": TUI_PLATFORMS,
}

# npm platform key -> the artifact terminal-hub publishes under
# agents/terminal-hub/<version>/. Mirrors TUI_ARTIFACT_NAMES in
# hub/agents/gaia/npm/src/platform.ts and the --artifact list in
# .github/workflows/release_components.yml.
TUI_ARTIFACT_NAMES = {
    "win32-x64": "gaia-win-x64.exe",
    "win32-arm64": "gaia-win-arm64.exe",
    "darwin-arm64": "gaia-darwin-arm64",
    "darwin-x64": "gaia-darwin-x64",
    "linux-x64": "gaia-linux-x64",
    "linux-arm64": "gaia-linux-arm64",
}

# npm platform key -> the artifact THIS package's release publishes under
# agents/gaia/<agentVersion>/. Mirrors the freeze matrix in
# .github/workflows/release_agent_gaia.yml.
SIDECAR_ARTIFACT_NAMES = {
    "win32-x64": "gaia-agent-win32-x64.exe",
    "darwin-arm64": "gaia-agent-darwin-arm64",
    "darwin-x64": "gaia-agent-darwin-x64",
    "linux-x64": "gaia-agent-linux-x64",
}
STDIO_ARTIFACT_NAMES = {
    "win32-x64": "gaia-agent-stdio-win32-x64.exe",
    "darwin-arm64": "gaia-agent-stdio-darwin-arm64",
    "darwin-x64": "gaia-agent-stdio-darwin-x64",
    "linux-x64": "gaia-agent-stdio-linux-x64",
}
ARTIFACT_NAMES = {
    "sidecar": SIDECAR_ARTIFACT_NAMES,
    "stdio": STDIO_ARTIFACT_NAMES,
    "tui": TUI_ARTIFACT_NAMES,
}

SCHEMA_VERSION = "3.0"
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
            # The tui lane is published by someone else (the core release), so a
            # wrong filename here is not a build failure anywhere -- it is a 404
            # on a user's first run. This is the only place that catches it.
            if component == "tui" and rec.get("filename") != TUI_ARTIFACT_NAMES[plat]:
                raise SystemExit(
                    f"error: {p} tui/{plat} names artifact "
                    f"{rec.get('filename')!r}, but the terminal-hub lane publishes "
                    f"{TUI_ARTIFACT_NAMES[plat]!r} for that platform. Fix the meta, "
                    "or -- if terminal-hub renamed its artifacts -- update "
                    "TUI_ARTIFACT_NAMES here AND in "
                    "hub/agents/gaia/npm/src/platform.ts."
                )
            # sidecar and stdio share one hub directory, so a crossed filename
            # publishes and hash-verifies cleanly and only shows up as the TUI
            # failing to speak to a REST binary (or vice versa) on a user's box.
            if (
                component in ("sidecar", "stdio")
                and rec.get("filename") != ARTIFACT_NAMES[component][plat]
            ):
                raise SystemExit(
                    f"error: {p} {component}/{plat} names artifact "
                    f"{rec.get('filename')!r}, but this package publishes "
                    f"{ARTIFACT_NAMES[component][plat]!r} for that platform. The "
                    "sidecar and stdio transports live in the same "
                    "agents/gaia/<version>/ directory, so a swapped name would "
                    "hash-verify at publish time and hand the installer the wrong "
                    "binary. Fix the meta, or update ARTIFACT_NAMES here AND the "
                    "freeze matrix in .github/workflows/release_agent_gaia.yml."
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


def _checked_base_url(raw: str, version: str, flag: str) -> str:
    """An http(s) directory URL whose trailing segment is ``version``."""
    base = raw.rstrip("/")
    if not re.match(r"^https?://", base):
        raise SystemExit(f"error: {flag} must be an http(s) URL, got '{base}'.")
    tail = base.rsplit("/", 1)[-1]
    if tail != version:
        raise SystemExit(
            f"error: {flag} '{base}' ends with '{tail}', but the version for that "
            f"component is '{version}'. Hub paths are agents/<id>/<version>/, so a "
            "mismatch would point the installer at the wrong release."
        )
    return base


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate the per-component binaries.lock.json (schema 3.0)."
    )
    parser.add_argument(
        "--version",
        required=True,
        help="Agent version — the sidecar's and the stdio binary's, e.g. 0.1.0.",
    )
    parser.add_argument(
        "--sidecar-base-url",
        required=True,
        help="Public directory the gaia-lane filenames (sidecar AND stdio) are "
        "joined onto, e.g. https://hub.amd-gaia.ai/agents/gaia/0.1.0",
    )
    parser.add_argument(
        "--tui-version",
        required=True,
        help="Published terminal-hub component version consumed for the TUI, "
        "e.g. 0.23.0.",
    )
    parser.add_argument(
        "--tui-base-url",
        required=True,
        help="Public directory the TUI's filenames are joined onto, e.g. "
        "https://hub.amd-gaia.ai/agents/terminal-hub/0.23.0",
    )
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

    gaia_lane_base = _checked_base_url(
        args.sidecar_base_url, args.version, "--sidecar-base-url"
    )
    lanes = {
        # sidecar and stdio are two freezes of the same agent published into one
        # agents/gaia/<version>/ directory, so they share a version and base URL
        # by construction rather than by two flags that could drift apart.
        "sidecar": (args.version, gaia_lane_base),
        "stdio": (args.version, gaia_lane_base),
        "tui": (
            args.tui_version,
            _checked_base_url(args.tui_base_url, args.tui_version, "--tui-base-url"),
        ),
    }

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
            f"generator writes the two-lane schema {SCHEMA_VERSION!r}. Migrate the "
            "lock (each component gains its own componentVersion + baseUrl, and its "
            "platform map moves under 'platforms') or use the generator that matches "
            "its schema."
        )

    metas = _load_metas(args.meta)
    lock["schemaVersion"] = SCHEMA_VERSION
    lock["agentVersion"] = args.version
    # Schema 2.0's shared base URL. Leaving it would be a second, silently stale
    # source of truth for a download path.
    lock.pop("baseUrl", None)
    lock.pop("_comment", None)

    components = lock.setdefault("components", {})
    for component, (version, base) in lanes.items():
        lane = components.setdefault(component, {})
        lane["componentVersion"] = version
        lane["baseUrl"] = base
        platforms = lane.setdefault("platforms", {})
        for plat, rec in metas.get(component, {}).items():
            platforms[plat] = rec

    # Entries with no meta this run keep whatever the committed lock held (a
    # partial local run must not wipe the others). That means a stale hand-edit
    # — a platform key the component does not publish, or a tui filename from
    # the old gaia-lane naming — can survive untouched. Validate the WHOLE lock,
    # not just what the metas changed.
    for component, lane in sorted(components.items()):
        for plat, rec in sorted(lane.get("platforms", {}).items()):
            if plat not in COMPONENTS.get(component, set()):
                raise SystemExit(
                    f"error: {args.lock} carries a '{component}' entry for "
                    f"unsupported platform {plat!r}. Supported: "
                    f"{', '.join(sorted(COMPONENTS[component]))}. Remove the stale "
                    "entry -- it would ship a lock the installer 404s on."
                )
            if component == "tui" and rec.get("filename") != TUI_ARTIFACT_NAMES[plat]:
                raise SystemExit(
                    f"error: {args.lock} tui/{plat} names artifact "
                    f"{rec.get('filename')!r}, but the terminal-hub lane publishes "
                    f"{TUI_ARTIFACT_NAMES[plat]!r}. This entry had no meta this run, "
                    "so it is a stale committed value -- fix it in the lock."
                )
            if (
                component in ("sidecar", "stdio")
                and rec.get("filename") != ARTIFACT_NAMES[component][plat]
            ):
                raise SystemExit(
                    f"error: {args.lock} {component}/{plat} names artifact "
                    f"{rec.get('filename')!r}, but this package publishes "
                    f"{ARTIFACT_NAMES[component][plat]!r}. This entry had no meta "
                    "this run, so it is a stale committed value -- fix it in the "
                    "lock. The two transports share a hub directory, so nothing "
                    "downstream would catch the swap."
                )

    args.lock.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    print(f"[gen-lock] agentVersion={lock['agentVersion']}", flush=True)
    for component, (version, base) in sorted(lanes.items()):
        updated = ", ".join(sorted(metas.get(component, {}))) or "(none)"
        print(f"[gen-lock] {component}: {version} @ {base}", flush=True)
        print(f"[gen-lock] {component}: updated {updated}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
