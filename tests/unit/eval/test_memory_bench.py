# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The long-horizon memory benchmark (``gaia eval memory``, #4727).

The ``pr`` suite replays six people's months of chats through the real memory
pipeline and must hold the committed baseline check by check. The rest pins the
bench's own machinery: scenario validation, the simulated clock, the gate.
"""

from __future__ import annotations

import importlib.util
import json
import textwrap
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import gaia.agents.base.memory as memory_mod
import gaia.agents.base.memory_store as store_mod
from gaia.eval.memory import runner
from gaia.eval.memory.clock import simulated_time
from gaia.eval.memory.harness import CheckResult, ScriptedChat
from gaia.eval.memory.scenario import ScenarioError, load_scenario, load_scenarios

#: A replay needs faiss for vector search, and a strict replay fails on memory's
#: "vector search disabled" warning; skip visibly rather than fail or mis-pass.
requires_faiss = pytest.mark.skipif(
    importlib.util.find_spec("faiss") is None,
    reason="faiss-cpu is not installed; the memory bench replay needs it for "
    "vector search (pip install faiss-cpu). The Unit Tests workflow installs it.",
)

BASELINE = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "eval_baselines"
    / "memory-bench"
    / "pr.json"
)


@requires_faiss
def test_the_pr_suite_holds_its_baseline():
    """A check the baseline passes must still pass.

    On an intended change, replace the baseline with the new run's
    results.json: ``gaia eval memory --suite pr --out <dir>`` and copy it.
    """
    result = runner.run_suite("pr")
    gated = runner.gate(result, json.loads(BASELINE.read_text(encoding="utf-8")))

    assert not gated.missing_checks, (
        "baseline checks that no longer run (update the baseline in the same "
        f"change): {gated.missing_checks}"
    )
    assert not gated.regressions, (
        "memory regressed against tests/fixtures/eval_baselines/memory-bench/"
        f"pr.json: {gated.regressions}"
    )


def test_every_scenario_loads():
    scenarios = load_scenarios()
    assert len(scenarios) >= 6
    assert all(s.probes for s in scenarios)


# ---------------------------------------------------------------------------
# Scenario validation fails loudly
# ---------------------------------------------------------------------------

GOOD = """
id: tiny
title: Tiny
persona: Someone
end: 2026-02-01T09:00
sessions:
  - id: s01
    date: 2026-01-01T09:00
    turns:
      - user: "I live in Lisbon these days"
        assistant: "Lovely city."
probes:
  - id: p1
    at: end
    kind: current_fact
    stored: [Lisbon]
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "scenario.yaml"
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def test_a_good_scenario_loads(tmp_path):
    scenario = load_scenario(_write(tmp_path, GOOD))
    assert scenario.id == "tiny"
    assert scenario.sessions[0].turns[0].user.startswith("I live in Lisbon")


@pytest.mark.parametrize(
    "edit, message",
    [
        (
            lambda s: s.replace("title: Tiny", "title: Tiny\ncolour: blue"),
            "unknown field",
        ),
        (lambda s: s.replace("at: end", "at: s09"), "neither 'end' nor a session id"),
        (lambda s: s.replace("kind: current_fact", "kind: vibes"), "kind 'vibes'"),
        (lambda s: s.replace("stored: [Lisbon]", "stored: ['(']"), "bad regex"),
        (
            lambda s: s.replace("end: 2026-02-01T09:00", "end: 2025-12-01T09:00"),
            "end must be after",
        ),
        (lambda s: s.replace("stored: [Lisbon]", "seen: [Lisbon]"), "need a query"),
    ],
)
def test_a_malformed_scenario_is_refused(tmp_path, edit, message):
    with pytest.raises(ScenarioError, match=message):
        load_scenario(_write(tmp_path, edit(textwrap.dedent(GOOD))))


def test_sessions_must_be_in_date_order(tmp_path):
    text = textwrap.dedent(GOOD).replace(
        "probes:",
        """  - id: s02
    date: 2025-12-01T09:00
    turns:
      - user: "Earlier than the first chat"
        assistant: "Hm."
probes:""",
    )
    with pytest.raises(ScenarioError, match="date order"):
        load_scenario(_write(tmp_path, text))


# ---------------------------------------------------------------------------
# The simulated clock
# ---------------------------------------------------------------------------


def test_the_clock_moves_memorys_now_and_is_restored():
    real_memory_datetime = memory_mod.datetime
    with simulated_time() as clock:
        clock.start(datetime(2026, 1, 5, 20, 0))
        assert store_mod.datetime.now().date().isoformat() == "2026-01-05"
        clock.set(datetime(2026, 7, 14, 9, 0))  # across a daylight-saving change
        now = store_mod.datetime.now()
        assert (now.month, now.day, now.hour) == (7, 14, 9)
        with pytest.raises(ValueError, match="backwards"):
            clock.set(datetime(2026, 3, 1, 9, 0))
    assert memory_mod.datetime is real_memory_datetime
    assert abs(memory_mod.datetime.now() - datetime.now()) < timedelta(seconds=5)


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def _result(checks):
    return runner.SuiteResult(
        suite="pr",
        model="m",
        started=0,
        seconds=0,
        scenarios=["s"],
        checks=[{"key": k, "passed": ok} for k, ok in checks.items()],
        metrics={},
        by_kind={},
        memories_at_end={},
    )


