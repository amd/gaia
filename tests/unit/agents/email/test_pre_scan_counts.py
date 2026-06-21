# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Regression tests for the inbox pre-scan's cheap-counts guarantee (#1265).

A pre-scan exists to give per-category counts *cheaply* — its whole value is
that it does NOT run full per-email processing (no LLM round-trip per message).
These tests lock two things at once:

1. The per-category counts returned by ``pre_scan_inbox_impl`` match a fixture
   inbox with a known category distribution.
2. The expensive per-email path is never triggered during a pre-scan. Concretely:
   - ``triage_inbox_impl`` is invoked WITHOUT an LLM ``classifier`` (the only gate
     that lets a heuristic-uncertain message reach the model), and
   - ``decode_message_body`` — read only inside that LLM branch — is never called,
   - and through the production tool wiring, neither ``make_llm_classifier`` nor the
     agent's ``chat.send_messages`` ever fires.

The fixture deliberately includes ``confident=False`` messages (IMPORTANT label,
plain no-match) — exactly the ones the *expensive* ``triage_inbox`` tool would
send to the LLM. If pre-scan ever regressed to wire a classifier, the spies here
would catch it.

All LLM access is mocked: no Lemonade server, no ``gaia eval``.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

# Make tests.fixtures importable.
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102);
# skip when a framework-only env lacks it.

pytest.importorskip("gaia_agent_email")  # noqa: E402
from gaia_agent_email.tools import read_tools  # noqa: E402
from gaia_agent_email.tools.read_tools import pre_scan_inbox_impl  # noqa: E402
from gaia_agent_email.tools.triage_heuristics import (  # noqa: E402
    CATEGORY_FYI,
    CATEGORY_NEEDS_RESPONSE,
    CATEGORY_PROMOTIONAL,
    CATEGORY_URGENT,
)

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402


def _b64url(text: str) -> str:
    """URL-safe base64 with stripped padding — Gmail's body.data wire format."""
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _msg(
    msg_id: str,
    *,
    subject: str,
    sender: str,
    label_ids: List[str],
    body: str = "Body text for the fixture message.",
) -> Dict[str, Any]:
    """Build a minimal Gmail API v1 message dict (single text/plain part)."""
    return {
        "id": msg_id,
        "threadId": msg_id,
        "labelIds": list(label_ids),
        "snippet": body[:120],
        "internalDate": "1700000000000",
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
            ],
            "body": {"size": len(body), "data": _b64url(body)},
        },
        "sizeEstimate": len(body),
    }


# Fixture inbox with a KNOWN heuristic distribution.
#
# The heuristic only ever commits *confidently* to ``low priority`` and
# ``informational``; ``urgent``/``actionable`` are never heuristic-confident.
# So a heuristic-only pre-scan buckets these messages as:
#   low priority   : promo-labelled + promo-keyword + social      -> 3
#   informational  : updates-labelled + automated-sender + no-match -> 3
#   actionable     : IMPORTANT-labelled (confident=False)           -> 1
#   urgent         : (none — heuristic never emits urgent)          -> 0
_EXPECTED = {
    CATEGORY_URGENT: 0,
    CATEGORY_NEEDS_RESPONSE: 1,
    CATEGORY_FYI: 3,
    CATEGORY_PROMOTIONAL: 3,
}


@pytest.fixture
def fixture_inbox() -> FakeGmailBackend:
    gmail = FakeGmailBackend()  # empty store — we inject exactly what we want
    messages = [
        # --- low priority (confident) ---
        _msg(
            "m_promo_label",
            subject="Weekend getaway ideas",
            sender="travel@deals.example.com",
            label_ids=["INBOX", "CATEGORY_PROMOTIONS"],
        ),
        _msg(
            "m_promo_keyword",
            subject="50% off everything — sale ends tonight",
            sender="shop@store.example.com",
            label_ids=["INBOX"],
        ),
        _msg(
            "m_social",
            subject="Someone liked your post",
            sender="notify@social.example.com",
            label_ids=["INBOX", "CATEGORY_SOCIAL"],
        ),
        # --- informational (confident) ---
        _msg(
            "m_updates_label",
            subject="Your order has shipped",
            sender="ship@retailer.example.com",
            label_ids=["INBOX", "CATEGORY_UPDATES"],
        ),
        _msg(
            "m_automated_sender",
            subject="Your weekly summary",
            sender="noreply@service.example.com",
            label_ids=["INBOX"],
        ),
        # --- informational (NOT confident — no-match) ---
        _msg(
            "m_no_match",
            subject="Lunch next week?",
            sender="colleague@example.com",
            label_ids=["INBOX"],
        ),
        # --- actionable (NOT confident — IMPORTANT label) ---
        _msg(
            "m_important",
            subject="Please review the Q3 numbers",
            sender="boss@example.com",
            label_ids=["INBOX", "IMPORTANT"],
        ),
    ]
    for m in messages:
        gmail.add_message(m)
    return gmail


