# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Reply/draft target resolution from a sender or topic (#2403).

``draft_reply`` used to require a concrete ``message_id`` (or an exact
subject the model had already searched for). Asking to "draft a reply to
rocm-ci@amd.com" or "reply ... regarding SIC-4482" dead-ended on "give me a
message ID / subject line" even though the sender / incident token uniquely
identify the message.

``resolve_message_target`` closes that gap: a target given as a sender
address, brand, or topic token is translated to a search, the best-matching
thread is picked, and the concrete message id is returned — while a concrete
id still passes straight through (no regression). Ambiguity and absence fail
LOUD and actionable, never a silent wrong-target and never a bare
"provide exact subject/message-id" wall.

All tests are hermetic: FakeGmailBackend only, no Lemonade, no network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# parents[0] = tests/, [1] = python/, [2] = email/, [3] = agents/,
# [4] = hub/, [5] = repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.tools.reply_tools import resolve_message_target  # noqa: E402

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _msg(
    msg_id: str,
    *,
    sender: str,
    subject: str,
    thread_id: str | None = None,
    internal_ms: int = 1_700_000_000_000,
    snippet: str = "",
) -> dict:
    """Build a minimal Gmail-API-v1-shape message."""
    return {
        "id": msg_id,
        "threadId": thread_id or msg_id,
        "internalDate": str(internal_ms),
        "snippet": snippet,
        "labelIds": ["INBOX", "UNREAD"],
        "payload": {
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": "Thu, 23 Jul 2026 09:00:00 +0000"},
            ]
        },
    }


def _backend(*messages: dict, user_email: str = "user@example.com") -> FakeGmailBackend:
    b = FakeGmailBackend(user_email=user_email)
    for m in messages:
        b.add_message(m)
    return b


def _list_calls(backend: FakeGmailBackend) -> list:
    return [c for c in backend.transport.calls if c[0] == "list_messages"]


# ---------------------------------------------------------------------------
# AC (a): a sender / topic target resolves to the right message via search
# ---------------------------------------------------------------------------


def test_resolves_unique_sender_to_message_id():
    gmail = _backend(
        _msg(
            "m1",
            sender="rocm-ci@amd.com",
            subject="ACTION REQUIRED: Engineering Freeze SIC-4482",
        ),
        _msg("m2", sender="newsletter@othercorp.com", subject="Weekly digest"),
    )
    resolved_id, provider, msg = resolve_message_target(
        {"google": gmail}, target="rocm-ci@amd.com"
    )
    assert resolved_id == "m1"
    assert provider == "google"
    assert msg is not None
    # It actually searched (did not demand an id).
    assert _list_calls(gmail), "expected a search to resolve the sender"


def test_resolves_topic_token_via_search():
    gmail = _backend(
        _msg(
            "inc1",
            sender="rocm-ci@amd.com",
            subject="ACTION REQUIRED: Engineering Freeze SIC-4482",
        ),
        _msg("other", sender="rocm-ci@amd.com", subject="Nightly build passed"),
    )
    resolved_id, provider, _ = resolve_message_target(
        {"google": gmail}, target="SIC-4482"
    )
    assert resolved_id == "inc1"
    assert provider == "google"


def test_operator_query_passes_through_to_search():
    gmail = _backend(
        _msg("m1", sender="rocm-ci@amd.com", subject="Freeze SIC-4482"),
    )
    resolved_id, _, _ = resolve_message_target(
        {"google": gmail}, target="from:rocm-ci@amd.com"
    )
    assert resolved_id == "m1"


# ---------------------------------------------------------------------------
# No regression: a concrete id passes straight through WITHOUT searching
# ---------------------------------------------------------------------------


def test_concrete_id_passes_through_without_search():
    gmail = _backend(
        _msg("abc123def456", sender="rocm-ci@amd.com", subject="Freeze SIC-4482"),
    )
    resolved_id, provider, _ = resolve_message_target(
        {"google": gmail}, target="abc123def456"
    )
    assert resolved_id == "abc123def456"
    assert provider == "google"
    # A concrete id is used as-is — never routed through a search.
    assert not _list_calls(gmail), "a concrete id must not trigger a search"


