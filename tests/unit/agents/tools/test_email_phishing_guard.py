# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The flagship's own phishing safeguard (#4150).

These tests run on the flagship path — ``EmailToolsMixin`` — with no
``importorskip`` on the retired hub email agent. The safeguard the retired
agent carried (body fencing, a suspicion verdict with a stated reason, and a
do-not-restate rule) was lost when triage moved here, and both guard tests
that would have caught it were gated behind an import that no longer resolves
on this path.

The guard has to live in the tool result, because the turn that produced the
defect never loaded a skill. The ``inbox-triage`` check at the bottom covers
the second layer, not the first.
"""

import json

import httpx
import pytest

from gaia.agents.tools._email.graph import OutlookReadBackend
from gaia.agents.tools._email.phishing import (
    UNTRUSTED_BODY_CLOSE,
    UNTRUSTED_BODY_OPEN,
    assess_message,
)
from gaia.agents.tools.email_tools import EmailToolsMixin


def make_backend(handler):
    """An Outlook backend whose HTTP goes to `handler`, with a fixed token."""
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OutlookReadBackend(lambda: "test-token", http_client=client)


# The lure: what the agent promoted to "Urgent & Action Items" and then
# restated as its own advice.
LURE = {
    "id": "lure-1",
    "conversationId": "conv-lure",
    "subject": "Your account has been suspended - verify now to regain access",
    "from": {
        "emailAddress": {
            "name": "Account Security",
            "address": "security@account-verify-microsoft.com",
        }
    },
    "toRecipients": [{"emailAddress": {"address": "me@example.com"}}],
    "ccRecipients": [],
    "receivedDateTime": "2026-09-20T07:00:00Z",
    "isRead": False,
    "flag": {"flagStatus": "notFlagged"},
    "bodyPreview": "Click here to verify your account within 24 hours.",
    "body": {
        "contentType": "text",
        "content": (
            "Your account has been suspended. Click the link below to verify "
            "your account and restore access."
        ),
    },
}

# A real colleague asking a real question — the precision case. Nothing here
# may be flagged, or the flag stops meaning anything.
GENUINE = {
    "id": "real-1",
    "conversationId": "conv-real",
    "subject": "Q3 numbers",
    "from": {"emailAddress": {"name": "Dana Ruiz", "address": "dana@example.com"}},
    "toRecipients": [{"emailAddress": {"address": "me@example.com"}}],
    "ccRecipients": [],
    "receivedDateTime": "2026-09-20T08:15:00Z",
    "isRead": False,
    "flag": {"flagStatus": "notFlagged"},
    "bodyPreview": "Can you confirm the Q3 figures before Friday?",
    "body": {"contentType": "text", "content": "Can you confirm the Q3 figures?"},
}


class _Harness(EmailToolsMixin):
    def __init__(self, backend):
        self._email_backend = backend
        self.tools = {}

    def _tool(self, name):
        from gaia.agents.base.tools import _TOOL_REGISTRY

        return _TOOL_REGISTRY[name]["function"]


@pytest.fixture
def harness():
    def build(messages):
        def handler(request):
            if "/messages/" in request.url.path:
                return httpx.Response(200, json=messages[0])
            return httpx.Response(200, json={"value": messages})

        h = _Harness(make_backend(handler))
        h.register_email_tools()
        return h

    return build


# --------------------------------------------------------------------------
# the detector
# --------------------------------------------------------------------------


def test_lure_is_suspicious_and_says_why():
    suspicious, reasons = assess_message(
        subject=LURE["subject"],
        sender=LURE["from"]["emailAddress"]["address"],
        body=LURE["body"]["content"],
    )
    assert suspicious is True
    # #4072: never flagged with an empty rationale.
    assert reasons and all(r.strip() for r in reasons)


def test_ordinary_mail_is_not_flagged():
    suspicious, reasons = assess_message(
        subject=GENUINE["subject"],
        sender=GENUINE["from"]["emailAddress"]["address"],
        body=GENUINE["body"]["content"],
    )
    assert suspicious is False
    assert reasons == []


@pytest.mark.parametrize(
    "subject,sender,body",
    [
        ("Urgent action required - click to restore access", "x@example.com", ""),
        ("Hello", "billing@paypal-secure.com", "Please confirm."),
        (
            "Notice",
            "x@example.com",
            "Verify your account to move your banking details.",
        ),
    ],
)
def test_every_channel_states_a_reason(subject, sender, body):
    suspicious, reasons = assess_message(subject=subject, sender=sender, body=body)
    assert suspicious is True
    assert reasons and all(r.strip() for r in reasons)


@pytest.mark.parametrize(
    "sender",
    [
        "Security <secur1ty@paypa1-support.com>",
        "PayPal Service <service@paypa1-secure.com>",
        "Microsoft <admin@micros0ft-verify.com>",
    ],
)
def test_homoglyph_senders_are_caught_by_the_domain(sender):
    """Digit-for-letter brands were the lures the agent left unmentioned."""
    suspicious, reasons = assess_message(
        subject="Action needed", sender=sender, body=""
    )
    assert suspicious is True
    assert reasons


@pytest.mark.parametrize(
    "subject,sender,body",
    [
        (
            "Security alert",
            "Google <no-reply@accounts.google.com>",
            "A new sign-in on Windows. Check activity or secure your account.",
        ),
        (
            "Security alert",
            "no-reply@accounts.google.com",
            "If this was not you, change your password. Review your activity.",
        ),
        ("Your receipt", "billing@stripe.com", "Your invoice is attached."),
    ],
)
def test_a_real_security_alert_is_not_flagged(subject, sender, body):
    """Google's own alert reads exactly like a lure and must survive the guard.

    A rule that catches the homoglyphs by matching "security alert" would fail
    here, and a false positive on a genuine alert is the worse error.
    """
    suspicious, _ = assess_message(subject=subject, sender=sender, body=body)
    assert suspicious is False


# --------------------------------------------------------------------------
# the tool surface — what the model reads with NO skill loaded
#
# The turn that produced #4150 never called `load_skill`: the model read
# `list_inbox` output and promoted the lure straight from it. So the guard has
# to hold here, in the tool result itself. `_Harness` is the mixin alone — no
# agent, no skill loader, no SKILL.md — which is exactly that path.
# --------------------------------------------------------------------------


def test_the_guard_holds_with_no_skill_loaded(harness):
    """The whole defect, reproduced at the layer it actually happened on."""
    h = harness([LURE, GENUINE])
    assert not hasattr(h, "load_skill")

    out = json.loads(h._tool("list_inbox")())
    lure = next(m for m in out["messages"] if m["id"] == "lure-1")
    assert lure["suspicious"] is True
    assert lure["suspicious_reasons"]
    assert out["suspicious_guidance"]


def test_listing_flags_the_lure_and_leaves_genuine_mail_alone(harness):
    out = json.loads(harness([LURE, GENUINE])._tool("list_inbox")())
    by_id = {m["id"]: m for m in out["messages"]}
    assert by_id["lure-1"]["suspicious"] is True
    assert by_id["lure-1"]["suspicious_reasons"]
    assert "suspicious" not in by_id["real-1"]


def test_listing_forbids_the_action_bucket_and_the_restated_ask(harness):
    """The two behaviours #4150 reported: wrong bucket, and repeated advice."""
    out = json.loads(harness([LURE, GENUINE])._tool("list_inbox")())
    guidance = out["suspicious_guidance"].lower()
    assert out["suspicious_count"] == 1
    assert "urgent" in guidance and "action" in guidance
    assert "never repeat" in guidance or "do not repeat" in guidance


