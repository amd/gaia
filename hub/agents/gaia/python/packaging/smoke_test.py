# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
No-Python smoke test for the frozen GAIA flagship agent binaries.

Two DIFFERENT programs freeze out of this package and each has its own wire, so
each has its own mode. ``--mode`` picks which; the default is ``sidecar``.

``--mode sidecar`` -- the REST surface the daemon supervises:

  1. Launch the binary as a subprocess -- binary only, no interpreter available
     to it.
  2. Poll ``GET /health`` until ready (dependency-free readiness probe).
  3. ``GET /health``       -> 200 ``{"status": "ok", ...}``.
  4. ``GET /version``      -> 200 with BOTH ``apiVersion`` and ``agentVersion``
     non-empty. The daemon reads exactly these two keys to decide whether to
     attach, so an empty one is a contract break, not cosmetic.
  5. ``GET /openapi.json`` -> contains ``/v1/gaia/query`` and ``/v1/gaia/init``.

``--mode stdio`` -- the child the TUI spawns. This mode exists because the two
binaries are indistinguishable from the outside: an HTTP sidecar frozen under
the stdio name boots happily, passes any "did it start?" check, and then feeds
uvicorn's startup log to a JSON line scanner (#3062). So the assertions are all
about the WIRE:

  1. Launch the binary BARE -- no argv at all, exactly as the TUI spawns the
     flagship (``seedAgents`` in ``tui/internal/catalog/catalog.go`` declares no
     ``BinaryArgs``). A binary that needs ``--host``/``--port`` to do anything is
     the sidecar.
  2. The first stdout line must be the startup model-state ping: a canonical
     ``status`` event naming the model the agent resolved
     (``gaia_agent/stdio.py`` ``_model_state_event``, written by ``main`` before
     stdin is read). Reaching it proves the frozen binary constructed the whole
     ``GaiaAgent`` -- every hidden import, every bundled data file.
  3. Write ``{"gaia_query": "/model"}`` and expect exactly one terminal ``final``
     event back. ``/model`` is intercepted before the LLM ever sees it
     (``run_model_command``), so this is a real round trip that needs NO model
     and NO Lemonade -- verified against a dead Lemonade URL, which the ping
     reports as ``lemonade_reachable: false`` and otherwise ignores.
  4. Nothing but JSON objects on stdout -- stdout IS the wire, and anything else
     there renders in the TUI as an unreadable event.
  5. Nothing listening on the sidecar's port, re-checked every second while
     waiting for (2). This is the check that actually catches a mis-frozen
     binary: uvicorn logs to STDERR, so the sidecar under the stdio name is not
     noisy on stdout -- it is SILENT there, and silence alone would only fail at
     the end of the startup window.

It does NOT prove the agent can answer a question: no inference runs, so a model
that loads but produces garbage passes this. That is deliberate -- requiring a
model server would make the release gate depend on a runner that has one.

This harness runs under Python (it is a test driver), but the program under test
is the frozen binary. Uses only the stdlib so it has no install requirements.

