# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""#2110 — briefing + task tools are registered agent-loop tools.

Before this change the agent loop registered no briefing/task tools, so
"give me a daily briefing" and "extract action items" silently fell back to
a raw pre_scan fence. These tests pin: (1) the three tools register, (2)
get_briefing cold-generates a briefing, (3) extract_action_items drives a
fresh scan and populates the task store, (4) list_tasks reads them back.
"""

from __future__ import annotations

import json
from email.message import EmailMessage
from types import SimpleNamespace

import pytest

pytest.importorskip("gaia_agent_email")

from gaia_agent_email import task_store  # noqa: E402
from gaia_agent_email.tools.briefing_tools import BriefingToolsMixin  # noqa: E402

from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402
from gaia.database.mixin import DatabaseMixin  # noqa: E402
from tests.fixtures.email.fake_gmail import (  # noqa: E402
    FakeGmailBackend,
    mbox_message_to_gmail_payload,
)


def _make_msg(subject: str, body: str, sender: str = "a@example.com") -> dict:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "user@example.com"
    msg["Subject"] = subject
    msg["Message-ID"] = f"<{subject.replace(' ', '-')}@ex.example>"
    msg.set_content(body)
    return mbox_message_to_gmail_payload(msg)


class _Host(BriefingToolsMixin, DatabaseMixin):
    """Minimal EmailTriageAgent stand-in for the briefing/task tool surface."""

    def __init__(self, backend, db_path):
        self._gmail = backend
        self._backends = {"google": backend}
        self.config = SimpleNamespace(debug=False)
        self.init_db(str(db_path))
        task_store.init_schema(self)
        self._prescan_calls = 0

    def _pre_scan_all_backends(self, *, max_messages: int) -> dict:
        self._prescan_calls += 1
        return {
            "kind": "email_pre_scan",
            "urgent": [],
            "actionable": [],
            "informational_count": 3,
            "suggested_archives": [],
            "totals": {"urgent": 0, "actionable": 0, "informational": 3},
        }


@pytest.fixture
def host(tmp_path):
    backend = FakeGmailBackend()
    backend.add_message(
        _make_msg(
            "Project sync",
            "Hi there. Please review the design doc by Friday. Thanks!",
        )
    )
    backend.add_message(
        _make_msg(
            "Report",
            "Could you send the quarterly report? Kindly confirm receipt.",
            sender="b@example.com",
        )
    )
    h = _Host(backend, tmp_path / "state.db")
    try:
        yield h
    finally:
        h.close_db()


def _tool(host, name):
    _TOOL_REGISTRY.clear()
    host._register_briefing_tools()
    assert name in _TOOL_REGISTRY, f"{name} not registered"
    return _TOOL_REGISTRY[name]["function"]


def test_all_three_tools_register(host):
    _TOOL_REGISTRY.clear()
    host._register_briefing_tools()
    for name in ("get_briefing", "list_tasks", "extract_action_items"):
        assert name in _TOOL_REGISTRY


def test_get_briefing_cold_generates(host, monkeypatch, tmp_path):
    # Point the persisted-briefing path at an empty tmp file so load returns None.
    import gaia_agent_email.briefing as briefing

    monkeypatch.setattr(briefing, "briefing_path", lambda: tmp_path / "brief.json")
    fn = _tool(host, "get_briefing")
    out = json.loads(fn(max_messages=10))
    assert out["ok"] is True
    assert out["data"]["briefing"]["kind"] == "email_pre_scan"
    assert host._prescan_calls == 1  # generated fresh
    assert (tmp_path / "brief.json").exists()  # persisted


def test_get_briefing_returns_persisted_when_present(host, monkeypatch, tmp_path):
    import gaia_agent_email.briefing as briefing

    path = tmp_path / "brief.json"
    path.write_text(
        json.dumps({"generated_at": "2026-06-01T08:00:00Z", "briefing": {"kind": "x"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(briefing, "briefing_path", lambda: path)
    fn = _tool(host, "get_briefing")
    out = json.loads(fn(max_messages=10))
    assert out["ok"] is True
    assert out["data"]["generated_at"] == "2026-06-01T08:00:00Z"
    assert host._prescan_calls == 0  # did NOT regenerate


def test_extract_action_items_drives_scan_and_persists(host):
    fn = _tool(host, "extract_action_items")
    out = json.loads(fn(max_messages=10))
    assert out["ok"] is True
    assert out["data"]["messages_scanned"] == 2
    assert out["data"]["count"] >= 2  # at least one item per message
    assert out["data"]["tasks_created"] >= 2

    # The items were persisted — list_tasks reads them back.
    list_fn = _tool(host, "list_tasks")
    listed = json.loads(list_fn(status="open"))
    assert listed["ok"] is True
    assert listed["data"]["count"] >= 2


def test_extract_action_items_idempotent(host):
    fn = _tool(host, "extract_action_items")
    json.loads(fn(max_messages=10))
    second = json.loads(fn(max_messages=10))
    # Re-running creates no new tasks (dedup per message+description).
    assert second["data"]["tasks_created"] == 0


def test_list_tasks_empty_from_cold_store(host):
    fn = _tool(host, "list_tasks")
    out = json.loads(fn(status=""))
    assert out["ok"] is True
    assert out["data"]["count"] == 0


def test_list_tasks_rejects_bad_status(host):
    fn = _tool(host, "list_tasks")
    out = json.loads(fn(status="bogus"))
    assert out["ok"] is False
