# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Reading a data file as text points the model at analyze_data_file.

Qwen3.5-2B answered "total revenue per salesperson" by reading sales.csv and
adding rows up itself, dropping a month; Gemma called analyze_data_file.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from gaiapy.chat_agent import ChatAgent

from gaia.agents.base.agent import Agent


def _host(executions=(), registry=("analyze_data_file", "read_file")):
    return SimpleNamespace(
        _tools_registry={name: {} for name in registry},
        _turn_tool_executions=[{"tool": t} for t in executions],
    )


@pytest.mark.parametrize(
    "tool, args, table",
    [
        (
            "read_file",
            {"file_path": "C:/data/sales_data_2025.csv"},
            "C:/data/sales_data_2025.csv",
        ),
        ("run_shell_command", {"command": 'type "Q1 sales.xlsx"'}, "Q1 sales.xlsx"),
        (
            "query_specific_file",
            {"file_path": "report.TSV", "query": "total"},
            "report.TSV",
        ),
        ("run_python", {"code": "import pandas as pd; pd.read_csv('a.csv')"}, "a.csv"),
    ],
)
def test_a_text_read_of_a_table_names_it(tool, args, table):
    assert ChatAgent._table_read_by_hand(_host(), tool, args) == table


@pytest.mark.parametrize(
    "host, tool, args",
    [
        (_host(), "read_file", {"file_path": "notes.md"}),
        (_host(), "analyze_data_file", {"file_path": "a.csv"}),
        (_host(["analyze_data_file"]), "read_file", {"file_path": "a.csv"}),
        (_host(registry=("read_file",)), "read_file", {"file_path": "a.csv"}),
        (_host(), "read_file", {"file_path": "report.csv.md"}),
    ],
    ids=["not-a-table", "the-tool-itself", "already-analysed", "no-tool", "md-file"],
)
def test_no_hint_where_it_does_not_apply(host, tool, args):
    assert ChatAgent._table_read_by_hand(host, tool, args) == ""


def test_the_hint_rides_on_the_result():
    host = object.__new__(ChatAgent)
    host._turn_tool_executions = []
    host.observers = []
    registry = {"analyze_data_file": {}, "read_file": {}}
    with (
        patch.object(ChatAgent, "_tools_registry", new=property(lambda _: registry)),
        patch.object(ChatAgent, "_thin_tool_schemas", return_value=False),
        patch.object(Agent, "_execute_tool", return_value={"status": "success"}),
    ):
        result = ChatAgent._execute_tool(host, "read_file", {"file_path": "s.csv"})
    assert result["status"] == "success"
    assert "analyze_data_file" in result["next_step"]
    assert "file_path='s.csv'" in result["next_step"]


def _git_host(request_text):
    host = object.__new__(ChatAgent)
    host._turn_tool_executions = []
    host.observers = []
    host._original_user_input = request_text
    return host


def test_a_git_command_run_outside_the_named_repo_says_where_to_run_it(tmp_path):
    repo = tmp_path / "ledgerlite"
    (repo / ".git").mkdir(parents=True)
    (repo / "src").mkdir()
    host = _git_host(f"In {repo / 'src'} what does git blame say about line 3?")
    failed = {"status": "error", "stderr": "fatal: not a git repository"}
    hint = ChatAgent._repo_the_command_missed(
        host, "run_shell_command", {"command": "git blame config.py"}, failed
    )
    assert hint == str(repo)


@pytest.mark.parametrize(
    "args, result",
    [
        (
            {"command": "git status", "working_directory": "C:/x"},
            "not a git repository",
        ),
        ({"command": "git status"}, "On branch main"),
    ],
    ids=["working-directory-given", "it-worked"],
)
def test_no_git_hint_where_it_does_not_apply(tmp_path, args, result):
    repo = tmp_path / "r"
    (repo / ".git").mkdir(parents=True)
    host = _git_host(f"check {repo}")
    assert (
        ChatAgent._repo_the_command_missed(host, "run_shell_command", args, result)
        == ""
    )