Exit code 0 = PASS, non-zero = FAIL. Verbose ``[smoke]`` logging throughout.
"""

from __future__ import annotations

import argparse
import json
import queue
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HOST = "127.0.0.1"
PORT = 8142  # 8141 is the gaia sidecar's runtime port; 8131 is email. NEVER 4001.
BASE = f"http://{HOST}:{PORT}"

REQUIRED_PATHS = {"/v1/gaia/query", "/v1/gaia/init"}

# The sidecar's default bind port. The stdio binary must never listen anywhere,
# and this is the one port a mis-frozen stdio binary would land on.
SIDECAR_DEFAULT_PORT = 8141

# Must match gaia_agent.stdio.QUERY_KEY -- the agent only unwraps an object
# carrying exactly this key, and a bare line would still be read as a query, so
# using the wrapper is what actually exercises the host's half of the contract.
QUERY_KEY = "gaia_query"

# `/model` with no argument lists models and returns; it never reaches
# process_query. See gaia_agent.stdio.is_model_command / run_model_command.
MODEL_COMMAND = "/model"

# Agent construction is eager (embeddings, two FAISS rebuilds, scratchpad DB,
# filesystem index) -- ~21s from source on a warm dev box, and a onefile binary
# must unpack itself before any of that starts on a cold CI runner.
STDIO_STARTUP_DEADLINE_S = 300.0
# The round trip itself is local bookkeeping: ~2s observed from source.
STDIO_TURN_DEADLINE_S = 90.0


# The agent's events carry non-ASCII (arrows, box drawing). The Windows runner's
# console is cp1252, so printing one raises UnicodeEncodeError and the smoke test
# dies mid-run looking like a binary failure. Reconfigure rather than escape the
# text, so every log line is safe and not just the ones we remembered.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


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


class StdioContractBreak(Exception):
    """The frozen binary did not speak the TUI's stdin/stdout contract."""


def _port_is_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(2.0)
        return s.connect_ex((HOST, port)) == 0


def _pump(stream, on_line, label: str) -> None:
    """Read *stream* line by line on a daemon thread, ending with a None sentinel.

    A thread per pipe rather than a timed read: there is no portable way to put
    a deadline on a pipe read (``select`` does not take Windows pipes), and a
    full stderr buffer would deadlock the child.
    """

    def run() -> None:
        try:
            while True:
                line = stream.readline()
                if not line:
                    break
                on_line(line.rstrip("\r\n"))
        except (OSError, ValueError) as exc:
            # Expected when teardown closes the pipe under us; still logged,
            # because a truncated read otherwise looks like a clean end of output.
            log(f"{label} reader stopped: {exc}")
        finally:
            on_line(None)

    threading.Thread(target=run, daemon=True, name=label).start()


class _Deadline:
    """One time budget shared across several reads, so a multi-event wait cannot
    restart its clock on every event it skips."""

    def __init__(self, budget_s: float) -> None:
        self.budget_s = budget_s
        self.end = time.time() + budget_s

    def remaining(self) -> float:
        return self.end - time.time()


def _next_event(
    events: "queue.Queue",
    proc: subprocess.Popen,
    deadline: _Deadline,
    what: str,
    guard=None,
) -> dict:
    """Next JSON object off stdout, or raise ``StdioContractBreak``.

    *guard* is re-checked on every idle second so a binary that fails by going
    silent (an HTTP server, which never answers stdin at all) is caught in
    seconds instead of at the end of the startup window.
    """
    while True:
        remaining = deadline.remaining()
        if remaining <= 0:
            raise StdioContractBreak(
                f"no {what} within {deadline.budget_s:.0f}s. The binary wrote no "
                "usable JSON to stdout -- which is what an HTTP server frozen "
                "under the stdio name does: it binds a port and never reads stdin."
            )
        try:
            line = events.get(timeout=min(remaining, 1.0))
        except queue.Empty:
            if guard is not None:
                guard()
            continue
        if line is None:
            raise StdioContractBreak(
                f"stdout closed before {what} arrived (process exit code: "
                f"{proc.poll()})"
            )
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError as exc:
            raise StdioContractBreak(
                f"stdout carried a line that is not JSON ({exc}): {line[:200]!r}. "
                "stdout IS the wire -- the TUI parses every line as an event, so "
                "anything else there renders as an unreadable-event warning."
            ) from exc
        if not isinstance(event, dict):
            raise StdioContractBreak(
                f"stdout carried JSON that is not an object: {line[:200]!r}"
            )
        log(f"<- {json.dumps(event, ensure_ascii=False)[:300]}")
        return event


