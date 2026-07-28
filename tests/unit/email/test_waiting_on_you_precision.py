# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Precision gate for the waiting-on-you detector (#2581), mirroring
``test_phishing_precision.py``'s corpus-driven approach.

Runs the full ``detect_waiting_on_you_impl`` pipeline (signal detection +
corroboration) against every PROMOTIONAL row of the vendor-derived
adversarial corpus (``tests/fixtures/email/vendor_corpus_seed.jsonl``),
scored as an isolated first-contact message (the corpus generator marks
every row ``is_thread_root=True`` — no row models prior correspondence, so
treating each as a cold first contact is the correct, conservative reading
of the fixture data, not an assumption this test imposes).

This is the issue's real gate (see the plan's "Constraints" section): a
single false "someone is waiting on you" costs more trust than several
misses, so precision is measured directly, not inferred from the unit
tests in ``hub/agents/email/python/tests/test_waiting_on_you_tools.py``.

Local, deterministic — no LLM, no Lemonade, no network.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.tools.calendar_tools import (  # noqa: E402
    detect_meeting_request_heuristic,
)
from gaia_agent_email.tools.text_signals import (  # noqa: E402
    has_direct_ask_signal,
    has_meeting_time_signal,
)
from gaia_agent_email.tools.triage_heuristics import (  # noqa: E402
    _AUTOMATED_SENDER_KEYWORDS,
)
from gaia_agent_email.tools.waiting_on_you_tools import (  # noqa: E402
    detect_waiting_on_you_impl,
)

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

_CORPUS_PATH = _REPO_ROOT / "tests" / "fixtures" / "email" / "vendor_corpus_seed.jsonl"
USER_EMAIL = "user@example.com"
NOW_MS = 1_750_000_000_000
DAY_MS = 24 * 60 * 60 * 1000


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _looks_automated(sender: str) -> bool:
    sender_lower = (sender or "").lower()
    return any(kw in sender_lower for kw in _AUTOMATED_SENDER_KEYWORDS)


def _load_corpus() -> List[Dict[str, Any]]:
    with open(_CORPUS_PATH, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _promotional_rows() -> List[Dict[str, Any]]:
    return [r for r in _load_corpus() if r.get("category") == "PROMOTIONAL"]


def _question_mark_non_automated_subset() -> List[Dict[str, Any]]:
    """The 47-row subset the #2584 panel measured: a literal '?' in subject
    or body, from a sender that does not look automated."""
    out = []
    for r in _promotional_rows():
        text = (r.get("subject", "") or "") + (r.get("body", "") or "")
        if "?" in text and not _looks_automated(r.get("sender", "")):
            out.append(r)
    return out


def _fires_signal(rec: Dict[str, Any]) -> bool:
    """True when the candidate's text alone would fire a direct-ask or
    meeting-time signal (either the leaf predicates or the gated existing
    calendar heuristic) — the subset a corroboration-side exploit needs to
    target, independent of whether corroboration is present."""
    subject_lower = (rec.get("subject", "") or "").lower()
    body_lower = (rec.get("body", "") or "").lower()
    if has_direct_ask_signal(subject_lower, body_lower):
        return True
    if has_meeting_time_signal(subject_lower, body_lower):
        return True
    detection = detect_meeting_request_heuristic(
        rec.get("subject", ""), rec.get("body", "")
    )
    return bool(detection.is_meeting_request and detection.confidence == "high")


def _candidate_message(rec: Dict[str, Any]) -> Dict[str, Any]:
    body = rec.get("body", "") or ""
    to_addrs = rec.get("to") or [USER_EMAIL]
    return {
        "id": rec["id"],
        "threadId": rec["id"],
        "labelIds": ["INBOX"],
        "snippet": body[:80],
        "internalDate": str(NOW_MS - DAY_MS),
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "Subject", "value": rec.get("subject", "")},
                {"name": "From", "value": rec.get("sender", "")},
                {"name": "To", "value": ", ".join(to_addrs)},
                {"name": "Date", "value": "1 day ago"},
            ],
            "body": {"size": len(body), "data": _b64(body)},
        },
        "sizeEstimate": len(body),
    }


