# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Publish frozen GAIA flagship-agent binaries to the Agent Hub R2 Worker.

POSTs each artifact + the agent's ``gaia-agent.yaml`` to the Worker's
``POST /publish`` endpoint (multipart/form-data, Bearer auth). The Worker
computes the SHA-256 server-side and stores the object immutably at
``agents/<id>/<version>/<filename>``. A single ``<id>/<version>`` accepts many
per-platform binaries (each a distinct filename).

ONLY the frozen Python ``sidecar`` (``gaia-agent-<platform>[.exe]``) is published
here. The package's other component -- the Go terminal UI -- is the separately
published ``terminal-hub`` component (``agents/terminal-hub/<version>/``), which
this package consumes rather than rebuilds; republishing it under
``agents/gaia/`` would put the same bytes at a second version under a third
name. ``tui`` is therefore NOT an accepted component here. The summary JSON
still records the component per artifact so ``gen_binaries_lock.py`` can write
the two-lane lock without guessing.

Idempotency (re-running a published release is a no-op):
  * 201 -> published. We assert the Worker-returned SHA-256 equals the SHA-256
    we computed locally (integrity check).
  * 409 (version_exists) -> the filename is already published. We GET the stored
    object and assert its bytes hash to the SAME SHA-256 we hold. Match = true
    no-op; mismatch = loud failure, because a DIFFERENT binary is published
    under this immutable name.

NO silent fallback: any other non-2xx, a SHA mismatch, or a missing token raises
with an actionable message.

Auth: the Bearer token is read from ``AGENT_HUB_PUBLISH_TOKEN`` ONLY. It is
never logged, echoed, or written to disk.

Usage::

    AGENT_HUB_PUBLISH_TOKEN=*** python publish_to_r2.py \\
        --base-url https://hub.amd-gaia.ai \\
        --manifest hub/agents/gaia/python/gaia-agent.yaml \\
        --artifact dist/gaia-agent-win32-x64.exe \\
        [--summary-out published.json]

Each ``--artifact`` is ``<path>[=<component>:<platform>]``. Both halves are
inferred from the filename when the suffix is omitted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import requests
import yaml

PUBLISH_PATH = "/publish"
TOKEN_ENV = "AGENT_HUB_PUBLISH_TOKEN"

# filename prefix -> (component, installed executable stem). Sidecar only: the
# terminal UI ships from the terminal-hub lane and must never be published into
# agents/gaia/, so no prefix routes it here.
COMPONENT_PREFIXES = {
    "gaia-agent-": ("sidecar", "gaia-agent"),
}
COMPONENTS = {c for c, _ in COMPONENT_PREFIXES.values()}
EXECUTABLE_STEMS = dict(COMPONENT_PREFIXES.values())

# Optional docs that ride along with every POST. Each becomes a field on the
# hub catalog entry, rendered as its own tab on the agent page.
#   CLI dest -> (multipart field, upload filename, content type)
DOC_PARTS = {
    "readme": ("readme", "README.md", "text/markdown"),
    "changelog": ("changelog", "CHANGELOG.md", "text/markdown"),
    "spec": ("spec", "SPEC.md", "text/markdown"),
    "skill": ("skill", "SKILL.md", "text/markdown"),
    "evaluation": ("evaluation", "EVALUATION.md", "text/markdown"),
    "capability_matrix": (
        "capability_matrix",
        "CAPABILITY_MATRIX.md",
        "text/markdown",
    ),
    "eval_scorecard": ("eval_scorecard", "eval-scorecard.md", "text/markdown"),
    "package_files": ("package_files", "package-files.json", "application/json"),
}


def _sha256_file(path: Path) -> tuple[str, int]:
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest(), len(data)


def _read_token() -> str:
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        raise SystemExit(
            f"error: {TOKEN_ENV} is not set. Export the Agent Hub Bearer publish "
            "token in the environment (never pass it on the command line or commit "
            "it). See workers/agent-hub/README.md."
        )
    return token


