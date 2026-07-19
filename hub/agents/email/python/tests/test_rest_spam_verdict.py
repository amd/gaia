# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""REST triage must honor the LLM's ``is_spam`` verdict when the heuristic
abstains (#2124).

The heuristic prefilter deliberately returns ``spam_confident=False`` for
content-based spam (it only commits ``is_spam`` from a narrow mechanical
sender signal), leaving the judgment to the LLM. ``POST /v1/email/triage``
used to discard that LLM verdict and always echo the heuristic's ``False``,
so blatant spam came back ``is_spam=false`` and the anti-spam draft guard
never fired. These tests pin the REST response shape for both branches and
lock REST/agent-loop agreement on a known-spam fixture.
"""

import json
import types


def _classify_chat(*, category, is_spam, summary="A short summary."):
    """Chat stub: classify calls return the given JSON verdict, everything
    else returns a plain-text summary (matches classify_email_llm's prompt,
    which begins with 'Classify this email.')."""

    class _FakeChat:
        def send_messages(self, messages, system_prompt="", **kwargs):
            resp = types.SimpleNamespace()
            content = messages[0].get("content", "") if messages else ""
            if "Classify" in content:
                resp.text = json.dumps(
                    {
                        "category": category,
                        "is_spam": is_spam,
                        "confidence": 1.0,
                        "reasoning": "test",
                    }
                )
            else:
                resp.text = summary
            return resp

    return _FakeChat()


def _single_request(*, subject, sender_email, body, message_id="msg-001"):
    from gaia_agent_email.contract import (
        EmailAddress,
        EmailMessage,
        EmailTriageRequest,
        SingleEmailInput,
    )

    msg = EmailMessage(
        message_id=message_id,
        subject=subject,
        from_=EmailAddress(email=sender_email),
        body=body,
    )
    payload = SingleEmailInput(
        message=msg,
        principal=EmailAddress(email="user@example.com"),
    )
    return EmailTriageRequest(payload=payload)


# A blatant advance-fee scam whose sender/subject match no mechanical
# heuristic branch -> classify_category_heuristic abstains (confident=False,
# spam_confident=False), so the LLM verdict must decide is_spam.
_SCAM_SUBJECT = "You have won the lottery"
_SCAM_SENDER = "agent@lotto-prize.example"
_SCAM_BODY = (
    "Dear Winner, to claim your prize send your bank account number and a "
    "processing fee of $500 to our agent immediately."
)


def test_heuristic_abstains_uses_llm_spam_verdict():
    """Heuristic abstains -> REST returns the LLM's is_spam and drops the
    draft scaffold (the anti-spam guard fires)."""
    from gaia_agent_email.api_routes import EmailTriageService

    request = _single_request(
        subject=_SCAM_SUBJECT, sender_email=_SCAM_SENDER, body=_SCAM_BODY
    )
    chat = _classify_chat(category="PROMOTIONAL", is_spam=True)

    result = EmailTriageService().triage_request(request, chat=chat).result

    assert result.is_spam is True
    # Anti-spam guard: no reply scaffold for LLM-detected spam.
    assert result.draft is None


def test_heuristic_abstains_llm_not_spam_stays_false():
    """Heuristic abstains, LLM says not-spam -> REST returns is_spam=False and
    the draft scaffold is produced normally."""
    from gaia_agent_email.api_routes import EmailTriageService

    request = _single_request(
        subject="Can you review the Q3 deck?",
        sender_email="colleague@example.com",
        body="I'd love your feedback on the attached deck before Friday.",
    )
    chat = _classify_chat(category="NEEDS_RESPONSE", is_spam=False)

    result = EmailTriageService().triage_request(request, chat=chat).result

    assert result.is_spam is False
    assert result.draft is not None


def test_heuristic_confident_spam_verdict_wins():
    """Mechanical high-confidence path (spam_confident=True): the heuristic's
    verdict stands even if the LLM would disagree -- no behavior change."""
    from gaia_agent_email.api_routes import EmailTriageService

    # A promo subject keyword makes the category confident=PROMOTIONAL and the
    # anonymous 'contact.NNNN@' local-part is the mechanical spam signal, so
    # _spam_fields commits is_spam=True with spam_confident=True.
    request = _single_request(
        subject="Special offer just for you",
        sender_email="contact.1234@promo.example",
        body="Act now on this special offer.",
    )
    # LLM disagrees (is_spam=False) but must not be consulted for is_spam.
    chat = _classify_chat(category="PROMOTIONAL", is_spam=False)

    result = EmailTriageService().triage_request(request, chat=chat).result

    assert result.is_spam is True
    assert result.draft is None


def _fake_gmail(message):
    class _FakeGmail:
        def list_messages(self, label_ids=None, max_results=25):
            return {"messages": [{"id": message["id"]}]}

        def get_message(self, message_id):
            return message

    return _FakeGmail()


def test_rest_and_agent_loop_agree_on_known_spam():
    """Regression: REST and the agent-loop path must reach the same is_spam
    verdict on the same known-spam message, given the same LLM answer."""
    from gaia_agent_email.api_routes import EmailTriageService
    from gaia_agent_email.tools.read_tools import triage_inbox_impl

    # Agent-loop path: a classifier that returns the LLM verdict directly.
    def _classifier(*, subject, sender, body, message_id=""):
        return {
            "category": "PROMOTIONAL",
            "is_spam": True,
            "confidence": 1.0,
            "reasoning": "test",
        }

    gmail_message = {
        "id": "spam-001",
        "threadId": "spam-001",
        "snippet": _SCAM_BODY,
        "labelIds": [],
        "payload": {
            "headers": [
                {"name": "Subject", "value": _SCAM_SUBJECT},
                {"name": "From", "value": _SCAM_SENDER},
            ]
        },
    }
    loop_result = triage_inbox_impl(_fake_gmail(gmail_message), classifier=_classifier)
    loop_is_spam = loop_result["results"][0]["is_spam"]

    # REST path: same message, same LLM verdict via the chat stub.
    request = _single_request(
        subject=_SCAM_SUBJECT, sender_email=_SCAM_SENDER, body=_SCAM_BODY
    )
    rest_is_spam = (
        EmailTriageService()
        .triage_request(
            request, chat=_classify_chat(category="PROMOTIONAL", is_spam=True)
        )
        .result.is_spam
    )

    assert loop_is_spam is True
    assert rest_is_spam is True
    assert loop_is_spam == rest_is_spam
