# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""How a tool tells a person to connect an account, for the front-end they use.

In the Agent UI a tool that finds no usable account puts :func:`connect_card`
in its result under :data:`CONNECT_CARD_KEY`. The stream handler lifts it into
a typed ``connect_account`` card, so the Connect button appears whatever the
model writes, and the model is told the button is there. Other front-ends have
no button, so they get the remedy in words they can act on.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Tuple

from gaia.agents.base.tool_outcome import tool_result_dict

#: The card's render key, and its payload's ``kind``.
CONNECT_CARD_KIND = "connect_account"

#: Where a tool result carries the card.
CONNECT_CARD_KEY = "connect_card"

#: The accounts a card may offer. Any tool result can carry a card, so one
#: naming anything else is dropped rather than drawn as a sign-in button.
CONNECT_CARD_PROVIDERS = frozenset({"google", "microsoft", "microsoft_work"})

#: The front-end that draws the card.
AGENT_UI = "agent_ui"

#: What the model is told in the Agent UI. The button is the remedy.
CONNECT_BUTTON_NOTE = (
    "GAIA is showing the user a Connect button for this in the chat, and "
    "the same button is in Settings > Connectors. Tell the user what is "
    "missing and that the button is on screen, and to ask again once they "
    "have signed in. Accounts connect only through that button: never give "
    "or run a terminal command for it."
)

#: What the model is told in the terminal UI, which has no button of its own.
CONNECT_IN_APP_NOTE = (
    "Accounts are connected in the GAIA desktop app, under Settings > "
    "Connectors. Tell the user what is missing and to connect it there, then "
    "ask again. Never give or run a terminal command for it."
)

#: The tool row's one-line summary when the tool gave no error of its own.
CONNECT_CARD_SUMMARY = "Needs a connected account"

#: Where a fix lives when signing in is not it (a grant, a setup fault).
CONNECTORS_SETTINGS = "Settings > Connectors"


def agent_surface(agent: Any) -> Optional[str]:
    """The front-end *agent* serves: "agent_ui", "tui", "cli", or None."""
    return getattr(getattr(agent, "config", None), "surface", None)


def in_app(agent: Any) -> bool:
    """Whether *agent* serves one of the apps, where a command is no remedy."""
    return agent_surface(agent) in (AGENT_UI, "tui")


def connect_note(
    providers: Iterable[str], *, agent_id: str, surface: Optional[str]
) -> str:
    """What to tell the model about connecting *providers* on *surface*."""
    if surface == AGENT_UI:
        return CONNECT_BUTTON_NOTE
    if surface == "tui":
        return CONNECT_IN_APP_NOTE
    # A headless caller is already in a terminal, so the command is its remedy.
    commands = " or ".join(
        f"`gaia connectors connect {p} --grant-agent {agent_id}`" for p in providers
    )
    return (
        f"Connect it with {commands}, or in the GAIA desktop app under "
        f"{CONNECTORS_SETTINGS}, then ask again."
    )


def connect_card(
    providers: Iterable[str], *, agent_id: str, reconnect: Iterable[str] = ()
) -> Dict[str, Any]:
    """The card payload offering *providers*, granting *agent_id* on sign-in.

    *reconnect* names those connected already, so the card does not read their
    being connected as the fix.
    """
    names = list(dict.fromkeys(providers))
    if not names:
        raise ValueError("a connect card needs at least one provider")
    unknown = [p for p in names if p not in CONNECT_CARD_PROVIDERS]
    if unknown:
        raise ValueError(
            f"a connect card can offer only {sorted(CONNECT_CARD_PROVIDERS)}, "
            f"not {unknown}"
        )
    card = {"kind": CONNECT_CARD_KIND, "providers": names, "agent_id": agent_id}
    again = [p for p in names if p in set(reconnect)]
    if again:
        card["reconnect"] = again
    return card


def token_fault(name: str, error: Exception) -> Tuple[str, bool]:
    """``(message, needs_reconnect)`` for a token the connectors refused.

    Worded for the apps: the connectors' own text ends in a terminal command.
    """
    from gaia.connectors.errors import ConnectionBusyError

    if isinstance(error, ConnectionBusyError):
        return (
            f"The {name} connection is being saved by another GAIA operation "
            "right now. Retry in a moment.",
            False,
        )
    return (
        f"GAIA could not use the {name} connection ({type(error).__name__}), "
        "so it needs reconnecting.",
        True,
    )


def with_connect_remedy(
    payload: Dict[str, Any],
    providers: Iterable[str],
    *,
    agent_id: str,
    surface: Optional[str],
    field: str = "error",
    reconnect: Iterable[str] = (),
) -> Dict[str, Any]:
    """*payload* with how to connect *providers* added to its ``field``.

    Unchanged when there is nothing to connect. The card rides along only in
    the Agent UI, the one front-end that draws it.
    """
    names = list(dict.fromkeys(providers))
    if not names:
        return payload
    note = connect_note(names, agent_id=agent_id, surface=surface)
    payload[field] = f"{payload[field]} {note}" if payload.get(field) else note
    if surface == AGENT_UI:
        payload[CONNECT_CARD_KEY] = connect_card(
            names, agent_id=agent_id, reconnect=reconnect
        )
    return payload


def with_connect_card(
    payload: Dict[str, Any], error: Any, *, agent_id: str, surface: Optional[str]
) -> Dict[str, Any]:
    """A failed result, plus the remedy when *error* names connectors to connect."""
    return with_connect_remedy(
        payload,
        getattr(error, "connect", ()),
        agent_id=agent_id,
        surface=surface,
        reconnect=getattr(error, "reconnect", ()),
    )


def connect_card_in(result: Any) -> Optional[Dict[str, Any]]:
    """The well-formed connect card a tool result carries, else ``None``."""
    data = tool_result_dict(result)
    card = data.get(CONNECT_CARD_KEY) if data else None
    if not isinstance(card, dict) or card.get("kind") != CONNECT_CARD_KIND:
        return None
    providers = card.get("providers")
    if not isinstance(providers, list) or not providers:
        return None
    if not all(isinstance(p, str) and p in CONNECT_CARD_PROVIDERS for p in providers):
        return None
    if not isinstance(card.get("agent_id", ""), str):
        return None
    return card
