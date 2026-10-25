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
from gaia.agents.base.call_risk import RISK_NOTE_ARG
from gaia.agents.base.console import SilentConsole
from gaia.agents.base.denied_effects import DeniedEffects
from gaia.agents.tools.shell_tools import (
    NOTE_PUSH,
    NOTE_UNINSPECTABLE,
    TIER_CONFIRM,
    TIER_REFUSE,
    ShellToolsMixin,
    outside_note,
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


# ---------------------------------------------------------------------------
# Full access in a relayed Agent UI chat: gated commands are asked about
# ---------------------------------------------------------------------------


def _full_access_chat(*, watched: bool = True) -> SSEOutputHandler:
    """The handler a sidecar ``/query`` builds for a Full access chat.

    Over the socket Full access pre-approves prompts but keeps the shell gates
    on (#3373); ``watched`` is the caller declaring a person can answer.
    """
    stdio = pytest.importorskip("gaia_agent.stdio")
    handler = SSEOutputHandler()
    stdio.PermissionState(full_access=True, lifts_shell_gates=False).attach(handler)
    handler.answers_confirmations = watched
    assert handler.auto_approve_gated_tools and not handler.full_access
    return handler


def test_a_full_access_chat_asks_before_a_gated_command_and_runs_it_on_allow(repo):
    handler = _full_access_chat()
    agent = _agent(handler)

    result, events = _drive(
        agent, handler, _shell("git add a.txt", repo), on_prompt=_answer(handler)
    )

    [request] = _prompts(events)
    assert request["args"] == _shell("git add a.txt", repo)  # no marker on the wire
    [wire] = CanonicalTranslator(run_id="r", agent_id="gaia").translate(request)
    assert wire["type"] == "needs_confirmation"
    assert wire["summary"].splitlines()[0] == "git add a.txt"
    assert result["status"] == "success", result
    assert _staged(repo) == {"tracked.txt", "a.txt"}


def test_a_full_access_chat_runs_nothing_the_person_denies(repo):
    handler = _full_access_chat()
    agent = _agent(handler)

    result, events = _drive(
        agent,
        handler,
        _shell("git add a.txt", repo),
        on_prompt=_answer(handler, approved=False),
    )

    assert len(_prompts(events)) == 1
    assert result["status"] == "denied"
    assert _staged(repo) == {"tracked.txt"}


def test_a_full_access_chat_asks_once_for_a_one_time_line(repo):
    python = _python()
    command = f"{python} -c \"open('ran.txt', 'a').write('x')\""
    handler = _full_access_chat()
    agent = _agent(handler)

    first, first_events = _drive(
        agent, handler, _shell(command, repo), on_prompt=_answer(handler)
    )
    second, second_events = _drive(
        agent,
        handler,
        _shell(command, repo),
        on_prompt=_answer(handler, approved=False),
    )

    [request] = _prompts(first_events)
    assert request["args"][RISK_NOTE_ARG] == NOTE_UNINSPECTABLE
    assert first["approved_once"] is True
    assert len(_prompts(second_events)) == 1
    assert second["status"] == "denied"
    assert (repo / "ran.txt").read_text() == "x"


def test_a_full_access_chat_runs_a_safe_command_unasked(repo):
    handler = _full_access_chat()
    agent = _agent(handler)

    result, events = _drive(agent, handler, _shell("git status", repo))

    assert _prompts(events) == []
    assert result["status"] == "success", result


def test_an_unwatched_full_access_run_still_refuses_unasked(repo):
    handler = _full_access_chat(watched=False)
    agent = _agent(handler)

    result, events = _drive(agent, handler, _shell("git add a.txt", repo))

    assert _prompts(events) == []
    assert result["status"] == "error"
    assert result["executed"] is False
    assert "needs your approval" in result["error"]
    assert _staged(repo) == {"tracked.txt"}


def test_the_auto_approve_env_still_refuses_even_on_a_watched_chat(repo, monkeypatch):
    monkeypatch.setattr(
        "gaia.agents.base.console.auto_approve_env_enabled", lambda: True
    )
    handler = _full_access_chat()
    agent = _agent(handler)

    result, events = _drive(agent, handler, _shell("git add a.txt", repo))

    assert _prompts(events) == []
    assert result["status"] == "error"
    assert result["executed"] is False
    assert _staged(repo) == {"tracked.txt"}


def test_a_direct_call_on_a_full_access_chat_never_runs_a_gated_command(repo):
    """The execution path still refuses a call no prompt stood behind."""
    from gaia.agents.base.tools import get_tool_metadata

    handler = _full_access_chat()
    _agent(handler)
    run = get_tool_metadata("run_shell_command")["function"]

    result = run(command="git add a.txt", working_directory=str(repo))

    assert result["status"] == "error"
    assert result["executed"] is False
    assert handler.event_queue.empty()
    assert _staged(repo) == {"tracked.txt"}


def test_the_model_cannot_pass_the_needs_person_marker(repo):
    from gaia.agents.base.call_risk import NEEDS_PERSON_ARG

    handler = _full_access_chat()
    agent = _agent(handler)

    result = agent._execute_tool(
        "run_shell_command", {**_shell("git add a.txt", repo), NEEDS_PERSON_ARG: True}
    )

    assert result["error_type"] == "invalid_arguments"
    assert _staged(repo) == {"tracked.txt"}


def test_a_marked_call_on_an_unwatched_handler_is_denied_not_approved():
    from gaia.agents.base.call_risk import NEEDS_PERSON_ARG

    handler = SSEOutputHandler()
    handler.auto_approve_gated_tools = True

    approved = handler.confirm_tool_execution(
        "run_shell_command", {"command": "git add a.txt", NEEDS_PERSON_ARG: True}
    )

    assert approved is False
    assert handler.event_queue.empty()


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
# What GAIA's checks would refuse is asked about once, never "always"
# ---------------------------------------------------------------------------


def _python() -> str:
    for name in ("python", "python3"):
        if shutil.which(name):
            return name
    pytest.skip("needs a python on PATH")


ONCE_CASES = [
    # (command, the risk note the prompt must carry)
    ("git push", NOTE_PUSH),
    ("git fetch origin", NOTE_UNINSPECTABLE),
    ("git worktree add wt", NOTE_UNINSPECTABLE),
    ("git -c core.pager=cat log", NOTE_UNINSPECTABLE),
    ('python -c "print(1)"', NOTE_UNINSPECTABLE),
    ("python ../outside.py", outside_note("../outside.py")),
    ("powershell -Command \"[IO.File]::Exists('x')\"", NOTE_UNINSPECTABLE),
    ("echo $(whoami)", NOTE_UNINSPECTABLE),
]


@pytest.mark.parametrize("command,note", ONCE_CASES)
def test_a_policy_refusal_becomes_a_one_time_prompt(repo, command, note):
    if command.startswith("python "):
        # A missing interpreter is refused as not installed, before any policy.
        command = _python() + command[len("python") :]
    handler = SSEOutputHandler()
    agent = _agent(handler)

    result, events = _drive(
        agent,
        handler,
        _shell(command, repo),
        on_prompt=_answer(handler, approved=False),
    )

    [request] = _prompts(events)
    assert request["args"]["command"] == command
    assert request["args"][RISK_NOTE_ARG] == note
    assert os.path.samefile(request["args"]["working_directory"], repo)
    # Approved once or not at all: "always" is never on offer.
    assert "always_scope" not in request
    assert request["risk"] in ("write", "execute", "destructive")

    [wire] = CanonicalTranslator(run_id="r", agent_id="gaia").translate(request)
    assert wire["type"] == "needs_confirmation"
    assert "always_scope" not in wire
    shown, folder, risk_line = wire["summary"].split("\n")
    assert shown == command  # byte for byte what the shell receives
    assert folder == f"in {request['args']['working_directory']}"
    assert risk_line == note

    assert result["status"] == "denied"
    assert result["hint"] == DECLINED_HINT


def test_an_outside_path_is_named_and_approved_before_the_run(repo, monkeypatch):
    """The prompt names the outside path, and the run does not refuse it after."""
    from gaia.security import PathValidator

    monkeypatch.setattr("gaia.security._system_temp_roots", lambda: set())
    project = repo / "project"
    project.mkdir()
    (repo / "notes.txt").write_text("outside\n")
    handler = SSEOutputHandler()
    agent = _agent(handler)
    agent.path_validator = PathValidator(allowed_paths=[str(project)])
    target = str((repo / "notes.txt").resolve())

    result, events = _drive(
        agent,
        handler,
        {"command": f'cat "{target}"', "working_directory": str(project)},
        on_prompt=_answer(handler),
    )

    [request] = _prompts(events)
    assert request["args"][RISK_NOTE_ARG] == outside_note(target)
    assert "always_scope" not in request
    assert result["status"] == "success", result
    assert "outside" in result["stdout"]
    assert result["approved_once"] is True


def test_approve_once_runs_the_exact_line_once(repo):
    python = _python()
    command = f"{python} -c \"open('ran.txt', 'a').write('x')\""
    handler = SSEOutputHandler()
    agent = _agent(handler)

    first, first_events = _drive(
        agent, handler, _shell(command, repo), on_prompt=_answer(handler)
    )
    second, second_events = _drive(
        agent,
        handler,
        _shell(command, repo),
        on_prompt=_answer(handler, approved=False),
    )

    assert first["status"] == "success", first
    assert first["approved_once"] is True
    assert first["steps"] == [{"command": command, "return_code": 0}]
    assert len(_prompts(first_events)) == 1
    # The approval was spent: the same line is asked about again.
    assert len(_prompts(second_events)) == 1
    assert second["status"] == "denied"
    assert (repo / "ran.txt").read_text() == "x"


def test_an_always_answer_to_a_one_time_prompt_grants_nothing(repo):
    """A client that sends "always" anyway gets one run, not a standing grant."""
    python = _python()
    command = f"{python} -c \"open('ran.txt', 'a').write('x')\""
    handler = SSEOutputHandler()
    agent = _agent(handler)

    _drive(
        agent, handler, _shell(command, repo), on_prompt=_answer(handler, always=True)
    )
    again, events = _drive(
        agent,
        handler,
        _shell(command, repo),
        on_prompt=_answer(handler, approved=False),
    )

    assert len(_prompts(events)) == 1
    assert again["status"] == "denied"
    assert handler.session_grants() == set()
    assert (repo / "ran.txt").read_text() == "x"


def test_a_one_time_line_fails_closed_when_nobody_can_answer(repo, monkeypatch):
    python = _python()
    command = f"{python} -c \"open('ran.txt', 'a').write('x')\""

    silent = _agent(SilentConsole())
    assert (
        silent._execute_tool("run_shell_command", _shell(command, repo))["status"]
        == "denied"
    )

    background = SSEOutputHandler(background_mode=True)
    result, events = _drive(_agent(background), background, _shell(command, repo))
    assert result["status"] == "denied" and _prompts(events) == []

    timed = SSEOutputHandler()
    timed.confirm_timeout_seconds = 0.3
    result, _ = _drive(_agent(timed), timed, _shell(command, repo))
    assert result["timed_out"] is True

    monkeypatch.setattr(
        "gaia.agents.base.console.auto_approve_env_enabled", lambda: True
    )
    auto = SSEOutputHandler()
    result, events = _drive(_agent(auto), auto, _shell(command, repo))
    assert result["status"] == "error" and _prompts(events) == []
    assert "needs your approval" in result["error"]

    assert not (repo / "ran.txt").exists()


def test_a_direct_call_never_runs_a_one_time_line_unapproved(repo):
    """Reaching the tool without the prompt — no console, or no approval — refuses."""
    from gaia.agents.base.tools import get_tool_metadata

    handler = SSEOutputHandler()
    agent = _agent(handler)
    run = get_tool_metadata("run_shell_command")["function"]

    result = run(command="git push", working_directory=str(repo))

    assert result["status"] == "error"
    assert result["executed"] is False
    assert "needs your approval" in result["error"]


def test_a_one_time_line_cannot_be_polled(repo):
    handler = SSEOutputHandler()
    agent = _agent(handler)

    result = agent._execute_tool(
        "wait_for_condition", {"command": "git fetch origin", "timeout": 5}
    )

    assert result["status"] == "error"
    assert result["executed"] is False
    assert "can't be polled" in result["error"]
    assert handler.event_queue.empty()


# ---------------------------------------------------------------------------
# Hard refusals that stay hard: never asked about
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        # What runs could not be shown: an encoded script, malformed quoting.
        "powershell -EncodedCommand ZQBjAGgAbwA=",
        'echo "unterminated',
        # Destroys work, so it is never offered.
        "git push --force",
        "git reset --hard",
        "git clean -fd",
        "git push origin +main",
        "git push origin :main",
        "git push -fu origin main",
        "git push --mirror",
        "gh repo delete amd/gaia",
        # Behind an option or inside another command's text, still never offered.
        "git -c core.pager=cat reset --hard",
        "echo $(git reset --hard)",
        "cmd /c 'x & powershell -enc ZQBjAGgAbwA='",
        # Text that would not read on screen the way it runs.
        "git fetch origin\u202e",
        # The strictest segment decides the whole line.
        "git add a.txt && powershell -EncodedCommand ZQBjAGgAbwA=",
    ],
)
def test_the_remaining_hard_refusals_never_prompt(repo, command):
    handler = SSEOutputHandler()
    agent = _agent(handler)

    result, events = _drive(agent, handler, _shell(command, repo))

    assert _prompts(events) == [], f"{command!r} reached the prompt"
    assert result["status"] == "error"
    assert result["executed"] is False
    assert result.get("tier", TIER_REFUSE) == TIER_REFUSE
    assert _staged(repo) == {"tracked.txt"}


