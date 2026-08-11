# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Contract tests for the attention-view read-model (#2582).

Covers the additive schema 2.8 bump to ``gaia_agent_email.contract``:

- ``AttentionItemKind`` — the four source signals an item can carry.
- ``AttentionItem`` — one surfaced item, tagged with why it needs attention.
- ``AttentionCoverage`` — the honesty fields (scanned / total_unread /
  scan_truncated / degraded / mailbox_errors) the renderer needs to state
  what was actually covered, mirroring #2584's pre-scan coverage fields.
- ``EmailAttentionResult`` / ``EmailAttentionResponse`` — the top-level
  envelope, including ``cache_age_seconds`` / ``stale`` so a cached result
  is never presented as current.

Every model in ``contract.py`` sets ``extra="forbid"`` — these tests also
confirm the new models keep that fail-loudly contract.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")  # noqa: E402

from gaia_agent_email.contract import (  # noqa: E402
    SCHEMA_VERSION,
    AttentionCoverage,
    AttentionItem,
    AttentionItemKind,
    EmailAttentionResponse,
    EmailAttentionResult,
    MailboxError,
    MessageError,
)


class TestSchemaVersionBump:
    def test_schema_version_is_2_12(self):
        # Bumped again by #2829 (POST /v1/email/query gains optional
        # session_id) since this file's #2582 attention-view bump to 2.8 --
        # additive like every bump before it, so this is a routine
        # version-pin update, not a contract regression.
        assert SCHEMA_VERSION == "2.12"


class TestAttentionItemKind:
    def test_four_kinds_exist(self):
        assert AttentionItemKind.MEETING_REQUEST == "meeting_request"
        assert AttentionItemKind.WAITING_ON_YOU == "waiting_on_you"
        assert AttentionItemKind.NEEDS_REVIEW == "needs_review"
        assert AttentionItemKind.ACTION_ITEM == "action_item"


class TestAttentionItem:
    def test_minimal_item_defaults(self):
        item = AttentionItem(kind="meeting_request", why="proposes a meeting")
        assert item.message_id is None
        assert item.thread_id is None
        assert item.sender == ""
        assert item.subject == ""
        assert item.due_hint is None
        assert item.mailbox is None

    def test_full_item_round_trips(self):
        item = AttentionItem(
            kind=AttentionItemKind.WAITING_ON_YOU,
            message_id="m1",
            thread_id="t1",
            sender="a@example.com",
            subject="Quick question",
            why="waiting 3d on your reply",
            mailbox="google",
        )
        assert item.kind == AttentionItemKind.WAITING_ON_YOU
        assert item.mailbox == "google"

    def test_why_is_required(self):
        with pytest.raises(ValidationError):
            AttentionItem(kind="needs_review")

    def test_kind_is_required(self):
        with pytest.raises(ValidationError):
            AttentionItem(why="something")

    def test_rejects_unknown_kind(self):
        with pytest.raises(ValidationError):
            AttentionItem(kind="bogus_kind", why="x")

    def test_forbids_unknown_fields(self):
        with pytest.raises(ValidationError):
            AttentionItem(kind="action_item", why="x", extra_field="nope")


class TestAttentionCoverage:
    def test_defaults(self):
        cov = AttentionCoverage()
        assert cov.scanned == 0
        assert cov.total_unread is None
        assert cov.scan_truncated is False
        assert cov.degraded is False
        assert cov.mailbox_errors is None

    def test_accepts_mailbox_errors(self):
        cov = AttentionCoverage(
            scanned=10,
            total_unread=25,
            scan_truncated=True,
            degraded=True,
            mailbox_errors=[MailboxError(mailbox="microsoft", error="token expired")],
        )
        assert cov.mailbox_errors[0].mailbox == "microsoft"
        assert cov.scan_truncated is True

    def test_accepts_message_errors(self):
        cov = AttentionCoverage(
            scanned=10,
            degraded=True,
            message_errors=[
                MessageError(message_id="m1", error="rate-limited, try again")
            ],
        )
        assert cov.message_errors[0].message_id == "m1"
        assert cov.mailbox_errors is None

    def test_forbids_unknown_fields(self):
        with pytest.raises(ValidationError):
            AttentionCoverage(bogus=True)


class TestMessageError:
    def test_forbids_unknown_fields(self):
        with pytest.raises(ValidationError):
            MessageError(message_id="m1", error="x", bogus=True)


class TestEmailAttentionResult:
    def _coverage(self, **overrides):
        return AttentionCoverage(**overrides)

    def test_kind_discriminator_defaults(self):
        result = EmailAttentionResult(
            coverage=self._coverage(), generated_at="2026-07-28T00:00:00+00:00"
        )
        assert result.kind == "email_attention"
        assert result.items == []

    def test_cache_age_and_stale_default(self):
        result = EmailAttentionResult(
            coverage=self._coverage(), generated_at="2026-07-28T00:00:00+00:00"
        )
        assert result.cache_age_seconds == 0.0
        assert result.stale is False

    def test_cache_age_and_stale_settable(self):
        result = EmailAttentionResult(
            coverage=self._coverage(),
            generated_at="2026-07-28T00:00:00+00:00",
            cache_age_seconds=612.5,
            stale=True,
        )
        assert result.cache_age_seconds == 612.5
        assert result.stale is True

    def test_cache_age_rejects_negative(self):
        with pytest.raises(ValidationError):
            EmailAttentionResult(
                coverage=self._coverage(),
                generated_at="2026-07-28T00:00:00+00:00",
                cache_age_seconds=-1,
            )

    def test_coverage_is_required(self):
        with pytest.raises(ValidationError):
            EmailAttentionResult(generated_at="2026-07-28T00:00:00+00:00")

    def test_generated_at_is_required(self):
        with pytest.raises(ValidationError):
            EmailAttentionResult(coverage=self._coverage())

    def test_accepts_items_list(self):
        item = AttentionItem(kind="meeting_request", why="proposes a meeting")
        result = EmailAttentionResult(
            items=[item],
            coverage=self._coverage(scanned=5),
            generated_at="2026-07-28T00:00:00+00:00",
        )
        assert len(result.items) == 1
        assert result.items[0].kind == AttentionItemKind.MEETING_REQUEST

    def test_forbids_unknown_fields(self):
        with pytest.raises(ValidationError):
            EmailAttentionResult(
                coverage=self._coverage(),
                generated_at="2026-07-28T00:00:00+00:00",
                bogus_field=True,
            )


class TestEmailAttentionResponse:
    def test_schema_version_defaults_to_current(self):
        result = EmailAttentionResult(
            coverage=AttentionCoverage(), generated_at="2026-07-28T00:00:00+00:00"
        )
        response = EmailAttentionResponse(result=result)
        assert response.schema_version == SCHEMA_VERSION

    def test_forbids_unknown_fields(self):
        result = EmailAttentionResult(
            coverage=AttentionCoverage(), generated_at="2026-07-28T00:00:00+00:00"
        )
        with pytest.raises(ValidationError):
            EmailAttentionResponse(result=result, bogus_field=True)
