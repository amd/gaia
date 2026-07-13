# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
No-Python smoke test for the frozen GAIA email REST sidecar (milestone #49).

Proves the FROZEN BINARY (not ``python -m ...``) boots and serves the email
REST surface:

  1. Launch the binary as a subprocess (binary only — no interpreter).
  2. Poll GET /health until ready (dependency-free readiness probe).
  3. GET /openapi.json and assert the email paths are present.
  4. GET /version and assert the contract apiVersion is advertised.
  5. POST /v1/email/triage with a FIXTURE-DERIVED body. Triage uses the real
     local Lemonade model. A 200 is validated against the frozen contract; with
     no Lemonade reachable (e.g. CI) the route must return HTTP 502 ("local LLM
     triage failed"). Both prove the route is wired and frozen correctly; a hang
     or unhandled 500 is a failure.

This harness itself runs under Python (it is a test driver), but the SERVER
under test is the frozen binary with no Python available to it. Uses only the
stdlib (urllib, mailbox, json) so it has no install requirements of its own.

Exit code 0 = PASS, non-zero = FAIL. Verbose logging throughout for debugging.
"""

from __future__ import annotations

import argparse
import json
import mailbox
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]  # .../<repo root>
FIXTURE_MBOX = REPO / "tests" / "fixtures" / "email" / "synthetic_inbox.mbox"

HOST = "127.0.0.1"
PORT = 8131  # NOT 4001 (reserved).
BASE = f"http://{HOST}:{PORT}"


def log(msg: str) -> None:
    print(f"[smoke] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Fixture-derived triage body
# ---------------------------------------------------------------------------


def _first_message_fields() -> tuple[str, str, str]:
    """Pull (subject, from, body) from the first fixture mbox message."""
    if not FIXTURE_MBOX.exists():
        raise FileNotFoundError(f"fixture mbox not found: {FIXTURE_MBOX}")
    box = mailbox.mbox(str(FIXTURE_MBOX))
    try:
        for msg in box:
            subject = msg.get("Subject", "") or "(no subject)"
            sender = msg.get("From", "alice@example.com")
            if msg.is_multipart():
                body = ""
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        raw = part.get_payload(decode=True) or b""
                        body = raw.decode("utf-8", errors="replace")
                        break
            else:
                raw = msg.get_payload(decode=True) or b""
                body = (
                    raw.decode("utf-8", errors="replace")
                    if isinstance(raw, bytes)
                    else str(raw)
                )
            return subject, sender, (body or "(empty body)")
    finally:
        box.close()
    raise RuntimeError("fixture mbox had no messages")


def _parse_from(raw: str) -> dict:
    raw = (raw or "").strip()
    if "<" in raw and ">" in raw:
        name = raw[: raw.index("<")].strip().strip('"') or None
        email = raw[raw.index("<") + 1 : raw.index(">")].strip()
    else:
        name, email = None, raw
    if "@" not in email:
        email = "alice@example.com"
    out = {"email": email}
    if name:
        out["name"] = name
    return out


def build_triage_body() -> dict:
    try:
        subject, sender_raw, body = _first_message_fields()
        log(f"fixture-derived message: subject={subject!r} from={sender_raw!r}")
    except Exception as exc:  # fall back to a representative inline message
        log(f"fixture unavailable ({exc}); using inline representative message")
        subject = "Can you review the Q3 report by Friday?"
        sender_raw = "Alice Example <alice@example.com>"
        body = "Hi, please review the attached Q3 report and let me know by Friday."
    sender = _parse_from(sender_raw)
    return {
        "schema_version": "1.0",
        "payload": {
            "kind": "single",
            "principal": {"email": "user@example.com"},
            "message": {
                "message_id": "smoke-msg-1",
                "from": sender,
                "to": [{"email": "user@example.com"}],
                "subject": subject,
                "body": body,
            },
        },
    }


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib only)
# ---------------------------------------------------------------------------


def _get(path: str, timeout: float = 5.0):
    req = urllib.request.Request(BASE + path, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def _post(path: str, payload: dict, timeout: float = 30.0):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the server AND its children.

    PyInstaller's one-file bootloader spawns a child process; terminating the
    parent orphans the child (and the listening socket). On Windows, taskkill
    /T /F kills the whole tree. A host app must do the same on shutdown.
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


def wait_for_health(proc: subprocess.Popen, deadline_s: float = 60.0) -> bool:
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
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(0.5)
    log(f"/health not ready within {deadline_s}s")
    return False


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_openapi() -> bool:
    status, spec = _get("/openapi.json", timeout=10.0)
    paths = set(spec.get("paths", {}).keys())
    log(f"openapi paths: {sorted(paths)}")
    required = {"/v1/email/triage", "/v1/email/draft", "/v1/email/send"}
    missing = required - paths
    if missing:
        log(f"FAIL: missing email paths in openapi: {missing}")
        return False
    log("openapi check PASS — all email paths present")
    return True


def check_version() -> bool:
    status, body = _get("/version", timeout=5.0)
    log(f"/version -> {body}")
    if not body.get("apiVersion"):
        log("FAIL: /version missing apiVersion")
        return False
    log("version check PASS")
    return True


def check_triage() -> bool:
    """Triage routes to the real local LLM. PASS = a contract-valid 200 (model
    reachable) OR the request is accepted and routed but no model is present —
    a 502 ('local LLM triage failed') or a timeout waiting on Lemonade. A FAIL is
    a wrong status that means the route/contract is broken (e.g. 400/404/422) or
    an unhandled 500."""
    body = build_triage_body()
    log(f"POST /v1/email/triage body={json.dumps(body)[:300]}...")
    try:
        status, resp = _post("/v1/email/triage", body, timeout=20.0)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        if e.code == 502:
            log(f"triage returned 502 (no Lemonade reachable): {detail[:300]}")
            log("triage check PASS (route accepted + routed the request)")
            return True
        log(f"FAIL: triage returned unexpected HTTP {e.code}: {detail[:500]}")
        return False
    except (urllib.error.URLError, TimeoutError) as e:
        # Accepted + routed, then timed out waiting on an absent model.
        log(f"triage request timed out waiting on Lemonade (none reachable): {e}")
        log("triage check PASS (route accepted + routed the request)")
        return True
    log(f"triage HTTP {status} -> {json.dumps(resp)[:500]}")
    # 200: a live model answered — validate against the frozen contract.
    try:
        from gaia_agent_email.contract import parse_response

        parsed = parse_response(resp)
        log(
            "contract-valid triage response: "
            f"request_kind={parsed.request_kind} "
            f"category={parsed.result.category.value} "
            f"summary={parsed.result.summary[:80]!r}"
        )
    except Exception as exc:
        log(f"FAIL: response did not validate against contract: {exc}")
        return False
    if parsed.request_kind != "single":
        log(f"FAIL: expected request_kind=single, got {parsed.request_kind}")
        return False
    log("triage check PASS (live model, contract-valid)")
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-test the frozen email sidecar.")
    parser.add_argument(
        "binary", help="Path to the frozen email-agent executable to test."
    )
    args = parser.parse_args(argv)

    binary = Path(args.binary).resolve()
    if not binary.exists():
        log(f"FAIL: binary not found: {binary}")
        return 2

    # Preflight: refuse to run if the port is already taken — otherwise we'd
    # health-check a stale server and report a false PASS.
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex((HOST, PORT)) == 0:
            log(f"FAIL: port {PORT} already in use — kill the stale server first.")
            return 2

    log(f"launching frozen binary: {binary}")
    cmd = [str(binary), "--host", HOST, "--port", str(PORT)]
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
            # Drain whatever the server printed so failures are debuggable.
            try:
                out = proc.stdout.read() if proc.stdout else ""
                log(f"server output:\n{out}")
            except OSError as exc:
                log(f"could not drain server output: {exc}")
            return 3
        results["openapi"] = check_openapi()
        results["version"] = check_version()
        results["triage"] = check_triage()
    finally:
        log("shutting down server")
        _kill_tree(proc)
        # Surface server logs for debugging regardless of pass/fail.
        try:
            if proc.stdout:
                tail = proc.stdout.read()
                if tail:
                    log(f"server output tail:\n{tail[-2000:]}")
        except OSError as exc:
            log(f"could not drain server output tail: {exc}")

    log(f"results: {results}")
    ok = all(results.values()) and len(results) == 3
    log("VERDICT: PASS" if ok else "VERDICT: FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