def _outbound_message(
    msg_id: str, *, thread_id: str, to: str, body: str, age_days: float
) -> Dict[str, Any]:
    return {
        "id": msg_id,
        "threadId": thread_id,
        "labelIds": ["SENT"],
        "snippet": body[:80],
        "internalDate": str(int(NOW_MS - age_days * DAY_MS)),
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "Subject", "value": "Re:"},
                {"name": "From", "value": f"Me <{USER_EMAIL}>"},
                {"name": "To", "value": to},
                {"name": "Date", "value": f"{age_days} days ago"},
            ],
            "body": {"size": len(body), "data": _b64(body)},
        },
        "sizeEstimate": len(body),
    }


def _run_isolated(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Score a corpus record as a standalone first-contact message: single
    message, its own thread, no prior correspondence."""
    gmail = FakeGmailBackend(user_email=USER_EMAIL)
    gmail.add_message(_candidate_message(rec))
    return detect_waiting_on_you_impl(gmail, now_ms=NOW_MS)


def _sender_address(rec: Dict[str, Any]) -> str:
    from gaia_agent_email.tools.read_tools import extract_sender_email

    return extract_sender_email(rec.get("sender", ""))


# ---------------------------------------------------------------------------
# Corpus integrity — locks in the panel's stated measurements
# ---------------------------------------------------------------------------


class TestCorpusMeasurementsMatchPanelFacts:
    def test_promotional_row_count_is_104(self):
        assert len(_promotional_rows()) == 104

    def test_question_mark_non_automated_subset_is_47(self):
        assert len(_question_mark_non_automated_subset()) == 47

    def test_adversarial_subtype_count_is_42(self):
        adversarial = [
            r
            for r in _promotional_rows()
            if r.get("promotional_subtype") == "adversarial"
        ]
        assert len(adversarial) == 42


# ---------------------------------------------------------------------------
# The real gate: zero false positives, reported explicitly
# ---------------------------------------------------------------------------


class TestPrecisionGate:
    def test_zero_qualify_over_full_promotional_corpus(self, capsys):
        records = _promotional_rows()
        false_positives = [
            rec["id"] for rec in records if _run_isolated(rec)["waiting_on_you"]
        ]
        print(
            f"\nwaiting-on-you false positives over {len(records)} PROMOTIONAL "
            f"rows: {len(false_positives)} {false_positives}"
        )
        assert false_positives == [], (
            f"detector qualified {len(false_positives)} of {len(records)} "
            f"PROMOTIONAL rows that must stay negative: {false_positives}"
        )

    def test_zero_qualify_over_question_mark_non_automated_subset(self, capsys):
        """AC: 'proven against the 47-row ?-bearing non-automated
        PROMOTIONAL subset, which must stay negative.'"""
        subset = _question_mark_non_automated_subset()
        false_positives = [
            rec["id"] for rec in subset if _run_isolated(rec)["waiting_on_you"]
        ]
        print(
            f"\nwaiting-on-you false positives over the {len(subset)}-row "
            f"'?'-bearing non-automated subset: {len(false_positives)} "
            f"{false_positives}"
        )
        assert false_positives == [], (
            f"detector qualified {len(false_positives)} of {len(subset)} rows "
            f"in the '?'-bearing non-automated subset: {false_positives}"
        )


# ---------------------------------------------------------------------------
# Checkpoint-fix gate: corroboration exercised, not structurally absent.
#
# The isolated-first-contact measurement above proves nothing about the
# corroboration gate itself — every corpus row is a thread root with no
# prior correspondence, so that gate never actually runs. An independent
# adversarial verifier confirmed the isolated zero-FP number and then
# manufactured corroboration two cheap ways, both of which used to qualify
# 15/15 of the 25 rows whose text alone fires a direct-ask/meeting-time
# signal (including both named regression ids). This section re-runs that
# exact construction against the fixed detector and reports the count under
# each — this, not the isolated-corpus number, is the real gate.
# ---------------------------------------------------------------------------


def _signal_firing_rows() -> List[Dict[str, Any]]:
    return [r for r in _promotional_rows() if _fires_signal(r)]


class TestCorroborationExercisedGate:
    def test_signal_firing_subset_is_25_rows(self):
        """Locks in the verifier's stated denominator."""
        assert len(_signal_firing_rows()) == 25

    def test_6a_prior_outbound_in_thread_does_not_manufacture_corroboration(
        self, capsys
    ):
        """Verifier construction 6a: inject one earlier outbound message in
        the SAME thread reading only "Please remove me from this list." —
        this used to qualify 15/15 signal-firing rows, including both named
        regression ids, because ``has_earlier_outbound`` was content-blind."""
        rows = _signal_firing_rows()
        false_positives = []
        for rec in rows:
            gmail = FakeGmailBackend(user_email=USER_EMAIL)
            sender_addr = _sender_address(rec) or "sender@example.com"
            gmail.add_message(
                _outbound_message(
                    f"{rec['id']}-prior",
                    thread_id=rec["id"],
                    to=sender_addr,
                    body="Please remove me from this list.",
                    age_days=30,
                )
            )
            gmail.add_message(_candidate_message(rec))
            out = detect_waiting_on_you_impl(gmail, now_ms=NOW_MS)
            if out["waiting_on_you"]:
                false_positives.append(rec["id"])
        print(
            f"\n6a (prior in-thread opt-out-shaped outbound) false positives "
            f"over {len(rows)} signal-firing rows: {len(false_positives)} "
            f"{false_positives}"
        )
        assert false_positives == [], (
            f"6a construction qualified {len(false_positives)} of "
            f"{len(rows)} rows: {false_positives}"
        )

    def test_6b_unrelated_prior_sent_thread_does_not_manufacture_corroboration(
        self, capsys
    ):
        """Verifier construction 6b: inject one earlier, UNRELATED sent
        thread to the same address reading only "what's the pricing?" — this
        used to qualify 15/15 signal-firing rows via the known-correspondent
        path, because a single one-off contact was treated as sufficient."""
        rows = _signal_firing_rows()
        false_positives = []
        for rec in rows:
            gmail = FakeGmailBackend(user_email=USER_EMAIL)
            sender_addr = _sender_address(rec) or "sender@example.com"
            gmail.add_message(
                _outbound_message(
                    f"{rec['id']}-unrelated-sent",
                    thread_id=f"{rec['id']}-unrelated-thread",
                    to=sender_addr,
                    body="what's the pricing?",
                    age_days=60,
                )
            )
            gmail.add_message(_candidate_message(rec))
            out = detect_waiting_on_you_impl(gmail, now_ms=NOW_MS)
            if out["waiting_on_you"]:
                false_positives.append(rec["id"])
        print(
            f"\n6b (one unrelated prior sent thread) false positives over "
            f"{len(rows)} signal-firing rows: {len(false_positives)} "
            f"{false_positives}"
        )
        assert false_positives == [], (
            f"6b construction qualified {len(false_positives)} of "
            f"{len(rows)} rows: {false_positives}"
        )

    def test_6c_genuine_substantive_message_in_different_thread_does_not_qualify(
        self, capsys
    ):
        """Verifier construction 6c (the one that broke the first fix): one
        GENUINE, substantive prior message to a vendor in a DIFFERENT
        thread — "Thanks for reaching out. Could you send over pricing for
        the enterprise tier and whether it includes SSO?" (25 words, not a
        dismissal, not opt-out-shaped) — used to corroborate 24/25
        signal-firing rows via known_correspondent, because "has this
        person ever sent me a real message" is not evidence THIS message
        needs a reply. The known_correspondent path was removed entirely
        (not tightened further) specifically because tightening the
        word/count floor cannot fix this: the prior message here is
        genuinely substantive by any reasonable floor."""
        rows = _signal_firing_rows()
        false_positives = []
        for rec in rows:
            gmail = FakeGmailBackend(user_email=USER_EMAIL)
            sender_addr = _sender_address(rec) or "sender@example.com"
            gmail.add_message(
                _outbound_message(
                    f"{rec['id']}-different-thread",
                    thread_id=f"{rec['id']}-different-thread-id",
                    to=sender_addr,
                    body=(
                        "Thanks for reaching out. Could you send over "
                        "pricing for the enterprise tier and whether it "
                        "includes SSO?"
                    ),
                    age_days=45,
                )
            )
            gmail.add_message(_candidate_message(rec))
            out = detect_waiting_on_you_impl(gmail, now_ms=NOW_MS)
            if out["waiting_on_you"]:
                false_positives.append(rec["id"])
        print(
            f"\n6c (genuine substantive prior message, different thread) "
            f"false positives over {len(rows)} signal-firing rows: "
            f"{len(false_positives)} {false_positives}"
        )
        assert false_positives == [], (
            f"6c construction qualified {len(false_positives)} of "
            f"{len(rows)} rows: {false_positives}"
        )


# ---------------------------------------------------------------------------
# Recall sanity check: the surviving corroboration path (genuine in-thread
# history) must still catch the real positive cases this feature exists for.
# Not corpus-derived (the corpus has no genuine positives — it is a
# PROMOTIONAL-only precision fixture) — these mirror the hand-authored
# positive scenarios in test_waiting_on_you_tools.py, re-asserted here
# alongside the precision numbers so all the evidence lives in one place.
# ---------------------------------------------------------------------------


class TestRecallHoldsAfterHardening:
    def test_genuine_in_thread_history_still_qualifies(self, capsys):
        gmail = FakeGmailBackend(user_email=USER_EMAIL)
        gmail.add_message(
            _outbound_message(
                "s1",
                thread_id="t1",
                to="alice@example.com",
                body=(
                    "Sure, I will take a look at the numbers and get back "
                    "to you with any questions before the review."
                ),
                age_days=10,
            )
        )
        candidate = {
            "id": "r1",
            "threadId": "t1",
            "labelIds": ["INBOX"],
            "snippet": "",
            "internalDate": str(NOW_MS - 3 * DAY_MS),
            "payload": {
                "mimeType": "text/plain",
                "filename": "",
                "headers": [
                    {"name": "Subject", "value": "Re: budget"},
                    {"name": "From", "value": "Alice <alice@example.com>"},
                    {"name": "To", "value": USER_EMAIL},
                    {"name": "Date", "value": "3 days ago"},
                ],
                "body": {
                    "size": 0,
                    "data": _b64(
                        "Thanks! Could you please confirm the numbers by Friday?"
                    ),
                },
            },
            "sizeEstimate": 0,
        }
        gmail.add_message(candidate)
        out = detect_waiting_on_you_impl(gmail, now_ms=NOW_MS)
        print(f"\ngenuine in-thread history recall check: {out['waiting_on_you']}")
        assert len(out["waiting_on_you"]) == 1
        assert out["waiting_on_you"][0]["message_id"] == "r1"

    def test_incident_wording_in_genuine_thread_still_qualifies(self, capsys):
        gmail = FakeGmailBackend(user_email=USER_EMAIL)
        gmail.add_message(
            _outbound_message(
                "s1",
                thread_id="t1",
                to="alice@example.com",
                body=(
                    "Sure, I will take a look at the numbers and get back "
                    "to you with any questions before the review."
                ),
                age_days=10,
            )
        )
        candidate = {
            "id": "r1",
            "threadId": "t1",
            "labelIds": ["INBOX"],
            "snippet": "",
            "internalDate": str(NOW_MS - 2 * DAY_MS),
            "payload": {
                "mimeType": "text/plain",
                "filename": "",
                "headers": [
                    {"name": "Subject", "value": "Catching up"},
                    {"name": "From", "value": "Alice <alice@example.com>"},
                    {"name": "To", "value": USER_EMAIL},
                    {"name": "Date", "value": "2 days ago"},
                ],
                "body": {
                    "size": 0,
                    "data": _b64("Any chance to meet this Thursday at 9am?"),
                },
            },
            "sizeEstimate": 0,
        }
        gmail.add_message(candidate)
        out = detect_waiting_on_you_impl(gmail, now_ms=NOW_MS)
        print(f"\nincident-wording recall check: {out['waiting_on_you']}")
        assert len(out["waiting_on_you"]) == 1
        assert out["waiting_on_you"][0]["message_id"] == "r1"
