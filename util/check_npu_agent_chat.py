#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""One real agent turn on the NPU, the way a new user's Agent UI sends it.

    python util/check_npu_agent_chat.py --model gemma4-it-e4b-FLM --logs <dir>

Run with ``GAIA_HOME`` set to an empty directory: the run refuses any other.
In order, it

1. on Linux, fails unless the ``amdxdna`` driver has given the NPU a
   ``/dev/accel`` node, naming what the machine needs;
2. runs ``gaia init --profile npu`` from that empty home, which installs
   GAIA's own Lemonade, its NPU backend, the model and the GAIA agent binary;
3. fails unless that Lemonade says its NPU backend is installed;
4. starts the Agent UI backend, makes a chat as the Agent UI does, and sends
   one message. The backend hands the turn to the agent sidecar the daemon
   starts, so the turn crosses the same processes as a user's (#6632);
5. fails unless the answer is non-empty, no step said the model could not
   be reached, a sidecar log was written, and Lemonade shows *--model*
   loaded with the ``flm`` recipe;
6. with ``--also-llm-model``, sends that model one direct ``gaia llm`` prompt;
7. stops what it started and copies every ``*.log`` under ``GAIA_HOME`` and
   the backend's output into ``--logs``.

Used by .github/workflows/test_npu_agent_chat.yml; run it the same way by
hand on an NPU PC to reproduce the job.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

INIT_TIMEOUT_S = 90 * 60
BACKEND_START_TIMEOUT_S = 300
#: A cold FLM load plus the flagship's ~20K-token first prompt on the NPU.
TURN_TIMEOUT_S = 30 * 60
LLM_TIMEOUT_S = 20 * 60
STOP_TIMEOUT_S = 60

#: The #6632 failure, however a layer words it.
UNREACHABLE_MARKERS = (
    "couldn't reach",
    "could not reach",
    "lemonadenetworkerror",
    "cannot reach lemonade",
)

#: Sent as the Agent UI sends them, so the backend treats this as the app.
UI_HEADERS = {"X-Gaia-UI": "1", "X-Gaia-Client": "agent-ui"}

PROMPT = "Reply with one short sentence that says hello."

LOCALHOSTS = "localhost,127.0.0.1"

LINUX_DRIVER_HELP = (
    "This runner has no amdxdna NPU driver - provision it. A Linux NPU runner "
    "needs: the amdxdna kernel driver loaded (in mainline kernels from 6.14, or "
    "the amdxdna DKMS module on older ones), the NPU firmware it loads from "
    "/lib/firmware/amdnpu/, and a /dev/accel/accel* node the runner's user "
    "can open. Lemonade then installs FastFlowLM itself (flm:npu). Distro "
    "steps, including the memlock limit FastFlowLM needs: "
    "https://lemonade-server.ai/flm_npu_linux.html"
)


class CheckFailed(RuntimeError):
    """A step failed; the message says which and where to look."""


# ── Pure checks (unit-tested) ─────────────────────────────────────────────


def linux_npu_problems(root: Path = Path("/")) -> List[str]:
    """Why this Linux machine cannot run an FLM model; empty when it can."""
    problems = []
    accel = sorted((root / "dev" / "accel").glob("accel*"))
    if not (root / "sys" / "module" / "amdxdna").exists():
        problems.append("the amdxdna kernel module is not loaded")
    if not accel:
        problems.append("there is no /dev/accel/accel* device node")
    return problems


def linux_npu_facts(root: Path = Path("/")) -> Dict[str, Any]:
    """What a maintainer needs to know about this machine's NPU stack."""
    facts: Dict[str, Any] = {
        "kernel": platform.release(),
        "accel_nodes": [
            str(p.relative_to(root))
            for p in sorted((root / "dev" / "accel").glob("accel*"))
        ],
        "amdxdna_loaded": (root / "sys" / "module" / "amdxdna").exists(),
    }
    version = root / "sys" / "module" / "amdxdna" / "version"
    if version.is_file():
        facts["amdxdna_version"] = version.read_text(encoding="utf-8").strip()
    if os.name == "posix":
        import resource  # pylint: disable=import-error,import-outside-toplevel

        soft, _ = resource.getrlimit(resource.RLIMIT_MEMLOCK)
        facts["memlock"] = "unlimited" if soft == resource.RLIM_INFINITY else soft
    firmware = root / "lib" / "firmware" / "amdnpu"
    if firmware.is_dir():
        facts["firmware"] = sorted(
            str(p.relative_to(firmware)) for p in firmware.rglob("*") if p.is_file()
        )
    return facts


