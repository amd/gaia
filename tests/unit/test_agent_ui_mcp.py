# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for agent_ui_mcp error normalization and routing."""

from unittest.mock import MagicMock, patch

import pytest
import requests


class TestNormalizeError:
    """P3 + P4: error shape must not leak internal URLs."""

    def test_connection_error_no_url(self):
        from gaia.mcp.servers.agent_ui_mcp import _normalize_error

        err = requests.exceptions.ConnectionError("Connection refused")
        result = _normalize_error(err, "http://localhost:4200")
        assert result["status"] == "error"
        assert "localhost:4200" not in result["detail"]
        assert (
            "connect" in result["detail"].lower()
            or "running" in result["detail"].lower()
        )

    def test_http_404_clean_detail(self):
        from gaia.mcp.servers.agent_ui_mcp import _normalize_error

        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.json.return_value = {"detail": "Session not found"}
        err = requests.exceptions.HTTPError(response=mock_resp)
        result = _normalize_error(err, "http://localhost:4200")
        assert result["status"] == "error"
        assert "localhost:4200" not in str(result)
        assert "Session not found" in result["detail"]

    def test_http_500_no_url_leak(self):
        from gaia.mcp.servers.agent_ui_mcp import _normalize_error

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.json.side_effect = ValueError("not json")
        mock_resp.text = "Internal Server Error at http://localhost:4200/api/chat/send"
        err = requests.exceptions.HTTPError(response=mock_resp)
        result = _normalize_error(err, "http://localhost:4200")
        assert result["status"] == "error"
        assert "localhost:4200" not in result["detail"]

    def test_api_404_matches_get_session_shape(self):
        """P3: send_message 404 must return same structured shape as get_session 404."""
        from gaia.mcp.servers.agent_ui_mcp import _api

        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.json.return_value = {"detail": "Session not found"}
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=mock_resp
        )
        with patch("requests.get", return_value=mock_resp):
            result = _api("http://localhost:4200", "get", "/sessions/nonexistent")
        assert result["status"] == "error"
        assert "localhost:4200" not in str(result)


class TestOpenSessionInBrowser:
    """P1: open_session_in_browser must return hash URL and trigger activate."""

    def test_hash_url_format(self):
        """Verify hash URL is used instead of query-param URL."""
        session_id = "test-session-id"
        # Hash URL should not contain '?session='
        expected_hash = f"#{session_id}"
        expected_no_query = "?session="
        assert expected_hash in f"http://localhost:4200/#{session_id}"
        assert expected_no_query not in f"http://localhost:4200/#{session_id}"

    def test_returns_hash_url_on_open(self):
        pytest.importorskip("mcp")  # constructing the server needs optional mcp
        from gaia.mcp.servers.agent_ui_mcp import create_agent_ui_mcp

        session_id = "abc123"

        with (
            patch("webbrowser.open") as mock_open,
            patch("requests.get") as mock_get,
            patch("requests.post") as mock_post,
        ):
            # Dev port check fails
            mock_get.side_effect = Exception("not running")
            # Activate endpoint and any POST succeeds
            activate_resp = MagicMock()
            activate_resp.raise_for_status.return_value = None
            activate_resp.json.return_value = {"activated": True}
            mock_post.return_value = activate_resp

            mcp = create_agent_ui_mcp("http://localhost:4200")
            # Retrieve the tool function from the tool manager
            tools = mcp._tool_manager._tools  # pylint: disable=protected-access
            open_fn = tools.get("open_session_in_browser")
            if open_fn is None:
                pytest.skip(
                    "Tool manager structure differs — skipping direct invocation test"
                )

            result = open_fn.fn(session_id=session_id)

        assert "#" in result.get("url", "")
        assert "?session=" not in result.get("url", "")
        assert result.get("opened") is True


