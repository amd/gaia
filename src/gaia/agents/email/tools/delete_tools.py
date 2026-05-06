# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Delete tools — soft-delete (trash) with undo, and permanent_delete.

``trash_message`` records the action; ``restore_message(action_id)``
within the undo window calls ``untrash_message`` and marks the row as
undone. After the window, restore raises with an actionable message.

``permanent_delete`` is registered in TOOLS_REQUIRING_CONFIRMATION at
the agent level — it never auto-executes.
"""

from __future__ import annotations

import json
from typing import Any, Dict

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


def trash_message_impl(
    gmail, db, *, message_id: str, debug: bool = False
) -> Dict[str, Any]:
    with log_tool_call("trash_message", {"message_id": message_id}, debug=debug) as st:
        prior = gmail.get_message(message_id)
        prior_labels = list(prior.get("labelIds", []))
        gmail.trash_message(message_id)
        action_id = action_store.record_action(
            db,
            action_type="trash",
            message_id=message_id,
            thread_id=prior.get("threadId"),
            payload={"prior_labels": prior_labels},
        )
        st["result_summary"] = {"action_id": action_id}
        return {"action_id": action_id, "message_id": message_id}


def restore_message_impl(
    gmail, db, *, action_id: str, window_seconds: int, debug: bool = False
) -> Dict[str, Any]:
    with log_tool_call("restore_message", {"action_id": action_id}, debug=debug) as st:
        action = action_store.fetch_undoable(
            db, action_id=action_id, window_seconds=window_seconds
        )
        if action is None:
            raise RuntimeError(
                f"undo window has expired ({window_seconds} s) or action_id "
                f"{action_id!r} is unknown. Use Gmail's Trash to recover the "
                "message manually within 30 days, or use permanent_delete to "
                "fully remove it."
            )
        if action["action_type"] != "trash":
            raise RuntimeError(
                f"restore_message only undoes trash actions; got "
                f"{action['action_type']!r}"
            )
        gmail.untrash_message(action["message_id"])
        action_store.mark_undone(db, action_id=action_id)
        st["result_summary"] = {"restored_message_id": action["message_id"]}
        return {
            "action_id": action_id,
            "message_id": action["message_id"],
            "restored": True,
        }


def permanent_delete_impl(
    gmail, db, *, message_id: str, debug: bool = False
) -> Dict[str, Any]:
    with log_tool_call(
        "permanent_delete", {"message_id": message_id}, debug=debug
    ) as st:
        gmail.permanent_delete(message_id)
        # Record AFTER the irrecoverable action — the row is for audit,
        # not undo (there is no undo for permanent_delete).
        action_id = action_store.record_action(
            db,
            action_type="permanent_delete",
            message_id=message_id,
            payload={"irreversible": True},
        )
        st["result_summary"] = {"action_id": action_id}
        return {
            "action_id": action_id,
            "message_id": message_id,
            "irreversible": True,
        }


class DeleteToolsMixin:
    def _register_delete_tools(self) -> None:
        gmail = self._gmail
        db = self
        debug_flag = bool(getattr(self.config, "debug", False))
        window = int(getattr(self.config, "undo_window_seconds", 30))

        @tool
        def trash_message(message_id: str) -> str:
            """Move to Trash. Reversible via restore_message inside the undo window."""
            try:
                return _envelope_ok(
                    trash_message_impl(
                        gmail, db, message_id=message_id, debug=debug_flag
                    )
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def restore_message(action_id: str) -> str:
            """Restore a recently-trashed message by action_id."""
            try:
                return _envelope_ok(
                    restore_message_impl(
                        gmail,
                        db,
                        action_id=action_id,
                        window_seconds=window,
                        debug=debug_flag,
                    )
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def permanent_delete(message_id: str) -> str:
            """PERMANENTLY delete a message. Irreversible. Requires user confirmation."""
            try:
                return _envelope_ok(
                    permanent_delete_impl(
                        gmail, db, message_id=message_id, debug=debug_flag
                    )
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")
