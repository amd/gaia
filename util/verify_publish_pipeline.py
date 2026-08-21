# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
End-to-end regression test for the Agent Hub publish pipeline, run entirely on
localhost.

Run it with no arguments::

    python util/verify_publish_pipeline.py

It boots ``workers/agent-hub`` under ``wrangler dev`` with Miniflare's simulated
R2, drives ``hub/agents/email/python/packaging/publish_to_r2.py`` against it, and
asserts on behaviour. Exit code is 0 only if every case passes.

WHAT THIS PROVES
----------------
A  A normal-sized artifact publishes through the Worker POST lane (201), lands in
   the catalog, and downloads back with the SHA-256 the publisher computed.
B  Re-publishing identical bytes is an idempotent no-op, not a failure.
C  Re-publishing DIFFERENT bytes under a published version is skipped with a
   ``::warning::``, and the run reports the PUBLISHED hash/size -- not the local
   rebuild's. A lock built from that summary describes bytes users can download.
D  ``--strict-immutable`` restores the hard failure for case C.
E  An artifact at/over the Worker request-body cap with no R2 credentials is
   refused up front, naming the offending files, having published NOTHING --
   including the small siblings that would otherwise be stored first, immutably.
F  An oversized artifact already in R2 publishes by reference: the Worker
   verifies the stored object's size and the SHA-256 R2 itself recorded, and only
   then records it. A wrong claimed hash is rejected.
G  The same pre-flight refuses to start when boto3 is missing, before storing
   anything -- credentials alone are not enough to make the upload possible.
H  ``--require-new`` turns "this run stored nothing new" into a hard failure,
   while the default stays lenient with a warning that says so.
I  The platform key and executable name are derived from the artifact filename
   rather than hardcoded to one product. Both are load-bearing:
   release_agent_gaia.yml passes its binaries with no explicit
   ``=<platform-key>``, and release_agent_email.yml feeds this summary into
   gen_binaries_lock.py, which reads ``executable`` straight out of it.

WHAT THIS CANNOT PROVE
----------------------
* That the real ``R2_ACCESS_KEY_ID`` / ``R2_SECRET_ACCESS_KEY`` pair works from
  GitHub's IP space against the real ``gaia-hub`` bucket. This harness uses
  deliberately bogus credentials that never authenticate anywhere.
* That boto3's SigV4 request is accepted by REAL R2. Miniflare records whatever
  checksum it is handed; real R2 computes its own and can reject the request
  shape (see the ``when_required`` checksum note in publish_to_r2.py).
* Real artifact sizes. The oversized fixture is a synthetic 90 MiB file, not the
  ~120 MB sidecar or the ~135 MB Agent UI installers.
* The macOS PyInstaller freeze legs, the Cloudflare WAF behaviour that forces
  uploads onto the workers.dev hostname, or the release workflows' asset waits.

SAFETY
------
The base URL is asserted to be loopback before anything runs (see
``_assert_localhost``). There is no flag, env var, or code path that lets this
harness talk to hub.amd-gaia.ai or any workers.dev host: published objects there
are permanent and public.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKER_DIR = REPO_ROOT / "workers" / "agent-hub"
PUBLISHER = REPO_ROOT / "hub/agents/email/python/packaging/publish_to_r2.py"

# Must match DIRECT_UPLOAD_THRESHOLD in publish_to_r2.py. Asserted at startup so
# a change there fails here loudly instead of silently testing the wrong lane.
DIRECT_UPLOAD_THRESHOLD = 90 * 1024 * 1024

AGENT_ID = "zz-pipeline-test"
PUBLISH_TOKEN = "zz-local-verify-token"

# Never a real account: any boto3 call that escapes to the network dies on an
# untrusted host rather than authenticating against Cloudflare.
BOGUS_R2 = {
    "R2_ACCESS_KEY_ID": "zz-local-bogus-key",
    "R2_SECRET_ACCESS_KEY": "zz-local-bogus-secret",
    "CLOUDFLARE_ACCOUNT_ID": "zz-local-bogus-account",
}

# gaia's own dev API. Binding it would kill a running session.
FORBIDDEN_PORTS = {4001}

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


class Skip(Exception):
    """The environment cannot run this harness (missing node, etc.)."""


