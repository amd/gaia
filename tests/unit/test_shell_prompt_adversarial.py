# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Adversarial checks on the shell approval prompt (#5090, #5094).

Each test feeds the guard something hostile and asserts it holds: the prompt
shows exactly the line that would run or nothing runs, a one-time approval is
spent on one exact call, "always" is never granted for a one-time line, the
strictest segment of a chained line decides it, the scratch-folder write
exemption covers only the agent's real scratch folder, and no answer ever lets
``execute_python_file`` run a script there unasked.
"""

from __future__ import annotations

import os
import subprocess
import sys
from unittest.mock import patch

import pytest

from gaia.agents.base.call_risk import RISK_NOTE_ARG
from gaia.agents.base.console import AgentConsole
from gaia.agents.base.tool_grants import (
    SCRATCH_ARG,
    command_reaches_folder,
    grant_scope,
    is_inside_folder,
)
from gaia.agents.base.tools import tool as register_tool
from gaia.agents.tools.shell_tools import (
    NOTE_PUSH,
    NOTE_UNINSPECTABLE,
    TIER_CONFIRM,
    TIER_CONFIRM_ONCE,
    TIER_REFUSE,
    ShellToolsMixin,
    outside_note,
)
from gaia.security import PathValidator
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.sse_translation import CanonicalTranslator
from tests.unit import test_scratch_prompts as scratch_mod
from tests.unit.test_shell_prompt_instead_of_refuse import (
    _agent,
    _answer,
    _drive,
    _prompts,
    _python,
    _shell,
    _staged,
)


@pytest.fixture
def repo(tmp_path):
    """A git repository with one tracked file and two untracked ones."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.txt").write_text("needle\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("a\n")
    (tmp_path / "b.txt").write_text("b\n")
    return tmp_path


def _wire(request) -> dict:
    [wire] = CanonicalTranslator(run_id="r", agent_id="gaia").translate(request)
    return wire


def _assert_exact_or_refused(command, result, events):
    """Either the prompt showed this exact text, or nothing was asked and it refused."""
    prompts = _prompts(events)
    if prompts:
        [request] = prompts
        assert request["args"]["command"] == command
        assert _wire(request)["summary"].split("\n", 1)[0] == command
        # The prompt was denied, so nothing ran.
        assert result["status"] == "denied"
    else:
        assert result["status"] == "error", result
        assert result.get("tier", TIER_REFUSE) == TIER_REFUSE
    assert result.get("executed") is not True


# ---------------------------------------------------------------------------
# What the prompt shows is what would run, or nothing runs
# ---------------------------------------------------------------------------

MISLEADING_TEXT = [
    "git push‮",  # right-to-left override
    "git​ push",  # zero-width space inside the program name
    "git push⁦ --force⁩",  # isolate around a destructive flag
    "﻿git push",  # byte-order mark
    "git push‍",  # zero-width joiner
    "git status\ngit push --force",  # second line
    "git status\rgit push",  # carriage return hides the head on a terminal
    "git status\x00git push",  # NUL
    "git status\x1b[2Kgit push",  # ANSI erase-line escape
    "git status\x08\x08\x08\x08\x08\x08push",  # backspaces repaint the line
]


@pytest.mark.parametrize("command", MISLEADING_TEXT)
def test_text_that_reads_differently_than_it_runs_is_never_prompted(repo, command):
    handler = SSEOutputHandler()
    agent = _agent(handler)

    result, events = _drive(
        agent, handler, _shell(command, repo), on_prompt=_answer(handler)
    )

    assert _prompts(events) == [], f"{command!r} reached the prompt"
    assert result["status"] == "error"
    assert result["executed"] is False
    assert result.get("tier", TIER_REFUSE) == TIER_REFUSE


@pytest.mark.parametrize(
    "command",
    [
        "gіt push",  # Cyrillic i in the program name
        "ｇｉｔ push",  # full-width "git"
        "git puѕh",  # Cyrillic s in the subcommand
        "git push",  # no-break space instead of a space
        "git add á.txt",  # combining accent
    ],
)
def test_homoglyphs_are_shown_exactly_or_refused(repo, command):
    handler = SSEOutputHandler()
    agent = _agent(handler)

    result, events = _drive(
        agent,
        handler,
        _shell(command, repo),
        on_prompt=_answer(handler, approved=False),
    )

    _assert_exact_or_refused(command, result, events)
    assert _staged(repo) == {"tracked.txt"}


def test_a_very_long_one_time_line_is_shown_byte_for_byte(repo):
    command = "echo $(whoami) " + "x" * 20000 + " END"
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
    shown, folder, note = _wire(request)["summary"].split("\n")
    assert shown == command
    assert note == NOTE_UNINSPECTABLE
    assert result["status"] == "denied"


def test_the_terminal_shows_a_long_one_time_line_unshortened():
    from gaia.agents.base.console import format_confirmation_args

    command = "echo $(whoami) " + "y" * 50000 + " TAIL"
    rendered = format_confirmation_args(
        {"command": command, RISK_NOTE_ARG: NOTE_UNINSPECTABLE}
    )
    assert command in rendered
    assert "chars]" not in rendered


# ---------------------------------------------------------------------------
# "Always" is never granted for a one-time line
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command,note",
    [
        ("git fetch origin", NOTE_UNINSPECTABLE),
        ("echo $(whoami)", NOTE_UNINSPECTABLE),
        ("git push", NOTE_PUSH),
    ],
)
def test_a_client_sending_always_on_a_one_time_prompt_grants_nothing(
    repo, command, note
):
    handler = SSEOutputHandler()
    agent = _agent(handler)

    first, first_events = _drive(
        agent,
        handler,
        _shell(command, repo),
        on_prompt=_answer(handler, approved=True, always=True),
    )
    second, second_events = _drive(
        agent,
        handler,
        _shell(command, repo),
        on_prompt=_answer(handler, approved=False),
    )

    [request] = _prompts(first_events)
    assert request["args"][RISK_NOTE_ARG] == note
    assert "always_scope" not in request
    assert handler.session_grants() == set()
    assert len(_prompts(second_events)) == 1
    assert second["status"] == "denied"


