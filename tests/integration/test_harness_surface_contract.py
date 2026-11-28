# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Both GAIA agents, on every surface a person reaches them through, set up as an
installed GAIA is.

The real daemon starts the real agent: gaiacpp as built, or the Python agent
from this checkout. The model server is a fake on a port the OS picked, holding
a key, and it is recorded only where GAIA's own embedded Lemonade records
itself (``~/.gaia/lemonade/state.json``). No ``LEMONADE_BASE_URL``, address or
key is in any environment, so an agent that answers found the server itself.

Each of these would have caught a bug that shipped while every lane was green:

- #8826  terminal gaiacpp asked Lemonade's standard port, not GAIA's server
- #8854  the gaiacpp the desktop app starts did the same
- #8858  gaiacpp refused a path outside the folder without asking
- #8495  ``gaia -p`` flags were dropped when the agent ran through the service

Run by .github/workflows/build_agents.yml on the gaiacpp it just built, and by
test_distributed_seams.yml for changes on the Python side of the same seam.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest

from tests.integration.harness_contract.surfaces import (
    SURFACES,
    WEB_TOOL,
    WRITE_TOOL,
    StdioAgent,
    Turn,
    ask_cli,
    ask_ui,
    is_folder_request,
    ui_session,
    write_call,
)
from tests.integration.harness_contract.world import (
    ABSENT_MODEL,
    LAST_MODEL,
    REPO,
    SAVED_MODEL,
    STANDARD_PORT,
    Canary,
    World,
    free_port,
    gaiacpp_binary,
)

pytestmark = pytest.mark.integration

HARNESSES = tuple(
    h for h in os.environ.get("GAIA_TEST_HARNESSES", "gaiapy,gaiacpp").split(",") if h
)
#: Another checkout whose daemon, backend and Python agent to run: how the suite
#: is shown to fail on a commit without a fix.
SRC = Path(os.environ.get("GAIA_TEST_GAIA_SRC") or REPO)

ANSWER = "Hello from the fake model server"

#: The model each surface sends when ``default_model`` and ``last_model`` differ.
#: `gaia -p` reopens on the last pick and a bare agent starts on the saved
#: default: see `default_model` and `last_model` in docs/reference/cli.mdx and
#: "Using what GAIA set up" in docs/cpp/gaia-agent.mdx. The Agent UI picks
#: between the two by which is loaded, so for it either is right.
MODEL_SENT = {"ui": None, "cli": LAST_MODEL, "stdio": SAVED_MODEL}


@dataclass(frozen=True)
class Difference:
    """One place the two agents are documented to behave differently."""

    scenario: str
    surface: str
    harness: str
    behaviour: str
    doc: str
    #: Words in *doc* that state it; the suite fails when they are gone.
    quote: str


#: Every difference the tests below allow. One the docs don't state is a bug:
#: add it here only with the sentence that documents it.
EXPECTED_DIFFERENCES = (
    Difference(
        "restart",
        "stdio",
        "gaiacpp",
        "finds GAIA's server once, at start; after it moves, turns end in error",
        "docs/cpp/gaia-agent.mdx",
        "`gaiacpp` finds GAIA's own model server once, at start",
    ),
    Difference(
        "folder_request",
        "ui",
        "gaiacpp",
        "asks twice: the folder card, then the write (the Python agent asks once)",
        "docs/cpp/gaia-agent.mdx",
        "raises the flagship's `allow_path_access` prompt",
    ),
    Difference(
        "folder_request",
        "stdio",
        "gaiacpp",
        "asks twice: the folder prompt, then the write (the Python agent asks once)",
        "docs/cpp/gaia-agent.mdx",
        "asks first, with the same `allow_path_access` prompt the Python agent raises",
    ),
)


def difference(scenario: str, surface: str, harness: str) -> Optional[Difference]:
    for known in EXPECTED_DIFFERENCES:
        if (known.scenario, known.surface, known.harness) == (
            scenario,
            surface,
            harness,
        ):
            return known
    return None


def test_every_expected_difference_is_documented():
    undocumented = [
        f"{d.scenario}/{d.surface}/{d.harness}: {d.doc} no longer says {d.quote!r}"
        for d in EXPECTED_DIFFERENCES
        if d.quote not in " ".join((REPO / d.doc).read_text(encoding="utf-8").split())
    ]
    assert not undocumented, "\n".join(undocumented)