def _assert_localhost(base_url: str) -> None:
    """Refuse any non-loopback target. The live hub has no unpublish route."""
    host = (urlparse(base_url).hostname or "").lower()
    if host not in LOOPBACK_HOSTS:
        raise SystemExit(
            f"refusing to run against {base_url!r}: this harness publishes test "
            f"artifacts and only loopback targets are allowed (got host {host!r}). "
            "Published objects on hub.amd-gaia.ai are permanent and public -- "
            "there is no delete route."
        )


def _free_port() -> int:
    for _ in range(50):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if port not in FORBIDDEN_PORTS:
            return port
    raise SystemExit("error: could not find a free loopback port.")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _http(url: str, method: str = "GET") -> tuple[int, bytes]:
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        raise SystemExit(f"error: {method} {url} failed: {e}") from e


# ---------------------------------------------------------------------------
# Worker lifecycle
# ---------------------------------------------------------------------------

# wrangler dev is pointed at this generated entry instead of src/index.ts. It
# re-exports the real Worker unchanged and adds ONE test-only route, /zz-seed,
# which writes an object into the local bucket WITH a recorded whole-object
# SHA-256 -- what a single-part S3 PUT carrying x-amz-checksum-sha256 produces,
# and the only thing the by-reference lane will accept. Neither `wrangler r2
# object put` nor Miniflare's host-side R2 proxy can produce that for a 90 MiB
# object, so case F cannot be exercised without it.
SEED_ENTRY = """\
import worker from "./src/index";

const hex = (buf) =>
  [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname === "/zz-seed") {
      const key = url.searchParams.get("key");
      const size = Number(url.searchParams.get("size"));
      const bytes = new Uint8Array(size).fill(0x43);
      bytes.set(new TextEncoder().encode("BIG"), 0);
      const digest = await crypto.subtle.digest("SHA-256", bytes);
      await env.BUCKET.put(key, bytes, {
        sha256: digest,
        httpMetadata: { contentType: "application/octet-stream" },
      });
      const head = await env.BUCKET.head(key);
      return Response.json({
        key,
        size: head.size,
        sha256: head.checksums?.sha256 ? hex(head.checksums.sha256) : null,
      });
    }
    return worker.fetch(request, env, ctx);
  },
};
"""


