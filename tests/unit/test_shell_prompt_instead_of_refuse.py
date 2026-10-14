# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A command refused only for lacking a skill grant asks the user (#5090).

A skill grant is pre-approval, so not having one means "ask", not "no". These
drive the real ``Agent._execute_tool`` through the real ``SSEOutputHandler`` —
the handler the TUI and Agent UI sit behind — against a throwaway git
repository, and read the prompt off the wire the way a front-end does.

What must not move: anything refused because the guard cannot inspect it, or
because it is dangerous or reaches outside the project, is refused before any
prompt; and with nobody to answer, a confirmable command is refused, never run.
"""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import threading
import time
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import DECLINED_HINT, Agent
from gaia.agents.base.console import SilentConsole
from gaia.agents.base.denied_effects import DeniedEffects
from gaia.agents.tools.shell_tools import (
    TIER_CONFIRM,
    TIER_REFUSE,
    ShellToolsMixin,
)
from gaia.skills.binaries import BinaryGrants
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.sse_translation import CanonicalTranslator

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="drives a real git repository"
)

#: Grant syntax a user cannot act on (#5086). Logs only.
GRANT_JARGON = ("SKILL.md", "shell:execute", "skill grant", "load_skill")


class _ShellAgent(Agent, ShellToolsMixin):
    """The flagship's shell tool on the base agent, and nothing else."""

    def _get_system_prompt(self):
        return "probe"

    def _register_tools(self):
        self.register_shell_tools()

    def _create_console(self):
        return self._console_override


def _agent(console, *granted: str) -> _ShellAgent:
    with patch("gaia.agents.base.agent.AgentSDK"):
        _ShellAgent._console_override = console
        agent = _ShellAgent(silent_mode=True, skip_lemonade=True)
    # One request's worth of calls, without the burst limit pacing the test.
    agent.max_commands_per_minute = 1000
    agent.max_commands_per_10_seconds = 1000
    agent._denied_effects = DeniedEffects(os.getcwd())
    if granted:
        agent._granted_binaries = BinaryGrants()
        for binary in granted:
            agent._granted_binaries.grant(binary, skill_name="coding")
    return agent


@pytest.fixture
def repo(tmp_path):
    """A git repository with one tracked file and two untracked ones."""
    run = lambda *a: subprocess.run(  # noqa: E731
        ["git", *a], cwd=tmp_path, check=True, capture_output=True
    )
    run("init", "-q")
    (tmp_path / "tracked.txt").write_text("needle\n")
    run("add", "tracked.txt")
    (tmp_path / "a.txt").write_text("a\n")
    (tmp_path / "b.txt").write_text("b\n")
    return tmp_path


def _staged(repo) -> set:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return set(out.split())


def _shell(command: str, repo) -> dict:
    return {"command": command, "working_directory": str(repo)}


def _drive(agent, handler, args, on_prompt=None, on_question=None):
    """Run one shell call on a worker; answer what it asks; return (result, events)."""
    outcome = {}

    def call():
        outcome["result"] = agent._execute_tool("run_shell_command", args)

    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    events = []
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            event = handler.event_queue.get(timeout=0.05)
        except queue.Empty:
            if not worker.is_alive():
                break
            continue
        events.append(event)
        if event.get("type") == "permission_request" and on_prompt is not None:
            on_prompt(event)
        if event.get("type") == "user_input_request" and on_question is not None:
            on_question(event)
    worker.join(timeout=5)
    assert not worker.is_alive(), "the call never finished"
    return outcome["result"], events


def _prompts(events) -> list:
    return [e for e in events if e.get("type") == "permission_request"]


def _answer(handler, *, approved=True, always=False):
    return lambda event: handler.resolve_tool_confirmation(
        approved=approved, always=always, confirm_id=event["confirm_id"]
    )


# ---------------------------------------------------------------------------
# A missing grant becomes a prompt
# ---------------------------------------------------------------------------


def test_a_grant_missing_command_asks_with_the_exact_command(repo):
    handler = SSEOutputHandler()
    agent = _agent(handler)

    result, events = _drive(
        agent, handler, _shell("git add a.txt", repo), on_prompt=_answer(handler)
    )

    [request] = _prompts(events)
    assert request["tool"] == "run_shell_command"
    assert request["args"]["command"] == "git add a.txt"
    assert request["risk"] == "write"
    # "Always" names the command family shown, never the whole shell.
    assert request["always_scope"] == "git add"
    assert request["confirm_id"]

    # The same prompt as a front-end receives it (TUI / canonical wire).
    [wire] = CanonicalTranslator(run_id="r", agent_id="gaia").translate(request)
    assert wire["type"] == "needs_confirmation"
    assert wire["action"] == "run_shell_command"
    assert wire["confirm_id"] == request["confirm_id"]
    assert wire["always_scope"] == "git add"
    assert wire["risk"] == "write"
    command_line, *rest = wire["summary"].splitlines()
    assert command_line == "git add a.txt"
    assert rest[0].startswith("in ")  # the folder it runs in
    assert rest[-1] == "Changes files or repository state where it runs."

    assert result["status"] == "success", result
    assert _staged(repo) == {"tracked.txt", "a.txt"}


