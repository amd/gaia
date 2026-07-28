# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Agent-led mailbox onboarding — the agent fixes its own access (#2469).

The failure this replaces: the user asks "triage my inbox", the pre-scan comes
back ``CONNECTOR_ERROR: All connected mailboxes failed … (credential problem)``,
and the agent's best move is to tell someone sitting in a terminal chat to leave
it, run ``gaia connectors connect google --scopes <scopes> --grant-agent
installed:email``, and come back. Every part of that is the product failing.

What happens instead: the agent works out *which* of the four problems it
actually has (see :mod:`gaia_agent_email.mailbox_state`), says something
specific about that one, and offers to fix it right there — asking, through the
mid-run question primitive, only for what it genuinely cannot determine on its
own.

The whole conversation is scripted **here**, in Python, not improvised by the
model. The model's only decision is whether to call ``setup_mailbox_access``;
what gets asked, in what order, and what each answer does is deterministic —
which is what makes it testable and what stops a 4B local model from inventing
a setup step.

The honest limit
----------------
Connecting Google today still needs the user to supply their own OAuth client id
**and** client secret (there is no first-party GAIA client), and the browser flow
is loopback-only — so it needs a browser on this machine. None of that can be
prompted away. What the agent can do is know exactly when it is about to need
them, say so plainly with a link before asking, and do everything else itself.
Ship a first-party OAuth client and both of those questions disappear from this
flow entirely; nothing else here changes.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from gaia_agent_email import mailbox_state as ms
from gaia_agent_email.question import (
    InputUnansweredError,
    InputUnsupportedError,
    Option,
    ask,
)
from gaia_agent_email.tools.envelope import _envelope_err, _envelope_ok

from gaia.agents.base.tools import tool
from gaia.connectors.errors import GrantAfterConnectError
from gaia.logger import get_logger

log = get_logger(__name__)

#: Where a user goes to create the OAuth client they still have to supply.
OAUTH_DOCS_URL = "https://amd-gaia.ai/docs/guides/email"

#: Bound on the browser sign-in wait. Must exceed ``flow._FLOW_TIMEOUT_SECONDS``
#: (120s) so the flow's own timeout is the one that fires, with its own message.
_OAUTH_WAIT_TIMEOUT_SECONDS = 150.0

#: Answer values the scripted questions branch on.
_YES = "yes"
_NO = "no"

_DECLINED = (
    "No problem — I've left everything as it is. Ask me again when you want to "
    "set it up."
)


def _scope_labels(provider: str, scopes: List[str]) -> str:
    """Render scopes as plain language ('Read, modify, and send email')."""
    try:
        if provider == "google":
            from gaia.connectors.providers.google import SCOPE_DESCRIPTIONS
        else:
            from gaia.connectors.providers.microsoft import SCOPE_DESCRIPTIONS
    except Exception as exc:  # noqa: BLE001 — the scopes are still the truth
        # Degrades the question to raw scope URLs, so it must not be invisible.
        log.warning("onboarding: no scope labels for %s: %s", provider, exc)
        SCOPE_DESCRIPTIONS = {}
    labels = [SCOPE_DESCRIPTIONS.get(s, s) for s in scopes]
    return "; ".join(labels) if labels else "the mailbox permissions it needs"


def narrate(agent: Any, message: str) -> None:
    """Narrate a step to the live stream, if the surface has a console."""
    console = getattr(agent, "console", None)
    printer = getattr(console, "print_info", None)
    if callable(printer):
        try:
            printer(message)
        except Exception as exc:  # noqa: BLE001 — narration is never fatal
            log.debug("onboarding: status narration failed: %s", exc)


def _oauth_client_gap(provider: str) -> Optional[str]:
    """Return which OAuth client credential is missing, or ``None`` if fine.

    ``"client_id"`` when no client is configured at all; ``"client_secret"``
    when Google has an id but no secret (its token endpoint rejects refreshes
    without one, so connecting would "succeed" and then 401 later).
    """
    from gaia.connectors.errors import ConfigurationError

    try:
        from gaia.connectors.providers import get as get_provider

        prov = get_provider(provider)
    except (ConfigurationError, KeyError):
        return "client_id"
    except Exception as exc:  # noqa: BLE001
        log.warning("onboarding: could not load the %s provider: %s", provider, exc)
        return "client_id"
    if not getattr(prov, "client_id", ""):
        return "client_id"
    if provider == "google" and not getattr(prov, "client_secret", ""):
        return "client_secret"
    return None


