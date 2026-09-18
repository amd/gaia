#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Find out whether the R2 credentials can publish, and if not, why.

``util/verify_publish_pipeline.py`` covers the publish *logic* against a local
Worker using deliberately bogus credentials. It says nothing about the real
``R2_ACCESS_KEY_ID`` / ``R2_SECRET_ACCESS_KEY`` pair -- and that is the half
that actually fails, always as ``PutObject -> Unauthorized``.

Every artifact at or over the Worker's request-body cap (the Agent UI
installers at 111-141 MB, the gaia sidecar's Linux build at ~120 MB) is PUT
straight to R2 over the S3 API. R2 answers a bad credential, a mismatched key
pair, a wrong endpoint and a whitespace-corrupted secret with the SAME
``Unauthorized``, so the error never says which. These probes separate them::

    python util/check_r2_credentials.py
    python util/check_r2_credentials.py --size-mb 120     # match the real sidecar
    python util/check_r2_credentials.py --compare a1b2...,c3d4...,e5f6...

WHAT EACH PROBE RULES OUT
-------------------------
[0] fingerprints  A SHA-256 of each value, safe to log. ``--compare`` diffs them
    against another machine's or CI's, which is the only way to see that the
    stored copy is simply a DIFFERENT credential. A mismatched key id paired
    with a good secret is indistinguishable from every other cause, and no
    amount of re-pasting the secret fixes it.
[1] whitespace    Checked on the RAW value, before stripping. A trailing newline
    is trivially stored (``gh secret set`` keeps whatever it is handed, and the
    GitHub UI does not render it) and is signed as part of the credential.
[2] shape         An R2 S3 access key id is 32 hex characters and its secret 64.
    Cloudflare also shows a "Token value" above them on creation -- a different
    credential entirely, which can never authenticate here.
[3] head_bucket   Authenticates, and is scoped to this bucket.
[4] put plain     The simplest possible write.
[5] put+checksum  Exactly the call publish_to_r2.py makes, including the
    botocore config that stops boto3 sending an aws-chunked trailer R2 rejects.
[6] jurisdiction  A bucket created with a jurisdiction is ONLY reachable at
    ``<account>.eu.r2.cloudflarestorage.com``; the standard endpoint answers
    Unauthorized however good the token is. Probed only when the standard
    endpoint refused, where it is the diagnosis rather than noise.
[7] release size  A single-part PUT at real artifact size, which exercises the
    request shape boto3 builds for a large body.
[8] round-trip    Fetches the object back through the hub Worker. A credential
    can happily write to a same-named bucket in a DIFFERENT account; only this
    proves it is the bucket the hub actually serves.

Reading the error codes matters more than pass/fail::

    Unauthorized           token not valid for this account/bucket, or wrong endpoint
    AccessDenied           right token, insufficient permission (Object Read only)
    NoSuchBucket           right account, wrong bucket name
    InvalidAccessKeyId     key id not recognised at all
    SignatureDoesNotMatch  secret wrong, or whitespace in either value

Credentials come from ``.env`` (gitignored) or the environment, environment
first. Probes are written under ``agents/_credential-probe/`` and deleted; that
prefix is never a published agent, and a direct S3 write does not touch
``index.json`` -- only the Worker's ``/publish`` rebuilds the catalog -- so a
probe leaves no trace in the hub listing. No secret value is ever printed.

WHAT THIS STILL CANNOT PROVE
----------------------------
That the credential works **from GitHub's runners**. It runs wherever you run
it. A token with Client IP Address Filtering passes here and fails in CI --
use ``--compare`` against a runner's fingerprints to tell that apart from CI
simply holding different values.
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

PROBE_PREFIX = "agents/_credential-probe"
REQUIRED = ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "CLOUDFLARE_ACCOUNT_ID")
EXPECTED_LEN = {
    "R2_ACCESS_KEY_ID": 32,
    "R2_SECRET_ACCESS_KEY": 64,
    "CLOUDFLARE_ACCOUNT_ID": 32,
}


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


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.strip().encode()).hexdigest()[:16]


def err(exc) -> str:
    """Error code plus HTTP status -- the pair is more diagnostic than either."""
    r = getattr(exc, "response", {}) or {}
    code = (r.get("Error") or {}).get("Code", type(exc).__name__)
    status = (r.get("ResponseMetadata") or {}).get("HTTPStatusCode", "?")
    return f"{code} (HTTP {status})"


