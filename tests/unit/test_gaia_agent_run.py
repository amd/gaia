# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Failing (TDD) contract tests for ``gaia agent run <id>`` (#2242).

There is currently no CLI path to launch an arbitrary registered agent (built-in
or a user's custom agent under ``~/.gaia/agents/<id>/agent.py``) other than the
two hardcoded verbs ``gaia browse``/``gaia analyze``. These tests fix the
observable CLI contract for the new ``gaia agent run <id>`` subcommand *before*
it is implemented, per TDD. They deliberately do not import or assume the name
of the shared discover/get/create_agent helper the implementation will add —
only ``gaia.agents.registry.AgentRegistry`` (already public) and the CLI entry
point (``gaia.cli.main``) are treated as fixed surface.

Every test in this module is expected to FAIL until #2242 is implemented,
typically with an argparse ``SystemExit(2)`` ("invalid choice: 'run'") since
the ``run`` subcommand does not exist yet.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixture helpers — write a real ~/.gaia/agents/<id>/agent.py to disk so
# gaia.agents.registry.AgentRegistry.discover() picks it up exactly the way a
# real user's custom agent would be discovered (no mocking of discovery
# itself).
# ---------------------------------------------------------------------------

_MARKER_AGENT_TEMPLATE = '''\
from gaia.agents.base.agent import Agent


class {cls}(Agent):
    """Test-only custom agent for #2242 CLI contract tests."""

    AGENT_ID = "{id}"
    AGENT_NAME = "{cls}"

    def __init__(self, **kwargs):
        # Force skip_lemonade so construction never needs a live Lemonade
        # server, regardless of what the CLI/registry thread through.
        kwargs["skip_lemonade"] = True
        kwargs["silent_mode"] = False
        super().__init__(**kwargs)

    def _register_tools(self):
        pass

    def process_query(self, query, **kwargs):
        # No LLM call at all — the response is a canned marker so the test
        # can prove this exact class's process_query ran with this query,
        # without needing a live Lemonade server.
        print("CUSTOM_MARKER_RESPONSE::" + str(query))
        return dict(status="success", result="custom-response-to::" + str(query))
'''

_EXPLODING_AGENT_TEMPLATE = '''\
from gaia.agents.base.agent import Agent


class {cls}(Agent):
    """Test-only custom agent whose constructor always raises (#2242)."""

    AGENT_ID = "{id}"
    AGENT_NAME = "{cls}"

    def __init__(self, **kwargs):
        raise RuntimeError(
            "synthetic-construction-failure: {id} always fails to construct "
            "(test fixture for #2242)"
        )

    def _register_tools(self):
        pass
'''


def _write_custom_agent(
    home: Path, agent_id: str, class_name: str, template: str
) -> Path:
    """Write a ``~/.gaia/agents/<agent_id>/agent.py`` under *home*."""
    agent_dir = home / ".gaia" / "agents" / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "agent.py").write_text(
        template.format(cls=class_name, id=agent_id), encoding="utf-8"
    )
    return agent_dir


def _run_cli(argv):
    """Invoke ``gaia.cli.main()`` with *argv* as ``sys.argv``, restoring after."""
    from gaia import cli

    original_argv = sys.argv
    try:
        sys.argv = argv
        return cli.main()
    finally:
        sys.argv = original_argv


# ---------------------------------------------------------------------------
# 1. Unknown agent id -> actionable error naming both the bad id and a real
#    registered id, non-zero exit, no raw traceback.
# ---------------------------------------------------------------------------


