# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Credentials in file text are masked before the model reads it.

Reading ``.git/config`` shows a remote's URL with its token as ``[redacted]``.
Only the view is masked: the file keeps its token, and no write or edit may
put the marker where the token was.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import FILE_CONTENT_TOOLS, Agent
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_edit import masked_secret_refusal
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.agents.tools.file_tools import FileSearchToolsMixin
from gaia.credential_shapes import (
    HIDDEN_LINE,
    REDACTED,
    holds_credentials,
    redact_file_content,
    redact_file_text,
)
from gaia.security import PathValidator

GHP = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
GLPAT = "glpat-" + "abcdefghij0123456789"
REMOTE = f"https://user:{GHP}@github.com/amd/gaia.git"
GIT_CONFIG = (
    "[core]\n"
    "\trepositoryformatversion = 0\n"
    '[remote "origin"]\n'
    f"\turl = {REMOTE}\n"
    "\tfetch = +refs/heads/*:refs/remotes/origin/*\n"
    '[branch "main"]\n'
    "\tremote = origin\n"
)
PLAIN_CONFIG = (
    "[server]\n"
    "host = 127.0.0.1\n"
    "port = 8080\n"
    "docs = https://amd-gaia.ai/docs/guides/chat\n"
    "remote = git@github.com:amd/gaia.git\n"
    ".env: listed here is not a search prefix\n"
)


class _IOHost(Agent, FileIOToolsMixin):
    def __init__(self, **kwargs):
        self.path_validator = PathValidator()
        super().__init__(**kwargs)

    def _get_system_prompt(self):
        return "probe"

    def _register_tools(self):
        self.register_file_io_tools()


class _SearchHost(Agent, FileSearchToolsMixin):
    def __init__(self, **kwargs):
        self.path_validator = PathValidator()
        super().__init__(**kwargs)

    def _get_system_prompt(self):
        return "probe"

    def _register_tools(self):
        self.register_file_search_tools()


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


@pytest.fixture(params=[_IOHost, _SearchHost], ids=["file_io", "file_search"])
def agent(request, tmp_path, monkeypatch):
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home" / ".gaia"))
    monkeypatch.chdir(tmp_path)
    with patch("gaia.agents.base.agent.AgentSDK"):
        host = request.param(skip_lemonade=True, silent_mode=True)
    host.path_validator.allowed_paths.add(tmp_path.resolve())
    host.console.auto_approve_gated_tools = True
    host.console.full_access = True
    return host


@pytest.fixture
def git_config(tmp_path) -> Path:
    path = tmp_path / "repo" / ".git" / "config"
    path.parent.mkdir(parents=True)
    path.write_bytes(GIT_CONFIG.encode("utf-8"))
    return path


@pytest.fixture
def remotes(tmp_path) -> Path:
    """The same text in a file the write tools may change (``.git`` is write-blocked)."""
    path = tmp_path / "remotes.cfg"
    path.write_bytes(GIT_CONFIG.encode("utf-8"))
    return path


def _read(agent, path: Path) -> dict:
    result = agent._execute_tool("read_file", {"file_path": str(path)})
    assert result["status"] == "success", result
    return result


def test_read_file_masks_a_token_in_git_config(agent, git_config):
    result = _read(agent, git_config)

    assert GHP not in str(result)
    assert f"url = https://{REDACTED}@github.com/amd/gaia.git" in result["content"]
    assert git_config.read_bytes() == GIT_CONFIG.encode("utf-8")


def test_read_file_leaves_a_normal_config_unchanged(agent, tmp_path):
    path = tmp_path / "app.ini"
    path.write_bytes(PLAIN_CONFIG.encode("utf-8"))

    result = _read(agent, path)

    assert REDACTED not in str(result) and HIDDEN_LINE not in str(result)
    assert "host = 127.0.0.1" in result["content"]
    assert "git@github.com:amd/gaia.git" in result["content"]
    assert ".env: listed here is not a search prefix" in result["content"]


def test_edit_cannot_write_the_marker_over_a_token(agent, remotes):
    content = _read(agent, remotes)["content"]
    masked_line = next(line for line in content.splitlines() if "url =" in line)
    masked_line = masked_line.split("|", 1)[-1].strip()

    result = agent._execute_tool(
        "edit_file",
        {
            "file_path": str(remotes),
            "old_content": "[core]",
            "new_content": f"[core]\n\turl = {masked_line}",
        },
    )

    assert result["status"] == "error"
    assert result["error_type"] == "redacted_secret"
    assert remotes.read_bytes() == GIT_CONFIG.encode("utf-8")


