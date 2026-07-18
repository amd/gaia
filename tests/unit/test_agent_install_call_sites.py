# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Integration-style locks on a couple of real agent-not-installed call
sites (#2240). These exercise the actual code paths in ``gaia.cli`` and
``gaia.api.agent_registry`` — no mocking of the import failure itself is
needed, because ``gaia_agent_chat`` / ``gaia_agent_routing`` are genuinely
not installed in the unit-test environment (they ship as separate hub
wheels), so the real ``ImportError`` fires naturally.

These are a spot-check, not the full backstop — ``test_agent_install_hints.py``
(Increment 4's repo-wide guard test) is what prevents every other one of the
~20 call sites from reintroducing the same broken advice.
"""

from __future__ import annotations

import pytest

from gaia.cli import async_main


async def test_cli_chat_action_not_installed_message_is_truthful():
    """`gaia chat` with no chat agent installed must not advise a broken
    install path, and must point at `gaia chat --ui` per #2240."""
    with pytest.raises(RuntimeError) as excinfo:
        await async_main("chat", no_lemonade_check=True)
    message = str(excinfo.value)
    assert "amd-gaia[agents]" not in message
    assert "pip install gaia-agent-" not in message
    assert "uv pip install gaia-agent-" not in message
    assert "gaia chat --ui" in message
    # Working install path, pinned to the running core's version.
    assert "git+https://github.com/amd/gaia.git@v" in message
    assert "#subdirectory=hub/agents/python/chat" in message


def test_api_agent_registry_gaia_code_hint_is_truthful():
    """AgentRegistry.get_agent("gaia-code") must not advise a broken path
    when gaia-agent-routing/gaia-agent-code aren't installed (#2240)."""
    from gaia.api.agent_registry import AgentRegistry

    with pytest.raises(ValueError) as excinfo:
        AgentRegistry().get_agent("gaia-code")
    message = str(excinfo.value)
    assert "amd-gaia[agents]" not in message
    assert "pip install gaia-agent-" not in message
    assert "uv pip install gaia-agent-" not in message
    assert "git+https://github.com/amd/gaia.git@v" in message


def test_cli_blender_branch_local_extra_untouched():
    """The blender install hint is a REAL local extra (setup.py `[blender]`)
    and must not be swept into the shared agent_not_installed_message()
    helper or the git-subdirectory install advice (#2240 explicitly
    excludes it)."""
    from pathlib import Path

    import gaia.cli as cli_module

    source = Path(cli_module.__file__).read_text(encoding="utf-8")
    assert 'uv pip install -e ".[blender]"' in source