def test_run_unknown_agent_id_reports_available_ids(monkeypatch, tmp_path, capsys):
    # Sandbox HOME so AgentRegistry.discover() sees a clean, deterministic set
    # of agents (no leftover custom agents from the real environment).
    monkeypatch.setenv("HOME", str(tmp_path))

    from gaia.agents.registry import AgentRegistry

    registry = AgentRegistry()
    registry.discover()
    known_ids = [reg.id for reg in registry.list()]
    assert known_ids, (
        "expected at least one real registered agent id (e.g. the 'builder' "
        "built-in) to assert against — if this fails, the test environment "
        "itself has no discoverable agents at all"
    )

    bogus_id = "totally-bogus-agent-id-2242"

    with pytest.raises(SystemExit) as excinfo:
        _run_cli(["gaia", "agent", "run", bogus_id, "-q", "hello"])

    assert excinfo.value.code not in (0, None), "unknown agent id must exit non-zero"

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert bogus_id in combined, "error must name the unknown id the user typed"
    assert any(kid in combined for kid in known_ids), (
        "error must name at least one real registered agent id so the user "
        "knows what IS available"
    )
    assert "Traceback (most recent call last)" not in combined, (
        "an unknown agent id must produce a clean CLI error, not a raw "
        "Python traceback"
    )


# ---------------------------------------------------------------------------
# 2. A registered custom_python agent resolves and is actually constructed
#    (not just "no crash" — proven via a class-specific marker in the
#    --list-tools output, which only prints if the real custom class was
#    instantiated and its console reached).
# ---------------------------------------------------------------------------


def test_run_resolves_and_constructs_custom_python_agent(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    agent_id = "custom-marker-agent-2242"
    class_name = "CustomMarkerAgent2242"
    _write_custom_agent(tmp_path, agent_id, class_name, _MARKER_AGENT_TEMPLATE)

    rc = _run_cli(["gaia", "agent", "run", agent_id, "--list-tools"])
    assert rc in (0, None), "listing tools for a valid custom agent must succeed"

    out = capsys.readouterr().out
    assert f"Registered Tools for {class_name}" in out, (
        "the --list-tools output must come from the real custom agent class "
        "instantiated by the registry, not a stub or a different agent"
    )


# ---------------------------------------------------------------------------
# 3. A built-in agent id also resolves through the SAME path (not a
#    custom-only special case). Uses the in-core 'builder' built-in, which
#    needs no external hub wheel, so it is always available in a bare
#    framework unit-test environment.
# ---------------------------------------------------------------------------


def test_run_resolves_and_constructs_builtin_agent(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))

    from gaia.llm.lemonade_manager import LemonadeManager

    # BuilderAgent.__init__ -> Agent.__init__ calls LemonadeManager.ensure_ready
    # unless skip_lemonade=True is threaded through; stub it so construction
    # doesn't need a live Lemonade server (behavioral boundary, not an
    # assumption about how `run` wires kwargs).
    monkeypatch.setattr(LemonadeManager, "ensure_ready", lambda **kwargs: True)

    rc = _run_cli(["gaia", "agent", "run", "builder", "--list-tools"])
    assert rc in (
        0,
        None,
    ), "listing tools for the built-in 'builder' agent must succeed"

    out = capsys.readouterr().out
    assert "Registered Tools for BuilderAgent" in out, (
        "the --list-tools output must come from the real, in-core "
        "BuilderAgent class — proving 'run' reaches built-in construction, "
        "not only custom_python agents"
    )


# ---------------------------------------------------------------------------
# 4. Construction failure -> clean, actionable message naming the agent id
#    and the underlying cause; non-zero exit; no raw traceback to the user.
# ---------------------------------------------------------------------------