def _needs_client_secret(provider: str) -> bool:
    """Whether *provider*'s token endpoint accepts a client secret at all.

    Microsoft's route here is a public PKCE client — per the identity
    platform docs it must NOT send one (``providers/microsoft.py``) — so
    asking Outlook users for a secret is not just unnecessary, it is asking
    for a credential that Microsoft never issues for this client type.
    """
    return provider == "google"


def _collect_oauth_client(agent: Any, provider: str) -> Dict[str, str]:
    """Ask for the OAuth client credentials, but only the ones actually missing.

    Returns the ``configure()`` config fragment — ``{}`` when nothing is needed.
    """
    gap = _oauth_client_gap(provider)
    if gap is None:
        return {}

    label = ms.provider_label(provider)
    wants_secret = _needs_client_secret(provider)
    narrate(
        agent,
        f"{label} needs an OAuth client before I can sign you in.",
    )

    id_and_secret = "ID and secret" if wants_secret else "ID"
    pronoun = "them" if wants_secret else "it"
    stored_as = "They're" if wants_secret else "It's"
    have_label = "I have them" if wants_secret else "I have it"
    proceed = ask(
        agent,
        (
            f"To connect {label} I need an OAuth client {id_and_secret} that you "
            "create once in your provider console — GAIA does not ship one yet, "
            "so this part I genuinely cannot do for you. The walkthrough is at "
            f"{OAUTH_DOCS_URL}. {stored_as} stored in your OS keychain, never "
            f"sent anywhere but the provider. Do you have {pronoun} to hand?"
        ),
        options=(
            Option(
                _YES,
                have_label,
                f"You'll paste the client {id_and_secret.lower()}. Takes a few "
                "seconds.",
            ),
            Option(
                _NO,
                "Not right now",
                "I'll stop here and change nothing. Come back when you've made one.",
            ),
        ),
        allow_free_text=False,
    )
    if proceed != _YES:
        raise _Declined(
            f"OK — nothing changed. Create a Desktop OAuth client for {label} "
            f"({OAUTH_DOCS_URL}), then ask me again and I'll take it from there."
        )

    config: Dict[str, str] = {}
    if gap == "client_id":
        config["client_id"] = ask(
            agent,
            f"Paste the {label} OAuth client ID "
            "(for Google it ends in .apps.googleusercontent.com).",
            allow_free_text=True,
        )
    if wants_secret:
        config["client_secret"] = ask(
            agent,
            f"Now the {label} OAuth client secret. It goes straight into your "
            "OS keychain.",
            allow_free_text=True,
            sensitive=True,
        )
        if gap == "client_secret":
            # Re-saving requires the id alongside the secret; reuse the stored
            # one.
            from gaia.connectors.providers import get as get_provider

            config["client_id"] = getattr(get_provider(provider), "client_id", "")
    return config


class _Declined(Exception):
    """The user said no. Not an error — a complete, respected answer."""


def _walkthrough_stuck_exc() -> type:
    """Lazily return ``setup_walkthrough.WalkthroughStuck``.

    A plain top-of-file import would be circular: ``setup_walkthrough``
    imports ``connect_scopes``/``narrate``/``OAUTH_DOCS_URL`` from THIS
    module. Deferring the import to call time (used only inside an
    ``except`` clause) breaks the cycle without duplicating the class.
    """
    from gaia_agent_email.tools.setup_walkthrough import WalkthroughStuck

    return WalkthroughStuck


def connect_scopes(provider: str, agent_scopes: List[str]) -> List[str]:
    """The provider's catalog scopes plus the ones this agent needs, deduped.

    Mirrors what ``gaia connectors connect`` requests, so a mailbox connected
    through the agent is not weaker than the same mailbox connected from a
    shell — the divergence that left accounts showing as "default".
    """
    defaults: List[str] = []
    try:
        from gaia.connectors.providers import get as get_provider

        defaults = list(getattr(get_provider(provider), "default_scopes", ()) or ())
    except Exception as exc:  # noqa: BLE001 — mail scopes alone still connect
        log.warning(
            "onboarding: no catalog default_scopes for %s (%s); connecting with "
            "the agent's scopes only — the account email may be unavailable",
            provider,
            exc,
        )
    merged = list(defaults)
    for scope in agent_scopes:
        if scope not in merged:
            merged.append(scope)
    return merged