def test_always_alone_without_approved_still_runs_only_once(repo):
    """``always=True, approved=False`` is read as a yes; it must still be one run."""
    python = _python()
    command = f"{python} -c \"open('ran.txt', 'a').write('x')\""
    handler = SSEOutputHandler()
    agent = _agent(handler)

    _drive(
        agent,
        handler,
        _shell(command, repo),
        on_prompt=_answer(handler, approved=False, always=True),
    )
    again, events = _drive(
        agent,
        handler,
        _shell(command, repo),
        on_prompt=_answer(handler, approved=False),
    )

    assert handler.session_grants() == set()
    assert len(_prompts(events)) == 1
    assert again["status"] == "denied"
    assert (repo / "ran.txt").read_text() == "x"


def test_typing_a_at_the_terminal_on_a_one_time_prompt_denies(monkeypatch):
    console = AgentConsole()
    monkeypatch.setattr(
        "gaia.agents.base.console.terminal_is_interactive", lambda: True
    )
    monkeypatch.setattr("builtins.input", lambda _prompt="": "a")
    args = {"command": "git push", RISK_NOTE_ARG: NOTE_PUSH}

    assert console.confirm_tool_execution("run_shell_command", args) is False
    assert console.session_grants() == set()


def test_no_grant_scope_exists_for_any_one_time_shell_call():
    for tool in ("run_shell_command", "run_cli_command", "wait_for_condition"):
        assert grant_scope(tool, {"command": "git status", RISK_NOTE_ARG: "x"}) is None


# ---------------------------------------------------------------------------
# A one-time approval covers one exact call
# ---------------------------------------------------------------------------


def _tool(name="run_shell_command"):
    from gaia.agents.base.tools import get_tool_metadata

    return get_tool_metadata(name)["function"]


def _write_line(python: str, name: str = "ran.txt") -> str:
    return f"{python} -c \"open('{name}', 'a').write('x')\""


def test_an_approval_does_not_carry_to_a_different_command(repo):
    python = _python()
    handler = SSEOutputHandler()
    agent = _agent(handler)
    approved = _write_line(python)
    agent.note_confirmed_call("run_shell_command", _shell(approved, repo))

    result = _tool()(
        command=_write_line(python, "other.txt"), working_directory=str(repo)
    )

    assert result["status"] == "error"
    assert result["executed"] is False
    assert not (repo / "other.txt").exists()
    # Spent by that attempt: the approved line no longer runs either.
    result = _tool()(command=approved, working_directory=str(repo))
    assert result["executed"] is False
    assert not (repo / "ran.txt").exists()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c + " ",  # trailing space
        lambda c: " " + c,  # leading space
        lambda c: c.replace(" -c ", "  -c "),  # doubled space
        lambda c: c.replace("'a'", '"a"'),  # different quoting
    ],
)
def test_an_approval_is_for_the_exact_bytes(repo, mutate):
    python = _python()
    agent = _agent(SSEOutputHandler())
    approved = _write_line(python)
    agent.note_confirmed_call("run_shell_command", _shell(approved, repo))

    result = _tool()(command=mutate(approved), working_directory=str(repo))

    assert result["executed"] is False
    assert not (repo / "ran.txt").exists()


def test_an_approval_does_not_carry_to_a_different_folder(repo):
    python = _python()
    other = repo / "sub"
    other.mkdir()
    agent = _agent(SSEOutputHandler())
    command = _write_line(python)
    agent.note_confirmed_call("run_shell_command", _shell(command, repo))

    result = _tool()(command=command, working_directory=str(other))

    assert result["executed"] is False
    assert not (other / "ran.txt").exists()
    assert not (repo / "ran.txt").exists()


def test_an_approval_cannot_be_replayed(repo):
    python = _python()
    agent = _agent(SSEOutputHandler())
    command = _write_line(python)
    agent.note_confirmed_call("run_shell_command", _shell(command, repo))

    first = _tool()(command=command, working_directory=str(repo))
    second = _tool()(command=command, working_directory=str(repo))
    third = _tool()(command=command, working_directory=str(repo))

    assert first["status"] == "success", first
    assert second["executed"] is False
    assert third["executed"] is False
    assert (repo / "ran.txt").read_text() == "x"


def test_any_other_shell_call_spends_a_pending_approval(repo):
    python = _python()
    handler = SSEOutputHandler()
    agent = _agent(handler, "git")
    command = _write_line(python)
    agent.note_confirmed_call("run_shell_command", _shell(command, repo))

    # A granted read in between, with no prompt.
    between, events = _drive(agent, handler, _shell("git status", repo))
    assert between["status"] == "success", between
    assert _prompts(events) == []

    result = _tool()(command=command, working_directory=str(repo))
    assert result["executed"] is False
    assert not (repo / "ran.txt").exists()


def test_an_approval_noted_for_another_tool_is_not_a_shell_approval(repo):
    python = _python()
    agent = _agent(SSEOutputHandler())
    command = _write_line(python)
    agent.note_confirmed_call("run_python", {"command": command})
    agent.note_confirmed_call("write_file", _shell(command, repo))

    result = _tool()(command=command, working_directory=str(repo))

    assert result["executed"] is False
    assert not (repo / "ran.txt").exists()