def test_the_prompt_names_the_folder_a_session_cd_moved_to(repo):
    """With no working_directory the call runs where the last `cd` left the
    session, so the prompt has to say so — the arguments alone do not."""
    handler = SSEOutputHandler()
    agent = _agent(handler)
    moved, _ = _drive(
        agent, handler, {"command": f'cd "{repo}"'}, on_prompt=_answer(handler)
    )
    assert moved["status"] == "success", moved

    result, events = _drive(
        agent, handler, {"command": "git add a.txt"}, on_prompt=_answer(handler)
    )

    [request] = _prompts(events)
    assert os.path.samefile(request["args"]["working_directory"], repo)
    [wire] = CanonicalTranslator(run_id="r", agent_id="gaia").translate(request)
    assert wire["summary"].splitlines()[1].startswith("in ")
    assert result["status"] == "success", result
    assert _staged(repo) == {"tracked.txt", "a.txt"}


def test_approve_once_runs_it_once_and_the_next_call_asks_again(repo):
    handler = SSEOutputHandler()
    agent = _agent(handler)

    first, first_events = _drive(
        agent, handler, _shell("git add a.txt", repo), on_prompt=_answer(handler)
    )
    second, second_events = _drive(
        agent,
        handler,
        _shell("git add b.txt", repo),
        on_prompt=_answer(handler, approved=False),
    )

    assert first["status"] == "success"
    assert len(_prompts(first_events)) == 1
    # "Once" granted nothing: the same family is asked about again.
    [again] = _prompts(second_events)
    assert again["args"]["command"] == "git add b.txt"
    assert second["status"] == "denied"
    assert _staged(repo) == {"tracked.txt", "a.txt"}


def test_always_covers_only_the_family_that_was_shown(repo):
    handler = SSEOutputHandler()
    agent = _agent(handler)

    _drive(
        agent,
        handler,
        _shell("git add a.txt", repo),
        on_prompt=_answer(handler, always=True),
    )
    same_family, same_events = _drive(agent, handler, _shell("git add b.txt", repo))
    other_family, other_events = _drive(
        agent,
        handler,
        _shell("git commit -m wip", repo),
        on_prompt=_answer(handler, approved=False),
    )

    assert same_family["status"] == "success"
    assert _prompts(same_events) == []
    assert _staged(repo) == {"tracked.txt", "a.txt", "b.txt"}
    # `git add` was granted; `git commit` was never shown, so it still asks.
    [asked] = _prompts(other_events)
    assert asked["always_scope"] == "git commit"
    assert other_family["status"] == "denied"


def test_a_deny_is_final_for_the_turn_and_says_so(repo):
    handler = SSEOutputHandler()
    agent = _agent(handler)
    questions = []

    denied, _ = _drive(
        agent,
        handler,
        _shell("git add a.txt", repo),
        on_prompt=_answer(handler, approved=False),
    )

    def keep_refusing(event):
        questions.append(event)
        handler.resolve_user_input(event["request_id"], Agent.KEEP_DENIED_EFFECT)

    # The same effect, spelled another way, in the same turn.
    rephrased, events = _drive(
        agent, handler, _shell("git add .", repo), on_question=keep_refusing
    )

    assert denied["status"] == "denied"
    assert denied["hint"] == DECLINED_HINT
    assert "timed_out" not in denied
    # The rephrasing is not offered as a fresh prompt; it is held to the "no".
    assert _prompts(events) == []
    assert len(questions) == 1
    assert rephrased["status"] == "denied"
    assert rephrased["executed"] is False
    assert "git add a.txt" in rephrased["error"]
    assert _staged(repo) == {"tracked.txt"}


# ---------------------------------------------------------------------------
# Nobody can answer: fail closed
# ---------------------------------------------------------------------------


def test_no_interactive_client_refuses_and_never_runs(repo):
    """A console with no way to ask a person denies; it never answers for them."""
    agent = _agent(SilentConsole())

    result = agent._execute_tool("run_shell_command", _shell("git add a.txt", repo))

    assert result["status"] == "denied"
    assert "requires explicit user approval" in result["error"]
    assert _staged(repo) == {"tracked.txt"}


def test_a_background_run_refuses_without_raising_a_prompt(repo):
    handler = SSEOutputHandler(background_mode=True)
    agent = _agent(handler)

    result, events = _drive(agent, handler, _shell("git add a.txt", repo))

    assert result["status"] == "denied"
    assert _prompts(events) == []
    assert _staged(repo) == {"tracked.txt"}