def _load_manifest(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"error: manifest not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise SystemExit(f"error: {path} is not valid YAML: {e}") from e
    if not isinstance(data, dict) or "id" not in data or "version" not in data:
        raise SystemExit(f"error: {path} must define at least 'id' and 'version'.")
    return data


def _strip_exe(filename: str) -> str:
    return filename[: -len(".exe")] if filename.endswith(".exe") else filename


def _infer(filename: str) -> tuple[str, str]:
    """Infer ``(component, platform)`` from ``gaia-{agent,tui}-<platform>[.exe]``."""
    stem = _strip_exe(filename)
    for prefix, (component, _) in COMPONENT_PREFIXES.items():
        if stem.startswith(prefix):
            platform = stem[len(prefix) :]
            if not platform:
                break
            return component, platform
    raise SystemExit(
        f"error: cannot infer component/platform from '{filename}'. Expected a "
        f"name starting with one of {', '.join(sorted(COMPONENT_PREFIXES))}, or "
        "pass it explicitly as <path>=<component>:<platform>."
    )


def _parse_artifact_arg(arg: str) -> tuple[Path, str, str]:
    """Split ``<path>[=<component>:<platform>]`` into (path, component, platform)."""
    if "=" in arg:
        raw_path, _, spec = arg.rpartition("=")
        path = Path(raw_path)
        if ":" not in spec:
            raise SystemExit(
                f"error: artifact key '{spec}' must be '<component>:<platform>' "
                f"(component in {', '.join(sorted(COMPONENTS))})."
            )
        component, _, platform = spec.partition(":")
        if component not in COMPONENTS:
            raise SystemExit(
                f"error: unknown component '{component}' in '{arg}'. "
                f"Supported: {', '.join(sorted(COMPONENTS))}."
            )
        if not platform:
            raise SystemExit(f"error: missing platform key in '{arg}'.")
        return path, component, platform
    path = Path(arg)
    component, platform = _infer(path.name)
    return path, component, platform


def _download_sha256(base_url: str, agent_id: str, version: str, filename: str) -> str:
    url = f"{base_url.rstrip('/')}/agents/{agent_id}/{version}/{filename}"
    resp = requests.get(
        url, headers={"accept": "application/octet-stream"}, timeout=120
    )
    if resp.status_code != 200:
        raise SystemExit(
            f"error: 409 said '{filename}' exists but GET {url} returned "
            f"HTTP {resp.status_code}. Cannot verify idempotency; failing loudly."
        )
    return hashlib.sha256(resp.content).hexdigest()


def publish_one(
    base_url: str,
    manifest_path: Path,
    manifest: dict,
    artifact_path: Path,
    component: str,
    platform_key: str,
    token: str,
    docs: dict[str, bytes],
) -> dict:
    if not artifact_path.exists():
        raise SystemExit(f"error: artifact not found: {artifact_path}")
    filename = artifact_path.name
    local_sha, size = _sha256_file(artifact_path)
    agent_id = str(manifest["id"])
    version = str(manifest["version"])
    publish_url = f"{base_url.rstrip('/')}{PUBLISH_PATH}"

    print(
        f"[publish] {component}/{platform_key} {filename} ({size} bytes, "
        f"sha256={local_sha[:12]}…) -> {agent_id}@{version}",
        flush=True,
    )

    with artifact_path.open("rb") as fh:
        files = {
            "manifest": (
                "gaia-agent.yaml",
                manifest_path.read_bytes(),
                "application/x-yaml",
            ),
            "artifact": (filename, fh, "application/octet-stream"),
        }
        # The docs ride on every POST so the catalog index always reflects the
        # latest published copy; Workers predating a field ignore the unknown part.
        for dest, payload in docs.items():
            field, upload_name, content_type = DOC_PARTS[dest]
            files[field] = (upload_name, payload, content_type)
        resp = requests.post(
            publish_url,
            headers={"authorization": f"Bearer {token}"},
            files=files,
            timeout=300,
        )

    if resp.status_code == 201:
        body = resp.json()
        server_sha = body.get("published", {}).get("artifact", {}).get("sha256")
        if server_sha != local_sha:
            raise SystemExit(
                f"error: integrity check FAILED for {filename}: Worker stored "
                f"sha256={server_sha} but local sha256={local_sha}. The upload was "
                "corrupted in transit; failing loudly."
            )
        n = body.get("published", {}).get("version_artifacts", "?")
        print(
            f"[publish] OK 201 — stored, server sha256 verified. "
            f"{agent_id}@{version} now has {n} artifact(s).",
            flush=True,
        )
    elif resp.status_code == 409:
        remote_sha = _download_sha256(base_url, agent_id, version, filename)
        if remote_sha != local_sha:
            raise SystemExit(
                f"error: {filename} is already published at {agent_id}@{version} "
                f"with a DIFFERENT sha256 (remote={remote_sha}, local={local_sha}). "
                "Published artifacts are immutable — bump the version to change it."
            )
        print(
            "[publish] OK 409 — already published with identical bytes "
            "(idempotent no-op).",
            flush=True,
        )
    else:
        raise SystemExit(
            f"error: publish of {filename} failed: HTTP {resp.status_code} "
            f"{resp.text[:500]}"
        )

    stem = EXECUTABLE_STEMS[component]
    executable = f"{stem}.exe" if filename.endswith(".exe") else stem
    return {
        "component": component,
        "platform": platform_key,
        "filename": filename,
        "executable": executable,
        "sha256": local_sha,
        "size": size,
    }


def _read_docs(args: argparse.Namespace) -> dict[str, bytes]:
    docs: dict[str, bytes] = {}
    for dest in DOC_PARTS:
        path = getattr(args, dest, None)
        if path is None:
            continue
        if not path.exists():
            flag = "--" + dest.replace("_", "-")
            raise SystemExit(
                f"error: {flag} path not found: {path}. Pass a real file, or omit "
                f"{flag} to publish without it."
            )
        payload = path.read_bytes()
        docs[dest] = payload
        print(f"[publish] attaching {dest}: {path} ({len(payload)} bytes)", flush=True)
    return docs


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Publish gaia-agent + gaia-tui binaries to the Agent Hub R2 Worker."
    )
    parser.add_argument(
        "--base-url", required=True, help="Worker origin, e.g. https://hub.amd-gaia.ai."
    )
    parser.add_argument(
        "--manifest", required=True, type=Path, help="gaia-agent.yaml path."
    )
    parser.add_argument(
        "--artifact",
        action="append",
        required=True,
        metavar="PATH[=COMPONENT:PLATFORM]",
        help="Artifact file, optionally with =<component>:<platform>. Repeatable.",
    )
    for dest, (_, upload_name, _) in DOC_PARTS.items():
        parser.add_argument(
            "--" + dest.replace("_", "-"),
            type=Path,
            dest=dest,
            help=f"Path to the {upload_name} to attach to the catalog entry.",
        )
    parser.add_argument(
        "--summary-out",
        type=Path,
        help="Write a JSON array of "
        "{component,platform,filename,executable,sha256,size} "
        "(the input gen_binaries_lock.py consumes).",
    )
    args = parser.parse_args(argv)

    token = _read_token()
    manifest = _load_manifest(args.manifest)
    docs = _read_docs(args)

    results = []
    for raw in args.artifact:
        path, component, platform_key = _parse_artifact_arg(raw)
        results.append(
            publish_one(
                args.base_url,
                args.manifest,
                manifest,
                path,
                component,
                platform_key,
                token,
                docs,
            )
        )

    if args.summary_out:
        args.summary_out.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"[publish] wrote summary -> {args.summary_out}", flush=True)

    print(
        f"[publish] DONE — {len(results)} artifact(s) published/verified.", flush=True
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
