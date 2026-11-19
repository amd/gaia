# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the `gaia knowledge` CLI handler wiring (issue #2000).

Covers ``handle_knowledge_command`` (src/gaia/cli.py):

  * ``BudgetConfig`` is constructed with ``block = not args.no_block`` —
    inverting this would silently disable the Tavily spend cap
  * ``TavilyBudgetExceeded`` and ``TavilyConfigError`` raised by the client
    map to ``sys.exit(1)``
  * a dropped or refused connection prints a one-line error naming the
    service and the remedy, and exits 1, instead of a raw traceback

``tests/unit/test_tavily_wrapper.py`` covers the Tavily client itself; this
file only exercises the CLI handler's wiring, so ``BudgetConfig`` and
``TavilyClient`` are replaced with lightweight fakes.
"""

import importlib.util
from argparse import Namespace
from pathlib import Path

import pytest
import requests

import gaia.web.tavily as tavily_module
from gaia import cli
from gaia.web.client import SearchUnavailableError, WebClient

_REAL_BUDGET_CONFIG = tavily_module.BudgetConfig
_REAL_TAVILY_CLIENT = tavily_module.TavilyClient


def _load_smoke():
    repo = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "knowledge_cli_smoke_test", repo / "installer" / "core" / "smoke_test.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
    configured = False

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
        return {"cap": None, "total_credits": 0, "session_credits": 0}

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
    _FakeTavilyClient.configured = False
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


# ---------------------------------------------------------------------------
# Usage line: session spend shown only where the command spent something
# ---------------------------------------------------------------------------


@pytest.fixture
def _seeded_usage(monkeypatch):
    monkeypatch.setattr(
        _FakeTavilyClient,
        "usage",
        lambda self: {"cap": 5, "total_credits": 7, "session_credits": 1},
    )


@pytest.mark.parametrize("action", ["search", "extract"])
def test_spending_actions_report_session_credits(_seeded_usage, capsys, action):
    cli.handle_knowledge_command(_knowledge_args(action))
    out = capsys.readouterr().out
    assert "Credits used: 7 total, 1 this session (cap: 5)" in out


def test_usage_action_omits_always_zero_session_figure(_seeded_usage, capsys):
    cli.handle_knowledge_command(_knowledge_args("usage"))
    out = capsys.readouterr().out
    assert "Credits used: 7 total (cap: 5)" in out
    assert "this session" not in out


# ---------------------------------------------------------------------------
# Network failures -> one actionable line, exit 1, no traceback
# ---------------------------------------------------------------------------


def _raiser(exc):
    def _raise(*a, **k):
        raise exc

    return _raise


_DROPPED = [
    requests.exceptions.ConnectionError(
        ("Connection aborted.", BrokenPipeError(32, "Broken pipe"))
    ),
    requests.exceptions.ConnectionError(
        ("Connection aborted.", ConnectionResetError(104, "Connection reset by peer"))
    ),
    requests.exceptions.ReadTimeout("read timed out"),
    requests.exceptions.SSLError("handshake failed"),
]


@pytest.mark.parametrize("exc", _DROPPED, ids=lambda e: type(e).__name__)
def test_dropped_connection_during_search_is_actionable(capsys, exc):
    _FakeTavilyClient.on_search = _raiser(exc)

    with pytest.raises(SystemExit) as excinfo:
        cli.handle_knowledge_command(_knowledge_args("search"))

    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert "Web search failed: DuckDuckGo could not be reached" in out
    assert "Check your internet connection" in out
    assert "Traceback" not in out


def test_dropped_connection_names_tavily_when_configured(capsys):
    _FakeTavilyClient.configured = True
    _FakeTavilyClient.on_search = _raiser(requests.exceptions.ConnectionError("x"))

    with pytest.raises(SystemExit):
        cli.handle_knowledge_command(_knowledge_args("search"))

    assert "Web search failed: Tavily could not be reached" in capsys.readouterr().out


def test_dropped_connection_during_extract_is_actionable(capsys):
    _FakeTavilyClient.configured = True
    _FakeTavilyClient.on_extract = _raiser(requests.exceptions.ConnectionError("x"))

    with pytest.raises(SystemExit) as excinfo:
        cli.handle_knowledge_command(_knowledge_args("extract"))

    assert excinfo.value.code == 1
    assert "Web extract failed: Tavily could not be reached" in (
        capsys.readouterr().out
    )


def test_rate_limited_search_prints_the_wait_and_exits_1(capsys):
    _FakeTavilyClient.on_search = _raiser(
        SearchUnavailableError(
            "DuckDuckGo turned the search away (HTTP 429), so no results were "
            "read. Searching again in about 30 seconds may work.",
            retry_after=30,
            status=429,
        )
    )

    with pytest.raises(SystemExit) as excinfo:
        cli.handle_knowledge_command(_knowledge_args("search"))

    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert "searching again in about 30 seconds" in out.lower()
    assert "could not be reached" not in out


def test_client_is_closed_after_a_network_failure(monkeypatch):
    created = []
    real_init = _FakeTavilyClient.__init__

    def _tracking_init(self, *a, **k):
        real_init(self, *a, **k)
        created.append(self)

    monkeypatch.setattr(_FakeTavilyClient, "__init__", _tracking_init)
    _FakeTavilyClient.on_search = _raiser(requests.exceptions.ConnectionError("x"))

    with pytest.raises(SystemExit):
        cli.handle_knowledge_command(_knowledge_args("search"))

    assert created[0].closed is True


@pytest.mark.parametrize(
    "exc", [ValueError("bad parse"), KeyError("results"), RuntimeError("HTTP 500")]
)
def test_non_network_errors_are_not_swallowed(exc):
    # Only network errors are translated; a bug must still surface loudly.
    _FakeTavilyClient.on_search = _raiser(exc)

    with pytest.raises(type(exc)):
        cli.handle_knowledge_command(_knowledge_args("search"))


def test_real_fallback_path_with_dropped_connection_passes_the_freeze_walk(
    monkeypatch, capsys
):
    """The frozen-binary walk ran exactly this: no key, DDG, connection reset."""
    monkeypatch.setattr(tavily_module, "_load_api_key", lambda: None)
    monkeypatch.setattr(tavily_module, "BudgetConfig", _REAL_BUDGET_CONFIG)
    monkeypatch.setattr(
        tavily_module,
        "TavilyClient",
        lambda budget=None: _REAL_TAVILY_CLIENT(db_path=":memory:", budget=budget),
    )
    monkeypatch.setattr(
        WebClient,
        "post",
        _raiser(
            requests.exceptions.ConnectionError(
                ("Connection aborted.", BrokenPipeError(32, "Broken pipe"))
            )
        ),
    )

    with pytest.raises(SystemExit) as excinfo:
        cli.handle_knowledge_command(_knowledge_args("search", query="smoke-walk"))

    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert "DuckDuckGo could not be reached" in out
    assert _load_smoke().broken_start(out) is None


def test_a_server_error_is_not_blamed_on_the_users_connection(capsys):
    """A 5xx means the network worked; it must not say to check the connection."""
    response = requests.Response()
    response.status_code = 503
    _FakeTavilyClient.on_search = _raiser(
        requests.exceptions.HTTPError("503 Server Error", response=response)
    )

    with pytest.raises(SystemExit) as excinfo:
        cli.handle_knowledge_command(_knowledge_args("search"))

    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert "answered with 503" in out
    assert "down for a moment" in out
    assert "Check your internet connection" not in out
    assert "Traceback" not in out


def test_a_rejected_request_is_not_called_an_outage(capsys):
    """A 4xx is not the service being down for a moment."""
    response = requests.Response()
    response.status_code = 400
    _FakeTavilyClient.on_search = _raiser(
        requests.exceptions.HTTPError("400 Client Error", response=response)
    )

    with pytest.raises(SystemExit) as excinfo:
        cli.handle_knowledge_command(_knowledge_args("search"))

    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert "answered with 400" in out
    assert "rejected the request" in out
    assert "down for a moment" not in out
    assert "Check your internet connection" not in out


def test_a_connection_dropped_mid_response_is_a_dropped_connection(capsys):
    _FakeTavilyClient.on_search = _raiser(
        requests.exceptions.ChunkedEncodingError("Connection broken: IncompleteRead")
    )

    with pytest.raises(SystemExit) as excinfo:
        cli.handle_knowledge_command(_knowledge_args("search"))

    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert "could not be reached" in out
    assert "Traceback" not in out


@pytest.mark.parametrize(
    "exc",
    [
        requests.exceptions.TooManyRedirects("Exceeded 30 redirects."),
        requests.exceptions.MissingSchema(
            "Invalid URL 'example.com': No scheme supplied"
        ),
        requests.exceptions.ContentDecodingError("bad gzip"),
    ],
    ids=lambda e: type(e).__name__,
)
def test_any_other_request_error_is_still_a_clean_exit(capsys, exc):
    _FakeTavilyClient.on_extract = _raiser(exc)

    with pytest.raises(SystemExit) as excinfo:
        cli.handle_knowledge_command(_knowledge_args("extract"))

    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert "Web extract failed" in out
    assert "Check your internet connection" not in out


@pytest.mark.parametrize(
    "status, hint", [(502, "may be down"), (404, "rejected the request")]
)
def test_a_duckduckgo_error_status_is_one_line_not_a_traceback(capsys, status, hint):
    """With no Tavily key, a DuckDuckGo 5xx printed a traceback."""
    from gaia.web.client import SearchStatusError

    _FakeTavilyClient.on_search = _raiser(SearchStatusError("DuckDuckGo", status))

    with pytest.raises(SystemExit) as excinfo:
        cli.handle_knowledge_command(_knowledge_args("search"))

    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert f"DuckDuckGo answered HTTP {status}" in out
    assert hint in out
    assert "Traceback" not in out
