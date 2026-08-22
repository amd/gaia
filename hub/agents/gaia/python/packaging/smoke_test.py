# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
No-Python smoke test for the frozen GAIA flagship agent sidecar.

Proves the FROZEN BINARY (not ``python -m ...``) boots and serves the sidecar
contract:

  1. Launch the binary as a subprocess -- binary only, no interpreter available
     to it.
  2. Poll ``GET /health`` until ready (dependency-free readiness probe).
  3. ``GET /health``       -> 200 ``{"status": "ok", ...}``.
  4. ``GET /version``      -> 200 with BOTH ``apiVersion`` and ``agentVersion``
     non-empty. The daemon reads exactly these two keys to decide whether to
     attach, so an empty one is a contract break, not cosmetic.
  5. ``GET /openapi.json`` -> contains ``/v1/gaia/query`` and ``/v1/gaia/init``.

This harness runs under Python (it is a test driver), but the SERVER under test
is the frozen binary. Uses only the stdlib so it has no install requirements.

Exit code 0 = PASS, non-zero = FAIL. Verbose ``[smoke]`` logging throughout.
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HOST = "127.0.0.1"
PORT = 8142  # 8141 is the gaia sidecar's runtime port; 8131 is email. NEVER 4001.
BASE = f"http://{HOST}:{PORT}"

REQUIRED_PATHS = {"/v1/gaia/query", "/v1/gaia/init"}


def log(msg: str) -> None:
    print(f"[smoke] {msg}", flush=True)


def _get(path: str, timeout: float = 5.0):
    req = urllib.request.Request(BASE + path, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the server AND its children.

    PyInstaller's one-file bootloader spawns a child process; terminating the
    parent orphans the child and leaves the socket bound. A host app must do the
    same on shutdown.
    """
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
    else:
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _drain(proc: subprocess.Popen, label: str, tail_chars: int = 4000) -> None:
    try:
        if proc.stdout:
            out = proc.stdout.read()
            if out:
                log(f"{label}:\n{out[-tail_chars:]}")
    except OSError as exc:
        log(f"could not drain server output ({label}): {exc}")


def wait_for_health(proc: subprocess.Popen, deadline_s: float = 90.0) -> bool:
    start = time.time()
    while time.time() - start < deadline_s:
        if proc.poll() is not None:
            log(f"server process exited early with code {proc.returncode}")
            return False
        try:
            status, body = _get("/health", timeout=2.0)
            if status == 200 and body.get("status") == "ok":
                log(f"/health ready after {time.time() - start:.1f}s -> {body}")
                return True
            log(f"/health answered but not ready: HTTP {status} {body}")
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(0.5)
    log(f"/health not ready within {deadline_s}s")
    return False


def check_health() -> bool:
    status, body = _get("/health", timeout=5.0)
    log(f"/health -> HTTP {status} {body}")
    if status != 200:
        log(f"FAIL: /health returned HTTP {status}")
        return False
    if body.get("status") != "ok":
        log(f"FAIL: /health status is {body.get('status')!r}, expected 'ok'")
        return False
    log("health check PASS")
    return True


def check_version() -> bool:
    status, body = _get("/version", timeout=5.0)
    log(f"/version -> HTTP {status} {body}")
    if status != 200:
        log(f"FAIL: /version returned HTTP {status}")
        return False
    missing = [k for k in ("apiVersion", "agentVersion") if not body.get(k)]
    if missing:
        log(f"FAIL: /version has empty/absent field(s): {missing}")
        return False
    log(
        f"version check PASS (apiVersion={body['apiVersion']}, "
        f"agentVersion={body['agentVersion']})"
    )
    return True


def check_openapi() -> bool:
    status, spec = _get("/openapi.json", timeout=15.0)
    if status != 200:
        log(f"FAIL: /openapi.json returned HTTP {status}")
        return False
    paths = set(spec.get("paths", {}).keys())
    log(f"openapi paths: {sorted(paths)}")
    missing = REQUIRED_PATHS - paths
    if missing:
        log(f"FAIL: missing gaia paths in openapi: {sorted(missing)}")
        return False
    log("openapi check PASS -- all required gaia paths present")
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-test the frozen GAIA agent sidecar."
    )
    parser.add_argument(
        "binary", help="Path to the frozen gaia-agent executable to test."
    )
    args = parser.parse_args(argv)

    binary = Path(args.binary).resolve()
    if not binary.exists():
        log(f"FAIL: binary not found: {binary}")
        return 2

    # Preflight: refuse if the port is already bound -- otherwise we would
    # health-check a stale server and report a false PASS.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(2.0)
        if s.connect_ex((HOST, PORT)) == 0:
            log(f"FAIL: port {PORT} already in use -- kill the stale server first.")
            return 2

    cmd = [str(binary), "--host", HOST, "--port", str(PORT)]
    log(f"launching frozen binary: {binary}")
    log(f"command: {' '.join(cmd)}")
    t0 = time.time()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    results: dict[str, bool] = {}
    try:
        ready = wait_for_health(proc)
        log(f"startup time to /health: {time.time() - t0:.1f}s")
        if not ready:
            return 3  # the finally block kills the tree and drains the log
        results["health"] = check_health()
        results["version"] = check_version()
        results["openapi"] = check_openapi()
    finally:
        log("shutting down server")
        _kill_tree(proc)
        _drain(proc, "server output tail")

    log(f"results: {results}")
    ok = len(results) == 3 and all(results.values())
    log("VERDICT: PASS" if ok else "VERDICT: FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