# ── the world each harness runs in ──


def _world_base(tmp_path_factory) -> Path:
    """Where the temp homes go: never inside a shared temp folder.

    The Python agent keeps every shared temp folder out of reach whatever the
    person answers, so a folder request could not be allowed there.
    """
    named = os.environ.get("GAIA_TEST_WORLD_ROOT") or os.environ.get("RUNNER_TEMP")
    base = (
        Path(named) / f"harness-contract-{uuid.uuid4().hex[:8]}"
        if named
        else tmp_path_factory.getbasetemp()
    ).resolve()
    shared = {Path("/tmp").resolve(), Path("/var/tmp").resolve()}
    if any(root == base or root in base.parents for root in shared):
        pytest.fail(
            f"the suite's temp homes would be in {base}, inside a shared temp folder "
            "the agents never open. Set GAIA_TEST_WORLD_ROOT to a folder outside "
            "/tmp, or pass --basetemp."
        )
    base.mkdir(parents=True, exist_ok=True)
    return base


@pytest.fixture(scope="session")
def canary():
    """Hold Lemonade's standard port, so an agent that looks there is counted."""
    listener = Canary()
    if not listener.listening:
        listener.close()
        pytest.fail(
            f"port {STANDARD_PORT} is in use, so the suite cannot tell whether an "
            "agent asked Lemonade's standard port. Stop the Lemonade Server "
            "listening there, then run again."
        )
    yield listener
    listener.close()


@pytest.fixture(scope="module", params=HARNESSES)
def world(request, tmp_path_factory, canary):
    harness = request.param
    if harness == "gaiacpp" and gaiacpp_binary() is None:
        pytest.fail(
            "no gaiacpp to test: build it (cmake -S cpp -B cpp/build && cmake --build "
            "cpp/build --target gaiacpp) or set GAIA_TEST_GAIACPP to one. "
            "GAIA_TEST_HARNESSES=gaiapy runs the Python agent alone."
        )
    root = _world_base(tmp_path_factory) / harness
    made = World(root, src=SRC, source_agent=harness == "gaiapy")
    made.harness = harness
    made.canary = canary
    try:
        made.configure(
            default_model=SAVED_MODEL, last_model=LAST_MODEL, flagship_agent=harness
        )
        made.mark_agent_installed()
        made.start_model_server()
        yield made
    finally:
        made.stop()


@pytest.fixture(autouse=True)
def _logs_of_a_failure(request):
    failed = request.session.testsfailed
    yield
    if request.session.testsfailed > failed and "world" in request.fixturenames:
        print(request.getfixturevalue("world").describe())


@pytest.fixture(scope="module")
def backend(world):
    world.start_backend()
    return world


@pytest.fixture(scope="module")
def _children(world):
    return {}


@pytest.fixture
def child(world, _children):
    """The terminal UI's agent, kept across tests as gaia-cli keeps it.

    Where an agent is documented to find GAIA's server once, at start, the
    terminal UI restarts it after the server moves; so does this.
    """
    stale = _children.get("port") != world.fake_port and difference(
        "restart", "stdio", world.harness
    )
    if "agent" not in _children or stale:
        _children.update(agent=StdioAgent(world, world.harness), port=world.fake_port)
    return _children["agent"]


@pytest.fixture
def ask(request, world):
    """Send one message on *surface*; the agent's questions go to *decide*."""

    def send(surface: str, text: str, decide=None) -> Turn:
        if surface == "ui":
            return ask_ui(request.getfixturevalue("backend"), text, decide)
        if surface == "stdio":
            return request.getfixturevalue("child").ask(text, decide)
        assert decide is None, "gaia -p has nobody to ask"
        return ask_cli(world, text)

    return send


def _require_gaiacpp(why: str) -> None:
    """Skip when the run left gaiacpp out on purpose; fail when it is just missing."""
    if "gaiacpp" not in HARNESSES:
        pytest.skip(f"GAIA_TEST_HARNESSES leaves gaiacpp out, and {why}")
    if gaiacpp_binary() is None:
        pytest.fail(
            f"{why}, and there is none: build it (cmake --build cpp/build --target "
            "gaiacpp) or set GAIA_TEST_GAIACPP."
        )


