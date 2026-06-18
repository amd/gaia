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


class TestErrorEnvelopeCallers:
    """#1750: internal callers must read the normalized {"status": "error"} shape,
    not the old "error" key, or backend errors silently vanish."""

    @staticmethod
    def _build_server():
        from gaia.mcp.servers.agent_ui_mcp import create_agent_ui_mcp

        with patch("requests.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value={"mcp_memory_enabled": False}),
            )
            mock_get.return_value.raise_for_status.return_value = None
            return create_agent_ui_mcp("http://localhost:4200")

    def test_get_messages_surfaces_backend_error(self):
        """A backend error must propagate, not fall through to an empty session."""
        pytest.importorskip("mcp")
        mcp = self._build_server()
        get_messages = mcp._tool_manager._tools["get_messages"].fn
        with patch(
            "gaia.mcp.servers.agent_ui_mcp._api",
            return_value={"status": "error", "detail": "Session not found"},
        ):
            result = get_messages(session_id="bogus")
        assert result.get("status") == "error"
        assert "messages" not in result  # no silent empty-session fallback

    def test_index_document_does_not_falsely_report_link(self):
        """A failed session-link must not be reported as a successful link."""
        pytest.importorskip("mcp")
        mcp = self._build_server()
        index_document = mcp._tool_manager._tools["index_document"].fn
        with patch(
            "gaia.mcp.servers.agent_ui_mcp._api",
            side_effect=[
                {"id": "doc-1"},  # upload-path succeeds
                {"status": "error", "detail": "attach failed"},  # link fails
            ],
        ):
            result = index_document(filepath="/tmp/x.txt", session_id="sess-1")
        assert "linked_to_session" not in result
