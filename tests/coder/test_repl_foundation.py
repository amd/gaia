# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Foundation tests for the new gaia-coder interactive REPL stack.

Covers :mod:`gaia.coder.llm`, :mod:`gaia.coder.tool_schema`,
:mod:`gaia.coder.agent`, and :mod:`gaia.coder.repl` — the four files
that land in the ``feat/coder-interactive`` branch.

Real Anthropic calls are stubbed at the :class:`gaia.coder.llm.CoderLLM`
seam; tool dispatch uses the actual ``@tool`` registry so changes to
the mixins are caught here.
"""

from __future__ import annotations

import json
import os
import types
from pathlib import Path
from typing import Any, List
from unittest.mock import MagicMock

import pytest

from gaia.coder.agent import (
    READ_TOOLS,
    WRITE_TOOLS,
    Agent,
    Message,
    SendResult,
    auto_approve_policy,
    safe_default_policy,
)
from gaia.coder.llm import AssistantTurn, CoderLLM, ToolUse, Usage
from gaia.coder.tool_schema import ToolDispatcher, build_anthropic_tools

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_coder_home(tmp_path, monkeypatch):
    """Redirect ``$GAIA_CODER_HOME`` so REPL state doesn't pollute the user."""
    monkeypatch.setenv("GAIA_CODER_HOME", str(tmp_path / "coder-home"))
    yield


@pytest.fixture(autouse=True, scope="session")
def _register_tools():
    """Tool registration is side-effecting (``@tool`` decorator).

    The tool mixins only register their tools when ``register_*_tools()``
    is called. Tests that exercise the tool registry need that
    registration to have happened. Construct one transient instance per
    session.
    """
    from gaia.coder.tools.cli import CLIToolsMixin
    from gaia.coder.tools.github import GitHubToolsMixin
    from gaia.coder.tools.search import SearchToolsMixin

    class _Reg(SearchToolsMixin, CLIToolsMixin):
        pass

    r = _Reg()
    r.register_search_tools()
    r.register_cli_tools()
    GitHubToolsMixin().register_github_tools()
    yield


@pytest.fixture
def stub_llm():
    """A :class:`CoderLLM` whose ``chat_with_tools`` is fully stubbed.

    Tests pre-load the queue of :class:`AssistantTurn` objects the stub
    should return; ``chat_with_tools`` pops them in order. ``complete``
    returns whatever ``stub_llm._next_complete`` is set to.
    """

    class _StubLLM(CoderLLM):
        def __init__(self):
            # Skip the parent ``__init__`` — we don't want to construct a
            # real ClaudeClient (would require ANTHROPIC_API_KEY).
            self._model = "stub-model"
            self._default_max_tokens = 4096
            self._client = None
            self.queue: List[AssistantTurn] = []
            self.next_complete: str = ""
            self.complete_calls: List[str] = []

        def complete(self, prompt: str, **_kwargs: Any) -> str:
            self.complete_calls.append(prompt)
            return self.next_complete

        def chat_with_tools(self, **_kwargs: Any) -> AssistantTurn:
            if not self.queue:
                raise AssertionError("StubLLM.chat_with_tools called with empty queue")
            return self.queue.pop(0)

    return _StubLLM()


def _turn(text: str = "", tool_uses=(), stop_reason: str = "end_turn") -> AssistantTurn:
    return AssistantTurn(
        text=text,
        tool_uses=tuple(tool_uses),
        stop_reason=stop_reason,
        usage=Usage(
            input_tokens=10, output_tokens=20, input_cost_usd=0.0, output_cost_usd=0.0
        ),
        raw_content=tuple(
            ([{"type": "text", "text": text}] if text else [])
            + [
                {"type": "tool_use", "id": u.id, "name": u.name, "input": dict(u.input)}
                for u in tool_uses
            ]
        ),
    )


# ---------------------------------------------------------------------------
# tool_schema
# ---------------------------------------------------------------------------


def test_build_anthropic_tools_includes_registered_tools():
    """Every @tool-registered function appears in the Anthropic payload."""
    tools = build_anthropic_tools()
    names = {t["name"] for t in tools}
    # Sentinel set — these all live in the existing tool mixins
    assert "read_file" in names
    assert "write_file" in names
    assert "edit_file" in names
    assert "search_code" in names
    assert "run_cli_command" in names


def test_build_anthropic_tools_input_schema_shape():
    tools = {t["name"]: t for t in build_anthropic_tools()}
    schema = tools["read_file"]["input_schema"]
    assert schema["type"] == "object"
    assert "path" in schema["properties"]
    assert schema["properties"]["path"]["type"] == "string"
    assert "path" in schema["required"]