# ---------------------------------------------------------------------------
# Counts match the fixture
# ---------------------------------------------------------------------------


class TestPreScanCounts:
    def test_counts_match_fixture(self, fixture_inbox):
        """Per-category counts from a heuristic-only pre-scan match the fixture."""
        out = pre_scan_inbox_impl(fixture_inbox, max_messages=50)

        totals = out["totals"]
        assert totals["urgent"] == _EXPECTED[CATEGORY_URGENT]
        assert totals["actionable"] == _EXPECTED[CATEGORY_NEEDS_RESPONSE]
        assert totals["informational"] == _EXPECTED[CATEGORY_FYI]
        # Low-priority messages are surfaced as suggested archives.
        assert totals["suggested_archives"] == _EXPECTED[CATEGORY_PROMOTIONAL]

        # The scalar mirror of the informational bucket must agree.
        assert out["informational_count"] == _EXPECTED[CATEGORY_FYI]

        # Section list lengths agree with the totals (nothing dropped by caps
        # at this size — all caps are >= the per-section counts here).
        assert len(out["urgent"]) == _EXPECTED[CATEGORY_URGENT]
        assert len(out["actionable"]) == _EXPECTED[CATEGORY_NEEDS_RESPONSE]
        assert len(out["suggested_archives"]) == _EXPECTED[CATEGORY_PROMOTIONAL]

        # Sanity: every fixture message is accounted for in exactly one bucket.
        accounted = (
            totals["urgent"]
            + totals["actionable"]
            + totals["informational"]
            + totals["suggested_archives"]
        )
        assert accounted == 7


# ---------------------------------------------------------------------------
# Cheap-scan guarantee: full per-email processing is NOT triggered
# ---------------------------------------------------------------------------


class TestPreScanIsCheap:
    def test_no_llm_classifier_and_no_body_decode(self, fixture_inbox, monkeypatch):
        """``pre_scan_inbox_impl`` must not wire an LLM classifier into triage,
        and must not read message bodies (the LLM branch's first step).

        We wrap ``triage_inbox_impl`` to record the ``classifier`` it receives,
        and spy ``decode_message_body`` (used only inside the per-email LLM
        branch). Both are the cheap-scan tripwires.
        """
        # Record the *effective* classifier triage receives. ``triage_inbox_impl``
        # defaults ``classifier`` to None, so an absent kwarg and an explicit
        # ``classifier=None`` are equivalent — both mean "no LLM wired". We assert
        # the resolved value is None either way.
        seen_classifiers: List[Any] = []
        real_triage = read_tools.triage_inbox_impl

        def _recording_triage(*args, **kwargs):
            seen_classifiers.append(kwargs.get("classifier"))
            return real_triage(*args, **kwargs)

        monkeypatch.setattr(read_tools, "triage_inbox_impl", _recording_triage)

        decode_calls: List[Any] = []
        real_decode = read_tools.decode_message_body

        def _spy_decode(payload):
            decode_calls.append(payload)
            return real_decode(payload)

        monkeypatch.setattr(read_tools, "decode_message_body", _spy_decode)

        out = pre_scan_inbox_impl(fixture_inbox, max_messages=50)

        # Pre-scan still produced counts.
        assert out["kind"] == "email_pre_scan"
        assert out["totals"]["suggested_archives"] == _EXPECTED[CATEGORY_PROMOTIONAL]

        # triage_inbox_impl was called exactly once, and WITHOUT a classifier.
        assert seen_classifiers == [None], (
            "pre-scan must call triage without an LLM classifier; "
            f"saw classifier kwargs: {seen_classifiers!r}"
        )

        # Bodies were never decoded — proves no per-email LLM follow-up ran.
        assert decode_calls == [], (
            "pre-scan must not read message bodies (cheap scan); "
            f"decode_message_body was called {len(decode_calls)} time(s)"
        )

    def test_force_llm_stays_cheap(self, fixture_inbox, monkeypatch):
        """Even with ``force_llm=True`` the pre-scan stays cheap: there is no
        classifier wired, so nothing routes to the model and no body is read.
        """
        decode_calls: List[Any] = []
        real_decode = read_tools.decode_message_body

        def _spy_decode(payload):
            decode_calls.append(payload)
            return real_decode(payload)

        monkeypatch.setattr(read_tools, "decode_message_body", _spy_decode)

        out = pre_scan_inbox_impl(fixture_inbox, max_messages=50, force_llm=True)

        assert out["kind"] == "email_pre_scan"
        assert decode_calls == [], (
            "force_llm pre-scan must still be cheap — no body decode; "
            f"decode_message_body was called {len(decode_calls)} time(s)"
        )


