# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A UI turn must never block on input() printed to the server's terminal."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.console import AgentConsole, SilentConsole
from gaia.api.sse_handler import SSEOutputHandler as ApiSSEOutputHandler
from gaia.security import PathValidator
from gaia.ui.sse_handler import SSEOutputHandler as UiSSEOutputHandler


@pytest.mark.parametrize(
    "console_cls, expected",
    [
        (AgentConsole, True),
        (SilentConsole, False),
        (UiSSEOutputHandler, False),
        (ApiSSEOutputHandler, False),
    ],
)
def test_only_the_terminal_console_accepts_stdin_prompts(console_cls, expected):
    agent = SimpleNamespace(console=console_cls.__new__(console_cls))
    assert Agent._console_accepts_stdin_prompts(agent) is expected


def test_no_console_means_no_prompt():
    assert Agent._console_accepts_stdin_prompts(SimpleNamespace()) is False


def _validator(tmp_path, requester_on_stdin):
    return PathValidator(
        allowed_paths=[str(tmp_path)],
        interactive_check=requester_on_stdin,
    )


def test_server_console_auto_denies_even_when_server_has_a_tty(tmp_path):
    validator = _validator(tmp_path, lambda: False)
    with (
        patch("gaia.security._is_interactive", return_value=True),
        patch("builtins.input", side_effect=AssertionError("prompted the server")),
    ):
        assert validator._prompt_user_for_access(tmp_path / "elsewhere") is False


def test_server_console_overwrite_does_not_prompt(tmp_path):
    existing = tmp_path / "f.txt"
    existing.write_text("x")
    validator = _validator(tmp_path, lambda: False)
    with (
        patch("gaia.security._is_interactive", return_value=True),
        patch("builtins.input", side_effect=AssertionError("prompted the server")),
    ):
        assert validator._prompt_overwrite(existing, 1) is True


def test_terminal_console_still_prompts(tmp_path):
    validator = _validator(tmp_path, lambda: True)
    with (
        patch("gaia.security._is_interactive", return_value=True),
        patch("builtins.input", return_value="y"),
    ):
        assert validator._prompt_user_for_access(tmp_path / "elsewhere") is True


def test_no_tty_never_prompts_even_for_terminal_console(tmp_path):
    validator = _validator(tmp_path, lambda: True)
    with (
        patch("gaia.security._is_interactive", return_value=False),
        patch("builtins.input", side_effect=AssertionError("prompted without a tty")),
    ):
        assert validator._prompt_user_for_access(tmp_path / "elsewhere") is False


def test_console_swap_is_honoured_per_prompt(tmp_path):
    """The UI cache-hit path swaps agent.console after the validator exists."""
    agent = SimpleNamespace(console=AgentConsole.__new__(AgentConsole))
    validator = _validator(
        tmp_path, lambda: Agent._console_accepts_stdin_prompts(agent)
    )
    agent.console = UiSSEOutputHandler.__new__(UiSSEOutputHandler)
    with (
        patch("gaia.security._is_interactive", return_value=True),
        patch("builtins.input", side_effect=AssertionError("prompted the server")),
    ):
        assert validator._prompt_user_for_access(tmp_path / "elsewhere") is False


# End-to-end through is_path_allowed. It swallows exceptions, so prompting is
# detected by recording input() calls (and answering "y"), not by raising.


@pytest.fixture()
def project_layout(tmp_path, monkeypatch):
    """A project dir, a path outside it, and a private ~ for persisted grants."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    project = tmp_path / "project"
    project.mkdir()
    return project, tmp_path / "outside" / "secret.txt"


def _agent_validator(project, console_cls):
    agent = SimpleNamespace(console=console_cls.__new__(console_cls))
    return PathValidator(
        allowed_paths=[str(project)],
        interactive_check=lambda: Agent._console_accepts_stdin_prompts(agent),
    )


def test_ui_turn_is_denied_without_prompting_the_server_tty(project_layout):
    project, outside = project_layout
    validator = _agent_validator(project, UiSSEOutputHandler)
    prompts = []
    with (
        patch("gaia.security._is_interactive", return_value=True),
        patch("builtins.input", side_effect=lambda *a: prompts.append(a) or "y"),
    ):
        allowed = validator.is_path_allowed(str(outside))
    assert prompts == [], "the UI turn blocked on the server's stdin"
    assert allowed is False


def test_cli_turn_still_prompts_and_the_answer_is_honoured(project_layout):
    project, outside = project_layout
    validator = _agent_validator(project, AgentConsole)
    prompts = []
    with (
        patch("gaia.security._is_interactive", return_value=True),
        patch("builtins.input", side_effect=lambda *a: prompts.append(a) or "y"),
    ):
        allowed = validator.is_path_allowed(str(outside))
    assert len(prompts) == 1
    assert allowed is True


def test_chat_agent_validator_asks_its_console_before_prompting(monkeypatch):
    """The UI fix only holds if ChatAgent hands its validator the hook."""
    chat = pytest.importorskip("gaia_agent_chat")
    config = chat.ChatAgentConfig(
        silent_mode=True,
        enable_browser=False,
        enable_filesystem=False,
        enable_scratchpad=False,
    )
    with (
        patch("gaia_agent_chat.agent.RAGSDK"),
        patch("gaia_agent_chat.agent.RAGConfig"),
    ):
        agent = chat.ChatAgent(config)
    monkeypatch.setattr("gaia.security._is_interactive", lambda: True)

    agent.console = SimpleNamespace(supports_stdin_prompts=False)
    assert agent.path_validator._can_prompt() is False

    agent.console = SimpleNamespace(supports_stdin_prompts=True)
    assert agent.path_validator._can_prompt() is True
