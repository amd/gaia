# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Scheduled daily inbox briefing (#1608).

Covers the issue's two test acceptance criteria plus the fail-loud config
surface and the REST pull endpoint:

1. The scheduled job invokes ``pre_scan_inbox`` and produces the
   ``email_pre_scan`` envelope.
2. A disabled schedule produces no briefing.

No live mailbox, no LLM — the pre-scan runs its heuristic path against an
in-memory fake backend (same shape as ``test_rest_contract``'s).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from gaia_agent_email import export_openapi
from gaia_agent_email.briefing import (
    BriefingConfigError,
    BriefingScheduleConfig,
    BriefingScheduler,
    BriefingUnavailableError,
    load_latest_briefing,
    persist_briefing,
    run_briefing_job,
    seconds_until_next_run,
    summarize_briefing,
)


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    """Redirect ``Path.home()`` so tests never touch the real ``~/.gaia``."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)


def _gmail_message(
    msg_id: str, *, subject: str, sender: str, label_ids: list[str], snippet: str = ""
) -> dict:
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
    """In-memory backend exposing the read calls pre_scan_inbox_impl uses."""

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
# AC test 1 — the scheduled job invokes pre_scan_inbox and produces the
# email_pre_scan envelope.
# ---------------------------------------------------------------------------


def test_briefing_job_produces_email_pre_scan_envelope():
    backend = _fake_backend()
    delivered = []

    record = run_briefing_job(backend, max_messages=10, sink=delivered.append)

    # The job actually read the inbox through the backend (pre_scan_inbox ran).
    assert backend.list_calls >= 1
    # Delivered exactly what it returned.
    assert delivered == [record]
    # The payload is the agent's email_pre_scan envelope, untouched.
    briefing = record["briefing"]
    assert briefing["kind"] == "email_pre_scan"
    assert set(briefing) == {
        "kind",
        "urgent",
        "actionable",
        "informational_count",
        "suggested_archives",
        "suggested_drafts",
        "preferences_applied",
        "totals",
    }
    assert any(i["message_id"] == "m1" for i in briefing["suggested_archives"])
    assert record["generated_at"]  # stamped for the pull surface


def test_briefing_job_default_sink_persists_to_disk(tmp_path):
    run_briefing_job(_fake_backend(), max_messages=10)

    path = tmp_path / ".gaia" / "email" / "briefing_latest.json"
    assert path.exists()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["briefing"]["kind"] == "email_pre_scan"
    assert load_latest_briefing() == on_disk


def test_briefing_job_no_mailbox_fails_loud(monkeypatch):
    # Backend resolution reuses the REST pre-scan resolver; with no mailbox
    # connected the job raises the same actionable message — never an empty
    # briefing.
    monkeypatch.setattr(
        "gaia_agent_email.api_routes.connected_mailbox_providers", lambda: []
    )
    with pytest.raises(BriefingUnavailableError, match="No mailbox connected"):
        run_briefing_job(max_messages=10, sink=lambda record: None)


# ---------------------------------------------------------------------------
# AC test 2 — a disabled schedule produces no briefing.
# ---------------------------------------------------------------------------


def test_disabled_schedule_produces_no_briefing(tmp_path):
    ran = []

    async def scenario():
        scheduler = BriefingScheduler(
            BriefingScheduleConfig(enabled=False),
            run_job=lambda **kw: ran.append(kw),
        )
        started = scheduler.start()
        # Give the loop a chance to run anything it (wrongly) scheduled.
        await asyncio.sleep(0.05)
        await scheduler.stop()
        return started

    started = asyncio.run(scenario())

    assert started is False
    assert ran == []  # the job never ran
    assert load_latest_briefing() is None  # nothing persisted
    assert not (tmp_path / ".gaia" / "email" / "briefing_latest.json").exists()


def test_enabled_schedule_fires_the_job(monkeypatch):
    fired = asyncio.Event()
    ran = []

    def fake_job(**kwargs):
        ran.append(kwargs)
        fired.set()

    # Collapse the wait so the daily fire happens immediately.
    monkeypatch.setattr(
        "gaia_agent_email.briefing.seconds_until_next_run", lambda *_: 0.0
    )

    async def scenario():
        scheduler = BriefingScheduler(
            BriefingScheduleConfig(enabled=True, max_messages=7), run_job=fake_job
        )
        assert scheduler.start() is True
        await asyncio.wait_for(fired.wait(), timeout=2)
        await scheduler.stop()

    asyncio.run(scenario())

    assert ran and ran[0] == {"max_messages": 7}


# ---------------------------------------------------------------------------
# Config — explicit, off by default, fail-loud.
# ---------------------------------------------------------------------------


def test_config_defaults_are_off():
    config = BriefingScheduleConfig.from_env(environ={})
    assert config.enabled is False
    assert config.time_of_day == "08:00"
    assert config.max_messages == 25


def test_config_env_opt_in():
    config = BriefingScheduleConfig.from_env(
        environ={
            "GAIA_EMAIL_BRIEFING_ENABLED": "true",
            "GAIA_EMAIL_BRIEFING_TIME": "06:30",
            "GAIA_EMAIL_BRIEFING_MAX_MESSAGES": "50",
        }
    )
    assert config.enabled is True
    assert config.time_of_day == "06:30"
    assert config.max_messages == 50


@pytest.mark.parametrize(
    "environ",
    [
        {"GAIA_EMAIL_BRIEFING_ENABLED": "maybe"},
        {"GAIA_EMAIL_BRIEFING_TIME": "8am"},
        {"GAIA_EMAIL_BRIEFING_TIME": "25:00"},
        {"GAIA_EMAIL_BRIEFING_MAX_MESSAGES": "many"},
        {"GAIA_EMAIL_BRIEFING_MAX_MESSAGES": "0"},
        {"GAIA_EMAIL_BRIEFING_MAX_MESSAGES": "101"},
    ],
)
def test_config_invalid_values_fail_loud(environ):
    with pytest.raises(BriefingConfigError):
        BriefingScheduleConfig.from_env(environ=environ)


def test_seconds_until_next_run_today_and_tomorrow():
    now = datetime(2026, 7, 1, 7, 0, 0)
    assert seconds_until_next_run("08:00", now) == 3600.0
    # Already past today's fire time → tomorrow.
    assert seconds_until_next_run("08:00", now.replace(hour=9)) == 23 * 3600.0


# ---------------------------------------------------------------------------
# REST pull surface — GET /v1/email/briefing
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    return TestClient(export_openapi.build_app())


def test_get_briefing_404_before_any_scheduled_run(client):
    resp = client.get("/v1/email/briefing")
    assert resp.status_code == 404
    assert "GAIA_EMAIL_BRIEFING_ENABLED" in resp.json()["detail"]


def test_get_briefing_returns_latest_persisted_run(client):
    record = run_briefing_job(_fake_backend(), max_messages=10)

    resp = client.get("/v1/email/briefing")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["generated_at"] == record["generated_at"]
    assert body["briefing"]["kind"] == "email_pre_scan"
    assert body["briefing"]["totals"]["suggested_archives"] >= 1


def test_get_briefing_corrupt_file_fails_loud(client, tmp_path):
    path = tmp_path / ".gaia" / "email" / "briefing_latest.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    resp = client.get("/v1/email/briefing")
    assert resp.status_code == 500
    assert "unreadable" in resp.json()["detail"]


def test_persist_briefing_is_atomic_and_loadable(tmp_path):
    dest = tmp_path / "nested" / "briefing.json"
    record = {"generated_at": "2026-07-01T08:00:00+00:00", "briefing": {"kind": "x"}}
    persist_briefing(record, path=dest)
    assert load_latest_briefing(path=dest) == record
    assert not dest.with_suffix(".json.tmp").exists()


# ---------------------------------------------------------------------------
# #2525 — the briefing must carry a structured breakdown, not just a count.
#
# The pre-scan envelope already has the urgency/category counts, the
# individual urgent/actionable items, and the applied preferences — the bug
# was that the LLM was told to collapse all of it into "one short framing
# sentence" and discard the rest. ``summarize_briefing`` computes the
# breakdown deterministically (in code, not by the model) so the headline can
# never assert an urgency judgement the classification did not itself make.
# ---------------------------------------------------------------------------


def _mixed_envelope() -> dict:
    return {
        "kind": "email_pre_scan",
        "urgent": [
            {
                "message_id": "u1",
                "thread_id": "t-u1",
                "sender": "boss@corp.example",
                "subject": "Server down — need you now",
                "why": "urgent keyword + escalation",
            }
        ],
        "actionable": [
            {
                "message_id": "a1",
                "thread_id": "t-a1",
                "sender": "alice@corp.example",
                "subject": "Can you review this by EOD?",
                "why": "needs a reply",
            }
        ],
        "informational_count": 23,
        "suggested_archives": [
            {
                "message_id": "s1",
                "thread_id": "t-s1",
                "sender": "deals@shop.example",
                "subject": "50% off this weekend!",
                "reason": "promotional",
            }
        ],
        "suggested_drafts": [],
        "preferences_applied": {
            "priority_senders": ["boss@corp.example"],
            "low_priority_senders": ["newsletters@techcrunch.com"],
            "category_defaults": {},
        },
        "totals": {
            "urgent": 1,
            "actionable": 1,
            "informational": 23,
            "suggested_archives": 1,
        },
    }


def _all_informational_envelope() -> dict:
    return {
        "kind": "email_pre_scan",
        "urgent": [],
        "actionable": [],
        "informational_count": 25,
        "suggested_archives": [],
        "suggested_drafts": [],
        "preferences_applied": {
            "priority_senders": [],
            "low_priority_senders": [],
            "category_defaults": {},
        },
        "totals": {
            "urgent": 0,
            "actionable": 0,
            "informational": 25,
            "suggested_archives": 0,
        },
    }


def test_briefing_summary_reports_all_three_counts():
    summary = summarize_briefing(_mixed_envelope())

    assert summary["breakdown"] == {
        "urgent": 1,
        "actionable": 1,
        "informational": 23,
        "suggested_archives": 1,
    }
    assert summary["total_scanned"] == 26
    assert summary["needs_attention"] is True


def test_briefing_summary_all_informational_states_it_plainly():
    summary = summarize_briefing(_all_informational_envelope())

    assert summary["needs_attention"] is False
    assert summary["total_scanned"] == 25
    assert summary["breakdown"]["informational"] == 25
    # The headline must plainly say nothing needs attention, not pad it.
    assert "nothing" in summary["headline"].lower()
    assert "25" in summary["headline"]


def test_briefing_summary_surfaces_messages_individually():
    summary = summarize_briefing(_mixed_envelope())

    # Not just a count — the actual urgent + actionable messages, individually.
    subjects = {item["subject"] for item in summary["highlights"]}
    assert subjects == {"Server down — need you now", "Can you review this by EOD?"}
    urgencies = {item["message_id"]: item["urgency"] for item in summary["highlights"]}
    assert urgencies == {"u1": "urgent", "a1": "actionable"}


def test_briefing_summary_names_applied_preferences():
    summary = summarize_briefing(_mixed_envelope())

    assert "priority sender: boss@corp.example" in summary["preferences_applied"]
    assert (
        "low priority sender: newsletters@techcrunch.com"
        in summary["preferences_applied"]
    )


def test_briefing_summary_never_asserts_unset_urgency():
    # An envelope where the classifier found nothing urgent/actionable must
    # never be summarized as needing attention, no matter what informational
    # count is present — the honesty rule from #2525.
    summary = summarize_briefing(_all_informational_envelope())
    assert summary["needs_attention"] is False
    assert summary["breakdown"]["urgent"] == 0
    assert summary["breakdown"]["actionable"] == 0


def test_briefing_summary_tolerates_missing_optional_fields():
    # A stub/legacy envelope missing keys (e.g. a bare {"kind": "x"} persisted
    # by an older build) must not crash the summary — it degrades to zeros.
    summary = summarize_briefing({"kind": "x"})
    assert summary["total_scanned"] == 0
    assert summary["needs_attention"] is False
    assert summary["highlights"] == []
    assert summary["preferences_applied"] == []
