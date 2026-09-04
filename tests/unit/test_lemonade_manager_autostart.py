# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``LemonadeManager.ensure_ready`` gets the server started instead of describing it.

``ensure_ready`` is the gate every in-process GAIA path goes through — the base
``Agent`` constructor, ``gaia chat``, ``gaia llm``, the Agent UI server. Before
this, a stopped Lemonade made it print instructions and return False. Now it
asks the daemon — which owns the process — for one.

Two properties are pinned here because they pull in opposite directions: the
start has to happen on the cold path, and it must NOT be reachable on the warm
one (that call sits in front of every agent construction).
"""

from unittest.mock import MagicMock, patch

import pytest

from gaia.llm.lemonade_client import LemonadeStatus
from gaia.llm.lemonade_manager import LemonadeManager
from gaia.llm.lemonade_service import LemonadeStartError
from gaia.llm.lemonade_supervisor import LemonadeState


@pytest.fixture(autouse=True)
def _reset_manager():
    """The manager is a singleton; a warm one would skip the branch under test."""
    LemonadeManager.reset()
    yield
    LemonadeManager.reset()


def _status(running=True, context_size=0, loaded_models=None):
    return LemonadeStatus(
        running=running,
        context_size=context_size,
        loaded_models=[] if loaded_models is None else loaded_models,
    )


def _client(*statuses):
    client = MagicMock()
    client.base_url = "http://localhost:13305/api/v1"
    client.get_status.side_effect = list(statuses)
    return client


@patch("gaia.llm.lemonade_manager.LemonadeClient")
@patch("gaia.llm.lemonade_service.ensure_lemonade_running")
def test_a_stopped_server_is_started_rather_than_described(start, client_cls):
    """The whole point: no user ever has to type a Lemonade command."""
    start.return_value = LemonadeState(
        base_url="http://localhost:13305/api/v1",
        started=True,
        owned=True,
        pid=42,
        waited_seconds=6.0,
    )
    client_cls.return_value = _client(
        _status(running=False),  # the cold probe
        _status(running=True, context_size=65536, loaded_models=[{"id": "Gemma"}]),
        _status(running=True, context_size=65536, loaded_models=[{"id": "Gemma"}]),
    )

    assert LemonadeManager.ensure_ready(min_context_size=65536, quiet=True) is True

    # The floor the caller asked for has to reach the start, or the server comes
    # up with its own small default and every long request fails afterwards.
    assert start.call_args.kwargs["ctx_size"] == 65536
    assert start.call_args.kwargs["base_url"] == "http://localhost:13305/api/v1"


@patch("gaia.llm.lemonade_manager.LemonadeClient")
@patch("gaia.llm.lemonade_service.ensure_lemonade_running")
def test_a_running_server_never_reaches_the_starter(start, client_cls):
    """Startup-latency guard.

    ensure_ready runs in front of every agent construction, so the warm path
    must not acquire a lock, resolve tooling, or probe a second time.
    """
    client_cls.return_value = _client(
        _status(running=True, context_size=65536, loaded_models=[{"id": "Gemma"}])
    )

    assert LemonadeManager.ensure_ready(min_context_size=65536, quiet=True) is True
    start.assert_not_called()


@patch("gaia.llm.lemonade_manager.LemonadeClient")
@patch("gaia.llm.lemonade_service.ensure_lemonade_running")
def test_a_start_that_cannot_happen_surfaces_the_reason_verbatim(
    start, client_cls, capsys
):
    """No silent fallback.

    ensure_ready's contract is a bool, so the actionable message is PRINTED
    rather than raised past callers that cannot handle it — but it is never
    swallowed, and it never degrades to a quiet "no LLM".
    """
    start.side_effect = LemonadeStartError(
        "Lemonade Server is not running at http://localhost:13305/api/v1, and "
        "GAIA could not find an installed copy to start.\n"
        "To fix: Run `gaia init` to install it.\n"
        "See https://amd-gaia.ai/docs/guides/install"
    )
    client_cls.return_value = _client(_status(running=False))

    assert LemonadeManager.ensure_ready(min_context_size=65536, quiet=False) is False

    err = capsys.readouterr().err
    assert "gaia init" in err
    assert "amd-gaia.ai/docs/guides/install" in err


@patch("gaia.llm.lemonade_manager.LemonadeClient")
@patch("gaia.llm.lemonade_service.ensure_lemonade_running")
def test_a_start_that_reports_success_but_leaves_it_down_is_not_treated_as_ready(
    start, client_cls
):
    """Trust the re-probe, not the return value.

    ``ensure_lemonade_running`` returning is evidence the LAUNCH worked; only
    the follow-up status says the server is usable. Skipping it would let a
    half-started server take this call green and fail on the first query.
    """
    start.return_value = LemonadeState(
        base_url="http://localhost:13305/api/v1",
        started=True,
        owned=True,
        pid=42,
        waited_seconds=1.0,
    )
    client_cls.return_value = _client(_status(running=False), _status(running=False))

    assert LemonadeManager.ensure_ready(min_context_size=65536, quiet=True) is False