def test_a_delete_outside_the_project_is_never_offered(repo, monkeypatch):
    from gaia.security import PathValidator

    monkeypatch.setattr("gaia.security._system_temp_roots", lambda: set())
    project = repo / "project"
    project.mkdir()
    (repo / "sibling").mkdir()
    handler = SSEOutputHandler()
    agent = _agent(handler)
    agent.path_validator = PathValidator(allowed_paths=[str(project)])

    result, events = _drive(
        agent,
        handler,
        {"command": "rm -rf ../sibling", "working_directory": str(project)},
    )

    assert _prompts(events) == []
    assert result["tier"] == TIER_REFUSE
    assert (repo / "sibling").is_dir()


def test_a_granted_remote_cli_still_cannot_reach_a_credential_folder(repo):
    """A grant exempts a remote CLI's operands from the path scan; a one-time
    line runs as raw shell text, so it is scanned anyway."""
    from gaia.security import SECRET_DIRECTORIES, PathValidator

    handler = SSEOutputHandler()
    agent = _agent(handler, "gh")
    agent.path_validator = PathValidator(allowed_paths=[str(repo)])
    secret = os.path.join(sorted(SECRET_DIRECTORIES)[0], "id_rsa")

    result, events = _drive(
        agent,
        handler,
        _shell(f'gh issue create --title x --body-file "{secret}"', repo),
    )

    assert _prompts(events) == []
    assert result["tier"] == TIER_REFUSE


