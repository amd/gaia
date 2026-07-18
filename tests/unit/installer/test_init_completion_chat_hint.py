# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""`gaia init`'s completion banner must not promise `gaia chat` when the chat
agent isn't installed (#2240).

Today `_print_completion()` prints `gaia chat` / `gaia chat --ui` unconditionally
in the chat/npu/default-else branches, even on a bare `pip install amd-gaia`
with no chat agent wheel present — the exact dead end the issue reports.

Detection must use `importlib.util.find_spec("gaia_agent_chat")` (a real
import would transitively drag in the RAG/SD/VLM/MCP mixins, and a broken
transitive dependency would misreport as "chat agent not installed" — see
CLAUDE.md's fail-loudly rule). Every test below patches
`importlib.util.find_spec` directly (rather than some
`gaia.installer.init_command`-local re-export) on the assumption the
implementation calls it as qualified `importlib.util.find_spec(...)`
(`import importlib.util`, not `from importlib.util import find_spec`) — the
same qualified-access convention the plan mandates so the patch actually
takes effect regardless of which module does the check.

The `minimal` profile is explicitly out of scope (#2240) — it already only
recommends `gaia llm 'Hello'` plus `gaia init --profile chat`, and must not
grow a find_spec guard.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gaia.installer.init_command import InitCommand


def _printed_strings(mock_console: MagicMock) -> str:
    """Join only the string args passed to console.print().

    Rich's completion header is a `Panel(...)` object, not a string — its
    default `str()` embeds a live memory address (`<rich.panel.Panel object
    at 0x...>`), which differs across otherwise-identical runs and would
    make any equality comparison over the full printed text flaky. Only the
    plain-string print() calls (the actual quick-start lines) are relevant
    to what this test suite asserts.
    """
    return "\n".join(
        c.args[0]
        for c in mock_console.print.call_args_list
        if c.args and isinstance(c.args[0], str)
    )


def _rich_output(profile: str, chat_agent_installed: bool) -> str:
    """Render the rich-console completion banner and return the printed text."""
    cmd = InitCommand(profile=profile, yes=True)
    cmd.console = MagicMock()
    find_spec_result = object() if chat_agent_installed else None
    with patch("importlib.util.find_spec", return_value=find_spec_result):
        cmd._print_completion()
    return _printed_strings(cmd.console)


def _plain_output(profile: str, chat_agent_installed: bool, capsys) -> str:
    """Render the plain-text completion banner (RICH_AVAILABLE forced False)."""
    cmd = InitCommand(profile=profile, yes=True)
    find_spec_result = object() if chat_agent_installed else None
    with (
        patch("gaia.installer.init_command.RICH_AVAILABLE", False),
        patch("importlib.util.find_spec", return_value=find_spec_result),
    ):
        cmd._print_completion()
    return capsys.readouterr().out


@pytest.mark.parametrize("profile", ["chat", "npu", "all"])
def test_rich_banner_unchanged_when_chat_agent_present(profile):
    """The "all" profile exercises the default/else branch — all three recommend gaia chat."""
    printed = _rich_output(profile, chat_agent_installed=True)
    assert "gaia chat" in printed
    assert "not installed" not in printed.lower()


@pytest.mark.parametrize("profile", ["chat", "npu", "all"])
def test_rich_banner_shows_install_hint_before_chat_commands_when_absent(profile):
    printed = _rich_output(profile, chat_agent_installed=False)
    assert "not installed" in printed.lower()
    # The install hint text must appear before the `gaia chat` quick-start
    # line, not after — a user reading top-to-bottom needs the install step
    # first.
    install_pos = printed.lower().find("not installed")
    chat_cmd_pos = printed.find("gaia chat")
    assert install_pos != -1 and chat_cmd_pos != -1
    assert install_pos < chat_cmd_pos
    # And the hint must be truthful — no broken advice (#2240).
    assert "amd-gaia[agents]" not in printed
    assert "pip install gaia-agent-" not in printed


@pytest.mark.parametrize("profile", ["chat", "npu", "all"])
def test_plain_banner_shows_install_hint_before_chat_commands_when_absent(
    profile, capsys
):
    printed = _plain_output(profile, chat_agent_installed=False, capsys=capsys)
    assert "not installed" in printed.lower()
    install_pos = printed.lower().find("not installed")
    chat_cmd_pos = printed.find("gaia chat")
    assert install_pos != -1 and chat_cmd_pos != -1
    assert install_pos < chat_cmd_pos
    assert "amd-gaia[agents]" not in printed
    assert "pip install gaia-agent-" not in printed


@pytest.mark.parametrize("profile", ["chat", "npu", "all"])
def test_plain_banner_unchanged_when_chat_agent_present(profile, capsys):
    printed = _plain_output(profile, chat_agent_installed=True, capsys=capsys)
    assert "gaia chat" in printed
    assert "not installed" not in printed.lower()


def test_minimal_profile_never_calls_find_spec_and_is_unaffected():
    """Minimal is explicitly out of scope for the chat-agent guard (#2240)."""
    cmd = InitCommand(profile="minimal", yes=True)
    cmd.console = MagicMock()
    with patch("importlib.util.find_spec") as mock_find_spec:
        cmd._print_completion()
    mock_find_spec.assert_not_called()

    printed = _printed_strings(cmd.console)
    assert "gaia llm" in printed
    assert "gaia init --profile chat" in printed
    assert "not installed" not in printed.lower()


def test_minimal_profile_output_identical_regardless_of_chat_agent_presence():
    outputs = []
    for installed in (True, False):
        cmd = InitCommand(profile="minimal", yes=True)
        cmd.console = MagicMock()
        find_spec_result = object() if installed else None
        with patch("importlib.util.find_spec", return_value=find_spec_result):
            cmd._print_completion()
        outputs.append(_printed_strings(cmd.console))
    assert outputs[0] == outputs[1]
