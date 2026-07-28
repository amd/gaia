# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tell apart the ways a mailbox can be unusable (#2469).

"I can't reach your mailbox" is four different problems wearing one error
message, and they have four different fixes — one of which needs no browser at
all. Collapsing them into a single "connect your mailbox" prompt makes the agent
ask for an OAuth round-trip the user may not need, so this module classifies the
state precisely and the onboarding flow says something different for each:

======================== ================================================
state                    what it actually takes to fix
======================== ================================================
``ok``                   nothing
``agent_not_granted``    a local grant write — **no OAuth, no browser**
``connection_missing_scopes``  re-authorise with the missing scopes
``reauth_required``      re-authorise; the stored credentials stopped working
``not_connected``        connect from scratch
``error``                something else entirely — surfaced verbatim
======================== ================================================

The state names are ``AuthRequiredError.Reason`` values wherever one exists, so
the classification and the error the connectors framework would have raised
cannot drift apart.

Checks run cheapest-first and stop at the first failure, so the common healthy
case costs one keyring read plus one token refresh, and the broken cases never
pay for a probe whose answer is already known.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from gaia.logger import get_logger

log = get_logger(__name__)

#: Grant-ledger identity the email agent's mailbox access is recorded under.
AGENT_ID = "installed:email"

#: Mailbox providers this agent can drive, in canonical (display) order.
PROVIDERS = ("google", "microsoft")

#: Human names — the user picked "Gmail", not "google".
PROVIDER_LABELS = {"google": "Gmail", "microsoft": "Outlook"}

STATE_OK = "ok"
STATE_NOT_CONNECTED = "not_connected"
STATE_NOT_GRANTED = "agent_not_granted"
STATE_MISSING_SCOPES = "connection_missing_scopes"
STATE_REAUTH_REQUIRED = "reauth_required"
STATE_ERROR = "error"

#: States the onboarding flow knows how to repair, worst-first. Ordering is the
#: tie-break when several providers are broken in different ways: offer to fix
#: the one that is closest to working rather than an arbitrary first entry.
REPAIRABLE = (
    STATE_NOT_GRANTED,
    STATE_REAUTH_REQUIRED,
    STATE_MISSING_SCOPES,
    STATE_NOT_CONNECTED,
)


def provider_label(provider: str) -> str:
    """Return the human name for *provider* (``google`` → ``Gmail``)."""
    return PROVIDER_LABELS.get(provider, provider)


def required_scopes(provider: str) -> List[str]:
    """The mail scopes this agent needs from *provider*.

    Calendar scopes are deliberately excluded: a user who only wants triage
    should not be forced to hand over their calendar to get it.
    """
    if provider == "google":
        from gaia_agent_email.scopes import GMAIL_SCOPES

        return list(GMAIL_SCOPES)
    if provider == "microsoft":
        from gaia_agent_email.outlook_scopes import OUTLOOK_MAIL_SCOPES

        return list(OUTLOOK_MAIL_SCOPES)
    raise ValueError(
        f"Unknown mailbox provider {provider!r}. Supported: {', '.join(PROVIDERS)}."
    )


def _account_email(raw: Any) -> Optional[str]:
    """Map the store's no-email sentinel to ``None`` so it never shows as text."""
    from gaia.connectors.store import DEFAULT_ACCOUNT

    email = raw or None
    return None if email == DEFAULT_ACCOUNT else email


def inspect_provider(provider: str, *, probe: bool = True) -> Dict[str, Any]:
    """Classify one provider's mailbox access.

    Returns ``{provider, label, state, account_email, scopes, missing_scopes,
    detail}``. ``detail`` is a one-line plain-language explanation for the state
    — what is wrong, in the words the user would use.

    ``probe=False`` skips the live token refresh: everything except
    ``reauth_required`` is still detected, from local state only. The probe is
    the ONLY way to catch credentials that are stored but no longer accepted,
    which is exactly the failure that produces "(credential problem)" halfway
    through a triage — so it is on by default.
    """
    from gaia.connectors.api import get_connection
    from gaia.connectors.grants import check_agent_grant

    label = provider_label(provider)
    scopes = required_scopes(provider)
    result: Dict[str, Any] = {
        "provider": provider,
        "label": label,
        "state": STATE_OK,
        "account_email": None,
        "scopes": [],
        "missing_scopes": [],
        "detail": "",
    }

    try:
        conn = get_connection(provider)
    except Exception as exc:  # noqa: BLE001 — a broken store must stay visible
        log.warning("mailbox_state: get_connection(%s) failed: %s", provider, exc)
        result["state"] = STATE_ERROR
        result["detail"] = f"Could not read the stored {label} connection: {exc}"
        return result

    if not conn:
        result["state"] = STATE_NOT_CONNECTED
        result["detail"] = f"No {label} mailbox is connected yet."
        return result

    result["account_email"] = _account_email(conn.get("account_email"))
    granted = list(conn.get("scopes") or [])
    result["scopes"] = granted

    if conn.get("error") == "configuration":
        # A stored connection whose OAuth client credentials are gone (never
        # entered, rotated, or wiped). The store can't even report its scopes,
        # so every later check would misread it — most damagingly as "missing
        # scopes", which sends the user off to fix the wrong thing.
        result["state"] = STATE_REAUTH_REQUIRED
        result["detail"] = (
            f"The {label} OAuth client credentials are missing, so the saved "
            "sign-in can't be used."
        )
        return result

    missing = [s for s in scopes if s not in set(granted)]
    if missing:
        result["state"] = STATE_MISSING_SCOPES
        result["missing_scopes"] = missing
        result["detail"] = (
            f"The {label} connection is missing {len(missing)} permission"
            f"{'s' if len(missing) > 1 else ''} this agent needs."
        )
        return result

    try:
        has_grant = check_agent_grant(provider, AGENT_ID, scopes)
    except Exception as exc:  # noqa: BLE001 — never fail closed and silent
        log.warning("mailbox_state: grant check for %s failed: %s", provider, exc)
        result["state"] = STATE_ERROR
        result["detail"] = f"Could not read the {label} permission ledger: {exc}"
        return result

    if not has_grant:
        result["state"] = STATE_NOT_GRANTED
        result["missing_scopes"] = scopes
        result["detail"] = (
            f"{label} is connected, but this agent has not been allowed to use "
            "it yet."
        )
        return result

    if not probe:
        result["detail"] = f"{label} looks usable (credentials not re-checked)."
        return result

    probe_state, probe_detail, probe_missing = _probe_credentials(
        provider, scopes, label
    )
    result["state"] = probe_state
    result["detail"] = probe_detail
    if probe_missing:
        result["missing_scopes"] = probe_missing
    return result


