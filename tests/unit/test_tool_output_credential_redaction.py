# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Credentials in shell and Python output are masked before the model reads it.

``git remote -v`` prints a remote's URL with whatever token it was cloned
with. The command still works; the token never reaches the conversation.
"""

from __future__ import annotations

import shutil
import subprocess
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.agents.tools.shell_tools import ShellToolsMixin
from gaia.credential_shapes import (
    HIDDEN_LINE,
    REDACTED,
    mask_credential_file_lines,
    redact_credentials,
    redact_tool_output,
)
from gaia.llm import failure_record


# Assembled at runtime so secret scanners don't flag test fixtures.
def _fake(prefix: str, body: str) -> str:
    return prefix + body


GHP = _fake("ghp_", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4")
PAT = _fake("github_pat_", "11ABCDEFG0123456789_abcdefghijklmnop")
GLPAT = _fake("glpat-", "abcdefghij0123456789")
AKIA = _fake("AKIA", "ABCDEFGHIJKLMNOP")
DB_PW = _fake("DB_PASSWORD=", "hunter2")
API_SECRET = _fake("API_SECRET=", "s3cr3t")
PEM = _fake("-----BEGIN PRIVATE", " KEY-----")


@pytest.mark.parametrize(
    "text, secret",
    [
        (f"origin\thttps://user:{GHP}@github.com/amd/gaia.git (fetch)", GHP),
        (f"origin\thttps://x-access-token:{PAT}@github.com/a/b (push)", PAT),
        (f"origin https://{GHP}@github.com/a/b.git", GHP),
        (
            _fake("remote ssh://deploy:", "hunter2pass@git.example.com:2222/r.git"),
            "hunter2pass",
        ),
        (f"GET https://api.example.com/v1/items?access_token={GLPAT}&page=2", GLPAT),
        (
            _fake("GET https://api.example.com/v1/items?page=2&TOKEN=", "s3cr3tvalue1"),
            "s3cr3tvalue1",
        ),
        (_fake("HTTPS://User:", "Pa55w0rdXYZ@Example.COM/x"), "Pa55w0rdXYZ"),
        (f"export AWS_ACCESS_KEY_ID={AKIA}", AKIA),
        (f"token: {GHP.upper().replace('GHP_', 'ghp_')}", GHP.upper()[4:]),
        (f"GITLAB={GLPAT.upper().replace('GLPAT-', 'glpat-')}", GLPAT.upper()[6:]),
        (f"pat={PAT}", PAT),
    ],
)
def test_credentials_are_masked(text, secret):
    out = redact_credentials(text)

    assert secret not in out
    assert REDACTED in out


@pytest.mark.parametrize(
    "text",
    [
        "origin\tgit@github.com:amd/gaia.git (fetch)",
        "Author: Jane Doe <jane.doe@example.com>",
        "see https://github.com/amd/gaia/pull/12 for details",
        "https://www.npmjs.com/package/@scope/pkg",
        "GET https://api.example.com/v1/items?page=2&sort=desc",
        "max_tokens=512 tokens_per_second=31.2",
        "ghp_short",
        "mailto:someone@example.com",
        _fake("task-sk-", "abcdefghijklmnopqrstuvwxyz0123"),
        _fake("build_sk-", "abcdefghijklmnopqrstuvwxyz0123"),
    ],
)
def test_ordinary_text_is_left_alone(text):
    assert redact_credentials(text) == text


def test_nested_results_are_masked_and_rebuilt():
    result = {
        "status": "success",
        "stdout": f"https://u:{GHP}@github.com/a/b",
        "steps": [{"output": f"key {AKIA}"}],
        "return_code": 0,
    }

    out = redact_tool_output(result)

    assert GHP not in str(out) and AKIA not in str(out)
    assert out["return_code"] == 0 and out["status"] == "success"


def test_failure_records_mask_the_shared_shapes():
    out = failure_record.redact(f"clone https://{GHP}@github.com/a/b and {AKIA}")

    assert GHP not in out and AKIA not in out


GREP_OUTPUT = (
    "./src/app.py:12:# TODO: retry\n"
    f"./.env:3:{DB_PW}\n"
    f"./config/.env.local-4-{API_SECRET}\n"
    "./README.md:7:TODO list\n"
)


@pytest.mark.parametrize(
    "line",
    [
        f"./.env:3:{DB_PW}",
        f".env:{DB_PW}",
        f"sub/dir/.env.production:1:{DB_PW}",
        f"./config/.env.local-4-{API_SECRET}",
        f"certs/server.key:1:{PEM}",
        f"certs/server.pem:{PEM}",
        f"deploy/tls/site.KEY:{PEM}",
        'proj\\secrets.json:2:  "token": "abc"',
        f"> proj\\.env:1:{DB_PW}",
        f"  proj\\.env:2:{API_SECRET}",
        f"./.env\x00{DB_PW}",
        f"3:./.env:{DB_PW}",
        f"3:.env:{DB_PW}",
        f"\x1b[35m./.env\x1b[m\x1b[36m:\x1b[m3:{DB_PW}",
        "C:\\work\\app\\.npmrc:1://registry/:_authToken=x",
    ],
)
def test_a_line_from_a_credential_file_is_hidden(line):
    out = mask_credential_file_lines(line + "\n")

    for secret in ("hunter2", "s3cr3t", '"abc"', "PRIVATE KEY", "_authToken"):
        assert secret not in out, line
    assert HIDDEN_LINE in out
    assert out.endswith("\n")


@pytest.mark.parametrize(
    "line",
    [
        "./src/app.py:12:# TODO: retry",
        "./.env.example:1:DB_PASSWORD=",
        "docs/env.md:3:put DB_PASSWORD in .env",
        ".gitignore:1:.env",
        "./README.md:7:copy .env.example to .env",
        "src/settings.py-40-load_dotenv('.env')",
        " M .env",
        "ls: cannot access '.env': No such file or directory",
        "",
    ],
)
def test_other_lines_are_left_alone(line):
    assert mask_credential_file_lines(line) == line


def test_a_search_keeps_its_other_lines():
    out = mask_credential_file_lines(GREP_OUTPUT)

    assert "hunter2" not in out and "s3cr3t" not in out
    assert "./src/app.py:12:# TODO: retry\n" in out
    assert "./README.md:7:TODO list\n" in out
    assert out.count(HIDDEN_LINE) == 2


class _OutputAgent(Agent):
    """A base agent whose output tools print what they are told to."""

    def _get_system_prompt(self):
        return "probe"

    def _register_tools(self):
        @tool
        def run_shell_command(command: str) -> dict:
            """Print a canned remote list."""
            return {
                "status": "success",
                "stdout": f"origin\thttps://user:{GHP}@github.com/amd/gaia.git (fetch)",
                "command": command,
            }

        @tool
        def run_python(code: str) -> dict:
            """Print a canned key."""
            return {"status": "success", "stdout": f"{AKIA}\n{PAT}\n"}

        @tool
        def execute_python_file(file_path: str) -> dict:
            """Print what a recursive search of a folder holding .env prints."""
            return {"status": "success", "stdout": GREP_OUTPUT}

        @tool
        def read_notes(name: str) -> dict:
            """A tool whose output is not masked here."""
            return {"status": "success", "content": f"{name} {GHP}"}


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    saved = dict(_TOOL_REGISTRY)
    with patch("gaia.agents.base.agent.AgentSDK"):
        built = _OutputAgent(silent_mode=True, skip_lemonade=True)
    built.console.auto_approve_gated_tools = True
    try:
        yield built
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def test_git_remote_output_keeps_working_with_the_token_masked(agent):
    result = agent._execute_tool("run_shell_command", {"command": "git remote -v"})

    assert result["status"] == "success"
    assert GHP not in str(result)
    assert "github.com/amd/gaia.git (fetch)" in result["stdout"]


def test_python_output_is_masked(agent):
    result = agent._execute_tool("run_python", {"code": "print(1)"})

    assert AKIA not in result["stdout"] and PAT not in result["stdout"]


def test_search_output_hides_credential_file_lines(agent):
    result = agent._execute_tool("execute_python_file", {"file_path": "search.py"})

    assert "hunter2" not in result["stdout"] and "s3cr3t" not in result["stdout"]
    assert "./src/app.py:12:# TODO: retry" in result["stdout"]


def test_other_tools_are_not_rewritten(agent):
    result = agent._execute_tool("read_notes", {"name": "x"})

    assert GHP in result["content"]


class _ShellAgent(Agent, ShellToolsMixin):
    """The real shell tool on the base agent."""

    def _get_system_prompt(self):
        return "probe"

    def _register_tools(self):
        self.register_shell_tools()


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_real_git_remote_v_runs_with_the_token_masked(tmp_path, monkeypatch):
    """Local only: a remote is added, never contacted."""
    repo = tmp_path / "repo"
    repo.mkdir()
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(tmp_path))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "remote",
            "add",
            "origin",
            f"https://user:{GHP}@github.com/amd/gaia.git",
        ],
        check=True,
    )
    saved = dict(_TOOL_REGISTRY)
    try:
        with patch("gaia.agents.base.agent.AgentSDK"):
            agent = _ShellAgent(silent_mode=True, skip_lemonade=True)
        agent.console.full_access = True
        agent.console.auto_approve_gated_tools = True
        result = agent._execute_tool(
            "run_shell_command",
            {"command": "git remote -v", "working_directory": str(repo)},
        )
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)

    assert result["status"] == "success", result
    assert GHP not in str(result)
    assert "github.com/amd/gaia.git (fetch)" in str(result)
