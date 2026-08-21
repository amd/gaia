#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Prove the R2 S3 credentials can actually publish, before a release tries.

``util/verify_publish_pipeline.py`` covers the publish *logic* against a local
Worker with deliberately bogus credentials. It says nothing about whether the
real ``R2_ACCESS_KEY_ID`` / ``R2_SECRET_ACCESS_KEY`` pair works -- and that is
the half that has actually been failing, with ``PutObject -> Unauthorized``.
This script covers the other half.

Every artifact at or over the Worker's request-body cap (the Agent UI
installers at 111-141 MB, the gaia sidecar's Linux build at ~120 MB) is PUT
straight to R2 over the S3 API. R2 answers a bad credential AND a malformed
request with the same ``Unauthorized``, so the error alone never says which,
and re-pasting the secret has been the default guess. These checks separate
them, in increasing order of privilege, and name the first that fails:

    1. can it see the bucket?                (scoped correctly)
    2. can it WRITE, with a checksum?        (Object Read & Write, not Read only)
    3. can it write at RELEASE SIZE?         (the request shape boto3 builds for
                                              a large single-part upload)
    4. does the hub Worker serve it back?    (same bucket, not another account's)

Run it against the credentials a release would use::

    python util/check_r2_credentials.py                 # reads .env, then the environment
    python util/check_r2_credentials.py --size-mb 120   # match the real sidecar

It writes probe objects under ``agents/_credential-probe/`` and deletes them.
That prefix is never a published agent, and a direct S3 write does not touch
``index.json`` -- only the Worker's ``/publish`` rebuilds the catalog -- so a
probe leaves no trace in the hub listing. No secret value is ever printed.

WHAT THIS STILL CANNOT PROVE
----------------------------
That the credential works **from GitHub's runners**. It runs from wherever you
run it. A token with Client IP Address Filtering passes here and fails in CI --
if every check below passes and CI still returns Unauthorized, that filter (or
an expired TTL) is the remaining explanation.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"
DEFAULT_BUCKET = "gaia-hub"
PUBLIC_BASE = "https://hub.amd-gaia.ai"

# Never a published agent id, so a probe can never collide with a real release.
PROBE_PREFIX = "agents/_credential-probe"
REQUIRED = ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "CLOUDFLARE_ACCOUNT_ID")


