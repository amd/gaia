# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Both harnesses run under the same conditions and are scored the same way.

Each test here pins a real bug found while comparing them: a hard-coded 900 s
cap on one side, a missing toolchain on one side, a timeout reported as zero
turns, and one harness's transcripts read in the other's shape.
"""

import json
import os
import sys
import time
from pathlib import Path

import pytest

from gaia.eval import flagship_tasks as ft
from gaia.eval.bench import config as bench_config
from gaia.eval.bench import harness, metering

from .conftest import cc_tool, gaia_tool, one_task_file

BOTH = [harness.GAIA, harness.CLAUDE_CODE]
PYTEST_RUN = "....                                  [100%]\n4 passed in 0.02s\n"


def _fix(workdir: Path) -> str:
    dates = workdir / "toybox" / "dates.py"
    dates.write_text(
        dates.read_text().replace('if v.endswith("Z"):', 'if v[-1:] in ("Z", "z"):')
    )
    tests = workdir / "tests" / "test_dates.py"
    tests.write_text(
        tests.read_text()
        + "\n\ndef test_lowercase_z():\n    assert parse_updated('2026-01-02 03:04:05z')\n"
    )
    return "Fixed, and the suite passes."


@pytest.mark.parametrize("harness_name", BOTH)
def test_every_harness_gets_the_configured_time_limit(
    fake_agent_run, fake_launch, harness_name
):
    config = bench_config.resolve(harness=harness_name, run_timeout=1234)
    fake_agent_run(harness_name=harness_name, config=config)
    assert [c["timeout_s"] for c in fake_launch.calls] == [1234]


def test_both_harnesses_get_the_same_default_limit(fake_agent_run, fake_launch):
    for name in BOTH:
        fake_agent_run(harness_name=name, model="fireworks.glm-5p3-flash")
    limits = {c["timeout_s"] for c in fake_launch.calls}
    assert limits == {bench_config.DEFAULT_RUN_TIMEOUT_S}


@pytest.mark.parametrize("harness_name", BOTH)
def test_every_harness_finds_the_toolchain_and_the_gh_stand_in_first(
    fake_agent_run, fake_launch, harness_name
):
    fake_agent_run(harness_name=harness_name, model="fireworks.glm-5p3-flash")
    call = fake_launch.calls[0]
    path = call["env"]["PATH"].split(os.pathsep)
    assert path[0].endswith(os.path.join("harness", "gh", "bin"))
    assert path[1] == str(Path(sys.executable).parent)
    assert call["gh_config_empty"], "the agent's gh config held a login"
    assert call["cwd"].name == "toybox"


def test_the_gaia_leg_reaches_the_model_through_the_gateway(
    fake_agent_run, fake_launch
):
    fake_agent_run(harness_name=harness.GAIA)
    env = fake_launch.calls[0]["env"]
    assert env["LEMONADE_BASE_URL"].startswith("http://127.0.0.1:")
    assert env["LEMONADE_BASE_URL"].endswith("/api/v1")


def test_claude_code_on_an_open_model_goes_through_the_gateway(
    fake_agent_run, fake_launch
):
    fake_agent_run(harness_name=harness.CLAUDE_CODE, model="fireworks.glm-5p3-flash")
    call = fake_launch.calls[0]
    assert call["env"]["ANTHROPIC_BASE_URL"].startswith("http://127.0.0.1:")
    assert call["env"]["ANTHROPIC_MODEL"] == "fireworks.glm-5p3-flash"
    assert call["env"]["ANTHROPIC_AUTH_TOKEN"] == harness.GATEWAY_PLACEHOLDER
    cmd = call["cmd"]
    for flag in (
        "--dangerously-skip-permissions",
        "--no-session-persistence",
        "stream-json",
    ):
        assert flag in cmd


def test_claude_code_on_an_anthropic_model_uses_its_own_login(
    fake_agent_run, fake_launch, monkeypatch
):
    # A run started from inside a Claude Code session must not hand the agent
    # that session's routing or settings.
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:7/host-session")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "sdk")
    card, _ = fake_agent_run(harness_name=harness.CLAUDE_CODE, model="claude-sonnet-5")
    env = fake_launch.calls[0]["env"]
    assert not [k for k in env if k.startswith(("ANTHROPIC", "CLAUDE"))]
    task = card["tasks"][0]
    assert (task["cost_usd"], task["cost_source"]) == (0.42, metering.API_EQUIVALENT)


@pytest.mark.parametrize("harness_name", BOTH)
def test_a_timeout_keeps_the_partial_record(fake_agent_run, fake_launch, harness_name):
    fake_launch.timed_out = True
    card, out = fake_agent_run(
        harness_name=harness_name, model="fireworks.glm-5p3-flash"
    )
    task = card["tasks"][0]
    assert task["timed_out"] and task["error"].startswith("timed out after")
    assert task["tool_calls"] == 1, "the calls made before the cap were lost"
    assert task["steps"] >= 1, "a cut-off run reported zero turns"
    transcript = json.loads((out / "02-bugfix" / "transcript.json").read_text())
    assert transcript["prompt"] and (
        transcript.get("conversation") or transcript.get("events")
    )


@pytest.mark.parametrize("harness_name", BOTH)
def test_both_harnesses_are_scored_by_the_same_probes(
    fake_launch, bench_env, harness_name
):
    tasks_file = one_task_file(bench_env, "02-bugfix")

    def run(tag):
        return ft.run_suite(
            "one",
            "fireworks.glm-5p3-flash",
            bench_env / tag,
            tasks_file=tasks_file,
            config=bench_config.resolve(harness=harness_name),
        )["tasks"][0]

    untouched = run("untouched")
    fake_launch.act = _fix
    fixed = run("fixed")
    assert untouched["passed"] is False and untouched["why"].startswith("probe")
    assert fixed["passed"] is True, fixed["why"]


def test_both_harnesses_give_the_judge_the_same_evidence(fake_launch, bench_env):
    """The judge sees each harness's real test runs; one shape once read as none."""
    fake_launch.act = _fix
    fake_launch.conversation = gaia_tool(
        "run_shell_command", {"command": "python -m pytest"}, {"stdout": PYTEST_RUN}
    )
    fake_launch.events = cc_tool(1, "Bash", {"command": "python -m pytest"}, PYTEST_RUN)
    sent = {}
    for name in BOTH:
        out = bench_env / f"out-{name}"
        ft.run_suite(
            "one",
            "fireworks.glm-5p3-flash",
            out,
            tasks_file=one_task_file(bench_env, "02-bugfix"),
            config=bench_config.resolve(harness=name),
        )
        transcript = json.loads((out / "02-bugfix" / "transcript.json").read_text())
        attempt = ft.attempt_from("a", transcript, "diff")
        sent[name] = attempt
    for attempt in sent.values():
        assert "4 passed in 0.02s" in attempt.checks
        assert "pytest" in attempt.record
    assert sent[harness.GAIA].checks == sent[harness.CLAUDE_CODE].checks.replace(
        "Bash", "run_shell_command"
    )


