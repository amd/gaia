# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Scheduled daily inbox briefing tests (#1608).

Covers the issue's two test acceptance criteria at the Python seam —
(1) the scheduled job invokes ``pre_scan_inbox`` and produces the
``email_pre_scan`` envelope, (2) a disabled schedule produces no briefing
— plus the schedule persistence contract (off by default, fail-loud on a
corrupt file) and the REST trigger surface. No live mailbox, no LLM.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import pytest

# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102);
# skip cleanly when a framework-only env lacks it.
pytest.importorskip("gaia_agent_email")

from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent_email import briefing, export_openapi  # noqa: E402
from gaia_agent_email.briefing import (  # noqa: E402
    BriefingConfigError,
    load_schedule,
    run_scheduled_briefing,
    save_schedule,
)
from gaia_agent_email.contract import BriefingSchedule  # noqa: E402


def _gmail_message(
    msg_id: str,
    *,
    subject: str,
    sender: str,
    label_ids: list[str],
    snippet: str = "",
) -> dict:
    """Build a minimal Gmail-API-v1-shaped message the pre-scan path reads."""
    return {
        "id": msg_id,
        "threadId": f"t-{msg_id}",
        "labelIds": label_ids,
        "snippet": snippet,
        "payload": {
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
            ],
            "mimeType": "text/plain",
            "body": {"data": ""},
        },
    }


class _FakeBackend:
    """In-memory backend exposing just the read calls pre_scan_inbox_impl uses."""

    def __init__(self, messages: list[dict]):
        self._messages = {m["id"]: m for m in messages}
        self.list_calls = 0

    def list_messages(self, *, label_ids=None, max_results=25, **_):  # noqa: ANN001
        self.list_calls += 1
        ids = list(self._messages)[:max_results]
        return {
            "messages": [
                {"id": i, "threadId": self._messages[i]["threadId"]} for i in ids
            ],
            "nextPageToken": None,
        }

    def get_message(self, message_id: str) -> dict:
        return self._messages[message_id]


class _ExplodingBackend:
    """A backend that fails the test if the briefing touches the mailbox."""

    def __getattr__(self, name: str):
        raise AssertionError(
            f"disabled schedule must not touch the mailbox (called {name!r})"
        )


def _fake_backend() -> _FakeBackend:
    return _FakeBackend(
        [
            _gmail_message(
                "m1",
                subject="50% off this weekend!",
                sender="deals@shop.example",
                label_ids=["INBOX", "CATEGORY_PROMOTIONS"],
            ),
            _gmail_message(
                "m2",
                subject="Project sync notes",
                sender="alice@corp.example",
                label_ids=["INBOX"],
                snippet="Sharing the notes from today's sync.",
            ),
        ]
    )


# ---------------------------------------------------------------------------
# Schedule persistence — off by default, fail-loud on corruption.
# ---------------------------------------------------------------------------


def test_absent_schedule_defaults_to_disabled(tmp_path):
    sched = load_schedule(tmp_path / "briefing.json")
    assert sched.enabled is False
    assert sched.time == "08:00"
    assert sched.max_messages == 25


def test_save_load_round_trip(tmp_path):
    path = tmp_path / "briefing.json"
    saved = BriefingSchedule(enabled=True, time="07:30", max_messages=50)
    save_schedule(saved, path)
    assert load_schedule(path) == saved
    # Atomic write leaves no temp file behind.
    assert list(tmp_path.iterdir()) == [path]


def test_corrupt_schedule_fails_loudly(tmp_path):
    path = tmp_path / "briefing.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(BriefingConfigError, match=re.escape(str(path))):
        load_schedule(path)


def test_invalid_schedule_fields_fail_loudly(tmp_path):
    path = tmp_path / "briefing.json"
    path.write_text(json.dumps({"enabled": True, "time": "25:99"}), encoding="utf-8")
    with pytest.raises(BriefingConfigError, match="briefing/schedule"):
        load_schedule(path)


# ---------------------------------------------------------------------------
# The scheduled job — the issue's two test acceptance criteria.
# ---------------------------------------------------------------------------


