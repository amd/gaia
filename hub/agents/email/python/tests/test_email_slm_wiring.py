# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""SLM wiring tests — config to caller, for the seams routing tests can't reach.

``test_email_slm_triage`` / ``test_email_slm_phishing`` inject fake classifiers
straight into ``triage_inbox_impl``, which proves the routing but never proves
anything actually hands them over. Three seams sit outside that:

1. ``EmailTriageAgent.__init__`` builds both classifiers under ``use_slm`` and
   passes each to the right ``triage_inbox_impl`` keyword. The suite's hermetic
   fixture pins classifier construction to ``None``, so a swapped or dropped
   keyword here is invisible everywhere else — the two callables have different
   signatures, so a swap is a ``TypeError`` in production only.
2. ``pre_scan_inbox_impl`` / ``merge_pre_scan_backends`` forward both keywords,
   so the pre-scan and attention surfaces see SLM verdicts too.
3. The REST service's ``_build_result_llm`` consults the phishing SLM under
   ``use_slm`` and falls back to ``detect_phishing`` without it.

Hermetic: fake classifiers, fake mail backend, no Lemonade, no network.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

# parents[0]=tests/, [1]=email/, [2]=python/, [3]=agents/, [4]=hub/, [5]=repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.agent import EmailTriageAgent  # noqa: E402
from gaia_agent_email.api_routes import EmailTriageService  # noqa: E402
from gaia_agent_email.config import EmailAgentConfig  # noqa: E402
from gaia_agent_email.contract import (  # noqa: E402
    EmailAddress,
    EmailMessage,
    EmailTriageRequest,
    SingleEmailInput,
)
from gaia_agent_email.tools import read_tools, slm_phishing, slm_triage  # noqa: E402
from gaia_agent_email.tools.read_tools import (  # noqa: E402
    merge_pre_scan_backends,
    pre_scan_inbox_impl,
)
from gaia_agent_email.tools.triage_heuristics import (  # noqa: E402
    CATEGORY_URGENT,
    detect_phishing,
)

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

_PHISHING_SUBJECT = "Your mailbox will be deactivated — verify your password now"
_PHISHING_BODY = (
    "Confirm your password within 24 hours using the secure link below or "
    "access will be removed."
)


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


def _triage_slm(category: str = CATEGORY_URGENT):
    """Category-SLM callable — signature must match ``make_slm_classifier``'s."""

    def _classifier(*, subject, sender, body, message_id=""):
        return {"category": category, "confidence": 0.9, "source": "slm"}

    return _classifier


def _phishing_slm(verdict: Optional[bool] = True):
    """Phishing-SLM callable — a DIFFERENT signature (no ``message_id``)."""

    def _classifier(*, subject, sender, body):
        return verdict

    return _classifier


def _phishing_slm_by_subject():
    """Flags only the credential-harvest subject, so routing stays observable."""

    def _classifier(*, subject, sender, body):
        return "verify your password" in subject

    return _classifier


class _ScriptedChat:
    """Canned classify/summarize JSON; counts calls so escalations are visible."""

    class _Resp:
        text = (
            '{"category": "FYI", "is_spam": false, "suggested_action": "none", '
            '"summary": "Canned summary.", "action_items": []}'
        )
        stats: Dict[str, Any] = {}
        usage: Dict[str, Any] = {}

    def __init__(self) -> None:
        self.calls = 0

    def send_messages(self, messages, **kwargs):
        self.calls += 1
        return self._Resp()


def _build_agent(tmp_path: Path, *, config: EmailAgentConfig) -> EmailTriageAgent:
    with patch("gaia.agents.base.agent.AgentSDK") as sdk:
        sdk.return_value = MagicMock()
        return EmailTriageAgent(config=config)


def _config(tmp_path: Path, *, use_slm: bool, gmail=None) -> EmailAgentConfig:
    return EmailAgentConfig(
        use_slm=use_slm,
        gmail_backend=gmail if gmail is not None else _backend(),
        db_path=str(tmp_path / f"state-{use_slm}.db"),
        memory_db_path=str(tmp_path / f"memory-{use_slm}.db"),
        silent_mode=True,
    )


@pytest.fixture(autouse=True)
def _no_memory(monkeypatch):
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")


# ---------------------------------------------------------------------------
# 1. Agent construction + keyword wiring
# ---------------------------------------------------------------------------