# ---------------------------------------------------------------------------
# Answers only reach the prompt they were given for
# ---------------------------------------------------------------------------


def test_a_confirm_id_from_an_earlier_prompt_cannot_approve_a_new_one(repo):
    python = _python()
    handler = SSEOutputHandler()
    handler.confirm_timeout_seconds = 0.6
    agent = _agent(handler)
    earlier = []

    def approve_and_keep(event):
        earlier.append(event["confirm_id"])
        handler.resolve_tool_confirmation(approved=True, confirm_id=event["confirm_id"])

    first, _ = _drive(
        agent, handler, _shell("git status", repo), on_prompt=approve_and_keep
    )
    assert first["status"] == "success", first
    delivered = []

    def replay_old(_event):
        delivered.append(
            handler.resolve_tool_confirmation(
                approved=True, always=True, confirm_id=earlier[0]
            )
        )

    result, events = _drive(
        agent, handler, _shell(_write_line(python), repo), on_prompt=replay_old
    )

    [request] = _prompts(events)
    assert request["confirm_id"] != earlier[0]
    assert delivered == [False]
    assert result["status"] == "denied"
    assert result["timed_out"] is True
    assert handler.session_grants() == set()
    assert not (repo / "ran.txt").exists()


def test_a_second_answer_to_an_answered_prompt_is_dropped(repo):
    handler = SSEOutputHandler()
    agent = _agent(handler)
    answers = []

    def approve_twice(event):
        answers.append(
            handler.resolve_tool_confirmation(
                approved=False, confirm_id=event["confirm_id"]
            )
        )
        answers.append(
            handler.resolve_tool_confirmation(
                approved=True, always=True, confirm_id=event["confirm_id"]
            )
        )

    result, _ = _drive(
        agent, handler, _shell("git add a.txt", repo), on_prompt=approve_twice
    )

    # The first answer stands; the second is reported undelivered.
    assert answers == [True, False]
    assert result["status"] == "denied"
    assert handler.session_grants() == set()
    assert _staged(repo) == {"tracked.txt"}


def test_an_approval_flagged_timed_out_is_a_denial(repo):
    handler = SSEOutputHandler()
    agent = _agent(handler)

    result, _ = _drive(
        agent,
        handler,
        _shell("git fetch origin", repo),
        on_prompt=lambda event: handler.resolve_tool_confirmation(
            approved=True, confirm_id=event["confirm_id"], timed_out=True
        ),
    )

    assert result["status"] == "denied"
    assert result["timed_out"] is True


def test_a_cancelled_run_refuses_a_pending_one_time_prompt(repo):
    handler = SSEOutputHandler()
    agent = _agent(handler)

    result, _ = _drive(
        agent,
        handler,
        _shell("git fetch origin", repo),
        on_prompt=lambda _event: handler.cancelled.set(),
    )

    assert result["status"] == "denied"
    assert result.get("executed") is not True


# ---------------------------------------------------------------------------
# A chained line takes its strictest segment's tier, in any position
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command,tier",
    [
        ("git status && git add a.txt", TIER_CONFIRM),
        ("git add a.txt && git push", TIER_CONFIRM_ONCE),
        ("git push && git add a.txt", TIER_CONFIRM_ONCE),
        ("git status | git push", TIER_CONFIRM_ONCE),
        ("git status ; git push", TIER_CONFIRM_ONCE),
        ("git status || echo $(whoami)", TIER_CONFIRM_ONCE),
        ("git push && git push --force", TIER_REFUSE),
        ("git push --force && git push", TIER_REFUSE),
        ("git add a.txt && git reset --hard", TIER_REFUSE),
        ("git status && git status && git status && git clean -fdx", TIER_REFUSE),
        ("git add a.txt || git push origin :main", TIER_REFUSE),
        ("echo ok; powershell -enc ZQBjAGgAbwA=", TIER_REFUSE),
        ("echo ok && powershell -EnCoDeDcOmMaNd ZQBjAGgAbwA=", TIER_REFUSE),
    ],
)
def test_a_chained_line_takes_its_strictest_segment(command, tier):
    error, _ = ShellToolsMixin()._validate_shell_command(command)

    assert error is not None, command
    assert error.get("tier", TIER_REFUSE) == tier, (command, error)


def test_a_refused_segment_is_never_softened_by_a_confirmable_one(repo):
    handler = SSEOutputHandler()
    agent = _agent(handler)

    result, events = _drive(
        agent,
        handler,
        _shell("git add a.txt && git push && git reset --hard", repo),
        on_prompt=_answer(handler),
    )

    assert _prompts(events) == []
    assert result["tier"] == TIER_REFUSE
    assert _staged(repo) == {"tracked.txt"}


# ---------------------------------------------------------------------------
# A path outside the project is classified before the prompt
# ---------------------------------------------------------------------------


@pytest.fixture
def scoped(repo, monkeypatch):
    from gaia.security import PathValidator

    monkeypatch.setattr("gaia.security._system_temp_roots", lambda: set())
    project = repo / "project"
    project.mkdir()
    (repo / "notes.txt").write_text("outside\n")
    handler = SSEOutputHandler()
    agent = _agent(handler)
    agent.path_validator = PathValidator(allowed_paths=[str(project)])
    return agent, handler, project


