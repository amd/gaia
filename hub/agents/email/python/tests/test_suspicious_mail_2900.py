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
    last_tool_payload,
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
# Contract additivity (schema 2.13)
# ---------------------------------------------------------------------------


class TestContractAdditivity:
    def test_schema_version_bumped(self):
        assert SCHEMA_VERSION == "2.13"

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
        assert "prefer the narrower tool" in _SYSTEM_PROMPT.lower()

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
