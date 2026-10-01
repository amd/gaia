# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""`gaia chat -q` must print only the answer on stdout, so it can be scripted."""

import asyncio
import logging
import sys
import types

import pytest

from gaia.logger import AGENT_LOG_ENV, log_manager

ANSWER = "🧠 gaia: 391"


class _StubAgent:
    """Answers like the real agent and logs the diagnostics the real one does."""

    def __init__(self, config):
        self.config = config
        self.current_session = object()

    def process_query(self, query, trace=False):
        logging.getLogger("gaia.agents.base.skill_loader").info("SKILL_LOADER {}")
        logging.getLogger("gaia.agents.base.tool_loader").info(
            'TOOL_LOADER {"scores": {}, "admitted": []}'
        )
        logging.getLogger("gaia.agents.base.memory").info(
            "[MemoryMixin] extraction started"
        )
        print(ANSWER)
        return {"status": "success"}

    def stop_watching(self):
        pass


class _StubConfig:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


@pytest.fixture
def stub_agent(monkeypatch):
    """Serve the stub from both the chat wheel and the flagship wheel."""
    for pkg, mod, cls, cfg in (
        ("gaia_agent_chat", "agent", "ChatAgent", "ChatAgentConfig"),
        ("gaia_agent", "agent", "GaiaAgent", "GaiaAgentConfig"),
    ):
        agent_mod = types.ModuleType(f"{pkg}.{mod}")
        setattr(agent_mod, cls, _StubAgent)
        setattr(agent_mod, cfg, _StubConfig)
        monkeypatch.setitem(sys.modules, pkg, types.ModuleType(pkg))
        monkeypatch.setitem(sys.modules, f"{pkg}.{mod}", agent_mod)
    app_mod = types.ModuleType("gaia_agent_chat.app")
    app_mod.interactive_mode = lambda agent: None
    monkeypatch.setitem(sys.modules, "gaia_agent_chat.app", app_mod)


@pytest.fixture
def console_logging():
    """Restore the global console and file handlers the chat path reconfigures."""
    handler = log_manager.console_handler
    saved = (handler.stream, handler.level, log_manager.file_handler)
    saved_log_file = log_manager.log_file
    root = logging.getLogger()
    saved_root_handlers = list(root.handlers)
    yield
    if log_manager.file_handler is not saved[2]:
        log_manager.file_handler.close()
    handler.stream = saved[0]
    handler.setLevel(saved[1])
    log_manager.file_handler = saved[2]
    log_manager.log_file = saved_log_file
    root.handlers = saved_root_handlers


def _run_one_shot(**kwargs):
    from gaia.cli import async_main

    # As in a real run: the console handler starts on stdout at INFO. Set here,
    # not in a fixture — capsys's stdout is only final inside the test body.
    log_manager.console_handler.stream = sys.stdout
    log_manager.console_handler.setLevel(logging.NOTSET)
    return asyncio.run(
        async_main(
            "chat",
            query="What is 17 times 23? Answer with just the number.",
            model="stub-model",
            device="gpu",
            no_lemonade_check=True,
            **kwargs,
        )
    )


def test_one_shot_stdout_is_only_the_answer(
    stub_agent, console_logging, capsys, tmp_path, monkeypatch
):
    log_file = tmp_path / "agent.log"
    monkeypatch.setenv(AGENT_LOG_ENV, str(log_file))

    assert _run_one_shot() == 0

    captured = capsys.readouterr()
    assert captured.out == ANSWER + "\n"
    assert "TOOL_LOADER" not in captured.err
    logged = log_file.read_text(encoding="utf-8")
    for line in ("SKILL_LOADER", "TOOL_LOADER", "[MemoryMixin] extraction started"):
        assert line in logged


def test_debug_shows_diagnostics_on_stderr_not_stdout(
    stub_agent, console_logging, capsys, monkeypatch
):
    monkeypatch.delenv(AGENT_LOG_ENV, raising=False)

    assert _run_one_shot(debug=True) == 0

    captured = capsys.readouterr()
    assert captured.out == ANSWER + "\n"
    assert "TOOL_LOADER" in captured.err
