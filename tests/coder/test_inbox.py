# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for :mod:`gaia.coder.inbox` (Phase 5, §4.5)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from gaia.coder import inbox as inbox_mod
from gaia.coder.stores import em_inbox as em_inbox_store
from gaia.coder.stores import feedback as feedback_store


def _conn(tmp_path: Path):
    return em_inbox_store.open_store(tmp_path / "inbox.db")


def test_enqueue_returns_id_and_row_round_trips(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    try:
        msg_id = inbox_mod.enqueue(
            conn,
            severity="question",
            body="what tier",
            from_handle="kovtcharov-amd",
            channel="cli",
        )
        assert msg_id
        row = em_inbox_store.get_row(conn, msg_id)
        assert row is not None
        assert row.severity == "question"
        assert row.body == "what tier"
        assert row.state == "pending"
    finally:
        conn.close()


def test_enqueue_rejects_bad_severity(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    try:
        with pytest.raises(inbox_mod.InboxError, match="invalid severity"):
            inbox_mod.enqueue(
                conn,
                severity="shouting",
                body="hi",
                from_handle="kovtcharov-amd",
                channel="cli",
            )
    finally:
        conn.close()


def test_enqueue_rejects_empty_body(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    try:
        with pytest.raises(inbox_mod.InboxError, match="body is required"):
            inbox_mod.enqueue(
                conn,
                severity="info",
                body="   ",
                from_handle="kovtcharov-amd",
                channel="cli",
            )
    finally:
        conn.close()


def test_inbox_auto_ack_within_5s(tmp_path: Path) -> None:
    """§4.5 demands the ack reaches the original channel within 5 seconds.

    Measured end-to-end: enqueue + auto_ack + dispatch-callback round trip
    must complete well under the SLA — in practice we see sub-millisecond
    on a laptop. The 5000ms bound is a generous upper limit that still
    catches a regression if someone accidentally wires in an LLM call.
    """
    conn = _conn(tmp_path)
    try:
        posted: list[tuple[str, str]] = []

        def dispatch(channel: str, text: str) -> None:
            posted.append((channel, text))

        start = time.perf_counter()
        msg_id = inbox_mod.enqueue(
            conn,
            severity="question",
            body="are you alive?",
            from_handle="kovtcharov-amd",
            channel="cli",
        )
        ack = inbox_mod.auto_ack(conn, msg_id, eta_minutes=3, dispatch=dispatch)
        elapsed = time.perf_counter() - start

        assert elapsed < 5.0, f"auto-ack took {elapsed:.3f}s, >5s SLA"
        assert posted == [("cli", ack)]
        assert "next breakpoint" in ack
        assert "ETA: ~3 min" in ack

        row = em_inbox_store.get_row(conn, msg_id)
        assert row is not None
        assert row.ack_sent_at is not None
    finally:
        conn.close()


def test_auto_ack_missing_row_raises(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    try:
        with pytest.raises(inbox_mod.InboxError, match="unknown inbox id"):
            inbox_mod.auto_ack(conn, "does-not-exist", eta_minutes=1)
    finally:
        conn.close()


def test_mark_seen_is_idempotent(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    try:
        msg_id = inbox_mod.enqueue(
            conn,
            severity="info",
            body="fyi",
            from_handle="kovtcharov-amd",
            channel="cli",
        )
        inbox_mod.mark_seen(conn, msg_id)
        inbox_mod.mark_seen(conn, msg_id)  # still fine
        row = em_inbox_store.get_row(conn, msg_id)
        assert row.state == "seen"
    finally:
        conn.close()


def test_mark_answered_records_text_and_timestamp(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    try:
        msg_id = inbox_mod.enqueue(
            conn,
            severity="question",
            body="tier?",
            from_handle="kovtcharov-amd",
            channel="cli",
        )
        inbox_mod.mark_answered(conn, msg_id, "Tier 3")
        row = em_inbox_store.get_row(conn, msg_id)
        assert row.state == "answered"
        assert row.answer == "Tier 3"
        assert row.answered_at is not None
    finally:
        conn.close()


def test_inbox_escalate_to_feedback(tmp_path: Path) -> None:
    """Escalation moves a row from em_inbox → feedback.db with translated severity."""
    inbox_conn = _conn(tmp_path)
    fb_conn = feedback_store.open_store(tmp_path / "fb.db")
    try:
        msg_id = inbox_mod.enqueue(
            inbox_conn,
            severity="critical",
            body="regression shipped",
            from_handle="kovtcharov-amd",
            channel="gh-comment",
        )
        fb_id = inbox_mod.escalate(
            inbox_conn,
            msg_id,
            fb_conn,
            fix_class="tool",
            context_url="https://github.com/amd/gaia/pull/900",
        )
        assert fb_id

        fb_row = feedback_store.get_row(fb_conn, fb_id)
        assert fb_row is not None
        assert fb_row.severity == "critical"
        assert fb_row.body == "regression shipped"
        assert fb_row.fix_class == "tool"
        assert fb_row.context_url == "https://github.com/amd/gaia/pull/900"

        inbox_row = em_inbox_store.get_row(inbox_conn, msg_id)
        assert inbox_row.state == "escalated"
        assert inbox_row.escalated_to == fb_id
    finally:
        inbox_conn.close()
        fb_conn.close()


def test_inbox_escalate_translates_info_severity(tmp_path: Path) -> None:
    """info → low; question → med; critical → critical (docstring contract)."""
    inbox_conn = _conn(tmp_path)
    fb_conn = feedback_store.open_store(tmp_path / "fb.db")
    try:
        for inbox_sev, fb_sev in (
            ("info", "low"),
            ("question", "med"),
            ("critical", "critical"),
        ):
            msg_id = inbox_mod.enqueue(
                inbox_conn,
                severity=inbox_sev,
                body=f"{inbox_sev} thing",
                from_handle="kovtcharov-amd",
                channel="cli",
            )
            fb_id = inbox_mod.escalate(inbox_conn, msg_id, fb_conn)
            fb_row = feedback_store.get_row(fb_conn, fb_id)
            assert fb_row.severity == fb_sev, inbox_sev
    finally:
        inbox_conn.close()
        fb_conn.close()


def test_poll_at_breakpoint_returns_only_pending_oldest_first(
    tmp_path: Path,
) -> None:
    conn = _conn(tmp_path)
    try:
        # Seed with explicit timestamps so ordering is deterministic across
        # the sub-second clock resolution.
        a = inbox_mod.enqueue(
            conn,
            severity="info",
            body="first",
            from_handle="kovtcharov-amd",
            channel="cli",
            received_at="2026-04-20T10:00:00Z",
        )
        b = inbox_mod.enqueue(
            conn,
            severity="info",
            body="second",
            from_handle="kovtcharov-amd",
            channel="cli",
            received_at="2026-04-20T10:00:30Z",
        )
        c = inbox_mod.enqueue(
            conn,
            severity="info",
            body="third",
            from_handle="kovtcharov-amd",
            channel="cli",
            received_at="2026-04-20T10:01:00Z",
        )
        # Mark b seen; poll must skip it.
        inbox_mod.mark_seen(conn, b)

        pending = inbox_mod.poll_at_breakpoint(conn)
        ids = [r.id for r in pending]
        assert ids == [a, c]
    finally:
        conn.close()