def test_the_gate_fails_on_a_regression_and_on_a_dropped_check():
    baseline = {
        "suite": "pr",
        "checks": [
            {"key": "s/p/seen:a", "passed": True},
            {"key": "s/p/seen:b", "passed": False},
            {"key": "s/p/seen:gone", "passed": True},
        ],
    }
    gated = runner.gate(
        _result({"s/p/seen:a": False, "s/p/seen:b": True, "s/p/seen:new": True}),
        baseline,
    )

    assert gated.regressions == ["s/p/seen:a"]
    assert gated.fixed == ["s/p/seen:b"]
    assert gated.new_checks == ["s/p/seen:new"]
    assert gated.missing_checks == ["s/p/seen:gone"]
    assert not gated.passed


def test_the_gate_refuses_a_baseline_from_another_suite():
    with pytest.raises(ValueError, match="suite"):
        runner.gate(_result({}), {"suite": "full", "checks": []})


def test_the_scripted_model_refuses_requests_it_does_not_script():
    [scenario] = load_scenarios(["jordan_reminders_change"])
    chat = ScriptedChat(scenario, resolve=lambda pattern: "id")
    with pytest.raises(RuntimeError, match="does not script"):
        chat.send_messages([{"role": "user", "content": "Write me a poem"}])


def test_a_failed_check_is_named_in_the_report():
    check = CheckResult(
        scenario="maya",
        probe="home",
        kind="changed_fact",
        check="unseen",
        target="Calgary",
        passed=False,
    )
    result = _result({})
    result.checks = [{**check.__dict__, "key": check.key}]
    result.metrics = {k: None for k in (*runner.METRICS, "overall")}
    assert "`maya/home/unseen:Calgary`" in runner.render_report(result)


@requires_faiss
def test_a_scripting_slip_fails_the_replay_instead_of_passing_quietly(tmp_path):
    """Production logs a failed extraction and carries on; the bench must not."""
    from gaia.eval.memory.harness import HashEmbedder, Replay

    broken = textwrap.dedent(GOOD).replace(
        '        assistant: "Lovely city."',
        '        assistant: "Lovely city."\n'
        "        extract:\n"
        '          - {op: update, update_of: "nothing matches this", content: "x"}',
    )
    scenario = load_scenario(_write(tmp_path, broken))
    replay = Replay(
        scenario,
        tmp_path,
        chat_factory=lambda resolve: ScriptedChat(scenario, resolve),
        embedder=HashEmbedder(),
        strict=True,
    )
    with pytest.raises(RuntimeError, match="strict replay") as raised:
        replay.run()
    # The slip itself, not some other warning (a missing faiss, say), failed it.
    assert "no current memory matches it" in str(raised.value)


@requires_faiss
def test_a_scripted_update_matching_two_memories_fails_the_replay(tmp_path):
    """A loose ``update_of`` must not quietly pick one of several memories."""
    from gaia.eval.memory.harness import HashEmbedder, Replay

    ambiguous = textwrap.dedent(GOOD).replace(
        '      - user: "I live in Lisbon these days"\n'
        '        assistant: "Lovely city."',
        '      - user: "I live in Lisbon these days and work in Lisbon too"\n'
        '        assistant: "Lovely city."\n'
        "        extract:\n"
        '          - {op: add, category: fact, content: "User lives in Lisbon"}\n'
        '          - {op: add, category: fact, content: "User works at a bakery in '
        'Lisbon"}\n'
        '      - user: "Actually I moved my whole life over to Porto now"\n'
        '        assistant: "Porto it is."\n'
        "        extract:\n"
        '          - {op: update, update_of: "Lisbon", category: fact, '
        'content: "User lives in Porto"}',
    )
    scenario = load_scenario(_write(tmp_path, ambiguous))
    replay = Replay(
        scenario,
        tmp_path,
        chat_factory=lambda resolve: ScriptedChat(scenario, resolve),
        embedder=HashEmbedder(),
        strict=True,
    )
    with pytest.raises(RuntimeError, match="strict replay") as raised:
        replay.run()
    assert "matches 2 current memories" in str(raised.value)


@pytest.mark.parametrize("suite", ["pr", None])
def test_model_without_the_full_suite_is_refused(suite, capsys, monkeypatch):
    """--model only steers the full suite; the pr suite must not ignore it."""
    from gaia import cli

    args = ["gaia", "eval", "memory", "--model", "Some-Model-GGUF"]
    if suite:
        args += ["--suite", suite]
    monkeypatch.setattr("sys.argv", args)
    monkeypatch.setattr(
        runner, "run_suite", lambda *a, **k: pytest.fail("the suite ran")
    )
    with pytest.raises(SystemExit) as exited:
        cli.main()
    assert exited.value.code == 2
    assert "--model applies only to --suite full" in capsys.readouterr().out


def test_gating_one_scenario_compares_only_that_scenario():
    baseline = {
        "suite": "pr",
        "checks": [
            {"key": "maya/p/seen:x", "passed": True},
            {"key": "sam/p/seen:y", "passed": True},
        ],
    }
    result = _result({"maya/p/seen:x": True})
    result.scenarios = ["maya"]

    gated = runner.gate(result, baseline)

    assert gated.passed and not gated.missing_checks
