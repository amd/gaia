# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Both GAIA agents on a real model, on a real Strix Halo, as an installed GAIA.

tests/integration/test_harness_surface_contract.py proves the seam on hosted
runners against a scripted model. This is the same seam with nothing scripted:
the installed ``gaia`` in an empty HOME starts GAIA's own Lemonade, and the
Python agent and gaiacpp each answer through the Agent UI backend from a model
that really runs. No address or key for the model server is in any environment.

Checks, each on what the machine shows afterwards rather than on wording:

- first-run setup through the backend with gaiacpp chosen reaches Ready
- each agent answers, reads a planted fact, writes a file once approved, leaves
  a denied shell command undone, and asks before using a folder outside the chat
- one chat is answered by the Python agent, gaiacpp, then the Python agent
- gaiacpp as the terminal UI's child finds GAIA's server and reads the fact

A model does not call the same tool every time, so a check that depends on one
gets ``GAIA_HW_ATTEMPTS`` tries (default 2); the evidence says which passed.

Opt-in like the rest of tests/hardware: ``GAIA_HW_RUN=1``, or
``GAIA_HW_REQUIRE=1`` in CI, which fails instead of skipping. The gaiacpp to
test is named by ``GAIA_TEST_GAIACPP``.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, List, Optional

import psutil
import pytest
import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

from halo_harness import (  # noqa: E402
    GEMMA,
    Scenario,
    amdgpu_present,
    evidence,
)

from tests.integration.harness_contract.surfaces import (  # noqa: E402
    Turn,
    ask_ui,
    is_folder_request,
)

pytestmark = [pytest.mark.integration, pytest.mark.real_model]

_MISSING = None
if not sys.platform.startswith("linux"):
    _MISSING = "Linux only"
elif not amdgpu_present():
    _MISSING = "no amdgpu card in /sys/class/drm"
elif "1" not in (os.environ.get("GAIA_HW_RUN"), os.environ.get("GAIA_HW_REQUIRE")):
    _MISSING = "set GAIA_HW_RUN=1 to run the Strix Halo hardware checks"
elif not os.environ.get("GAIA_TEST_GAIACPP"):
    _MISSING = "set GAIA_TEST_GAIACPP to the gaiacpp to test"
if _MISSING:
    if os.environ.get("GAIA_HW_REQUIRE") == "1":
        raise RuntimeError(f"GAIA_HW_REQUIRE=1 but this machine cannot run: {_MISSING}")
    pytestmark.append(pytest.mark.skip(reason=_MISSING))

HARNESSES = ("gaiapy", "gaiacpp")
ATTEMPTS = int(os.environ.get("GAIA_HW_ATTEMPTS") or 2)
#: One turn on a real model, a cold load included.
TURN_TIMEOUT_S = 240


class Installed:
    """The installed GAIA of one scenario, with its Agent UI backend running."""

    def __init__(self, scenario: Scenario):
        self.scenario = scenario
        self.gaiacpp = Path(os.environ["GAIA_TEST_GAIACPP"]).resolve()
        if not self.gaiacpp.is_file():
            raise RuntimeError(f"GAIA_TEST_GAIACPP names {self.gaiacpp}: not a file")
        # GAIA looks here first for gaiacpp; nothing else about it is injected.
        self.env = {**scenario.env, "GAIA_GAIACPP_PATH": str(self.gaiacpp)}
        self.work = scenario.root / "work"
        self.outside = scenario.root / "outside"
        self.work.mkdir()
        self.outside.mkdir()
        self.backend: Optional[subprocess.Popen] = None
        self.port = 0

    def start_backend(self) -> None:
        import socket

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        log = open(self.scenario.root / "backend.log", "ab")  # noqa: SIM115
        self.backend = subprocess.Popen(
            [sys.executable, "-m", "gaia.ui.server", "--host", "127.0.0.1"]
            + ["--port", str(self.port)],
            env=self.env,
            cwd=str(self.scenario.home),
            stdout=log,
            stderr=log,
        )
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if self.backend.poll() is not None:
                raise AssertionError(f"the backend exited {self.backend.returncode}")
            try:
                if requests.get(f"{self.url}/api/health", timeout=3).ok:
                    return
            except requests.RequestException:
                pass
            time.sleep(0.5)
        raise AssertionError("the Agent UI backend did not answer within 180 s")

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def api(self, method: str, path: str, **kwargs) -> requests.Response:
        token = (self.scenario.gaia_home / "agent-ui-token").read_text("utf-8").strip()
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Gaia-UI": "1",
            "X-Gaia-Client": "agent-ui",
        }
        kwargs.setdefault("timeout", TURN_TIMEOUT_S)
        return requests.request(method, f"{self.url}{path}", headers=headers, **kwargs)

    def choose(self, harness: str) -> None:
        reply = self.api("PUT", "/api/settings", json={"flagship_agent": harness})
        assert reply.status_code == 200, f"choosing {harness}: {reply.text}"

    def session(self) -> str:
        reply = self.api("POST", "/api/sessions", json={"folders": [str(self.work)]})
        assert reply.status_code == 200, f"POST /api/sessions: {reply.text}"
        return reply.json()["id"]

    def agent_program(self) -> Optional[str]:
        ledger = self.scenario.gaia_home / "host" / "sidecars.json"
        entries = json.loads(ledger.read_text("utf-8")) if ledger.is_file() else []
        for entry in entries:
            if entry.get("agent_id") == "gaia" and psutil.pid_exists(entry["pid"]):
                program = Path((entry.get("argv") or [""])[0]).name.lower()
                return "gaiacpp" if program.startswith("gaiacpp") else "gaiapy"
        return None

    def stop(self) -> None:
        if self.backend is not None and self.backend.poll() is None:
            self.backend.terminate()
            try:
                self.backend.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.backend.kill()


