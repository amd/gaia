# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``answer_problems``: a subclass's own answer check gets one repair round."""

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.memory import MemoryMixin
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


def test_a_reply_of_only_markup_debris_gets_one_repair(monkeypatch, tmp_path):
    # Qwen3.5-2B's whole reply to "how many files are in my documents folder?".
    agent = _make(_PlainAgent, monkeypatch, tmp_path)
    sent = _script(agent, '\n\n"}', "You have 12 files there.")
    result = agent.process_query("question")
    assert len(sent) == 2
    (repair,) = _repairs(sent)
    assert "garbled" in repair
    assert result["result"] == "You have 12 files there."


class _KeepingAgent(Agent):
    """Asked to keep something; flags any answer until ``keep`` has run."""

    def _get_system_prompt(self):
        return "Answer."

    def _register_tools(self):
        from gaia.agents.base.tools import tool

        agent = self

        @tool
        def keep(fact: str) -> dict:
            """Keep a fact."""
            agent.kept.append(fact)
            return {"status": "stored"}

    def _kept_this_turn(self):
        return any(r.get("tool") == "keep" for r in self._turn_tool_executions)

    def answer_problems(self, answer):
        return [] if self._kept_this_turn() else ["Nothing was kept."]

    def required_calls(self):
        return (
            [] if self._kept_this_turn() else [("keep", {"fact": "router is 10.0.0.1"})]
        )


def test_a_call_the_repair_ignored_is_made_once_and_answered_from(
    monkeypatch, tmp_path
):
    agent = _make(_KeepingAgent, monkeypatch, tmp_path)
    agent.kept = []
    sent = _script(agent, "Got it.", "Got it, really.", "Saved: router is 10.0.0.1.")
    result = agent.process_query("remember the router is 10.0.0.1")
    assert agent.kept == ["router is 10.0.0.1"]
    assert len(sent) == 3
    ran = [
        m["content"]
        for m in sent[-1]
        if m.get("role") == "user" and "needed `keep`" in str(m.get("content"))
    ]
    assert len(ran) == 1 and "'status': 'stored'" in ran[0]
    assert result["result"] == "Saved: router is 10.0.0.1."
    # The user sees the call GAIA made, like any other tool step.
    agent.console.print_tool_usage.assert_any_call("keep")
    agent.console.pretty_print_json.assert_any_call(
        {"fact": "router is 10.0.0.1"}, "Arguments"
    )
    agent.console.print_tool_complete.assert_called()


def test_a_required_call_is_made_at_most_once_per_turn(monkeypatch, tmp_path):
    agent = _make(_KeepingAgent, monkeypatch, tmp_path)
    agent.kept = []
    agent.required_calls = lambda: [("keep", {"fact": "x y"})]
    agent.answer_problems = lambda answer: ["Nothing was kept."]
    sent = _script(agent, "a", "b", "c")
    result = agent.process_query("remember x y")
    assert agent.kept == ["x y"]
    assert len(sent) == 3
    assert "Nothing was kept." in result["result"]


class _RememberingAgent(MemoryMixin, Agent):
    """Has remember, and records every call made to it."""

    def _get_system_prompt(self):
        return "Answer."

    def _register_tools(self):
        from gaia.agents.base.tools import tool

        agent = self

        @tool
        def remember(fact: str) -> dict:
            """Keep a fact."""
            agent.saved.append(fact)
            return {"status": "stored", "knowledge_id": "k1"}


@pytest.mark.parametrize(
    "request_text, fact",
    [
        ("Remember that my dog's name is Rex.", "my dog's name is Rex"),
        ("My birthday is March 3. Please remember that.", "My birthday is March 3"),
    ],
)
def test_a_plain_remember_request_the_model_skips_is_saved_once(
    monkeypatch, tmp_path, request_text, fact
):
    agent = _make(_RememberingAgent, monkeypatch, tmp_path)
    agent.saved = []
    agent.console.background_mode = False
    _script(agent, "Got it.", "Got it, noted.", "Okay.", "Saved.", "Saved.")
    agent.process_query(request_text)
    assert agent.saved == [fact]


def test_a_declined_detail_is_never_pushed_back_for_a_save(monkeypatch, tmp_path):
    agent = _make(_RememberingAgent, monkeypatch, tmp_path)
    agent.saved = []
    agent.console.background_mode = False
    sent = _script(agent, "Okay, I left your phone number out.")
    agent.process_query("Remember my phone is 555-1234 leave it out")
    assert agent.saved == []
    assert len(sent) == 1


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


