# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Task persistence from triage action items (#1605).

Acceptance criteria under test:
- Triaging a message with action cues creates task records linked to the
  source ``message_id``.
- Re-triaging the same message creates NO duplicate tasks.
- The inline ``action_items`` response is unchanged (additive).

Store-level tests exercise ``task_store`` directly against an in-memory
DatabaseMixin; surface-level tests drive the real ``POST /v1/email/triage``
route with a fake chat (no Lemonade) and an in-memory action DB injected via
the existing ``resolve_action_db`` seam.
"""

from __future__ import annotations

import json as _json
import sqlite3
import types as types_mod

import pytest

# The email agent ships as the standalone gaia-agent-email wheel (#1102);
# skip cleanly when a framework-only env lacks it.
pytest.importorskip("gaia_agent_email")

from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent_email import export_openapi, task_store  # noqa: E402
from gaia_agent_email.contract import ActionItem  # noqa: E402

from gaia.database.mixin import DatabaseMixin  # noqa: E402


class _DB(DatabaseMixin):
    pass


@pytest.fixture
def db():
    d = _DB()
    d.init_db(":memory:")
    task_store.init_schema(d)
    return d


# ---------------------------------------------------------------------------
# Store level
# ---------------------------------------------------------------------------


def test_record_creates_tasks_linked_to_message(db):
    items = [
        ActionItem(description="Please review the Q3 budget.", due_hint="Friday"),
        ActionItem(
            description="Follow up at https://example.com/doc",
            type="link",
            url="https://example.com/doc",
        ),
    ]
    created = task_store.record_action_items(db, message_id="m-1", items=items)
    assert len(created) == 2

    rows = task_store.list_tasks(db, message_id="m-1")
    assert [r["description"] for r in rows] == [
        "Please review the Q3 budget.",
        "Follow up at https://example.com/doc",
    ]
    assert all(r["message_id"] == "m-1" for r in rows)
    assert rows[0]["due_hint"] == "Friday"
    assert rows[1]["item_type"] == "link"
    assert rows[1]["url"] == "https://example.com/doc"
    assert all(r["status"] == "open" for r in rows)


def test_rerecord_same_message_creates_no_duplicates(db):
    items = [ActionItem(description="Please review the Q3 budget.")]
    first = task_store.record_action_items(db, message_id="m-1", items=items)
    assert len(first) == 1

    again = task_store.record_action_items(db, message_id="m-1", items=items)
    assert again == []
    assert len(task_store.list_tasks(db, message_id="m-1")) == 1


def test_concurrent_duplicate_insert_raises_integrity_error_is_skipped(db, monkeypatch):
    """A concurrent triage of the same message can win the pre-insert dedup
    check race and hit the UNIQUE index on the actual INSERT. That must be
    treated as the dedup invariant firing (skip), not re-raised as a failure
    that would turn a successful triage into a 500 (bot review on #1917)."""
    real_insert = db.insert
    calls = {"n": 0}

    def flaky_insert(table, data):
        calls["n"] += 1
        if calls["n"] == 1:
            # Simulate another connection having already inserted the same
            # (message_id, description_norm) row between our SELECT and INSERT.
            raise sqlite3.IntegrityError(
                "UNIQUE constraint failed: email_tasks.message_id, "
                "email_tasks.description_norm"
            )
        return real_insert(table, data)

    monkeypatch.setattr(db, "insert", flaky_insert)

    created = task_store.record_action_items(
        db,
        message_id="m-race",
        items=[
            ActionItem(description="Please review the Q3 budget."),
            ActionItem(description="A second, distinct item."),
        ],
    )

    # The first item lost the race (IntegrityError -> skipped); the second
    # item still gets recorded normally.
    assert len(created) == 1
    rows = task_store.list_tasks(db, message_id="m-race")
    assert [r["description"] for r in rows] == ["A second, distinct item."]


def test_non_integrity_error_on_insert_still_propagates(db, monkeypatch):
    """Only the dedup-race IntegrityError is swallowed. Any other exception
    (e.g. the sqlite file is locked/unwritable) is a real failure and must
    still propagate loudly — no silent fallback."""

    def broken_insert(table, data):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(db, "insert", broken_insert)

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        task_store.record_action_items(
            db, message_id="m-1", items=[ActionItem(description="x")]
        )


def test_dedup_key_is_normalized_description():
    # Whitespace/case drift in a re-extracted item must not duplicate the task.
    d = _DB()
    d.init_db(":memory:")
    task_store.init_schema(d)
    task_store.record_action_items(
        d, message_id="m-1", items=[ActionItem(description="Please review the doc")]
    )
    created = task_store.record_action_items(
        d, message_id="m-1", items=[ActionItem(description="  please REVIEW   the doc ")]
    )
    assert created == []
    assert len(task_store.list_tasks(d, message_id="m-1")) == 1


def test_same_item_different_messages_are_distinct_tasks(db):
    item = [ActionItem(description="Please review the doc")]
    task_store.record_action_items(db, message_id="m-1", items=item)
    task_store.record_action_items(db, message_id="m-2", items=item)
    assert len(task_store.list_tasks(db)) == 2
    assert len(task_store.list_tasks(db, message_id="m-2")) == 1


def test_record_without_message_id_fails_loudly(db):
    with pytest.raises(ValueError, match="message_id"):
        task_store.record_action_items(
            db, message_id="", items=[ActionItem(description="x")]
        )


def test_mark_task_done_is_idempotent(db):
    (task_id,) = task_store.record_action_items(
        db, message_id="m-1", items=[ActionItem(description="Please reply")]
    )
    task_store.mark_task_done(db, task_id=task_id)
    row = task_store.list_tasks(db, message_id="m-1")[0]
    assert row["status"] == "done"
    first_done = row["completed_at"]
    assert first_done is not None

    task_store.mark_task_done(db, task_id=task_id)
    assert task_store.list_tasks(db, message_id="m-1")[0]["completed_at"] == first_done
    assert task_store.list_tasks(db, status="open") == []


# ---------------------------------------------------------------------------
# REST surface — POST /v1/email/triage persists tasks as a side-effect
# ---------------------------------------------------------------------------


class _FakeChat:
    """Deterministic chat stub: classification JSON for classify calls,
    a fixed summary otherwise. No Lemonade needed."""

    def send_messages(self, messages, system_prompt="", **kwargs):
        resp = types_mod.SimpleNamespace()
        first = messages[0].get("content", "") if messages else ""
        resp.text = (
            _json.dumps(
                {"category": "NEEDS_RESPONSE", "confidence": 0.9, "reasoning": "t"}
            )
            if "Classify" in first
            else "summary"
        )
        return resp


def _triage_payload(message_id: str = "msg-77") -> dict:
    return {
        "payload": {
            "kind": "single",
            "principal": {"email": "me@example.com"},
            "message": {
                "message_id": message_id,
                "from": {"name": "Alice", "email": "alice@example.com"},
                "to": [{"email": "me@example.com"}],
                "subject": "Budget",
                "body": "Please review the Q3 budget and reply by Friday.",
            },
        }
    }


@pytest.fixture
def triage_env(monkeypatch, db):
    """Real /triage route, fake chat, in-memory task DB. Returns (client, db)."""
    from gaia_agent_email import api_routes as email_routes

    monkeypatch.setattr(
        email_routes.EmailTriageService,
        "_build_llm_chat",
        lambda self, **kw: _FakeChat(),
    )
    monkeypatch.setattr(email_routes, "resolve_action_db", lambda: db)
    return TestClient(export_openapi.build_app()), db


def test_triage_endpoint_persists_linked_tasks(triage_env):
    client, db = triage_env
    resp = client.post("/v1/email/triage", json=_triage_payload())
    assert resp.status_code == 200, resp.text

    # Inline response unchanged (additive): action items still returned.
    inline = resp.json()["result"]["action_items"]
    assert inline, "expected the cue-bearing body to yield inline action items"

    # ...and now also persisted, linked to the source message.
    rows = task_store.list_tasks(db, message_id="msg-77")
    assert [r["description"] for r in rows] == [i["description"] for i in inline]


def test_retriage_endpoint_creates_no_duplicate_tasks(triage_env):
    client, db = triage_env
    assert client.post("/v1/email/triage", json=_triage_payload()).status_code == 200
    count_after_first = len(task_store.list_tasks(db, message_id="msg-77"))
    assert count_after_first > 0

    assert client.post("/v1/email/triage", json=_triage_payload()).status_code == 200
    assert len(task_store.list_tasks(db, message_id="msg-77")) == count_after_first


def test_batch_triage_endpoint_persists_per_item_tasks(triage_env):
    client, db = triage_env
    item = _triage_payload("msg-batch-1")["payload"]
    resp = client.post("/v1/email/triage/batch", json={"items": [item, item]})
    assert resp.status_code == 200, resp.text

    # Two batch items for the SAME message dedup into one set of tasks.
    rows = task_store.list_tasks(db, message_id="msg-batch-1")
    inline = resp.json()["results"][0]["result"]["action_items"]
    assert [r["description"] for r in rows] == [i["description"] for i in inline]


# ---------------------------------------------------------------------------
# Persistence failures must not turn a successful triage into a 500
# (bot review on #1917: sqlite3.IntegrityError from a concurrent duplicate
# should not collapse the response; and /triage/batch's documented per-item
# isolation must hold for persistence failures too, not just triage failures).
# ---------------------------------------------------------------------------


def test_single_triage_persistence_failure_does_not_500(triage_env, monkeypatch):
    def boom(db, *, message_id, items):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(task_store, "record_action_items", boom)

    client, _db = triage_env
    resp = client.post("/v1/email/triage", json=_triage_payload("msg-boom"))

    # Triage itself succeeded; persistence failing must not surface as a 500.
    assert resp.status_code == 200, resp.text
    assert resp.json()["result"]["action_items"]


def test_batch_triage_persistence_failure_is_isolated_per_item(triage_env, monkeypatch):
    """One item's persistence failure must not collapse the whole batch into
    a 500, and must not flip that item's already-successful `result` into an
    `error` — only the triage step, not the persistence side-effect, decides
    result vs error for a batch item."""
    real_record = task_store.record_action_items

    def flaky_record(db, *, message_id, items):
        if message_id == "msg-batch-boom":
            raise sqlite3.OperationalError("database is locked")
        return real_record(db, message_id=message_id, items=items)

    monkeypatch.setattr(task_store, "record_action_items", flaky_record)

    client, db = triage_env
    item_ok = _triage_payload("msg-batch-ok")["payload"]
    item_boom = _triage_payload("msg-batch-boom")["payload"]
    resp = client.post(
        "/v1/email/triage/batch", json={"items": [item_boom, item_ok]}
    )

    assert resp.status_code == 200, resp.text
    results = resp.json()["results"]
    # Both items still report a successful triage `result` (not `error`) —
    # persistence failing for one is invisible to the triage contract.
    assert results[0]["result"] is not None
    assert results[0]["error"] is None
    assert results[1]["result"] is not None
    assert results[1]["error"] is None

    # The item whose persistence failed has no task rows; the other does.
    assert task_store.list_tasks(db, message_id="msg-batch-boom") == []
    assert len(task_store.list_tasks(db, message_id="msg-batch-ok")) > 0
