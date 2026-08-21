# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Publish frozen email-agent binaries to the GAIA Agent Hub R2 Worker
(milestone #49, issue #1648).

POSTs each artifact + the agent's ``gaia-agent.yaml`` to the Worker's
``POST /publish`` endpoint (multipart/form-data, Bearer auth). The Worker
computes the SHA-256 server-side and stores the object immutably at
``agents/<id>/<version>/<filename>``. A single ``<id>/<version>`` accepts
multiple per-platform binaries (each a distinct filename) — see the Worker
README.

Idempotency (re-running a published release is a no-op):
  * 201 -> published. We assert the Worker-returned SHA-256 equals the SHA-256
    we computed locally (integrity/atomicity check).
  * 409 (version_exists) -> the filename is already published. We GET the stored
    object and assert its bytes hash to the SAME SHA-256 we hold. If they match
    it is a true no-op (success); if they DIFFER we fail loudly — that means a
    different binary is already published under this immutable name.

NO silent fallback: any other non-2xx, a SHA mismatch, or a missing token
raises with an actionable message.

Auth: the Bearer token is read from the ``AGENT_HUB_PUBLISH_TOKEN`` environment
variable ONLY. It is never logged, echoed, or written to disk.

Usage::

    AGENT_HUB_PUBLISH_TOKEN=*** python publish_to_r2.py \
        --base-url https://hub.example.workers.dev \
        --manifest hub/agents/email/python/gaia-agent.yaml \
        --artifact dist/email-agent-win32-x64.exe=win32-x64 \
        [--artifact dist/email-agent-linux-x64=linux-x64 ...] \
        [--summary-out published.json]

Each ``--artifact`` is ``<path>[=<platform-key>]``. The platform key is recorded
in the summary JSON (consumed by gen_binaries_lock.py); if omitted it is
inferred from the filename suffix.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import os
import sys
from pathlib import Path

import requests
import yaml

PUBLISH_PATH = "/publish"
TOKEN_ENV = "AGENT_HUB_PUBLISH_TOKEN"


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


def _parse_artifact_arg(arg: str) -> tuple[Path, str | None]:
    """Split ``<path>[=<platform-key>]`` into (path, platform_key|None)."""
    if "=" in arg:
        raw_path, _, key = arg.rpartition("=")
        return Path(raw_path), key or None
    return Path(arg), None


def _infer_platform_key(filename: str) -> str:
    """Infer the platform key from ``email-agent-<key>[.exe]``."""
    stem = filename
    if stem.endswith(".exe"):
        stem = stem[: -len(".exe")]
    prefix = "email-agent-"
    if not stem.startswith(prefix):
        raise SystemExit(
            f"error: cannot infer platform key from '{filename}'. Pass it "
            "explicitly as <path>=<platform-key>."
        )
    return stem[len(prefix) :]


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


# Cloudflare caps a Worker request body by plan — 100 MB on Free/Pro. Anything
# at or above this goes straight to R2 over the S3 API instead, and is published
# by reference. Deliberately below the real cap so an artifact that grows into
# the limit switches lanes before it starts 413ing mid-release.
DIRECT_UPLOAD_THRESHOLD = 90 * 1024 * 1024


def _r2_credentials() -> tuple[str, str, str] | None:
    """R2 S3 credentials, or None when direct upload is not configured."""
    key = os.environ.get("R2_ACCESS_KEY_ID")
    secret = os.environ.get("R2_SECRET_ACCESS_KEY")
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
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
    platform_key: str,
    token: str,
    readme_bytes: bytes | None = None,
    changelog_bytes: bytes | None = None,
    spec_bytes: bytes | None = None,
    skill_bytes: bytes | None = None,
    evaluation_bytes: bytes | None = None,
    capability_matrix_bytes: bytes | None = None,
    eval_scorecard_bytes: bytes | None = None,
    package_files_bytes: bytes | None = None,
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
        f"[publish] {filename} ({size} bytes, sha256={local_sha[:12]}…) "
        f"-> {agent_id}@{version}",
        flush=True,
    )

    # Oversized artifacts cannot travel through the Worker at all (Cloudflare
    # caps the request body at 100 MB on Free/Pro), so they go straight to R2 and
    # the POST below only carries their coordinates. The Worker verifies the
    # stored object's size and SHA-256 before recording it, so this is a
    # different transport, not a weaker guarantee.
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
        # Same multipart field name + shape the Worker expects from
        # `gaia agent publish` (src/gaia/hub/publisher.py): the README becomes
        # the catalog entry's `readme` (rendered as sanitized markdown on the
        # website). The README rides along on every POST so the index always
        # reflects the latest published README; Workers predating the field
        # ignore the unknown part.
        if readme_bytes is not None:
            files["readme"] = ("README.md", readme_bytes, "text/markdown")
        # The CHANGELOG rides along the same way — it becomes the catalog entry's
        # `changelog`, rendered as a Changelog section on the hub agent page.
        if changelog_bytes is not None:
            files["changelog"] = ("CHANGELOG.md", changelog_bytes, "text/markdown")
        # SPEC.md + SKILL.md ride along the same way — they become the catalog
        # entry's `spec` / `skill`, rendered as their own doc tabs on the hub page.
        if spec_bytes is not None:
            files["spec"] = ("SPEC.md", spec_bytes, "text/markdown")
        if skill_bytes is not None:
            files["skill"] = ("SKILL.md", skill_bytes, "text/markdown")
        # EVALUATION.md rides along the same way — it becomes the catalog entry's
        # `evaluation`, rendered as its own doc tab on the hub page.
        if evaluation_bytes is not None:
            files["evaluation"] = ("EVALUATION.md", evaluation_bytes, "text/markdown")
        # CAPABILITY_MATRIX.md rides along the same way — it becomes the catalog
        # entry's `capability_matrix`, rendered as its own doc tab on the hub page.
        if capability_matrix_bytes is not None:
            files["capability_matrix"] = (
                "CAPABILITY_MATRIX.md",
                capability_matrix_bytes,
                "text/markdown",
            )
        # The eval scorecard rides along with the first platform binary and becomes
        # the catalog entry's `eval_score` and `eval_scorecard_url`.
        if eval_scorecard_bytes is not None:
            files["eval_scorecard"] = (
                "eval-scorecard.md",
                eval_scorecard_bytes,
                "text/markdown",
            )
        # The whole-package file listing rides with the zip artifact — it becomes
        # the catalog entry's `package.files` (the hub's file-list display).
        if package_files_bytes is not None:
            files["package_files"] = (
                "package-files.json",
                package_files_bytes,
                "application/json",
            )
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
        # Already published. Nothing can overwrite it, so the only question is
        # what this run reports downstream.
        remote_sha, remote_size = _download_published(
            base_url, agent_id, version, filename
        )
        if remote_sha == local_sha:
            print(
                f"[publish] OK 409 — already published with identical bytes "
                f"(idempotent no-op).",
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

    executable = "email-agent.exe" if filename.endswith(".exe") else "email-agent"
    return {
        "platform": platform_key,
        "filename": filename,
        "executable": executable,
        "sha256": local_sha,
        "size": size,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Publish email-agent binaries to R2.")
    parser.add_argument(
        "--base-url", required=True, help="Worker origin, e.g. https://hub.example."
    )
    parser.add_argument(
        "--manifest", required=True, type=Path, help="gaia-agent.yaml path."
    )
    parser.add_argument(
        "--artifact",
        action="append",
        required=True,
        metavar="PATH[=PLATFORM]",
        help="Artifact file, optionally with =<platform-key>. Repeatable.",
    )
    parser.add_argument(
        "--readme",
        type=Path,
        help="Path to README.md to publish as the agent's catalog readme "
        "(POSTed as the multipart 'readme' part the Worker accepts).",
    )
    parser.add_argument(
        "--changelog",
        type=Path,
        help="Path to CHANGELOG.md to publish as the agent's catalog changelog "
        "(POSTed as the multipart 'changelog' part the Worker accepts).",
    )
    parser.add_argument(
        "--spec",
        type=Path,
        help="Path to SPEC.md to publish as the agent's catalog spec "
        "(POSTed as the multipart 'spec' part the Worker accepts).",
    )
    parser.add_argument(
        "--skill",
        type=Path,
        help="Path to SKILL.md to publish as the agent's catalog skill "
        "(POSTed as the multipart 'skill' part the Worker accepts).",
    )
    parser.add_argument(
        "--evaluation",
        type=Path,
        help="Path to EVALUATION.md to publish as the agent's catalog evaluation "
        "(POSTed as the multipart 'evaluation' part the Worker accepts).",
    )
    parser.add_argument(
        "--capability-matrix",
        type=Path,
        help="Path to CAPABILITY_MATRIX.md to publish as the agent's catalog "
        "capability matrix (POSTed as the multipart 'capability_matrix' part "
        "the Worker accepts).",
    )
    parser.add_argument(
        "--eval-scorecard",
        type=Path,
        help="Path to the eval scorecard markdown (e.g. SCORECARD.md) to "
        "publish as the agent's catalog eval score and scorecard URL "
        "(POSTed as the multipart 'eval_scorecard' part the Worker accepts). "
        "Absent = publish without an eval scorecard.",
    )
    parser.add_argument(
        "--package-files",
        type=Path,
        help='Path to a package-files.json ({"files":[{name,size_bytes}]}) to '
        "attach to the zip artifact (POSTed as the 'package_files' part). It "
        "becomes the catalog's package.files (the hub's file-list display).",
    )
    parser.add_argument(
        "--summary-out",
        type=Path,
        help="Write a JSON array of {platform,filename,executable,sha256,size}.",
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

    readme_bytes = None
    if args.readme is not None:
        if not args.readme.exists():
            raise SystemExit(
                f"error: --readme path not found: {args.readme}. Pass the agent's "
                "README.md, or omit --readme to publish without one."
            )
        readme_bytes = args.readme.read_bytes()
        print(
            f"[publish] attaching readme: {args.readme} ({len(readme_bytes)} bytes)",
            flush=True,
        )

    changelog_bytes = None
    if args.changelog is not None:
        if not args.changelog.exists():
            raise SystemExit(
                f"error: --changelog path not found: {args.changelog}. Pass the "
                "agent's CHANGELOG.md, or omit --changelog to publish without one."
            )
        changelog_bytes = args.changelog.read_bytes()
        print(
            f"[publish] attaching changelog: {args.changelog} "
            f"({len(changelog_bytes)} bytes)",
            flush=True,
        )

    spec_bytes = None
    if args.spec is not None:
        if not args.spec.exists():
            raise SystemExit(
                f"error: --spec path not found: {args.spec}. Pass the agent's "
                "SPEC.md, or omit --spec to publish without one."
            )
        spec_bytes = args.spec.read_bytes()
        print(
            f"[publish] attaching spec: {args.spec} ({len(spec_bytes)} bytes)",
            flush=True,
        )

    skill_bytes = None
    if args.skill is not None:
        if not args.skill.exists():
            raise SystemExit(
                f"error: --skill path not found: {args.skill}. Pass the agent's "
                "SKILL.md, or omit --skill to publish without one."
            )
        skill_bytes = args.skill.read_bytes()
        print(
            f"[publish] attaching skill: {args.skill} ({len(skill_bytes)} bytes)",
            flush=True,
        )

    evaluation_bytes = None
    if args.evaluation is not None:
        if not args.evaluation.exists():
            raise SystemExit(
                f"error: --evaluation path not found: {args.evaluation}. Pass the "
                "agent's EVALUATION.md, or omit --evaluation to publish without one."
            )
        evaluation_bytes = args.evaluation.read_bytes()
        print(
            f"[publish] attaching evaluation: {args.evaluation} "
            f"({len(evaluation_bytes)} bytes)",
            flush=True,
        )

    capability_matrix_bytes = None
    if args.capability_matrix is not None:
        if not args.capability_matrix.exists():
            raise SystemExit(
                f"error: --capability-matrix path not found: {args.capability_matrix}. "
                "Pass the agent's CAPABILITY_MATRIX.md, or omit --capability-matrix "
                "to publish without one."
            )
        capability_matrix_bytes = args.capability_matrix.read_bytes()
        print(
            f"[publish] attaching capability matrix: {args.capability_matrix} "
            f"({len(capability_matrix_bytes)} bytes)",
            flush=True,
        )

    eval_scorecard_bytes = None
    if args.eval_scorecard is not None:
        if not args.eval_scorecard.exists():
            raise SystemExit(
                f"error: --eval-scorecard path not found: {args.eval_scorecard}. "
                "Pass the scorecard markdown, or omit --eval-scorecard to publish "
                "without one."
            )
        eval_scorecard_bytes = args.eval_scorecard.read_bytes()
        print(
            f"[publish] attaching eval scorecard: {args.eval_scorecard} "
            f"({len(eval_scorecard_bytes)} bytes)",
            flush=True,
        )

    package_files_bytes = None
    if args.package_files is not None:
        if not args.package_files.exists():
            raise SystemExit(
                f"error: --package-files path not found: {args.package_files}."
            )
        package_files_bytes = args.package_files.read_bytes()
        print(
            f"[publish] attaching package file list: {args.package_files} "
            f"({len(package_files_bytes)} bytes)",
            flush=True,
        )

    # Pre-flight the oversized lane BEFORE publishing anything. Each artifact is
    # published in turn, so discovering halfway through that the S3 credentials
    # are absent leaves the smaller platforms stored immutably under a version
    # that can never be completed -- recoverable only by burning a version.
    oversized = [
        p
        for p in (_parse_artifact_arg(raw)[0] for raw in args.artifact)
        if p.exists() and p.stat().st_size >= DIRECT_UPLOAD_THRESHOLD
    ]
    if oversized and _r2_credentials() is None:
        listing = "\n".join(
            f"    {p.name}  ({p.stat().st_size / 1e6:.1f} MB)" for p in oversized
        )
        raise SystemExit(
            "error: these artifacts exceed the Worker request-body cap "
            f"({DIRECT_UPLOAD_THRESHOLD / 1e6:.1f} MB) and must upload straight to "
            f"R2 over the S3 API:\n{listing}\n"
            "  but R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY and CLOUDFLARE_ACCOUNT_ID "
            "are not all set.\n"
            "  Fix: pass all three to this step (see release_components.yml), from "
            "an R2 API token with\n"
            "       Object Read & Write (Cloudflare -> R2 -> Manage R2 API Tokens).\n"
            "  Note: the cap is 100 MB on BOTH the Free and Pro plans, so upgrading "
            "to Pro does not\n"
            "        remove this requirement.\n"
            "  Nothing has been published."
        )

    results = []
    for raw in args.artifact:
        path, key = _parse_artifact_arg(raw)
        # A .zip is the whole-package artifact (not a platform binary); it has no
        # platform key to infer, so default it to "package".
        platform_key = key or (
            "package"
            if path.name.lower().endswith(".zip")
            else _infer_platform_key(path.name)
        )
        results.append(
            publish_one(
                args.base_url,
                args.manifest,
                manifest,
                path,
                platform_key,
                token,
                readme_bytes=readme_bytes,
                changelog_bytes=changelog_bytes,
                spec_bytes=spec_bytes,
                skill_bytes=skill_bytes,
                evaluation_bytes=evaluation_bytes,
                capability_matrix_bytes=capability_matrix_bytes,
                eval_scorecard_bytes=eval_scorecard_bytes,
                package_files_bytes=package_files_bytes,
                strict_immutable=args.strict_immutable,
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
    sys.exit(main())
