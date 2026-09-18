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

Two transports, same guarantee:
  * Through the Worker -- the artifact bytes ride in the POST body. The Worker
    hashes them server-side.
  * Straight to R2 -- an artifact at or over ``DIRECT_UPLOAD_THRESHOLD`` cannot
    fit in a Worker request body (Cloudflare caps it at 100 MB on Free/Pro), so
    it is PUT over the S3 API first and the POST carries only its coordinates
    (``artifact_ref_*``). The Worker verifies the stored object's size and the
    SHA-256 R2 recorded before it records the artifact, so this is a different
    transport, not a weaker check.

Idempotency (re-running a published release is a no-op):
  * 201 -> published. We assert the Worker-returned SHA-256 equals the SHA-256
    we computed locally (integrity check).
  * 409 ``version_exists`` -> the filename is already published. We GET the
    stored object; identical bytes are a true no-op. DIFFERENT bytes are skipped
    with a warning and the PUBLISHED sha256/size is what the summary reports --
    neither Go nor PyInstaller is byte-reproducible, so a rebuild of a released
    version always differs. ``--strict-immutable`` restores the hard failure.
  * Any other 409 (``artifact_mismatch`` / ``artifact_unverifiable`` /
    ``id_conflict``) means the catalog was NOT modified, and stays a failure.

NO silent fallback: any other non-2xx, a SHA mismatch, or a missing token raises
with an actionable message.

Auth: the Bearer token is read from ``AGENT_HUB_PUBLISH_TOKEN`` ONLY. It is
never logged, echoed, or written to disk. Direct-to-R2 uploads additionally need
``R2_ACCESS_KEY_ID``, ``R2_SECRET_ACCESS_KEY`` and ``CLOUDFLARE_ACCOUNT_ID``,
read from the environment the same way.

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
import base64
import contextlib
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

# Cloudflare caps a Worker request body by plan -- 100 MB on Free/Pro. Anything
# at or above this goes straight to R2 over the S3 API instead, and is published
# by reference. Deliberately below the real cap so an artifact that grows into
# the limit switches lanes before it starts 413ing mid-release.
DIRECT_UPLOAD_THRESHOLD = 90 * 1024 * 1024


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
    """Infer ``(component, platform)`` from ``gaia-agent-<platform>[.exe]``."""
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


def _download_published(
    base_url: str, agent_id: str, version: str, filename: str
) -> tuple[str, int]:
    """SHA-256 and byte size of what is ALREADY published under this name."""
    url = f"{base_url.rstrip('/')}/agents/{agent_id}/{version}/{filename}"
    resp = requests.get(
        url, headers={"accept": "application/octet-stream"}, timeout=120
    )
    if resp.status_code != 200:
        raise SystemExit(
            f"error: 409 said '{filename}' exists but GET {url} returned "
            f"HTTP {resp.status_code}. Cannot verify idempotency; failing loudly."
        )
    return hashlib.sha256(resp.content).hexdigest(), len(resp.content)


def _r2_credentials() -> tuple[str, str, str] | None:
    """R2 S3 credentials, or None when direct upload is not configured.

    Stripped: a trailing newline is easy to store (``gh secret set`` keeps
    whatever it is handed, and the GitHub UI does not show it) and it is signed
    as part of the credential, so SigV4 fails and R2 answers ``Unauthorized`` --
    indistinguishable from a wrong key, and unfixable by re-pasting the value.
    """
    key = (os.environ.get("R2_ACCESS_KEY_ID") or "").strip()
    secret = (os.environ.get("R2_SECRET_ACCESS_KEY") or "").strip()
    account = (os.environ.get("CLOUDFLARE_ACCOUNT_ID") or "").strip()
    if key and secret and account:
        return key, secret, account
    return None