def _run_oauth(agent: Any, provider: str) -> Dict[str, Any]:
    """Run the browser OAuth flow and grant the result to this agent."""
    from gaia.connectors._loop import run_sync
    from gaia.connectors.grants import grant_agent
    from gaia.connectors.handler import configure

    label = ms.provider_label(provider)
    scopes = ms.required_scopes(provider)

    config: Dict[str, Any] = {
        # Authorize the provider's own identity scopes alongside the mail ones.
        # Without them the token cannot name the account, so every surface shows
        # the mailbox as "default" instead of the address the user just signed
        # in with. The grant below stays narrow — identity is for the
        # connection, not for the agent.
        "scopes": connect_scopes(provider, scopes),
        # Committing the grant inside the same flow is what stops the
        # connected-but-unusable dead end this whole feature exists to remove.
        "grant_agents": {ms.AGENT_ID: scopes},
    }
    config.update(_collect_oauth_client(agent, provider))

    started = run_sync(configure(provider, config))
    auth_url = started.get("authorization_url") or ""
    flow_id = started.get("flow_id")
    if not flow_id:
        raise RuntimeError(
            f"Starting the {label} sign-in did not return a flow to wait on. "
            "Retry, or connect from Settings → Connections in the Agent UI."
        )

    narrate(
        agent,
        f"Opening your browser to sign in to {label}. If it didn't open, use "
        f"this link (valid for 2 minutes): {auth_url}",
    )

    from gaia.connectors.flow import complete_authorization

    # run_sync defaults to 30s, but this coroutine waits on a HUMAN picking an
    # account and reading a consent screen — the flow's own bound is 120s. At
    # the default we would abandon a sign-in that then succeeds, skip the grant
    # below, and report failure on a mailbox that is now connected-but-ungranted
    # — the exact dead end this feature exists to remove.
    state = run_sync(
        complete_authorization(flow_id), timeout=_OAUTH_WAIT_TIMEOUT_SECONDS
    )
    granted = list(state.get("scopes") or scopes)
    # configure() already committed the grant via grant_agents; this is
    # idempotent and covers a provider path that ignores it.
    grant_agent(provider, ms.AGENT_ID, granted)
    return state


def _run_connect(agent: Any, provider: str) -> None:
    """Run whichever sign-in *provider* actually uses.

    Microsoft goes through the guided device-code walkthrough (#2590) — no
    client secret, no browser required. Every other provider keeps the
    existing browser-loopback path.
    """
    if provider == "microsoft":
        _run_microsoft_setup(agent)
    else:
        _run_oauth(agent, provider)


def _run_microsoft_setup(agent: Any) -> Dict[str, Any]:
    """First-time Microsoft connect: walk the guided setup if the OAuth
    client isn't configured yet, then sign in with a device code.

    Reuses ``_oauth_client_gap`` — the SAME check ``_collect_oauth_client``
    uses — so "is a client already configured" has exactly one answer, not
    two that could disagree. A mailbox that already has a client configured
    (e.g. a reconnect) skips straight to sign-in — never re-walked.
    """
    from gaia.connectors._loop import run_sync
    from gaia.connectors.handler import configure
    from gaia.connectors.setup_routes import get_route
    from gaia_agent_email.tools import setup_walkthrough as sw

    gap = _oauth_client_gap("microsoft")
    if gap is not None:
        route = get_route("microsoft")
        if route is None:
            # Defensive — unreachable while setup_routes.ROUTES is exactly
            # {"microsoft": MS_PERSONAL}. A future route removal must still
            # fail as a legible message, never a crash or a silent no-op.
            raise RuntimeError(
                "No guided walkthrough exists for Microsoft yet. Connect from "
                f"Settings → Connections in the Agent UI, or see {OAUTH_DOCS_URL}."
            )
        collected, _trace = sw.run_setup_walkthrough(agent, route)
        run_sync(
            configure(
                "microsoft",
                {"client_id": collected["client_id"], "save_only": True},
            )
        )
    return sw.run_device_oauth(agent, "microsoft")


_CLIENT_FIRST_BLURB = (
    "First you'll paste an OAuth client ID and secret you create once in your "
    "provider console — GAIA does not ship one. Then your browser opens."
)
#: Microsoft's client is public-PKCE (no secret) and this route signs in with
#: a short code, not a browser redirect — reusing ``_CLIENT_FIRST_BLURB``
#: here would repeat the exact secret-mention bug this feature exists to fix.
_CLIENT_FIRST_BLURB_NO_SECRET = (
    "First I'll walk you through creating an OAuth client ID — GAIA does not "
    "ship one, and this route needs no secret. Then you'll sign in with a "
    "short code instead of a browser."
)