def test_build_anthropic_tools_include_filter_validates_names():
    with pytest.raises(ValueError, match="unknown tool"):
        build_anthropic_tools(include=["does_not_exist"])


def test_build_anthropic_tools_exclude_filter_drops_tools():
    tools = build_anthropic_tools(exclude=["write_file", "edit_file"])
    names = {t["name"] for t in tools}
    assert "write_file" not in names
    assert "edit_file" not in names
    assert "read_file" in names


def test_dispatcher_runs_read_tool_with_safe_policy(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("world", encoding="utf-8")
    d = ToolDispatcher()
    result = d.run(
        tool_use_id="t1",
        name="read_file",
        tool_input={"path": str(f)},
    )
    assert not result.is_error
    assert result.content == "world"


def test_dispatcher_returns_error_block_on_tool_exception(tmp_path):
    """Tool internal errors become is_error=True results, never raise."""
    d = ToolDispatcher()
    result = d.run(
        tool_use_id="t1",
        name="read_file",
        tool_input={"path": str(tmp_path / "missing.txt")},
    )
    assert result.is_error
    assert "FileNotFoundError" in result.content


def test_dispatcher_unknown_tool_raises_keyerror():
    """Unknown tool names are an infrastructure bug; do not silently fail."""
    d = ToolDispatcher()
    with pytest.raises(KeyError, match="unknown tool"):
        d.run(tool_use_id="t1", name="not_a_tool", tool_input={})


def test_dispatcher_truncates_huge_output(tmp_path, monkeypatch):
    f = tmp_path / "big.txt"
    f.write_text("x" * 200_000, encoding="utf-8")
    d = ToolDispatcher(max_output_chars=1000)
    result = d.run(
        tool_use_id="t1",
        name="read_file",
        tool_input={"path": str(f)},
    )
    assert "truncated" in result.content
    assert len(result.content) < 1500  # cap + suffix


def test_dispatcher_permission_denial_returns_error_block():
    d = ToolDispatcher(permission_check=lambda n, i: "deny: not now")
    result = d.run(
        tool_use_id="t1",
        name="read_file",
        tool_input={"path": "/etc/passwd"},
    )
    assert result.is_error
    assert "PERMISSION DENIED" in result.content


# ---------------------------------------------------------------------------
# Agent — happy path with stub LLM
# ---------------------------------------------------------------------------


def test_agent_send_returns_text_when_no_tool_use(stub_llm):
    stub_llm.queue.append(_turn(text="Hello, world."))
    agent = Agent(llm=stub_llm)
    result = agent.send("hi there")
    assert isinstance(result, SendResult)
    assert result.text == "Hello, world."
    assert result.tool_calls == []
    assert result.iterations == 1


def test_agent_send_runs_tool_then_finalises(stub_llm, tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("payload", encoding="utf-8")
    stub_llm.queue.extend(
        [
            _turn(
                tool_uses=[ToolUse(id="u1", name="read_file", input={"path": str(f)})],
                stop_reason="tool_use",
            ),
            _turn(text="The file contains 'payload'."),
        ]
    )
    agent = Agent(llm=stub_llm, permission_policy=auto_approve_policy)
    result = agent.send("read x.txt")
    assert "payload" in result.text or result.text.endswith("'payload'.")
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["name"] == "read_file"
    assert not result.tool_calls[0]["is_error"]


def test_agent_send_runaway_loop_raises(stub_llm):
    """Loop without end_turn must raise, not silently truncate."""
    # Always a tool_use, never an end_turn
    for i in range(10):
        stub_llm.queue.append(
            _turn(
                tool_uses=[
                    ToolUse(id=f"u{i}", name="read_file", input={"path": "/nope"})
                ],
                stop_reason="tool_use",
            )
        )
    agent = Agent(llm=stub_llm, permission_policy=auto_approve_policy, max_iterations=3)
    with pytest.raises(RuntimeError, match="exceeded max_iterations"):
        agent.send("loop forever")


def test_agent_history_includes_user_assistant_and_tool_results(stub_llm, tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("payload", encoding="utf-8")
    stub_llm.queue.extend(
        [
            _turn(
                tool_uses=[ToolUse(id="u1", name="read_file", input={"path": str(f)})],
                stop_reason="tool_use",
            ),
            _turn(text="done"),
        ]
    )
    agent = Agent(llm=stub_llm, permission_policy=auto_approve_policy)
    agent.send("ok")
    roles = [m.role for m in agent.history]
    # user, assistant (tool_use), user (tool_result), assistant (end_turn)
    assert roles == ["user", "assistant", "user", "assistant"]


def test_agent_reset_clears_history(stub_llm):
    stub_llm.queue.append(_turn(text="hi"))
    agent = Agent(llm=stub_llm)
    agent.send("hi")
    assert len(agent.history) == 2
    agent.reset()
    assert agent.history == []
    assert agent.session_usage.turns == 0


def test_agent_session_usage_accumulates(stub_llm):
    stub_llm.queue.extend([_turn(text="a"), _turn(text="b")])
    agent = Agent(llm=stub_llm)
    agent.send("first")
    agent.send("second")
    assert agent.session_usage.turns == 2
    assert agent.session_usage.input_tokens == 20
    assert agent.session_usage.output_tokens == 40


def test_agent_safe_policy_denies_unclassified_tool():
    """Unknown tools must be denied by safe_default_policy, not auto-approved."""
    verdict = safe_default_policy("an_unknown_tool", {})
    assert verdict is not None
    assert verdict.startswith("deny:")


def test_agent_safe_policy_returns_prompt_for_write_tool():
    for name in WRITE_TOOLS:
        verdict = safe_default_policy(name, {})
        assert verdict == "prompt", f"{name} should require prompting"


def test_agent_safe_policy_approves_read_tools():
    for name in READ_TOOLS:
        verdict = safe_default_policy(name, {})
        assert verdict is None, f"{name} should auto-approve"


def test_agent_system_prompt_contains_required_sections(
    stub_llm, tmp_path, monkeypatch
):
    """System prompt must include identity / architecture / runtime sections."""
    # Write a CLAUDE.md inside repo_root so the section appears
    (tmp_path / "CLAUDE.md").write_text("project rules go here", encoding="utf-8")
    agent = Agent(llm=stub_llm, repo_root=tmp_path)
    sp = agent.system_prompt()
    assert "<identity>" in sp
    assert "<architecture>" in sp
    assert "<runtime>" in sp
    assert "<repo_claude_md>" in sp
    assert "project rules go here" in sp
    assert str(tmp_path) in sp  # repo_root is shown


# ---------------------------------------------------------------------------
# Repl — session save/load
# ---------------------------------------------------------------------------


def test_session_save_and_load_roundtrip(stub_llm, tmp_path, monkeypatch):
    from gaia.coder.repl import (
        _deserialise_history,
        list_sessions,
        load_session,
        save_session,
    )

    monkeypatch.setenv("GAIA_CODER_HOME", str(tmp_path / "home"))
    history = [
        Message(role="user", content="first message"),
        Message(role="assistant", content=[{"type": "text", "text": "first reply"}]),
    ]
    path = save_session(
        session_id="abc123",
        history=history,
        model="stub-model",
        repo_root=tmp_path,
        usage={"turns": 1, "total_tokens": 30},
    )
    assert path.exists()

    payload = load_session("abc123")
    assert payload["session_id"] == "abc123"
    assert payload["model"] == "stub-model"
    assert len(payload["history"]) == 2
    rehydrated = _deserialise_history(payload["history"])
    assert rehydrated[0].role == "user"
    assert rehydrated[1].role == "assistant"

    listed = list_sessions()
    assert any(s["id"] == "abc123" for s in listed)


def test_session_load_unknown_id_raises(tmp_path, monkeypatch):
    from gaia.coder.repl import load_session

    monkeypatch.setenv("GAIA_CODER_HOME", str(tmp_path / "home"))
    with pytest.raises(FileNotFoundError, match="not found"):
        load_session("no-such-id")


def test_session_load_unknown_schema_raises(tmp_path, monkeypatch):
    from gaia.coder.repl import _sessions_dir, load_session

    monkeypatch.setenv("GAIA_CODER_HOME", str(tmp_path / "home"))
    p = _sessions_dir() / "fubar.json"
    p.write_text(json.dumps({"schema_version": 99, "history": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        load_session("fubar")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_help_includes_repl_subcommand():
    """`gaia-coder --help` must advertise the repl subcommand."""
    from gaia.coder.cli import _build_parser

    parser = _build_parser()
    help_text = parser.format_help()
    assert "repl" in help_text
    assert "interactive" in help_text.lower()


def test_cli_no_subcommand_dispatches_to_repl(monkeypatch):
    """Running `gaia-coder` (no args) must invoke ``run_repl``."""
    from gaia.coder import cli

    captured = {}

    def fake_run_repl(**kwargs):
        captured.update(kwargs)
        return 7

    monkeypatch.setattr(cli, "_handle_repl", lambda args: fake_run_repl())
    rc = cli.main([])
    assert rc == 7


def test_cli_verbose_flag_sets_debug(monkeypatch):
    """`-v` must flip the root logger to DEBUG."""
    import logging

    from gaia.coder import cli

    monkeypatch.setattr(cli, "_handle_repl", lambda args: 0)
    cli.main(["-v"])
    assert logging.getLogger().level == logging.DEBUG