def _upload_to_r2(artifact_path: Path, key: str, sha_hex: str) -> None:
    """PUT an artifact straight into the hub bucket, bypassing the Worker.

    Single-part on purpose: R2 records a whole-object SHA-256 only for
    non-multipart uploads, and the Worker refuses to publish an object it cannot
    verify. ``put_object`` is always single-part (``upload_file`` would switch to
    multipart above its threshold and silently strip the checksum).
    """
    try:
        import boto3  # imported lazily: only the direct-upload path needs it
        from botocore.config import Config as BotoConfig
    except ImportError as e:  # pragma: no cover - environment problem, not logic
        raise SystemExit(
            "error: boto3 is required to upload artifacts larger than "
            f"{DIRECT_UPLOAD_THRESHOLD} bytes directly to R2. "
            "Install it (pip install boto3) and re-run."
        ) from e

    creds = _r2_credentials()
    if creds is None:
        raise SystemExit(
            f"error: {artifact_path.name} is too large to publish through the "
            "Worker (Cloudflare caps request bodies at 100 MB on Free/Pro), so it "
            "must go directly to R2 — but R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY "
            "and CLOUDFLARE_ACCOUNT_ID are not all set. Create an R2 API token "
            "(Cloudflare dashboard -> R2 -> Manage API Tokens) with Object "
            "Read & Write on the hub bucket and set all three."
        )
    access_key, secret_key, account_id = creds
    bucket = os.environ.get("R2_BUCKET", "gaia-hub")

    # boto3 >= 1.36 adds a CRC32 checksum to every PutObject by default and
    # sends it as an aws-chunked trailer (Content-Encoding: aws-chunked,
    # x-amz-content-sha256: STREAMING-UNSIGNED-PAYLOAD-TRAILER). R2 does not
    # accept that trailer format and rejects the request outright — as
    # `Unauthorized`, which reads like a credentials problem and is not one.
    # `when_required` suppresses the automatic checksum while still sending the
    # explicit ChecksumSHA256 below as a normal header, which is the whole point:
    # the Worker refuses to publish an object R2 recorded no SHA-256 for.
    client = boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
        config=BotoConfig(
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )
    print(
        f"[publish] uploading {artifact_path.name} -> r2://{bucket}/{key}", flush=True
    )
    with artifact_path.open("rb") as fh:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=fh,
            ContentType="application/octet-stream",
            # Base64, not hex — and this is what makes the object verifiable.
            ChecksumSHA256=base64.b64encode(bytes.fromhex(sha_hex)).decode("ascii"),
        )


