# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""What a new chat's empty screen offers: task cards and the working folder.

Cards come from what is installed and set up on this PC — skills, indexed
documents, connected mail accounts — never from a model call, so the screen
costs nothing to open. A card only appears for a skill the agent has.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Optional

from gaia.logger import get_logger

logger = get_logger(__name__)

MAX_CARDS = 4

MAIL_CONNECTORS = ("google", "microsoft", "microsoft_work")


@dataclass(frozen=True)
class Card:
    skill: str
    title: str
    note: str
    prompt: str
    #: True when the prompt ends where the user types the subject.
    needs_input: bool = False


@dataclass(frozen=True)
class Context:
    skills: frozenset
    indexed_documents: int
    mail_connected: bool
    folder_name: str


def _documents(ctx: Context) -> Optional[Card]:
    if not ctx.indexed_documents:
        return None
    n = ctx.indexed_documents
    return Card(
        "document-brief",
        "Ask about your documents",
        f"{n} indexed document{'s' if n != 1 else ''}",
        "Using my indexed documents, give me the key points and anything I should act on.",
    )


def _inbox(ctx: Context) -> Optional[Card]:
    if not ctx.mail_connected:
        return None
    return Card(
        "inbox-triage",
        "Triage my inbox",
        "Your connected mail account",
        "Triage my inbox: what needs a reply today, and what can wait?",
    )


def _research(_ctx: Context) -> Card:
    return Card(
        "research-report",
        "Research a topic",
        "A cited report from the web",
        "Research and write a short cited report on: ",
        needs_input=True,
    )


def _code(ctx: Context) -> Card:
    return Card(
        "coding",
        "Work on code",
        f"In {ctx.folder_name}",
        f"Look around {ctx.folder_name} and tell me what this project does.",
    )


def _summarize(_ctx: Context) -> Card:
    return Card(
        "summarize",
        "Summarize something",
        "A document, transcript or email",
        "Summarize this: ",
        needs_input=True,
    )


def _check_in(_ctx: Context) -> Card:
    return Card(
        "check-in",
        "Check in",
        "Pick up where you left off",
        "Let's check in.",
    )


# What the user set up comes first; the rest work on a fresh install.
_RULES: List[Callable[[Context], Optional[Card]]] = [
    _documents,
    _inbox,
    _research,
    _code,
    _summarize,
    _check_in,
]


def pick_cards(ctx: Context) -> List[Dict[str, Any]]:
    """Up to :data:`MAX_CARDS` cards, each for a skill the agent has."""
    cards = []
    for rule in _RULES:
        card = rule(ctx)
        if card is not None and card.skill in ctx.skills:
            cards.append(asdict(card))
        if len(cards) == MAX_CARDS:
            break
    return cards


def working_folder() -> Dict[str, Any]:
    """The folder a new chat's agent works in, as ``_compute_allowed_paths`` grants it."""
    from gaia.ui import _chat_helpers as chat

    cwd = chat.default_working_dir()
    if chat._unsafe_directory_grant_reason(cwd):
        folder, own = chat._managed_documents_dir(), True
    else:
        folder, own = cwd, False
    return {"path": str(folder), "name": folder.name or str(folder), "gaia_folder": own}


def _mail_connected() -> bool:
    from gaia.connectors.errors import ConnectorsError
    from gaia.connectors.registry import REGISTRY
    from gaia.connectors.store import peek_connection

    try:
        for connector_id in MAIL_CONNECTORS:
            spec = REGISTRY.get(connector_id)
            if peek_connection(spec.oauth_provider_ref or spec.id) is not None:
                return True
    except ConnectorsError as e:
        # The inbox card is a hint; an unreadable keyring must not blank the screen.
        logger.warning("New-chat inbox card skipped: can't read connections: %s", e)
    return False


def start_screen(db, skills: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The cards and working folder for a new chat; *skills* is ``/api/skills``."""
    import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import

    folder = working_folder()
    ctx = Context(
        skills=frozenset(s["name"] for s in skills),
        indexed_documents=sum(
            1 for d in db.list_documents() if d.get("indexing_status") == "complete"
        ),
        mail_connected=_mail_connected(),
        folder_name=folder["name"],
    )
    return {"cards": pick_cards(ctx), "working_folder": folder}
