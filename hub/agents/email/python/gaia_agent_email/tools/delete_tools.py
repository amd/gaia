# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Delete tools — soft-delete (trash) with undo, and permanent_delete.

``trash_message`` records the action; ``restore_message(action_id)``
within the undo window calls ``untrash_message`` and marks the row as
undone. After the window, restore raises with an actionable message.

``permanent_delete`` is declared in the agent's
``CONFIRMATION_REQUIRED_TOOLS`` (merged with the generic base set via
``confirmation_required_tools()``, #1440) — it never auto-executes.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from gaia.agents.base.tools import tool
from gaia_agent_email.tools.envelope import _envelope_err, _envelope_ok
from gaia_agent_email import action_store
from gaia_agent_email.verbose import log_tool_call
from gaia.connectors.errors import ConnectorsError
from gaia.connectors.formatting import format_connector_error
from gaia.logger import get_logger

log = get_logger(__name__)


def trash_message_impl(
    gmail, db, *, message_id: str, mailbox: Optional[str] = None, debug: bool = False
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
            mailbox=mailbox,
        )
        st["result_summary"] = {"action_id": action_id}
        return {"action_id": action_id, "message_id": message_id}


def restore_message_impl(
    resolve_backend, db, *, action_id: str, window_seconds: int, debug: bool = False
) -> Dict[str, Any]:
    """Undo a trash within the window, routing to the message's own mailbox.

    ``resolve_backend(action: dict) -> backend`` picks the backend for the
    fetched action row (#1603 Phase 2) — the row records which mailbox the
    message belongs to, so undo never untrashes against the wrong account when
    multiple mailboxes are connected.
    """
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
        backend = resolve_backend(action)
        backend.untrash_message(action["message_id"])
        action_store.mark_undone(db, action_id=action_id)
        st["result_summary"] = {"restored_message_id": action["message_id"]}
        return {
            "action_id": action_id,
            "message_id": action["message_id"],
            "restored": True,
        }


def permanent_delete_impl(
    gmail, db, *, message_id: str, mailbox: Optional[str] = None, debug: bool = False
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
            mailbox=mailbox,
        )
        st["result_summary"] = {"action_id": action_id}
        return {
            "action_id": action_id,
            "message_id": message_id,
            "irreversible": True,
        }


class DeleteToolsMixin:
    def _register_delete_tools(self) -> None:
        db = self
        agent = self  # for per-message backend routing (#1603 Phase 2)
        debug_flag = bool(getattr(self.config, "debug", False))
        window = int(getattr(self.config, "undo_window_seconds", 120))

        @tool
        def trash_message(message_id: str, mailbox: str = "") -> str:
            """Move to Trash. Reversible via restore_message inside the undo window.

            ``mailbox`` (optional) names the source mailbox ('google' or
            'microsoft') from triage output, so the action routes correctly when
            multiple mailboxes are connected. Omit it when only one is connected
            or the message was already tagged by triage.
            """
            try:
                provider = agent._provider_for_message(message_id, mailbox or None)
                backend = agent._backends[provider]
                return _envelope_ok(
                    trash_message_impl(
                        backend,
                        db,
                        message_id=message_id,
                        mailbox=provider,
                        debug=debug_flag,
                    )
                )
            except ConnectorsError as exc:
                return _envelope_err(format_connector_error(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def restore_message(action_id: str) -> str:
            """Restore a recently-trashed message by action_id."""
            try:
                return _envelope_ok(
                    restore_message_impl(
                        agent._backend_for_action,
                        db,
                        action_id=action_id,
                        window_seconds=window,
                        debug=debug_flag,
                    )
                )
            except ConnectorsError as exc:
                return _envelope_err(format_connector_error(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def permanent_delete(message_id: str, mailbox: str = "") -> str:
            """PERMANENTLY delete a message. Irreversible. Requires user confirmation.

            ``mailbox`` (optional) names the source mailbox for routing when
            multiple are connected (see ``trash_message``).
            """
            try:
                provider = agent._provider_for_message(message_id, mailbox or None)
                backend = agent._backends[provider]
                return _envelope_ok(
                    permanent_delete_impl(
                        backend,
                        db,
                        message_id=message_id,
                        mailbox=provider,
                        debug=debug_flag,
                    )
                )
            except ConnectorsError as exc:
                return _envelope_err(format_connector_error(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")