def _marker(name: str) -> str:
    """Text for one turn that no other turn says; see the fake's --script."""
    return f"{name}-{uuid.uuid4().hex[:8]}"


def _settle(world: World) -> int:
    """Forget what reached the model server and its standard port so far."""
    world.canary.connections()
    return len(world.requests())


def _assert_only_gaias_server(world: World, since: int, model: Optional[str]) -> None:
    chats = world.chats(since)
    assert chats, f"no chat request reached GAIA's model server:\n{world.describe()}"
    refused = [r for r in world.requests()[since:] if r.get("auth") != "ok"]
    assert not refused, f"requests without GAIA's key for its own server: {refused}"
    # The turn's own request is the last; a warm-up before it may be on the
    # other saved model, never on one GAIA wasn't set up with.
    if model is not None:
        assert chats[-1]["model"] == model, [r["model"] for r in chats]
    assert {r["model"] for r in chats} <= {SAVED_MODEL, LAST_MODEL}
    knocks = world.canary.connections()
    assert knocks == 0, (
        f"{knocks} connection(s) to Lemonade's standard port {STANDARD_PORT}, which "
        "is not GAIA's server"
    )


# ── the scenarios ──


@pytest.mark.parametrize("surface", SURFACES)
def test_first_message_reaches_gaias_own_server_on_the_saved_model(world, ask, surface):
    """#8826, #8854: the answer comes from GAIA's server, asked with its key."""
    since = _settle(world)
    turn = ask(surface, "Say hello.")
    assert ANSWER in turn.answer, turn.said
    _assert_only_gaias_server(world, since, MODEL_SENT[surface])


@pytest.mark.parametrize("surface", SURFACES)
def test_no_download_without_consent(request, world, surface):
    """A model that isn't on disk is named, never downloaded behind the person's back."""
    world.configure(default_model=ABSENT_MODEL, last_model=None)
    since = _settle(world)
    try:
        if surface == "ui":
            turn = ask_ui(request.getfixturevalue("backend"), "Say hello.")
        elif surface == "stdio":
            # The child settles its model as it starts.
            turn = StdioAgent(world, world.harness).ask("Say hello.")
        else:
            turn = ask_cli(world, "Say hello.")
    finally:
        world.configure(default_model=SAVED_MODEL, last_model=LAST_MODEL)
    assert not world.pulls(since), f"a download started unasked: {world.pulls(since)}"
    if surface == "ui" and ANSWER in turn.answer:
        # The app answered on a model that is on disk rather than fetch one.
        assert {r["model"] for r in world.chats(since)} <= {SAVED_MODEL, LAST_MODEL}
        return
    assert ANSWER not in turn.answer
    assert ABSENT_MODEL in turn.said and "download" in turn.said.lower(), turn.said


@pytest.mark.parametrize("surface", ["ui", "stdio"])
@pytest.mark.parametrize("answer", ["allow", "deny"])
def test_write_outside_folder_asks_for_the_folder(world, ask, surface, answer):
    """#8858: a path outside the chat's folders is asked about, and the answer holds."""
    target = world.outside / f"{surface}-{answer}.txt"
    said = _marker("write-outside")
    world.play(
        [
            {
                "when": said,
                "tool_calls": [write_call(world.harness, target, "from the agent")],
                "then": "The tool said:",
                "echo_results": True,
            }
        ]
    )
    turn = ask(surface, f"{said} please", lambda prompt: answer == "allow")
    folder = [p for p in turn.prompts if is_folder_request(p, target)]
    assert folder, f"no request to use {target}; the agent asked: {turn.prompts}"
    asks_twice = difference("folder_request", surface, world.harness)
    assert len(turn.prompts) == (
        2 if asks_twice and answer == "allow" else 1
    ), turn.prompts
    if answer == "allow":
        assert target.read_text(encoding="utf-8") == "from the agent"
    else:
        assert not target.exists()
        assert re.search(
            r"denied|did not allow|declined|refused", turn.answer, re.I
        ), turn.said