def test_a_direct_call_with_no_console_refuses_in_plain_words(repo):
    """The execution path's own backstop, for a host nothing could prompt on."""
    from gaia.agents.base.tools import get_tool_metadata

    class _NoConsole(ShellToolsMixin):
        pass

    _NoConsole().register_shell_tools()
    run = get_tool_metadata("run_shell_command")["function"]

    result = run(command="git add a.txt", working_directory=str(repo))

    assert result["status"] == "error"
    assert result["tier"] == TIER_REFUSE
    assert result["executed"] is False
    assert result["error"].startswith("Not run: `git add a.txt` needs your approval")
    for jargon in GRANT_JARGON:
        assert jargon not in result["error"], jargon
    assert _staged(repo) == {"tracked.txt"}


def test_an_auto_approving_run_refuses_rather_than_widening(repo, monkeypatch):
    monkeypatch.setattr(
        "gaia.agents.base.console.auto_approve_env_enabled", lambda: True
    )
    handler = SSEOutputHandler()
    agent = _agent(handler)

    result, events = _drive(agent, handler, _shell("git add a.txt", repo))

    assert result["status"] == "error"
    assert result["executed"] is False
    assert _prompts(events) == []
    assert "needs your approval" in result["error"]
    assert "GAIA_AUTO_APPROVE_TOOLS" not in result["error"]
    assert _staged(repo) == {"tracked.txt"}


def test_a_prompt_nobody_answers_refuses(repo):
    handler = SSEOutputHandler()
    handler.confirm_timeout_seconds = 0.3
    agent = _agent(handler)

    result, events = _drive(agent, handler, _shell("git add a.txt", repo))

    assert len(_prompts(events)) == 1
    assert result["status"] == "denied"
    assert result["timed_out"] is True
    assert _staged(repo) == {"tracked.txt"}


def test_an_answer_to_a_different_prompt_is_not_consent(repo):
    handler = SSEOutputHandler()
    handler.confirm_timeout_seconds = 0.5
    agent = _agent(handler)
    delivered = []

    def answer_the_wrong_prompt(_event):
        delivered.append(
            handler.resolve_tool_confirmation(approved=True, confirm_id="stale-id")
        )

    result, _ = _drive(
        agent,
        handler,
        _shell("git add a.txt", repo),
        on_prompt=answer_the_wrong_prompt,
    )

    assert delivered == [False]
    assert result["status"] == "denied"
    assert result["timed_out"] is True
    assert _staged(repo) == {"tracked.txt"}


# ---------------------------------------------------------------------------
# Hard refusals stay hard: refused before the prompt, never asked about
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        # The guard cannot inspect what this would run.
        "powershell -EncodedCommand ZQBjAGgAbwA=",
        "powershell -Command \"[IO.File]::Delete('x')\"",
        "git -c core.pager=sh log",
        # Code in no file, or a path outside the project.
        'python -c "print(1)"',
        "python ../outside.py",
        # Refused even under a grant: publishes, or reaches the network.
        "git push",
        "git fetch origin",
        # Operators only a shell could act on.
        "ls $(whoami)",
    ],
)
def test_inspection_and_danger_refusals_never_prompt(repo, command):
    handler = SSEOutputHandler()
    agent = _agent(handler)

    result, events = _drive(agent, handler, _shell(command, repo))

    assert _prompts(events) == [], f"{command!r} reached the prompt"
    assert result["status"] == "error"
    assert result["executed"] is False
    assert result.get("tier", TIER_REFUSE) == TIER_REFUSE
    for jargon in ("SKILL.md", "skill grant"):
        assert jargon not in result["error"], jargon


# ---------------------------------------------------------------------------
# A skill grant is still pre-approval
# ---------------------------------------------------------------------------


def test_a_skill_grant_still_runs_its_reads_without_a_prompt(repo):
    granted = SSEOutputHandler()
    with_grant = _agent(granted, "git")
    result, events = _drive(with_grant, granted, _shell("git grep needle", repo))

    assert result["status"] == "success", result
    assert "needle" in result["stdout"]
    assert _prompts(events) == []


def test_the_same_read_without_the_grant_asks_first(repo):
    handler = SSEOutputHandler()
    agent = _agent(handler)

    result, events = _drive(
        agent, handler, _shell("git grep needle", repo), on_prompt=_answer(handler)
    )

    assert len(_prompts(events)) == 1
    assert result["status"] == "success", result


def test_the_guard_marks_a_missing_grant_as_confirmable_not_refused():
    error, _ = ShellToolsMixin()._validate_shell_command("git add a.txt")
    assert error["tier"] == TIER_CONFIRM
    for jargon in GRANT_JARGON:
        assert jargon not in error["error"] + error["hint"], jargon