def _kill_tree(pid: int) -> None:
    """Kill the wrangler process AND its workerd child."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    import signal

    for kill in (
        lambda: os.killpg(os.getpgid(pid), signal.SIGTERM),
        lambda: os.kill(pid, signal.SIGTERM),
    ):
        try:
            kill()
            return
        except (ProcessLookupError, PermissionError, OSError):
            continue


class Worker:
    """A `wrangler dev` Worker on loopback, with simulated R2."""

    def __init__(self, persist_dir: Path) -> None:
        self.port = _free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._persist = persist_dir
        self._proc: subprocess.Popen | None = None
        self._entry = WORKER_DIR / ".zz-verify-entry.ts"
        self._dev_vars = WORKER_DIR / ".dev.vars"
        self._dev_vars_backup: bytes | None = None
        self.log = persist_dir / "wrangler.log"

    def __enter__(self) -> "Worker":
        _assert_localhost(self.base_url)
        npx = shutil.which("npx")
        if not npx:
            raise Skip("npx (Node.js) is not on PATH")
        if not (WORKER_DIR / "node_modules").is_dir():
            npm = shutil.which("npm")
            if not npm:
                raise Skip("npm is not on PATH")
            print(f"[setup] installing Worker deps (npm ci in {WORKER_DIR})...")
            if subprocess.run([npm, "ci"], cwd=WORKER_DIR).returncode != 0:
                raise Skip("npm ci failed in workers/agent-hub")

        # Back up a developer's real .dev.vars rather than clobbering it.
        if self._dev_vars.exists():
            self._dev_vars_backup = self._dev_vars.read_bytes()
        tokens = {PUBLISH_TOKEN: {"publisher": "zz-local-verify", "authors": ["*"]}}
        self._dev_vars.write_text(
            f"PUBLISH_TOKENS={json.dumps(tokens)}\n"
            "WORKER_BUILD=zz-verify-publish-pipeline\n",
            encoding="utf-8",
        )
        self._entry.write_text(SEED_ENTRY, encoding="utf-8")

        cmd = [
            npx,
            "wrangler",
            "dev",
            self._entry.name,
            "--local",
            "--ip",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--persist-to",
            str(self._persist / "wrangler-state"),
        ]
        print(f"[setup] {' '.join(cmd)}")
        with self.log.open("wb") as fh:
            self._proc = subprocess.Popen(
                cmd,
                cwd=WORKER_DIR,
                stdout=fh,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
        self._await_health()
        return self

    def _tail(self, n: int = 3000) -> str:
        try:
            return self.log.read_text(errors="replace")[-n:]
        except OSError:
            return "(no wrangler log)"

    def _await_health(self, timeout: float = 180.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._proc and self._proc.poll() is not None:
                raise SystemExit(
                    f"error: wrangler dev exited early (code {self._proc.returncode}).\n"
                    + self._tail()
                )
            try:
                status, body = _http(f"{self.base_url}/health")
                if status == 200:
                    print(f"[setup] Worker ready on {self.base_url}: {body.decode()!r}")
                    return
            except SystemExit:
                pass
            time.sleep(1)
        raise SystemExit("error: wrangler dev never became healthy.\n" + self._tail())

    def __exit__(self, *exc) -> None:
        if self._proc and self._proc.poll() is None:
            _kill_tree(self._proc.pid)
        self._entry.unlink(missing_ok=True)
        if self._dev_vars_backup is not None:
            self._dev_vars.write_bytes(self._dev_vars_backup)
        else:
            self._dev_vars.unlink(missing_ok=True)

    # -- helpers used by the cases ------------------------------------------

    def seed(self, key: str, size: int) -> dict:
        """Put an object into local R2 with a recorded whole-object SHA-256."""
        status, body = _http(f"{self.base_url}/zz-seed?key={key}&size={size}")
        if status != 200:
            raise SystemExit(f"error: seeding {key} failed: HTTP {status} {body!r}")
        return json.loads(body)

    def manifest(self) -> dict | None:
        status, body = _http(f"{self.base_url}/agents/{AGENT_ID}/manifest.json")
        return json.loads(body) if status == 200 else None

    def versions(self) -> list[str]:
        m = self.manifest()
        return sorted(m["versions"]) if m else []

    def artifacts(self, version: str) -> list[str]:
        m = self.manifest()
        entry = (m or {}).get("versions", {}).get(version)
        return sorted(a["filename"] for a in entry["artifacts"]) if entry else []

    def download(self, version: str, filename: str) -> tuple[int, bytes]:
        return _http(f"{self.base_url}/agents/{AGENT_ID}/{version}/{filename}")


# ---------------------------------------------------------------------------
# Driving the publisher
# ---------------------------------------------------------------------------

# Runs publish_to_r2.py with boto3 (and botocore) unimportable, reproducing a CI
# runner whose publisher deps were installed without it.
NO_BOTO_RUNNER = """\
import runpy, sys
from importlib.abc import MetaPathFinder