class TestAgentWiring:
    def test_use_slm_off_builds_nothing(self, tmp_path, monkeypatch):
        built: List[str] = []
        monkeypatch.setattr(
            slm_triage, "make_slm_classifier", lambda cfg: built.append("triage")
        )
        monkeypatch.setattr(
            slm_phishing,
            "make_slm_phishing_classifier",
            lambda cfg: built.append("phishing"),
        )

        agent = _build_agent(tmp_path, config=_config(tmp_path, use_slm=False))

        assert agent._slm_triage_classifier is None
        assert agent._slm_phishing_classifier is None
        assert built == []  # the factories are never even called

    def test_use_slm_on_builds_both(self, tmp_path, monkeypatch):
        triage, phishing = _triage_slm(), _phishing_slm()
        monkeypatch.setattr(slm_triage, "make_slm_classifier", lambda cfg: triage)
        monkeypatch.setattr(
            slm_phishing, "make_slm_phishing_classifier", lambda cfg: phishing
        )

        agent = _build_agent(tmp_path, config=_config(tmp_path, use_slm=True))

        assert agent._slm_triage_classifier is triage
        assert agent._slm_phishing_classifier is phishing

    def test_each_classifier_reaches_its_own_keyword(self, tmp_path, monkeypatch):
        # A swapped pair would raise TypeError only against a real classifier,
        # so assert identity per keyword rather than "both are not None".
        # IMPORTANT label -> spam_confident=True, so an SLM category hit means
        # no LLM call at all and the wiring is the only thing under test.
        triage, phishing = _triage_slm(), _phishing_slm()
        monkeypatch.setattr(slm_triage, "make_slm_classifier", lambda cfg: triage)
        monkeypatch.setattr(
            slm_phishing, "make_slm_phishing_classifier", lambda cfg: phishing
        )
        gmail = _backend(
            _msg("m1", subject="hello there", labels=["INBOX", "IMPORTANT"])
        )
        agent = _build_agent(
            tmp_path, config=_config(tmp_path, use_slm=True, gmail=gmail)
        )
        chat = _ScriptedChat()
        agent.chat = chat

        seen: Dict[str, Any] = {}
        real_impl = read_tools.triage_inbox_impl

        def _spy(backend, **kwargs):
            seen.update(kwargs)
            return real_impl(backend, **kwargs)

        monkeypatch.setattr(read_tools, "triage_inbox_impl", _spy)
        out = agent._triage_all_backends(max_messages=5)

        assert seen["slm_classifier"] is triage
        assert seen["slm_phishing_classifier"] is phishing
        decision = out["results"][0]
        assert decision["category"] == CATEGORY_URGENT
        assert decision["source"] == "slm"
        assert decision["is_phishing"] is True
        assert decision["phishing_source"] == "slm"
        assert chat.calls == 0  # spam-confident, so nothing escalates

    def test_slm_category_hit_still_pays_the_spam_escalation(
        self, tmp_path, monkeypatch
    ):
        # A message no heuristic rule matches is spam_confident=False, and
        # is_spam lives only in the LLM. The SLM resolves the category, but the
        # LLM classify call still runs for the spam verdict — enabling the SLM
        # does not remove that call, it only takes the category decision away
        # from it.
        monkeypatch.setattr(
            slm_triage, "make_slm_classifier", lambda cfg: _triage_slm()
        )
        monkeypatch.setattr(
            slm_phishing,
            "make_slm_phishing_classifier",
            lambda cfg: _phishing_slm(False),
        )
        gmail = _backend(_msg("m1", subject="hello there", labels=["INBOX"]))
        agent = _build_agent(
            tmp_path, config=_config(tmp_path, use_slm=True, gmail=gmail)
        )
        chat = _ScriptedChat()
        agent.chat = chat

        out = agent._triage_all_backends(max_messages=5)

        decision = out["results"][0]
        assert decision["category"] == CATEGORY_URGENT  # SLM decided
        assert decision["source"] == "slm"
        assert chat.calls == 1  # ...and the LLM still ran, for is_spam

    def test_pre_scan_path_gets_both_classifiers(self, tmp_path, monkeypatch):
        triage, phishing = _triage_slm(), _phishing_slm()
        monkeypatch.setattr(slm_triage, "make_slm_classifier", lambda cfg: triage)
        monkeypatch.setattr(
            slm_phishing, "make_slm_phishing_classifier", lambda cfg: phishing
        )
        gmail = _backend(_msg("m1", subject=_PHISHING_SUBJECT, body=_PHISHING_BODY))
        agent = _build_agent(
            tmp_path, config=_config(tmp_path, use_slm=True, gmail=gmail)
        )

        seen: Dict[str, Any] = {}
        real_merge = read_tools.merge_pre_scan_backends

        def _spy(backends, **kwargs):
            seen.update(kwargs)
            return real_merge(backends, **kwargs)

        monkeypatch.setattr(read_tools, "merge_pre_scan_backends", _spy)
        out = agent._pre_scan_all_backends(max_messages=5)

        assert seen["slm_classifier"] is triage
        assert seen["slm_phishing_classifier"] is phishing
        assert "m1" in _phishing_flagged(out)


# ---------------------------------------------------------------------------
# 2. Pre-scan forwarding
# ---------------------------------------------------------------------------


def _phishing_flagged(pre_scan: Dict[str, Any]) -> set:
    """A phishing verdict surfaces as an actionable 'flagged as phishing' item."""
    return {
        item.get("message_id")
        for section in pre_scan.values()
        if isinstance(section, list)
        for item in section
        if isinstance(item, dict) and "flagged as phishing" in (item.get("why") or "")
    }


def _routing(pre_scan: Dict[str, Any]) -> Dict[str, str]:
    return {
        item["message_id"]: name
        for name, section in pre_scan.items()
        if isinstance(section, list)
        for item in section
        if isinstance(item, dict) and item.get("message_id")
    }