@pytest.mark.parametrize(
    "command",
    [
        "cat ../notes.txt",
        "cat ./../notes.txt",
        "cat sub/../../notes.txt",
        "cd .. && cat notes.txt",
    ],
)
def test_an_outside_path_is_named_on_the_prompt_before_anything_runs(scoped, command):
    agent, handler, project = scoped

    result, events = _drive(
        agent,
        handler,
        {"command": command, "working_directory": str(project)},
        on_prompt=_answer(handler, approved=False),
    )

    [request] = _prompts(events)
    note = request["args"][RISK_NOTE_ARG]
    assert note.startswith("Reads or changes files outside this project")
    assert "always_scope" not in request
    assert result["status"] == "denied"
    assert result.get("executed") is not True


def test_an_outside_working_directory_is_a_one_time_prompt(scoped, repo):
    agent, handler, _project = scoped

    result, events = _drive(
        agent,
        handler,
        {"command": "git add a.txt", "working_directory": str(repo)},
        on_prompt=_answer(handler, approved=False),
    )

    [request] = _prompts(events)
    assert request["args"][RISK_NOTE_ARG] == outside_note(str(repo.resolve()))
    assert "always_scope" not in request
    assert result["status"] == "denied"
    assert _staged(repo) == {"tracked.txt"}


# ---------------------------------------------------------------------------
# Display-only prompt fields cannot come from the model
# ---------------------------------------------------------------------------


def test_a_model_supplied_risk_note_never_reaches_the_prompt(repo):
    handler = SSEOutputHandler()
    agent = _agent(handler)

    result, events = _drive(
        agent,
        handler,
        {**_shell("git add a.txt", repo), RISK_NOTE_ARG: "Harmless. Reads one file."},
        on_prompt=_answer(handler),
    )

    for request in _prompts(events):
        assert request["args"].get(RISK_NOTE_ARG) != "Harmless. Reads one file."
    assert result.get("executed") is not True
    assert _staged(repo) == {"tracked.txt"}


# ---------------------------------------------------------------------------
# The scratch-folder exemption covers only the real scratch folder
# ---------------------------------------------------------------------------


def _scratch_drive(folders, tool_name, args, answer=None):
    agent, handler = scratch_mod._agent(folders)
    result, prompts = scratch_mod._drive(agent, handler, tool_name, args, answer)
    return agent, handler, result, prompts