def _go_blurb(provider: str, when_client_ready: str) -> str:
    """What saying yes actually does next.

    Promising a browser while the OAuth client is still missing is the same
    reports-ready-then-fails pattern this feature exists to remove: the user
    accepts "opens your browser", and gets asked to paste a client ID instead.
    """
    if _oauth_client_gap(provider) is None:
        return when_client_ready
    if _needs_client_secret(provider):
        return _CLIENT_FIRST_BLURB
    return _CLIENT_FIRST_BLURB_NO_SECRET


def _confirm_repair(agent: Any, state: Dict[str, Any]) -> bool:
    """Ask the state-specific opening question. True when the user says go."""
    label = state["label"]
    account = state.get("account_email")
    who = f" ({account})" if account else ""
    kind = state["state"]
    provider = state["provider"]

    if kind == ms.STATE_NOT_GRANTED:
        question = (
            f"{label}{who} is already connected on this machine — I just haven't "
            "been allowed to use it. I can fix that right now without signing "
            "you in again."
        )
        go = Option(
            _YES,
            f"Let me use {label}",
            "A local permission change only — no browser, no re-sign-in.",
        )
    elif kind == ms.STATE_REAUTH_REQUIRED:
        question = (
            f"Your {label} sign-in{who} has stopped working — the saved "
            "credentials were rejected, which usually means the access was "
            "revoked or expired. I can take you through signing in again."
        )
        go = Option(
            _YES,
            f"Reconnect {label}",
            _go_blurb(provider, "Opens your browser to sign in again. Your mail is untouched."),
        )
    elif kind == ms.STATE_MISSING_SCOPES:
        missing = _scope_labels(state["provider"], state.get("missing_scopes") or [])
        question = (
            f"{label}{who} is connected, but the sign-in doesn't cover "
            f"everything I need: {missing}. Re-authorising adds it."
        )
        go = Option(
            _YES,
            "Re-authorise",
            _go_blurb(provider, "Opens your browser to approve the extra permission."),
        )
    else:  # not_connected
        question = (
            f"I don't have access to a {label} mailbox yet, so there's nothing "
            "for me to read. I can connect one now."
        )
        go = Option(
            _YES,
            f"Connect {label}",
            _go_blurb(provider, "Opens your browser to sign in. Nothing is sent anywhere else."),
        )

    answer = ask(
        agent,
        question,
        options=(
            go,
            Option(_NO, "Not now", "Change nothing. I'll ask again next time."),
        ),
        allow_free_text=False,
    )
    return answer == _YES


def _choose_provider(agent: Any, states: List[Dict[str, Any]]) -> str:
    """Ask which mailbox to set up when nothing is connected at all."""
    answer = ask(
        agent,
        "I don't have a mailbox to work with yet. Which one should I connect?",
        options=(
            Option(
                "google",
                "Gmail",
                "A gmail.com or Google Workspace account.",
            ),
            Option(
                "microsoft",
                "Outlook",
                "An outlook.com or Microsoft 365 account.",
            ),
            Option(_NO, "Neither right now", "Change nothing and carry on."),
        ),
        allow_free_text=False,
    )
    if answer == _NO:
        raise _Declined(_DECLINED)
    return answer


