# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Adversarial checks on the shell approval prompt (#5090, #5094).

Each test feeds the guard something hostile and asserts it holds: the prompt
shows exactly the line that would run or nothing runs, a one-time approval is
spent on one exact call, "always" is never granted for a one-time line, the
strictest segment of a chained line decides it, and the scratch-folder
exemption covers only the agent's real scratch folder.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from gaia.agents.base.call_risk import RISK_NOTE_ARG
from gaia.agents.base.console import AgentConsole
from gaia.agents.base.tool_grants import SCRATCH_ARG, grant_scope
from gaia.agents.tools.shell_tools import (
    NOTE_PUSH,
    NOTE_UNINSPECTABLE,
    TIER_CONFIRM,
    TIER_CONFIRM_ONCE,
    TIER_REFUSE,
    ShellToolsMixin,
    outside_note,
)
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

SCRATCH_LABEL = scratch_mod.SCRATCH_LABEL


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
    assert request.get("always_scope") != SCRATCH_LABEL
    assert SCRATCH_ARG not in request["args"]


def test_always_on_scratch_does_not_cover_a_link_out_of_it(folders):
    project, scratch = folders
    link = scratch / "linked"
    if not _junction(link, project):
        pytest.skip("this host cannot create a directory link")
    agent, handler = scratch_mod._agent(folders)

    _r, prompts = scratch_mod._drive(
        agent,
        handler,
        "execute_python_file",
        {"file_path": str(scratch / "a.py")},
        answer=(True, True),
    )
    assert prompts[0]["always_scope"] == SCRATCH_LABEL

    result, prompts = scratch_mod._drive(
        agent, handler, "execute_python_file", {"file_path": str(link / "b.py")}
    )
    assert len(prompts) == 1
    assert result["status"] == "denied"
    assert agent.ran == [("execute_python_file", str(scratch / "a.py"))]


@pytest.mark.parametrize("where", LOOKALIKES[:6])
def test_a_lookalike_script_is_never_offered_the_scratch_grant(folders, where):
    target = str(where(*folders))

    agent, _h, _result, prompts = _scratch_drive(
        folders, "execute_python_file", {"file_path": target}
    )

    [request] = prompts
    assert request.get("always_scope") != SCRATCH_LABEL
    assert agent.ran == []


def test_a_model_supplied_scratch_marker_earns_nothing(folders):
    project, scratch = folders
    target = str(project / "tool.py")

    agent, handler, result, prompts = _scratch_drive(
        folders,
        "execute_python_file",
        {"file_path": target, SCRATCH_ARG: str(project)},
        answer=(True, True),
    )

    for request in prompts:
        assert request.get("always_scope") != SCRATCH_LABEL
    assert agent.ran == []
    assert not any("scratch" in key for key in handler.session_grants())


def test_the_scratch_grant_scope_rejects_traversal_and_prefix_tricks(tmp_path):
    scratch = str(tmp_path / "gaia-scratch-a")
    for target in (
        scratch + "-b/x.py",
        scratch + "b/x.py",
        os.path.join(scratch, "..", "x.py"),
        os.path.join(scratch, "sub", "..", "..", "x.py"),
        scratch,  # the folder itself is not a script inside it
    ):
        scope = grant_scope(
            "execute_python_file", {"file_path": target, SCRATCH_ARG: scratch}
        )
        assert scope is None or scope.label != SCRATCH_LABEL, target


def test_a_scratch_marker_on_another_tool_grants_nothing(tmp_path):
    scratch = str(tmp_path / "gaia-scratch-a")
    for tool in ("run_python", "write_file", "run_shell_command"):
        scope = grant_scope(
            tool,
            {
                "file_path": os.path.join(scratch, "x.py"),
                "command": "python x.py",
                "code": "print(1)",
                SCRATCH_ARG: scratch,
            },
        )
        assert scope is None or scope.label != SCRATCH_LABEL, tool
