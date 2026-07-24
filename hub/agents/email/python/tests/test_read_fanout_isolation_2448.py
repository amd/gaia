# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Per-provider failure isolation in the read-tool fan-out (#2448).

``list_inbox`` / ``search_messages`` fan out over every connected mailbox. A
broken token on ONE provider (e.g. Microsoft ``invalid_request`` on refresh)
must NOT abort the call across a healthy Google mailbox: the healthy mailbox's
results still come back, the broken one is recorded under ``mailbox_errors``,
and only when EVERY mailbox fails does the tool return an error envelope.

Hermetic: two FakeGmailBackends only, no Lemonade, no network.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.tools.read_tools import ReadToolsMixin  # noqa: E402

from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402
from gaia.connectors.errors import ConnectorsError  # noqa: E402

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _msg(msg_id: str, subject: str = "Hello") -> Dict[str, Any]:
    return {
        "id": msg_id,
        "threadId": msg_id,
        "labelIds": ["INBOX"],
        "snippet": subject,
        "internalDate": "1750000000000",
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": "alice@example.com"},
                {"name": "To", "value": "user@example.com"},
                {"name": "Date", "value": "Mon, 1 Jan 2026 00:00:00 +0000"},
            ],
            "body": {"data": _b64url("body"), "size": 4},
        },
        "sizeEstimate": 4,
    }


class _BrokenBackend:
    """Backend whose every read raises ``ConnectorsError`` — a stale token."""

    def list_messages(self, **_kwargs: Any) -> Dict[str, Any]:
        raise ConnectorsError(
            "Microsoft token refresh failed (invalid_request). Reconnect the "
            "Microsoft account in Settings → Connectors."
        )

    def get_message(self, message_id: str) -> Dict[str, Any]:  # pragma: no cover
        raise ConnectorsError("Microsoft token refresh failed (invalid_request).")


class _Host(ReadToolsMixin):
    """Minimal tool-hosting stand-in with an ordered backends map."""

    def __init__(self, backends: Dict[str, Any]):
        self._gmail = next(iter(backends.values()))
        self._backends = backends
        self._message_mailbox: Dict[str, str] = {}
        self.config = SimpleNamespace(debug=False)

    def _remember_message_mailbox(self, message_id, provider):
        if message_id:
            self._message_mailbox[message_id] = provider


def _tool(host: _Host, name: str):
    _TOOL_REGISTRY.clear()
    host._register_read_tools()
    assert name in _TOOL_REGISTRY
    return _TOOL_REGISTRY[name]["function"]


def _healthy_google() -> FakeGmailBackend:
    gmail = FakeGmailBackend(user_email="user@example.com")
    gmail.add_message(_msg("g1", "From Google"))
    gmail.add_message(_msg("g2", "Also Google"))
    return gmail


# ---------------------------------------------------------------------------
# One provider broken, one healthy → partial success (the #2448 fix)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_name", ["list_inbox", "search_messages"])
def test_broken_provider_does_not_abort_healthy_mailbox(tool_name):
    # Microsoft listed first so its failure happens BEFORE the healthy Google
    # backend is scanned — the pre-fix bug aborted the whole fan-out here.
    host = _Host({"microsoft": _BrokenBackend(), "google": _healthy_google()})
    tool = _tool(host, tool_name)

    envelope = json.loads(
        tool(query="Google") if tool_name == "search_messages" else tool(max_results=25)
    )

    assert envelope["ok"] is True
    data = envelope["data"]
    # Healthy mailbox's messages survived.
    assert data["messages"], "healthy Google mailbox should still return results"
    assert all(m["mailbox"] == "google" for m in data["messages"])
    # Broken mailbox recorded, not silently dropped.
    assert len(data["mailbox_errors"]) == 1
    assert data["mailbox_errors"][0]["mailbox"] == "microsoft"
    assert "invalid_request" in data["mailbox_errors"][0]["error"]


# ---------------------------------------------------------------------------
# Every provider broken → loud error, not a misleading empty result
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_name", ["list_inbox", "search_messages"])
def test_all_providers_broken_returns_error_envelope(tool_name):
    host = _Host({"microsoft": _BrokenBackend(), "outlook2": _BrokenBackend()})
    tool = _tool(host, tool_name)

    envelope = json.loads(
        tool(query="x") if tool_name == "search_messages" else tool(max_results=25)
    )

    assert envelope["ok"] is False
    assert "invalid_request" in envelope["error"]


# ---------------------------------------------------------------------------
# No failures → envelope carries no mailbox_errors key (contract unchanged)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_name", ["list_inbox", "search_messages"])
def test_all_healthy_has_no_mailbox_errors_key(tool_name):
    host = _Host({"google": _healthy_google()})
    tool = _tool(host, tool_name)

    envelope = json.loads(
        tool(query="Google") if tool_name == "search_messages" else tool(max_results=25)
    )

    assert envelope["ok"] is True
    assert "mailbox_errors" not in envelope["data"]
