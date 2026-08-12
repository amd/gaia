# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""#2900 — a scoped "anything suspicious?" question must not dump the full
four-bucket triage report.

The issue's own theory ("the agent runs its default full-triage flow
regardless of the question") is wrong — there is no router in this codebase.
The real mechanism is ``rewrite_triage_answer`` (``answer_grounding.py``),
which unconditionally renders the full triage card whenever ``pre_scan_inbox``
was called this turn, by design (it stops the model hand-summarizing a list a
tool already computed, which corrupted it in five consecutive live runs).
That mechanism is correct and untouched here.

The fix is a new, narrow tool — ``check_suspicious_mail`` — that surfaces
ONLY phishing/spam-flagged messages, following the same pattern already used
by ``get_briefing``/``extract_action_items``/``list_tasks``: a scoped
question reaches a scoped tool, so there is nothing unrelated to leak.

Covers, in order:

- ``pre_scan_inbox_impl`` threading ``is_phishing``/``is_spam`` structurally
  onto every ``actionable`` row, and capturing the flagged subset into a new
  ``suspicious``/``suspicious_total`` pair BEFORE ``PRE_SCAN_ACTIONABLE_CAP``
  is applied (the cap-truncation regression the design's reflection caught).
- ``merge_pre_scan_backends`` merging ``suspicious`` across mailboxes the
  same way as every other section, re-capped post-merge, and NOT folded into
  the merged ``scanned`` figure (already counted once via ``actionable``).
- ``check_suspicious_mail`` as a registered agent-loop tool: correct
  envelope shape, the zero-mailbox guard, and that it never touches
  ``agent._last_needs_you_card`` (#2745 — exclusive to ``pre_scan_inbox``).
- ``rewrite_triage_answer`` never firing on a turn where only
  ``check_suspicious_mail`` was called (AC1), while a genuine
  ``pre_scan_inbox`` turn is completely unaffected (AC2 regression).
- The contract additions (``PreScanItem.is_phishing``/``is_spam``,
  ``EmailPreScanResult.suspicious``/``suspicious_total``, schema 2.13).
- The system-prompt wording: the tool is named so the model can call it, but
  no scoped-question phrasing is enumerated in code or prompt text (#2762).
"""

from __future__ import annotations

import base64
import json
import logging
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

import pytest

# parents[0]=tests/, [1]=python/, [2]=email/, [3]=agents/, [4]=hub/, [5]=repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.agent import _SYSTEM_PROMPT  # noqa: E402
from gaia_agent_email.answer_grounding import (  # noqa: E402
    _honest_suspicious_summary,
    last_tool_payload,
    render_suspicious_list,
    rewrite_suspicious_mail_answer,
    rewrite_triage_answer,
)
from gaia_agent_email.contract import (  # noqa: E402
    SCHEMA_VERSION,
    EmailPreScanResult,
    PreScanItem,
)
from gaia_agent_email.tools.read_tools import (  # noqa: E402
    NO_MAILBOX_CONNECTED_MESSAGE,
    PRE_SCAN_ACTIONABLE_CAP,
    PRE_SCAN_SUSPICIOUS_CAP,
    ReadToolsMixin,
    merge_pre_scan_backends,
    pre_scan_inbox_impl,
)

from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402
from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

# The literal four section headings ``rewrite_triage_answer`` renders for a
# genuine pre_scan_inbox turn — see answer_grounding.py's _TRIAGE_SECTIONS.
_TRIAGE_SECTION_HEADINGS = (
    "### Waiting on your reply",
    "### Needs a response",
    "### Meetings to decide",
    "### Needs a manual look",
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _msg(
    msg_id: str, *, subject: str, sender: str, body: str, label_ids=("INBOX",)
) -> Dict[str, Any]:
    return {
        "id": msg_id,
        "threadId": msg_id,
        "labelIds": list(label_ids),
        "snippet": body[:200],
        "internalDate": "1700000000000",
        "sizeEstimate": len(body),
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
            ],
            "body": {"size": len(body), "data": _b64url(body)},
        },
    }


# ``_looks_phishing``'s single-phrase list (triage_heuristics.py) — fires on
# subject alone, no keyword pairing needed, so this is a stable, minimal
# trigger for the REAL detector (never re-implemented in this test file).
_PHISHING_SUBJECT = "We detected unusual sign-in activity on your account"


def _fake_triage_result(
    msg_id: str,
    *,
    is_phishing: bool = False,
    is_spam: bool = False,
    category: str = "NEEDS_RESPONSE",
    subject: str = "subject",
    sender: str = "someone@example.com",
) -> Dict[str, Any]:
    """A single ``triage_inbox_impl``-shaped result row — the minimal set of
    keys ``pre_scan_inbox_impl``'s per-message loop reads."""
    return {
        "id": msg_id,
        "thread_id": msg_id,
        "from": sender,
        "subject": subject,
        "category": category,
        "is_spam": is_spam,
        "is_phishing": is_phishing,
        "is_meeting_request": False,
        "internal_date": "1700000000000",
        "rationale": "flagged" if (is_phishing or is_spam) else "",
    }


# ---------------------------------------------------------------------------
# pre_scan_inbox_impl — structural threading + pre-cap capture
# ---------------------------------------------------------------------------


class TestPreScanImplSuspiciousStructural:
    def test_phishing_message_flows_through_real_detector(self):
        gmail = FakeGmailBackend()
        gmail.add_message(
            _msg(
                "phish-1",
                subject=_PHISHING_SUBJECT,
                sender="security@paypa1-alerts.example",
                body="Click here to verify your account immediately.",
            )
        )
        out = pre_scan_inbox_impl(gmail, max_messages=25)
        assert out["suspicious_total"] == 1
        row = out["suspicious"][0]
        assert row["message_id"] == "phish-1"
        assert row["is_phishing"] is True
        assert row["is_spam"] is False
        # Same row must also still be present in actionable (detection/
        # routing unchanged — #2900 is scope-of-response only).
        assert any(a["message_id"] == "phish-1" for a in out["actionable"])
        actionable_row = next(
            a for a in out["actionable"] if a["message_id"] == "phish-1"
        )
        assert actionable_row["is_phishing"] is True

    def test_clean_inbox_has_no_suspicious_items(self):
        gmail = FakeGmailBackend()
        gmail.add_message(
            _msg(
                "clean-1",
                subject="Q3 roadmap review",
                sender="colleague@example.com",
                body="Can you take a look at the roadmap doc before Friday?",
            )
        )
        out = pre_scan_inbox_impl(gmail, max_messages=25)
        assert out["suspicious"] == []
        assert out["suspicious_total"] == 0

    def test_every_prescan_item_carries_the_new_fields_defaulted_false(self):
        """Non-flagged rows get real (not missing) is_phishing/is_spam
        fields, both False — the contract change applies uniformly, not
        only to the flagged branch."""
        gmail = FakeGmailBackend()
        gmail.add_message(
            _msg(
                "clean-2",
                subject="Re: dinner Friday?",
                sender="friend@example.com",
                body="Still on for Friday?",
            )
        )
        out = pre_scan_inbox_impl(gmail, max_messages=25)
        all_items = out["actionable"] + out["urgent"] + out["needs_review"]
        assert all_items, "fixture message must land in an id-carrying bucket"
        for item in all_items:
            assert item["is_phishing"] is False
            assert item["is_spam"] is False


class TestPreScanImplSuspiciousCapTruncation:
    """The reflection-caught regression: a naive client-side filter of the
    already-capped ``actionable`` bucket would silently drop a flagged
    message ranked past ``PRE_SCAN_ACTIONABLE_CAP``. ``suspicious_total``
    must still count it because it's captured pre-cap."""

    def test_flagged_message_past_the_actionable_cap_is_still_counted(
        self, monkeypatch
    ):
        # One phishing message, ranked LAST among more than
        # PRE_SCAN_ACTIONABLE_CAP actionable-category results — a
        # cap-based slice of ``actionable`` would drop it.
        n_clean = PRE_SCAN_ACTIONABLE_CAP + 3
        results = [
            _fake_triage_result(f"clean-{i}", category="NEEDS_RESPONSE")
            for i in range(n_clean)
        ] + [_fake_triage_result("phish-last", is_phishing=True)]
        assert len(results) > PRE_SCAN_ACTIONABLE_CAP

        monkeypatch.setattr(
            "gaia_agent_email.tools.read_tools.triage_inbox_impl",
            lambda *a, **k: {"results": results},
        )
        gmail = FakeGmailBackend()
        out = pre_scan_inbox_impl(gmail, max_messages=50)

        assert out["suspicious_total"] == 1
        assert out["suspicious"][0]["message_id"] == "phish-last"
        # Sanity: this IS the regression scenario — the phishing message
        # really is past the actionable cap in that bucket's own slice.
        actionable_ids = [a["message_id"] for a in out["actionable"]]
        assert len(actionable_ids) == PRE_SCAN_ACTIONABLE_CAP
        assert "phish-last" not in actionable_ids, (
            "fixture must actually exercise the cap — if this fails the "
            "test stopped proving what it claims to"
        )

    def test_spam_message_also_captured_pre_cap(self, monkeypatch):
        results = [_fake_triage_result("spam-1", is_spam=True)]
        monkeypatch.setattr(
            "gaia_agent_email.tools.read_tools.triage_inbox_impl",
            lambda *a, **k: {"results": results},
        )
        gmail = FakeGmailBackend()
        out = pre_scan_inbox_impl(gmail, max_messages=25)
        assert out["suspicious_total"] == 1
        assert out["suspicious"][0]["is_spam"] is True
        assert out["suspicious"][0]["is_phishing"] is False


class TestPreScanImplSuspiciousNotDoubleCounted:
    def test_suspicious_total_not_folded_into_scanned(self, monkeypatch):
        results = [_fake_triage_result("phish-1", is_phishing=True)]
        monkeypatch.setattr(
            "gaia_agent_email.tools.read_tools.triage_inbox_impl",
            lambda *a, **k: {"results": results},
        )
        gmail = FakeGmailBackend()
        out = pre_scan_inbox_impl(gmail, max_messages=25)
        # One message scanned total (it's counted once, via actionable) —
        # NOT two (once for actionable, again for suspicious).
        assert out["scanned"] == 1


# ---------------------------------------------------------------------------
# merge_pre_scan_backends — cross-mailbox merge
# ---------------------------------------------------------------------------


class TestMergePreScanBackendsSuspicious:
    def test_merges_and_tags_mailbox_across_backends(self, monkeypatch):
        calls = {"n": 0}

        def fake_triage(backend, *a, **k):
            calls["n"] += 1
            # Each backend contributes one flagged message with a distinct id.
            return {
                "results": [
                    _fake_triage_result(f"phish-{calls['n']}", is_phishing=True)
                ]
            }

        monkeypatch.setattr(
            "gaia_agent_email.tools.read_tools.triage_inbox_impl", fake_triage
        )
        backends = {"google": FakeGmailBackend(), "microsoft": FakeGmailBackend()}
        result = merge_pre_scan_backends(backends, max_messages=50)

        assert result["suspicious_total"] == 2
        mailboxes = {row["mailbox"] for row in result["suspicious"]}
        assert mailboxes == {"google", "microsoft"}

    def test_merged_scanned_still_not_double_counted(self, monkeypatch):
        def fake_triage(backend, *a, **k):
            return {"results": [_fake_triage_result("phish-solo", is_phishing=True)]}

        monkeypatch.setattr(
            "gaia_agent_email.tools.read_tools.triage_inbox_impl", fake_triage
        )
        backends = {"google": FakeGmailBackend()}
        result = merge_pre_scan_backends(backends, max_messages=50)
        assert result["scanned"] == 1

    def test_merged_suspicious_is_capped_post_merge(self, monkeypatch):
        """Each backend independently caps its own contribution to
        PRE_SCAN_SUSPICIOUS_CAP; the merge must re-cap rather than letting
        N backends x cap through uncapped (unlike ``informational``)."""
        per_backend = PRE_SCAN_SUSPICIOUS_CAP

        def fake_triage(backend, *a, **k):
            return {
                "results": [
                    _fake_triage_result(f"{id(backend)}-{i}", is_phishing=True)
                    for i in range(per_backend)
                ]
            }

        monkeypatch.setattr(
            "gaia_agent_email.tools.read_tools.triage_inbox_impl", fake_triage
        )
        backends = {"google": FakeGmailBackend(), "microsoft": FakeGmailBackend()}
        result = merge_pre_scan_backends(backends, max_messages=200)
        # Total (pre-cap) count across both backends is honest...
        assert result["suspicious_total"] == per_backend * 2
        # ...but the returned LIST is capped, same policy as urgent/actionable.
        assert len(result["suspicious"]) <= PRE_SCAN_SUSPICIOUS_CAP


# ---------------------------------------------------------------------------
# check_suspicious_mail — registered agent-loop tool
# ---------------------------------------------------------------------------


class _Host(ReadToolsMixin):
    """Minimal EmailTriageAgent stand-in for the read-tools surface,
    mirroring test_briefing_task_tools_2110.py's ``_Host`` pattern."""

    def __init__(self, backends: dict):
        self._backends = backends
        self._gmail = next(iter(backends.values()), None)
        self.config = SimpleNamespace(debug=False)
        self._session_preferences = {}
        self._last_needs_you_card = None
        self._remember_message_mailbox = lambda *a, **k: None

    def _pre_scan_all_backends(
        self, *, max_messages: int, include_informational: bool = False
    ):
        from gaia_agent_email.tools.read_tools import merge_pre_scan_backends

        return merge_pre_scan_backends(
            self._backends,
            max_messages=max_messages,
            include_informational=include_informational,
        )

    def _refresh_mail_backends(self) -> None:
        # No-op stand-in — this fixture has no config to re-resolve from,
        # so it leaves ``_backends`` exactly as the test set it. The real
        # agent's refresh behavior (construction-time state going stale in
        # a long-lived session) is covered against the REAL agent by
        # TestCheckSuspiciousMailLongLivedSession below, not here.
        pass


def _tool(host, name):
    _TOOL_REGISTRY.clear()
    host._register_read_tools()
    assert name in _TOOL_REGISTRY, f"{name} not registered"
    return _TOOL_REGISTRY[name]["function"]


class TestCheckSuspiciousMailTool:
    def test_registers(self):
        host = _Host({"google": FakeGmailBackend()})
        _TOOL_REGISTRY.clear()
        host._register_read_tools()
        assert "check_suspicious_mail" in _TOOL_REGISTRY

    def test_returns_flagged_rows_with_honest_count(self, monkeypatch):
        monkeypatch.setattr(
            "gaia_agent_email.tools.read_tools.triage_inbox_impl",
            lambda *a, **k: {
                "results": [_fake_triage_result("phish-1", is_phishing=True)]
            },
        )
        host = _Host({"google": FakeGmailBackend()})
        fn = _tool(host, "check_suspicious_mail")
        out = json.loads(fn(max_messages=25))
        assert out["ok"] is True
        assert out["data"]["kind"] == "email_suspicious_scan"
        assert out["data"]["suspicious_total"] == 1
        assert out["data"]["suspicious"][0]["message_id"] == "phish-1"

    def test_zero_items_reports_empty_not_error(self, monkeypatch):
        monkeypatch.setattr(
            "gaia_agent_email.tools.read_tools.triage_inbox_impl",
            lambda *a, **k: {"results": []},
        )
        host = _Host({"google": FakeGmailBackend()})
        fn = _tool(host, "check_suspicious_mail")
        out = json.loads(fn(max_messages=25))
        assert out["ok"] is True
        assert out["data"]["suspicious"] == []
        assert out["data"]["suspicious_total"] == 0

    def test_never_updates_last_needs_you_card(self, monkeypatch):
        """#2745 — that positional-reference card is exclusive to
        pre_scan_inbox; this tool's rows carry no ``ref`` and a stale
        pointer here would let 'reply to 1' resolve against the wrong
        surface."""
        monkeypatch.setattr(
            "gaia_agent_email.tools.read_tools.triage_inbox_impl",
            lambda *a, **k: {
                "results": [_fake_triage_result("phish-1", is_phishing=True)]
            },
        )
        host = _Host({"google": FakeGmailBackend()})
        fn = _tool(host, "check_suspicious_mail")
        fn(max_messages=25)
        assert host._last_needs_you_card is None

    def test_no_mailbox_connected_returns_actionable_error(self):
        host = _Host({})
        fn = _tool(host, "check_suspicious_mail")
        out = json.loads(fn(max_messages=25))
        assert out["ok"] is False
        assert out["error"] == NO_MAILBOX_CONNECTED_MESSAGE

    def test_logs_its_own_tool_name_not_only_pre_scan_inbox(self, monkeypatch, caplog):
        """Review follow-up on #2900/#2910: the tool has no ``_impl`` of its
        own — it composes its result from ``pre_scan_inbox_impl`` (via
        ``_pre_scan_all_backends``), which logs its OWN call as literal
        "pre_scan_inbox". Without the outer wrapper, a successful
        ``check_suspicious_mail`` call was indistinguishable from
        ``pre_scan_inbox`` in the tool trace — a live sweep scored tool
        selection from those log lines and reported a false "0/5" that was
        later retracted."""
        monkeypatch.setattr(
            "gaia_agent_email.tools.read_tools.triage_inbox_impl",
            lambda *a, **k: {
                "results": [_fake_triage_result("phish-1", is_phishing=True)]
            },
        )
        host = _Host({"google": FakeGmailBackend()})
        fn = _tool(host, "check_suspicious_mail")
        with caplog.at_level(logging.INFO, logger="gaia_agent_email"):
            fn(max_messages=25)
        tool_result_names = [
            getattr(r, "tool_name", None)
            for r in caplog.records
            if getattr(r, "stage", None) == "tool_result"
        ]
        assert "check_suspicious_mail" in tool_result_names, (
            "check_suspicious_mail must emit its own tool_result log entry "
            f"— got {tool_result_names}"
        )

    def test_docstring_does_not_promise_more_than_the_cap_can_deliver(self):
        """Review follow-up on #2910's count/cap item: the docstring told
        the model to 'list EVERY entry individually ... never drop entries'
        with no qualifier, while ``suspicious`` is capped at
        ``PRE_SCAN_SUSPICIOUS_CAP`` and ``suspicious_total`` is captured
        pre-cap — an unfulfillable promise once more than the cap is
        flagged. The docstring must instead tell the model to disclose the
        gap between the two, mirroring what ``_honest_suspicious_summary``
        now renders."""
        host = _Host({"google": FakeGmailBackend()})
        fn = _tool(host, "check_suspicious_mail")
        doc = (fn.__doc__ or "").lower()
        # "own cap", not bare "cap" — the latter also matches "captured",
        # which says nothing about acknowledging a cap exists.
        assert "own cap" in doc, "docstring must acknowledge suspicious has a cap"
        assert "showing" in doc, (
            "docstring must tell the model how to disclose a truncated list "
            "(e.g. 'showing 10'), not just claim completeness"
        )


# ---------------------------------------------------------------------------
# check_suspicious_mail — long-lived-session refresh (review follow-up on
# #2900/#2910). The ``_Host`` stand-in above sets ``_backends`` once in
# ``__init__`` and never calls ``_refresh_mail_backends`` — every other
# pre-scan-derived tool re-resolves per call via
# ``_pre_scan_all_backends`` -> ``_refresh_mail_backends`` (agent.py), so a
# stand-in that skips that method can't catch a guard that short-circuits
# before it. This exercises the REAL ``EmailTriageAgent`` and its REAL
# registered tool instead.
# ---------------------------------------------------------------------------


def _build_zero_connector_agent(tmp_path, monkeypatch):
    """Construct with the real #2418 zero-connector path: no injected
    backend, nothing connected — ``agent._backends`` starts truly empty
    from ``__init__``'s own ``ConfigurationError`` branch, not a hand-set
    dict (mirrors ``test_zero_connector_construction_2418.py``)."""
    from unittest.mock import MagicMock, patch

    from gaia_agent_email.agent import EmailTriageAgent
    from gaia_agent_email.config import EmailAgentConfig

    monkeypatch.setattr(
        "gaia_agent_email.config.connected_mailbox_providers", lambda: []
    )
    cfg = EmailAgentConfig(
        model_id="test-model",
        db_path=str(tmp_path / "state.db"),
        memory_db_path=str(tmp_path / "memory.db"),
        silent_mode=True,
        debug=False,
    )
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    with patch("gaia.agents.base.agent.AgentSDK") as mock_sdk:
        mock_sdk.return_value = MagicMock()
        return EmailTriageAgent(config=cfg)


class TestCheckSuspiciousMailLongLivedSession:
    def test_no_backends_at_construction_then_mailbox_connects_mid_session(
        self, tmp_path, monkeypatch
    ):
        """A mailbox that becomes resolvable AFTER ``__init__`` (a connector
        grant completing mid-session, or startup recovering from a
        ``ConfigurationError``) must be picked up by ``check_suspicious_mail``
        — it must NOT keep reporting "no mailbox connected" off
        construction-time state. Fails against the pre-fix code, where the
        tool's ``if not agent._backends`` guard reads the stale empty dict
        from ``__init__`` and returns before ``_pre_scan_all_backends`` ever
        gets a chance to refresh it."""
        agent = _build_zero_connector_agent(tmp_path, monkeypatch)
        assert agent._backends == {}, "must start truly empty, mirroring #2418"

        # Mailbox becomes resolvable mid-session — same seam #2418's own
        # test suite uses to simulate the connected set changing, here
        # exercised through the REAL agent's REAL registered tool.
        fake_gmail = FakeGmailBackend()
        fake_gmail.add_message(
            _msg(
                "clean-1",
                subject="Q3 roadmap review",
                sender="colleague@example.com",
                body="Can you take a look before Friday?",
            )
        )
        agent.config.gmail_backend = fake_gmail

        check_suspicious_mail = agent._tools_registry["check_suspicious_mail"][
            "function"
        ]
        envelope = json.loads(check_suspicious_mail(max_messages=25))

        assert envelope["ok"] is True, (
            "check_suspicious_mail must see the newly-connected mailbox "
            f"instead of reporting the stale construction-time state, got {envelope}"
        )
        assert envelope["data"]["suspicious_total"] == 0


# ---------------------------------------------------------------------------
# rewrite_triage_answer — must never fire on a check_suspicious_mail-only turn
# ---------------------------------------------------------------------------


def _tool_entry(name: str, data: dict) -> dict:
    envelope = json.dumps({"ok": True, "data": data})
    return {
        "role": "tool",
        "name": name,
        "content": [{"type": "text", "text": envelope}],
    }


class TestRewriteTriageAnswerScoping:
    def test_check_suspicious_mail_alone_is_never_rewritten(self):
        """AC1 — the core of #2900: a turn that only calls the scoped tool
        gets no four-bucket render, because rewrite_triage_answer keys on
        the literal tool name 'pre_scan_inbox', which never appears here."""
        envelope = {
            "kind": "email_suspicious_scan",
            "suspicious": [
                {
                    "message_id": "phish-1",
                    "sender": "security@paypa1-alerts.example",
                    "subject": _PHISHING_SUBJECT,
                    "why": "flagged as phishing",
                    "is_phishing": True,
                    "is_spam": False,
                }
            ],
            "suspicious_total": 1,
        }
        conversation = [_tool_entry("check_suspicious_mail", envelope)]
        model_answer = (
            "One flagged message: security@paypa1-alerts.example — "
            f'"{_PHISHING_SUBJECT}" (phishing).'
        )
        out = rewrite_triage_answer(model_answer, conversation)
        assert out == model_answer
        for heading in _TRIAGE_SECTION_HEADINGS:
            assert heading not in out

    def test_check_suspicious_mail_alone_zero_findings_stays_a_clean_negative(self):
        envelope = {
            "kind": "email_suspicious_scan",
            "suspicious": [],
            "suspicious_total": 0,
        }
        conversation = [_tool_entry("check_suspicious_mail", envelope)]
        model_answer = "Nothing flagged this scan."
        out = rewrite_triage_answer(model_answer, conversation)
        assert out == model_answer
        for heading in _TRIAGE_SECTION_HEADINGS:
            assert heading not in out

    def test_last_tool_payload_does_not_see_check_suspicious_mail_as_prescan(self):
        conversation = [_tool_entry("check_suspicious_mail", {"suspicious_total": 1})]
        assert last_tool_payload(conversation, "pre_scan_inbox") is None

    def test_genuine_pre_scan_inbox_turn_is_unaffected_ac2_regression(self):
        """A real pre_scan_inbox call must still get the full render — this
        issue must not suppress the intended full-triage flow."""
        envelope = {
            "kind": "email_pre_scan",
            "urgent": [],
            "actionable": [],
            "needs_review": [],
            "needs_you": [
                {
                    "ref": 1,
                    "kind": "needs_response",
                    "message_id": "m1",
                    "sender": "colleague@example.com",
                    "subject": "Q3 roadmap",
                    "why": "awaiting your reply",
                }
            ],
            "scanned": 10,
            "total_unread": 10,
            "degraded": False,
        }
        conversation = [_tool_entry("pre_scan_inbox", envelope)]
        model_answer = "Here's your inbox — 1 item needs attention."
        out = rewrite_triage_answer(model_answer, conversation)
        assert "### Needs a response" in out
        assert "Q3 roadmap" in out

    def test_both_tools_called_prescan_still_wins_documented_residual_risk(self):
        """Not a design goal — a documented residual risk (see the plan's
        Adversarial Reflection / Eval gate): if the model calls BOTH tools
        in the same turn, rewrite_triage_answer still fires on
        pre_scan_inbox's presence, appending the full card regardless of
        what check_suspicious_mail also returned. Fixing this in code would
        require parsing the user's question, which #2762 forbids — so this
        is pinned as known behavior, not asserted away."""
        suspicious_envelope = {"suspicious": [], "suspicious_total": 0}
        prescan_envelope = {
            "kind": "email_pre_scan",
            "urgent": [],
            "actionable": [],
            "needs_review": [],
            "needs_you": [
                {
                    "ref": 1,
                    "kind": "needs_response",
                    "message_id": "m1",
                    "sender": "colleague@example.com",
                    "subject": "Q3 roadmap",
                    "why": "awaiting your reply",
                }
            ],
            "scanned": 10,
            "total_unread": 10,
            "degraded": False,
        }
        conversation = [
            _tool_entry("check_suspicious_mail", suspicious_envelope),
            _tool_entry("pre_scan_inbox", prescan_envelope),
        ]
        out = rewrite_triage_answer("Nothing flagged this scan.", conversation)
        assert "### Needs a response" in out  # the full card still renders


# ---------------------------------------------------------------------------
# rewrite_suspicious_mail_answer — review follow-up: the flagged-mail list
# must reach the user grounded in tool data, not free-form model prose
# (mirrors rewrite_triage_answer's guarantee for pre_scan_inbox).
# ---------------------------------------------------------------------------


class TestRewriteSuspiciousMailAnswer:
    def test_renders_flagged_rows_from_tool_data_not_model_prose(self):
        envelope = {
            "kind": "email_suspicious_scan",
            "suspicious": [
                {
                    "message_id": "phish-1",
                    "sender": "security@paypa1-alerts.example",
                    "subject": _PHISHING_SUBJECT,
                    "why": "flagged as phishing",
                    "is_phishing": True,
                    "is_spam": False,
                }
            ],
            "suspicious_total": 1,
            "scanned": 25,
        }
        conversation = [_tool_entry("check_suspicious_mail", envelope)]
        # Deliberately wrong model prose (dropped the flagged message) — the
        # rewrite must replace it with the tool's own row, not trust this.
        model_answer = "Nothing flagged this scan."
        out = rewrite_suspicious_mail_answer(model_answer, conversation)
        assert "### Flagged this scan" in out
        assert "security@paypa1-alerts.example" in out or "paypa1-alerts" in out
        assert _PHISHING_SUBJECT in out
        assert "phishing" in out.lower()
        # The bug this test was supposed to catch and didn't: a correct list
        # is worthless if a false all-clear still sits right above it. The
        # wrong opener must be GONE, not just outnumbered by a correct list
        # appended underneath it.
        assert model_answer not in out, (
            "the model's contradicting 'Nothing flagged' opener survived "
            "into the answer, sitting directly above the flagged-mail list"
        )
        assert out.startswith("1 flagged"), (
            "expected the grounded summary (_honest_suspicious_summary) to "
            f"lead the answer, got: {out!r}"
        )

    def test_zero_findings_is_a_noop_model_prose_stands(self):
        """A clean negative carries no list to render, so the model's own
        (already-correct) prose is left alone — mirrors
        render_needs_you_list's ``if not items: return ""`` behavior."""
        envelope = {"kind": "email_suspicious_scan", "suspicious": [], "suspicious_total": 0}
        conversation = [_tool_entry("check_suspicious_mail", envelope)]
        model_answer = "Nothing flagged this scan."
        out = rewrite_suspicious_mail_answer(model_answer, conversation)
        assert out == model_answer

    def test_no_check_suspicious_mail_call_is_a_noop(self):
        out = rewrite_suspicious_mail_answer("some reply", conversation=[])
        assert out == "some reply"

    def test_defers_to_pre_scan_inbox_when_both_tools_ran(self):
        """When both tools ran this turn, pre_scan_inbox's four-bucket card
        already won (rewrite_triage_answer fires first in
        ground_final_answer) — this function must not then clobber that
        card with a narrower suspicious-only list underneath it."""
        suspicious_envelope = {
            "suspicious": [
                {
                    "message_id": "phish-1",
                    "sender": "a@example.com",
                    "subject": _PHISHING_SUBJECT,
                    "is_phishing": True,
                    "is_spam": False,
                }
            ],
            "suspicious_total": 1,
        }
        prescan_envelope = {"kind": "email_pre_scan", "scanned": 10}
        conversation = [
            _tool_entry("check_suspicious_mail", suspicious_envelope),
            _tool_entry("pre_scan_inbox", prescan_envelope),
        ]
        already_rendered_card = "### Waiting on your reply\n\n1. Someone — Subject"
        out = rewrite_suspicious_mail_answer(already_rendered_card, conversation)
        assert out == already_rendered_card

    def test_render_suspicious_list_renders_every_row_with_no_summarizing(self):
        envelope = {
            "suspicious": [
                {
                    "message_id": "m1",
                    "sender": "a@example.com",
                    "subject": "Sub A",
                    "is_phishing": True,
                    "is_spam": False,
                    "why": "flagged as phishing",
                },
                {
                    "message_id": "m2",
                    "sender": "b@example.com",
                    "subject": "Sub B",
                    "is_phishing": False,
                    "is_spam": True,
                    "why": "flagged as spam",
                },
            ]
        }
        out = render_suspicious_list(envelope)
        assert "Sub A" in out and "phishing" in out
        assert "Sub B" in out and "spam" in out
        # Each row is its own bullet line, not merged onto one line — a
        # substring check alone can't distinguish "one line, two entries"
        # from "two lines, two entries".
        bullet_lines = [
            line for line in out.splitlines() if line.strip().startswith("- ")
        ]
        assert len(bullet_lines) == 2

    def test_render_suspicious_list_rows_carry_no_positional_number(self):
        """Review follow-up on #2910: these rows have no card ``ref`` (see
        ``check_suspicious_mail``'s docstring — it never touches
        ``agent._last_needs_you_card``), so a positional number here would
        contradict agent.py's own NUMBERING ITEMS IN YOUR REPLY rule and let
        a follow-up like "archive 1" resolve against a stale needs_you card
        instead of this list, acting on the wrong message. Fails against the
        pre-fix ``enumerate(items, start=1)`` rendering."""
        envelope = {
            "suspicious": [
                {
                    "message_id": "m1",
                    "sender": "a@example.com",
                    "subject": "Sub A",
                    "is_phishing": True,
                    "is_spam": False,
                    "why": "flagged as phishing",
                },
                {
                    "message_id": "m2",
                    "sender": "b@example.com",
                    "subject": "Sub B",
                    "is_phishing": False,
                    "is_spam": True,
                    "why": "flagged as spam",
                },
            ]
        }
        out = render_suspicious_list(envelope)
        for line in out.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            assert not re.match(r"^\d+\.", stripped), (
                f"row starts with a positional number the card doesn't "
                f"carry: {line!r}"
            )
            assert stripped.startswith("- "), f"expected a bullet row, got: {line!r}"

    def test_render_suspicious_list_empty_returns_empty_string(self):
        assert render_suspicious_list({"suspicious": []}) == ""


# ---------------------------------------------------------------------------
# _honest_suspicious_summary / rewrite_suspicious_mail_answer — the lead's
# quoted count must agree with what the list underneath actually shows
# (review follow-up on #2910: PRE_SCAN_SUSPICIOUS_CAP=10 caps the rendered
# list, but suspicious_total is captured pre-cap, so a scan with >10 flagged
# messages quoted a total the list never displayed).
# ---------------------------------------------------------------------------


class TestSuspiciousSummaryCapDisclosure:
    def test_summary_matches_list_when_under_the_cap(self):
        envelope = {
            "suspicious": [{"message_id": "m1"}],
            "suspicious_total": 1,
            "scanned": 25,
        }
        summary = _honest_suspicious_summary(envelope)
        assert summary == "1 flagged message this scan. 25 messages scanned."

    def test_summary_discloses_the_cap_when_total_exceeds_the_rendered_list(self):
        """The regression this test pins: quoting the pre-cap total while
        rendering only the capped list, with no indication the two differ."""
        envelope = {
            "suspicious": [{"message_id": f"m{i}"} for i in range(10)],
            "suspicious_total": 15,
            "scanned": 80,
        }
        summary = _honest_suspicious_summary(envelope)
        assert "15" in summary, "must not silently drop the true pre-cap total"
        assert "showing 10" in summary, (
            "must disclose the cap rather than silently disagreeing with "
            f"what render_suspicious_list actually renders — got: {summary!r}"
        )

    def test_malformed_suspicious_total_logs_a_warning_not_a_silent_fallback(
        self, caplog
    ):
        """``suspicious_total`` is a required contract field (default 0,
        always an int) — a missing/non-int value means a broken envelope,
        not a normal case. Falling back to the shown count is still the
        most honest number available, but doing so without a trace would
        be exactly the silent-fallback pattern CLAUDE.md forbids."""
        envelope = {
            "suspicious": [{"message_id": f"m{i}"} for i in range(10)],
            "suspicious_total": None,
            "scanned": 80,
        }
        with caplog.at_level(logging.WARNING, logger="gaia_agent_email"):
            summary = _honest_suspicious_summary(envelope)
        assert summary == "10 flagged messages this scan. 80 messages scanned."
        assert "suspicious_total" in caplog.text, (
            "malformed suspicious_total must be logged, not silently "
            f"substituted — got log output: {caplog.text!r}"
        )

    def test_rewrite_suspicious_mail_answer_lead_discloses_cap(self):
        """End-to-end: the lead line the user actually sees must agree with
        the list rendered beneath it, not just the underlying helper."""
        items = [
            {
                "message_id": f"m{i}",
                "sender": f"sender{i}@example.com",
                "subject": f"Sub {i}",
                "is_phishing": True,
                "is_spam": False,
            }
            for i in range(10)
        ]
        envelope = {
            "kind": "email_suspicious_scan",
            "suspicious": items,
            "suspicious_total": 15,
            "scanned": 80,
        }
        conversation = [_tool_entry("check_suspicious_mail", envelope)]
        out = rewrite_suspicious_mail_answer("Nothing flagged this scan.", conversation)
        assert out.startswith("15 flagged messages this scan — showing 10.")
        # The list beneath must actually contain exactly the 10 it claims.
        assert out.count("### Flagged this scan") == 1
        rendered_rows = [
            line for line in out.splitlines() if line.strip().startswith("- ")
        ]
        assert len(rendered_rows) == 10


# ---------------------------------------------------------------------------
# _honest_suspicious_summary — coverage caveats the replaced model sentence
# could have carried (a failed mailbox, a partial inbox scan) must survive
# the unconditional lead-sentence replacement, not just its counts (review
# follow-up on #2910).
# ---------------------------------------------------------------------------


class TestSuspiciousSummaryCoverageCaveats:
    def test_degraded_scan_names_the_failed_mailbox(self):
        envelope = {
            "suspicious": [{"message_id": "m1"}],
            "suspicious_total": 1,
            "scanned": 25,
            "degraded": True,
            "mailbox_errors": [{"mailbox": "microsoft", "error": "token expired"}],
        }
        summary = _honest_suspicious_summary(envelope)
        assert "Outlook" in summary, (
            f"must name the mailbox that failed (provider_label('microsoft') "
            f"== 'Outlook'), got: {summary!r}"
        )
        assert "couldn't be scanned" in summary
        # A user must not be able to read this as whole-account coverage.
        assert "only" in summary

    def test_degraded_scan_names_multiple_failed_mailboxes(self):
        envelope = {
            "suspicious": [],
            "suspicious_total": 0,
            "scanned": 10,
            "degraded": True,
            "mailbox_errors": [
                {"mailbox": "microsoft", "error": "token expired"},
                {"mailbox": "google", "error": "rate limited"},
            ],
        }
        summary = _honest_suspicious_summary(envelope)
        assert "Outlook" in summary
        assert "Gmail" in summary

    def test_non_degraded_scan_carries_no_failure_caveat(self):
        envelope = {
            "suspicious": [{"message_id": "m1"}],
            "suspicious_total": 1,
            "scanned": 25,
        }
        summary = _honest_suspicious_summary(envelope)
        assert "couldn't be scanned" not in summary
        assert summary == "1 flagged message this scan. 25 messages scanned."

    def test_malformed_mailbox_errors_entry_logs_a_warning_not_a_silent_drop(
        self, caplog
    ):
        envelope = {
            "suspicious": [],
            "suspicious_total": 0,
            "scanned": 10,
            "degraded": True,
            "mailbox_errors": [{"error": "no mailbox name"}],
        }
        with caplog.at_level(logging.WARNING, logger="gaia_agent_email"):
            summary = _honest_suspicious_summary(envelope)
        assert "could not be scanned" in summary
        assert "mailbox_errors" in caplog.text

    def test_partial_inbox_scan_states_the_denominator(self):
        envelope = {
            "suspicious": [],
            "suspicious_total": 0,
            "scanned": 50,
            "total_inbox": 812,
        }
        summary = _honest_suspicious_summary(envelope)
        assert "50 of 812 in the inbox scanned" in summary, summary
        assert "50 messages scanned" not in summary

    def test_full_inbox_scan_does_not_fabricate_a_denominator(self):
        envelope = {
            "suspicious": [],
            "suspicious_total": 0,
            "scanned": 812,
            "total_inbox": 812,
        }
        summary = _honest_suspicious_summary(envelope)
        assert "of 812" not in summary
        assert summary == "0 flagged messages this scan. 812 messages scanned."

    def test_unknown_total_inbox_does_not_fabricate_a_denominator(self):
        envelope = {
            "suspicious": [],
            "suspicious_total": 0,
            "scanned": 50,
            "total_inbox": None,
        }
        summary = _honest_suspicious_summary(envelope)
        assert "of" not in summary
        assert summary == "0 flagged messages this scan. 50 messages scanned."

    def test_rewrite_suspicious_mail_answer_lead_carries_both_caveats(self):
        """End-to-end: a degraded, partial scan's lead sentence — the one the
        user actually sees — must carry both caveats, not just the counts."""
        items = [
            {
                "message_id": "m1",
                "sender": "a@example.com",
                "subject": "Sub",
                "is_phishing": True,
                "is_spam": False,
            }
        ]
        envelope = {
            "kind": "email_suspicious_scan",
            "suspicious": items,
            "suspicious_total": 1,
            "scanned": 50,
            "total_inbox": 812,
            "degraded": True,
            "mailbox_errors": [{"mailbox": "microsoft", "error": "token expired"}],
        }
        conversation = [_tool_entry("check_suspicious_mail", envelope)]
        out = rewrite_suspicious_mail_answer("Nothing flagged this scan.", conversation)
        assert "50 of 812 in the inbox scanned" in out
        assert "Outlook" in out
        assert "couldn't be scanned" in out


# ---------------------------------------------------------------------------
# rewrite_suspicious_mail_answer — a ZERO-finding scan is not always a
# no-op (review follow-up on #2910/#2900): an unqualified "nothing
# suspicious" is a false all-clear when part of the account was never
# scanned, which is worse than the wordy-lead case the flagged path already
# fixes, since there is no list underneath to tip the user off.
# ---------------------------------------------------------------------------


class TestRewriteSuspiciousMailAnswerZeroFindingsCoverageGap:
    def test_degraded_zero_findings_names_the_failed_mailbox(self):
        envelope = {
            "kind": "email_suspicious_scan",
            "suspicious": [],
            "suspicious_total": 0,
            "scanned": 25,
            "degraded": True,
            "mailbox_errors": [{"mailbox": "microsoft", "error": "token expired"}],
        }
        conversation = [_tool_entry("check_suspicious_mail", envelope)]
        model_answer = "Nothing suspicious this scan."
        out = rewrite_suspicious_mail_answer(model_answer, conversation)
        assert out != model_answer, (
            "an unqualified all-clear must not survive a degraded scan with "
            "zero findings — the model's bare 'nothing suspicious' is exactly "
            "the false all-clear this guard exists to prevent"
        )
        assert "Outlook" in out
        assert "couldn't be scanned" in out

    def test_partial_zero_findings_states_the_denominator(self):
        envelope = {
            "kind": "email_suspicious_scan",
            "suspicious": [],
            "suspicious_total": 0,
            "scanned": 50,
            "total_inbox": 812,
        }
        conversation = [_tool_entry("check_suspicious_mail", envelope)]
        model_answer = "Nothing suspicious this scan."
        out = rewrite_suspicious_mail_answer(model_answer, conversation)
        assert out != model_answer
        assert "50 of 812 in the inbox scanned" in out

    def test_clean_complete_zero_findings_is_still_a_noop(self):
        """The acceptance bar this fix must not break: a genuinely clean,
        complete scan leaves the model's own (already correct) prose alone
        — no caveat is invented when there is nothing to disclose."""
        envelope = {
            "kind": "email_suspicious_scan",
            "suspicious": [],
            "suspicious_total": 0,
            "scanned": 812,
            "total_inbox": 812,
        }
        conversation = [_tool_entry("check_suspicious_mail", envelope)]
        model_answer = "Nothing suspicious this scan."
        out = rewrite_suspicious_mail_answer(model_answer, conversation)
        assert out == model_answer

    def test_pre_scan_inbox_also_ran_is_still_a_noop(self):
        """The existing deferral (both tools ran this turn) still wins over
        the new coverage-gap check — rewrite_triage_answer's card already
        covers this turn, so this function must not touch it either way."""
        suspicious_envelope = {
            "suspicious": [],
            "suspicious_total": 0,
            "degraded": True,
            "mailbox_errors": [{"mailbox": "microsoft", "error": "token expired"}],
        }
        prescan_envelope = {"kind": "email_pre_scan", "scanned": 10}
        conversation = [
            _tool_entry("check_suspicious_mail", suspicious_envelope),
            _tool_entry("pre_scan_inbox", prescan_envelope),
        ]
        already_rendered_card = "### Waiting on your reply\n\n1. Someone — Subject"
        out = rewrite_suspicious_mail_answer(already_rendered_card, conversation)
        assert out == already_rendered_card


# ---------------------------------------------------------------------------
# Contract additivity (schema 2.13)
# ---------------------------------------------------------------------------


class TestContractAdditivity:
    def test_schema_version_bumped(self):
        assert SCHEMA_VERSION == "2.14"

    def test_pre_scan_item_accepts_phishing_and_spam_flags(self):
        item = PreScanItem(
            message_id="m1", sender="a@example.com", subject="s", is_phishing=True
        )
        assert item.is_phishing is True
        assert item.is_spam is False  # default

    def test_pre_scan_item_defaults_both_false(self):
        item = PreScanItem(message_id="m1")
        assert item.is_phishing is False
        assert item.is_spam is False

    def test_email_pre_scan_result_accepts_suspicious_fields(self):
        result = EmailPreScanResult(
            suspicious=[
                PreScanItem(
                    message_id="m1",
                    sender="a@example.com",
                    subject="s",
                    is_phishing=True,
                )
            ],
            suspicious_total=1,
        )
        assert result.suspicious_total == 1
        assert result.suspicious[0].is_phishing is True

    def test_email_pre_scan_result_defaults_suspicious_empty(self):
        result = EmailPreScanResult()
        assert result.suspicious == []
        assert result.suspicious_total == 0


# ---------------------------------------------------------------------------
# System prompt wording — tool named, no scoped-phrasing enumeration (#2762)
# ---------------------------------------------------------------------------


class TestSystemPromptWording:
    def test_check_suspicious_mail_is_listed_as_a_read_tool(self):
        assert "check_suspicious_mail" in _SYSTEM_PROMPT

    def test_general_routing_sentence_present(self):
        # Reworded from "prefer the narrower tool" (#2900 gap fix) to a
        # stronger imperative — check the substance (narrower tool wins
        # over pre_scan_inbox for a scoped question), not the exact words.
        assert "must be used instead" in _SYSTEM_PROMPT.lower()
        assert "narrower tool" in _SYSTEM_PROMPT.lower()

    def test_no_scoped_question_phrasing_is_enumerated(self):
        """The design decision under test: the prompt names the TOOL
        (necessary — the model must know it exists to call it) but never
        hardcodes an example scoped question the way BRIEFING & TASKS
        hardcodes general-ask phrasings for get_briefing. A regex keyed on
        any of these strings appearing in the prompt would mean a future
        edit reintroduced exactly the phrase-matching #2762 forbids."""
        forbidden_example_phrasings = (
            "anything suspicious",
            "is there anything suspicious",
            "any phishing",
            "sketchy",
            "should i be worried",
        )
        lowered = _SYSTEM_PROMPT.lower()
        for phrase in forbidden_example_phrasings:
            assert phrase not in lowered, (
                f"system prompt hardcodes example phrasing {phrase!r} — "
                "this is exactly the router-table shortcut #2762 forbids"
            )
