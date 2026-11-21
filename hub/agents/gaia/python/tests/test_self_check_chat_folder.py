# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The end-of-turn check for a file saved into a mistyped copy of the working
folder compares against the chat's folder, not the folder the process started
in: the Agent UI backend and the daemon's sidecar both start in the home
folder (#8629)."""

import os

import pytest
from gaiapy.chat_agent import ChatAgent, ChatAgentConfig

from gaia.agents.base.completion import CompletionEvidence
from gaia.agents.base.tools import _TOOL_REGISTRY

REQUEST = "Write answers.md"


@pytest.fixture
def folders(tmp_path, monkeypatch):
    """A chat folder, a mistyped sibling of it, and the folder the process runs in."""
    chat = tmp_path / "runs" / "knowledgework-20261119-071118" / "kw-research"
    slip = tmp_path / "runs" / "knowledgework-20261119" / "kw-research"
    process = tmp_path / "home"
    for folder in (chat, slip, process):
        folder.mkdir(parents=True)
    monkeypatch.chdir(process)
    return chat, slip, process


def _agent(working_directory, allowed):
    return ChatAgent(
        ChatAgentConfig(
            silent_mode=True,
            allowed_paths=[str(path) for path in allowed],
            working_directory=working_directory,
        )
    )


def _plan_after_writing(agent, report):
    report.write_text("# Answers\n", encoding="utf-8")
    # The agent builds the turn's evidence with the process folder.
    evidence = CompletionEvidence(REQUEST, os.getcwd())
    args = {"file_path": str(report)}
    evidence.record("write_file", args, {"status": "success", **args}, True)
    agent._completion_evidence = evidence
    agent._current_query = REQUEST
    agent._turn_tool_executions = []
    return agent._build_self_check_plan()


@pytest.fixture
def registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


def test_a_mistyped_copy_of_the_chat_folder_is_raised(folders, registry):
    """Built the way the Agent UI and the sidecar build it: the chat's folder
    is not the folder the process runs in."""
    chat, slip, _process = folders
    agent = _agent(str(chat), [chat, slip])

    plan = _plan_after_writing(agent, slip / "answers.md")

    assert plan is not None
    assert len(plan["findings"]) == 1
    finding = plan["findings"][0]
    assert "`answers.md` was written to" in finding
    # The written path comes back in the evidence's own case-folded form.
    assert str(slip).lower() in finding.lower() and str(chat) in finding


def test_a_file_in_the_chat_folder_is_not_raised(folders, registry):
    chat, slip, _process = folders
    agent = _agent(str(chat), [chat, slip])

    assert _plan_after_writing(agent, chat / "answers.md") is None


def test_without_a_chat_folder_the_process_folder_is_the_working_folder(
    tmp_path, monkeypatch, registry
):
    process = tmp_path / "runs" / "knowledgework-20261119-071118" / "kw-research"
    slip = tmp_path / "runs" / "knowledgework-20261119" / "kw-research"
    for folder in (process, slip):
        folder.mkdir(parents=True)
    monkeypatch.chdir(process)
    agent = _agent(None, [process, slip])

    plan = _plan_after_writing(agent, slip / "answers.md")

    assert plan is not None and len(plan["findings"]) == 1
    assert str(process) in plan["findings"][0]
