# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Organize tools — label, archive, mark read/unread, star.

These are reversible via the action log + ``restore_message``, so they
do NOT require per-action confirmation. Bulk-archive protection happens
at a higher layer via the batch-threshold counter (Phase I3).

Ordering invariant (Adversarial B2): every mutate tool MUST execute the
Gmail call FIRST and only ``record_action`` on success. If the API
raises, no row is written — phantom undo entries are a state-corruption
class.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from gaia.agents.base.tools import tool
from gaia.agents.email import action_store
from gaia.agents.email.verbose import log_tool_call
from gaia.connectors.errors import ConnectorsError
from gaia.logger import get_logger

log = get_logger(__name__)


def _envelope_ok(data: Any) -> str:
    return json.dumps({"ok": True, "data": data}, default=str)


def _envelope_err(message: str) -> str:
    return json.dumps({"ok": False, "error": message})


# ---------------------------------------------------------------------------
# Pure impls
# ---------------------------------------------------------------------------


def archive_message_impl(
    gmail,
    db,
    *,
    message_id: str,
    prior: Optional[Dict[str, Any]] = None,
    debug: bool = False,
) -> Dict[str, Any]:
    with log_tool_call(
        "archive_message", {"message_id": message_id}, debug=debug
    ) as st:
        # Fetch prior labels so the undo path can restore them. The
        # caller may pass ``prior`` to avoid a redundant API round-trip
        # (the organize closures fetch once for sender + prior_labels).
        if prior is None:
            prior = gmail.get_message(message_id)
        prior_labels = list(prior.get("labelIds", []))
        # Gmail call — if this raises, NO db row is written.
        gmail.archive_message(message_id)
        action_id = action_store.record_action(
            db,
            action_type="archive",
            message_id=message_id,
            thread_id=prior.get("threadId"),
            payload={"prior_labels": prior_labels},
        )
        st["result_summary"] = {"action_id": action_id}
        return {"action_id": action_id, "message_id": message_id}


def mark_read_impl(
    gmail, db, *, message_id: str, debug: bool = False
) -> Dict[str, Any]:
    with log_tool_call("mark_read", {"message_id": message_id}, debug=debug) as st:
        gmail.mark_read(message_id)
        action_id = action_store.record_action(
            db, action_type="mark_read", message_id=message_id, payload={}
        )
        st["result_summary"] = {"action_id": action_id}
        return {"action_id": action_id, "message_id": message_id}


def mark_unread_impl(
    gmail, db, *, message_id: str, debug: bool = False
) -> Dict[str, Any]:
    with log_tool_call("mark_unread", {"message_id": message_id}, debug=debug) as st:
        gmail.mark_unread(message_id)
        action_id = action_store.record_action(
            db, action_type="mark_unread", message_id=message_id, payload={}
        )
        st["result_summary"] = {"action_id": action_id}
        return {"action_id": action_id, "message_id": message_id}


def add_star_impl(gmail, db, *, message_id: str, debug: bool = False) -> Dict[str, Any]:
    with log_tool_call("add_star", {"message_id": message_id}, debug=debug) as st:
        gmail.add_star(message_id)
        action_id = action_store.record_action(
            db, action_type="add_star", message_id=message_id, payload={}
        )
        st["result_summary"] = {"action_id": action_id}
        return {"action_id": action_id, "message_id": message_id}


def remove_star_impl(
    gmail, db, *, message_id: str, debug: bool = False
) -> Dict[str, Any]:
    with log_tool_call("remove_star", {"message_id": message_id}, debug=debug) as st:
        gmail.remove_star(message_id)
        action_id = action_store.record_action(
            db, action_type="remove_star", message_id=message_id, payload={}
        )
        st["result_summary"] = {"action_id": action_id}
        return {"action_id": action_id, "message_id": message_id}


def label_message_impl(
    gmail, db, *, message_id: str, label_id: str, debug: bool = False
) -> Dict[str, Any]:
    with log_tool_call(
        "label_message",
        {"message_id": message_id, "label_id": label_id},
        debug=debug,
    ) as st:
        gmail.add_label(message_id, label_id)
        action_id = action_store.record_action(
            db,
            action_type="add_label",
            message_id=message_id,
            payload={"label_id": label_id},
        )
        st["result_summary"] = {"action_id": action_id}
        return {"action_id": action_id, "message_id": message_id, "label_id": label_id}


def move_to_label_impl(
    gmail,
    db,
    *,
    message_id: str,
    label_id: str,
    prior: Optional[Dict[str, Any]] = None,
    debug: bool = False,
) -> Dict[str, Any]:
    """Add a label and remove INBOX.

    Non-atomic: two public Protocol calls (``add_label`` then
    ``archive_message``). If the second call raises, the message will
    have the new label but still be in INBOX. The ``prior_labels`` field
    in the action row captures both the original label set so
    ``restore_message`` can recover either partial state.
    """
    with log_tool_call(
        "move_to_label",
        {"message_id": message_id, "label_id": label_id},
        debug=debug,
    ) as st:
        if prior is None:
            prior = gmail.get_message(message_id)
        prior_labels = list(prior.get("labelIds", []))
        # Gmail call first (ordering invariant: DB write only on success).
        gmail.add_label(message_id, label_id)
        gmail.archive_message(message_id)
        action_id = action_store.record_action(
            db,
            action_type="move_to_label",
            message_id=message_id,
            payload={"label_id": label_id, "prior_labels": prior_labels},
        )
        st["result_summary"] = {"action_id": action_id}
        return {"action_id": action_id, "message_id": message_id, "label_id": label_id}


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------