# ---------------------------------------------------------------------------
# Production tool wiring: the registered tool never reaches the LLM
# ---------------------------------------------------------------------------


def _make_email_agent(fake_gmail, tmp_path):
    """Construct an EmailTriageAgent with the gmail backend injected and the
    AgentSDK mocked, so ``agent.chat`` is a spy and no live LLM is needed.
    Mirrors the helper in ``tests/unit/agents/test_email_agent_tools.py``.
    """
    from unittest.mock import MagicMock, patch

    from gaia_agent_email.agent import EmailTriageAgent
    from gaia_agent_email.config import EmailAgentConfig

    cfg = EmailAgentConfig(
        gmail_backend=fake_gmail,
        db_path=str(tmp_path / "state.db"),
        silent_mode=True,
    )
    with (
        patch("gaia.llm.lemonade_manager.LemonadeManager.ensure_ready"),
        patch("gaia.agents.base.agent.AgentSDK") as mock_sdk,
    ):
        mock_sdk.return_value = MagicMock()
        agent = EmailTriageAgent(config=cfg)
    return agent


def _registered_tool(name):
    from gaia.agents.base.tools import _TOOL_REGISTRY

    return _TOOL_REGISTRY[name]["function"]


class TestPreScanToolWiringIsCheap:
    def test_pre_scan_tool_never_calls_llm(self, fixture_inbox, tmp_path, monkeypatch):
        """End-to-end through the tool registry with the production classifier
        wiring present (``agent.chat`` is a live spy): invoking ``pre_scan_inbox``
        must NOT build an LLM classifier and must NOT call ``chat.send_messages``,
        while still returning per-category counts.
        """
        agent = _make_email_agent(fixture_inbox, tmp_path)
        try:
            # Spy the factory the *expensive* triage tool uses to bind the LLM.
            classifier_builds: List[Any] = []

            def _spy_make_classifier(chat):
                classifier_builds.append(chat)
                raise AssertionError("pre_scan_inbox must not build an LLM classifier")

            monkeypatch.setattr(read_tools, "make_llm_classifier", _spy_make_classifier)

            envelope = json.loads(_registered_tool("pre_scan_inbox")(50))

            assert envelope["ok"] is True, envelope
            data = envelope["data"]
            assert data["kind"] == "email_pre_scan"
            assert (
                data["totals"]["suggested_archives"] == _EXPECTED[CATEGORY_PROMOTIONAL]
            )
            assert data["informational_count"] == _EXPECTED[CATEGORY_FYI]

            # The classifier factory was never invoked during pre-scan.
            assert (
                classifier_builds == []
            ), "pre-scan built an LLM classifier — full processing leaked in"

            # The mocked LLM was never asked to classify anything.
            agent.chat.send_messages.assert_not_called()
        finally:
            agent.close_db()