class TestPreScanForwarding:
    def test_pre_scan_uses_both_classifiers(self):
        gmail = _backend(
            _msg("m1", subject=_PHISHING_SUBJECT, body=_PHISHING_BODY),
            _msg("m2", subject="hello there"),
        )

        out = pre_scan_inbox_impl(
            gmail,
            max_messages=5,
            slm_classifier=_triage_slm(),
            slm_phishing_classifier=_phishing_slm_by_subject(),
        )

        assert "m1" in _phishing_flagged(out)
        # The SLM's URGENT lands m2 in the urgent bucket, not needs_review.
        assert _routing(out)["m2"] == "urgent"

    def test_pre_scan_without_classifiers_is_unchanged(self):
        gmail = _backend(_msg("m2", subject="hello there"))

        out = pre_scan_inbox_impl(gmail, max_messages=5)

        assert _phishing_flagged(out) == set()
        assert _routing(out)["m2"] == "needs_review"

    def test_merge_preserves_slm_verdicts(self):
        gmail = _backend(_msg("m1", subject=_PHISHING_SUBJECT, body=_PHISHING_BODY))

        out = merge_pre_scan_backends(
            {"google": gmail},
            max_messages=5,
            slm_classifier=_triage_slm(),
            slm_phishing_classifier=_phishing_slm(),
        )

        assert "m1" in _phishing_flagged(out)


# ---------------------------------------------------------------------------
# 3. REST service
# ---------------------------------------------------------------------------


def _rest_request() -> EmailTriageRequest:
    return EmailTriageRequest(
        payload=SingleEmailInput(
            principal=EmailAddress(email="me@example.com"),
            message=EmailMessage(
                message_id="m1",
                from_=EmailAddress(email="alice@example.com"),
                to=[EmailAddress(email="me@example.com")],
                subject="hello there",
                body="Nothing phishy in this body at all.",
            ),
        )
    )


class TestRestServiceWiring:
    def test_phishing_slm_decides_when_use_slm_on(self, monkeypatch):
        monkeypatch.setattr(
            slm_phishing, "get_slm_phishing_classifier", lambda cfg: object()
        )
        monkeypatch.setattr(
            slm_phishing,
            "classify_phishing_slm",
            lambda clf, *, subject, sender, body: True,
        )
        service = EmailTriageService(EmailAgentConfig(use_slm=True))

        result = service.triage_request(_rest_request(), chat=_ScriptedChat()).result

        # The heuristic sees nothing phishing in this message; the SLM does.
        assert result.is_phishing is True

    def test_heuristic_decides_when_use_slm_off(self, monkeypatch):
        def _boom(*args, **kwargs):  # pragma: no cover - must never run
            raise AssertionError("SLM consulted with use_slm=False")

        monkeypatch.setattr(slm_phishing, "get_slm_phishing_classifier", _boom)
        service = EmailTriageService(EmailAgentConfig(use_slm=False))

        result = service.triage_request(_rest_request(), chat=_ScriptedChat()).result

        assert result.is_phishing is False

    def test_slm_miss_falls_back_to_heuristic(self, monkeypatch):
        monkeypatch.setattr(
            slm_phishing, "get_slm_phishing_classifier", lambda cfg: object()
        )
        monkeypatch.setattr(
            slm_phishing,
            "classify_phishing_slm",
            lambda clf, *, subject, sender, body: None,
        )
        service = EmailTriageService(EmailAgentConfig(use_slm=True))
        request = EmailTriageRequest(
            payload=SingleEmailInput(
                principal=EmailAddress(email="me@example.com"),
                message=EmailMessage(
                    message_id="m1",
                    from_=EmailAddress(email="security@account-verify-mail.net"),
                    to=[EmailAddress(email="me@example.com")],
                    subject=_PHISHING_SUBJECT,
                    body=_PHISHING_BODY,
                ),
            )
        )

        result = service.triage_request(request, chat=_ScriptedChat()).result

        # On a miss the verdict is whatever detect_phishing says on this exact
        # input — asserted against the detector itself, not a guess at it.
        assert result.is_phishing is detect_phishing(
            _PHISHING_SUBJECT, "security@account-verify-mail.net", _PHISHING_BODY
        )

    def test_summarize_call_means_usage_is_never_null_on_this_path(self, monkeypatch):
        # Guards the contract wording: an SLM category hit does NOT make REST
        # usage null, because _build_result_llm always summarizes.
        monkeypatch.setattr(
            slm_triage, "get_slm_triage_classifier", lambda cfg: object()
        )
        monkeypatch.setattr(
            slm_triage,
            "classify_email_slm",
            lambda clf, **kwargs: {
                "category": CATEGORY_URGENT,
                "confidence": 0.9,
                "source": "slm",
            },
        )
        service = EmailTriageService(EmailAgentConfig(use_slm=True))
        chat = _ScriptedChat()

        result = service.triage_request(_rest_request(), chat=chat).result

        assert result.category.value == CATEGORY_URGENT  # SLM decided
        assert chat.calls >= 1  # summarize still ran
