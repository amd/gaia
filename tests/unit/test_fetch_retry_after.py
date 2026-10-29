# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A rate-limited fetch tells the agent how long the server asked it to wait.

``raise_for_status`` keeps only the status line, so ``fetch_page`` and
``fetch_webpage`` turned a 429 with ``Retry-After: 3600`` into "429 Client
Error". The agent fetched the same URL again at once and could not tell the
user when the limit resets.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from gaia.web.client import fetch_error_text
from tests.unit.test_profilespec_characterization import chat_agent_build_context

URL = "https://status.example.com/board"


def _response(status, headers=None):
    response = requests.Response()
    response.status_code = status
    response.url = URL
    response.reason = {429: "Too Many Requests", 404: "Not Found"}.get(status, "")
    response.headers.update(headers or {})
    response._content = b""
    return response


def _http_error(status, headers=None):
    try:
        _response(status, headers).raise_for_status()
    except requests.HTTPError as exc:
        return exc
    raise AssertionError("raise_for_status did not raise")


def test_retry_after_seconds_are_named():
    text = fetch_error_text(_http_error(429, {"Retry-After": "3600"}))

    assert "429" in text
    assert "3600 s (about 60 min)" in text
    assert "don't fetch this URL again" in text


def test_a_short_wait_has_no_minutes():
    text = fetch_error_text(_http_error(429, {"Retry-After": "30"}))

    assert "30 s before" in text
    assert "min)" not in text


def test_an_error_without_retry_after_is_unchanged():
    exc = _http_error(404)

    assert fetch_error_text(exc) == str(exc)


def test_an_error_with_no_response_is_unchanged():
    exc = requests.ConnectionError("connection refused")

    assert fetch_error_text(exc) == "connection refused"


def test_fetch_page_reports_retry_after():
    from gaia.agents.tools.browser_tools import BrowserToolsMixin

    registered = {}

    def capture(*args, **kwargs):
        def decorator(fn):
            registered[fn.__name__] = fn
            return fn

        return decorator(args[0]) if args and callable(args[0]) else decorator

    class Agent(BrowserToolsMixin):
        def __init__(self):
            self._web_client = MagicMock()

    with patch("gaia.agents.base.tools.tool", capture):
        agent = Agent()
        agent.register_browser_tools()
    agent._web_client.get.return_value = _response(429, {"Retry-After": "3600"})

    result = registered["fetch_page"](URL)

    assert result.startswith("Error fetching page:")
    assert "3600 s (about 60 min)" in result


@pytest.fixture
def fetch_webpage():
    with chat_agent_build_context("full") as agent:
        agent._register_tools()
        yield agent, agent._tools_registry["fetch_webpage"]["function"]


def test_fetch_webpage_reports_retry_after(fetch_webpage):
    agent, tool = fetch_webpage
    client = MagicMock()
    client.get.return_value = _response(429, {"Retry-After": "3600"})

    with patch.object(agent, "_inline_web_client", return_value=client):
        result = tool(URL)

    assert result["status"] == "error"
    assert result["retry_after"] == "3600"
    assert "Retry-After" in result["hint"]


def test_fetch_webpage_error_without_retry_after_has_no_hint(fetch_webpage):
    agent, tool = fetch_webpage
    client = MagicMock()
    client.get.return_value = _response(404)

    with patch.object(agent, "_inline_web_client", return_value=client):
        result = tool(URL)

    assert result["status"] == "error"
    assert "retry_after" not in result and "hint" not in result
