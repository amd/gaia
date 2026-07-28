# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Authored setup-walkthrough content for guided mailbox onboarding (#2590).

The problem this replaces: connecting Outlook meant sending someone to
Microsoft's developer portal with a single paragraph of instructions and no
way to tell whether they actually got it right. This module is the single
source of truth for what the walkthrough says, step by step, so the content
lives in ONE place — not duplicated across the OAuth-not-configured error
message, a docs page, and an agent prompt that could each drift.

**Outlook only.** This is a deliberate scope cut (#2590 adversarial review):
``google_personal`` and every other route earn their own PR. ``ROUTES`` has
exactly one entry.

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
# Step instructions are the SAME text as the four steps previously
# hand-maintained in ``providers.microsoft.OAuthClientNotConfiguredError``'s
# ``console_steps`` — that field is now derived from this route via
# ``render_console_steps`` (see ``providers/microsoft.py``), so the two
# cannot drift the way five earlier copies did (#2116).

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
            id="account_type_redirect",
            title="Set the account type and redirect URI",
            instruction=(
                "Set the supported account type to 'any account' ('common' "
                "audience — personal + work/school), and add a "
                "http://localhost redirect URI under Authentication -> "
                "Mobile & desktop applications"
            ),
            verifiable=False,
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

#: Provider id -> its guided walkthrough. Outlook only in this PR — see the
#: module docstring for why the other routes are out of scope.
ROUTES: Dict[str, SetupRoute] = {"microsoft": MS_PERSONAL}


def get_route(provider: str) -> Optional[SetupRoute]:
    """Return the guided walkthrough for *provider*, or ``None`` if it has none.

    Never raises — a provider with no route is a normal, expected case (every
    provider except Microsoft, today), and the caller renders a defined
    "no guided walkthrough yet" response rather than crashing.
    """
    return ROUTES.get(provider)


def render_console_steps(route: SetupRoute) -> str:
    """Render *route*'s steps as the numbered plain-text block used in
    ``OAuthClientNotConfiguredError.console_steps`` — the CLI-facing error a
    user sees before any interactive walkthrough is even possible.

    This is the single rendering function for that text; nothing else may
    hand-maintain a second copy of it (see the module docstring).
    """
    return "\n".join(
        f"  {i}. {step.instruction}" for i, step in enumerate(route.steps, start=1)
    )


__all__ = [
    "QA",
    "MS_PERSONAL",
    "ROUTES",
    "SetupRoute",
    "Step",
    "get_route",
    "render_console_steps",
]
