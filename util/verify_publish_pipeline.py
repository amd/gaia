# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
End-to-end regression test for the Agent Hub publish pipeline, run entirely on
localhost.

Run it with no arguments::

    python util/verify_publish_pipeline.py

It boots ``workers/agent-hub`` under ``wrangler dev`` with Miniflare's simulated
R2, drives a publisher against it, and asserts on behaviour. Exit code is 0 only
if every applicable case passes.

TWO PUBLISHERS, NOT ONE
-----------------------
There are two ``publish_to_r2.py`` scripts and they are different programs::

    hub/agents/email/python/packaging/publish_to_r2.py   --publisher email
        release_agent_email.yml, release_agent_chat.yml, release_components.yml

    hub/agents/gaia/python/packaging/publish_to_r2.py    --publisher gaia
        release_agent_gaia.yml

Proving one says NOTHING about the other, which is why this runs both by default
and labels every row with the publisher it came from. They differ today in more
than plumbing: the gaia copy has no direct-to-R2 lane (it refuses anything at the
request-body cap and tells you to port one), still hard-fails a differing
rebuild, takes ``<component>:<platform>`` artifact keys rather than
``<platform>``, and has neither opt-in flag. Those differences live in ``SPECS``;
a case needing a capability a publisher lacks is reported **N/A**, never passed.
When the R2 lane is ported to the gaia publisher, flip its ``direct_r2`` and
``lenient_rebuild`` and the relevant cases start applying to it.

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
   rather than hardcoded to one product. Both are load-bearing: the release
   workflows pass binaries with no explicit artifact key, and
   release_agent_email.yml feeds this summary into gen_binaries_lock.py, which
   reads ``executable`` straight out of it.
J  A publisher WITHOUT a direct-to-R2 lane refuses an oversized artifact at the
   cap rather than 413ing mid-release. This is the gaia release's live blocker:
   its Linux sidecar is ~120 MB.

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

# The size at which an artifact stops fitting through the Worker. Checked against
# each publisher's own constant at startup, so moving it there fails here loudly
# instead of silently testing the wrong lane.
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


@dataclass(frozen=True)
class Spec:
    """One publisher script, and which behaviours it currently claims to have.

    There are TWO publishers and they are not the same program. Every case below
    names the one it exercises, because proving the email publisher says nothing
    about the gaia release -- release_agent_gaia.yml runs the gaia copy.
    """

    key: str
    path: Path
    threshold_const: str
    drives: str
    # Capabilities. A case that needs one the publisher lacks is reported N/A
    # rather than passed or failed, so the table never overstates coverage.
    direct_r2: bool
    lenient_rebuild: bool
    strict_immutable_flag: bool
    require_new_flag: bool
    # (filename, expected platform, expected executable) for case I.
    executable_cases: tuple[tuple[str, str, str], ...]
    _artifact_key: str = "{platform}"

    def artifact_arg(self, path: Path, platform: str) -> str:
        return f"{path}={self._artifact_key.format(platform=platform)}"