def test_run_agent_construction_failure_is_reported_cleanly(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("HOME", str(tmp_path))
    agent_id = "exploding-agent-2242"
    class_name = "ExplodingAgent2242"
    _write_custom_agent(tmp_path, agent_id, class_name, _EXPLODING_AGENT_TEMPLATE)

    with pytest.raises(SystemExit) as excinfo:
        _run_cli(["gaia", "agent", "run", agent_id, "-q", "hello"])

    assert excinfo.value.code not in (
        0,
        None,
    ), "a construction failure must exit non-zero, not silently succeed"

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert agent_id in combined, "the error must name the agent id that failed"
    assert (
        "synthetic-construction-failure" in combined
    ), "the error must surface the underlying cause, not swallow it"
    assert "Traceback (most recent call last)" not in combined, (
        "a constructor exception must be converted into a clean CLI error, "
        "not dumped to the user as a raw Python traceback (raise ... from e "
        "chaining internally is fine — that's not what this asserts)"
    )


# ---------------------------------------------------------------------------
# 5. `gaia agent run <id>` and `gaia browse` resolve agents through
#    equivalent AgentRegistry calls — proving one shared discover/get/
#    create_agent code path, not a second, independently reimplemented one
#    living only in cli_agent.py's `run` command.
#
# This is a black-box, behavioral coupling test rather than a check on a
# specific helper function name: the shared helper's name/location is an
# implementation decision left to whoever implements #2242, not part of this
# test's contract. If neither browse/analyze nor `run` end up calling
# AgentRegistry.get()/.create_agent() with the same (agent_id) shape, this is
# the strongest signal available at this layer that the two paths diverged;
# full duplicate-inline-code detection (e.g. "browse doesn't have its own
# second copy of the get()-is-None-check + create_agent() pair") is left to
# code review, since asserting that reliably requires knowing the
# implementation's file layout, which this test intentionally does not.
# ---------------------------------------------------------------------------


def test_run_and_browse_share_the_same_registry_resolution_path(
    monkeypatch, tmp_path, capsys
):
    pytest.importorskip("gaia_agent_browser")

    monkeypatch.setenv("HOME", str(tmp_path))

    from gaia.agents.registry import AgentRegistry
    from gaia.llm.lemonade_manager import LemonadeManager

    monkeypatch.setattr(LemonadeManager, "ensure_ready", lambda **kwargs: True)

    call_log = []
    orig_get = AgentRegistry.get
    orig_create_agent = AgentRegistry.create_agent

    def logging_get(self, agent_id):
        call_log.append(("get", agent_id))
        return orig_get(self, agent_id)

    def logging_create_agent(self, agent_id, **kwargs):
        call_log.append(("create_agent", agent_id))
        return orig_create_agent(self, agent_id, **kwargs)

    monkeypatch.setattr(AgentRegistry, "get", logging_get)
    monkeypatch.setattr(AgentRegistry, "create_agent", logging_create_agent)

    _run_cli(["gaia", "browse", "--no-lemonade-check", "--list-tools"])
    browse_calls = list(call_log)
    call_log.clear()
    capsys.readouterr()  # discard browse's stdout before the run invocation

    _run_cli(["gaia", "agent", "run", "web", "--list-tools"])
    run_calls = list(call_log)

    assert (
        "get",
        "web",
    ) in browse_calls, "gaia browse must resolve id 'web' via AgentRegistry.get()"
    assert ("create_agent", "web") in browse_calls
    assert ("get", "web") in run_calls, (
        "gaia agent run web must resolve the SAME id 'web' via the SAME "
        "AgentRegistry.get() call, not a separate lookup mechanism"
    )
    assert ("create_agent", "web") in run_calls, (
        "gaia agent run web must construct via the SAME AgentRegistry."
        "create_agent(), not a duplicated construction path"
    )
    # Both entry points went through exactly the same *set* of registry
    # operations for the same agent id.
    assert (
        {c[0] for c in browse_calls}
        == {c[0] for c in run_calls}
        == {
            "get",
            "create_agent",
        }
    )


# ---------------------------------------------------------------------------
# 6. End-to-end integration: a real custom agent, run via
#    `gaia agent run <id> -q "<query>"`, produces a response.
# ---------------------------------------------------------------------------


def test_run_custom_agent_end_to_end_produces_a_response(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    agent_id = "custom-e2e-agent-2242"
    class_name = "CustomE2EAgent2242"
    _write_custom_agent(tmp_path, agent_id, class_name, _MARKER_AGENT_TEMPLATE)

    query = "what is the answer to 2242"
    rc = _run_cli(["gaia", "agent", "run", agent_id, "-q", query])
    assert rc in (0, None), "a successful query must not exit non-zero"

    out = capsys.readouterr().out
    assert f"CUSTOM_MARKER_RESPONSE::{query}" in out, (
        "the real custom agent's process_query must have run with the "
        "given query and produced a response, end-to-end through "
        "'gaia agent run <id> -q'"
    )
