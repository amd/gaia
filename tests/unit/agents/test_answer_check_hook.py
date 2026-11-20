# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``answer_problems``: a subclass's own answer check gets one repair round."""

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY


class _CheckedAgent(Agent):
    """Flags any answer that still says BAD."""

    def _get_system_prompt(self):
        return "Answer."

    def _register_tools(self):
        pass

    def answer_problems(self, answer):
        return ["The answer still says BAD."] if "BAD" in (answer or "") else []


class _PlainAgent(_CheckedAgent):
    def answer_problems(self, answer):
        return Agent.answer_problems(self, answer)


def _make(cls, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = cls(silent_mode=True, skip_lemonade=True)
    agent.streaming = False
    agent.console = MagicMock()
    agent.console.cancelled = None
    return agent


@pytest.fixture(autouse=True)
def _registry():
    snapshot = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def _script(agent, *answers):
    sent = []
    left = list(answers)

    def send(messages, *a, **kw):
        sent.append([dict(m) for m in messages])
        assert left, "unexpected model call"
        return MagicMock(
            text=json.dumps({"answer": left.pop(0)}), stats={}, finish_reason="stop"
        )

    agent.chat = MagicMock()
    agent.chat.send_messages.side_effect = send
    return sent


def _repairs(sent):
    return [
        m["content"]
        for m in sent[-1]
        if m.get("role") == "user" and "[check:answer]" in str(m.get("content"))
    ]


def test_a_flagged_answer_is_sent_back_once_and_the_repair_is_kept(
    monkeypatch, tmp_path
):
    agent = _make(_CheckedAgent, monkeypatch, tmp_path)
    sent = _script(agent, "BAD draft", "Fixed answer.")
    result = agent.process_query("question")
    assert len(sent) == 2
    (repair,) = _repairs(sent)
    assert "The answer still says BAD." in repair
    assert result["result"] == "Fixed answer."


def test_a_problem_left_after_the_repair_is_named_to_the_user(monkeypatch, tmp_path):
    agent = _make(_CheckedAgent, monkeypatch, tmp_path)
    sent = _script(agent, "BAD draft", "Still BAD.")
    result = agent.process_query("question")
    assert len(sent) == 2, "only one repair round"
    assert result["result"].startswith("Still BAD.")
    assert "The answer still says BAD." in result["result"]


def test_an_agent_without_a_check_answers_once(monkeypatch, tmp_path):
    agent = _make(_PlainAgent, monkeypatch, tmp_path)
    sent = _script(agent, "BAD but unchecked")
    result = agent.process_query("question")
    assert len(sent) == 1
    assert result["result"] == "BAD but unchecked"


_PAGE = "https://www.example.org/report/page-one"


class _CitingAgent(Agent):
    """Flags a cited link it never fetched, the way the flagship's research check does."""

    def _get_system_prompt(self):
        return "Answer with citations."

    def _register_tools(self):
        from gaia.agents.base.tools import tool

        agent = self

        @tool
        def fetch_page(url: str) -> dict:
            """Read a web page."""
            agent.fetched.append(url)
            return {"status": "success", "content": f"text of {url}"}

    def answer_problems(self, answer):
        if _PAGE in (answer or "") and _PAGE not in self.fetched:
            return [f"This cited page was never opened: {_PAGE}."]
        return []


def _script_steps(agent, *steps):
    sent = []
    left = list(steps)

    def send(messages, *a, **kw):
        sent.append([dict(m) for m in messages])
        assert left, "unexpected model call"
        return MagicMock(text=json.dumps(left.pop(0)), stats={}, finish_reason="stop")

    agent.chat = MagicMock()
    agent.chat.send_messages.side_effect = send
    return sent


def _citing(monkeypatch, tmp_path):
    agent = _make(_CitingAgent, monkeypatch, tmp_path)
    agent.fetched = []
    agent._tool_requires_confirmation = lambda *a, **kw: False
    return agent


def test_the_repair_may_open_the_page_it_was_asked_to(monkeypatch, tmp_path):
    """The turn is already answered when the check runs; the repair reopens it."""
    agent = _citing(monkeypatch, tmp_path)
    report = f"The finding holds [source]({_PAGE})."
    _script_steps(
        agent,
        {"answer": report},
        {"tool": "fetch_page", "tool_args": {"url": _PAGE}},
        {"answer": report},
    )
    result = agent.process_query("research the finding")
    assert agent.fetched == [_PAGE], "the repair's fetch was refused"
    assert result["result"] == report


def test_an_acknowledgement_does_not_replace_the_report(monkeypatch, tmp_path):
    agent = _citing(monkeypatch, tmp_path)
    report = (
        "## Findings\n\nThe finding holds, with a long explanation of why it "
        f"matters and what the sources say [source]({_PAGE}).\n\n## Sources\n- {_PAGE}"
    )
    _script_steps(agent, {"answer": report}, {"answer": "Noted."})
    result = agent.process_query("research the finding")
    assert result["result"].startswith("## Findings")
    assert f"This cited page was never opened: {_PAGE}." in result["result"]


def test_a_repair_that_runs_out_of_steps_keeps_the_report(monkeypatch, tmp_path):
    """No summary written with tools withheld: the report stays, its problems named."""
    import gaia.agents.base.agent as agent_module

    monkeypatch.setattr(agent_module, "_ANSWER_REPAIR_STEPS", 1)
    agent = _citing(monkeypatch, tmp_path)
    agent.max_steps = 1
    report = f"## Findings\n\nThe finding holds [source]({_PAGE})."
    other = "https://www.example.org/report/page-two"
    _script_steps(
        agent,
        {"answer": report},
        {"tool": "fetch_page", "tool_args": {"url": other}},
    )
    result = agent.process_query("research the finding")
    assert result["result"].startswith("## Findings")
    assert f"This cited page was never opened: {_PAGE}." in result["result"]


def test_a_repair_near_the_step_limit_still_gets_room(monkeypatch, tmp_path):
    agent = _citing(monkeypatch, tmp_path)
    agent.max_steps = 1
    report = f"The finding holds [source]({_PAGE})."
    _script_steps(
        agent,
        {"answer": report},
        {"tool": "fetch_page", "tool_args": {"url": _PAGE}},
        {"answer": report},
    )
    result = agent.process_query("research the finding")
    assert agent.fetched == [_PAGE]
    assert result["result"] == report


class _FailingFetchAgent(_CitingAgent):
    def _register_tools(self):
        from gaia.agents.base.tools import tool

        @tool
        def fetch_page(url: str) -> dict:
            """Read a web page."""
            return {"status": "error", "error": f"404 Not Found for url: {url}"}


def test_a_turn_the_failure_breaker_ends_still_names_its_unread_citations(
    monkeypatch, tmp_path
):
    """A model whose every fetch 404s answered from memory, citing a dead page."""
    agent = _make(_FailingFetchAgent, monkeypatch, tmp_path)
    agent.fetched = []
    agent._tool_requires_confirmation = lambda *a, **kw: False
    fetches = [
        {"tool": "fetch_page", "tool_args": {"url": f"https://example.org/p{i}"}}
        for i in range(6)
    ]
    report = f"From what I know, the finding holds [source]({_PAGE})."
    # Four failures, a refused fetch, then one more ends the turn: a closing answer.
    _script_steps(agent, *fetches, {"answer": report})
    result = agent.process_query("research the finding")
    assert result["result"].startswith("From what I know")
    assert f"This cited page was never opened: {_PAGE}." in result["result"]