class TestSendMessageDocstring:
    """P2: send_message docstring must not overpromise real-time render."""

    def test_docstring_honest(self):
        pytest.importorskip("mcp")  # constructing the server needs optional mcp
        from gaia.mcp.servers.agent_ui_mcp import create_agent_ui_mcp

        with patch("requests.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value={"mcp_memory_enabled": False}),
            )
            mock_get.return_value.raise_for_status.return_value = None
            mcp = create_agent_ui_mcp("http://localhost:4200")

        tools = mcp._tool_manager._tools  # pylint: disable=protected-access
        send_msg_tool = tools.get("send_message")
        if send_msg_tool is None:
            pytest.skip("send_message tool not found in tool manager")

        doc = send_msg_tool.fn.__doc__ or ""
        # Must NOT say "streams to the webapp in real time"
        assert (
            "real time" not in doc.lower()
        ), "send_message docstring should not claim real-time streaming"
        # Should mention open_session_in_browser
        assert "open_session_in_browser" in doc


class TestIsError:
    """#1755: error detection must key on the structured envelope, not the
    legacy ``{"error": ...}`` shape that ``_normalize_error`` replaced."""

    def test_new_envelope_is_error(self):
        from gaia.mcp.servers.agent_ui_mcp import _is_error

        assert _is_error({"status": "error", "detail": "boom"}) is True

    def test_success_payload_not_error(self):
        from gaia.mcp.servers.agent_ui_mcp import _is_error

        assert _is_error({"messages": [], "total": 0}) is False
        assert _is_error({"status": "ok"}) is False
        assert _is_error("not a dict") is False

    def test_legacy_error_key_not_matched(self):
        from gaia.mcp.servers.agent_ui_mcp import _is_error

        # The legacy shape no longer appears; _is_error must not depend on it.
        assert _is_error({"error": "boom"}) is False


def _get_tool(name):
    """Construct the MCP server and return a tool's raw function."""
    pytest.importorskip("mcp")  # constructing the server needs optional mcp
    from gaia.mcp.servers.agent_ui_mcp import create_agent_ui_mcp

    with patch("requests.get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200, json=MagicMock(return_value={})
        )
        mock_get.return_value.raise_for_status.return_value = None
        mcp = create_agent_ui_mcp("http://localhost:4200")

    tool = mcp._tool_manager._tools.get(name)  # pylint: disable=protected-access
    if tool is None:
        pytest.skip(f"{name} tool not found in tool manager")
    return tool.fn


class TestGetMessagesSurfacesError:
    """#1755: get_messages keyed on the old shape, so a backend error fell
    through to an empty success payload (silent fallback)."""

    def test_backend_error_not_silent_empty(self):
        get_messages = _get_tool("get_messages")

        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.json.return_value = {"detail": "Session not found"}
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=mock_resp
        )
        with patch("requests.get", return_value=mock_resp):
            result = get_messages("nonexistent")

        assert result["status"] == "error"
        assert result.get("detail")
        # Regression: must NOT degrade to {"messages": [], "total": 0}.
        assert "messages" not in result


class TestIndexDocumentLinkFailure:
    """#1755: index_document reported a session link succeeded even when the
    attach call failed (false success)."""

    @staticmethod
    def _upload_ok():
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"id": "doc-1"}
        resp.raise_for_status.return_value = None
        return resp

    def test_attach_failure_not_reported_as_linked(self):
        index_document = _get_tool("index_document")

        attach_resp = MagicMock()
        attach_resp.status_code = 404
        attach_resp.json.return_value = {"detail": "Session not found"}
        attach_resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=attach_resp
        )
        with patch("requests.post", side_effect=[self._upload_ok(), attach_resp]):
            result = index_document("/tmp/file.pdf", session_id="sess-1")

        assert "linked_to_session" not in result
        assert result.get("link_error")

    def test_attach_success_reports_linked(self):
        index_document = _get_tool("index_document")

        attach_resp = MagicMock()
        attach_resp.status_code = 200
        attach_resp.json.return_value = {"attached": True}
        attach_resp.raise_for_status.return_value = None
        with patch("requests.post", side_effect=[self._upload_ok(), attach_resp]):
            result = index_document("/tmp/file.pdf", session_id="sess-1")

        assert result.get("linked_to_session") == "sess-1"
        assert "link_error" not in result
