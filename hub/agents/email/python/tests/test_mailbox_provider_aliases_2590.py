# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Mailbox provider vocabulary normalization (#2590 real-world blocker).

The bug: ``setup_mailbox_access(provider="outlook")`` rejected with "I don't
support 'outlook'. I can connect Gmail or Outlook." — a rejection that names
the exact word it just rejected. A live model hears "Outlook" (this agent's
OWN copy uses that word everywhere, via ``provider_label``) and has no reason
to guess the internal id ``"microsoft"`` instead; the tool's docstring didn't
even tell it to. Invisible to unit tests that pass canonical ids directly —
only a live model, using the vocabulary the product itself teaches it,
produces this.
"""

from __future__ import annotations

import json

import pytest
from gaia_agent_email import mailbox_state as ms
from gaia_agent_email.tools import onboarding_tools as ob
from onboarding_fakes import FakeAgent as _FakeAgent

# ---------------------------------------------------------------------------
# mailbox_state.resolve_provider — pure normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "alias,expected",
    [
        # Canonical ids still resolve.
        ("google", "google"),
        ("microsoft", "microsoft"),
        # Microsoft's real-world vocabulary — personal connector.
        ("outlook", "microsoft"),
        ("Outlook", "microsoft"),
        ("  outlook  ", "microsoft"),
        ("OUTLOOK", "microsoft"),
        ("outlook.com", "microsoft"),
        ("hotmail", "microsoft"),
        ("live", "microsoft"),
        # Microsoft's real-world vocabulary — work/school connector (#2629).
        ("office365", "microsoft_work"),
        ("o365", "microsoft_work"),
        ("microsoft 365", "microsoft_work"),
        ("entra", "microsoft_work"),
        ("exchange", "microsoft_work"),
        # Google's real-world vocabulary.
        ("gmail", "google"),
        ("Gmail", "google"),
        ("gmail.com", "google"),
        ("googlemail", "google"),
        ("google workspace", "google"),
        ("gsuite", "google"),
    ],
)
def test_every_alias_resolves_to_its_canonical_provider(alias, expected):
    assert ms.resolve_provider(alias) == expected


@pytest.mark.parametrize("bogus", ["yahoo", "protonmail", "", "  ", "icloud"])
def test_unknown_values_are_still_rejected(bogus):
    assert ms.resolve_provider(bogus) is None


def test_provider_label_of_every_alias_target_is_itself_a_resolvable_alias():
    """Regression test for the exact self-contradiction bug: whatever
    provider_label() would show the user for a canonical id must ALSO
    resolve back through resolve_provider — otherwise a message built from
    provider_label() could show a word that gets rejected as input."""
    for provider in ms.PROVIDERS:
        label = ms.provider_label(provider)
        assert ms.resolve_provider(label) == provider, (
            f"provider_label({provider!r}) == {label!r}, but "
            f"resolve_provider({label!r}) != {provider!r} — the label GAIA "
            "shows the user would be rejected if echoed back as input."
        )


# ---------------------------------------------------------------------------
# onboarding_tools._normalize_provider_arg — what the real tool calls first
# ---------------------------------------------------------------------------
#
# The ``setup_mailbox_access`` tool itself is a closure registered per-agent
# instance (``_register_onboarding_tools``), not a module attribute, so it
# cannot be invoked directly in a unit test. ``_normalize_provider_arg`` is
# the extracted, directly-testable piece that closure calls FIRST — the
# exact code path the live-model repro hit.


def test_outlook_alias_resolves_to_the_canonical_provider_id():
    """The live-model repro, exactly as it happened: the model passes the
    word the product's OWN copy taught it ('Outlook'), not the internal id."""
    wanted, error = ob._normalize_provider_arg("outlook")
    assert error is None
    assert wanted == "microsoft"


def test_gmail_alias_resolves_to_the_canonical_provider_id():
    wanted, error = ob._normalize_provider_arg("Gmail")
    assert error is None
    assert wanted == "google"


def test_unrecognized_provider_is_still_rejected():
    wanted, error = ob._normalize_provider_arg("yahoo")
    assert wanted == ""
    assert error is not None
    assert "yahoo" in error


def test_rejection_message_never_names_a_string_it_would_itself_reject():
    """The regression test for the self-contradiction bug: the message must
    not claim to support (or list as an option) any word that, if passed
    right back in as ``provider``, would ALSO be rejected."""
    _wanted, error = ob._normalize_provider_arg("yahoo")

    assert error is not None
    for provider in ms.PROVIDERS:
        label = ms.provider_label(provider)
        if label in error:
            assert ms.resolve_provider(label) == provider, (
                f"rejection message contains {label!r}, which does not "
                "resolve back to the provider it names — the exact "
                "self-contradiction this test guards against"
            )


def test_omitted_provider_still_lets_the_flow_pick():
    """No behavior change for the empty-string case — 'let the flow pick'."""
    wanted, error = ob._normalize_provider_arg("")
    assert error is None
    assert wanted == ""


def test_whitespace_only_provider_still_lets_the_flow_pick():
    wanted, error = ob._normalize_provider_arg("   ")
    assert error is None
    assert wanted == ""


# ---------------------------------------------------------------------------
# End-to-end through _setup_mailbox_access — the alias actually drives setup
# ---------------------------------------------------------------------------


def test_outlook_alias_reaches_setup_flow_targeting_microsoft(monkeypatch):
    """Confirms the resolved id, not the raw alias, is what reaches the
    scripted flow — a alias that resolved but wasn't actually PASSED THROUGH
    would still break the walkthrough."""
    calls = []

    def fake_setup_flow(agent, wanted):
        calls.append(wanted)
        return json.dumps({"ok": True, "data": {"changed": False}})

    monkeypatch.setattr(ob, "_setup_flow", fake_setup_flow)
    wanted, error = ob._normalize_provider_arg("outlook")
    assert error is None

    ob._setup_mailbox_access(_FakeAgent(), wanted)

    assert calls == ["microsoft"]