class OnboardingToolsMixin:
    """Registers the mailbox self-service tools.

    State-free at construction: both tools read live connector state per call,
    so a mailbox connected elsewhere (Agent UI, ``gaia connectors``) is seen
    immediately — this flow must stay quiet when nothing is actually wrong.
    """

    def _register_onboarding_tools(self) -> None:
        agent = self

        @tool
        def check_mailbox_access() -> str:
            """Check whether this agent can actually USE a mailbox right now.

            Call this when a mailbox operation fails with a connection,
            credential, permission, or scope error, and before telling the user
            anything about why their mailbox did not work. It distinguishes the
            four different problems that all look like "can't reach your
            mailbox" — each has a different fix, and one of them needs no
            browser at all.

            Read-only: it never changes anything. To actually FIX what it
            reports, call ``setup_mailbox_access``.

            Returns:
                JSON envelope ``{"ok": true, "data": {"usable": bool,
                "providers": [{"provider", "label", "state", "account_email",
                "missing_scopes", "detail"}, ...]}}``. ``state`` is one of
                ``ok`` / ``not_connected`` / ``agent_not_granted`` /
                ``connection_missing_scopes`` / ``reauth_required`` / ``error``.
            """
            try:
                states = ms.survey(probe=True)
            except Exception as exc:  # noqa: BLE001
                log.exception("check_mailbox_access failed")
                return _envelope_err(f"{type(exc).__name__}: {exc}")
            usable = ms.first_usable(states)
            return _envelope_ok(
                {
                    "usable": usable is not None,
                    "usable_provider": usable["provider"] if usable else None,
                    "providers": states,
                }
            )

        @tool
        def setup_mailbox_access(provider: str = "") -> str:
            """Set up or repair mailbox access by ASKING the user, right here.

            Call this instead of telling the user to run a command or open
            Settings. It works out what is actually wrong, asks the user
            whether to fix it (and only for what it cannot determine itself),
            opens the browser when a sign-in is genuinely required, and reports
            what happened. It asks before every change — it never connects or
            grants anything unprompted.

            Do NOT call it speculatively: if a mailbox already works it returns
            immediately without asking anything.

            Args:
                provider: Optional — 'google' or 'microsoft' to target one
                    mailbox. Omit to let the flow pick the one that needs the
                    least work, or ask the user when nothing is connected.

            Returns:
                JSON envelope. On success ``{"ok": true, "data": {"changed":
                bool, "provider", "account_email", "state", "message"}}`` — read
                ``message`` back to the user. On refusal or failure ``{"ok":
                false, "error": "<what to tell the user>"}``.
            """
            wanted = (provider or "").strip().lower()
            if wanted and wanted not in ms.PROVIDERS:
                return _envelope_err(
                    f"I don't support {provider!r}. I can connect "
                    f"{' or '.join(ms.provider_label(p) for p in ms.PROVIDERS)}."
                )
            try:
                return _setup_mailbox_access(agent, wanted)
            except InputUnsupportedError as exc:
                return _envelope_err(
                    f"{exc} Until then, connect the mailbox from Settings → "
                    "Connections in the Agent UI."
                )
            except InputUnansweredError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:  # noqa: BLE001
                # The traceback goes to the log, where the operator can read it.
                # Only the exception TYPE reaches the tool result: this path can
                # be entered with an OAuth client secret in a caller's locals,
                # and str(exc) on a credential-handling failure is exactly where
                # one leaks into model context and then into the answer text.
                log.exception("setup_mailbox_access failed")
                return _envelope_err(
                    "Mailbox setup failed "
                    f"({type(exc).__name__} — see the agent log for details). "
                    "Nothing was changed. You can also connect from Settings → "
                    "Connections in the Agent UI."
                )


def _setup_mailbox_access(agent: Any, wanted: str) -> str:
    """Run the scripted flow and render its outcome as a tool envelope.

    "No" is a complete answer, not an error: a decline comes back ``ok`` with
    ``declined: true`` so the agent reports it as a respected choice rather than
    as a failure it should try to work around.
    """
    try:
        return _setup_flow(agent, wanted)
    except _Declined as exc:
        return _envelope_ok({"changed": False, "declined": True, "message": str(exc)})
    except GrantAfterConnectError as exc:
        # save_connection commits BEFORE the grant does (flow.py), so this is
        # NOT "nothing was changed" — the mailbox connected; only the local
        # permission write failed. .reason is authored/constructed by GAIA
        # (agent id, provider, a local OSError-style message) and never
        # carries a credential, so — unlike the generic catch-all in
        # setup_mailbox_access — showing it is safe.
        label = ms.provider_label(exc.provider)
        return _envelope_ok(
            {
                "changed": True,
                "provider": exc.provider,
                "account_email": None,
                "state": ms.STATE_NOT_GRANTED,
                "message": (
                    f"{label} is connected now, but I couldn't allow myself "
                    f"to use it: {exc.reason} Try again, or grant it manually "
                    "from Settings → Connections in the Agent UI."
                ),
            }
        )
    except _walkthrough_stuck_exc() as exc:
        # A defined, honest handoff (design §3) — not a failure. The
        # exception's own message is the user-facing text (mirrors
        # _Declined), so it is shown directly rather than improvised here.
        return _envelope_ok(
            {"changed": False, "declined": False, "stuck": True, "message": str(exc)}
        )


