# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""SLM triage-category integration tests.

Two layers, both hermetic (no Lemonade, no network, no model download):

1. ``triage_inbox_impl`` routing — with fake ``slm_classifier`` / ``classifier``
   callables, prove the SLM runs BEFORE the LLM, that an SLM hit skips the LLM
   for the category, that a None result / ``force_llm`` falls back to the LLM,
   and that an unresolved ``is_spam`` still reaches the LLM without the LLM
   overwriting the SLM's category.
2. ``classify_email_slm`` — with a fake classifier object, prove the "worked"
   contract: a usable in-taxonomy label returns a mapping; empty labels, an
   out-of-taxonomy label, or a raising ``predict_one`` all fail safe to None.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# parents[0]=tests/, [1]=email/, [2]=python/, [3]=agents/, [4]=hub/, [5]=repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.tools.read_tools import triage_inbox_impl  # noqa: E402
from gaia_agent_email.tools.slm_triage import classify_email_slm  # noqa: E402
from gaia_agent_email.tools.triage_heuristics import (  # noqa: E402
    CATEGORY_URGENT,
)

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _msg(
    msg_id: str,
    *,
    subject: str,
    sender: str = "alice@example.com",
    labels: Optional[List[str]] = None,
    body: str = "Some neutral body content.",
) -> Dict[str, Any]:
    return {
        "id": msg_id,
        "threadId": msg_id,
        "labelIds": labels or ["INBOX"],
        "snippet": body[:200],
        "internalDate": "1750000000000",
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
                {"name": "To", "value": "user@example.com"},
            ],
            "body": {"data": _b64url(body), "size": len(body.encode("utf-8"))},
        },
        "sizeEstimate": len(body),
    }


def _backend(*messages: Dict[str, Any]) -> FakeGmailBackend:
    gmail = FakeGmailBackend(user_email="user@example.com")
    for m in messages:
        gmail.add_message(m)
    return gmail


class _RecordingLLM:
    """Fake LLM classifier callable — records calls, returns a fixed mapping."""

    def __init__(self, category: str = "FYI", is_spam: bool = False) -> None:
        self.calls: List[dict] = []
        self._category = category
        self._is_spam = is_spam

    def __call__(self, *, subject, sender, body, message_id=""):
        self.calls.append({"subject": subject, "message_id": message_id})
        return {
            "category": self._category,
            "is_spam": self._is_spam,
            "confidence": 0.5,
            "reasoning": "llm reasoning",
            "suggested_action": "none",
        }


class _RecordingSLM:
    """Fake SLM classifier callable — records calls, returns a mapping or None."""

    def __init__(self, result: Optional[dict]) -> None:
        self.calls: List[dict] = []
        self._result = result

    def __call__(self, *, subject, sender, body, message_id=""):
        self.calls.append({"subject": subject, "message_id": message_id})
        return self._result


# ---------------------------------------------------------------------------
# triage_inbox_impl routing
# ---------------------------------------------------------------------------