@pytest.fixture(scope="module")
def installed():
    """GAIA installed for a new user, its own Lemonade running, Gemma on disk."""
    scenario = Scenario("harness-surfaces")
    shared = {Path("/tmp").resolve(), Path("/var/tmp").resolve()}
    if any(root in scenario.root.resolve().parents for root in shared):
        raise RuntimeError(
            f"{scenario.root} is inside a shared temp folder, which the agents never "
            "open: set GAIA_HW_WORKDIR to a folder outside /tmp."
        )
    app = Installed(scenario)
    try:
        # Chosen before init, so init fetches no larger model (EU-8).
        scenario.gaia("config", "set", "default_model", GEMMA)
        scenario.gaia(
            "init", "--profile", "minimal", "--gaia-lemonade", "--yes", timeout=3600
        )
        app.start_backend()
        yield app
    finally:
        app.stop()
        killed = scenario.cleanup()
        evidence(f"- harness-surfaces cleanup: force-killed {killed or 'nothing'}")


def _section(title: str) -> None:
    evidence()
    evidence(f"### {title}")
    evidence()


def _try(what: str, attempt: Callable[[int], Optional[str]]) -> None:
    """Run *attempt* up to ATTEMPTS times; it returns why it failed, or None."""
    problems: List[str] = []
    for n in range(1, ATTEMPTS + 1):
        problem = attempt(n)
        if problem is None:
            evidence(f"- PASS: {what} (attempt {n} of {ATTEMPTS})")
            return
        problems.append(f"attempt {n}: {problem}")
    evidence(f"- FAIL: {what}: {' | '.join(problems)}")
    pytest.fail(f"{what}: {' | '.join(problems)}")


def _said(turn: Turn) -> str:
    return (turn.answer or turn.error or "(nothing)")[:300].replace("\n", " ")


def test_setup_through_the_backend_with_gaiacpp_chosen_reaches_ready(installed):
    """Setup, run by the app's own endpoint, tests a real chat on gaiacpp.

    First, because it is also what installs the Python agent the later checks
    switch to, as it does on a new PC.
    """
    _section("Setup through the Agent UI backend with gaiacpp chosen")
    installed.choose("gaiacpp")
    started = installed.api("POST", "/api/setup/run", json={})
    assert started.status_code == 200, started.text
    status = started.json()
    deadline = time.monotonic() + 1500
    while status["state"] in ("running", "verifying") and time.monotonic() < deadline:
        time.sleep(2)
        status = installed.api("GET", "/api/setup/status").json()
    tests = {row["job"]: row for row in status.get("tests", [])}
    for job, row in tests.items():
        evidence(f"- setup test `{job}`: {row['state']} {row.get('error') or ''}")
    assert status["state"] == "ready", f"{status.get('error')}\n{tests}"
    assert tests["agent"]["state"] == "passed", tests["agent"]
    assert installed.agent_program() == "gaiacpp"
    evidence("- PASS: setup reached Ready and its test chat ran on gaiacpp")


