# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""`gaia llm` shows the free-memory refusal word for word, and fails.

The refusal says how much memory the model needs and how much is free, which
is the one thing a user can act on. The preload used to replace it with a
generic "stop the running server, then restart it" and exit 0.
"""

import sys
from unittest.mock import MagicMock

import pytest

from gaia.llm.lemonade_client import InsufficientMemoryError, LemonadeStatus
from gaia.llm.lemonade_manager import LemonadeManager

REFUSAL = (
    "Not enough free memory to load Gemma-4-E4B-it-GGUF at 32,768 tokens: it "
    "needs about 9.1 GB and only 6.0 GB is free. Close other applications, or "
    "choose a smaller model."
)


@pytest.fixture(autouse=True)
def _reset_manager():
    LemonadeManager.reset()
    yield
    LemonadeManager.reset()


@pytest.fixture
def idle_server(monkeypatch):
    """A running Lemonade with nothing loaded, whose load is refused."""
    client = MagicMock()
    client.base_url = "http://localhost:45678/api/v1"
    client.get_status.return_value = LemonadeStatus(
        running=True, context_size=0, loaded_models=[]
    )
    client.get_model_max_context_window.return_value = None
    client.load_model.side_effect = InsufficientMemoryError(REFUSAL)
    monkeypatch.setattr(
        "gaia.llm.lemonade_manager.LemonadeClient", lambda *a, **k: client
    )
    monkeypatch.setattr(
        "gaia.llm.lemonade_manager.gaia_runs_lemonade", lambda *a, **k: False
    )
    return client


def test_the_preload_raises_the_refusal_unchanged(idle_server):
    with pytest.raises(InsufficientMemoryError) as raised:
        LemonadeManager.ensure_ready(min_context_size=32768, quiet=True)

    assert str(raised.value) == REFUSAL
    assert LemonadeManager.is_initialized() is False
    assert LemonadeManager._lock.acquire(timeout=1.0)
    LemonadeManager._lock.release()


def _run_gaia_llm(monkeypatch):
    monkeypatch.setattr(
        "gaia.llm.lemonade_service.ensure_daemon_owns_lemonade", lambda *a, **k: None
    )
    monkeypatch.setattr(sys, "argv", ["gaia", "llm", "hello", "--no-stream"])
    from gaia.cli import main

    main()


def test_gaia_llm_prints_the_refusal_and_exits_non_zero(
    idle_server, monkeypatch, capsys
):
    def never(**kwargs):
        raise AssertionError("gaia llm sent the prompt after the load was refused")

    monkeypatch.setattr("gaia.apps.llm.app.main", never)

    with pytest.raises(SystemExit) as exc:
        _run_gaia_llm(monkeypatch)

    assert exc.value.code not in (0, None)
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert REFUSAL in output
    assert "stop the running server" not in output


def _answering_fails(monkeypatch, error, server_up):
    monkeypatch.setattr(
        "gaia.cli.initialize_lemonade_for_agent", lambda **kw: (True, None)
    )

    def fails(**kwargs):
        raise error

    monkeypatch.setattr("gaia.apps.llm.app.main", fails)
    if server_up is not None:
        monkeypatch.setattr("gaia.cli._lemonade_answers", lambda *a: server_up)


def _output(capsys):
    captured = capsys.readouterr()
    return captured.out + captured.err


NOT_RUNNING = "Lemonade server is not running or not accessible"


def test_a_refusal_while_answering_also_exits_non_zero(monkeypatch, capsys):
    _answering_fails(monkeypatch, InsufficientMemoryError(REFUSAL), server_up=True)

    with pytest.raises(SystemExit) as exc:
        _run_gaia_llm(monkeypatch)

    assert exc.value.code not in (0, None)
    output = _output(capsys)
    assert REFUSAL in output
    assert NOT_RUNNING not in output


@pytest.mark.parametrize(
    "stall",
    [
        "Read timed out: no token from the model for 120s (connection kept open)",
        "Stream stalled: timeout waiting for the next chunk",
    ],
)
def test_a_stalled_answer_shows_its_own_error_not_install_advice(
    stall, monkeypatch, capsys
):
    from gaia.llm.lemonade_client import LemonadeClientError

    _answering_fails(monkeypatch, LemonadeClientError(stall), server_up=True)

    with pytest.raises(SystemExit) as exc:
        _run_gaia_llm(monkeypatch)

    assert exc.value.code not in (0, None)
    output = _output(capsys)
    assert stall in output
    assert NOT_RUNNING not in output and "gaia init" not in output


@pytest.mark.allow_network
def test_an_unreachable_server_gets_the_start_hint(monkeypatch, capsys):
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        closed = sock.getsockname()[1]
    monkeypatch.setattr(LemonadeManager, "is_lemonade_installed", lambda: True)
    _answering_fails(monkeypatch, ConnectionError("connection refused"), server_up=None)
    monkeypatch.setattr(
        sys,
        "argv",
        ["gaia", "llm", "hello", "--base-url", f"http://localhost:{closed}"],
    )
    monkeypatch.setattr(
        "gaia.llm.lemonade_service.ensure_daemon_owns_lemonade", lambda *a, **k: None
    )
    from gaia.cli import main

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code not in (0, None)
    output = _output(capsys)
    assert "connection refused" in output
    assert NOT_RUNNING in output and "To start it" in output