class TestTriageRouting:
    # IMPORTANT label -> heuristic is confident=False but spam_confident=True,
    # so a resolved category means NO residual is_spam LLM call.
    _IMPORTANT = ["INBOX", "IMPORTANT"]

    def test_slm_hit_skips_llm_for_category(self):
        gmail = _backend(_msg("m1", subject="Re: project sync", labels=self._IMPORTANT))
        slm = _RecordingSLM({"category": CATEGORY_URGENT, "confidence": 0.91})
        llm = _RecordingLLM(category="FYI")

        out = triage_inbox_impl(gmail, classifier=llm, slm_classifier=slm)

        decision = out["results"][0]
        assert decision["category"] == CATEGORY_URGENT
        assert decision["source"] == "slm"
        assert decision["slm_confidence"] == 0.91
        assert len(slm.calls) == 1
        assert llm.calls == []  # LLM skipped — the whole point

    def test_slm_miss_falls_back_to_llm(self):
        gmail = _backend(_msg("m1", subject="Re: project sync", labels=self._IMPORTANT))
        slm = _RecordingSLM(None)  # "did not work"
        llm = _RecordingLLM(category="FYI")

        out = triage_inbox_impl(gmail, classifier=llm, slm_classifier=slm)

        decision = out["results"][0]
        assert decision["category"] == "FYI"
        assert decision["source"] == "llm"
        assert len(slm.calls) == 1
        assert len(llm.calls) == 1  # fell back

    def test_force_llm_skips_slm(self):
        gmail = _backend(_msg("m1", subject="Re: project sync", labels=self._IMPORTANT))
        slm = _RecordingSLM({"category": CATEGORY_URGENT, "confidence": 0.9})
        llm = _RecordingLLM(category="FYI")

        out = triage_inbox_impl(
            gmail, classifier=llm, slm_classifier=slm, force_llm=True
        )

        decision = out["results"][0]
        assert decision["source"] == "llm"
        assert slm.calls == []  # SLM never consulted under force_llm
        assert len(llm.calls) == 1

    def test_slm_category_hit_still_lets_llm_resolve_spam(self):
        # A plain message (no matching heuristic) is confident=False AND
        # spam_confident=False, so is_spam is unresolved. The SLM resolves the
        # category; the LLM is still called for is_spam, but must NOT overwrite
        # the SLM category.
        gmail = _backend(_msg("m1", subject="hello there", labels=["INBOX"]))
        slm = _RecordingSLM({"category": CATEGORY_URGENT, "confidence": 0.8})
        llm = _RecordingLLM(category="FYI", is_spam=True)

        out = triage_inbox_impl(gmail, classifier=llm, slm_classifier=slm)

        decision = out["results"][0]
        assert decision["category"] == CATEGORY_URGENT  # SLM preserved
        assert decision["source"] == "slm"
        assert decision["is_spam"] is True  # spam came from the LLM
        assert len(slm.calls) == 1
        assert len(llm.calls) == 1

    def test_no_slm_wired_is_unchanged_behavior(self):
        gmail = _backend(_msg("m1", subject="Re: project sync", labels=self._IMPORTANT))
        llm = _RecordingLLM(category="FYI")

        out = triage_inbox_impl(gmail, classifier=llm)  # no slm_classifier

        decision = out["results"][0]
        assert decision["source"] == "llm"
        assert len(llm.calls) == 1


# ---------------------------------------------------------------------------
# classify_email_slm — the "worked" contract
# ---------------------------------------------------------------------------


class _FakePrediction:
    def __init__(self, labels, confidences=None):
        self.predicted_labels = labels
        self.predicted_confidences = confidences or {}


class _FakeClassifier:
    def __init__(self, prediction=None, raises: bool = False):
        self._prediction = prediction
        self._raises = raises

    def predict_one(self, text: str):
        if self._raises:
            raise RuntimeError("boom")
        return self._prediction


class TestClassifyEmailSlm:
    def test_valid_label_returns_mapping(self):
        clf = _FakeClassifier(
            _FakePrediction([CATEGORY_URGENT], {CATEGORY_URGENT: 0.87})
        )
        out = classify_email_slm(clf, subject="s", sender="a@b.c", body="b")
        assert out == {
            "category": CATEGORY_URGENT,
            "confidence": 0.87,
            "source": "slm",
        }

    def test_empty_labels_returns_none(self):
        clf = _FakeClassifier(_FakePrediction([]))
        assert classify_email_slm(clf, subject="s", sender="a@b.c", body="b") is None

    def test_out_of_taxonomy_label_returns_none(self):
        clf = _FakeClassifier(
            _FakePrediction(["NOT_A_CATEGORY"], {"NOT_A_CATEGORY": 1.0})
        )
        assert classify_email_slm(clf, subject="s", sender="a@b.c", body="b") is None

    def test_prediction_error_returns_none(self):
        clf = _FakeClassifier(raises=True)
        assert classify_email_slm(clf, subject="s", sender="a@b.c", body="b") is None

    def test_none_classifier_returns_none(self):
        assert classify_email_slm(None, subject="s", sender="a@b.c", body="b") is None
