# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""SLM phishing-detection integration tests.

Hermetic (no Lemonade, no network). Two layers:

1. ``triage_inbox_impl`` routing — when a phishing classifier is wired it runs
   first on every message; a usable result is used alone (heuristics are not
   consulted). ``None`` falls back to ``detect_phishing``.
2. ``classify_phishing_slm`` — the binary "True"/"False" label contract and
   fail-safe handling of empty / unexpected labels and prediction errors.
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
from gaia_agent_email.tools.slm_phishing import classify_phishing_slm  # noqa: E402

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

# Subject that trips the deterministic phishing heuristic (keyword pair).
_PHISHING_SUBJECT = "Please verify your account and click the link now"
_BENIGN_SUBJECT = "Lunch tomorrow?"


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _msg(msg_id: str, *, subject: str, body: str = "Body text.") -> Dict[str, Any]:
    return {
        "id": msg_id,
        "threadId": msg_id,
        "labelIds": ["INBOX"],
        "snippet": body[:200],
        "internalDate": "1750000000000",
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": "sender@example.com"},
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


class _RecordingClassifier:
    def __init__(self, result: Optional[bool]) -> None:
        self.calls: List[dict] = []
        self._result = result

    def __call__(self, *, subject, sender, body):
        self.calls.append({"subject": subject})
        return self._result


# ---------------------------------------------------------------------------
# triage_inbox_impl routing
# ---------------------------------------------------------------------------


class TestPhishingRouting:
    def test_slm_true_sets_phishing(self):
        gmail = _backend(_msg("m1", subject=_PHISHING_SUBJECT))
        classifier = _RecordingClassifier(True)

        out = triage_inbox_impl(gmail, slm_phishing_classifier=classifier)

        decision = out["results"][0]
        assert decision["is_phishing"] is True
        assert decision["phishing_source"] == "slm"
        assert len(classifier.calls) == 1

    def test_slm_false_clears_phishing_without_heuristic(self):
        gmail = _backend(_msg("m1", subject=_PHISHING_SUBJECT))
        classifier = _RecordingClassifier(False)

        out = triage_inbox_impl(gmail, slm_phishing_classifier=classifier)

        decision = out["results"][0]
        assert decision["is_phishing"] is False
        assert decision["phishing_source"] == "slm"
        assert len(classifier.calls) == 1

    def test_slm_none_falls_back_to_heuristic(self):
        gmail = _backend(_msg("m1", subject=_PHISHING_SUBJECT))
        classifier = _RecordingClassifier(None)  # "did not work"

        out = triage_inbox_impl(gmail, slm_phishing_classifier=classifier)

        decision = out["results"][0]
        assert decision["is_phishing"] is True  # detect_phishing fallback
        assert "phishing_source" not in decision
        assert len(classifier.calls) == 1

    def test_slm_runs_first_on_benign_and_can_flag(self):
        gmail = _backend(_msg("m1", subject=_BENIGN_SUBJECT))
        classifier = _RecordingClassifier(True)

        out = triage_inbox_impl(gmail, slm_phishing_classifier=classifier)

        decision = out["results"][0]
        assert decision["is_phishing"] is True
        assert decision["phishing_source"] == "slm"
        assert len(classifier.calls) == 1

    def test_no_classifier_uses_heuristic_only(self):
        gmail = _backend(_msg("m1", subject=_PHISHING_SUBJECT))

        out = triage_inbox_impl(gmail)

        decision = out["results"][0]
        assert decision["is_phishing"] is True
        assert "phishing_source" not in decision


# ---------------------------------------------------------------------------
# classify_phishing_slm — binary label + fail-safe contract
# ---------------------------------------------------------------------------


class _FakePrediction:
    def __init__(self, labels):
        self.predicted_labels = labels
        self.predicted_confidences = {}


class _FakeClassifier:
    def __init__(self, prediction=None, raises: bool = False):
        self._prediction = prediction
        self._raises = raises

    def predict_one(self, text: str):
        if self._raises:
            raise RuntimeError("boom")
        return self._prediction


class TestClassifyPhishingSlm:
    def test_label_true_is_true(self):
        clf = _FakeClassifier(_FakePrediction(["True"]))
        assert classify_phishing_slm(clf, subject="s", sender="a@b.c", body="b") is True

    def test_label_false_is_false(self):
        clf = _FakeClassifier(_FakePrediction(["False"]))
        assert classify_phishing_slm(clf, subject="s", sender="a@b.c", body="b") is False

    def test_empty_labels_returns_none(self):
        clf = _FakeClassifier(_FakePrediction([]))
        assert classify_phishing_slm(clf, subject="s", sender="a@b.c", body="b") is None

    def test_unexpected_label_returns_none(self):
        clf = _FakeClassifier(_FakePrediction(["2"]))
        assert classify_phishing_slm(clf, subject="s", sender="a@b.c", body="b") is None

    def test_prediction_error_returns_none(self):
        clf = _FakeClassifier(raises=True)
        assert classify_phishing_slm(clf, subject="s", sender="a@b.c", body="b") is None

    def test_none_classifier_returns_none(self):
        assert classify_phishing_slm(None, subject="s", sender="a@b.c", body="b") is None