@pytest.mark.parametrize("harness", HARNESSES)
def test_agent_answers_reads_writes_and_respects_a_no(installed, harness):
    """Five things a person does in a chat, each read off the disk or the answer."""
    _section(f"{harness} through the Agent UI backend, on {GEMMA}")
    installed.choose(harness)
    session = installed.session()
    allow = lambda prompt: True  # noqa: E731
    deny = lambda prompt: False  # noqa: E731

    def answers(_n: int) -> Optional[str]:
        turn = ask_ui(
            installed, "Reply with the single word OK.", allow, session, TURN_TIMEOUT_S
        )
        if installed.agent_program() != harness:
            return f"answered by {installed.agent_program()}, not {harness}"
        return None if turn.answer.strip() else f"no answer: {_said(turn)}"

    _try("answers a prompt", answers)

    def reads(n: int) -> Optional[str]:
        word = secrets.token_hex(4)
        note = installed.work / f"note-{harness}-{n}.txt"
        note.write_text(f"The code word is {word}.\n", encoding="utf-8")
        turn = ask_ui(
            installed,
            f"Read the file {note} and tell me the code word in it.",
            allow,
            session,
            TURN_TIMEOUT_S,
        )
        return None if word in turn.answer else f"no code word in: {_said(turn)}"

    _try("reads a planted fact from a file", reads)

    def writes(n: int) -> Optional[str]:
        word = secrets.token_hex(4)
        target = installed.work / f"made-{harness}-{n}.txt"
        turn = ask_ui(
            installed,
            f"Create the file {target} containing exactly this text: {word}",
            allow,
            session,
            TURN_TIMEOUT_S,
        )
        if not turn.prompts:
            return f"wrote without asking, or not at all: {_said(turn)}"
        if not target.is_file() or word not in target.read_text("utf-8"):
            return f"{target.name} does not hold the text: {_said(turn)}"
        return None

    _try("writes a file once the write is approved", writes)

    def refuses(n: int) -> Optional[str]:
        target = installed.work / f"denied-{harness}-{n}.txt"
        turn = ask_ui(
            installed,
            f"Run this shell command for me: touch {target}",
            deny,
            session,
            TURN_TIMEOUT_S,
        )
        if target.exists():
            return f"{target.name} exists although every request was denied"
        return None if turn.prompts else f"never asked to run it: {_said(turn)}"

    _try("leaves a denied shell command undone", refuses)

    def asks_for_the_folder(n: int) -> Optional[str]:
        word = secrets.token_hex(4)
        target = installed.outside / f"out-{harness}-{n}.txt"
        turn = ask_ui(
            installed,
            f"Write the text {word} to the file {target}",
            allow,
            session,
            TURN_TIMEOUT_S,
        )
        if not any(is_folder_request(p, target) for p in turn.prompts):
            return f"no request to use {target}; asked: {turn.prompts}"
        if not target.is_file():
            return f"{target.name} missing after the request was allowed"
        return None

    _try(
        "asks before using a folder outside the chat, then writes", asks_for_the_folder
    )


def test_switching_harness_mid_chat(installed):
    """One chat keeps answering as the agent program changes under it."""
    _section("Switching the agent program inside one chat")
    session = None
    for harness in ("gaiapy", "gaiacpp", "gaiapy"):
        installed.choose(harness)
        session = session or installed.session()
        turn = ask_ui(
            installed,
            "Reply with the single word OK.",
            lambda p: True,
            session,
            TURN_TIMEOUT_S,
        )
        assert turn.answer.strip(), f"{harness} gave no answer: {_said(turn)}"
        assert installed.agent_program() == harness
        evidence(f"- PASS: {harness} answered in the same chat")


def test_gaiacpp_as_the_terminal_uis_child_finds_gaias_server(installed):
    """`gaiacpp --stdio` with no address in its environment answers from GAIA's server."""
    _section("gaiacpp over the terminal UI's stdio wire")
    word = secrets.token_hex(4)
    note = installed.work / "note-stdio.txt"
    note.write_text(f"The code word is {word}.\n", encoding="utf-8")
    prompt = f"Read the file {note} and tell me the code word in it."

    def reads(_n: int) -> Optional[str]:
        done = subprocess.run(
            [str(installed.gaiacpp), "--stdio"],
            input=prompt + "\n",
            env=installed.env,
            cwd=str(installed.work),
            capture_output=True,
            text=True,
            timeout=TURN_TIMEOUT_S,
            check=False,
        )
        events = [
            json.loads(line)
            for line in done.stdout.splitlines()
            if line.startswith("{")
        ]
        final = next((e for e in events if e.get("type") in ("final", "error")), {})
        if final.get("type") != "final":
            return f"ended in {final or 'nothing'}: {done.stderr[-300:]}"
        answer = final.get("answer") or ""
        return None if word in answer else f"no code word in: {answer[:300]}"

    _try("reads the planted fact", reads)
