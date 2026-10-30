# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The agent's own scratch folder does not cost the user a prompt per script.

Seen on a data task: every exploration step wrote ``exploreN.py`` into the
agent's scratch folder and then ran it, two prompts a step, 30 in all. Writing
there is not a change to anything the user owns, so it runs unasked. Running a
script there is asked every time, with only "allow once" on offer: because the
writes are free, a standing grant to run what is there would cover code the
user was never shown. Everything outside it is asked as before.
"""

from __future__ import annotations

import queue
import threading
import time
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.call_risk import RISK_NOTE_ARG
from gaia.agents.base.tool_grants import SCRATCH_ARG, grant_scope
from gaia.agents.base.tools import tool
from gaia.security import PathValidator
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.sse_translation import CanonicalTranslator


class _Probe(Agent):
    """Write and run tools that only record what reached them."""

    def _get_system_prompt(self):
        return "probe"

    def _register_tools(self):
        agent = self
        agent.ran = []

        @tool
        def write_file(file_path: str, content: str) -> dict:
            """Probe writer."""
            agent.ran.append(("write_file", file_path))
            return {"status": "success"}

        @tool
        def execute_python_file(file_path: str) -> dict:
            """Probe runner."""
            agent.ran.append(("execute_python_file", file_path))
            return {"status": "success", "return_code": 0}

        @tool
        def run_python(code: str) -> dict:
            """Probe snippet runner."""
            agent.ran.append(("run_python", code))
            return {"status": "success", "return_code": 0}

    def _create_console(self):
        return self._console_override


@pytest.fixture
def folders(tmp_path):
    project = tmp_path / "project"
    scratch = tmp_path / "gaia-scratch-test"
    project.mkdir()
    scratch.mkdir()
    return project, scratch


def _agent(folders):
    project, scratch = folders
    handler = SSEOutputHandler()
    with patch("gaia.agents.base.agent.AgentSDK"):
        _Probe._console_override = handler
        agent = _Probe(silent_mode=True, skip_lemonade=True)
    agent.path_validator = PathValidator(allowed_paths=[str(project)])
    agent.path_validator.set_scratch_dir(str(scratch))
    return agent, handler


def _drive(agent, handler, tool_name, args, answer=None):
    """Run one call; answer its prompt with ``(approved, always)``; return (result, prompts)."""
    outcome = {}
    worker = threading.Thread(
        target=lambda: outcome.update(result=agent._execute_tool(tool_name, args)),
        daemon=True,
    )
    worker.start()
    prompts = []
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            event = handler.event_queue.get(timeout=0.05)
        except queue.Empty:
            if not worker.is_alive():
                break
            continue
        if event.get("type") == "permission_request":
            prompts.append(event)
            approved, always = answer or (False, False)
            handler.resolve_tool_confirmation(
                approved=approved, always=always, confirm_id=event["confirm_id"]
            )
    worker.join(timeout=5)
    assert not worker.is_alive()
    return outcome["result"], prompts


def test_a_write_in_the_scratch_folder_runs_unasked(folders):
    agent, handler = _agent(folders)
    target = str(folders[1] / "explore.py")

    result, prompts = _drive(
        agent, handler, "write_file", {"file_path": target, "content": "print(1)"}
    )

    assert prompts == []
    assert result["status"] == "success"
    assert agent.ran == [("write_file", target)]


@pytest.mark.parametrize(
    "where",
    [
        lambda project, scratch: project / "report.py",  # the user's own folder
        lambda project, scratch: scratch / ".git" / "config",  # runs code later
        lambda project, scratch: scratch.parent / "gaia-scratch-other" / "x.py",
    ],
)
def test_a_write_anywhere_else_still_asks(folders, where):
    agent, handler = _agent(folders)
    target = str(where(*folders))

    result, prompts = _drive(
        agent, handler, "write_file", {"file_path": target, "content": "x"}
    )

    assert len(prompts) == 1
    assert prompts[0]["args"]["file_path"] == target
    assert result["status"] == "denied"
    assert agent.ran == []


def test_a_scratch_script_is_asked_each_run_and_never_offered_always(folders):
    project, scratch = folders
    agent, handler = _agent(folders)
    first = str(scratch / "explore.py")

    result, prompts = _drive(
        agent,
        handler,
        "execute_python_file",
        {"file_path": first},
        answer=(True, True),
    )
    [request] = prompts
    assert result["status"] == "success"
    assert "always_scope" not in request
    assert request["args"][SCRATCH_ARG] == str(scratch.resolve())
    assert handler.session_grants() == set()

    # The TUI's wire carries no scope either, so `a` is inert there.
    [wire] = CanonicalTranslator(run_id="r", agent_id="gaia").translate(request)
    assert not wire.get("always_scope")
    assert SCRATCH_ARG not in wire["summary"]

    # The same script again, and the next one, are each asked about.
    for script in (first, str(scratch / "explore2.py")):
        result, prompts = _drive(
            agent, handler, "execute_python_file", {"file_path": script}
        )
        assert len(prompts) == 1
        assert result["status"] == "denied"

    outside = str(project / "tool.py")
    result, prompts = _drive(
        agent, handler, "execute_python_file", {"file_path": outside}
    )
    assert len(prompts) == 1
    assert prompts[0]["always_scope"] == "running Python files"
    assert result["status"] == "denied"
    assert agent.ran == [("execute_python_file", first)]


def test_run_python_offers_always_for_snippets(folders):
    agent, handler = _agent(folders)

    _, prompts = _drive(
        agent, handler, "run_python", {"code": "print(1)"}, answer=(True, True)
    )
    again, more = _drive(agent, handler, "run_python", {"code": "print(2)"})

    assert prompts[0]["always_scope"] == "Python snippets (run_python)"
    assert more == []
    assert again["status"] == "success"


def test_a_scratch_marked_call_has_no_grant_scope(tmp_path):
    scratch = str(tmp_path / "gaia-scratch-test")
    marked = grant_scope(
        "execute_python_file",
        {
            "file_path": str(tmp_path / "gaia-scratch-test" / "a.py"),
            SCRATCH_ARG: scratch,
        },
    )
    unmarked = grant_scope(
        "execute_python_file", {"file_path": str(tmp_path / "project" / "a.py")}
    )

    assert marked is None
    assert unmarked.label == "running Python files"


def test_the_wire_args_are_only_what_the_model_sent(folders):
    """#5361: a policy client must not match on keys GAIA added to the prompt."""
    project, scratch = folders
    agent, handler = _agent(folders)
    risk_note = "Sends your commits to the remote repository."
    # A host that names the folder and a risk note the model never sent.
    agent.confirmation_args_for_call = lambda _name, args: {
        **args,
        "working_directory": str(project),
        RISK_NOTE_ARG: risk_note,
    }
    call = {"file_path": str(scratch / "explore.py")}

    _, [request] = _drive(agent, handler, "execute_python_file", call)
    assert request["args"][SCRATCH_ARG] == str(scratch.resolve())
    assert request["args"]["working_directory"] == str(project)

    [wire] = CanonicalTranslator(run_id="r", agent_id="gaia").translate(request)
    assert wire["args"] == call
    assert wire["prompt_context"] == {
        "working_directory": str(project),
        "risk_note": risk_note,
    }


def test_a_relative_write_is_judged_where_the_tool_writes_it(folders, monkeypatch):
    # The process sits in scratch, but the tool resolves bare names against the
    # chat workspace, so the write lands in the user's folder and must be asked.
    project, scratch = folders
    agent, handler = _agent(folders)
    monkeypatch.chdir(scratch)

    def preflight(_args):
        return None

    preflight.target_of = lambda args: project / args["file_path"]
    entry = dict(agent._tools_registry["write_file"], preflight=preflight)
    monkeypatch.setitem(agent._tools_registry, "write_file", entry)

    _result, prompts = _drive(
        agent, handler, "write_file", {"file_path": "explore.py", "content": "x"}
    )

    assert len(prompts) == 1