def check_startup_ping(event: dict) -> None:
    """The first line must be the model-state ping ``main`` writes at startup.

    Reaching it means the frozen binary built the whole ``GaiaAgent``, so this
    is also the check that catches a hidden import PyInstaller missed -- that
    failure arrives here as a terminal ``error`` event instead.
    """
    kind = event.get("type")
    if kind == "error":
        raise StdioContractBreak(
            "the binary's first event is a terminal error, so agent construction "
            f"failed inside the frozen binary: {event.get('detail') or event}"
        )
    if kind != "status":
        raise StdioContractBreak(
            f"first event is {kind!r}, expected the 'status' model-state ping "
            "(gaia_agent/stdio.py::_model_state_event)"
        )
    model_id = event.get("model_id")
    if not isinstance(model_id, str) or not model_id:
        raise StdioContractBreak(
            "the startup ping carries no model_id, so the TUI header has nothing "
            f"to name: {event}"
        )
    backend = event.get("model_backend")
    if backend not in ("lemonade", "claude"):
        raise StdioContractBreak(
            f"the startup ping's model_backend is {backend!r}, expected "
            "'lemonade' or 'claude'"
        )
    # Reported, not asserted: this test deliberately does not require a model
    # server, and the ping is designed to say so rather than fail.
    log(
        f"startup ping PASS -- model {model_id} on {backend} "
        f"(lemonade_reachable={event.get('lemonade_reachable')})"
    )


def check_model_round_trip(proc: subprocess.Popen, events: "queue.Queue") -> None:
    """Send ``/model`` and require exactly one terminal ``final`` back.

    ``/model`` is the only turn that needs no inference: ``dispatch_query``
    routes it to ``run_model_command`` before ``process_query`` is reached, so a
    PASS here does not imply a working model -- only a working wire.
    """
    line = json.dumps({QUERY_KEY: MODEL_COMMAND})
    log(f"-> {line}")
    try:
        proc.stdin.write(line + "\n")
        proc.stdin.flush()
    except OSError as exc:
        raise StdioContractBreak(
            f"could not write a query to the binary's stdin: {exc}"
        ) from exc

    deadline = _Deadline(STDIO_TURN_DEADLINE_S)
    while True:
        event = _next_event(
            events, proc, deadline, f"a terminal event for {MODEL_COMMAND}"
        )
        kind = event.get("type")
        if kind == "error":
            raise StdioContractBreak(
                f"{MODEL_COMMAND} answered with an error event: "
                f"{event.get('detail') or event}"
            )
        if kind != "final":
            # status/token/tool_* before the terminal event are legal; the turn
            # is only over when exactly one terminal event lands.
            continue
        answer = event.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            raise StdioContractBreak(
                f"{MODEL_COMMAND} returned an empty final event: {event}"
            )
        log(f"round-trip PASS -- final answer, {len(answer)} chars")
        return


def assert_no_http_listener() -> None:
    """The stdio binary must not be listening anywhere.

    The whole point of the separate target: the sidecar frozen under the stdio
    name boots fine and binds this port, and every "did it start?" check passes
    while the TUI reads uvicorn's log as JSON (#3062).
    """
    if _port_is_open(SIDECAR_DEFAULT_PORT):
        raise StdioContractBreak(
            f"something is listening on {HOST}:{SIDECAR_DEFAULT_PORT} -- this "
            "binary started an HTTP server, so it is the REST sidecar, not the "
            "stdio child the TUI spawns."
        )