def publish_one(
    base_url: str,
    manifest_path: Path,
    manifest: dict,
    artifact_path: Path,
    component: str,
    platform_key: str,
    token: str,
    docs: dict[str, bytes],
    strict_immutable: bool = False,
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

    # Oversized artifacts cannot travel through the Worker at all, so they go
    # straight to R2 and the POST below only carries their coordinates.
    by_reference = size >= DIRECT_UPLOAD_THRESHOLD
    if by_reference:
        # Check BEFORE uploading. The Worker's by-reference immutability guard
        # keys on the agent manifest and therefore fires only AFTER this PUT
        # would already have replaced the published bytes — leaving the catalog
        # describing the old artifact while R2 serves the new one, and the 409
        # handler below re-downloading the bytes it just overwrote and happily
        # agreeing with itself. Skipping the upload keeps that 409 meaningful.
        download_url = f"{base_url.rstrip('/')}/agents/{agent_id}/{version}/{filename}"
        head = requests.head(download_url, timeout=60, allow_redirects=True)
        if head.status_code == 200:
            print(
                f"[publish] {filename} is already in R2 — not overwriting it; "
                "the POST below verifies the stored bytes against this build.",
                flush=True,
            )
        else:
            _upload_to_r2(
                artifact_path, f"agents/{agent_id}/{version}/{filename}", local_sha
            )

    with contextlib.ExitStack() as stack:
        files = {
            "manifest": (
                "gaia-agent.yaml",
                manifest_path.read_bytes(),
                "application/x-yaml",
            ),
        }
        if by_reference:
            files["artifact_ref_filename"] = (None, filename)
            files["artifact_ref_sha256"] = (None, local_sha)
            files["artifact_ref_size"] = (None, str(size))
            files["artifact_ref_content_type"] = (None, "application/octet-stream")
        else:
            fh = stack.enter_context(artifact_path.open("rb"))
            files["artifact"] = (filename, fh, "application/octet-stream")
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

    published_now = False
    if resp.status_code == 201:
        body = resp.json()
        server_sha = body.get("published", {}).get("artifact", {}).get("sha256")
        if server_sha != local_sha:
            raise SystemExit(
                f"error: integrity check FAILED for {filename}: Worker stored "
                f"sha256={server_sha} but local sha256={local_sha}. The upload was "
                "corrupted in transit; failing loudly."
            )
        published_now = True
        n = body.get("published", {}).get("version_artifacts", "?")
        print(
            f"[publish] OK 201 — stored, server sha256 verified. "
            f"{agent_id}@{version} now has {n} artifact(s).",
            flush=True,
        )
    elif resp.status_code == 409:
        # Only ONE of the Worker's four 409s means "already published":
        # version_exists. artifact_mismatch / artifact_unverifiable / id_conflict
        # all say "the catalog was NOT modified" -- reconciling those against the
        # R2 object would report success for a release the hub never recorded.
        error_code = ""
        try:
            error_code = str(resp.json().get("error", {}).get("code", ""))
        except ValueError:
            pass
        if error_code != "version_exists":
            raise SystemExit(
                f"error: publish of {filename} was rejected with HTTP 409 "
                f"{error_code or '(no error code)'} -- this is NOT 'already "
                f"published' and the Worker did not record it in the catalog. "
                f"{resp.text[:500]}"
            )
        # Already published. Nothing can overwrite it, so the only question is
        # what this run reports downstream.
        remote_sha, remote_size = _download_published(
            base_url, agent_id, version, filename
        )
        if remote_sha == local_sha:
            print(
                "[publish] OK 409 — already published with identical bytes "
                "(idempotent no-op).",
                flush=True,
            )
        elif strict_immutable:
            raise SystemExit(
                f"error: {filename} is already published at {agent_id}@{version} "
                f"with a DIFFERENT sha256 (remote={remote_sha}, local={local_sha}). "
                "Published artifacts are immutable — bump the version to change it."
            )
        else:
            # A rebuild of an already-released version. Neither Go nor
            # PyInstaller is byte-reproducible, so re-running any published
            # version lands here every time -- that is a no-op to skip, not a
            # failure. The PUBLISHED bytes are authoritative: report their hash
            # and size so the lock describes what the hub actually serves, never
            # this run's throwaway rebuild.
            print(
                f"::warning::{filename} is already published at "
                f"{agent_id}@{version} and this rebuild differs "
                f"(remote={remote_sha[:12]}…, local={local_sha[:12]}…). Keeping "
                f"the published bytes. If you meant to ship a CHANGE, bump the "
                f"version — a published version can never be replaced.",
                flush=True,
            )
            local_sha, size = remote_sha, remote_size
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
        # Consumed by main()'s "did anything actually ship?" report and stripped
        # before the summary is written, so the on-disk shape is unchanged.
        "_published": published_now,
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


def _preflight_oversized(artifact_args: list[str]) -> None:
    """Refuse to start when the oversized lane is needed but unavailable.

    Each artifact is published in turn, so discovering halfway through that
    boto3 or the S3 credentials are absent leaves the smaller platforms stored
    immutably under a version that can never be completed -- recoverable only by
    burning a version.
    """
    paths = [_parse_artifact_arg(raw)[0] for raw in artifact_args]
    # A missing file is checked HERE, not at its turn in the publish loop: this
    # gate promises "nothing has been published", and skipping an absent path
    # would let the earlier platforms store immutably before the typo surfaces.
    missing = [p for p in paths if not p.exists()]
    if missing:
        listing = "\n".join(f"    {p}" for p in missing)
        raise SystemExit(
            f"error: these artifacts do not exist:\n{listing}\n"
            "  Check the --artifact paths (a freeze leg that produced no binary "
            "is the usual cause).\n"
            "  Nothing has been published."
        )
    oversized = [p for p in paths if p.stat().st_size >= DIRECT_UPLOAD_THRESHOLD]
    if not oversized:
        return
    # boto3 is only imported by the upload itself, so a missing dependency would
    # otherwise surface mid-publish.
    try:
        import boto3  # noqa: F401
    except ImportError:
        listing = "\n".join(f"    {p.name}" for p in oversized)
        raise SystemExit(
            "error: these artifacts must upload over the S3 API:\n"
            f"{listing}\n"
            "  but boto3 is not installed for this interpreter.\n"
            "  Fix: add boto3 to this step's dependency install "
            "(see release_agent_gaia.yml).\n"
            "  Nothing has been published."
        ) from None
    if _r2_credentials() is None:
        listing = "\n".join(
            f"    {p.name}  ({p.stat().st_size / 1e6:.1f} MB)" for p in oversized
        )
        raise SystemExit(
            "error: these artifacts exceed the Worker request-body cap "
            f"({DIRECT_UPLOAD_THRESHOLD / 1e6:.1f} MB) and must upload straight to "
            f"R2 over the S3 API:\n{listing}\n"
            "  but R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY and CLOUDFLARE_ACCOUNT_ID "
            "are not all set.\n"
            "  Fix: pass all three to this step (see release_agent_gaia.yml), from "
            "an R2 API token with\n"
            "       Object Read & Write (Cloudflare -> R2 -> Manage R2 API Tokens).\n"
            "  Note: the cap is 100 MB on BOTH the Free and Pro plans, so upgrading "
            "to Pro does not\n"
            "        remove this requirement.\n"
            "  Nothing has been published."
        )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Publish gaia-agent sidecar binaries to the Agent Hub R2 Worker."
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
    parser.add_argument(
        "--require-new",
        action="store_true",
        help="Exit non-zero when every artifact was already published, i.e. the "
        "run shipped nothing. Catches a forgotten version bump, which is "
        "otherwise a green release that changes nothing.",
    )
    parser.add_argument(
        "--strict-immutable",
        action="store_true",
        help="Fail when an artifact is already published and this build's bytes "
        "differ. Off by default: neither Go nor PyInstaller is byte-reproducible, "
        "so re-running a published version always differs and is a no-op to skip, "
        "not a failure. Turn it on to police 'changed the code, forgot to bump'.",
    )
    args = parser.parse_args(argv)

    token = _read_token()
    manifest = _load_manifest(args.manifest)
    docs = _read_docs(args)

    # Before publishing ANYTHING: every artifact must exist, and one that needs
    # the direct-to-R2 lane must be able to use it.
    _preflight_oversized(args.artifact)

    # Oversized first. The pre-flight proves the R2 credentials are PRESENT, not
    # that R2 accepts them — an expired or read-only token still fails at the
    # PUT. Publishing that artifact first means such a token dies before any
    # smaller platform is stored immutably under a version that can never be
    # completed. (Order is otherwise irrelevant: the docs ride on every POST and
    # the summary is keyed by component/platform.)
    ordered = sorted(
        args.artifact,
        key=lambda raw: _parse_artifact_arg(raw)[0].stat().st_size
        < DIRECT_UPLOAD_THRESHOLD,
    )

    results = []
    for raw in ordered:
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
                strict_immutable=args.strict_immutable,
            )
        )

    stored = sum(1 for r in results if r.pop("_published", False))

    if args.summary_out:
        args.summary_out.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"[publish] wrote summary -> {args.summary_out}", flush=True)

    version = str(manifest["version"])
    if stored == 0 and results:
        print(
            f"::warning::nothing new was stored — all {len(results)} artifact(s) "
            f"were already published at {manifest['id']}@{version}. If you meant "
            f"to ship a change, bump 'version' in the manifest: a published "
            f"version can never be replaced, so re-running it is a no-op.",
            flush=True,
        )
        if args.require_new:
            raise SystemExit(
                f"error: --require-new was set and {manifest['id']}@{version} was "
                "already fully published. Bump the manifest version."
            )
    print(
        f"[publish] DONE — {len(results)} artifact(s) verified, {stored} newly stored.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