def _setup_flow(agent: Any, wanted: str) -> str:
    """The scripted conversation. Raises ``_Declined`` when the user says no."""
    states = ms.survey(probe=True)
    by_provider = {s["provider"]: s for s in states}

    if wanted:
        target = by_provider[wanted]
        if target["state"] == ms.STATE_ERROR:
            # An unreadable store is not "not connected". Offering a browser
            # sign-in here walks the user through a flow that cannot persist.
            raise RuntimeError(
                f"I could not read the {target['label']} connection state at "
                f"all: {target['detail']}"
            )
        if target["state"] == ms.STATE_OK:
            return _envelope_ok(
                {
                    "changed": False,
                    "provider": target["provider"],
                    "account_email": target.get("account_email"),
                    "state": ms.STATE_OK,
                    "message": (
                        f"{target['label']} is already connected"
                        + (
                            f" as {target['account_email']}"
                            if target.get("account_email")
                            else ""
                        )
                        + " and I can use it — nothing to set up."
                    ),
                }
            )
    else:
        usable = ms.first_usable(states)
        if usable is not None:
            return _envelope_ok(
                {
                    "changed": False,
                    "provider": usable["provider"],
                    "account_email": usable.get("account_email"),
                    "state": ms.STATE_OK,
                    "message": (
                        f"{usable['label']} is already connected"
                        + (
                            f" as {usable['account_email']}"
                            if usable.get("account_email")
                            else ""
                        )
                        + " and working — nothing to set up."
                    ),
                }
            )
        target = ms.best_repair_target(states)
        if target is None:
            # Every provider is in the `error` state — a broken store, not a
            # missing mailbox. Never paper over it with a connect prompt.
            details = "; ".join(f"{s['label']}: {s['detail']}" for s in states)
            raise RuntimeError(
                f"I could not read the connection state at all ({details})."
            )
        if target["state"] == ms.STATE_NOT_CONNECTED and all(
            s["state"] == ms.STATE_NOT_CONNECTED for s in states
        ):
            # Nothing anywhere: the user picks, rather than being funnelled into
            # whichever provider happens to sort first.
            target = by_provider[_choose_provider(agent, states)]

    if not _confirm_repair(agent, target):
        raise _Declined(_DECLINED)

    provider = target["provider"]
    label = target["label"]

    if target["state"] == ms.STATE_NOT_GRANTED:
        from gaia.connectors.grants import grant_agent

        grant_agent(provider, ms.AGENT_ID, ms.required_scopes(provider))
        narrate(agent, f"Allowed this agent to use {label}.")
    else:
        _run_connect(agent, provider)

    final = ms.inspect_provider(provider, probe=True)
    if final["state"] != ms.STATE_OK:
        from gaia_agent_email import forwarded_credentials

        if forwarded_credentials.is_forwarding_enabled():
            # The connection itself succeeded; only the daemon's hand-over is
            # outstanding. That is a wait, not a failure — say which it is.
            return _handover_pending(provider, label, "")
        # Loud: reporting success on a mailbox that still does not work is how
        # the user ends up back at the same error one question later.
        return _envelope_err(
            f"{label} still isn't usable after that: {final['detail']} "
            "Try again, or connect from Settings → Connections in the Agent UI."
        )

    who = f" as {final['account_email']}" if final.get("account_email") else ""
    return _envelope_ok(
        {
            "changed": True,
            "provider": provider,
            "account_email": final.get("account_email"),
            "state": ms.STATE_OK,
            "message": (
                f"{label} is connected{who} and I can use it now. Say the word "
                "and I'll pick up where we left off."
            ),
        }
    )


def _handover_pending(provider: str, label: str, who: str) -> str:
    """Report a connection that landed but has not reached this process yet.

    Under the daemon the sidecar never holds the credential itself — the daemon
    forwards short-lived tokens in on a timer. Saying "all set" before that hand
    -over would send the user straight back into the same error, so the wait is
    stated plainly instead.
    """
    return _envelope_ok(
        {
            "changed": True,
            "provider": provider,
            "account_email": None,
            "state": ms.STATE_REAUTH_REQUIRED,
            "handover_pending": True,
            "message": (
                f"{label} is connected{who}. GAIA hands the access over to me "
                "separately, which takes a moment — try your request again "
                "shortly and I'll have it."
            ),
        }
    )


__all__ = ["OnboardingToolsMixin"]