class _ShellAgent(Agent):
    """Has a shell, the step #8803's request ruled out."""

    def _get_system_prompt(self):
        return "Answer."

    def _register_tools(self):
        from gaia.agents.base.tools import tool

        agent = self

        @tool
        def run_shell_command(command: str) -> dict:
            """Run a shell command."""
            agent.ran.append(command)
            return {"status": "success", "return_code": 0, "stdout": ""}


_FORBIDS = (
    "Use the file edit tool to replace alpha with beta in /tmp/ro.txt. "
    "Do not change permissions and do not use the shell."
)
_CHMOD = {
    "tool": "run_shell_command",
    "tool_args": {"command": "chmod u+w /tmp/ro.txt"},
}
_HIDDEN = "Done — `alpha` → `beta` in `/tmp/ro.txt`. A backup was saved."


def _shell(monkeypatch, tmp_path, approve):
    agent = _make(_ShellAgent, monkeypatch, tmp_path)
    agent.ran = []
    agent._tool_requires_confirmation = lambda *a, **kw: True
    agent.console.confirm_tool_execution.return_value = approve
    agent.console.confirmation_timed_out.return_value = False
    return agent


def test_an_approved_step_the_user_forbade_is_sent_back_to_be_disclosed(
    monkeypatch, tmp_path
):
    agent = _shell(monkeypatch, tmp_path, approve=True)
    told = "Done, but I ran `chmod u+w` on it, which you asked me not to."
    sent = _script_steps(agent, _CHMOD, {"answer": _HIDDEN}, {"answer": told})
    result = agent.process_query(_FORBIDS)
    assert agent.ran == ["chmod u+w /tmp/ro.txt"]
    (repair,) = _repairs(sent)
    assert "chmod u+w /tmp/ro.txt" in repair
    assert "went against the user's instruction" in repair
    assert result["result"] == told


def test_a_forbidden_step_left_unsaid_after_the_repair_is_named(monkeypatch, tmp_path):
    agent = _shell(monkeypatch, tmp_path, approve=True)
    sent = _script_steps(agent, _CHMOD, {"answer": _HIDDEN}, {"answer": _HIDDEN})
    result = agent.process_query(_FORBIDS)
    assert len(sent) == 3, "only one repair round"
    assert result["result"].startswith(_HIDDEN)
    assert "`chmod u+w /tmp/ro.txt`" in result["result"]


def test_a_declined_forbidden_step_is_not_sent_back(monkeypatch, tmp_path):
    agent = _shell(monkeypatch, tmp_path, approve=False)
    answer = "I can't make the replacement: the file is read-only."
    sent = _script_steps(agent, _CHMOD, {"answer": answer})
    result = agent.process_query(_FORBIDS)
    assert agent.ran == []
    assert _repairs(sent) == []
    assert result["result"] == answer


@pytest.mark.parametrize(
    "request_text",
    [
        "Replace alpha with beta in /tmp/ro.txt.",
        "Replace alpha with beta in /tmp/ro.txt. Do not touch any other file.",
    ],
)
def test_a_step_nobody_forbade_answers_once(monkeypatch, tmp_path, request_text):
    agent = _shell(monkeypatch, tmp_path, approve=True)
    sent = _script_steps(agent, _CHMOD, {"answer": _HIDDEN})
    result = agent.process_query(request_text)
    assert agent.ran == ["chmod u+w /tmp/ro.txt"]
    assert _repairs(sent) == []
    assert result["result"] == _HIDDEN


def test_only_a_permission_change_breaks_a_no_permission_change_request(
    monkeypatch, tmp_path
):
    agent = _shell(monkeypatch, tmp_path, approve=True)
    look = {"tool": "run_shell_command", "tool_args": {"command": "ls -l /tmp/ro.txt"}}
    sent = _script_steps(agent, look, {"answer": "It is `-r--r--r--`."})
    agent.process_query("Check /tmp/ro.txt but don't change its permissions.")
    assert agent.ran == ["ls -l /tmp/ro.txt"]
    assert _repairs(sent) == []