def test_allow_once_on_the_reroute_question_still_shows_the_one_time_prompt(repo):
    """The reroute question carries no risk note, so a one-time line is put to
    its own prompt as well — and runs only if that is approved."""
    handler = SSEOutputHandler()
    agent = _agent(handler)
    _drive(
        agent,
        handler,
        _shell("git fetch origin", repo),
        on_prompt=_answer(handler, approved=False),
    )

    def allow(event):
        handler.resolve_user_input(event["request_id"], Agent.ALLOW_DENIED_EFFECT)

    result, events = _drive(
        agent,
        handler,
        _shell("git fetch origin main", repo),
        on_prompt=_answer(handler, approved=False),
        on_question=allow,
    )

    [request] = _prompts(events)
    assert request["args"][RISK_NOTE_ARG] == NOTE_UNINSPECTABLE
    assert result["status"] == "denied"


def test_an_approval_left_by_a_failed_call_is_never_reused(repo):
    """An approval is spent by the call it was given for, even one that never
    reached the shell; the next call asks again."""
    python = _python()
    command = f"{python} -c \"open('ran.txt', 'a').write('x')\""
    handler = SSEOutputHandler()
    agent = _agent(handler)

    failed, _ = _drive(
        agent,
        handler,
        {**_shell(command, repo), "timeout": "soon"},
        on_prompt=_answer(handler),
    )
    again, events = _drive(
        agent,
        handler,
        _shell(command, repo),
        on_prompt=_answer(handler, approved=False),
    )

    assert failed["status"] == "error"
    assert len(_prompts(events)) == 1
    assert again["status"] == "denied"
    assert not (repo / "ran.txt").exists()