def _probe_forwarded(provider: str, scopes: List[str], label: str) -> tuple:
    """Check the DAEMON-forwarded access token instead of refreshing our own.

    Under the daemon the sidecar deliberately holds no long-lived credential —
    the daemon forwards short-lived access tokens in (#2154). So "can I use this
    mailbox right now" is a question about the forwarded store, not the keyring,
    and probing the keyring here would report a mailbox as healthy that this
    process still cannot touch.
    """
    from gaia_agent_email import forwarded_credentials

    try:
        forwarded_credentials.get_forwarded_token(provider, scopes)
    except Exception as exc:  # noqa: BLE001 — ConnectorsError, loudly
        return (
            STATE_REAUTH_REQUIRED,
            f"The daemon has not handed this agent a usable {label} token: {exc}",
            [],
        )
    return STATE_OK, f"{label} is connected and usable.", []


def _probe_credentials(provider: str, scopes: List[str], label: str) -> tuple:
    """Refresh a token to find out whether the stored credentials still work."""
    from gaia_agent_email import forwarded_credentials

    if forwarded_credentials.is_forwarding_enabled():
        return _probe_forwarded(provider, scopes, label)

    from gaia.connectors.errors import (
        AuthRequiredError,
        ConfigurationError,
        ConnectionRevokedError,
    )

    try:
        from gaia.connectors.api import get_access_token_sync

        get_access_token_sync(provider=provider, scopes=scopes, agent_id=AGENT_ID)
    except AuthRequiredError as exc:
        # The framework already classified it; reuse its verdict verbatim rather
        # than re-deriving one that could disagree.
        return exc.reason.value, str(exc), list(exc.missing_scopes or [])
    except ConnectionRevokedError as exc:
        return STATE_REAUTH_REQUIRED, str(exc), []
    except ConfigurationError as exc:
        # Missing/rotated OAuth client credentials — reconnecting is the fix,
        # and the flow will ask for the client id/secret on the way through.
        return STATE_REAUTH_REQUIRED, str(exc), []
    except Exception as exc:  # noqa: BLE001
        # A transport blip is NOT proof the credentials were revoked. Saying so
        # would send a user with a healthy mailbox through a browser re-auth to
        # fix nothing; STATE_ERROR is the flow's "I don't know" and it refuses
        # to guess rather than acting on one.
        log.warning("mailbox_state: token probe for %s failed: %s", provider, exc)
        return (
            STATE_ERROR,
            f"I could not check the {label} credentials: {exc}",
            [],
        )
    return STATE_OK, f"{label} is connected and usable.", []


def survey(*, probe: bool = True) -> List[Dict[str, Any]]:
    """Classify every supported provider, in canonical order."""
    return [inspect_provider(p, probe=probe) for p in PROVIDERS]


def first_usable(states: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return the first provider that is fully usable, or ``None``."""
    for state in states:
        if state["state"] == STATE_OK:
            return state
    return None


def best_repair_target(states: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Pick which broken provider to offer to fix.

    Closest-to-working first (see :data:`REPAIRABLE`): a mailbox that only needs
    a local grant beats one that needs a whole OAuth round-trip, because the
    cheap fix is also the one the user is most likely to say yes to.
    """
    for wanted in REPAIRABLE:
        for state in states:
            if state["state"] == wanted:
                return state
    return None


__all__ = [
    "AGENT_ID",
    "PROVIDERS",
    "PROVIDER_LABELS",
    "REPAIRABLE",
    "STATE_ERROR",
    "STATE_MISSING_SCOPES",
    "STATE_NOT_CONNECTED",
    "STATE_NOT_GRANTED",
    "STATE_OK",
    "STATE_REAUTH_REQUIRED",
    "best_repair_target",
    "first_usable",
    "inspect_provider",
    "provider_label",
    "required_scopes",
    "survey",
]
