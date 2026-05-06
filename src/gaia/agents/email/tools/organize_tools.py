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
from typing import Any, Dict

from gaia.agents.base.tools import tool
from gaia.agents.email import action_store
from gaia.agents.email.verbose import log_tool_call


def _envelope_ok(data: Any) -> str:
    return json.dumps({"ok": True, "data": data}, default=str)


def _envelope_err(message: str) -> str:
    return json.dumps({"ok": False, "error": message})


# ---------------------------------------------------------------------------
# Pure impls
# ---------------------------------------------------------------------------


def archive_message_impl(
    gmail, db, *, message_id: str, debug: bool = False
) -> Dict[str, Any]:
    with log_tool_call(
        "archive_message", {"message_id": message_id}, debug=debug
    ) as st:
        # Fetch prior labels FIRST so the undo path can restore them.
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
    gmail, db, *, message_id: str, label_id: str, debug: bool = False
) -> Dict[str, Any]:
    """Add a label and remove INBOX in one step."""
    with log_tool_call(
        "move_to_label",
        {"message_id": message_id, "label_id": label_id},
        debug=debug,
    ) as st:
        prior = gmail.get_message(message_id)
        prior_labels = list(prior.get("labelIds", []))
        gmail.add_label(message_id, label_id)
        gmail.archive_message(message_id)  # remove INBOX
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


class OrganizeToolsMixin:
    def _register_organize_tools(self) -> None:
        gmail = self._gmail
        db = self
        debug_flag = bool(getattr(self.config, "debug", False))
        # Reference the agent so the batch-threshold counter
        # (Phase I3) can be incremented from inside the closures.
        agent = self

        def _bump_organize_counter(message_id: str, sender: str) -> None:
            try:
                agent._record_organize_op(message_id, sender)
            except AttributeError:
                # Agent may not have wired the counter yet; ignore.
                pass

        @tool
        def archive_message(message_id: str) -> str:
            """Archive a message (remove from INBOX). Reversible via restore_message."""
            try:
                _bump_organize_counter(message_id, _peek_sender(gmail, message_id))
                return _envelope_ok(
                    archive_message_impl(
                        gmail, db, message_id=message_id, debug=debug_flag
                    )
                )
            except Exception as exc:
                return _envelope_err(repr(exc))

        @tool
        def mark_read(message_id: str) -> str:
            """Mark a message as read."""
            try:
                _bump_organize_counter(message_id, _peek_sender(gmail, message_id))
                return _envelope_ok(
                    mark_read_impl(gmail, db, message_id=message_id, debug=debug_flag)
                )
            except Exception as exc:
                return _envelope_err(repr(exc))

        @tool
        def mark_unread(message_id: str) -> str:
            """Mark a message as unread."""
            try:
                return _envelope_ok(
                    mark_unread_impl(gmail, db, message_id=message_id, debug=debug_flag)
                )
            except Exception as exc:
                return _envelope_err(repr(exc))

        @tool
        def add_star(message_id: str) -> str:
            """Star a message."""
            try:
                return _envelope_ok(
                    add_star_impl(gmail, db, message_id=message_id, debug=debug_flag)
                )
            except Exception as exc:
                return _envelope_err(repr(exc))

        @tool
        def remove_star(message_id: str) -> str:
            """Remove the star from a message."""
            try:
                return _envelope_ok(
                    remove_star_impl(gmail, db, message_id=message_id, debug=debug_flag)
                )
            except Exception as exc:
                return _envelope_err(repr(exc))

        @tool
        def label_message(message_id: str, label_id: str) -> str:
            """Add a label to a message. Pass the label id (e.g. ``Label_1``)."""
            try:
                _bump_organize_counter(message_id, _peek_sender(gmail, message_id))
                return _envelope_ok(
                    label_message_impl(
                        gmail,
                        db,
                        message_id=message_id,
                        label_id=label_id,
                        debug=debug_flag,
                    )
                )
            except Exception as exc:
                return _envelope_err(repr(exc))

        @tool
        def move_to_label(message_id: str, label_id: str) -> str:
            """Move a message out of INBOX into a label."""
            try:
                _bump_organize_counter(message_id, _peek_sender(gmail, message_id))
                return _envelope_ok(
                    move_to_label_impl(
                        gmail,
                        db,
                        message_id=message_id,
                        label_id=label_id,
                        debug=debug_flag,
                    )
                )
            except Exception as exc:
                return _envelope_err(repr(exc))


def _peek_sender(gmail, message_id: str) -> str:
    """Best-effort sender lookup for the batch-threshold counter.

    Failure is non-fatal — the counter degrades to message-id-only
    distinct counts.
    """
    try:
        msg = gmail.get_message(message_id)
        for h in (msg.get("payload") or {}).get("headers", []):
            if (h.get("name") or "").lower() == "from":
                return h.get("value", "")
    except Exception:
        pass
    return ""
