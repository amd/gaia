# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A failed `gaia chat --query` / `gaia stats` must exit non-zero.

The chat path used to hand its status back as an int that ``main()`` printed
as output and then exited 0 on — so `gaia chat -q ... && next-step` ran
next-step after a failure, with a stray ``1`` on stdout. These drive the real
``gaia.cli.main`` entry point because the exit code is the thing under test.
"""

import logging
import sys
import types

import pytest

from gaia import cli
from gaia.logger import AGENT_LOG_ENV, log_manager


class _StubConfig:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _stub_agent_class(outcome):
    class _StubAgent:
        def __init__(self, config):
            self.config = config
            self.current_session = object()

        def process_query(self, query, trace=False):
            if isinstance(outcome, BaseException):
                raise outcome
            return {"status": outcome}

        def stop_watching(self):
            pass

    return _StubAgent


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


@pytest.fixture
def stub_chat(monkeypatch, console_logging, tmp_path):
    """Install a stub agent in both the chat wheel and the flagship wheel."""
    monkeypatch.setenv(AGENT_LOG_ENV, str(tmp_path / "agent.log"))

    def install(outcome):
        agent_cls = _stub_agent_class(outcome)
        for pkg, cls, cfg in (
            ("gaia_agent_chat", "ChatAgent", "ChatAgentConfig"),
            ("gaia_agent", "GaiaAgent", "GaiaAgentConfig"),
        ):
            agent_mod = types.ModuleType(f"{pkg}.agent")
            setattr(agent_mod, cls, agent_cls)
            setattr(agent_mod, cfg, _StubConfig)
            monkeypatch.setitem(sys.modules, pkg, types.ModuleType(pkg))
            monkeypatch.setitem(sys.modules, f"{pkg}.agent", agent_mod)
        app_mod = types.ModuleType("gaia_agent_chat.app")
        app_mod.interactive_mode = lambda agent: None
        monkeypatch.setitem(sys.modules, "gaia_agent_chat.app", app_mod)

    return install


def _run_main(monkeypatch, *argv):
    """Run ``gaia <argv>`` through ``main()``; return its exit status."""
    monkeypatch.setattr(sys, "argv", ["gaia", *argv])
    try:
        cli.main()
    except SystemExit as exc:
        code = exc.code
        return 0 if code is None else code
    return 0


def _chat(monkeypatch):
    return _run_main(
        monkeypatch,
        "chat",
        "--query",
        "hi",
        "--model",
        "stub-model",
        "--device",
        "gpu",
        "--no-lemonade-check",
    )


def test_chat_query_success_exits_zero_without_printing_status(
    stub_chat, monkeypatch, capsys
):
    stub_chat("success")

    assert _chat(monkeypatch) == 0
    assert capsys.readouterr().out.strip() == ""


def test_chat_query_failure_exits_non_zero_without_stray_status(
    stub_chat, monkeypatch, capsys
):
    stub_chat("error")

    assert _chat(monkeypatch) == 1
    assert "1" not in capsys.readouterr().out.splitlines()


def test_chat_query_exception_exits_non_zero(stub_chat, monkeypatch, capsys):
    stub_chat(RuntimeError("backend exploded"))

    assert _chat(monkeypatch) == 1
    captured = capsys.readouterr()
    assert "1" not in captured.out.splitlines()
    assert "backend exploded" in captured.err


def test_chat_query_interrupt_exits_130(stub_chat, monkeypatch, capsys):
    stub_chat(KeyboardInterrupt())

    assert _chat(monkeypatch) == 130
    assert "130" not in capsys.readouterr().out


class _StubLLM:
    def __init__(self, stats):
        self._stats = stats

    def generate(self, **kwargs):
        yield "the answer"

    def get_performance_stats(self):
        if isinstance(self._stats, Exception):
            raise self._stats
        return self._stats


@pytest.fixture
def stub_llm(monkeypatch):
    def install(stats):
        monkeypatch.setattr(
            cli, "create_client", lambda *args, **kwargs: _StubLLM(stats)
        )

    return install


def _stats(monkeypatch):
    return _run_main(monkeypatch, "stats", "--no-lemonade-check")


def test_get_stats_propagates_backend_errors(stub_llm):
    stub_llm(ConnectionError("Lemonade not reachable at http://localhost:1"))
    client = cli.GaiaCliClient(model="stub-model")

    with pytest.raises(ConnectionError, match="not reachable"):
        client.get_stats()


def test_stats_backend_error_exits_non_zero_with_the_cause(
    stub_llm, monkeypatch, capsys
):
    stub_llm(ConnectionError("Lemonade not reachable at http://localhost:1"))

    assert _stats(monkeypatch) == 1
    captured = capsys.readouterr()
    assert "Lemonade not reachable at http://localhost:1" in captured.err
    assert "No stats" not in captured.out + captured.err


def test_prompt_stats_backend_error_exits_non_zero_after_the_answer(
    stub_llm, monkeypatch, capsys
):
    stub_llm(ConnectionError("Lemonade not reachable at http://localhost:1"))

    status = _run_main(monkeypatch, "prompt", "hi", "--stats", "--no-lemonade-check")

    assert status == 1
    captured = capsys.readouterr()
    assert "the answer" in captured.out
    assert "Lemonade not reachable at http://localhost:1" in captured.err


def test_stats_with_none_recorded_is_an_empty_state_not_an_error(
    stub_llm, monkeypatch, capsys
):
    stub_llm({})

    assert _stats(monkeypatch) == 0
    assert "No stats yet" in capsys.readouterr().out


def test_stats_prints_recorded_stats(stub_llm, monkeypatch, capsys):
    stub_llm({"tokens_per_second": 42.0})

    assert _stats(monkeypatch) == 0
    assert "tokens_per_second" in capsys.readouterr().out
