# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The chat agent's ``fetch_webpage`` returns a rate limit's ``Retry-After``.

Needs the gaia-agent-chat wheel, so it runs in test_chat_agent.yml; the
core helper and ``fetch_page`` are covered by test_fetch_retry_after.py.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

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
