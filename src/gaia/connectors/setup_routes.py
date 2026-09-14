# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Authored setup-walkthrough content for guided mailbox onboarding (#2590).

The problem this replaces: connecting Outlook meant sending someone to
Microsoft's developer portal with a single paragraph of instructions and no
way to tell whether they actually got it right. This module is the single
source of truth for what the walkthrough says, step by step, so the content
lives in ONE place — not duplicated across the OAuth-not-configured error
message, a docs page, and an agent prompt that could each drift.

**Outlook, and a personal Gmail route (#2594, "the Google half of #2590").**
``google_workspace`` (Internal consent screen, the account-kind interview)
and ``microsoft_work`` still earn their own PR — see #2594's remaining scope.
``ROUTES`` has two entries.

No ``resolve_route`` / interview function here — the provider alone selects
the route (``ROUTES[provider]``), because the only question that decides it
("Gmail or Outlook?") is already asked elsewhere
(``onboarding_tools._choose_provider``). A provider with no route must get a
defined, user-legible answer from ``get_route`` (``None``) — never a
``KeyError`` — so a caller can render "no guided walkthrough yet" instead of
crashing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class QA:
    """One authored FAQ answer, matched by keyword against a free-text question.

    ``answer`` is emitted to the user VERBATIM — the walkthrough driver must
    never paraphrase, prepend, or otherwise compose it. That is what keeps a
    small local model from inventing setup guidance.
    """

    question_hints: Tuple[str, ...]
    answer: str


@dataclass(frozen=True)
class Step:
    """One step of a guided setup walkthrough."""

    id: str
    title: str
    instruction: str
    #: Whether GAIA can check this step actually succeeded (offline shape
    #: check or a live probe) rather than taking the user's word for it.
    verifiable: bool
    #: Whether this step's answer is a credential, not a navigation choice.
    #: Gates the free-text FAQ lane off entirely (see
    #: ``gaia_agent_email.tools.setup_walkthrough``) — a credential prompt
    #: must never be re-issued with ``allow_free_text=False`` on an
    #: options-less prompt, and must never receive the FAQ lane at all.
    collects_credential: bool = False
    #: True for a step that ONLY the browser-loopback sign-in needs (e.g. a
    #: redirect URI). The device-code grant (RFC 8628) has no redirect at
    #: all — sending a device-code user through a redirect-URI step is not
    #: harmless filler, it is a wasted step this feature exists to remove.
    #: ``render_console_steps(sign_in="device_code")`` and the interactive
    #: walkthrough driver both filter these out; the CLI-facing rendering
    #: (``sign_in="loopback"``, the default) keeps them.
    loopback_only: bool = False
    #: True for a credential step whose value must never be echoed in
    #: cleartext (a client secret) — threaded through to ``ask(...,
    #: sensitive=True)`` by the walkthrough driver. Microsoft's route has
    #: nothing to hide (a public-client Application ID); Google's does.
    sensitive: bool = False
    faq: Tuple[QA, ...] = ()


@dataclass(frozen=True)
class SetupRoute:
    """A complete guided walkthrough for one provider."""

    id: str
    provider: str
    steps: Tuple[Step, ...]
    faq: Tuple[QA, ...] = ()


# ---------------------------------------------------------------------------
# Microsoft — personal + work/school (the ``common`` tenant)
# ---------------------------------------------------------------------------
# ``providers.microsoft.OAuthClientNotConfiguredError``'s ``console_steps`` is
# derived from this route via ``render_console_steps`` (see
# ``providers/microsoft.py``), so the CLI-facing text and the interactive
# walkthrough cannot drift the way five earlier hand-copies did (#2116).
#
# The device-code grant (RFC 8628) — the sign-in this PR wires up — has no
# redirect at all, so ``redirect_uri`` is ``loopback_only``: the interactive
# walkthrough must never send a device-code user to configure it. It DOES
# need the app marked as a public client (``public_client_flows``), which the
# Authentication -> Mobile & desktop platform also sets as a side effect —
# but relying on that side effect silently is how a registration that skips
# it fails device code with ``AADSTS7000218`` ("client_secret is required"),
# a spectacularly confusing error for a route that has no secret. Naming it
# as its own step makes it a step GAIA can actually walk the user through
# instead of an implicit side effect of a different one.

MS_PERSONAL = SetupRoute(
    id="ms_personal",
    provider="microsoft",
    steps=(
        Step(
            id="register",
            title="Register an app",
            instruction=(
                "Register an app at https://portal.azure.com -> Microsoft "
                "Entra ID -> App registrations"
            ),
            verifiable=False,
            faq=(
                QA(
                    question_hints=("sign in", "which account", "microsoft account"),
                    answer=(
                        "Use whichever Microsoft account you want to sign in "
                        "with when you connect the mailbox — it doesn't have "
                        "to be a special admin account for a personal "
                        "Outlook.com sign-in."
                    ),
                ),
            ),
        ),
        Step(
            id="account_type",
            title="Set the supported account type",
            instruction=(
                "Set the supported account type to 'any account' ('common' "
                "audience — personal + work/school)"
            ),
            verifiable=False,
        ),
        Step(
            id="redirect_uri",
            title="Add a redirect URI",
            instruction=(
                "Add a http://localhost redirect URI under Authentication -> "
                "Mobile & desktop applications"
            ),
            verifiable=False,
            loopback_only=True,
        ),
        Step(
            id="public_client_flows",
            title="Allow public client flows",
            instruction=(
                "Turn on 'Allow public client flows' under Authentication -> "
                "Advanced settings — this marks the app as a public client, "
                "which is what lets you sign in with a short code instead of "
                "a client secret"
            ),
            verifiable=False,
            faq=(
                QA(
                    question_hints=("why", "what does this do", "public client"),
                    answer=(
                        "It tells Microsoft this app can't keep a secret safe "
                        "(it runs on your machine, not a server), so sign-in "
                        "uses a short code instead — the same reason GAIA "
                        "never asks you for one."
                    ),
                ),
            ),
        ),
        Step(
            id="permissions",
            title="Add the Microsoft Graph permissions",
            instruction=(
                "Add the Microsoft Graph delegated permissions you need "
                "(e.g. Mail.ReadWrite, Mail.Send, Calendars.ReadWrite)"
            ),
            verifiable=False,
            faq=(
                QA(
                    question_hints=("admin consent", "grant consent", "why"),
                    answer=(
                        "Not for a personal account — these are delegated "
                        "permissions, so you approve them yourself the first "
                        "time you sign in. Admin consent only comes up on a "
                        "work/school tenant whose administrator has locked "
                        "down app consent."
                    ),
                ),
            ),
        ),
        Step(
            id="client_id",
            title="Copy the Application (client) ID",
            instruction=(
                "Copy the Application (client) ID — this is a public (PKCE) "
                "client, so no client secret is needed"
            ),
            verifiable=True,
            collects_credential=True,
            faq=(
                # The Overview blade shows THREE GUIDs side by side — a
                # shape check alone can't tell them apart, and pasting the
                # wrong one passes validation cleanly, then fails later in a
                # way that points nowhere near the real mistake.
                QA(
                    question_hints=(
                        "which id",
                        "three",
                        "tenant",
                        "object",
                        "directory",
                    ),
                    answer=(
                        "The Overview page shows three GUIDs — copy the one "
                        "labeled 'Application (client) ID'. The other two, "
                        "'Object ID' and 'Directory (tenant) ID', look the "
                        "same shape but are not what I need here."
                    ),
                ),
            ),
        ),
    ),
    faq=(
        QA(
            question_hints=("client secret", "secret"),
            answer=(
                "No — this app registration is a public client (PKCE), and "
                "Microsoft's own rules say a public client must never send a "
                "client secret. GAIA only needs the Application (client) ID "
                "you copy in the last step."
            ),
        ),
    ),
)

# ---------------------------------------------------------------------------
# Google — personal Gmail only (#2594, "the Google half of #2590")
# ---------------------------------------------------------------------------
# ``providers.google.OAuthClientNotConfiguredError``'s ``console_steps`` is
# derived from this route via ``render_console_steps`` (see
# ``providers/google.py``), same as Microsoft — the module docstring's #2116
# "one source of truth" guard applies here too.
#
# Unlike Microsoft, Google has no device-code flow for a personal (non-
# Workspace) client and requires a client secret even for a Desktop-app
# (PKCE) client — so this route is loopback-only (no ``loopback_only`` steps
# to drop) and its secret step is ``sensitive=True``.

GOOGLE_PERSONAL = SetupRoute(
    id="google_personal",
    provider="google",
    steps=(
        Step(
            id="project",
            title="Create or pick a project",
            instruction=(
                "Create or pick a project at https://console.cloud.google.com"
            ),
            verifiable=False,
        ),
        Step(
            id="enable_api",
            title="Enable the Gmail API",
            instruction=(
                "Enable the Gmail API for that project (APIs & Services -> "
                "Enabled APIs & services -> + Enable APIs and services)"
            ),
            verifiable=False,
            faq=(
                QA(
                    question_hints=("why enable", "what does this do", "gmail api"),
                    answer=(
                        "Google's console lets you create an OAuth client "
                        "before enabling any API for it — skip this step and "
                        "sign-in itself succeeds, but every Gmail call fails "
                        "afterward. Enabling it now avoids a confusing "
                        "failure later."
                    ),
                ),
            ),
        ),
        Step(
            id="consent_screen",
            title="Configure the OAuth consent screen",
            instruction=(
                "Configure the OAuth consent screen: choose 'External', and "
                "add your own Google account under 'Test users'"
            ),
            verifiable=False,
            faq=(
                QA(
                    question_hints=(
                        "test user",
                        "testing",
                        "100 user",
                        "unverified",
                    ),
                    answer=(
                        "A new consent screen starts in 'Testing' mode, which "
                        "only lets signed-in test users through — that's why "
                        "you add your own account. Google's app-verification "
                        "review is for apps used by the public; it doesn't "
                        "apply to a client only you sign in with."
                    ),
                ),
            ),
        ),
        Step(
            id="redirect_uri",
            title="Add a redirect URI",
            instruction=(
                "Add a http://localhost redirect URI under the OAuth client's "
                "'Authorized redirect URIs'"
            ),
            verifiable=False,
        ),
        Step(
            id="create_client",
            title="Create an OAuth client ID",
            instruction=(
                "Create an OAuth client ID of type 'Desktop app' — this gives "
                "you a Client ID and Client Secret"
            ),
            verifiable=False,
            faq=(
                QA(
                    question_hints=("application type", "web application", "desktop"),
                    answer=(
                        "Pick 'Desktop app', not 'Web application' — a Web "
                        "client requires an exact pre-registered redirect URI "
                        "and rejects GAIA's local sign-in port silently, which "
                        "shows up later as a sign-in that never completes."
                    ),
                ),
            ),
        ),
        Step(
            id="client_id",
            title="Copy the Client ID",
            instruction=(
                "Copy the Client ID (ends in .apps.googleusercontent.com)"
            ),
            verifiable=True,
            collects_credential=True,
        ),
        Step(
            id="client_secret",
            title="Copy the Client Secret",
            instruction="Copy the Client Secret shown next to it",
            verifiable=False,
            collects_credential=True,
            sensitive=True,
        ),
    ),
    faq=(
        QA(
            question_hints=("client secret", "why a secret", "secret"),
            answer=(
                "Yes — unlike Outlook, Google requires a client secret even "
                "for this Desktop-app (PKCE) client type. GAIA stores it in "
                "your OS keychain and never sends it anywhere but Google's "
                "token endpoint."
            ),
        ),
    ),
)

#: Provider id -> its guided walkthrough. See the module docstring for why
#: ``google_workspace`` and ``microsoft_work`` are not here yet.
ROUTES: Dict[str, SetupRoute] = {"microsoft": MS_PERSONAL, "google": GOOGLE_PERSONAL}

#: Sign-in mechanisms ``render_console_steps`` and the walkthrough driver
#: know how to filter for.
SIGN_IN_LOOPBACK = "loopback"
SIGN_IN_DEVICE_CODE = "device_code"
_VALID_SIGN_INS = (SIGN_IN_LOOPBACK, SIGN_IN_DEVICE_CODE)


def get_route(provider: str) -> Optional[SetupRoute]:
    """Return the guided walkthrough for *provider*, or ``None`` if it has none.

    Never raises — a provider with no route is a normal, expected case (every
    provider except Microsoft, today), and the caller renders a defined
    "no guided walkthrough yet" response rather than crashing.
    """
    return ROUTES.get(provider)


def steps_for(
    route: SetupRoute, *, sign_in: str = SIGN_IN_LOOPBACK
) -> Tuple[Step, ...]:
    """Return *route*'s steps filtered for *sign_in*.

    ``sign_in="device_code"`` drops every ``loopback_only`` step (a redirect
    URI the device-code grant never uses); ``sign_in="loopback"`` (the
    default) keeps everything — the CLI-facing text covers whichever route
    the user ends up taking.
    """
    if sign_in not in _VALID_SIGN_INS:
        raise ValueError(f"sign_in must be one of {_VALID_SIGN_INS}, got {sign_in!r}")
    if sign_in == SIGN_IN_DEVICE_CODE:
        return tuple(s for s in route.steps if not s.loopback_only)
    return route.steps


def render_console_steps(route: SetupRoute, *, sign_in: str = SIGN_IN_LOOPBACK) -> str:
    """Render *route*'s steps as the numbered plain-text block used in
    ``OAuthClientNotConfiguredError.console_steps`` — the CLI-facing error a
    user sees before any interactive walkthrough is even possible.

    This is the single rendering function for that text; nothing else may
    hand-maintain a second copy of it (see the module docstring).
    """
    steps = steps_for(route, sign_in=sign_in)
    return "\n".join(
        f"  {i}. {step.instruction}" for i, step in enumerate(steps, start=1)
    )


__all__ = [
    "QA",
    "GOOGLE_PERSONAL",
    "MS_PERSONAL",
    "ROUTES",
    "SIGN_IN_DEVICE_CODE",
    "SIGN_IN_LOOPBACK",
    "SetupRoute",
    "Step",
    "get_route",
    "render_console_steps",
    "steps_for",
]