SPECS = {
    "email": Spec(
        key="email",
        path=REPO_ROOT / "hub/agents/email/python/packaging/publish_to_r2.py",
        threshold_const="DIRECT_UPLOAD_THRESHOLD",
        drives="release_agent_email.yml, release_agent_chat.yml, release_components.yml",
        direct_r2=True,
        lenient_rebuild=True,
        strict_immutable_flag=True,
        require_new_flag=True,
        executable_cases=(
            ("gaia-agent-linux-x64", "linux-x64", "gaia-agent"),
            ("email-agent-win32-x64.exe", "win32-x64", "email-agent.exe"),
        ),
    ),
    # A separate 367-line script. It has NONE of the email publisher's fixes: no
    # direct-to-R2 lane (it hard-fails at the cap and tells you to port one), the
    # old hard-failure on a differing rebuild, and neither opt-in flag. Its
    # artifact key is <component>:<platform>, not <platform>.
    "gaia": Spec(
        key="gaia",
        path=REPO_ROOT / "hub/agents/gaia/python/packaging/publish_to_r2.py",
        threshold_const="DIRECT_UPLOAD_THRESHOLD",
        drives="release_agent_gaia.yml",
        direct_r2=True,
        lenient_rebuild=True,
        strict_immutable_flag=True,
        require_new_flag=True,
        executable_cases=(("gaia-agent-linux-x64", "linux-x64", "gaia-agent"),),
        _artifact_key="sidecar:{platform}",
    ),
}


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

    # Refuse to signal our OWN group: the child is spawned with
    # start_new_session, so a shared group means that call failed and killpg
    # would take this process (and the CI step) down with it.
    def _kill_group() -> None:
        pgid = os.getpgid(pid)
        if pgid == os.getpgid(0):
            raise OSError(f"pid {pid} shares our process group ({pgid})")
        os.killpg(pgid, signal.SIGTERM)

    for kill in (
        _kill_group,
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
        # POSIX: give wrangler its OWN process group. Teardown kills the group so
        # workerd dies with its parent, and without this that group is the one we
        # are running in -- on a GitHub runner every case passed and the step then
        # exited 143, because the SIGTERM reached the shell too.
        spawn: dict = {}
        if os.name != "nt":
            spawn["start_new_session"] = True
        with self.log.open("wb") as fh:
            self._proc = subprocess.Popen(
                cmd,
                cwd=WORKER_DIR,
                stdout=fh,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                **spawn,
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
    spec: Spec
    tmp: Path
    dist: Path
    no_boto_runner: Path
    manifests: dict[str, Path] = field(default_factory=dict)

    def arg(self, path: Path, platform: str) -> str:
        return self.spec.artifact_arg(path, platform)

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
            str(self.spec.path),
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
    run, summary = c.publish("0.0.1", [c.arg(path, "linux-x64")], summary="a.json")
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
    run, summary = c.publish("0.0.1", [c.arg(path, "linux-x64")], summary="b.json")
    assert run.returncode == 0, f"expected exit 0, got {run.returncode}\n{run.output}"
    assert run.says(
        "409", "identical bytes"
    ), f"expected an idempotent no-op\n{run.output}"
    assert summary and summary[0]["sha256"] == sha, "summary sha256 mismatch"
    return "409 reconciled as an idempotent no-op, exit 0"


def case_c(c: Ctx) -> str:
    published_sha, published_size = _sha256(SMALL), len(SMALL)
    path, local_sha = c.write_artifact(LINUX, REBUILD)
    run, summary = c.publish("0.0.1", [c.arg(path, "linux-x64")], summary="c.json")

    if not c.spec.lenient_rebuild:
        # This publisher still hard-fails a differing rebuild. Assert THAT, so the
        # table records real behaviour -- and so porting the lenient path here has
        # to come with a deliberate flip of `lenient_rebuild` on the spec.
        assert run.returncode != 0, (
            f"{c.spec.key} publisher is marked lenient_rebuild=False but exited 0\n"
            f"{run.output}"
        )
        assert run.says("immutable"), f"wrong error\n{run.output}"
        return "non-zero exit (this publisher has no lenient rebuild path yet)"

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
        "0.0.1",
        [c.arg(path, "linux-x64")],
        extra=["--strict-immutable"],
        summary="d.json",
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
        [c.arg(sibling, "darwin-arm64"), c.arg(big, "win-x64")],
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
        [c.arg(sibling, "darwin-arm64"), c.arg(big, "win-x64")],
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
        [c.arg(sibling, "darwin-arm64"), c.arg(big, "win-x64")],
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
        "0.0.1", [c.arg(path, "linux-x64")], extra=["--require-new"], summary="h1.json"
    )
    assert strict.returncode != 0, (
        f"--require-new accepted a run that stored nothing (exit {strict.returncode})\n"
        f"{strict.output}"
    )

    lenient, _ = c.publish("0.0.1", [c.arg(path, "linux-x64")], summary="h2.json")
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
    expected, args = {}, []
    for i, (filename, platform, executable) in enumerate(c.spec.executable_cases):
        path, _ = c.write_artifact(filename, SIBLING + bytes([i]))
        # Deliberately NO explicit artifact key -- inference is half of the proof.
        args.append(str(path))
        expected[filename] = (platform, executable)
    run, summary = c.publish("0.0.5", args, summary="i.json")
    assert run.returncode == 0, f"expected exit 0, got {run.returncode}\n{run.output}"
    assert summary, "no summary written"
    got = {r["filename"]: (r["platform"], r["executable"]) for r in summary}
    assert (
        got == expected
    ), f"derived names wrong:\n  got      {got}\n  expected {expected}"
    assert (
        "_published" not in summary[0]
    ), "the internal _published flag leaked into the summary"
    return "; ".join(f"{f} -> {e}" for f, (_, e) in sorted(expected.items()))


def case_j(c: Ctx) -> str:
    """A publisher with no direct-to-R2 lane must refuse an oversized artifact.

    This is the gaia release's real blocker: the Linux sidecar is ~120 MB against
    a publisher that stops at the cap. When the lane is ported, flip ``direct_r2``
    on the spec and cases E/F/G take over from this one.
    """
    sibling, _ = c.write_artifact(DARWIN, SIBLING)
    big, _ = c.write_artifact(WIN, OVERSIZED)
    run, summary = c.publish(
        "0.0.6",
        [c.arg(sibling, "darwin-arm64"), c.arg(big, "win-x64")],
        creds=True,
        summary="j.json",
    )
    assert run.returncode != 0, f"expected a non-zero exit\n{run.output}"
    assert (
        WIN in run.output
    ), f"the error does not name the offending file\n{run.output}"
    assert run.says("cap"), f"the error should name the request-body cap\n{run.output}"
    assert summary is None, "a summary was written for a failed run"
    return "non-zero exit at the request-body cap, as this publisher has no R2 lane"


# (letter, title, fn, required capability). A case whose capability the selected
# publisher lacks is reported N/A -- never silently passed. "!x" means the case
# applies only when the publisher does NOT have x.
CASES = [
    ("A", "small artifact, happy path", case_a, None),
    ("B", "re-publish identical bytes", case_b, None),
    ("C", "re-publish different bytes", case_c, None),
    (
        "D",
        "--strict-immutable restores the hard failure",
        case_d,
        "strict_immutable_flag",
    ),
    ("E", "oversized pre-flight, no R2 credentials", case_e, "direct_r2"),
    ("F", "oversized by-reference, verified against R2", case_f, "direct_r2"),
    ("G", "oversized pre-flight, boto3 missing", case_g, "direct_r2"),
    ("H", "--require-new when a run stores nothing", case_h, "require_new_flag"),
    ("I", "platform key + executable name are derived", case_i, None),
    ("J", "oversized refused (no direct-to-R2 lane)", case_j, "!direct_r2"),
]


def _applies(spec: Spec, need: str | None) -> bool:
    if need is None:
        return True
    if need.startswith("!"):
        return not getattr(spec, need[1:])
    return bool(getattr(spec, need))


def _read_threshold(spec: Spec) -> int:
    """Read a publisher's request-body cap constant without importing it."""
    import ast

    tree = ast.parse(spec.path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == spec.threshold_const
            for t in node.targets
        ):
            return int(eval(compile(ast.Expression(node.value), "<t>", "eval")))
    raise SystemExit(
        f"error: {spec.threshold_const} not found in {spec.path.relative_to(REPO_ROOT)}"
    )


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
        "--publisher",
        choices=[*SPECS, "both"],
        default="both",
        help="Which publisher script to exercise. There are TWO and they are not "
        "the same program: 'email' backs the email/chat/components releases, "
        "'gaia' backs release_agent_gaia.yml. Default: both.",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Leave the temp dir (fixtures + wrangler log) in place for debugging.",
    )
    args = parser.parse_args(argv)

    specs = (
        list(SPECS.values()) if args.publisher == "both" else [SPECS[args.publisher]]
    )
    for spec in specs:
        if not spec.path.exists():
            raise SystemExit(f"error: {spec.key} publisher not found at {spec.path}")
        threshold = _read_threshold(spec)
        if threshold != DIRECT_UPLOAD_THRESHOLD:
            raise SystemExit(
                f"error: {spec.path.relative_to(REPO_ROOT)} sets {spec.threshold_const} "
                f"to {threshold}, but this harness builds its oversized fixture at "
                f"{DIRECT_UPLOAD_THRESHOLD}. Update DIRECT_UPLOAD_THRESHOLD here so the "
                "oversized cases still test the right lane."
            )

    results: list[tuple[str, str, str, str, str]] = []
    for spec in specs:
        print(
            f"\n########  publisher: {spec.key}  ({spec.path.relative_to(REPO_ROOT)})"
        )
        print(f"########  drives: {spec.drives}")
        # A fresh Worker per publisher: both write to the same agent id, so one
        # bucket would let the second run inherit the first's published versions.
        tmp = Path(tempfile.mkdtemp(prefix=f"zz-verify-publish-{spec.key}-"))
        try:
            dist = tmp / "dist"
            dist.mkdir()
            runner = tmp / "_no_boto3.py"
            runner.write_text(NO_BOTO_RUNNER, encoding="utf-8")
            try:
                with Worker(tmp) as worker:
                    ctx = Ctx(
                        worker=worker,
                        spec=spec,
                        tmp=tmp,
                        dist=dist,
                        no_boto_runner=runner,
                    )
                    for letter, title, fn, need in CASES:
                        if args.only and letter not in args.only.upper():
                            continue
                        if not _applies(spec, need):
                            results.append(
                                (
                                    spec.key,
                                    letter,
                                    title,
                                    "N/A",
                                    f"publisher has no {need}",
                                )
                            )
                            print(f"\n=== {letter}. {title} ===\n  N/A   no {need}")
                            continue
                        print(f"\n=== {letter}. {title} ===", flush=True)
                        try:
                            detail = fn(ctx)
                            results.append((spec.key, letter, title, "PASS", detail))
                            print(f"  PASS  {detail}", flush=True)
                        except AssertionError as e:
                            results.append(
                                (spec.key, letter, title, "FAIL", str(e).split("\n")[0])
                            )
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

    print("\n" + "=" * 78)
    for key, letter, title, verdict, detail in results:
        print(f"  {verdict:<5} {key:<6} {letter}. {title}")
        print(f"          {detail}")
    failed = [r for r in results if r[3] == "FAIL"]
    ran = [r for r in results if r[3] != "N/A"]
    print("=" * 78)
    print(
        f"{len(ran) - len(failed)}/{len(ran)} passed "
        f"({len(results) - len(ran)} N/A) across {len(specs)} publisher(s)"
    )
    if failed:
        print(
            "\nThe publish pipeline is NOT verified. Fix the failures above before "
            "running a release workflow -- a bad publish to hub.amd-gaia.ai is permanent."
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
