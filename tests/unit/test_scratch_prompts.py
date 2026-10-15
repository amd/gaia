# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The agent's own scratch folder does not cost the user a prompt per script.

Seen on a data task: every exploration step wrote ``exploreN.py`` into the
agent's scratch folder and then ran it, two prompts a step, 30 in all. Writing
there is not a change to anything the user owns, so it runs unasked; running a
script there is still asked, but "always" covers the scratch folder for the
session, so one answer covers the next script. Everything outside it is
asked as before.
"""

from __future__ import annotations

import queue
import threading
import time
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tool_grants import SCRATCH_ARG, grant_scope
from gaia.agents.base.tools import tool
from gaia.security import PathValidator
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.sse_translation import CanonicalTranslator

SCRATCH_LABEL = "Python scripts in GAIA's scratch folder"


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


def test_always_on_a_scratch_script_covers_the_next_one_and_nothing_else(folders):
    project, scratch = folders
    agent, handler = _agent(folders)
    first = str(scratch / "explore.py")

    _, prompts = _drive(
        agent,
        handler,
        "execute_python_file",
        {"file_path": first},
        answer=(True, True),
    )
    [request] = prompts
    assert request["always_scope"] == SCRATCH_LABEL
    assert request["args"][SCRATCH_ARG] == str(scratch.resolve())

    # The TUI's wire: a deliverable prompt (confirm_id) naming what "a" grants,
    # which is when the TUI shows `a always: <scope> (this session)`.
    [wire] = CanonicalTranslator(run_id="r", agent_id="gaia").translate(request)
    assert wire["always_scope"] == SCRATCH_LABEL
    assert wire["confirm_id"] == request["confirm_id"]
    assert SCRATCH_ARG not in wire["summary"]

    second = str(scratch / "explore2.py")
    result, prompts = _drive(
        agent, handler, "execute_python_file", {"file_path": second}
    )
    assert prompts == []
    assert result["status"] == "success"

    outside = str(project / "tool.py")
    result, prompts = _drive(
        agent, handler, "execute_python_file", {"file_path": outside}
    )
    assert len(prompts) == 1
    assert prompts[0]["always_scope"] == "running Python files"
    assert result["status"] == "denied"
    assert agent.ran == [
        ("execute_python_file", first),
        ("execute_python_file", second),
    ]


def test_run_python_offers_always_for_snippets(folders):
    agent, handler = _agent(folders)

    _, prompts = _drive(
        agent, handler, "run_python", {"code": "print(1)"}, answer=(True, True)
    )
    again, more = _drive(agent, handler, "run_python", {"code": "print(2)"})

    assert prompts[0]["always_scope"] == "Python snippets (run_python)"
    assert more == []
    assert again["status"] == "success"


def test_the_scratch_grant_is_only_for_paths_inside_the_marked_folder(tmp_path):
    scratch = str(tmp_path / "gaia-scratch-test")
    inside = grant_scope(
        "execute_python_file",
        {
            "file_path": str(tmp_path / "gaia-scratch-test" / "a.py"),
            SCRATCH_ARG: scratch,
        },
    )
    elsewhere = grant_scope(
        "execute_python_file",
        {"file_path": str(tmp_path / "project" / "a.py"), SCRATCH_ARG: scratch},
    )
    unmarked = grant_scope(
        "execute_python_file",
        {"file_path": str(tmp_path / "gaia-scratch-test" / "a.py")},
    )

    assert inside.label == SCRATCH_LABEL
    # A marker that does not contain the path grants only the usual family.
    assert elsewhere.label == "running Python files"
    assert unmarked.label == "running Python files"
    assert inside.keys != elsewhere.keys