def _junction(link, target) -> bool:
    """A directory link at *link* to *target*; False when this host can't make one."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        pass
    if sys.platform == "win32":
        try:
            import _winapi

            _winapi.CreateJunction(str(target), str(link))
            return True
        except (OSError, AttributeError):
            return False
    return False


@pytest.fixture
def folders(tmp_path):
    project = tmp_path / "project"
    scratch = tmp_path / "gaia-scratch-test"
    project.mkdir()
    scratch.mkdir()
    return project, scratch


LOOKALIKES = [
    lambda project, scratch: scratch.parent / (scratch.name + "-x") / "a.py",
    lambda project, scratch: scratch.parent / (scratch.name + "x") / "a.py",
    lambda project, scratch: scratch / ".." / "project" / "a.py",
    lambda project, scratch: scratch / "sub" / ".." / ".." / "project" / "a.py",
    lambda project, scratch: str(scratch) + "\\..\\..\\Users\\a.py",
    lambda project, scratch: str(scratch) + "/../../outside.py",
    lambda project, scratch: scratch / ".bashrc",
    lambda project, scratch: scratch / ".github" / "workflows" / "ci.yml",
    lambda project, scratch: scratch / "sub" / ".git" / "hooks" / "pre-commit",
    lambda project, scratch: scratch / ".env",
]


@pytest.mark.parametrize("where", LOOKALIKES)
def test_a_write_that_only_looks_like_scratch_still_asks(folders, where):
    target = str(where(*folders))

    agent, _h, result, prompts = _scratch_drive(
        folders, "write_file", {"file_path": target, "content": "x"}
    )

    assert len(prompts) == 1, target
    assert result["status"] == "denied"
    assert agent.ran == []


def test_a_relative_write_whose_project_dir_escapes_scratch_still_asks(folders):
    _project, scratch = folders
    agent, _h, result, prompts = _scratch_drive(
        folders,
        "write_file",
        {"file_path": "a.py", "project_dir": str(scratch / ".." / "project")},
    )

    assert len(prompts) == 1
    assert agent.ran == []


def test_a_link_out_of_scratch_is_not_scratch(folders):
    project, scratch = folders
    link = scratch / "linked"
    if not _junction(link, project):
        pytest.skip("this host cannot create a directory link")
    target = str(link / "a.py")

    agent, _h, result, prompts = _scratch_drive(
        folders, "write_file", {"file_path": target, "content": "x"}
    )
    assert len(prompts) == 1
    assert agent.ran == []

    agent, _h, result, prompts = _scratch_drive(
        folders, "execute_python_file", {"file_path": target}
    )
    [request] = prompts
    assert SCRATCH_ARG not in request["args"]


def test_a_link_into_scratch_is_still_a_scratch_script(folders):
    project, scratch = folders
    link = project / "via"
    if not _junction(link, scratch):
        pytest.skip("this host cannot create a directory link")
    (scratch / "a.py").write_text("print(1)", encoding="utf-8")

    agent, handler, result, prompts = _scratch_drive(
        folders,
        "execute_python_file",
        {"file_path": str(link / "a.py")},
        answer=(True, True),
    )

    [request] = prompts
    assert request["args"][SCRATCH_ARG] == str(scratch.resolve())
    assert "always_scope" not in request
    assert handler.session_grants() == set()


def _follow_up(name, project, scratch):
    """A way to run code after a script in scratch was answered "always"."""
    approved = scratch / "a.py"
    if name == "same":
        return approved
    if name == "new":
        path = scratch / "fresh.py"
        path.write_text("print('new')", encoding="utf-8")
        return path
    if name == "edited":
        approved.write_text("print('edited')", encoding="utf-8")
        return approved
    if name == "renamed":
        path = scratch / "renamed.py"
        approved.rename(path)
        return path
    link = project / "via"
    if not _junction(link, scratch):
        pytest.skip("this host cannot create a directory link")
    return link / "a.py"


@pytest.mark.parametrize("follow_up", ["same", "new", "edited", "renamed", "via_link"])
def test_no_answer_lets_a_scratch_script_run_unasked(folders, follow_up):
    project, scratch = folders
    (scratch / "a.py").write_text("print('shown')", encoding="utf-8")
    agent, handler = scratch_mod._agent(folders)

    _r, prompts = scratch_mod._drive(
        agent,
        handler,
        "execute_python_file",
        {"file_path": str(scratch / "a.py")},
        answer=(True, True),
    )
    assert "always_scope" not in prompts[0]
    assert handler.session_grants() == set()

    target = str(_follow_up(follow_up, project, scratch))
    result, prompts = scratch_mod._drive(
        agent, handler, "execute_python_file", {"file_path": target}
    )

    assert len(prompts) == 1, follow_up
    assert result["status"] == "denied"
    assert agent.ran == [("execute_python_file", str(scratch / "a.py"))]


def test_a_python_files_grant_from_outside_does_not_reach_scratch(folders):
    project, scratch = folders
    agent, handler = scratch_mod._agent(folders)

    _r, prompts = scratch_mod._drive(
        agent,
        handler,
        "execute_python_file",
        {"file_path": str(project / "tool.py")},
        answer=(True, True),
    )
    assert prompts[0]["always_scope"] == "running Python files"

    targets = [scratch / "fresh.py"]
    link = project / "via"
    if _junction(link, scratch):
        targets.append(link / "fresh.py")
    for target in targets:
        result, prompts = scratch_mod._drive(
            agent, handler, "execute_python_file", {"file_path": str(target)}
        )
        assert len(prompts) == 1, target
        assert result["status"] == "denied"


def test_full_access_still_runs_a_scratch_script_unasked(folders):
    _project, scratch = folders
    agent, handler = scratch_mod._agent(folders)
    handler.auto_approve_gated_tools = True
    handler.full_access = True
    target = str(scratch / "a.py")

    result, prompts = scratch_mod._drive(
        agent, handler, "execute_python_file", {"file_path": target}
    )

    assert prompts == []
    assert result["status"] == "success"
    assert agent.ran == [("execute_python_file", target)]


@pytest.mark.parametrize("where", LOOKALIKES[:6])
def test_a_lookalike_script_is_not_marked_as_scratch(folders, where):
    target = str(where(*folders))

    agent, _h, _result, prompts = _scratch_drive(
        folders, "execute_python_file", {"file_path": target}
    )

    [request] = prompts
    assert SCRATCH_ARG not in request["args"]
    assert agent.ran == []


def test_a_model_supplied_scratch_marker_earns_nothing(folders):
    project, _scratch = folders
    target = str(project / "tool.py")

    agent, handler, result, prompts = _scratch_drive(
        folders,
        "execute_python_file",
        {"file_path": target, SCRATCH_ARG: str(project)},
        answer=(True, True),
    )

    assert prompts == []
    assert result["error_type"] == "invalid_arguments"
    assert agent.ran == []
    assert handler.session_grants() == set()


def test_inside_folder_judges_where_a_path_lands_not_its_spelling(tmp_path):
    scratch = tmp_path / "gaia-scratch-a"
    outside = tmp_path / "project"
    scratch.mkdir()
    outside.mkdir()
    s = str(scratch)
    for target in (
        s + "-b/x.py",
        s + "b/x.py",
        os.path.join(s, "..", "x.py"),
        os.path.join(s, "sub", "..", "..", "x.py"),
        s,  # the folder itself is not a script inside it
        s + os.sep,
        "",
    ):
        assert not is_inside_folder(target, s), target
    assert is_inside_folder(os.path.join(s, "sub", "..", "x.py"), s)
    assert not is_inside_folder(os.path.join(s, "x.py"), "")
    if sys.platform == "win32":
        assert is_inside_folder(os.path.join(s.upper(), "x.py"), s)

    # normpath says inside, realpath says outside: outside wins.
    out_link = scratch / "linked"
    # normpath says outside, realpath says inside: inside wins.
    in_link = outside / "via"
    # The scratch folder itself, named through a link.
    alias = tmp_path / "alias"
    if not (
        _junction(out_link, outside)
        and _junction(in_link, scratch)
        and _junction(alias, scratch)
    ):
        pytest.skip("this host cannot create a directory link")
    assert not is_inside_folder(str(out_link / "x.py"), s)
    assert is_inside_folder(str(in_link / "x.py"), s)
    assert is_inside_folder(os.path.join(s, "x.py"), str(alias))
    assert is_inside_folder(str(alias / "x.py"), s)


def _other_spellings(path):
    """Spellings of *path* that realpath leaves distinct from it."""
    spellings = []
    if sys.platform == "win32":
        spellings.append("\\\\?\\" + path)
        drive, rest = os.path.splitdrive(path)
        if drive.endswith(":"):
            spellings.append("\\\\localhost\\" + drive[0] + "$" + rest)
    swapped = path.swapcase()
    if swapped != path and os.path.exists(swapped):
        spellings.append(swapped)
    return spellings


def test_inside_folder_sees_through_other_spellings_of_the_folder(tmp_path):
    scratch = tmp_path / "gaia-scratch-a"
    scratch.mkdir()
    script = scratch / "a.py"
    script.write_text("print(1)", encoding="utf-8")

    spellings = [p for p in _other_spellings(str(script)) if os.path.exists(p)]
    if not spellings:
        pytest.skip("this host has no other spelling of a local path")
    for spelling in spellings:
        assert is_inside_folder(spelling, str(scratch)), spelling


def test_a_scratch_script_named_another_way_is_still_asked_about(folders):
    project, scratch = folders
    script = scratch / "a.py"
    script.write_text("print(1)", encoding="utf-8")
    spellings = [p for p in _other_spellings(str(script)) if os.path.exists(p)]
    if not spellings:
        pytest.skip("this host has no other spelling of a local path")
    agent, handler = scratch_mod._agent(folders)
    # A broader grant from outside scratch must not reach it either.
    scratch_mod._drive(
        agent,
        handler,
        "execute_python_file",
        {"file_path": str(project / "tool.py")},
        answer=(True, True),
    )

    for spelling in spellings:
        result, prompts = scratch_mod._drive(
            agent, handler, "execute_python_file", {"file_path": spelling}
        )
        assert len(prompts) == 1, spelling
        assert SCRATCH_ARG in prompts[0]["args"], spelling
        assert "always_scope" not in prompts[0], spelling
        assert result["status"] == "denied"


def test_inside_folder_counts_an_unresolvable_path_as_inside(tmp_path, monkeypatch):
    def broken(_path, *_a, **_k):
        raise OSError("cannot resolve")

    monkeypatch.setattr(os.path, "realpath", broken)
    assert is_inside_folder(str(tmp_path / "a.py"), str(tmp_path))


def test_a_scratch_marker_on_any_tool_grants_nothing(tmp_path):
    scratch = str(tmp_path / "gaia-scratch-a")
    for tool in (
        "execute_python_file",
        "run_python",
        "write_file",
        "run_shell_command",
    ):
        scope = grant_scope(
            tool,
            {
                "file_path": os.path.join(scratch, "x.py"),
                "command": "python x.py",
                "code": "print(1)",
                SCRATCH_ARG: scratch,
            },
        )
        assert scope is None, tool


# ---------------------------------------------------------------------------
# A shell or script grant never reaches code in the scratch folder
# ---------------------------------------------------------------------------


class _RunProbe(scratch_mod._Probe):
    """The shell and script tools, recording what reached them."""

    def _register_tools(self):
        super()._register_tools()
        agent = self

        @register_tool
        def run_shell_command(command: str, working_directory: str = "") -> dict:
            """Probe shell."""
            agent.ran.append(("run_shell_command", command, working_directory))
            return {"status": "success", "return_code": 0}

        @register_tool
        def execute_python_file(file_path: str, args: str = "") -> dict:
            """Probe runner with arguments."""
            agent.ran.append(("execute_python_file", file_path, args))
            return {"status": "success", "return_code": 0}


def _run_agent(folders):
    project, scratch = folders
    handler = SSEOutputHandler()
    with patch("gaia.agents.base.agent.AgentSDK"):
        _RunProbe._console_override = handler
        agent = _RunProbe(silent_mode=True, skip_lemonade=True)
    agent.path_validator = PathValidator(allowed_paths=[str(project)])
    agent.path_validator.set_scratch_dir(str(scratch))
    return agent, handler


def _scratch_spelling(kind, project, scratch):
    """The scratch folder as a shell call in *project* might name it."""
    if kind == "absolute":
        return str(scratch)
    if kind == "relative":
        return os.path.join("..", scratch.name)
    if kind == "dotdot":
        return os.path.join("sub", "..", "..", scratch.name)
    if kind == "link":
        link = project / "via"
        if not _junction(link, scratch):
            pytest.skip("this host cannot create a directory link")
        return "via"
    if kind == "extended":
        if sys.platform != "win32":
            pytest.skip("\\\\?\\ paths are Windows-only")
        return "\\\\?\\" + str(scratch)
    swapped = str(scratch).swapcase()
    if not os.path.isdir(swapped):
        pytest.skip("this filesystem is case-sensitive")
    return swapped


SPELLINGS = ["absolute", "relative", "dotdot", "link", "extended", "case"]

# (the call answered "always" in the project, a call that runs scratch code)
SCRATCH_FOLLOW_UPS = {
    "python -m via cd": ("python -m explore", "cd {p} && python -m explore"),
    "pytest": ("pytest -q", "pytest {p}" + os.sep + "test_x.py"),
    "uv run": ("uv run pytest -q", "uv run pytest {p}" + os.sep + "test_x.py"),
    "make": ("make", "cd {p} && make"),
    "pip install": ("pip install .", "pip install {p}"),
    "go run": ("go run main.go", "go run {p}" + os.sep + "main.go"),
    "project script": ("python tool.py", "python tool.py {p}" + os.sep + "x.py"),
}


def _shell_drive(agent, handler, command, folder, answer=None):
    return scratch_mod._drive(
        agent,
        handler,
        "run_shell_command",
        {"command": command, "working_directory": str(folder)},
        answer,
    )


@pytest.mark.parametrize("spelling", SPELLINGS)
@pytest.mark.parametrize("case", sorted(SCRATCH_FOLLOW_UPS))
def test_a_shell_grant_does_not_reach_scratch_code(folders, case, spelling):
    project, scratch = folders
    granted, follow_up = SCRATCH_FOLLOW_UPS[case]
    agent, handler = _run_agent(folders)
    _r, prompts = _shell_drive(agent, handler, granted, project, (True, True))
    assert prompts[0].get("always_scope"), granted
    assert handler.session_grants()

    command = follow_up.format(p=_scratch_spelling(spelling, project, scratch))
    result, prompts = _shell_drive(agent, handler, command, project)

    assert len(prompts) == 1, command
    assert prompts[0]["args"][SCRATCH_ARG] == str(scratch.resolve())
    assert "always_scope" not in prompts[0], command
    assert result["status"] == "denied"
    assert len(agent.ran) == 1


@pytest.mark.parametrize("spelling", SPELLINGS)
def test_python_m_run_in_scratch_as_its_working_folder_asks(folders, spelling):
    project, scratch = folders
    agent, handler = _run_agent(folders)
    _shell_drive(agent, handler, "python -m explore", project, (True, True))

    folder = os.path.join(str(project), _scratch_spelling(spelling, project, scratch))
    result, prompts = _shell_drive(agent, handler, "python -m explore", folder)

    assert len(prompts) == 1, folder
    assert "always_scope" not in prompts[0]
    assert result["status"] == "denied"


@pytest.mark.parametrize(
    "command",
    [
        "PYTHONPATH={p} python -m explore",
        "pytest --rootdir={p}",
        "make -C{p}",
        "cd sub && pytest ..{s}..{s}{name}",
        "cd {p} && cd .. && python -m explore",
        "pushd {p} && python -m explore",
    ],
)
def test_other_ways_of_naming_scratch_still_ask(folders, command):
    project, scratch = folders
    agent, handler = _run_agent(folders)
    for granted in ("python -m explore", "pytest -q", "make"):
        _shell_drive(agent, handler, granted, project, (True, True))

    command = command.format(p=str(scratch), s=os.sep, name=scratch.name)
    result, prompts = _shell_drive(agent, handler, command, project)

    assert len(prompts) == 1, command
    assert "always_scope" not in prompts[0]
    assert result["status"] == "denied"


@pytest.mark.parametrize(
    "args",
    [
        lambda project, scratch: str(scratch / "x.py"),
        lambda project, scratch: os.path.join("..", scratch.name, "x.py"),
        lambda project, scratch: "--input=" + str(scratch / "data.py"),
        lambda project, scratch: "-v " + str(scratch),
    ],
)
def test_a_project_script_handed_scratch_args_asks(folders, args):
    project, scratch = folders
    agent, handler = _run_agent(folders)
    script = str(project / "tool.py")
    _r, prompts = scratch_mod._drive(
        agent,
        handler,
        "execute_python_file",
        {"file_path": script},
        answer=(True, True),
    )
    assert prompts[0]["always_scope"] == "running Python files"

    handed = args(project, scratch)
    result, prompts = scratch_mod._drive(
        agent, handler, "execute_python_file", {"file_path": script, "args": handed}
    )

    assert len(prompts) == 1, handed
    assert "always_scope" not in prompts[0]
    assert result["status"] == "denied"
    assert agent.ran == [("execute_python_file", script, "")]


@pytest.mark.parametrize(
    "granted,follow_up",
    [
        ("pytest -q", "pytest tests/unit -q"),
        ("pytest -q", "cd sub && pytest -q 2>&1 | tail -5"),
        ("pytest -q", "pytest ..{s}{name}-x{s}test_x.py"),
        ("python -m explore", "python -m explore --out results"),
        ("make", "make"),
        ("pip install .", "pip install -e ."),
        ("go run main.go", "go run cmd/main.go"),
        ("git log --format=%H%n%s", "git log --format=%H%n%s -5"),
        ("python tool.py", "python tool.py data.csv"),
        ("pip install .", "pip install git+https://github.com/x/y.git"),
    ],
)
def test_a_grant_that_never_touches_scratch_still_holds(folders, granted, follow_up):
    project, scratch = folders
    agent, handler = _run_agent(folders)
    _shell_drive(agent, handler, granted, project, (True, True))

    command = follow_up.format(s=os.sep, name=scratch.name)
    result, prompts = _shell_drive(agent, handler, command, project)

    assert prompts == [], command
    assert result["status"] == "success"


def test_a_project_script_with_project_args_keeps_its_grant(folders):
    project, _scratch = folders
    agent, handler = _run_agent(folders)
    script = str(project / "tool.py")
    scratch_mod._drive(
        agent, handler, "execute_python_file", {"file_path": script}, (True, True)
    )

    result, prompts = scratch_mod._drive(
        agent,
        handler,
        "execute_python_file",
        {"file_path": script, "args": "data.csv --out results"},
    )

    assert prompts == []
    assert result["status"] == "success"


def test_full_access_still_runs_scratch_shell_code_unasked(folders):
    project, scratch = folders
    agent, handler = _run_agent(folders)
    handler.auto_approve_gated_tools = True
    handler.full_access = True

    command = f"cd {scratch} && python -m explore"
    result, prompts = _shell_drive(agent, handler, command, project)

    assert prompts == []
    assert result["status"] == "success"
    assert agent.ran == [("run_shell_command", command, str(project))]


@pytest.mark.parametrize(
    "command,says",
    [
        ("python -m explore", "runs that Python module"),
        ("pytest -q", "runs the test code"),
        ("make", "runs the Makefile"),
        ("pip install .", "setup code"),
        ("go run main.go", "runs Go code"),
        ("uv run pytest", "runs the test code"),
        ("uv run script.py", "any script"),
        ("npm test", "scripts"),
    ],
)
def test_a_grant_label_says_when_the_command_runs_code(command, says):
    scope = grant_scope("run_shell_command", {"command": command})

    assert scope is not None, command
    assert says in scope.label, scope.label


def test_python_m_is_not_the_same_grant_as_a_program_of_that_name():
    module = grant_scope("run_shell_command", {"command": "python -m explore"})
    program = grant_scope("run_shell_command", {"command": "explore"})

    assert module.keys != program.keys


def test_a_wrapper_that_runs_a_shell_is_not_granted():
    for command in ("uv run bash -c x", "poetry run sh x", "uv run --with x pytest"):
        assert grant_scope("run_shell_command", {"command": command}) is None, command


def test_reaching_the_folder_counts_an_unreadable_line_as_inside(tmp_path):
    folder = str(tmp_path)
    assert command_reaches_folder("pytest 'unbalanced", [str(tmp_path.parent)], folder)
    assert not command_reaches_folder("", [str(tmp_path.parent)], folder)


def test_the_real_shell_asks_after_cd_moved_its_session_into_scratch(
    folders, monkeypatch
):
    project, scratch = folders
    python = _python()
    (project / "tool.py").write_text("print('project')", encoding="utf-8")
    (scratch / "tool.py").write_text("print('scratch')", encoding="utf-8")
    monkeypatch.chdir(project)
    handler = SSEOutputHandler()
    agent = _agent(handler)
    agent.path_validator = PathValidator(allowed_paths=[str(project)])
    agent.path_validator.set_scratch_dir(str(scratch))

    result, events = _drive(
        agent,
        handler,
        {"command": f"{python} tool.py"},
        on_prompt=_answer(handler, always=True),
    )
    assert _prompts(events)[0]["always_scope"] == "python (any script)"
    assert "project" in str(result)
    _drive(agent, handler, {"command": f"cd {scratch}"}, on_prompt=_answer(handler))

    result, events = _drive(
        agent,
        handler,
        {"command": f"{python} tool.py"},
        on_prompt=_answer(handler, approved=False),
    )

    [request] = _prompts(events)
    assert "always_scope" not in request
    assert result["status"] == "denied"


@pytest.mark.parametrize(
    "command",
    [
        # A variable the line sets itself is filled in by the shell, not by GAIA.
        "A={parent}; cd $A{s}{name} && pytest -q",
        "cd ${{SCRATCH_ROOT}} && pytest -q",
        "A={parent}{s}{name}{s}test_x.py; pytest -q $A",
        "PYTHONPATH=$SCRATCH_ROOT pytest -q",
        # The scratch folder's name prefix is predictable.
        "cd {parent}{s}gaia-scratch-te* && pytest -q",
        "pytest {parent}{s}gaia-scratch-tes?{s}test_x.py",
        # Back to a folder an earlier call moved into.
        "cd - && pytest -q",
        "popd && pytest -q",
        # POSIX reads `\\a` as `a`; Windows keeps the backslash.
        "cd ..{s}gaia-scr\\atch-test && pytest -q",
    ],
)
def test_a_line_whose_folder_only_the_shell_knows_still_asks(folders, command):
    project, scratch = folders
    agent, handler = _run_agent(folders)
    _shell_drive(agent, handler, "pytest -q", project, (True, True))

    command = command.format(parent=str(scratch.parent), s="/", name=scratch.name)
    result, prompts = _shell_drive(agent, handler, command, project)

    assert len(prompts) == 1, command
    assert "always_scope" not in prompts[0]
    assert result["status"] == "denied"


@pytest.mark.skipif(sys.platform != "win32", reason="UNC paths are Windows-only")
@pytest.mark.parametrize(
    "word",
    ["\\\\evil\\share\\t.py", "//evil/share/t.py", "\\\\?\\UNC\\evil\\share\\t.py"],
)
def test_a_network_path_is_never_looked_at_before_the_prompt(
    tmp_path, monkeypatch, word
):
    looked = []
    real_stat, real_realpath = os.stat, os.path.realpath

    def stat(path, *a, **k):
        looked.append(str(path))
        return real_stat(path, *a, **k)

    def realpath(path, *a, **k):
        looked.append(str(path))
        return real_realpath(path, *a, **k)

    monkeypatch.setattr(os, "stat", stat)
    monkeypatch.setattr(os.path, "realpath", realpath)

    assert command_reaches_folder(
        f"pytest {word}", [str(tmp_path)], str(tmp_path / "s")
    )
    assert not [p for p in looked if "evil" in p]


@pytest.mark.parametrize(
    "command",
    [
        "pip install git+https://github.com/x/y.git",
        "curl --url=https://example.com/a?b=1",
        "awk '{print $1}' data.txt",
        "awk '{print $NF}' data.txt",
        "jq '.[] | $item' data.json",
        "grep -E 'end$' data.txt",
        "pytest tests/*.py",
    ],
)
def test_ordinary_words_do_not_read_as_scratch(folders, command):
    project, scratch = folders
    assert not command_reaches_folder(command, [str(project)], str(scratch))


def test_an_approved_call_with_a_home_relative_folder_runs(repo, monkeypatch):
    # The approval is noted with ~ as typed; the run expands it (#5700).
    monkeypatch.setenv("HOME", str(repo.parent))
    monkeypatch.setenv("USERPROFILE", str(repo.parent))
    folder = f"~/{repo.name}"
    python = _python()
    agent = _agent(SSEOutputHandler())
    approved = _write_line(python)
    agent.note_confirmed_call(
        "run_shell_command", {"command": approved, "working_directory": folder}
    )

    result = _tool()(command=approved, working_directory=folder)

    assert result["status"] == "success", result
    assert (repo / "ran.txt").exists()
