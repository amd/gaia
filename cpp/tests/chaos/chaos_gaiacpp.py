#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Chaos test for the gaiacpp binary: a model server that misbehaves on purpose.

Each run starts the real ``gaiacpp -p`` against a local server that follows a
seeded script of replies: valid tool calls and answers mixed with HTTP errors,
dropped connections, truncated and non-JSON bodies, error objects inside a 200,
malformed and oversized tool calls, unknown tools, repeated calls and slow
replies. Whatever the script, the binary must:

  * exit within the time limit (no hang) with exit code 0 or 1 (no crash),
  * end stdout with one JSON record whose ``status`` says how the run ended,
  * report ``success`` only when it has an answer, and say what failed otherwise,
  * stay within a memory bound, and leave the files it was told not to touch alone.

Usage::

    python cpp/tests/chaos/chaos_gaiacpp.py --binary cpp/build/gaiacpp --runs 200
    python cpp/tests/chaos/chaos_gaiacpp.py --binary ... --seed 1234   # replay one run

Exit code 0 when every run held the invariants, 1 otherwise (each violation is
printed with the seed that reproduces it).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MODEL = "chaos-model"
MAX_RSS_MB = 300
CRASH_MARKERS = (
    "terminate called",
    "Assertion",
    "Segmentation fault",
    "core dumped",
    "AddressSanitizer",
    "std::bad_alloc",
)

# name -> weight. "tool" and "answer" are well-formed replies; the rest are faults.
FAULTS = {
    "tool_ok": 30,
    "tool_two": 6,
    "answer": 10,
    "http_500": 5,
    "http_503": 3,
    "http_429": 3,
    "http_400": 3,
    "http_401": 2,
    "drop": 4,
    "truncated": 4,
    "not_json": 3,
    "empty_body": 3,
    "error_in_200": 3,
    "no_choices": 3,
    "empty_answer": 3,
    "bad_args": 4,
    "args_not_object": 3,
    "unknown_tool": 4,
    "missing_id": 3,
    "duplicate_ids": 2,
    "huge_answer": 2,
    "huge_args": 2,
    "tool_huge_output": 3,
    "tool_fails": 4,
    "tool_escapes": 3,
    "reasoning": 3,
    "slow": 2,
    "invalid_utf8": 2,
    "text_tool_call": 3,
}