def test_the_terminal_prompt_shows_a_long_one_time_line_whole():
    from gaia.agents.base.console import format_confirmation_args

    command = "echo " + "x" * 2000
    shown = format_confirmation_args({"command": command, RISK_NOTE_ARG: "note"})
    assert command in shown
    # An ordinary call still gets the head+tail shortening.
    assert "chars]" in format_confirmation_args({"command": command})


def test_a_credential_folder_is_never_offered(repo, tmp_path):
    from gaia.security import SECRET_DIRECTORIES, PathValidator

    handler = SSEOutputHandler()
    agent = _agent(handler)
    agent.path_validator = PathValidator(allowed_paths=[str(repo)])
    secret = os.path.join(sorted(SECRET_DIRECTORIES)[0], "id_rsa")

    result, events = _drive(agent, handler, _shell(f'cat "{secret}"', repo))

    assert _prompts(events) == []
    assert result["status"] == "error"
    assert result["tier"] == TIER_REFUSE


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


def test_the_same_read_without_the_grant_runs_unasked(repo):
    handler = SSEOutputHandler()
    agent = _agent(handler)

    result, events = _drive(
        agent, handler, _shell("git grep needle", repo), on_prompt=_answer(handler)
    )

    assert _prompts(events) == []
    assert result["status"] == "success", result


def test_the_guard_marks_a_missing_grant_as_confirmable_not_refused():
    error, _ = ShellToolsMixin()._validate_shell_command("git add a.txt")
    assert error["tier"] == TIER_CONFIRM
    for jargon in GRANT_JARGON:
        assert jargon not in error["error"] + error["hint"], jargon