def test_known_message_mailbox_fast_path_skips_search():
    gmail = _backend(
        _msg("known1", sender="rocm-ci@amd.com", subject="Freeze SIC-4482"),
    )
    resolved_id, provider, _ = resolve_message_target(
        {"google": gmail},
        target="known1",
        message_mailbox={"known1": "google"},
    )
    assert resolved_id == "known1"
    assert provider == "google"
    assert not _list_calls(gmail)


# ---------------------------------------------------------------------------
# One thread, several messages → resolve to the latest, NOT ambiguous
# ---------------------------------------------------------------------------


def test_single_thread_multiple_messages_resolves_latest():
    gmail = _backend(
        _msg(
            "t_old",
            sender="rocm-ci@amd.com",
            subject="Freeze SIC-4482",
            thread_id="thread-A",
            internal_ms=1_700_000_000_000,
        ),
        _msg(
            "t_new",
            sender="rocm-ci@amd.com",
            subject="Re: Freeze SIC-4482",
            thread_id="thread-A",
            internal_ms=1_700_000_500_000,
        ),
    )
    resolved_id, _, _ = resolve_message_target({"google": gmail}, target="SIC-4482")
    assert resolved_id == "t_new"


# ---------------------------------------------------------------------------
# AC (b): ambiguous target → actionable disambiguation listing candidates
# ---------------------------------------------------------------------------


def test_ambiguous_multiple_threads_raises_with_candidates():
    gmail = _backend(
        _msg(
            "inv1",
            sender="alice@corp.com",
            subject="Invoice 100 overdue",
            thread_id="thread-1",
        ),
        _msg(
            "inv2",
            sender="alice@corp.com",
            subject="Invoice 200 reminder",
            thread_id="thread-2",
        ),
    )
    with pytest.raises(ValueError) as exc:
        resolve_message_target({"google": gmail}, target="from:alice@corp.com")
    text = str(exc.value)
    # Lists both candidate subjects so the user can pick.
    assert "Invoice 100 overdue" in text
    assert "Invoice 200 reminder" in text
    # It is NOT a bare "give me a message id" dead-end.
    assert "message id" not in text.lower() or "which" in text.lower()


# ---------------------------------------------------------------------------
# AC (c): no match → actionable "not found", never a message-id wall
# ---------------------------------------------------------------------------


def test_no_match_raises_not_found():
    gmail = _backend(
        _msg("m1", sender="rocm-ci@amd.com", subject="Freeze SIC-4482"),
    )
    with pytest.raises(ValueError) as exc:
        resolve_message_target({"google": gmail}, target="nobody@nowhere.example")
    text = str(exc.value).lower()
    assert "no message" in text or "not found" in text
    assert "nobody@nowhere.example" in str(exc.value)


# ---------------------------------------------------------------------------
# Explicit mailbox scoping
# ---------------------------------------------------------------------------


def test_explicit_mailbox_restricts_search():
    google = _backend(
        _msg("g1", sender="rocm-ci@amd.com", subject="Freeze SIC-4482"),
    )
    microsoft = _backend(
        _msg("o1", sender="rocm-ci@amd.com", subject="Different freeze"),
    )
    resolved_id, provider, _ = resolve_message_target(
        {"google": google, "microsoft": microsoft},
        target="SIC-4482",
        explicit_mailbox="google",
    )
    assert resolved_id == "g1"
    assert provider == "google"


def test_explicit_mailbox_not_connected_raises():
    gmail = _backend(_msg("g1", sender="a@b.com", subject="hi"))
    with pytest.raises(ValueError) as exc:
        resolve_message_target(
            {"google": gmail}, target="hi", explicit_mailbox="microsoft"
        )
    assert "microsoft" in str(exc.value).lower()


def test_multi_mailbox_same_sender_disambiguates_across_providers():
    google = _backend(
        _msg("g1", sender="rocm-ci@amd.com", subject="Gmail freeze SIC-4482"),
    )
    microsoft = _backend(
        _msg("o1", sender="rocm-ci@amd.com", subject="Outlook freeze SIC-4482"),
    )
    with pytest.raises(ValueError) as exc:
        resolve_message_target(
            {"google": google, "microsoft": microsoft}, target="SIC-4482"
        )
    text = str(exc.value)
    assert "Gmail freeze SIC-4482" in text
    assert "Outlook freeze SIC-4482" in text
