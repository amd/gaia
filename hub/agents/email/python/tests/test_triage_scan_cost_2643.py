# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""#2643 — ``triage_inbox_impl``'s per-message fetch loop goes metadata-first
with escalate-on-demand full-body fetches, batched (levers 1+2).

Central claim under test: the heuristic pass runs on metadata alone (no
full-body HTTP fetch for a message the heuristic resolves from labels/
snippet/headers), and the SAME classification comes out either way -- this
is a cost change, not a behavior change. AC from the issue:

- a fake backend records ZERO full-body fetches for a message the heuristic
  resolves from labels alone
- a CATEGORY_PROMOTIONAL message whose body/snippet carries a deadline
  signal still escalates (the NOTUS-style regression guard) -- and when it
  does, the classifier receives the REAL decoded body, not an empty one
- envelope tokens at the new ceiling stay within envelope_budget_tokens()
- classification is unchanged on a fixed corpus before/after -- proven here
  by showing the loop's heuristic decision always matches an independent
  direct call to classify_category_heuristic on the same message's own
  snippet/headers/labels (the loop cannot have fed it something different)
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.context_budget import (  # noqa: E402
    envelope_budget_tokens,
    estimate_tokens_json,
)
from gaia_agent_email.tools.read_tools import (  # noqa: E402
    pre_scan_inbox_impl,
    triage_inbox_impl,
)
from gaia_agent_email.tools.triage_condense import condense_triage_result  # noqa: E402
from gaia_agent_email.tools.triage_heuristics import (  # noqa: E402
    CATEGORY_PROMOTIONAL,
    classify_category_heuristic,
)

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _msg(
    msg_id: str,
    *,
    subject: str,
    sender: str,
    label_ids: List[str],
    body: str,
    snippet: str | None = None,
) -> Dict[str, Any]:
    return {
        "id": msg_id,
        "threadId": f"t-{msg_id}",
        "labelIds": list(label_ids),
        "snippet": snippet if snippet is not None else body[:200],
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


def _batch_calls(gmail: FakeGmailBackend) -> List[tuple]:
    return [c for c in gmail.transport.calls if c[0] == "get_messages_batch"]


def _full_format_ids(gmail: FakeGmailBackend) -> set:
    out: set = set()
    for _method, kwargs in _batch_calls(gmail):
        if kwargs.get("format") == "full":
            out.update(kwargs.get("message_ids", []))
    return out


def _metadata_format_ids(gmail: FakeGmailBackend) -> set:
    out: set = set()
    for _method, kwargs in _batch_calls(gmail):
        if kwargs.get("format") == "metadata":
            out.update(kwargs.get("message_ids", []))
    return out


class TestMetadataFirstNoEscalation:
    def test_confidently_labeled_messages_never_get_a_full_body_fetch(self):
        gmail = FakeGmailBackend()
        for i in range(20):
            gmail.add_message(
                _msg(
                    f"m{i}",
                    subject=f"Weekly deals #{i}",
                    sender="deals@shop.example",
                    label_ids=["INBOX", "CATEGORY_PROMOTIONS"],
                    body="Huge savings this week on everything in store.",
                )
            )
        out = triage_inbox_impl(gmail, max_messages=20)
        assert out["resultSizeEstimate"] is None or True  # not under test here
        assert len(out["results"]) == 20
        assert all(r["confident"] for r in out["results"])
        assert _full_format_ids(gmail) == set(), (
            "every message here resolves confidently from its Gmail label -- "
            "the heuristic pass must never trigger a full-body fetch for any "
            f"of them; full-format ids fetched: {_full_format_ids(gmail)}"
        )
        assert _metadata_format_ids(gmail) == {f"m{i}" for i in range(20)}

    def test_a_single_metadata_batch_call_covers_the_whole_scan(self):
        from gaia_agent_email.gmail_backend import _BATCH_MAX_SUBREQUESTS

        scan_size = _BATCH_MAX_SUBREQUESTS  # exactly one chunk's worth
        gmail = FakeGmailBackend()
        for i in range(scan_size):
            gmail.add_message(
                _msg(
                    f"m{i}",
                    subject="Newsletter",
                    sender="news@example.com",
                    label_ids=["INBOX", "CATEGORY_UPDATES"],
                    body="Nothing to see here.",
                )
            )
        triage_inbox_impl(gmail, max_messages=scan_size)
        metadata_calls = [
            c for c in _batch_calls(gmail) if c[1].get("format") == "metadata"
        ]
        assert len(metadata_calls) == 1, (
            f"{scan_size} ids at the batch subrequest ceiling must cost "
            f"exactly ONE metadata round-trip, got {len(metadata_calls)}"
        )


class TestEscalationFetchesFullBody:
    def test_unresolved_message_triggers_exactly_one_full_batch_fetch(self):
        gmail = FakeGmailBackend()
        # No label, no subject keyword, no automated-sender match, not
        # IMPORTANT/STARRED -- falls all the way through to "no match".
        gmail.add_message(
            _msg(
                "m1",
                subject="Re: dinner Friday?",
                sender="alice@example.com",
                label_ids=["INBOX"],
                body="Want to grab dinner Friday night?",
            )
        )
        received = {}

        def _classifier(*, subject, sender, body, message_id=""):
            received["subject"] = subject
            received["sender"] = sender
            received["body"] = body
            received["message_id"] = message_id
            return {"category": "PERSONAL", "is_spam": False, "confidence": 0.9}

        out = triage_inbox_impl(gmail, max_messages=1, classifier=_classifier)
        assert out["results"][0]["source"] == "llm"
        assert _full_format_ids(gmail) == {"m1"}
        assert received["body"] == "Want to grab dinner Friday night?", (
            "the classifier must receive the REAL decoded body from a "
            "format='full' re-fetch, never the metadata-mode empty body"
        )
        assert received["subject"] == "Re: dinner Friday?"
        assert received["message_id"] == "m1"

    def test_no_classifier_wired_never_fetches_full_body_even_on_escalation(self):
        """pre_scan_inbox never wires a classifier (#2584's existing
        canary) -- an unresolved message stays confident=False but nobody
        needs its body, so the loop must not pay for a full fetch nobody
        will read."""
        gmail = FakeGmailBackend()
        gmail.add_message(
            _msg(
                "m1",
                subject="Re: dinner Friday?",
                sender="alice@example.com",
                label_ids=["INBOX"],
                body="Want to grab dinner Friday night?",
            )
        )
        out = triage_inbox_impl(gmail, max_messages=1, classifier=None)
        assert out["results"][0]["confident"] is False
        assert _full_format_ids(gmail) == set()

    def test_mixed_batch_only_escalated_ids_get_full_fetch(self):
        """CATEGORY_UPDATES (not CATEGORY_PROMOTIONS) for the confident
        bucket: a confidently-PROMOTIONAL message from a non-spam-pattern
        sender still escalates for its own reason (spam_confident=False,
        #1906 -- the LLM gets a say on is_spam even when the category is
        certain), which is correct, pre-existing, unrelated-to-#2643
        behavior. CATEGORY_UPDATES resolves to FYI, a non-PROMOTIONAL
        category, which the heuristic marks spam_confident=True
        unconditionally -- the clean "confidently resolved, no escalation
        for any reason" case this test wants."""
        gmail = FakeGmailBackend()
        for i in range(8):
            gmail.add_message(
                _msg(
                    f"update{i}",
                    subject="Your receipt",
                    sender="receipts@shop.example",
                    label_ids=["INBOX", "CATEGORY_UPDATES"],
                    body="Thanks for your order.",
                )
            )
        gmail.add_message(
            _msg(
                "needs_llm_1",
                subject="Quick question",
                sender="bob@example.com",
                label_ids=["INBOX"],
                body="Can you send me the deck from yesterday?",
            )
        )
        gmail.add_message(
            _msg(
                "needs_llm_2",
                subject="Another question",
                sender="carol@example.com",
                label_ids=["INBOX"],
                body="Any update on the proposal?",
            )
        )

        def _classifier(*, subject, sender, body, message_id=""):
            return {"category": "NEEDS_RESPONSE", "is_spam": False, "confidence": 0.8}

        triage_inbox_impl(gmail, max_messages=10, classifier=_classifier)
        assert _full_format_ids(gmail) == {"needs_llm_1", "needs_llm_2"}

    def test_confident_promotional_still_escalates_for_its_own_reason(self):
        """Documents the #1906 interaction directly (see the test above's
        docstring): a confidently-PROMOTIONAL message is not is_spam-
        confident, so it DOES trigger a full fetch when a classifier is
        wired -- for is_spam, never for category. This is pre-#2643,
        unrelated behavior; #2643 must not change it."""
        gmail = FakeGmailBackend()
        gmail.add_message(
            _msg(
                "promo1",
                subject="Sale",
                sender="deals@shop.example",
                label_ids=["INBOX", "CATEGORY_PROMOTIONS"],
                body="Save big.",
            )
        )

        def _classifier(*, subject, sender, body, message_id=""):
            return {"category": "PROMOTIONAL", "is_spam": False, "confidence": 0.6}

        out = triage_inbox_impl(gmail, max_messages=1, classifier=_classifier)
        assert _full_format_ids(gmail) == {"promo1"}
        result = out["results"][0]
        # Category came from the heuristic (already confident) and is
        # untouched; only is_spam was resolved by the LLM follow-up.
        assert result["category"] == CATEGORY_PROMOTIONAL
        assert result["source"] == "heuristic"


class TestClassificationUnchangedByFetchStrategy:
    """The heuristic decision the loop records must always match an
    independent, direct call to classify_category_heuristic on the SAME
    message's own snippet/subject/sender/labelIds -- proving the loop never
    feeds the heuristic something different (e.g. a truncated or missing
    snippet) than it would have gotten from the pre-#2643 full fetch, since
    Gmail's snippet field is identical regardless of format requested."""

    _CASES = [
        dict(
            subject="50% off — but heuristic matches the label",
            sender="deals@shop.example",
            label_ids=["INBOX", "CATEGORY_PROMOTIONS"],
            body="Big sale today only.",
        ),
        dict(
            subject="Your weekly digest",
            sender="notifications@service.example",
            label_ids=["INBOX"],
            body="Here is your weekly digest.",
        ),
        dict(
            subject="Re: budget review",
            sender="ceo@company.example",
            label_ids=["INBOX", "IMPORTANT"],
            body="Please review before Friday.",
        ),
        dict(
            subject="Membership renewal reminder",
            sender="Community Club <news@club.example>",
            label_ids=["INBOX", "CATEGORY_PROMOTIONS"],
            body=(
                "Attendance is required at this week's meeting. Failure to "
                "attend will result in suspension of your membership."
            ),
        ),
        dict(
            subject="Re: dinner Friday?",
            sender="alice@example.com",
            label_ids=["INBOX"],
            body="Want to grab dinner Friday night?",
        ),
    ]

    def test_loop_heuristic_matches_direct_call_for_every_case(self):
        gmail = FakeGmailBackend()
        for i, case in enumerate(self._CASES):
            gmail.add_message(_msg(f"m{i}", **case))

        out = triage_inbox_impl(gmail, max_messages=len(self._CASES))
        by_id = {r["id"]: r for r in out["results"]}

        for i, case in enumerate(self._CASES):
            mid = f"m{i}"
            snippet = case["body"][:200]
            direct = classify_category_heuristic(
                subject=case["subject"],
                sender=case["sender"],
                label_ids=case["label_ids"],
                body=snippet,
            )
            loop_result = by_id[mid]
            assert loop_result["category"] == direct.category, mid
            assert loop_result["confident"] == direct.confident, mid
            assert loop_result["is_spam"] == direct.is_spam, mid
            assert loop_result["is_phishing"] == direct.is_phishing, mid


class TestResultOrderPreserved:
    def test_results_stay_in_stub_order_across_the_two_phase_fetch(self):
        gmail = FakeGmailBackend()
        ids = []
        for i in range(15):
            mid = f"m{i:02d}"
            ids.append(mid)
            # Alternate confident-label and needs-escalation so the two
            # phases genuinely interleave in the input order.
            if i % 3 == 0:
                gmail.add_message(
                    _msg(
                        mid,
                        subject=f"Sale {i}",
                        sender="deals@shop.example",
                        label_ids=["INBOX", "CATEGORY_PROMOTIONS"],
                        body="Save now.",
                    )
                )
            else:
                gmail.add_message(
                    _msg(
                        mid,
                        subject=f"Personal note {i}",
                        sender=f"person{i}@example.com",
                        label_ids=["INBOX"],
                        body=f"Just checking in, message {i}.",
                    )
                )
        out = triage_inbox_impl(gmail, max_messages=15)
        assert [r["id"] for r in out["results"]] == ids


class TestNOTUSStyleCommitmentVetoStillEscalates:
    """#2087/#2113 regression guard: a bulk/promotional message with a real
    deadline in the SNIPPET must still escalate, unaffected by the fetch
    strategy -- the commitment-signal check reads msg["snippet"], which is
    identical under format=metadata and format=full (Gmail returns the same
    snippet regardless of requested format)."""

    def test_promotional_label_with_deadline_signal_in_snippet_escalates(self):
        gmail = FakeGmailBackend()
        body = (
            "NOTUS Weekly: your community pass renewal is due. Confirm by "
            "Friday or your membership will be suspended."
        )
        gmail.add_message(
            _msg(
                "notus1",
                subject="NOTUS Weekly",
                sender="news@notus.example",
                label_ids=["INBOX", "CATEGORY_PROMOTIONS"],
                body=body,
            )
        )
        received = {}

        def _classifier(*, subject, sender, body, message_id=""):
            received["body"] = body
            return {"category": "NEEDS_RESPONSE", "is_spam": False, "confidence": 0.7}

        out = triage_inbox_impl(gmail, max_messages=1, classifier=_classifier)
        result = out["results"][0]
        assert result["confident"] is False or result["source"] == "llm"
        assert (
            result["category"] == CATEGORY_PROMOTIONAL
            or result["category"] == "NEEDS_RESPONSE"
        )
        assert (
            "deadline/commitment signal" in result["rationale"]
            or result["source"] == "llm"
        )
        # The full body must have reached the classifier -- proves the
        # commitment-signal escalation correctly triggered a real
        # format="full" re-fetch, not a metadata-only stub.
        assert received["body"] == body


class TestEnvelopeBudgetAtRaisedCeiling:
    """#2087 regression guard: raising the ceiling must not blow the
    agent-loop's re-read budget. Structurally guaranteed by
    condense_triage_result / pre_scan_inbox's per-section caps -- this test
    proves it holds, not just asserts intent."""

    def _seed(self, gmail: FakeGmailBackend, n: int) -> None:
        for i in range(n):
            gmail.add_message(
                _msg(
                    f"m{i}",
                    subject=f"Message number {i} with a reasonably long subject line",
                    sender=f"sender{i}@example.com",
                    label_ids=["INBOX", "CATEGORY_PROMOTIONS"] if i % 2 else ["INBOX"],
                    body="Body text " * 30,
                )
            )

    @pytest.mark.parametrize("n", [100, 200])
    def test_triage_inbox_condensed_envelope_stays_in_budget(self, n):
        gmail = FakeGmailBackend()
        self._seed(gmail, n)
        raw = triage_inbox_impl(gmail, max_messages=n)
        condensed = condense_triage_result(raw)
        tokens = estimate_tokens_json(json.dumps(condensed, default=str))
        assert tokens <= envelope_budget_tokens(), (
            f"condensed triage envelope at n={n} used {tokens} tokens, "
            f"budget is {envelope_budget_tokens()}"
        )

    @pytest.mark.parametrize("n", [100, 200])
    def test_pre_scan_envelope_stays_in_budget(self, n):
        gmail = FakeGmailBackend()
        self._seed(gmail, n)
        result = pre_scan_inbox_impl(gmail, max_messages=n)
        tokens = estimate_tokens_json(json.dumps(result, default=str))
        assert tokens <= envelope_budget_tokens(), (
            f"pre-scan envelope at n={n} used {tokens} tokens, budget is "
            f"{envelope_budget_tokens()}"
        )