def test_scheduled_job_produces_pre_scan_envelope(tmp_path):
    """AC: the scheduled job invokes pre_scan_inbox → email_pre_scan envelope."""
    backend = _fake_backend()
    latest = tmp_path / "briefing_latest.json"
    now = datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc)

    out = run_scheduled_briefing(
        backend,
        schedule=BriefingSchedule(enabled=True),
        latest_path=latest,
        now=now,
    )

    assert out is not None
    assert out["kind"] == "email_briefing"
    assert out["generated_at"] == now.isoformat()
    assert out["schedule"]["enabled"] is True
    # The payload IS the pre-scan envelope — produced by pre_scan_inbox_impl,
    # which the fake backend proves was invoked (it listed + read messages).
    assert out["pre_scan"]["kind"] == "email_pre_scan"
    assert backend.list_calls == 1
    archived = {m["message_id"] for m in out["pre_scan"]["suggested_archives"]}
    assert "m1" in archived  # the promotional message

    # Delivery stub: the envelope persists as the latest briefing.
    assert json.loads(latest.read_text(encoding="utf-8")) == out


def test_disabled_schedule_produces_no_briefing(tmp_path):
    """AC: a disabled schedule produces no briefing and never reads mail."""
    latest = tmp_path / "briefing_latest.json"

    out = run_scheduled_briefing(
        _ExplodingBackend(),
        schedule=BriefingSchedule(enabled=False),
        latest_path=latest,
    )

    assert out is None
    assert not latest.exists()


def test_trigger_reads_persisted_schedule(tmp_path):
    """Without an explicit schedule, the trigger loads the persisted one —
    the path a dumb scheduler exercises."""
    sched_path = tmp_path / "briefing.json"
    save_schedule(BriefingSchedule(enabled=True, max_messages=10), sched_path)

    out = run_scheduled_briefing(
        _fake_backend(),
        schedule_path=sched_path,
        latest_path=tmp_path / "latest.json",
    )
    assert out is not None
    assert out["schedule"]["max_messages"] == 10


# ---------------------------------------------------------------------------
# REST trigger surface.
# ---------------------------------------------------------------------------


@pytest.fixture
def briefing_client(tmp_path, monkeypatch) -> TestClient:
    """A client whose briefing paths live under tmp_path and whose pre-scan
    backend is the in-memory fake."""
    from gaia_agent_email import api_routes

    monkeypatch.setattr(
        briefing, "briefing_schedule_path", lambda: tmp_path / "briefing.json"
    )
    monkeypatch.setattr(
        briefing, "latest_briefing_path", lambda: tmp_path / "briefing_latest.json"
    )
    monkeypatch.setattr(api_routes, "get_prescan_backend", _fake_backend)
    return TestClient(export_openapi.build_app())


def test_rest_schedule_defaults_off(briefing_client):
    resp = briefing_client.get("/v1/email/briefing/schedule")
    assert resp.status_code == 200
    assert resp.json()["schedule"]["enabled"] is False


def test_rest_schedule_put_get_round_trip(briefing_client):
    body = {"enabled": True, "time": "07:15", "max_messages": 40}
    resp = briefing_client.put("/v1/email/briefing/schedule", json=body)
    assert resp.status_code == 200
    assert resp.json()["schedule"] == body

    resp = briefing_client.get("/v1/email/briefing/schedule")
    assert resp.json()["schedule"] == body


def test_rest_schedule_rejects_bad_time_loudly(briefing_client):
    resp = briefing_client.put(
        "/v1/email/briefing/schedule", json={"enabled": True, "time": "9am"}
    )
    assert resp.status_code == 422


def test_rest_run_disabled_is_409(briefing_client, tmp_path):
    resp = briefing_client.post("/v1/email/briefing/run")
    assert resp.status_code == 409
    assert "disabled" in resp.json()["detail"]
    assert not (tmp_path / "briefing_latest.json").exists()


def test_rest_run_enabled_returns_briefing(briefing_client, tmp_path):
    briefing_client.put(
        "/v1/email/briefing/schedule", json={"enabled": True, "time": "08:00"}
    )
    resp = briefing_client.post("/v1/email/briefing/run")
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["kind"] == "email_briefing"
    assert result["pre_scan"]["kind"] == "email_pre_scan"
    assert (tmp_path / "briefing_latest.json").exists()