def run_stdio(binary: Path) -> int:
    # Preflight on the sidecar's port: a server already sitting there would make
    # the no-listener check meaningless, and we cannot tell it from our own.
    if _port_is_open(SIDECAR_DEFAULT_PORT):
        log(
            f"FAIL: port {SIDECAR_DEFAULT_PORT} is already in use -- kill the "
            "stale sidecar first, or the 'binary started no HTTP server' check "
            "cannot mean anything."
        )
        return 2

    # Bare argv: exactly how the TUI spawns the flagship (catalog.go seedAgents
    # declares no BinaryArgs). stderr stays SEPARATE -- merging it would put
    # tracebacks on the wire and mask the very thing this mode checks.
    cmd = [str(binary)]
    log(f"launching frozen binary: {binary}")
    log(f"command: {' '.join(cmd)} (bare, as the TUI spawns it)")
    t0 = time.time()
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        # JSON is UTF-8 by definition. Without this the reader decodes with the
        # locale codec, and on the Windows leg cp1252 dies on the first non-ASCII
        # byte the agent prints -- reported as "stdout closed", not as a decode
        # error, so it reads like a broken binary.
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    events: "queue.Queue" = queue.Queue()
    errors: list = []

    def keep_stderr(line) -> None:
        if line is not None:  # the pump's EOF sentinel
            errors.append(line)

    _pump(proc.stdout, events.put, "stdout")
    _pump(proc.stderr, keep_stderr, "stderr")

    results: dict[str, bool] = {}
    try:
        try:
            log(
                f"waiting up to {STDIO_STARTUP_DEADLINE_S:.0f}s for the startup "
                f"ping (watching {HOST}:{SIDECAR_DEFAULT_PORT} meanwhile)"
            )
            ping = _next_event(
                events,
                proc,
                _Deadline(STDIO_STARTUP_DEADLINE_S),
                "a startup event",
                guard=assert_no_http_listener,
            )
            log(f"time to first event: {time.time() - t0:.1f}s")
            check_startup_ping(ping)
            results["startup_ping"] = True

            assert_no_http_listener()
            log(f"no-listener PASS -- nothing bound on {HOST}:{SIDECAR_DEFAULT_PORT}")
            results["no_http_listener"] = True

            check_model_round_trip(proc, events)
            results["model_round_trip"] = True
        except StdioContractBreak as exc:
            log(f"FAIL: {exc}")
    finally:
        log("closing stdin (how a well-behaved agent is asked to exit)")
        try:
            proc.stdin.close()
        except OSError as exc:
            log(f"stdin was already gone: {exc}")
        try:
            proc.wait(timeout=30)
            log(f"exited on stdin close with code {proc.returncode}")
        except subprocess.TimeoutExpired:
            # Not a failure: the TUI force-kills after its own grace window
            # (SubprocessClient.Close), so a slow exit is tolerated by design.
            log("did not exit within 30s of stdin close -- killing the tree")
        _kill_tree(proc)
        if errors:
            log("stderr tail:\n" + "\n".join(errors[-40:]))

    log(f"results: {results}")
    ok = len(results) == 3 and all(results.values())
    log("VERDICT: PASS" if ok else "VERDICT: FAIL")
    return 0 if ok else 1


def run_sidecar(binary: Path) -> int:
    # Preflight: refuse if the port is already bound -- otherwise we would
    # health-check a stale server and report a false PASS.
    if _port_is_open(PORT):
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
        # Same reason as the stdio mode: the locale codec cannot read the
        # agent's UTF-8 log output on the Windows leg.
        encoding="utf-8",
        errors="replace",
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


MODES = {"sidecar": run_sidecar, "stdio": run_stdio}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-test a frozen GAIA flagship agent binary."
    )
    parser.add_argument("binary", help="Path to the frozen executable to test.")
    parser.add_argument(
        "--mode",
        choices=sorted(MODES),
        default="sidecar",
        help="Which wire to test: 'sidecar' probes /health, /version and "
        "/openapi.json over HTTP; 'stdio' spawns the binary bare and speaks the "
        "TUI's stdin/stdout JSONL contract (default: sidecar).",
    )
    args = parser.parse_args(argv)

    binary = Path(args.binary).resolve()
    if not binary.exists():
        log(f"FAIL: binary not found: {binary}")
        return 2

    log(f"mode: {args.mode}")
    return MODES[args.mode](binary)


if __name__ == "__main__":
    sys.exit(main())