def flm_backend_problem(system_info: Dict[str, Any]) -> Optional[str]:
    """Why Lemonade cannot run FLM models now; None when its NPU backend is in."""
    from gaia.llm.model_device import recipe_unavailable_reason

    reason = recipe_unavailable_reason("flm", system_info)
    if reason:
        return f"Lemonade says FLM models cannot run: {reason}"
    npu = ((system_info.get("recipes") or {}).get("flm") or {}).get("backends") or {}
    state = (npu.get("npu") or {}).get("state")
    if state != "installed":
        return (
            f"Lemonade's flm:npu backend is {state or 'not reported'}, not "
            "installed, after gaia init --profile npu"
        )
    return None


@dataclass
class TurnResult:
    """What one streamed turn said."""

    answer: str = ""
    errors: List[str] = field(default_factory=list)
    statuses: List[str] = field(default_factory=list)
    done: bool = False


def read_turn(lines: Iterable[str]) -> TurnResult:
    """Fold the backend's SSE lines into the turn's answer and errors.

    The last non-empty ``answer`` or ``done`` content wins over the streamed
    chunks, as in the app.
    """
    result = TurnResult()
    chunks = ""
    final = ""
    for line in lines:
        if not line or not line.startswith("data: "):
            continue
        data = line[len("data: ") :]
        if data == "[DONE]":
            break
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        kind = event.get("type", "")
        if kind == "chunk":
            chunks += event.get("content") or ""
        elif kind in ("answer", "done"):
            if event.get("content"):
                final = event["content"]
            if kind == "done":
                result.done = True
                # A failed turn's content is the reason, not an answer.
                for flag in ("failed", "cancelled"):
                    if event.get(flag):
                        result.errors.append(
                            f"the turn ended {flag}: {event.get('content') or '(no reason given)'}"
                        )
        elif kind in ("agent_error", "error"):
            result.errors.append(
                str(event.get("content") or event.get("message") or event)
            )
        elif kind == "status" and event.get("message"):
            result.statuses.append(event["message"])
    result.answer = (final or chunks).strip()
    return result


def turn_problems(result: TurnResult) -> List[str]:
    """Why the turn did not answer; empty when it did."""
    problems = [f"the turn reported an error: {e}" for e in result.errors]
    said = " ".join([result.answer, *result.errors, *result.statuses]).lower()
    hits = [m for m in UNREACHABLE_MARKERS if m in said]
    if hits:
        problems.append(
            f"the turn said the model could not be reached ({', '.join(hits)}) - "
            "the #6632 failure"
        )
    if not result.answer:
        problems.append("the turn ended with an empty answer")
    if not result.done:
        problems.append("the stream ended without a done event")
    return problems


def npu_load_problems(health: Dict[str, Any], model: str) -> List[str]:
    """Why Lemonade's /health does not show *model* running on the NPU."""
    from gaia.llm.model_device import find_loaded, loaded_entries

    entry = find_loaded(loaded_entries(health), model)
    if entry is None:
        names = [
            e.get("model_name") for e in health.get("all_models_loaded") or []
        ] or ["none"]
        return [f"{model} is not loaded in Lemonade (loaded: {', '.join(names)})"]
    if entry.get("recipe") != "flm":
        return [f"{model} is loaded with recipe {entry.get('recipe')!r}, not 'flm'"]
    return []


def sidecar_logs(gaia_home: Path) -> List[Path]:
    """Agent sidecar logs the daemon wrote under *gaia_home*."""
    return sorted((gaia_home / "agents").glob("*/logs/sidecar-*.log"))


def sidecar_request_failures(gaia_home: Path) -> List[str]:
    """Lemonade error bodies the agent sidecar logged.

    GAIA reports an FLM load failure (Lemonade's 500 ``model_load_error``) as
    "Couldn't reach the local LLM"; the sidecar log keeps the real cause.
    """
    found = []
    for log in sidecar_logs(gaia_home):
        text = log.read_text(encoding="utf-8", errors="replace")
        found += [
            f"{log.name}: {line.strip()}"
            for line in text.splitlines()
            if "Request failed with status" in line
        ]
    return found


# ── The run ───────────────────────────────────────────────────────────────


def _say(msg: str) -> None:
    print(f"[npu-agent-chat] {msg}", flush=True)


def _http(
    url: str,
    *,
    body: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 30,
):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method="GET" if body is None else "POST",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    return urllib.request.urlopen(req, timeout=timeout)


