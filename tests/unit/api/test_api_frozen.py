# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``gaia api`` in the frozen gaia, where the flagship can never be imported.

v0.26.0-rc18's frozen gaia started the server, listed the 'gaia' model, then
answered every chat completion with 404 "No module named 'gaia_agent'" and a
remedy spliced from two messages. It now refuses at startup with one message.
"""

from __future__ import annotations

import sys
from argparse import Namespace
from unittest.mock import MagicMock

import pytest

from gaia.api.flagship import FROZEN_API_UNAVAILABLE, api_unavailable_reason


@pytest.fixture
def frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)


@pytest.fixture
def no_flagship(monkeypatch):
    for name in ("gaia_agent", "gaia_agent.agent"):
        monkeypatch.setitem(sys.modules, name, None)


def _start_args(**overrides):
    args = dict(
        subcommand="start",
        host="127.0.0.1",
        port=8080,
        no_lemonade_check=False,
        base_url=None,
        debug=False,
        show_prompts=False,
        streaming=False,
    )
    args.update(overrides)
    return Namespace(**args)


def _assert_one_honest_message(text: str) -> None:
    assert "gaia -p" in text
    assert "source install" in text
    assert "pip install" not in text
    assert "install it from source" not in text
    assert "Python packages can't be installed" not in text


def test_reason_only_for_a_frozen_build_without_the_agent(monkeypatch, no_flagship):
    assert api_unavailable_reason() is None
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert api_unavailable_reason() == FROZEN_API_UNAVAILABLE
    _assert_one_honest_message(FROZEN_API_UNAVAILABLE)


def test_reason_is_none_when_the_agent_imports(frozen):
    pytest.importorskip("gaia_agent")
    assert api_unavailable_reason() is None


def test_start_refuses_before_touching_lemonade_or_binding(
    frozen, no_flagship, monkeypatch, capsys
):
    import uvicorn

    from gaia import cli

    init = MagicMock(return_value=(True, None))
    run = MagicMock()
    monkeypatch.setattr(cli, "initialize_lemonade_for_agent", init)
    monkeypatch.setattr(uvicorn, "run", run)
    with pytest.raises(SystemExit) as exc:
        cli.handle_api_command(_start_args())
    assert exc.value.code == 1
    init.assert_not_called()
    run.assert_not_called()
    captured = capsys.readouterr()
    assert FROZEN_API_UNAVAILABLE in captured.err
    _assert_one_honest_message(captured.err + captured.out)


def test_source_install_without_the_agent_still_starts(no_flagship, monkeypatch):
    import uvicorn

    from gaia import cli

    run = MagicMock()
    monkeypatch.setattr(uvicorn, "run", run)
    cli.handle_api_command(_start_args(no_lemonade_check=True))
    run.assert_called_once()


def test_start_server_refuses_too(frozen, no_flagship, monkeypatch):
    import uvicorn

    from gaia.api import app

    run = MagicMock()
    monkeypatch.setattr(uvicorn, "run", run)
    with pytest.raises(RuntimeError, match="can't run in this GAIA install"):
        app.start_server("127.0.0.1", 8080)
    run.assert_not_called()


def test_a_request_gets_the_same_message_not_a_spliced_one(frozen, no_flagship):
    from gaia.api.agent_registry import AgentRegistry

    with pytest.raises(ValueError) as exc:
        AgentRegistry().get_agent("gaia")
    message = str(exc.value)
    assert FROZEN_API_UNAVAILABLE in message
    _assert_one_honest_message(message)


def test_the_freeze_smoke_test_checks_this_refusal():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "installer" / "core" / "smoke_test.py"
    spec = importlib.util.spec_from_file_location("core_smoke_api", path)
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    assert smoke.API_REFUSAL in FROZEN_API_UNAVAILABLE
    assert "smoke.api_start()" in path.read_text(encoding="utf-8")


def test_start_exits_nonzero_when_the_model_server_is_unreachable(monkeypatch):
    import uvicorn

    from gaia import cli

    run = MagicMock()
    monkeypatch.setattr(
        cli, "initialize_lemonade_for_agent", MagicMock(return_value=(False, None))
    )
    monkeypatch.setattr(uvicorn, "run", run)
    with pytest.raises(SystemExit) as exc:
        cli.handle_api_command(_start_args(base_url="http://127.0.0.1:1/api/v1"))
    assert exc.value.code == 1
    run.assert_not_called()


def test_start_still_runs_when_the_model_server_answers(monkeypatch):
    import uvicorn

    from gaia import cli

    run = MagicMock()
    monkeypatch.setattr(
        cli,
        "initialize_lemonade_for_agent",
        MagicMock(return_value=(True, "http://127.0.0.1:13305/api/v1")),
    )
    monkeypatch.setattr(uvicorn, "run", run)
    cli.handle_api_command(_start_args())
    run.assert_called_once()


def test_no_lemonade_check_skips_the_check_even_when_it_would_fail(monkeypatch):
    import uvicorn

    from gaia import cli

    check = MagicMock(return_value=(False, None))
    run = MagicMock()
    monkeypatch.setattr(cli, "initialize_lemonade_for_agent", check)
    monkeypatch.setattr(uvicorn, "run", run)
    cli.handle_api_command(_start_args(no_lemonade_check=True))
    check.assert_not_called()
    run.assert_called_once()