def test_edit_whose_search_text_is_the_masked_view_is_refused(agent, remotes):
    _read(agent, remotes)

    result = agent._execute_tool(
        "edit_file",
        {
            "file_path": str(remotes),
            "old_content": f"\turl = https://{REDACTED}@github.com/amd/gaia.git",
            "new_content": "\turl = https://github.com/amd/gaia.git",
        },
    )

    assert result["status"] == "error"
    assert result["error_type"] == "redacted_secret"
    assert remotes.read_bytes() == GIT_CONFIG.encode("utf-8")


def test_rewriting_the_whole_file_from_the_masked_view_is_refused(agent, remotes):
    _read(agent, remotes)
    masked = GIT_CONFIG.replace(f"user:{GHP}", REDACTED)

    result = agent._execute_tool(
        "write_file", {"file_path": str(remotes), "content": masked}
    )

    assert result["status"] == "error"
    assert result["error_type"] == "redacted_secret"
    assert remotes.read_bytes() == GIT_CONFIG.encode("utf-8")


def test_an_edit_elsewhere_in_the_file_keeps_the_token(agent, remotes):
    _read(agent, remotes)

    result = agent._execute_tool(
        "edit_file",
        {
            "file_path": str(remotes),
            "old_content": "\tremote = origin\n",
            "new_content": "\tremote = origin\n\tmerge = refs/heads/main\n",
        },
    )

    assert result["status"] == "success", result
    assert GHP not in str(result)
    on_disk = remotes.read_text(encoding="utf-8")
    assert REMOTE in on_disk and REDACTED not in on_disk
    assert "merge = refs/heads/main" in on_disk


def test_a_rejected_edit_shows_its_excerpt_masked(agent, remotes):
    _read(agent, remotes)

    result = agent._execute_tool(
        "edit_file",
        {
            "file_path": str(remotes),
            "old_content": "\tfetch = +refs/heads/*:refs/remotes/upstream/*",
            "new_content": "\tfetch = +refs/heads/main",
        },
    )

    assert result["status"] == "error"
    assert GHP not in str(result)
    assert remotes.read_bytes() == GIT_CONFIG.encode("utf-8")


def test_git_config_stays_unwritable_from_the_masked_view(agent, git_config):
    _read(agent, git_config)

    result = agent._execute_tool(
        "write_file",
        {
            "file_path": str(git_config),
            "content": GIT_CONFIG.replace(f"user:{GHP}", REDACTED),
        },
    )

    assert result["status"] == "error"
    assert git_config.read_bytes() == GIT_CONFIG.encode("utf-8")


def test_a_file_that_really_holds_the_marker_stays_editable(agent, tmp_path):
    path = tmp_path / "check_masking.py"
    path.write_text(f'MARKER = "{REDACTED}"\n', encoding="utf-8")
    _read(agent, path)

    result = agent._execute_tool(
        "edit_file",
        {
            "file_path": str(path),
            "old_content": f'MARKER = "{REDACTED}"',
            "new_content": f'MARKER = "{REDACTED}"\nOTHER = "{REDACTED}"',
        },
    )

    assert result["status"] == "success", result
    assert path.read_text(encoding="utf-8").count(REDACTED) == 2


def test_a_new_file_may_contain_the_marker(agent, tmp_path):
    path = tmp_path / "notes.md"

    result = agent._execute_tool(
        "write_file",
        {"file_path": str(path), "content": f"Tokens show as {REDACTED}.\n"},
    )

    assert result["status"] == "success", result
    assert REDACTED in path.read_text(encoding="utf-8")


def test_search_results_mask_the_token(tmp_path, monkeypatch, git_config):
    monkeypatch.chdir(tmp_path)
    with patch("gaia.agents.base.agent.AgentSDK"):
        host = _SearchHost(skip_lemonade=True, silent_mode=True)
    host.path_validator.allowed_paths.add(tmp_path.resolve())

    result = host._execute_tool(
        "search_file_content",
        {"pattern": "github.com", "directory": str(git_config.parent)},
    )

    assert result["status"] == "success", result
    assert result["matches"], result
    assert GHP not in str(result)