class _Block(MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("boto3", "botocore"):
            raise ImportError(f"No module named {name!r}")
        return None


sys.meta_path.insert(0, _Block())
target = sys.argv[1]
sys.argv = sys.argv[1:]
runpy.run_path(target, run_name="__main__")
"""


@dataclass
class Run:
    returncode: int
    output: str

    def says(self, *needles: str) -> bool:
        return all(n.lower() in self.output.lower() for n in needles)


@dataclass
class Ctx:
    worker: Worker
    tmp: Path
    dist: Path
    no_boto_runner: Path
    manifests: dict[str, Path] = field(default_factory=dict)

    def manifest_for(self, version: str) -> Path:
        if version not in self.manifests:
            path = self.tmp / f"gaia-agent-{version}.yaml"
            path.write_text(
                f"id: {AGENT_ID}\n"
                "name: ZZ Pipeline Test\n"
                f"version: {version}\n"
                "description: Throwaway component used by util/verify_publish_pipeline.py.\n"
                "author: zz-local-verify\n"
                "license: MIT\n"
                "language: go\n"
                "type: component\n"
                "category: general\n"
                "security_tier: experimental\n"
                "requirements:\n"
                "  platforms:\n"
                "    - win-x64\n"
                "    - linux-x64\n"
                "    - darwin-arm64\n"
                "interfaces:\n"
                "  cli: true\n",
                encoding="utf-8",
            )
            self.manifests[version] = path
        return self.manifests[version]

    def write_artifact(self, name: str, payload: bytes) -> tuple[Path, str]:
        path = self.dist / name
        path.write_bytes(payload)
        return path, _sha256(payload)

    def publish(
        self,
        version: str,
        artifacts: list[str],
        *,
        extra: list[str] | None = None,
        creds: bool = False,
        block_boto3: bool = False,
        summary: str | None = None,
    ) -> tuple[Run, list[dict] | None]:
        summary_path = self.tmp / summary if summary else None
        if summary_path:
            summary_path.unlink(missing_ok=True)

        cmd = [sys.executable]
        if block_boto3:
            cmd.append(str(self.no_boto_runner))
        cmd += [
            str(PUBLISHER),
            "--base-url",
            self.worker.base_url,
            "--manifest",
            str(self.manifest_for(version)),
        ]
        for a in artifacts:
            cmd += ["--artifact", a]
        if summary_path:
            cmd += ["--summary-out", str(summary_path)]
        cmd += extra or []

        env = {k: v for k, v in os.environ.items() if k not in BOGUS_R2}
        env["AGENT_HUB_PUBLISH_TOKEN"] = PUBLISH_TOKEN
        if creds:
            env.update(BOGUS_R2)

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            errors="replace",
            env=env,
            cwd=REPO_ROOT,
            timeout=900,
        )
        run = Run(proc.returncode, proc.stdout + proc.stderr)
        data = (
            json.loads(summary_path.read_text(encoding="utf-8"))
            if summary_path and summary_path.exists()
            else None
        )
        return run, data


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

SMALL = b"V1-" + b"A" * (1_048_576 - 3)
REBUILD = b"V2-REBUILD-" + b"B" * (2_000_000 - 11)
SIBLING = b"SMALL-SIBLING" * 1000
# Byte-for-byte what the Worker's /zz-seed route generates, so a seeded object
# and this local file are the same artifact.
OVERSIZED = b"BIG" + b"C" * (DIRECT_UPLOAD_THRESHOLD - 3)

LINUX = f"{AGENT_ID}-linux-x64"
DARWIN = f"{AGENT_ID}-darwin-arm64"
WIN = f"{AGENT_ID}-win-x64.exe"


def case_a(c: Ctx) -> str:
    path, sha = c.write_artifact(LINUX, SMALL)
    run, summary = c.publish("0.0.1", [f"{path}=linux-x64"], summary="a.json")
    assert run.returncode == 0, f"expected exit 0, got {run.returncode}\n{run.output}"
    assert run.says("ok 201"), f"expected a 201 publish\n{run.output}"
    status, body = c.worker.download("0.0.1", LINUX)
    assert status == 200, f"artifact not downloadable: HTTP {status}"
    assert _sha256(body) == sha, "downloaded bytes do not match the published sha256"
    assert summary and summary[0]["sha256"] == sha, "summary sha256 mismatch"
    assert c.worker.artifacts("0.0.1") == [LINUX], "catalog does not list the artifact"
    return f"201, downloaded {len(body)} bytes, sha256 {sha[:12]}... matches"


def case_b(c: Ctx) -> str:
    path, sha = c.write_artifact(LINUX, SMALL)
    run, summary = c.publish("0.0.1", [f"{path}=linux-x64"], summary="b.json")
    assert run.returncode == 0, f"expected exit 0, got {run.returncode}\n{run.output}"
    assert run.says(
        "409", "identical bytes"
    ), f"expected an idempotent no-op\n{run.output}"
    assert summary and summary[0]["sha256"] == sha, "summary sha256 mismatch"
    return "409 reconciled as an idempotent no-op, exit 0"


def case_c(c: Ctx) -> str:
    published_sha, published_size = _sha256(SMALL), len(SMALL)
    path, local_sha = c.write_artifact(LINUX, REBUILD)
    run, summary = c.publish("0.0.1", [f"{path}=linux-x64"], summary="c.json")
    assert run.returncode == 0, f"expected exit 0, got {run.returncode}\n{run.output}"
    assert "::warning::" in run.output, f"expected a ::warning::\n{run.output}"
    assert summary, "no summary written"
    got = summary[0]
    assert got["sha256"] != local_sha, (
        "summary reports the LOCAL rebuild's hash -- a lock built from it would "
        "describe bytes nobody can download"
    )
    assert (
        got["sha256"] == published_sha
    ), f"summary sha256 {got['sha256']} != remote {published_sha}"
    assert (
        got["size"] == published_size
    ), f"summary size {got['size']} != remote {published_size}"
    return f"::warning::, exit 0, summary carries REMOTE {published_sha[:12]}.../{published_size}"


def case_d(c: Ctx) -> str:
    path, _ = c.write_artifact(LINUX, REBUILD)
    run, summary = c.publish(
        "0.0.1", [f"{path}=linux-x64"], extra=["--strict-immutable"], summary="d.json"
    )
    assert run.returncode != 0, f"expected a non-zero exit\n{run.output}"
    assert run.says("immutable", "bump the version"), f"wrong error\n{run.output}"
    assert summary is None, "a summary was written for a failed run"
    return "non-zero exit, 'immutable -- bump the version'"


def _assert_nothing_published(c: Ctx, version: str, names: list[str]) -> None:
    assert (
        version not in c.worker.versions()
    ), f"version {version} exists in the catalog -- something WAS published"
    for n in names:
        status, _ = c.worker.download(version, n)
        assert status == 404, f"{n} is downloadable (HTTP {status}) -- it was published"


def case_e(c: Ctx) -> str:
    sibling, _ = c.write_artifact(DARWIN, SIBLING)
    big, _ = c.write_artifact(WIN, OVERSIZED)
    run, summary = c.publish(
        "0.0.2",
        [f"{sibling}=darwin-arm64", f"{big}=win-x64"],
        creds=False,
        summary="e.json",
    )
    assert run.returncode != 0, f"expected a non-zero exit\n{run.output}"
    assert (
        WIN in run.output
    ), f"the error does not name the offending file\n{run.output}"
    assert run.says(
        "nothing has been published"
    ), f"missing the no-op promise\n{run.output}"
    assert run.says(
        "pro"
    ), f"the error should note Pro does not raise the cap\n{run.output}"
    assert summary is None, "a summary was written for a failed run"
    _assert_nothing_published(c, "0.0.2", [DARWIN, WIN])
    return "non-zero exit, names the file, catalog untouched (small sibling included)"


def _post_by_reference(
    c: Ctx, version: str, filename: str, sha: str
) -> tuple[int, bytes]:
    """Minimal multipart by-reference POST, used to prove the hash check bites."""
    boundary = "----zzverify"
    parts = [
        ("manifest", "gaia-agent.yaml", c.manifest_for(version).read_bytes()),
        ("artifact_ref_filename", None, filename.encode()),
        ("artifact_ref_sha256", None, sha.encode()),
        ("artifact_ref_size", None, str(len(OVERSIZED)).encode()),
    ]
    body = b""
    for name, fname, value in parts:
        disp = f'form-data; name="{name}"'
        if fname:
            disp += f'; filename="{fname}"'
        body += (
            f"--{boundary}\r\nContent-Disposition: {disp}\r\n\r\n".encode()
            + value
            + b"\r\n"
        )
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{c.worker.base_url}/publish",
        data=body,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {PUBLISH_TOKEN}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def case_f(c: Ctx) -> str:
    big, sha = c.write_artifact(WIN, OVERSIZED)
    seeded = c.worker.seed(f"agents/{AGENT_ID}/0.0.3/{WIN}", len(OVERSIZED))
    assert (
        seeded["sha256"] == sha
    ), f"seeded object hashes to {seeded['sha256']}, local file {sha}"
    sibling, _ = c.write_artifact(DARWIN, SIBLING)
    run, _ = c.publish(
        "0.0.3",
        [f"{sibling}=darwin-arm64", f"{big}=win-x64"],
        creds=True,
        summary="f.json",
    )
    assert run.returncode == 0, f"expected exit 0, got {run.returncode}\n{run.output}"
    assert run.says("already in r2"), f"expected the no-overwrite path\n{run.output}"
    assert c.worker.artifacts("0.0.3") == sorted(
        [DARWIN, WIN]
    ), "catalog is missing an artifact"
    status, body = c.worker.download("0.0.3", WIN)
    assert (
        status == 200 and _sha256(body) == sha
    ), "by-reference artifact is not served intact"

    # The verification must be real, not a rubber stamp: a wrong claimed hash for
    # a correctly-seeded object has to be refused.
    c.worker.seed(f"agents/{AGENT_ID}/0.0.9/{WIN}", len(OVERSIZED))
    wrong_but_well_formed = "de" + "ad" * 31  # 64 hex chars: passes the shape check
    status, body = _post_by_reference(c, "0.0.9", WIN, wrong_but_well_formed)
    assert (
        status == 409 and b"artifact_mismatch" in body
    ), f"a wrong claimed sha256 was NOT rejected: HTTP {status} {body[:300]!r}"
    assert (
        "0.0.9" not in c.worker.versions()
    ), "the rejected publish still touched the catalog"
    return (
        f"201 by reference ({len(OVERSIZED)} bytes verified); a bad claimed hash -> 409"
    )


def case_g(c: Ctx) -> str:
    sibling, _ = c.write_artifact(DARWIN, SIBLING)
    big, _ = c.write_artifact(WIN, OVERSIZED)
    run, summary = c.publish(
        "0.0.4",
        [f"{sibling}=darwin-arm64", f"{big}=win-x64"],
        creds=True,
        block_boto3=True,
        summary="g.json",
    )
    assert run.returncode != 0, f"expected a non-zero exit\n{run.output}"
    assert run.says("boto3"), f"the error does not mention boto3\n{run.output}"
    assert (
        WIN in run.output
    ), f"the error does not name the offending file\n{run.output}"
    assert summary is None, "a summary was written for a failed run"
    _assert_nothing_published(c, "0.0.4", [DARWIN, WIN])
    return "non-zero exit before storing anything, names boto3 and the file"


def case_h(c: Ctx) -> str:
    before = c.worker.artifacts("0.0.1")
    assert before, "case H needs a published version; run it after case A"
    path, _ = c.write_artifact(LINUX, SMALL)

    strict, _ = c.publish(
        "0.0.1", [f"{path}=linux-x64"], extra=["--require-new"], summary="h1.json"
    )
    assert strict.returncode != 0, (
        f"--require-new accepted a run that stored nothing (exit {strict.returncode})\n"
        f"{strict.output}"
    )

    lenient, _ = c.publish("0.0.1", [f"{path}=linux-x64"], summary="h2.json")
    assert (
        lenient.returncode == 0
    ), f"expected exit 0 without the flag\n{lenient.output}"
    # Not just "any ::warning::" -- case C emits one too, so a bare check would
    # pass for the wrong reason. This must be the nothing-shipped warning.
    assert lenient.says(
        "::warning::", "nothing new was stored"
    ), f"a run that stored nothing should say so\n{lenient.output}"
    assert c.worker.artifacts("0.0.1") == before, "the catalog changed"
    return "--require-new -> non-zero; default -> exit 0 + 'nothing new was stored'"


def case_i(c: Ctx) -> str:
    """The platform key and executable name are derived, not hardcoded.

    Both matter beyond cosmetics: release_agent_gaia.yml passes its binaries with
    no explicit ``=<platform-key>``, and release_agent_email.yml feeds this
    summary into gen_binaries_lock.py, which reads ``rec["executable"]``.
    """
    gaia, _ = c.write_artifact("gaia-agent-linux-x64", SIBLING)
    email, _ = c.write_artifact("email-agent-win32-x64.exe", SIBLING + b"x")
    # Deliberately NO "=platform" -- inference is half of what this proves.
    run, summary = c.publish("0.0.5", [str(gaia), str(email)], summary="i.json")
    assert run.returncode == 0, f"expected exit 0, got {run.returncode}\n{run.output}"
    assert summary, "no summary written"
    got = {r["filename"]: (r["platform"], r["executable"]) for r in summary}
    expected = {
        "gaia-agent-linux-x64": ("linux-x64", "gaia-agent"),
        "email-agent-win32-x64.exe": ("win32-x64", "email-agent.exe"),
    }
    assert (
        got == expected
    ), f"derived names wrong:\n  got      {got}\n  expected {expected}"
    assert (
        "_published" not in summary[0]
    ), "the internal _published flag leaked into the summary"
    return "gaia-agent-linux-x64 -> gaia-agent; email-agent-win32-x64.exe -> email-agent.exe"


CASES = [
    ("A", "small artifact, happy path", case_a),
    ("B", "re-publish identical bytes", case_b),
    ("C", "re-publish different bytes (remote hash wins)", case_c),
    ("D", "--strict-immutable restores the hard failure", case_d),
    ("E", "oversized pre-flight, no R2 credentials", case_e),
    ("F", "oversized by-reference, verified against R2", case_f),
    ("G", "oversized pre-flight, boto3 missing", case_g),
    ("H", "--require-new when a run stores nothing", case_h),
    ("I", "platform key + executable name are derived", case_i),
]


def _read_threshold() -> int:
    """Read DIRECT_UPLOAD_THRESHOLD out of the publisher without importing it."""
    import ast

    tree = ast.parse(PUBLISHER.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "DIRECT_UPLOAD_THRESHOLD"
            for t in node.targets
        ):
            return int(eval(compile(ast.Expression(node.value), "<t>", "eval")))
    raise SystemExit("error: DIRECT_UPLOAD_THRESHOLD not found in publish_to_r2.py")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the Agent Hub publish pipeline against a LOCAL Worker.",
        epilog="There is no option to target a remote hub. See the module docstring.",
    )
    parser.add_argument(
        "--only",
        metavar="LETTERS",
        help="Run only these cases, e.g. --only ACF. Default: all.",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Leave the temp dir (fixtures + wrangler log) in place for debugging.",
    )
    args = parser.parse_args(argv)

    if not PUBLISHER.exists():
        raise SystemExit(f"error: publisher not found at {PUBLISHER}")
    threshold = _read_threshold()
    if threshold != DIRECT_UPLOAD_THRESHOLD:
        raise SystemExit(
            f"error: publish_to_r2.py's DIRECT_UPLOAD_THRESHOLD is {threshold}, but this "
            f"harness builds its oversized fixture at {DIRECT_UPLOAD_THRESHOLD}. Update "
            "DIRECT_UPLOAD_THRESHOLD in this file so cases E/F/G still test the right lane."
        )

    selected = [c for c in CASES if not args.only or c[0] in args.only.upper()]
    tmp = Path(tempfile.mkdtemp(prefix="zz-verify-publish-"))
    results: list[tuple[str, str, bool, str]] = []
    try:
        dist = tmp / "dist"
        dist.mkdir()
        runner = tmp / "_no_boto3.py"
        runner.write_text(NO_BOTO_RUNNER, encoding="utf-8")

        try:
            with Worker(tmp) as worker:
                ctx = Ctx(worker=worker, tmp=tmp, dist=dist, no_boto_runner=runner)
                for letter, title, fn in selected:
                    print(f"\n=== {letter}. {title} ===", flush=True)
                    try:
                        detail = fn(ctx)
                        results.append((letter, title, True, detail))
                        print(f"  PASS  {detail}", flush=True)
                    except AssertionError as e:
                        first = str(e).split("\n")[0]
                        results.append((letter, title, False, first))
                        print(f"  FAIL  {e}", flush=True)
        except Skip as e:
            print(f"\nSKIPPED: {e}.")
            print(
                "  This harness needs Node.js to run the Agent Hub Worker locally. "
                "Install Node 20+ and re-run; nothing was verified."
            )
            return 0
    finally:
        if args.keep_temp:
            print(f"\n[cleanup] temp dir kept: {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 72)
    for letter, title, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {letter}. {title}")
        print(f"          {detail}")
    failed = [r for r in results if not r[2]]
    print("=" * 72)
    print(f"{len(results) - len(failed)}/{len(results)} passed")
    if failed:
        print(
            "\nThe publish pipeline is NOT verified. Fix the failures above before "
            "running a release workflow -- a bad publish to hub.amd-gaia.ai is permanent."
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
