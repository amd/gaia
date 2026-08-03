# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Resolve a positional card reference ("reply to 1", "archive 3") to the
message a ``needs_you`` triage card actually shows (#2745).

``NeedsYouItem.ref`` (contract.py, #2743) is a 1-based row number assigned
server-side and stable within ONE card render only — a rescan re-orders and
renumbers by design (older mail a deeper scan finds sorts to the front).
Before this module existed, the model had no deterministic way to turn "1"
back into a real message; it had to guess from its own reading of the
envelope, and a wrong guess on a reply/archive/accept is visible to a third
party and cannot be undone. This module is the one seam that does that
lookup, and it refuses rather than guesses whenever it can't.

Verb-agnostic by design: this resolves a row number to
``{message_id, subject, sender, kind, thread_id, mailbox}`` only. It does
NOT parse "reply"/"archive"/"accept" — picking the downstream action tool
from that data stays the model's job, unchanged by this module. Kept
verb-agnostic on purpose so a future "tell me more about 1" rides the same
resolver without any change here.

"Current card" means only the interactive chat card: ``self._last_needs_you_card``
is set by ``ReadToolsMixin``'s ``pre_scan_inbox`` TOOL closure in
``read_tools.py`` (never by ``pre_scan_inbox_impl`` directly), so a REST
``/prescan`` call or the scheduled briefing job — which also call
``pre_scan_inbox_impl`` — never feed this cache. Only a card the user
actually saw in THIS conversation is ever resolved against.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence

from gaia_agent_email.tools.envelope import _envelope_err, _envelope_ok

from gaia.agents.base.tools import tool
from gaia.logger import get_logger

log = get_logger(__name__)


class RefResolutionError(ValueError):
    """A positional card reference could not be resolved.

    Raised instead of returning a partial or best-guess match — every one
    of these is a question back to the user, never a silent wrong-message
    action (a reply/archive on the wrong message is visible to a third
    party and cannot be undone).
    """


def resolve_needs_you_ref(
    card: Optional[Sequence[Mapping[str, Any]]], ref: Any
) -> Dict[str, Any]:
    """Resolve a 1-based row number against the CURRENT ``needs_you`` card.

    ``card`` is the ``needs_you`` list from the most recent ``pre_scan_inbox``
    tool call this session — a list of plain dicts shaped like
    ``NeedsYouItem`` (``ref``, ``kind``, ``message_id``, ``thread_id``,
    ``sender``, ``subject``, ``mailbox``, ...). ``None`` means no scan has
    happened yet this session; an empty list ``[]`` means a scan DID run but
    found nothing for ``needs_you`` — these are different facts and get
    different refusal messages (a rescan that renumbers down to zero rows
    must not be reported as "no card yet", which the user just disproved by
    triaging).

    Returns a dict: ``{"ref", "message_id", "thread_id", "sender",
    "subject", "kind", "mailbox"}``.

    Raises :class:`RefResolutionError` — never returns a partial or guessed
    match — when:

    - ``card`` is ``None`` (no scan has happened yet this session) or ``[]``
      (a scan ran but ``needs_you`` was empty) — distinct messages for each;
    - ``ref`` does not parse to a positive integer;
    - no item on ``card`` has that ``ref`` (out of range for THIS card —
      a rescan may have renumbered since the number was shown);
    - the matching item has no ``message_id`` (a carried-over action item
      with no recoverable source message, per ``NeedsYouItem.message_id``'s
      own contract).
    """
    if card is None:
        raise RefResolutionError(
            "No triage card in this conversation yet — run a pre-scan "
            '("triage my inbox") first, then refer to a row number from it.'
        )
    if not card:
        raise RefResolutionError(
            "Your last triage found nothing needing a reply, so there are "
            "no rows to refer to. Run a new pre-scan if you think that's "
            "changed."
        )

    try:
        ref_n = int(str(ref).strip())
    except (TypeError, ValueError):
        raise RefResolutionError(
            f"{ref!r} isn't a row number from the current card — say which "
            "numbered row you mean."
        ) from None
    if ref_n < 1:
        raise RefResolutionError(
            f"{ref_n} isn't a valid row number — the card is numbered from 1."
        )

    matches = [item for item in card if _item_ref(item) == ref_n]
    if not matches:
        valid_refs = sorted({_item_ref(item) for item in card})
        raise RefResolutionError(
            f"Row {ref_n} isn't on the current card (rows "
            f"{valid_refs[0]}-{valid_refs[-1]}) — it may have been renumbered "
            "by a newer scan. Re-run the pre-scan and use the number it "
            "shows now, or say which message you mean."
        )
    if len(matches) > 1:
        raise RefResolutionError(
            f"Row {ref_n} is ambiguous on the current card — re-run the "
            "pre-scan and try again."
        )

    item = matches[0]
    message_id = item.get("message_id")
    if not message_id:
        subject = item.get("subject") or "(no subject)"
        raise RefResolutionError(
            f"Row {ref_n} ({subject!r}) has no source message to act on — "
            "it's a carried-over task with no recoverable message."
        )

    return {
        "ref": ref_n,
        "message_id": message_id,
        "thread_id": item.get("thread_id"),
        "sender": item.get("sender", ""),
        "subject": item.get("subject", ""),
        "kind": item.get("kind", ""),
        "mailbox": item.get("mailbox"),
    }


def _item_ref(item: Mapping[str, Any]) -> int:
    try:
        return int(item.get("ref", -1))
    except (TypeError, ValueError):
        return -1


class RefResolveToolsMixin:
    """Mixin that registers the positional-reference resolver tool.

    State-free at construction time — it relies on the agent class having
    set ``self._last_needs_you_card`` (``None`` until the first
    ``pre_scan_inbox`` call this session) before invoking
    ``self._register_ref_resolve_tools()``. The ``agent`` closure capture is
    used so the tool always reads the LIVE attribute, never a value snapshot
    at registration time — the whole point being that it reflects whichever
    card was most recently shown, including after a rescan.
    """

    def _register_ref_resolve_tools(self) -> None:
        agent = self  # captured for live access to ``_last_needs_you_card``

        @tool
        def resolve_needs_you_reference(ref: int) -> str:
            """Resolve a positional reference from the most recent triage
            card ("reply to 1", "archive 3", "accept 2", "tell me more
            about 4") to the message it refers to.

            Call this BEFORE acting on any numbered reference from a
            ``pre_scan_inbox`` card — never infer which message a number
            means from your own reading of the envelope, and never reuse a
            row number from an earlier turn once a newer pre-scan has run,
            since a rescan can renumber (older mail a deeper scan finds
            sorts to the front). On success, state the resolved
            ``subject``/``sender`` in your reply BEFORE calling the action
            tool (draft_reply, archive_message, accept_invite, ...) with the
            returned ``message_id`` — so a wrong resolution is visible to
            the user immediately, before any side effect. On failure, ask
            the user rather than guessing or falling back to a search.

            Args:
                ref: The row number the user referred to (the number shown
                    next to that row on the current triage card).

            Returns:
                JSON envelope. On success, ``data`` carries ``ref``,
                ``message_id``, ``thread_id``, ``sender``, ``subject``,
                ``kind``, ``mailbox``. On failure, ``error`` explains why
                (no card yet, out of range, ambiguous, or no recoverable
                message) — treat this as a question to ask the user, never
                as license to guess.
            """
            try:
                resolved = resolve_needs_you_ref(
                    getattr(agent, "_last_needs_you_card", None), ref
                )
                return _envelope_ok(resolved)
            except RefResolutionError as exc:
                return _envelope_err(str(exc))


__all__ = [
    "RefResolutionError",
    "RefResolveToolsMixin",
    "resolve_needs_you_ref",
]