@pytest.mark.parametrize(
    "request_text, forbidden",
    [
        ("Do not change permissions and do not use the shell.", 2),
        ("Fix it without using the terminal.", 1),
        ("Don’t chmod the file.", 1),
        ("Edit it, but never change the file's permissions.", 1),
        ("No shell commands, please.", 1),
        ("I don't know how to use the shell, can you do it?", 0),
        ("Don't worry, use the shell if you need to.", 0),
        ("Run chmod on it.", 0),
        ("Don＇t use the shell.", 1),
        ("Do​n't use the shell.", 1),
        ("I don't use the shell much; please list the files.", 0),
        ("We never touch the terminal at work. What's in this folder?", 0),
        ("Why is there no shell in this container?", 0),
        ("There's no terminal on this box, so check it another way.", 0),
        ("Fix it, and do not use the shell.", 1),
        ("There’s no shell here, so read the file another way.", 0),
        ("there’s no terminal on this box. What's in /tmp?", 0),
        ("I don’t use the shell much; please list the files.", 0),
        ("Don’t use the shell.", 1),
    ],
)
def test_what_counts_as_ruling_a_step_out(request_text, forbidden):
    from gaia.agents.base.forbidden_steps import ruled_out

    assert len(ruled_out(request_text)) == forbidden


_RAN_CHMOD = [
    {"tool": "run_shell_command", "args": {"command": "chmod u+w ro.txt"}, "ran": True}
]


@pytest.mark.parametrize(
    "answer",
    [
        "Done, alpha is beta now. It's writable now.",
        "Done. I made the file writable first.",
        "Done, alpha is beta now. I didn't use the shell.",
        "Done. No command was run; no commands were run at all.",
        "Done. I never touched permissions.",
        "Done. I didn’t change the file's permissions.",
    ],
)
def test_saying_only_the_effect_does_not_disclose_the_step(answer):
    from gaia.agents.base.forbidden_steps import undisclosed_forbidden_steps

    request = "Replace alpha with beta in ro.txt. Don't change permissions."
    assert "`chmod u+w ro.txt`" in undisclosed_forbidden_steps(
        request, _RAN_CHMOD, answer
    )


@pytest.mark.parametrize(
    "request_text",
    [
        "Replace alpha with beta in ro.txt.",
        "Make ro.txt writable and fix the typo.",
        "Can you check the file permissions on ro.txt?",
        "There’s no shell on my laptop, can you run it for me?",
        "Why is there no terminal in this container?",
        "I don't use the shell much; please fix the file.",
        "Use the shell to fix the permissions.",
        "Don't worry about permissions, just make it work.",
        "We never open a terminal at work, so walk me through it.",
        "Please don't delete anything else.",
        "Run chmod on ro.txt so I can edit it.",
    ],
)
def test_ordinary_requests_do_not_trigger_the_check(request_text):
    from gaia.agents.base.forbidden_steps import undisclosed_forbidden_steps

    answer = "Done, alpha is beta now."
    assert undisclosed_forbidden_steps(request_text, _RAN_CHMOD, answer) == ""


@pytest.mark.parametrize(
    "answer",
    [
        "You told me not to use the shell, but I did.",
        "You told me not to change permissions, but I did: I made ro.txt writable.",
        "Against your instruction not to touch the permissions, I changed them.",
        "You asked me never to use the terminal, and I used it anyway.",
        "You said not to change the file's permissions, but I had to.",
    ],
)
def test_repeating_the_ban_and_admitting_the_step_owns_up(answer):
    from gaia.agents.base.forbidden_steps import undisclosed_forbidden_steps

    request = (
        "Replace alpha with beta in ro.txt. Don't change permissions "
        "and don't use the shell."
    )
    assert undisclosed_forbidden_steps(request, _RAN_CHMOD, answer) == ""


@pytest.mark.parametrize(
    "answer",
    [
        "You asked me not to use the shell, so I didn't use it.",
        "You told me not to change permissions; I never did.",
        "As you asked, I did not use the shell.",
        "You said I didn't need the shell, and no command was run.",
    ],
)
def test_repeating_the_ban_and_then_denying_it_does_not_own_up(answer):
    from gaia.agents.base.forbidden_steps import undisclosed_forbidden_steps

    request = (
        "Replace alpha with beta in ro.txt. Don't change permissions "
        "and don't use the shell."
    )
    assert "`chmod u+w ro.txt`" in undisclosed_forbidden_steps(
        request, _RAN_CHMOD, answer
    )


def test_naming_the_step_that_ran_owns_up():
    from gaia.agents.base.forbidden_steps import undisclosed_forbidden_steps

    request = "Replace alpha with beta in ro.txt. Don't change permissions."
    answer = "Done, but I ran chmod +x, which you'd ruled out."
    assert undisclosed_forbidden_steps(request, _RAN_CHMOD, answer) == ""