@pytest.mark.parametrize(
    "args",
    [
        {"old_content": "a", "new_content": f"x {REDACTED}"},
        {"content": f"{REDACTED}\n"},
        {"new_implementation": f"def f():\n    return '{REDACTED}'\n"},
    ],
)
def test_every_written_argument_is_checked(tmp_path, args):
    path = tmp_path / "secret.txt"
    path.write_text(f"a\ntoken={GLPAT}\n", encoding="utf-8")

    refusal = masked_secret_refusal(path, args)

    assert refusal is not None and refusal["error_type"] == "redacted_secret"


@pytest.mark.parametrize(
    "args",
    [
        {"old_content": "a", "new_content": "b"},
        # The marker is moved, not added: the count is unchanged.
        {"old_content": f"x {REDACTED}", "new_content": f"{REDACTED} x"},
        {"old_content": "a", "new_content": "[REDACTED] differs in case"},
    ],
)
def test_writes_that_add_no_marker_pass(tmp_path, args):
    path = tmp_path / "secret.txt"
    path.write_text(f"a\nx {REDACTED}\ntoken={GLPAT}\n", encoding="utf-8")

    assert masked_secret_refusal(path, args) is None


def test_a_missing_file_is_left_to_the_tool(tmp_path):
    assert masked_secret_refusal(tmp_path / "absent.txt", {"content": REDACTED}) is None


def test_crlf_search_text_with_the_masked_view_is_refused(tmp_path):
    path = tmp_path / "remotes.cfg"
    path.write_bytes(GIT_CONFIG.replace("\n", "\r\n").encode("utf-8"))
    masked = f"\turl = https://{REDACTED}@github.com/amd/gaia.git\r\n"

    refusal = masked_secret_refusal(path, {"old_content": masked, "new_content": ""})

    assert refusal is not None and refusal["error_type"] == "redacted_secret"


def test_a_secret_query_param_is_masked_and_guarded(tmp_path):
    path = tmp_path / "hooks.txt"
    path.write_text(
        f"POST https://ci.example.com/hook?token={GLPAT}&x=1\n", encoding="utf-8"
    )
    masked = redact_file_content(path.read_text(encoding="utf-8"))

    assert GLPAT not in masked and f"token={REDACTED}&x=1" in masked
    refusal = masked_secret_refusal(path, {"content": masked})
    assert refusal is not None and refusal["error_type"] == "redacted_secret"


def test_file_content_masking_never_hides_lines():
    value = {"content": f".env:3:KEY=1\nurl = {REMOTE}\n", "lines": [GHP, 3]}

    out = redact_file_content(value)

    assert out["content"].startswith(".env:3:KEY=1\n")
    assert GHP not in str(out) and out["lines"][1] == 3
    assert holds_credentials(REMOTE) and not holds_credentials(PLAIN_CONFIG)


def test_reads_searches_and_edits_are_all_masked():
    for name in ("read_file", "search_file_content", "search_code", "edit_file"):
        assert name in FILE_CONTENT_TOOLS
    for name in ("write_file", "write_python_file", "run_shell_command"):
        assert name not in FILE_CONTENT_TOOLS


def test_mcp_file_preview_is_masked():
    pytest.importorskip("mcp")
    from gaia.mcp.servers import agent_ui_mcp

    with patch("requests.get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"mcp_memory_enabled": False}),
        )
        mock_get.return_value.raise_for_status.return_value = None
        server = agent_ui_mcp.create_agent_ui_mcp("http://127.0.0.1:1")
    preview_file = server._tool_manager._tools["preview_file"].fn
    preview = {"preview_lines": ["[remote]", f"url = {REMOTE}"], "is_text": True}
    with patch.object(agent_ui_mcp, "_api", return_value=preview):
        result = preview_file(filepath="/tmp/x")

    assert GHP not in str(result)
    assert result["preview_lines"][0] == "[remote]"


NOT_SECRETS = [
    '"repository": "git+ssh://git@github.com/amd/gaia.git"',
    "postgresql://postgres@localhost:5432/app",
    'requests.get(f"https://api.x.com/v1?key={api_key}")',
    "# sk-learn-compatible-estimator-wrapper",
    "https://user:${GIT_TOKEN}@github.com/amd/gaia.git",
    "https://user:password@example.com/x",
    "curl https://api.example.com/v1?token=YOUR_TOKEN_HERE",
    "url = https://example.com/x?api_key=%(key)s",
    "see https://example.com/items?key=abc",
    "https://user:MYPASSWORD@example.com/x",
    "https://user:$GIT_TOKEN@github.com/a.git",
    "https://user:<token>@github.com/a.git",
    "https://example.com/x?token={{ api_token }}",
    "https://example.com/x?access_token=YOUR_ACCESS_TOKEN",
    "fetch(`https://x.com/?api_key=${apiKey}`)",
    "requests.get(f'https://x.com/?token={token}')",
    "https://user:${DB_PASSWORD}@db/app",
]