def _message(content=None, tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    usage = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
    return {
        "id": "chaos",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
        "usage": usage,
    }


def _call(call_id, name, args):
    arguments = args if isinstance(args, str) else json.dumps(args)
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


class Script:
    """The replies one run gets, in order; past the end, a plain answer."""

    def __init__(self, seed: int):
        rng = random.Random(seed)
        names, weights = zip(*FAULTS.items())
        self.steps = rng.choices(names, weights=weights, k=rng.randint(1, 9))
        self.canary = Path(tempfile.gettempdir()) / f"chaos-canary-{seed}"
        self.served: list[str] = []
        self.lock = threading.Lock()

    def next(self) -> str:
        with self.lock:
            step = (
                self.steps[len(self.served)]
                if len(self.served) < len(self.steps)
                else "answer"
            )
            self.served.append(step)
            return step


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    script: Script = None  # set per run

    def log_message(self, *args):
        pass

    def _send(self, status: int, body: bytes, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj):
        self._send(status, json.dumps(obj).encode())

    def do_GET(self):
        if self.path.endswith("/health"):
            loaded = [
                {
                    "model_name": MODEL,
                    "type": "llm",
                    "recipe": "llamacpp",
                    "recipe_options": {"ctx_size": 32768},
                }
            ]
            self._json(
                200,
                {"status": "ok", "model_loaded": MODEL, "all_models_loaded": loaded},
            )
        elif "/models" in self.path:
            self._json(
                200, {"data": [{"id": MODEL, "labels": ["chat", "tool-calling"]}]}
            )
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        if self.path.endswith("/load"):
            return self._json(200, {"status": "ok"})
        if not self.path.endswith("/chat/completions"):
            return self._json(404, {"error": "not found"})
        try:
            request = json.loads(raw)
        except ValueError:
            return self._json(400, {"error": {"message": "request body is not JSON"}})
        if request.get("stream"):
            # The binary's headless mode does not stream; refuse loudly if that changes.
            return self._json(
                400, {"error": {"message": "chaos server does not stream"}}
            )
        step = self.script.next()
        getattr(self, "_" + step)()

    # ---- well-formed replies ----
    def _answer(self):
        self._json(200, _message("The task is done: 3 files checked."))

    def _tool_ok(self):
        self._json(
            200,
            _message(None, [_call("c1", "bash_execute", {"command": "echo chaos"})]),
        )

    def _tool_two(self):
        calls = [
            _call("c1", "file_read", {"path": "notes.txt"}),
            _call("c2", "bash_execute", {"command": "echo two"}),
        ]
        self._json(200, _message("Reading and echoing.", calls))

    def _reasoning(self):
        self._json(
            200, _message("<think>secret plan</think>Visible answer after thinking.")
        )

    def _slow(self):
        time.sleep(2.0)
        self._answer()

    # ---- transport and HTTP faults ----
    def _http_500(self):
        self._json(500, {"error": {"message": "internal error"}})

    def _http_503(self):
        self._send(503, b"<html>Service Unavailable</html>", "text/html")

    def _http_429(self):
        self._json(429, {"error": {"message": "rate limited"}})

    def _http_400(self):
        self._json(400, {"error": {"message": "bad request: unsupported parameter"}})

    def _http_401(self):
        self._json(401, {"error": {"message": "invalid api key"}})

    def _drop(self):
        self.connection.shutdown(socket.SHUT_RDWR)
        self.connection.close()

    def _truncated(self):
        body = json.dumps(_message("cut off")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body[: len(body) // 2])
        self.wfile.flush()
        self.connection.shutdown(socket.SHUT_RDWR)
        self.connection.close()

    def _not_json(self):
        self._send(200, b"<html><body>gateway page</body></html>", "text/html")

    def _empty_body(self):
        self._send(200, b"")

    def _invalid_utf8(self):
        body = b'{"choices":[{"message":{"role":"assistant","content":"bad \xff\xfe bytes"}}]}'
        self._send(200, body)

    # ---- well-formed HTTP, wrong content ----
    def _error_in_200(self):
        self._json(
            200, {"error": {"message": "model crashed", "type": "backend_error"}}
        )

    def _no_choices(self):
        self._json(200, {"id": "chaos", "choices": []})

    def _empty_answer(self):
        self._json(200, _message(""))

    def _bad_args(self):
        self._json(
            200,
            _message(
                None, [_call("c1", "bash_execute", '{"command": "echo unterminated')]
            ),
        )

    def _args_not_object(self):
        self._json(200, _message(None, [_call("c1", "file_read", "[1, 2, 3]")]))

    def _unknown_tool(self):
        self._json(
            200, _message(None, [_call("c1", "launch_missiles", {"target": "moon"})])
        )

    def _missing_id(self):
        call = _call("", "bash_execute", {"command": "echo no-id"})
        del call["id"]
        self._json(200, _message(None, [call]))

    def _duplicate_ids(self):
        calls = [
            _call("same", "bash_execute", {"command": "echo a"}),
            _call("same", "bash_execute", {"command": "echo b"}),
        ]
        self._json(200, _message(None, calls))

    def _huge_answer(self):
        self._json(200, _message("word " * 300_000))

    def _huge_args(self):
        self._json(
            200,
            _message(
                None,
                [
                    _call(
                        "c1",
                        "file_write",
                        {"path": "big.txt", "content": "x" * 2_000_000},
                    )
                ],
            ),
        )

    def _tool_huge_output(self):
        command = (
            "python -c \"print('y' * 3000000)\""
            if os.name == "nt"
            else "head -c 3000000 /dev/zero | tr '\\0' y"
        )
        self._json(
            200, _message(None, [_call("c1", "bash_execute", {"command": command})])
        )

    def _tool_fails(self):
        self._json(
            200,
            _message(None, [_call("c1", "file_read", {"path": "does-not-exist.txt"})]),
        )

    def _tool_escapes(self):
        outside = str(self.script.canary / "written-by-agent.txt")
        self._json(
            200,
            _message(
                None,
                [_call("c1", "file_write", {"path": outside, "content": "escaped"})],
            ),
        )

    def _text_tool_call(self):
        self._json(
            200,
            _message(
                'Let me look. {"tool": "bash_execute", "tool_args": {"command": "echo text"}}'
            ),
        )


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # a dropped connection is the fault being injected, not a server bug


def run_once(
    binary: str, seed: int, timeout: int, keep: bool
) -> tuple[list[str], dict]:
    """Run one scripted session; return the invariants it broke and what happened."""
    script = Script(seed)
    handler = type("SeededHandler", (Handler,), {"script": script})
    server = QuietServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    work = Path(tempfile.mkdtemp(prefix=f"gaiacpp-chaos-{seed}-"))
    home = Path(tempfile.mkdtemp(prefix=f"gaiacpp-chaos-home-{seed}-"))
    canary = script.canary
    shutil.rmtree(canary, ignore_errors=True)
    (work / "notes.txt").write_text("three things to check\n", encoding="utf-8")
    (work / "settings.json").write_text(json.dumps({"llmRetries": 1}), encoding="utf-8")
    env = dict(os.environ, GAIA_HOME=str(home))
    for var in (
        "LEMONADE_BASE_URL",
        "GAIA_BASE_URL",
        "GAIA_MODEL_ID",
        "LEMONADE_API_KEY",
        "GAIA_TURN_LOG",
    ):
        env.pop(var, None)
    cmd = [binary, "-p", "Check the three things in notes.txt and report.", "-m", MODEL]
    cmd += [
        "--base-url",
        f"http://127.0.0.1:{server.server_address[1]}/api/v1",
        "--config",
        str(work / "settings.json"),
    ]
    cmd += [
        "--output-format",
        "json",
        "--max-steps",
        "12",
        "--dangerously-skip-permissions",
        "--no-memory",
        "--no-skills",
    ]
    broken: list[str] = []
    seen = {"status": "hang", "planned": len(script.steps)}
    start = time.time()
    try:
        proc = subprocess.run(
            cmd, cwd=work, env=env, capture_output=True, timeout=timeout
        )
        elapsed = time.time() - start
        stdout = proc.stdout.decode("utf-8", errors="replace")
        stderr = proc.stderr.decode("utf-8", errors="replace")
        if proc.returncode not in (0, 1):
            broken.append(
                f"exit code {proc.returncode} (expected 0 or 1): crashed or was killed"
            )
        for marker in CRASH_MARKERS:
            if marker in stderr:
                broken.append(f"stderr shows '{marker}'")
        lines = [ln for ln in stdout.splitlines() if ln.strip()]
        record = None
        if not lines:
            broken.append("no output on stdout")
        else:
            try:
                record = json.loads(lines[-1])
            except ValueError:
                broken.append(f"last stdout line is not JSON: {lines[-1][:120]!r}")
        if isinstance(record, dict):
            seen["status"] = record.get("status")
            status = record.get("status")
            result = record.get("result")
            if status not in ("success", "error", "max_steps", "cancelled"):
                broken.append(f"unexpected status {status!r}")
            if not isinstance(result, str) or not result.strip():
                broken.append(
                    f"status {status!r} with an empty result: nothing tells the user what happened"
                )
            if (status == "success") != (proc.returncode == 0):
                broken.append(f"status {status!r} but exit code {proc.returncode}")
            rss = record.get("peak_rss_mb")
            if isinstance(rss, (int, float)) and rss > MAX_RSS_MB:
                broken.append(f"peak memory {rss} MB, over the {MAX_RSS_MB} MB bound")
            if isinstance(result, str) and "secret plan" in result:
                broken.append("the model's hidden reasoning reached the answer")
        if canary.exists():
            broken.append("a file was written outside the working folder")
        if broken:
            broken.append(
                f"script: {script.served} (planned {script.steps}); {elapsed:.1f}s; stderr tail: {stderr[-400:]!r}"
            )
    except subprocess.TimeoutExpired:
        broken.append(
            f"no exit within {timeout}s (hang); script so far: {script.served} (planned {script.steps})"
        )
    finally:
        server.shutdown()
        server.server_close()
        if not (keep and broken):
            shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(canary, ignore_errors=True)
    seen["served"] = list(script.served)
    return broken, seen


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--binary", required=True, help="path to gaiacpp")
    ap.add_argument("--runs", type=int, default=100)
    ap.add_argument("--first-seed", type=int, default=1)
    ap.add_argument("--seed", type=int, help="replay one seed")
    ap.add_argument("--timeout", type=int, default=120, help="seconds one run may take")
    ap.add_argument("--jobs", type=int, default=4, help="runs in parallel")
    ap.add_argument(
        "--keep", action="store_true", help="keep the working folder of a failed run"
    )
    args = ap.parse_args(argv)
    binary = str(Path(args.binary).resolve())
    if not Path(binary).is_file():
        print(
            f"error: no gaiacpp binary at {binary}; build it first (cmake --build <dir> --target gaiacpp)",
            file=sys.stderr,
        )
        return 2

    seeds = (
        [args.seed]
        if args.seed is not None
        else list(range(args.first_seed, args.first_seed + args.runs))
    )
    failures: dict[int, list[str]] = {}
    outcomes: list[dict] = []
    lock = threading.Lock()
    pending = list(seeds)

    def worker():
        while True:
            with lock:
                if not pending:
                    return
                seed = pending.pop(0)
            broken, seen = run_once(binary, seed, args.timeout, args.keep)
            with lock:
                outcomes.append(seen)
                if broken:
                    failures[seed] = broken

    jobs = 1 if args.seed is not None else max(1, args.jobs)
    start = time.time()
    threads = [threading.Thread(target=worker) for _ in range(jobs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for seed in sorted(failures):
        print(f"FAIL seed {seed}  (replay: --seed {seed})")
        for line in failures[seed]:
            print(f"    {line}")
    statuses: dict[str, int] = {}
    faults: dict[str, int] = {}
    for seen in outcomes:
        statuses[str(seen["status"])] = statuses.get(str(seen["status"]), 0) + 1
        for step in seen["served"]:
            faults[step] = faults.get(step, 0) + 1
    requests = sum(faults.values())
    print(f"how runs ended: {dict(sorted(statuses.items()))}")
    print(
        f"{requests} model requests served; replies by kind: {dict(sorted(faults.items()))}"
    )
    never = sorted(set(FAULTS) - set(faults))
    if never and args.seed is None and len(seeds) >= 100:
        print(
            f"FAIL these replies were never reached, so they were not tested: {never}"
        )
        return 1
    print(
        f"{len(seeds) - len(failures)} of {len(seeds)} runs held every invariant in {time.time() - start:.0f}s"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
