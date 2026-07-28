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


def _run_isolated(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Score a corpus record as a standalone first-contact message: single
    message, its own thread, no prior correspondence."""
    body = rec.get("body", "") or ""
    to_addrs = rec.get("to") or [USER_EMAIL]
    message = {
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
    gmail = FakeGmailBackend(user_email=USER_EMAIL)
    gmail.add_message(message)
    return detect_waiting_on_you_impl(gmail, now_ms=NOW_MS)


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