def _get_json(url: str, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    try:
        with _http(url, headers=headers) as reply:
            return json.load(reply)
    except urllib.error.HTTPError as e:
        raise CheckFailed(
            f"GET {url} returned HTTP {e.code}: {e.read()[:500]!r}"
        ) from e


def _gaia(*args: str) -> List[str]:
    return [sys.executable, "-m", "gaia.cli", *args]


def _child_env() -> Dict[str, str]:
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    env["NO_PROXY"] = env["no_proxy"] = LOCALHOSTS
    return env


def _run(cmd: List[str], timeout: float, what: str) -> str:
    """Run *cmd*, echoing its output live; return that output."""
    _say(f"$ {' '.join(cmd[1:])}")
    out: List[str] = []
    with subprocess.Popen(
        cmd,
        env=_child_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    ) as proc:
        timer = threading.Timer(timeout, proc.kill)
        timer.start()
        try:
            for line in proc.stdout:
                print(line, end="", flush=True)
                out.append(line)
            proc.wait()
        finally:
            timed_out = not timer.is_alive()
            timer.cancel()
    if timed_out:
        raise CheckFailed(f"{what} did not finish in {timeout:.0f} s and was killed")
    if proc.returncode != 0:
        raise CheckFailed(
            f"{what} exited {proc.returncode}; its output is above and the "
            "logs are in the run's artifact"
        )
    return "".join(out)


def _lemonade() -> tuple[str, Dict[str, str]]:
    """GAIA's Lemonade URL and auth headers, as the backend resolves them."""
    from gaia.llm.lemonade_client import (
        lemonade_auth_headers,
        resolve_lemonade_api_key,
        resolve_lemonade_base_url,
    )

    base = resolve_lemonade_base_url().rstrip("/")
    return base, lemonade_auth_headers(resolve_lemonade_api_key(base_url=base))


def _wait_for_backend(base: str, proc: subprocess.Popen) -> None:
    deadline = time.monotonic() + BACKEND_START_TIMEOUT_S
    while True:
        if proc.poll() is not None:
            raise CheckFailed(
                f"the Agent UI backend exited {proc.returncode} before answering; "
                "see backend.log in the logs"
            )
        try:
            with _http(f"{base}/api/health", headers=UI_HEADERS, timeout=5):
                return
        except (urllib.error.URLError, OSError) as e:
            if time.monotonic() > deadline:
                raise CheckFailed(
                    f"no Agent UI backend at {base} after "
                    f"{BACKEND_START_TIMEOUT_S} s: {e}"
                ) from e
            time.sleep(2)


def _send_turn(base: str, model: str) -> TurnResult:
    session = None
    try:
        with _http(
            f"{base}/api/sessions",
            body={"title": "NPU agent chat check", "model": model},
            headers=UI_HEADERS,
        ) as reply:
            session = json.load(reply)
    except urllib.error.HTTPError as e:
        raise CheckFailed(
            f"creating a chat failed with HTTP {e.code}: {e.read()[:500]!r}"
        ) from e
    _say(f"chat {session['id']} on {model} (agent {session.get('agent_type')})")
    started = time.monotonic()
    try:
        with _http(
            f"{base}/api/chat/send",
            body={"session_id": session["id"], "message": PROMPT, "stream": True},
            headers=UI_HEADERS,
            timeout=TURN_TIMEOUT_S,
        ) as reply:
            lines = (raw.decode("utf-8", "replace").rstrip("\r\n") for raw in reply)
            result = read_turn(lines)
    except urllib.error.HTTPError as e:
        raise CheckFailed(
            f"sending the message failed with HTTP {e.code}: {e.read()[:500]!r}"
        ) from e
    _say(f"turn took {time.monotonic() - started:.0f} s")
    return result


def _stop(proc: Optional[subprocess.Popen]) -> List[str]:
    """Stop the backend, the daemon and GAIA's Lemonade; return what went wrong."""
    trouble = []
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=STOP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            trouble.append("the Agent UI backend ignored terminate and was killed")
    for args in (("daemon", "stop"), ("lemonade", "embedded", "stop")):
        try:
            done = subprocess.run(
                _gaia(*args),
                env=_child_env(),
                timeout=STOP_TIMEOUT_S,
                capture_output=True,
                text=True,
                check=False,
            )
        except subprocess.TimeoutExpired:
            trouble.append(f"gaia {' '.join(args)} did not finish")
            continue
        if done.returncode != 0:
            trouble.append(
                f"gaia {' '.join(args)} exited {done.returncode}: "
                f"{(done.stdout + done.stderr).strip()[-300:]}"
            )
    return trouble


def _collect_logs(gaia_home: Path, logs: Path) -> None:
    for log in gaia_home.rglob("*.log"):
        dest = logs / "gaia_home" / log.relative_to(gaia_home)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(log, dest)


def _check_gaia_home() -> Path:
    raw = os.environ.get("GAIA_HOME", "").strip()
    if not raw:
        raise CheckFailed(
            "GAIA_HOME is not set. Point it at an empty directory so this run "
            "never touches your own ~/.gaia."
        )
    home = Path(raw)
    if home.exists() and any(home.iterdir()):
        raise CheckFailed(
            f"GAIA_HOME {home} is not empty, so this run would not start cold. "
            "Use a new directory."
        )
    return home


def run(args: argparse.Namespace) -> None:
    # This process talks to localhost too; a runner proxy must not take it.
    os.environ.update(NO_PROXY=LOCALHOSTS, no_proxy=LOCALHOSTS)
    logs = Path(args.logs).resolve()
    logs.mkdir(parents=True, exist_ok=True)
    gaia_home = _check_gaia_home()

    if sys.platform.startswith("linux"):
        facts = linux_npu_facts()
        _say(f"Linux NPU stack: {json.dumps(facts)}")
        problems = linux_npu_problems()
        if problems:
            raise CheckFailed(f"{'; '.join(problems)}. {LINUX_DRIVER_HELP}")

    if importlib.util.find_spec("gaia_agent") is not None:
        raise CheckFailed(
            "gaia_agent is importable in this Python, so the backend would run "
            "the agent in-process instead of through the gaia-agent binary the "
            "apps use. Install GAIA without the flagship wheel."
        )

    backend = None
    backend_log = None
    failure: Optional[BaseException] = None
    try:
        init = ["init", "--profile", "npu", "--yes", "--verbose"]
        init += ["--skip-webui-build"]
        if args.force_models:
            init.append("--force-models")
        _run(_gaia(*init), INIT_TIMEOUT_S, "gaia init --profile npu")

        lemonade, auth = _lemonade()
        _say(f"GAIA's Lemonade: {lemonade}")
        problem = flm_backend_problem(_get_json(f"{lemonade}/system-info", auth))
        if problem:
            raise CheckFailed(problem)

        url = f"http://127.0.0.1:{args.port}"
        backend_log = open(logs / "backend.log", "wb")  # noqa: SIM115
        backend = subprocess.Popen(  # pylint: disable=consider-using-with
            [sys.executable, "-m", "gaia.ui.server", "--host", "127.0.0.1"]
            + ["--port", str(args.port)],
            env=_child_env(),
            stdout=backend_log,
            stderr=subprocess.STDOUT,
        )
        _wait_for_backend(url, backend)

        result = _send_turn(url, args.model)
        _say(f"answer: {result.answer[:500]!r}")
        problems = turn_problems(result)

        server = _get_json(f"{url}/api/system/model-server", UI_HEADERS)
        if server.get("owner") != "gaia":
            problems.append(
                f"the backend used the {server.get('owner')!r} Lemonade at "
                f"{server.get('base_url')}, not GAIA's own"
            )
        health = _get_json(f"{lemonade}/health", auth)
        problems += npu_load_problems(health, args.model)
        if not sidecar_logs(gaia_home):
            problems.append(
                "no agent sidecar log under GAIA_HOME/agents, so the turn did "
                "not go through the gaia-agent binary"
            )
        if problems:
            raise CheckFailed("; ".join(problems))
        _say(f"{args.model} answered through the agent sidecar, on the NPU")

        if args.also_llm_model:
            out = _run(
                _gaia(
                    "llm",
                    "Reply with the single word OK.",
                    "--model",
                    args.also_llm_model,
                    "--no-stream",
                    "--max-tokens",
                    "16",
                ),
                LLM_TIMEOUT_S,
                f"gaia llm --model {args.also_llm_model}",
            )
            # gaia llm exits 0 on a failed prompt; only success prints this.
            if "LLM Response:" not in out:
                raise CheckFailed(
                    f"gaia llm --model {args.also_llm_model} printed no response"
                )
    except BaseException as e:
        failure = e
        raise
    finally:
        trouble = _stop(backend)
        if backend_log is not None:
            backend_log.close()
        _collect_logs(gaia_home, logs)
        if failure is not None:
            for line in sidecar_request_failures(gaia_home):
                _say(f"agent sidecar: {line}")
        for t in trouble:
            _say(f"teardown: {t}")
        if trouble and failure is None:
            raise CheckFailed(f"teardown failed: {'; '.join(trouble)}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", required=True, help="FLM chat model to test")
    parser.add_argument(
        "--also-llm-model", default="", help="also send this model one gaia llm"
    )
    parser.add_argument("--port", type=int, default=4317, help="backend port")
    parser.add_argument("--logs", required=True, help="directory for the logs")
    parser.add_argument(
        "--force-models",
        action="store_true",
        help="pass --force-models to gaia init (re-downloads cached models)",
    )
    args = parser.parse_args(argv)
    try:
        run(args)
    except CheckFailed as e:
        print(f"::error::{e}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