def test_print_flags_take_effect_through_the_daemon(world):
    """#8495: `gaia -p` flags reach the agent the service runs, on either agent."""
    target = world.work / "flagged.txt"
    said = _marker("write-inside")
    world.play(
        [
            {
                "when": said,
                "tool_calls": [write_call(world.harness, target, "written")],
                "then": "Done.",
            },
            {"when": "slow-answer", "then": "Too late.", "delay": 8},
        ]
    )
    _settle(world)

    allowed = ask_cli(
        world, f"{said} please", "--dangerously-skip-permissions", "--no-web"
    )
    assert allowed.exit_code == 0, allowed.said
    assert target.read_text(encoding="utf-8") == "written"
    web_tool = WEB_TOOL[world.harness]
    assert web_tool in allowed.record.get("withheld_tools", []), allowed.record

    target.unlink()
    write_tool = WRITE_TOOL[world.harness][0]
    withheld = ask_cli(
        world,
        f"{said} again",
        "--dangerously-skip-permissions",
        *("--disallowed-tools", write_tool),
    )
    assert not target.exists(), f"{write_tool} ran although --disallowed-tools named it"
    assert write_tool in withheld.record.get("withheld_tools", []), withheld.record

    limited = ask_cli(world, "slow-answer please", "--time-limit", "2")
    assert limited.exit_code != 0 and "Too late." not in limited.answer, limited.record
    assert limited.record.get("time_limit_reached") is True, limited.record


@pytest.mark.parametrize("surface", SURFACES)
def test_model_server_restart_on_new_port_is_followed(world, ask, surface):
    """#8854: GAIA's server comes back on another port and the next message works."""
    assert ANSWER in ask(surface, "Say hello.").answer
    old = world.fake_port
    world.stop_model_server()
    port = free_port()
    assert port != old
    world.start_model_server(port)
    since = _settle(world)

    turn = ask(surface, "Say hello again.")
    if difference("restart", surface, world.harness):
        assert f":{old}" in turn.error, turn.said
        assert not world.chats(since)
        return
    assert ANSWER in turn.answer, turn.said
    _assert_only_gaias_server(world, since, MODEL_SENT[surface])


@pytest.mark.parametrize("world", ["gaiapy"], indirect=True)
def test_switching_harness_between_messages(world, backend):
    """One chat, answered by the Python agent, then gaiacpp, then the Python agent."""
    _require_gaiacpp("switching agents needs a gaiacpp")
    session = ui_session(world)

    def choose(program: str):
        return world.api("PUT", "/api/settings", json={"flagship_agent": program})

    try:
        for program in ("gaiapy", "gaiacpp", "gaiapy"):
            switched = choose(program)
            assert switched.status_code == 200, switched.text
            since = _settle(world)
            turn = ask_ui(world, f"Say hello, {program}.", session=session)
            assert ANSWER in turn.answer, turn.said
            assert world.agent_program() == program
            _assert_only_gaias_server(world, since, MODEL_SENT["ui"])

        # Mid-answer: the turn is waiting on the person, and a switch would end it.
        target = world.work / "mid-answer.txt"
        said = _marker("write-inside")
        world.play(
            [
                {
                    "when": said,
                    "tool_calls": [write_call("gaiapy", target, "x")],
                    "then": "Ok.",
                }
            ]
        )
        refusals = []

        def deny_after_trying_to_switch(prompt) -> bool:
            refusals.append(choose("gaiacpp"))
            return False

        ask_ui(world, f"{said} please", deny_after_trying_to_switch, session=session)
        assert [r.status_code for r in refusals] == [409], [r.text for r in refusals]
        assert world.config()["flagship_agent"] == "gaiapy"
        assert world.agent_program() == "gaiapy"
    finally:
        world.configure(flagship_agent="gaiapy")


@pytest.fixture
def fresh_pc(tmp_path_factory, canary):
    """A PC where GAIA was just installed: no model downloaded, no agent chosen.

    The Python agent's install record is written ahead: fetching its build from
    the hub is the setup journey's job (src/gaia/apps/webui/e2e).
    """
    _require_gaiacpp("setup with gaiacpp chosen needs a gaiacpp")
    root = _world_base(tmp_path_factory) / f"fresh-{uuid.uuid4().hex[:8]}"
    made = World(root, src=SRC, source_agent=False)
    made.harness = "gaiacpp"
    made.canary = canary
    try:
        made.mark_agent_installed()
        made.mark_model_server_installed()
        made.start_model_server(models=[], FAKE_LEMONADE_PULL_SECONDS="0.2")
        yield made
    finally:
        made.stop()