def http_get(url: str, timeout: int = 60) -> tuple[int, bytes]:
    """GET via curl.

    Not urllib: Cloudflare's browser-integrity check answers a
    ``Python-urllib`` user agent with a 403 (error 1010) on this zone, which
    reads exactly like "the object is not there" and is not.
    """
    proc = subprocess.run(
        ["curl", "-s", "-w", "\n%{http_code}", "--max-time", str(timeout), url],
        capture_output=True,
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
        help="Release-size probe. Default 118 (Agent UI installer); 120 is the "
        "gaia Linux sidecar.",
    )
    parser.add_argument("--skip-large", action="store_true", help="Skip probe [7].")
    parser.add_argument(
        "--compare",
        metavar="KEY_FP,SECRET_FP,ACCOUNT_FP",
        help="Three 16-char fingerprints from another machine or a CI run, to "
        "diff against these. Different fingerprints mean the other side simply "
        "holds a different credential.",
    )
    parser.add_argument("--bucket", default=os.environ.get("R2_BUCKET", DEFAULT_BUCKET))
    args = parser.parse_args(argv)

    file_env = load_env_file(ENV_FILE)

    def raw(name: str) -> str:
        # NOT stripped: probe [1] has to see the value as the publisher does.
        value = os.environ.get(name)
        return value if value is not None else file_env.get(name, "")

    try:
        import boto3
        from botocore.config import Config
        from botocore.exceptions import ClientError
    except ImportError:
        print("boto3 is not installed. Run:  uv pip install boto3")
        return 2

    missing = [n for n in REQUIRED if not raw(n).strip()]
    if missing:
        print(f"Set these first: {', '.join(missing)}")
        print(f"  in {ENV_FILE} (gitignored), or the environment.")
        print("  Cloudflare -> R2 -> Manage R2 API Tokens -> Create API token,")
        print("  permission 'Object Read & Write'.")
        return 2

    values = {n: raw(n) for n in REQUIRED}
    bucket = args.bucket

    print("[0] fingerprints")
    fps = {}
    for n in REQUIRED:
        fps[n] = fingerprint(values[n])
        print(f"      {n:22} sha256={fps[n]} len={len(values[n].strip())}")
    if args.compare:
        other = [p.strip() for p in args.compare.split(",")]
        if len(other) != 3:
            print("      --compare needs exactly three comma-separated fingerprints")
            return 2
        print("      compared against:")
        differs = False
        for n, theirs in zip(REQUIRED, other):
            same = fps[n] == theirs
            differs = differs or not same
            print(f"      {n:22} {'MATCH' if same else 'DIFFERS -> ' + theirs}")
        if differs:
            print(
                "\n      The other side holds a DIFFERENT credential. A mismatched\n"
                "      key id with a good secret fails exactly like a bad secret,\n"
                "      which is why re-pasting the secret never fixes it."
            )
    print()

    print("[1] whitespace")
    dirty = [n for n in REQUIRED if values[n] != values[n].strip()]
    for n in dirty:
        print(f"      {n}: leading/trailing whitespace  <-- BREAKS THE SIGNATURE")
    if not dirty:
        print("      clean")

    print("[2] shape")
    for n in REQUIRED:
        got, want = len(values[n].strip()), EXPECTED_LEN[n]
        flag = "" if got == want else f"  <-- expected {want}"
        print(f"      {n:22} {got} chars{flag}")
    if any(len(values[n].strip()) != EXPECTED_LEN[n] for n in REQUIRED):
        print(
            "      A wrong length usually means Cloudflare's 'Token value' was\n"
            "      pasted instead of the Access Key ID / Secret Access Key shown\n"
            "      beneath it. That is a different credential and cannot work here."
        )
    print()

    key = values["R2_ACCESS_KEY_ID"].strip()
    secret = values["R2_SECRET_ACCESS_KEY"].strip()
    account = values["CLOUDFLARE_ACCOUNT_ID"].strip()

    cfg = Config(
        # Matches publish_to_r2.py: boto3 >= 1.36 otherwise sends an aws-chunked
        # trailer R2 rejects as `Unauthorized`, which would make this script
        # blame the credential for a request-shape problem.
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
    )
    standard = f"{account}.r2.cloudflarestorage.com"
    jurisdictional = f"{account}.eu.r2.cloudflarestorage.com"

    def client(host: str):
        return boto3.client(
            "s3",
            endpoint_url=f"https://{host}",
            aws_access_key_id=key,
            aws_secret_access_key=secret,
            region_name="auto",
            config=cfg,
        )

    small = f"{PROBE_PREFIX}/probe.bin"
    marker = f"gaia-r2-probe-{int(time.time())}".encode()
    digest = base64.b64encode(hashlib.sha256(marker).digest()).decode()

    print(f"[3] head_bucket    '{bucket}' -> {standard}")
    try:
        client(standard).head_bucket(Bucket=bucket)
        print("      OK")
    except ClientError as e:
        print(f"      FAIL  {err(e)}")

    print(f"[4] put_object     plain -> {standard}")
    plain_ok = False
    try:
        client(standard).put_object(Bucket=bucket, Key=small, Body=marker)
        print("      OK")
        plain_ok = True
    except ClientError as e:
        print(f"      FAIL  {err(e)}")

    print(f"[5] put_object     + ChecksumSHA256 (the publisher's call) -> {standard}")
    publisher_ok = False
    try:
        client(standard).put_object(
            Bucket=bucket,
            Key=small,
            Body=marker,
            ContentType="application/octet-stream",
            ChecksumSHA256=digest,
        )
        print("      OK  <- this is the call a release makes")
        publisher_ok = True
    except ClientError as e:
        print(f"      FAIL  {err(e)}")

    # Only meaningful when the standard endpoint refused: a jurisdictional
    # bucket is unreachable there no matter how good the token is.
    if not (plain_ok or publisher_ok):
        print(f"[6] jurisdiction   plain -> {jurisdictional}")
        try:
            client(jurisdictional).put_object(Bucket=bucket, Key=small, Body=marker)
            print(
                "      OK  <- BUCKET IS JURISDICTIONAL. publish_to_r2.py builds the\n"
                "              standard endpoint, so it can never reach this bucket."
            )
        except ClientError as e:
            print(f"      FAIL  {err(e)}")
    else:
        print("[6] jurisdiction   skipped (standard endpoint works)")

    if not publisher_ok:
        print(
            "\nThe publisher's call was refused. Remaining variables:\n"
            "  * Cloudflare -> R2 -> Manage R2 API Tokens -> open the token\n"
            "  * Permission must be 'Object Read & Write', not 'Object Read only'\n"
            f"  * Scope must include '{bucket}', or be 'Apply to all buckets'\n"
            "  * TTL must not have expired\n"
            "  * Client IP Address Filtering must not exclude the caller\n"
            "  * Use the Access Key ID + Secret Access Key, NOT the 'Token value'"
        )
        return 1

    if args.skip_large:
        print("[7] release size   SKIPPED")
    else:
        size = args.size_mb * 1024 * 1024
        big = f"{PROBE_PREFIX}/large-probe.bin"
        tmp = Path(os.environ.get("TEMP", "/tmp")) / "r2-credential-probe.bin"
        if not tmp.exists() or tmp.stat().st_size != size:
            block = os.urandom(1024 * 1024)
            with tmp.open("wb") as fh:
                for _ in range(size // len(block)):
                    fh.write(block)
        h = hashlib.sha256()
        with tmp.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)

        print(f"[7] release size   {args.size_mb} MB single-part -> {standard}")
        start = time.time()
        try:
            with tmp.open("rb") as fh:
                client(standard).put_object(
                    Bucket=bucket,
                    Key=big,
                    Body=fh,
                    ContentType="application/octet-stream",
                    ChecksumSHA256=base64.b64encode(
                        bytes.fromhex(h.hexdigest())
                    ).decode(),
                )
            dt = time.time() - start
            print(f"      OK  ({dt:.0f}s, {size / 1e6 / max(dt, 1):.1f} MB/s)")
        except ClientError as e:
            print(f"      FAIL  {err(e)}")
            print(
                "      A small write succeeded and a large one did not: that is the\n"
                "      request shape for a large body, not permissions."
            )
            tmp.unlink(missing_ok=True)
            return 1
        finally:
            tmp.unlink(missing_ok=True)
        client(standard).delete_object(Bucket=bucket, Key=big)

    print(f"[8] round-trip     GET {PUBLIC_BASE}/{small}")
    time.sleep(2)
    status, body = http_get(f"{PUBLIC_BASE}/{small}")
    same_bucket = status == 200 and body.strip() == marker
    print(f"      {'OK' if same_bucket else f'HTTP {status}'}")
    client(standard).delete_object(Bucket=bucket, Key=small)

    if not same_bucket:
        print(
            "\nThe write succeeded but the hub Worker cannot serve it back, so this\n"
            "credential addresses a different account's bucket than the one behind\n"
            f"{PUBLIC_BASE}. CLOUDFLARE_ACCOUNT_ID is what to re-check."
        )
        return 1

    print(
        "\nPASS - these credentials can publish release-sized artifacts to the "
        "hub's bucket.\n"
        "If CI still fails with Unauthorized, re-run there and diff the "
        "fingerprints with\n--compare: matching ones point at IP filtering or "
        "TTL, differing ones mean the\nstored copy is a different credential. "
        "Check the agent-publish environment\nscope first -- its secrets "
        "override the repository ones."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