SK_PROJ = "sk-proj-" + "Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4z"
SK_ANT = "sk-ant-api03-" + "Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4z"
HEX_TOKEN = "0123456789abcdef" * 2 + "01234567"
REAL_SECRETS = [
    (f"url = https://user:{GHP}@github.com/amd/gaia.git", GHP),
    ("postgresql://app:hunter2@db.internal:5432/app", "hunter2"),
    (f"https://x-access-token:{GLPAT}@gitlab.com/a/b.git", GLPAT),
    (f"https://{HEX_TOKEN}@github.com/amd/gaia.git", HEX_TOKEN),
    ("GET https://api.example.com/v1?access_token=Zq81mXv0Lp7c", "Zq81mXv0Lp7c"),
    (f"OPENAI_API_KEY={SK_PROJ}", SK_PROJ),
    (f"ANTHROPIC_API_KEY={SK_ANT}", SK_ANT),
    ("OLD_KEY=sk-" + "Ab3dEf6hIj9kLm2nOp5qRs8t", "Ab3dEf6hIj9kLm2nOp5qRs8t"),
    ("postgresql://app:p%40ssW0rd2024@db/app", "p%40ssW0rd2024"),
    ("postgresql://app:Pa$$w0rd2024@db/app", "Pa$$w0rd2024"),
    ("postgresql://app:{x}Pa55word@db/app", "Pa55word"),
    ("postgresql://app:HUNTERPASS@db/app", "HUNTERPASS"),
    ("mysql://root:$ecretP4ss@db/app", "$ecretP4ss"),
    ("mysql://root:${mixed_Case9}@db/app", "mixed_Case9"),
    (
        "https://acct.blob.core.windows.net/c/b?sv=2022-11-02&sp=r"
        "&sig=Xk2bLq9vPz4Rt7Yw1Mn5Cd8Ef3Gh6Jk0%2BaBcD%2FeFgH%3D%3D",
        "Xk2bLq9vPz4Rt7Yw1Mn5Cd8Ef3Gh6Jk0",
    ),
]


@pytest.mark.parametrize("line", NOT_SECRETS)
def test_secret_shaped_file_text_is_not_masked(line):
    assert redact_file_text(line) == line
    assert not holds_credentials(line)


@pytest.mark.parametrize("line, secret", REAL_SECRETS)
def test_real_secrets_in_file_text_are_still_masked(line, secret):
    out = redact_file_text(line)

    assert secret not in out and REDACTED in out
    assert holds_credentials(line)


@pytest.mark.parametrize("line", [*NOT_SECRETS[:4], NOT_SECRETS[-3]])
def test_a_file_with_secret_shaped_text_stays_editable(agent, tmp_path, line):
    path = tmp_path / "settings.txt"
    original = f"{line}\nname = demo\n"
    path.write_text(original, encoding="utf-8")
    shown = _read(agent, path)["content"]
    assert line in shown

    edited = agent._execute_tool(
        "edit_file",
        {
            "file_path": str(path),
            "old_content": f"{line}\nname = demo",
            "new_content": f"{line}\nname = renamed",
        },
    )
    assert edited["status"] == "success", edited
    _read(agent, path)
    rewritten = agent._execute_tool(
        "write_file",
        {"file_path": str(path), "content": f"{line}\nname = rewritten\n"},
    )

    assert rewritten["status"] == "success", rewritten
    assert path.read_text(encoding="utf-8") == f"{line}\nname = rewritten\n"


def test_gaia_md_instructions_cannot_write_the_marker_over_a_secret(tmp_path):
    path = tmp_path / "GAIA.md"
    path.write_text(f"# Demo\nremote: {REMOTE}\n", encoding="utf-8")

    refusal = masked_secret_refusal(
        path, {"instructions": f"Push to https://{REDACTED}@github.com/amd/gaia"}
    )

    assert refusal is not None and refusal["error_type"] == "redacted_secret"