def _extract_sender(msg: Dict[str, Any]) -> str:
    """Pull the ``From`` header out of a Gmail-API-shape message."""
    for h in (msg.get("payload") or {}).get("headers", []):
        if (h.get("name") or "").lower() == "from":
            return h.get("value", "")
    return ""


# Sentinel error envelope returned by every organize closure when the
# Phase I3 batch threshold trips. The LLM must surface this to the user
# and ask for batch confirmation (the agent's planning loop sees this
# error and re-asks the user).
_BATCH_THRESHOLD_ERROR = (
    "Batch threshold exceeded: more than 5 organize operations across "
    "more than 3 distinct senders in this turn. Refusing to continue "
    "without user confirmation. Surface this to the user as a single "
    "batch-confirm prompt — the agent should not auto-bypass."
)


class OrganizeToolsMixin:
    def _register_organize_tools(self) -> None:
        gmail = self._gmail
        db = self
        debug_flag = bool(getattr(self.config, "debug", False))
        agent = self  # for batch-threshold counter access

        def _check_threshold() -> Optional[str]:
            """Return error message if Phase I3 threshold is exceeded, else None.

            Called BEFORE each organize op so the offending op never
            actually fires. The counter has already been bumped for
            prior ops in the turn.
            """
            if agent._organize_batch_threshold_exceeded():
                return _BATCH_THRESHOLD_ERROR
            return None

        @tool
        def archive_message(message_id: str) -> str:
            """Archive a message (remove from INBOX). Reversible via restore_message."""
            try:
                if (err := _check_threshold()) is not None:
                    return _envelope_err(err)
                # Single Gmail fetch — used for both prior_labels (impl)
                # and sender (counter). Avoids the redundant round-trip
                # the previous _peek_sender helper introduced.
                prior = gmail.get_message(message_id)
                agent._record_organize_op(message_id, _extract_sender(prior))
                return _envelope_ok(
                    archive_message_impl(
                        gmail, db, message_id=message_id, prior=prior, debug=debug_flag
                    )
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def mark_read(message_id: str) -> str:
            """Mark a message as read."""
            try:
                if (err := _check_threshold()) is not None:
                    return _envelope_err(err)
                # mark_read does not need prior_labels; only bump
                # the counter with what we know — the message_id
                # alone is enough since distinct-sender counting
                # treats unknown senders as "" (one bucket).
                agent._record_organize_op(message_id, "")
                return _envelope_ok(
                    mark_read_impl(gmail, db, message_id=message_id, debug=debug_flag)
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def mark_unread(message_id: str) -> str:
            """Mark a message as unread."""
            try:
                if (err := _check_threshold()) is not None:
                    return _envelope_err(err)
                agent._record_organize_op(message_id, "")
                return _envelope_ok(
                    mark_unread_impl(gmail, db, message_id=message_id, debug=debug_flag)
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def add_star(message_id: str) -> str:
            """Star a message."""
            try:
                if (err := _check_threshold()) is not None:
                    return _envelope_err(err)
                agent._record_organize_op(message_id, "")
                return _envelope_ok(
                    add_star_impl(gmail, db, message_id=message_id, debug=debug_flag)
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def remove_star(message_id: str) -> str:
            """Remove the star from a message."""
            try:
                if (err := _check_threshold()) is not None:
                    return _envelope_err(err)
                agent._record_organize_op(message_id, "")
                return _envelope_ok(
                    remove_star_impl(gmail, db, message_id=message_id, debug=debug_flag)
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def label_message(message_id: str, label_id: str) -> str:
            """Add a label to a message. Pass the label id (e.g. ``Label_1``)."""
            try:
                if (err := _check_threshold()) is not None:
                    return _envelope_err(err)
                agent._record_organize_op(message_id, "")
                return _envelope_ok(
                    label_message_impl(
                        gmail,
                        db,
                        message_id=message_id,
                        label_id=label_id,
                        debug=debug_flag,
                    )
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def move_to_label(message_id: str, label_id: str) -> str:
            """Move a message out of INBOX into a label."""
            try:
                if (err := _check_threshold()) is not None:
                    return _envelope_err(err)
                # Single Gmail fetch — for prior_labels + sender.
                prior = gmail.get_message(message_id)
                agent._record_organize_op(message_id, _extract_sender(prior))
                return _envelope_ok(
                    move_to_label_impl(
                        gmail,
                        db,
                        message_id=message_id,
                        label_id=label_id,
                        prior=prior,
                        debug=debug_flag,
                    )
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")