def test_claude_code_verification_is_read_from_its_own_record(fake_launch, bench_env):
    fake_launch.act = _fix
    fake_launch.events = cc_tool(
        1, "Edit", {"file_path": "toybox/dates.py"}, "ok"
    ) + cc_tool(2, "Bash", {"command": "pytest"}, PYTEST_RUN)
    card = ft.run_suite(
        "one",
        "fireworks.glm-5p3-flash",
        bench_env / "out",
        tasks_file=one_task_file(bench_env, "02-bugfix"),
        config=bench_config.resolve(harness=harness.CLAUDE_CODE),
    )
    assert card["tasks"][0]["verified"] is True


def test_a_run_that_counted_no_tokens_costs_an_unknown_amount_not_nothing(
    fake_launch, bench_env
):
    """Lemonade reports zero usage on a streamed Anthropic reply (2026-09).

    Pricing that would publish a real run as free; it is unknown until metered.
    """
    fake_launch.events = []  # no usage block, as Lemonade's stream gives none
    card = ft.run_suite(
        "one",
        "fireworks.glm-5p3-flash",  # a model that does have a rate card
        bench_env / "out",
        tasks_file=one_task_file(bench_env, "02-bugfix"),
        config=bench_config.resolve(harness=harness.CLAUDE_CODE),
    )
    task = card["tasks"][0]
    assert task["steps"] >= 1, "the run did reach the model"
    assert (task["cost_usd"], task["cost_source"]) == (None, "")
    assert card["cost"] == {"source": "", "usd": None, "tokens": 0}


def test_the_scorecard_records_the_conditions(fake_agent_run):
    card, _ = fake_agent_run(harness_name=harness.CLAUDE_CODE, model="claude-sonnet-5")
    assert card["harness"] == harness.CLAUDE_CODE
    assert card["run_timeout_s"] == bench_config.DEFAULT_RUN_TIMEOUT_S
    assert card["full_access"] is False and card["fenced"] is False
    assert card["cost"]["source"] == metering.API_EQUIVALENT


def test_launch_kills_the_process_tree_at_the_cap_and_keeps_its_output(tmp_path):
    script = (
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print('started', flush=True)\n"
        "time.sleep(60)\n"
    )
    out, err = tmp_path / "out.log", tmp_path / "err.log"
    started = time.time()
    code, timed_out = harness.launch(
        [sys.executable, "-c", script],
        env=dict(os.environ),
        cwd=tmp_path,
        timeout_s=2,
        stdout_path=out,
        stderr_path=err,
    )
    assert (code, timed_out) == (None, True)
    assert time.time() - started < 30
    assert out.read_text().strip() == "started"


def test_launch_returns_the_exit_code(tmp_path):
    code, timed_out = harness.launch(
        [sys.executable, "-c", "raise SystemExit(3)"],
        env=dict(os.environ),
        cwd=tmp_path,
        timeout_s=30,
        stdout_path=tmp_path / "o",
        stderr_path=tmp_path / "e",
    )
    assert (code, timed_out) == (3, False)


def test_a_cut_off_stream_keeps_every_complete_event():
    text = '{"type": "assistant"}\n{"type": "user"}\n{"type": "assi'
    assert harness.parse_stream(text) == [{"type": "assistant"}, {"type": "user"}]


def test_the_gaia_child_records_calls_as_they_happen(tmp_path):
    from gaia.eval.bench import gaia_child

    class Agent:
        def __init__(self):
            self.chat = type("Chat", (), {"send_messages": lambda self, *a: "reply"})()

        def _execute_tool(self, name, args):
            return {"status": "success", "echo": args}

    agent = Agent()
    progress = tmp_path / "progress.jsonl"
    gaia_child.instrument(progress)(agent)
    agent.chat.send_messages([])
    agent._execute_tool("read_file", {"file_path": "x"})
    lines = [json.loads(line) for line in progress.read_text().splitlines()]
    assert lines == [
        {"event": "llm_call"},
        {
            "role": "tool",
            "name": "read_file",
            "tool_args": {"file_path": "x"},
            "content": {"status": "success", "echo": {"file_path": "x"}},
        },
    ]
