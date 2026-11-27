# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Helpers shared by the two Agent Hub publishers.

hub/components/terminal-hub/publish_to_r2.py and
hub/agents/gaia/python/packaging/publish_to_r2.py are different programs (their
artifact keys, summaries and doc handling differ), but they talk to the same
Worker and the same R2 bucket. The parts that must behave identically in both
live here, so a fix to one lane cannot miss the other.

Both scripts put this directory on ``sys.path`` before importing it.
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

import requests
import yaml

PUBLISH_PATH = "/publish"
TOKEN_ENV = "AGENT_HUB_PUBLISH_TOKEN"

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


def _listed_sha256(base_url: str, agent_id: str, version: str, filename: str) -> str:
    """SHA-256 the catalog lists for this filename -- what installs verify against."""
    url = f"{base_url.rstrip('/')}/agents/{agent_id}/manifest.json"
    resp = requests.get(url, headers={"accept": "application/json"}, timeout=60)
    if resp.status_code != 200:
        raise SystemExit(
            f"error: 409 said '{filename}' exists but GET {url} returned "
            f"HTTP {resp.status_code}. Cannot confirm the catalog's checksum; "
            "failing loudly."
        )
    try:
        catalog = resp.json()
    except ValueError as e:
        raise SystemExit(f"error: GET {url} did not return JSON: {e}") from e
    for channel in ("versions", "next_versions"):
        entry = (catalog.get(channel) or {}).get(version) or {}
        for art in entry.get("artifacts") or []:
            if art.get("filename") == filename and art.get("sha256"):
                return str(art["sha256"])
    raise SystemExit(
        f"error: 409 said '{filename}' exists but {url} lists no sha256 for it "
        f"under {version}. Cannot confirm what installs verify against; failing loudly."
    )


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