def test_clean_listing_carries_no_guidance_noise(harness):
    out = json.loads(harness([GENUINE])._tool("list_inbox")())
    assert out["suspicious_count"] == 0
    assert "suspicious_guidance" not in out


def test_search_results_carry_the_same_verdict(harness):
    out = json.loads(harness([LURE])._tool("search_email")(query="account"))
    assert out["messages"][0]["suspicious"] is True
    assert out["suspicious_guidance"]


def test_read_email_fences_the_body_and_flags_it(harness):
    out = json.loads(harness([LURE])._tool("read_email")(message_id="lure-1"))
    message = out["message"]
    assert message["body"].startswith(UNTRUSTED_BODY_OPEN)
    assert message["body"].rstrip().endswith(UNTRUSTED_BODY_CLOSE)
    assert message["suspicious"] is True
    assert message["suspicious_reasons"]
    assert out["suspicious_guidance"]


def test_every_body_is_fenced_not_just_a_suspicious_one(harness):
    """Fencing is what makes body text data; it cannot depend on the verdict."""
    out = json.loads(harness([GENUINE])._tool("read_email")(message_id="real-1"))
    assert out["message"]["body"].startswith(UNTRUSTED_BODY_OPEN)
    assert "suspicious" not in out["message"]


# --------------------------------------------------------------------------
# the skill — the other half of the flagship's triage path
# --------------------------------------------------------------------------


def test_triage_skill_states_the_suspicious_rule():
    from pathlib import Path

    skill = (
        Path(__file__).resolve().parents[4] / "hub/skills/inbox-triage/SKILL.md"
    ).read_text(encoding="utf-8")
    assert "suspicious" in skill.lower()
    body = skill.lower()
    assert "urgent" in body and "never repeat" in body
