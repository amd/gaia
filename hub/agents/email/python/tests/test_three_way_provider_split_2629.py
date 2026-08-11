# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Three-way mailbox provider split — Gmail, personal Outlook, work
Microsoft 365 (#2629).

The connectors framework already knows about a third connector id,
``microsoft_work`` (work Microsoft 365 / Entra, distinct from personal
``microsoft`` / Outlook.com — landed in #2628). The email agent, however,
still hardcodes a two-provider world everywhere: the id->backend mapping, the
alias table, the required-scopes lookup, the fan-out merge, and
``REQUIRED_CONNECTORS``. Connecting a work mailbox today does not get
ignored — it errors, or (worse, per the plan's Decision 0) would silently
read the WRONG mailbox, because the Graph token resolvers have the personal
connector id ``"microsoft"`` hardcoded into their closures regardless of
which Microsoft connector is actually being built for.

This module is the regression suite for the three-way split:

- AC0 (highest priority): the Graph token resolvers must be parameterized by
  connector id, or a connected work mailbox silently returns personal mail.
- AC1: one id->backend-family mapping (``mailbox_state.backend_family``).
- AC2/AC2b: three providers, three labels, a remapped (breaking, intended)
  alias table, and the label<->alias round-trip invariant.
- AC3: both Microsoft connector ids share the same Graph mail scopes.
- AC4/AC4b: the fan-out merge tags items by connector id (not by backend
  family) and isolates a work-mailbox failure from the other two.
- AC5: ``REQUIRED_CONNECTORS`` actually grows a third entry — the single
  point that would make the whole feature silently do nothing.
- AC11: the cross-connector error, when only the personal Outlook is
  connected but the work mailbox was requested, names both connectors and
  the exact reconnect command.

Every case here is expected to fail (or fail to import) against the
pre-#2629 two-provider code — that is the point of writing them first.

Hermetic: no network, no live mail, no Lemonade. Fakes only.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email import mailbox_state as ms  # noqa: E402
from gaia_agent_email import outlook_backend  # noqa: E402
from gaia_agent_email import outlook_calendar_backend  # noqa: E402
from gaia_agent_email import forwarded_credentials  # noqa: E402
from gaia_agent_email.outlook_scopes import (  # noqa: E402
    OUTLOOK_ALL_SCOPES,
    OUTLOOK_MAIL_SCOPES,
)
from gaia_agent_email.tools.read_tools import merge_pre_scan_backends  # noqa: E402
from gaia_agent_email.tools.triage_heuristics import CATEGORY_URGENT  # noqa: E402

from gaia.connectors.errors import ConnectorsError  # noqa: E402

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _msg(msg_id: str, subject: str = "Hello") -> Dict[str, Any]:
    return {
        "id": msg_id,
        "threadId": msg_id,
        "labelIds": ["INBOX"],
        "snippet": subject,
        "internalDate": "1750000000000",
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": "alice@example.com"},
                {"name": "To", "value": "user@example.com"},
                {"name": "Date", "value": "Mon, 1 Jan 2026 00:00:00 +0000"},
            ],
            "body": {"data": _b64url("body"), "size": 4},
        },
        "sizeEstimate": 4,
    }


def _slm_all_urgent(*, subject, sender, body, message_id=""):  # noqa: ARG001
    """Force every message into a confident URGENT verdict so each provider's
    single message lands in a predictable, inspectable bucket."""
    return {"category": CATEGORY_URGENT, "confidence": 0.9, "source": "slm"}


class _BrokenBackend:
    """Backend whose every read raises ``ConnectorsError`` — a stale token."""

    def list_messages(self, **_kwargs: Any) -> Dict[str, Any]:
        raise ConnectorsError(
            "Microsoft 365 token refresh failed (invalid_request). Reconnect "
            "the work account in Settings -> Connectors."
        )

    def get_message(self, message_id: str) -> Dict[str, Any]:  # pragma: no cover
        raise ConnectorsError("Microsoft 365 token refresh failed (invalid_request).")


# ---------------------------------------------------------------------------
# AC0 (HIGHEST PRIORITY) — per-connector token isolation (Decision 0)
# ---------------------------------------------------------------------------
#
# The pre-#2629 resolvers are zero-argument closures with the literal
# "microsoft" baked into get_credential_sync(...). The moment a work mailbox
# is routed through the same closure it would fetch the PERSONAL account's
# token and silently return personal mail tagged "microsoft_work" — the
# single worst failure mode in the plan. These tests assert the resolver
# forwards whichever connector id it is asked to build for.


def test_outlook_mail_token_resolver_forwards_connector_id_for_microsoft_work(
    monkeypatch,
):
    calls = []

    def fake_get_credential_sync(*args, **kwargs):
        connector_id = args[0] if args else kwargs.get("connector_id")
        calls.append(connector_id)
        return {"access_token": f"tok-{connector_id}"}

    monkeypatch.setattr(outlook_backend, "get_credential_sync", fake_get_credential_sync)
    monkeypatch.setattr(forwarded_credentials, "is_forwarding_enabled", lambda: False)

    token = outlook_backend._get_outlook_token("microsoft_work")
    assert token == "tok-microsoft_work"
    assert calls == ["microsoft_work"]


def test_outlook_mail_token_resolver_still_builds_personal_by_default(monkeypatch):
    calls = []

    def fake_get_credential_sync(*args, **kwargs):
        connector_id = args[0] if args else kwargs.get("connector_id")
        calls.append(connector_id)
        return {"access_token": f"tok-{connector_id}"}

    monkeypatch.setattr(outlook_backend, "get_credential_sync", fake_get_credential_sync)
    monkeypatch.setattr(forwarded_credentials, "is_forwarding_enabled", lambda: False)

    token = outlook_backend._get_outlook_token("microsoft")
    assert token == "tok-microsoft"
    assert calls == ["microsoft"]


def test_outlook_calendar_token_resolver_forwards_connector_id_for_microsoft_work(
    monkeypatch,
):
    calls = []

    def fake_get_credential_sync(*args, **kwargs):
        connector_id = args[0] if args else kwargs.get("connector_id")
        calls.append(connector_id)
        return {"access_token": f"tok-{connector_id}"}

    monkeypatch.setattr(
        outlook_calendar_backend, "get_credential_sync", fake_get_credential_sync
    )
    monkeypatch.setattr(forwarded_credentials, "is_forwarding_enabled", lambda: False)

    token = outlook_calendar_backend._get_outlook_calendar_token("microsoft_work")
    assert token == "tok-microsoft_work"
    assert calls == ["microsoft_work"]


def test_outlook_calendar_token_resolver_still_builds_personal_by_default(monkeypatch):
    calls = []

    def fake_get_credential_sync(*args, **kwargs):
        connector_id = args[0] if args else kwargs.get("connector_id")
        calls.append(connector_id)
        return {"access_token": f"tok-{connector_id}"}

    monkeypatch.setattr(
        outlook_calendar_backend, "get_credential_sync", fake_get_credential_sync
    )
    monkeypatch.setattr(forwarded_credentials, "is_forwarding_enabled", lambda: False)

    token = outlook_calendar_backend._get_outlook_calendar_token("microsoft")
    assert token == "tok-microsoft"
    assert calls == ["microsoft"]


# ---------------------------------------------------------------------------
# AC1 — one id -> backend-family mapping
# ---------------------------------------------------------------------------


def test_backend_family_maps_both_microsoft_ids_to_graph():
    assert ms.backend_family("microsoft_work") == "graph"
    assert ms.backend_family("microsoft") == "graph"


def test_backend_family_maps_google_to_gmail():
    assert ms.backend_family("google") == "gmail"


def test_backend_family_of_unknown_provider_raises():
    with pytest.raises(ValueError):
        ms.backend_family("yahoo")


# ---------------------------------------------------------------------------
# AC2 / AC2b — three providers, three labels, remapped alias table, round-trip
# ---------------------------------------------------------------------------


def test_providers_tuple_is_three_way():
    assert ms.PROVIDERS == ("google", "microsoft", "microsoft_work")


def test_microsoft_work_label_is_microsoft_365():
    assert ms.provider_label("microsoft_work") == "Microsoft 365"


@pytest.mark.parametrize(
    "alias,expected",
    [
        # Work vocabulary now resolves to the WORK connector (Decision 1 —
        # this remap is breaking, and intended).
        ("office365", "microsoft_work"),
        ("o365", "microsoft_work"),
        ("m365", "microsoft_work"),
        ("microsoft 365", "microsoft_work"),
        ("entra", "microsoft_work"),
        ("exchange", "microsoft_work"),
        # Personal vocabulary is UNCHANGED — the additive guarantee.
        ("microsoft", "microsoft"),
        ("outlook", "microsoft"),
        ("outlook.com", "microsoft"),
        ("hotmail", "microsoft"),
        ("live", "microsoft"),
    ],
)
def test_alias_resolution_three_way_split(alias, expected):
    assert ms.resolve_provider(alias) == expected


@pytest.mark.parametrize("bogus", ["work", "school"])
def test_work_and_school_are_explicitly_rejected_never_added(bogus):
    """These common English words carry real false-positive risk ("email my
    work colleague") and were deliberately rejected during design — a
    regression here means someone added them anyway."""
    assert ms.resolve_provider(bogus) is None


def test_round_trip_label_to_provider_for_every_provider():
    for provider in ms.PROVIDERS:
        assert ms.resolve_provider(ms.provider_label(provider)) == provider


# ---------------------------------------------------------------------------
# AC3 — both Microsoft ids share the same Graph mail scopes
# ---------------------------------------------------------------------------


def test_microsoft_work_required_scopes_match_personal_microsoft():
    assert ms.required_scopes("microsoft_work") == ms.required_scopes("microsoft")
    assert ms.required_scopes("microsoft_work") == list(OUTLOOK_MAIL_SCOPES)


def test_microsoft_work_requested_scopes_match_personal_microsoft():
    assert ms.requested_scopes("microsoft_work") == ms.requested_scopes("microsoft")
    assert ms.requested_scopes("microsoft_work") == list(OUTLOOK_ALL_SCOPES)


# ---------------------------------------------------------------------------
# AC4 / AC4b — 3-way fan-out + provenance, and failure isolation at 3
# ---------------------------------------------------------------------------


def _backend_with_one_message(user_email: str, msg_id: str) -> FakeGmailBackend:
    backend = FakeGmailBackend(user_email=user_email)
    backend.add_message(_msg(msg_id, subject=f"Hello from {msg_id}"))
    return backend


def test_three_way_fanout_tags_items_with_all_three_connector_ids():
    backends = {
        "google": _backend_with_one_message("g@example.com", "g1"),
        "microsoft": _backend_with_one_message("m@example.com", "m1"),
        "microsoft_work": _backend_with_one_message("w@example.com", "w1"),
    }

    merged = merge_pre_scan_backends(
        backends,
        max_messages=10,
        slm_classifier=_slm_all_urgent,
    )

    mailboxes = {item["mailbox"] for item in merged["urgent"]}
    assert mailboxes == {"google", "microsoft", "microsoft_work"}
    assert "microsoft_work" in mailboxes


def test_three_way_fanout_isolates_a_broken_work_mailbox():
    backends = {
        "google": _backend_with_one_message("g@example.com", "g1"),
        "microsoft": _backend_with_one_message("m@example.com", "m1"),
        "microsoft_work": _BrokenBackend(),
    }

    merged = merge_pre_scan_backends(
        backends,
        max_messages=10,
        slm_classifier=_slm_all_urgent,
    )

    surviving = {item["mailbox"] for item in merged["urgent"]}
    assert surviving == {"google", "microsoft"}
    # Broken mailbox recorded, not silently dropped.
    assert len(merged["mailbox_errors"]) == 1
    assert merged["mailbox_errors"][0]["mailbox"] == "microsoft_work"
    assert "invalid_request" in merged["mailbox_errors"][0]["error"]


# ---------------------------------------------------------------------------
# AC5 — REQUIRED_CONNECTORS actually grows a third entry
# ---------------------------------------------------------------------------


def test_required_connectors_has_three_entries_including_microsoft_work():
    from gaia_agent_email.agent import EmailTriageAgent

    assert len(EmailTriageAgent.REQUIRED_CONNECTORS) == 3
    by_id = {req.connector_id: req for req in EmailTriageAgent.REQUIRED_CONNECTORS}
    assert "microsoft_work" in by_id
    work_requirement = by_id["microsoft_work"]
    assert set(OUTLOOK_MAIL_SCOPES).issubset(set(work_requirement.scopes))


# ---------------------------------------------------------------------------
# AC11 — cross-connector error names the work connector, the id, and the fix
# ---------------------------------------------------------------------------


def test_requesting_microsoft_work_when_only_personal_is_connected_names_the_fix(
    monkeypatch,
):
    from gaia_agent_email import config as config_mod

    monkeypatch.setattr(
        config_mod, "connected_mailbox_providers", lambda: ["microsoft"]
    )

    cfg = config_mod.EmailAgentConfig(mail_provider="microsoft_work")
    with pytest.raises(config_mod.ConfigurationError) as exc_info:
        cfg.resolve_mail_backends()

    message = str(exc_info.value)
    assert "Microsoft 365" in message
    assert "microsoft_work" in message
    assert "gaia connectors connect microsoft_work" in message
    # Names the connected alternative so the user knows what IS available.
    assert "microsoft" in message