def _run_setup_with_gaiacpp(world: World) -> dict:
    """What Customize > gaiacpp > Continue does, then wait for setup to end."""
    world.start_backend()
    chosen = world.api("PUT", "/api/settings", json={"flagship_agent": "gaiacpp"})
    assert chosen.status_code == 200, chosen.text
    started = world.api("POST", "/api/setup/run", json={})
    assert started.status_code == 200, started.text
    deadline = time.monotonic() + 120
    status = started.json()
    while status["state"] in ("running", "verifying") and time.monotonic() < deadline:
        time.sleep(0.25)
        status = world.api("GET", "/api/setup/status").json()
    return status


def test_first_run_setup_with_gaiacpp_chosen_reaches_ready(fresh_pc):
    """#8854: setup's own test chat runs on gaiacpp, and so does the first real chat."""
    world = fresh_pc
    since = _settle(world)
    status = _run_setup_with_gaiacpp(world)
    tests = {row["job"]: row for row in status.get("tests", [])}
    assert status["state"] == "ready", f"{status.get('error')}\n{tests}"
    assert tests["agent"]["state"] == "passed", tests["agent"]
    assert world.agent_program() == "gaiacpp"

    turn = ask_ui(world, "Say hello.")
    assert ANSWER in turn.answer, turn.said
    refused = [r for r in world.requests()[since:] if r.get("auth") != "ok"]
    assert not refused, f"requests without GAIA's key for its own server: {refused}"
    assert world.canary.connections() == 0
    # The chat runs on a model setup downloaded, not on one it would have to fetch.
    assert world.chats(since)[-1]["model"] in {r["model"] for r in world.pulls(since)}


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="stands a shell script in for gaiacpp, which Windows cannot start as one",
)
def test_setup_names_the_agent_when_gaiacpp_cannot_reach_the_server(fresh_pc):
    """A gaiacpp that can't find GAIA's server fails setup's agent test, not a later chat."""
    world = fresh_pc
    home = world.root / "blind" / "home"
    home.mkdir(parents=True)
    wrapper = home.parent / "gaiacpp"
    # The real program, in a home with no record of GAIA's server.
    wrapper.write_text(
        f'#!/bin/sh\nHOME="{home}" GAIA_HOME="{home / ".gaia"}" '
        f'exec "{gaiacpp_binary()}" "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    world.gaiacpp = wrapper
    _settle(world)

    status = _run_setup_with_gaiacpp(world)
    tests = {row["job"]: row for row in status.get("tests", [])}
    assert status["state"] != "ready", status
    assert (
        tests["agent"]["state"] == "failed" and tests["agent"]["kind"] == "agent"
    ), tests
    assert tests["agent"]["error"], "the failed agent row says nothing"
    passed = {job for job, row in tests.items() if row["state"] == "passed"}
    assert {"server", "chat"} <= passed, f"only the agent should have failed: {tests}"
    assert (
        world.canary.connections() > 0
    ), "gaiacpp was expected to look on the standard port"


# Keep this one last: on gaiacpp it leaves a turn waiting on an approval.
@pytest.mark.parametrize("surface", ["cli"])
def test_gated_tool_with_nobody_to_ask_is_refused(request, world, surface):
    """`gaia -p` without a permission flag refuses a write and says which flag allows it."""
    if world.harness == "gaiacpp":
        request.applymarker(
            pytest.mark.xfail(
                strict=True,
                raises=subprocess.TimeoutExpired,
                reason=(
                    "gaiacpp behind the daemon waits on an approval nobody can give: "
                    "`gaia -p` with no permission flag hangs on a gated tool instead "
                    "of refusing it, as docs/reference/cli.mdx says it does"
                ),
            )
        )
    target = world.work / "unapproved.txt"
    said = _marker("write-inside")
    world.play(
        [
            {
                "when": said,
                "tool_calls": [write_call(world.harness, target, "x")],
                "then": "Done.",
            }
        ]
    )
    # Long enough to tell a refusal from a hang; a refusal takes a second or two.
    turn = ask_cli(
        world, f"{said} please", timeout=12 if world.harness == "gaiacpp" else 60
    )
    assert turn.exit_code == 1 and not target.exists()
    assert "--dangerously-skip-permissions" in turn.error, turn.said