def load_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines. Real environment variables take precedence."""
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def fail(headline: str, *lines: str) -> int:
    print(f"\nFAILED: {headline}")
    for line in lines:
        print(f"  {line}")
    return 1


def _http_get(url: str, timeout: int = 60) -> tuple[int, bytes]:
    """GET via curl.

    Not urllib: Cloudflare's browser-integrity check answers a
    ``Python-urllib`` user agent with a 403 (error 1010) on this zone, which
    reads exactly like "the object is not there" and is not.
    """
    proc = subprocess.run(
        ["curl", "-s", "-w", "\n%{http_code}", "--max-time", str(timeout), url],
        capture_output=True,
        text=False,
    )
    body, _, code = proc.stdout.rpartition(b"\n")
    try:
        return int(code.decode().strip()), body
    except ValueError:
        return 0, body


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--size-mb",
        type=int,
        default=118,
        help="Size of the large-upload probe. Default 118 (the Agent UI "
        "installer); use 120 for the gaia Linux sidecar.",
    )
    parser.add_argument(
        "--skip-large",
        action="store_true",
        help="Skip the release-size upload (checks 1, 2 and 4 only).",
    )
    args = parser.parse_args(argv)

    file_env = load_env_file(ENV_FILE)

    def get(name: str) -> str:
        return (os.environ.get(name) or file_env.get(name) or "").strip()

    try:
        import boto3
        from botocore.config import Config as BotoConfig
        from botocore.exceptions import ClientError
    except ImportError:
        return fail(
            "boto3 is not installed for this interpreter.",
            "Fix: uv pip install boto3   (or pip install boto3), then re-run.",
        )

    missing = [n for n in REQUIRED if not get(n)]
    if missing:
        return fail(
            f"not set: {', '.join(missing)}",
            f"Fill them in at {ENV_FILE} (gitignored), or export them.",
            "Cloudflare -> R2 -> Manage R2 API Tokens -> Create API token,",
            "permission 'Object Read & Write'. It shows the Access Key ID and",
            "Secret Access Key exactly once.",
        )

    key, secret, account = (get(n) for n in REQUIRED)
    bucket = get("R2_BUCKET") or DEFAULT_BUCKET
    endpoint = f"https://{account}.r2.cloudflarestorage.com"

    print(f"env file : {ENV_FILE}{'' if ENV_FILE.exists() else '  (absent)'}")
    print(f"account  : {account[:6]}...{account[-4:]} (len {len(account)})")
    print(f"key id   : {key[:6]}...{key[-4:]} (len {len(key)})")
    print(f"secret   : (len {len(secret)})")
    print(f"bucket   : {bucket}\n")

    # An R2 S3 access key id is 32 hex characters and its secret 64. A
    # Cloudflare *API token* pasted into either slot is a different credential
    # type entirely and can never authenticate here.
    for name, value, want in (("access key id", key, 32), ("secret", secret, 64)):
        if len(value) != want:
            print(
                f"WARNING: {name} is {len(value)} chars; R2 S3 credentials are "
                f"{want}. A Cloudflare API token is the wrong credential type "
                f"for the S3 API.\n"
            )

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=key,
        aws_secret_access_key=secret,
        region_name="auto",
        # Must match publish_to_r2.py: boto3 >= 1.36 otherwise sends an
        # aws-chunked trailer that R2 rejects as `Unauthorized`, which would
        # make this script blame the credential for a request-shape problem.
        config=BotoConfig(
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )

    def code_of(exc) -> str:
        return exc.response.get("Error", {}).get("Code", "?")

    # ListBuckets is account-level and a correctly bucket-scoped token is denied
    # it, so this is informational only -- gating on it reports a working
    # credential as broken.
    print("[1/4] head_bucket   - is it scoped to this bucket? ......... ", end="")
    try:
        client.head_bucket(Bucket=bucket)
        print("OK")
    except ClientError as e:
        print("FAILED")
        return fail(
            f"cannot access bucket '{bucket}' ({code_of(e)}).",
            "Either the key/secret pair is wrong, it belongs to a different",
            f"Cloudflare account than CLOUDFLARE_ACCOUNT_ID ({account[:6]}...),",
            "or the token is not scoped to this bucket.",
        )
    except Exception as e:
        print("FAILED")
        return fail(
            f"cannot reach {endpoint}: {e}", "Check the account id and network."
        )

    small_key = f"{PROBE_PREFIX}/probe.txt"
    marker = f"probe-{int(time.time())}".encode()
    print("[2/4] put_object    - can it WRITE, with a checksum? ....... ", end="")
    try:
        client.put_object(
            Bucket=bucket,
            Key=small_key,
            Body=marker,
            ContentType="text/plain",
            ChecksumSHA256=base64.b64encode(hashlib.sha256(marker).digest()).decode(),
        )
        print("OK")
    except ClientError as e:
        print("FAILED")
        return fail(
            f"cannot write to '{bucket}' ({code_of(e)}).",
            "It authenticates and sees the bucket, so this is a READ-ONLY token.",
            "Re-issue it with 'Object Read & Write' and update BOTH .env and the",
            "GitHub secrets (repository AND the agent-publish environment, whose",
            "value overrides the repository one).",
        )

    if args.skip_large:
        print("[3/4] release-size upload ................................. SKIPPED")
    else:
        size = args.size_mb * 1024 * 1024
        big_key = f"{PROBE_PREFIX}/large-probe.bin"
        tmp = Path(os.environ.get("TEMP", "/tmp")) / "r2-credential-probe.bin"
        if not tmp.exists() or tmp.stat().st_size != size:
            block = os.urandom(1024 * 1024)
            with tmp.open("wb") as fh:
                for _ in range(size // len(block)):
                    fh.write(block)
        digest = hashlib.sha256()
        with tmp.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)

        print(
            f"[3/4] put_object    - at release size ({args.size_mb} MB) ......... ",
            end="",
            flush=True,
        )
        start = time.time()
        try:
            with tmp.open("rb") as fh:
                client.put_object(
                    Bucket=bucket,
                    Key=big_key,
                    Body=fh,
                    ContentType="application/octet-stream",
                    ChecksumSHA256=base64.b64encode(
                        bytes.fromhex(digest.hexdigest())
                    ).decode(),
                )
            elapsed = time.time() - start
            print(f"OK ({elapsed:.0f}s, {size / 1e6 / max(elapsed, 1):.1f} MB/s)")
        except ClientError as e:
            print("FAILED")
            tmp.unlink(missing_ok=True)
            return fail(
                f"a {args.size_mb} MB upload failed ({code_of(e)}) where a small "
                "one succeeded.",
                "That is not a permissions problem -- it is the large-upload",
                "request shape. Compare this client's botocore config with",
                "publish_to_r2.py's _upload_to_r2.",
            )
        finally:
            tmp.unlink(missing_ok=True)
        client.delete_object(Bucket=bucket, Key=big_key)

    # The credential could write to a DIFFERENT account's bucket of the same
    # name. Only the Worker serving the bytes back proves it is the hub's.
    print("[4/4] round-trip    - does the hub Worker serve it back? ... ", end="")
    time.sleep(2)
    status, body = _http_get(f"{PUBLIC_BASE}/{small_key}")
    same_bucket = status == 200 and body.strip() == marker
    print("OK" if same_bucket else f"HTTP {status}")
    client.delete_object(Bucket=bucket, Key=small_key)

    if not same_bucket:
        return fail(
            "the write succeeded but the hub Worker cannot serve it back.",
            f"GET {PUBLIC_BASE}/{small_key} returned HTTP {status}.",
            "The credential addresses a different account's bucket than the one",
            "behind hub.amd-gaia.ai. CLOUDFLARE_ACCOUNT_ID is what to re-check.",
        )

    print(
        "\nPASS - these credentials can publish release-sized artifacts to the "
        "hub's bucket.\n"
        "If CI still fails with Unauthorized, the values stored in GitHub differ\n"
        "from these, or the token has Client IP Address Filtering / an expired\n"
        "TTL that refuses GitHub's runners. Check the agent-publish environment\n"
        "scope first: its secrets override the repository ones."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
