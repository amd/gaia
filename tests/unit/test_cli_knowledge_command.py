# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the `gaia knowledge` CLI handler wiring (issue #2000).

Covers ``handle_knowledge_command`` (src/gaia/cli.py):

  * ``BudgetConfig`` is constructed with ``block = not args.no_block`` —
    inverting this would silently disable the Tavily spend cap
  * ``TavilyBudgetExceeded`` and ``TavilyConfigError`` raised by the client
    map to ``sys.exit(1)``

``tests/unit/test_tavily_wrapper.py`` covers the Tavily client itself; this
file only exercises the CLI handler's wiring, so ``BudgetConfig`` and
``TavilyClient`` are replaced with lightweight fakes.
"""

from argparse import Namespace

import pytest

import gaia.web.tavily as tavily_module
from gaia import cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _knowledge_args(action="usage", *, budget=None, no_block=False, **extra):
    base = dict(
        knowledge_action=action,
        budget=budget,
        no_block=no_block,
        query="test query",
        depth="basic",
        max_results=5,
        urls=["https://example.com"],
    )
    base.update(extra)
    return Namespace(**base)


class _RecordingBudgetConfig:
    """Stands in for ``gaia.web.tavily.BudgetConfig``; records its kwargs."""

    #: populated per-instantiation by the test via monkeypatch closure
    last_kwargs = None

    def __init__(self, **kwargs):
        type(self).last_kwargs = kwargs
        self.cap = kwargs.get("cap")
        self.block = kwargs.get("block")


class _FakeTavilyClient:
    """Stands in for ``gaia.web.tavily.TavilyClient``."""

    #: set per-test to control what search()/extract() do
    on_search = None
    on_extract = None

    def __init__(self, *, budget=None, **kwargs):  # noqa: ARG002 - signature match
        self.budget = budget
        self.closed = False

    def search(self, *a, **k):
        if self.on_search is not None:
            return self.on_search(*a, **k)
        return {"source": "tavily", "results": []}

    def extract(self, *a, **k):
        if self.on_extract is not None:
            return self.on_extract(*a, **k)
        return {}

    def usage(self):
        return {"cap": None, "total_credits": 0}

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _patch_tavily(monkeypatch):
    """Replace BudgetConfig / TavilyClient with fakes for every test here.

    The real exception classes (``TavilyBudgetExceeded``, ``TavilyConfigError``)
    are left untouched — the CLI handler imports and catches those directly,
    so raising the real classes from a fake client exercises the real except
    clauses.
    """
    _RecordingBudgetConfig.last_kwargs = None
    _FakeTavilyClient.on_search = None
    _FakeTavilyClient.on_extract = None
    monkeypatch.setattr(tavily_module, "BudgetConfig", _RecordingBudgetConfig)
    monkeypatch.setattr(tavily_module, "TavilyClient", _FakeTavilyClient)
    yield


# ---------------------------------------------------------------------------
# BudgetConfig(block=not args.no_block) wiring
# ---------------------------------------------------------------------------


def test_budget_blocks_by_default_when_no_block_is_false():
    cli.handle_knowledge_command(_knowledge_args("usage", no_block=False))

    assert _RecordingBudgetConfig.last_kwargs["block"] is True


def test_no_block_flag_disables_blocking():
    cli.handle_knowledge_command(_knowledge_args("usage", no_block=True))

    assert _RecordingBudgetConfig.last_kwargs["block"] is False


def test_budget_cap_is_forwarded():
    cli.handle_knowledge_command(_knowledge_args("usage", budget=42, no_block=False))

    assert _RecordingBudgetConfig.last_kwargs["cap"] == 42
    assert _RecordingBudgetConfig.last_kwargs["block"] is True


# ---------------------------------------------------------------------------
# TavilyBudgetExceeded / TavilyConfigError -> exit code 1
# ---------------------------------------------------------------------------


def test_budget_exceeded_during_search_exits_1(capsys):
    from gaia.web.tavily import TavilyBudgetExceeded

    def _raise_budget(*a, **k):
        raise TavilyBudgetExceeded("credit cap of 10 reached")

    _FakeTavilyClient.on_search = _raise_budget

    with pytest.raises(SystemExit) as excinfo:
        cli.handle_knowledge_command(_knowledge_args("search"))

    assert excinfo.value.code == 1
    assert "credit cap of 10 reached" in capsys.readouterr().out


def test_config_error_during_search_exits_1(capsys):
    from gaia.web.tavily import TavilyConfigError

    def _raise_config(*a, **k):
        raise TavilyConfigError("tavily-python SDK is not installed")

    _FakeTavilyClient.on_search = _raise_config

    with pytest.raises(SystemExit) as excinfo:
        cli.handle_knowledge_command(_knowledge_args("search"))

    assert excinfo.value.code == 1
    assert "tavily-python SDK is not installed" in capsys.readouterr().out


def test_budget_exceeded_during_extract_exits_1():
    from gaia.web.tavily import TavilyBudgetExceeded

    def _raise_budget(*a, **k):
        raise TavilyBudgetExceeded("credit cap reached")

    _FakeTavilyClient.on_extract = _raise_budget

    with pytest.raises(SystemExit) as excinfo:
        cli.handle_knowledge_command(_knowledge_args("extract"))

    assert excinfo.value.code == 1


def test_client_is_closed_even_when_budget_exceeded(monkeypatch):
    from gaia.web.tavily import TavilyBudgetExceeded

    created = []
    real_init = _FakeTavilyClient.__init__

    def _tracking_init(self, *a, **k):
        real_init(self, *a, **k)
        created.append(self)

    monkeypatch.setattr(_FakeTavilyClient, "__init__", _tracking_init)

    def _raise_budget(*a, **k):
        raise TavilyBudgetExceeded("cap reached")

    _FakeTavilyClient.on_search = _raise_budget

    with pytest.raises(SystemExit):
        cli.handle_knowledge_command(_knowledge_args("search"))

    assert created[0].closed is True


def test_successful_search_does_not_exit():
    # A clean run must not raise SystemExit at all.
    cli.handle_knowledge_command(_knowledge_args("search"))
