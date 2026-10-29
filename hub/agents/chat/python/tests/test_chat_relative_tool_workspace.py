# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""ChatAgent's inline ``list_files`` and ``execute_python_file`` resolve a
relative path in the chat's folder, not the process folder, and still cannot
leave the chat's allowed folders (#5988)."""

import os
import sys
from pathlib import Path

import pytest
from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.security import PathValidator

SECRET = "TOPSECRET-outside-the-chat"


def _junction(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


@pytest.fixture
def chat_tools(tmp_path, monkeypatch):
    workspace = tmp_path / "chat"
    sidecar = tmp_path / "sidecar"
    workspace.mkdir()
    sidecar.mkdir()
    monkeypatch.chdir(sidecar)
    monkeypatch.setattr(
        PathValidator, "_prompt_user_for_access", lambda self, path: False
    )
    saved = dict(_TOOL_REGISTRY)
    try:
        ChatAgent(
            ChatAgentConfig(
                silent_mode=True,
                allowed_paths=[str(workspace)],
                working_directory=str(workspace),
            )
        )
        tools = {name: entry["function"] for name, entry in _TOOL_REGISTRY.items()}
        yield tools, workspace, sidecar
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def test_inline_file_tools_use_the_chat_folder(chat_tools):
    tools, workspace, sidecar = chat_tools
    (workspace / "helper.py").write_text(
        "import os\nprint('chat', os.getcwd())\n", encoding="utf-8"
    )
    (sidecar / "helper.py").write_text("sidecar\n", encoding="utf-8")

    listing = tools["list_files"](".")
    result = tools["execute_python_file"]("helper.py")

    assert str(workspace.resolve()) == listing["path"]
    assert result["status"] == "success", result
    assert "chat" in result["stdout"]


def test_inline_file_tools_cannot_leave_the_chat_folder(chat_tools):
    tools, workspace, sidecar = chat_tools
    marker = sidecar / "ran.txt"
    (sidecar / "secret.txt").write_text(SECRET, encoding="utf-8")
    (sidecar / "evil.py").write_text(
        f"open({str(marker)!r}, 'w').write('x')\nprint({SECRET!r})\n",
        encoding="utf-8",
    )
    _junction(workspace / "link", sidecar)

    for folder in ("..", "../sidecar", "link", str(sidecar)):
        listing = tools["list_files"](folder)
        assert "secret.txt" not in str(listing), (folder, listing)

    for script in ("../sidecar/evil.py", "link/evil.py", str(sidecar / "evil.py")):
        result = tools["execute_python_file"](script)
        assert SECRET not in str(result), (script, result)
        assert not marker.exists(), script
