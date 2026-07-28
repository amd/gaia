# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Delete tools — soft-delete (trash) with two restore paths.

``trash_message`` records the action. Two ways back out of Trash:

- ``restore_message(action_id)`` — fast path, only valid within the short
  undo window right after ``trash_message`` returns its ``action_id``.
- ``restore_trashed_message(message_id)`` — reconciles with live mailbox
  state instead: works any time the message is still in Trash, no window,
  no action_id required. ``search_trash`` finds the message_id when the
  caller doesn't already have it (#2523).

``permanent_delete`` is NOT registered as an agent tool (#2533): Google
gates ``DELETE /messages/{id}`` behind the ``https://mail.google.com/``
scope, which GAIA deliberately never requests (it would grant full-mailbox
delete access for one rare operation) — so the tool could never succeed.
``permanent_delete_impl`` still exists for direct/test use against a
backend that actually supports it (e.g. the fake); ``LiveGmailBackend``'s
implementation fails loud naming the missing scope instead of leaking a
raw 403 (see ``gmail_backend.py``).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from gaia.agents.base.tools import tool
from gaia_agent_email.tools.envelope import _envelope_err, _envelope_ok
from gaia_agent_email.tools.read_tools import (
    NO_MAILBOX_CONNECTED_MESSAGE,
    _format_message_for_llm,
)
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
                f"{action_id!r} is unknown. The message is still recoverable "
                "if it is in Trash — call restore_trashed_message(message_id) "
                "instead (use search_trash first if you don't have the "
                "message_id); Gmail keeps Trash for 30 days."
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


def find_trashed_messages_impl(
    gmail, *, query: Optional[str] = None, max_results: int = 25, debug: bool = False
) -> Dict[str, Any]:
    """List (optionally filtered) messages currently in Trash.

    Scoped to the TRASH label directly — ``search_messages`` defaults to
    INBOX and has no way to reach Trash. Lets the agent find a trashed
    message's id (for ``restore_trashed_message``) without an action_id,
    e.g. once the undo window has passed or in a brand new session (#2523).
    """
    with log_tool_call(
        "search_trash", {"query": query, "max_results": max_results}, debug=debug
    ) as st:
        listing = gmail.list_messages(
            label_ids=["TRASH"], query=query or None, max_results=max_results
        )
        stubs = listing.get("messages", [])
        out = [_format_message_for_llm(gmail.get_message(s["id"])) for s in stubs]
        st["result_summary"] = {"count": len(out)}
        return {"messages": out}


def restore_trashed_message_impl(
    gmail, db, *, message_id: str, mailbox: Optional[str] = None, debug: bool = False
) -> Dict[str, Any]:
    """Restore a message from Trash by reconciling with live mailbox state.

    Unlike ``restore_message_impl`` (action_id + a short undo window), this
    works any time the message is actually in Trash right now — no action
    row, no window, no expiring action_id to hold onto. Fixes #2523: the
    agent's only prior restore path stopped working once the undo window
    elapsed even though Gmail keeps trashed mail recoverable for 30 days.

    Fails loud (not a silent no-op) when the message is not currently in
    Trash, so a stale/wrong message_id never reads back as a successful
    restore.
    """
    with log_tool_call(
        "restore_trashed_message", {"message_id": message_id}, debug=debug
    ) as st:
        current = gmail.get_message(message_id)
        labels = set(current.get("labelIds", []))
        if "TRASH" not in labels:
            raise RuntimeError(
                f"message {message_id!r} is not in Trash right now (current "
                f"labels: {sorted(labels) or ['none']}) — nothing to restore. "
                "Use search_trash to find a message that is actually in Trash."
            )
        gmail.untrash_message(message_id)
        action_id = action_store.record_action(
            db,
            action_type="restore_trashed",
            message_id=message_id,
            thread_id=current.get("threadId"),
            payload={"restored_from": "trash"},
            mailbox=mailbox,
        )
        st["result_summary"] = {"action_id": action_id}
        return {
            "action_id": action_id,
            "message_id": message_id,
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
            """Move a message to Trash. This is NOT archive — always tell the
            user "moved to Trash", never "archived"; they are different
            actions with different recoverability.

            Reversible any time the message is still in Trash: call
            restore_trashed_message(message_id) (use search_trash first if
            you don't have the id). restore_message(action_id) is a faster
            shortcut that only works for a short window right after this call.

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
            """Restore a recently-trashed message by action_id. Fast path —
            only valid for a short window right after trash_message returns
            this id. Once that window has passed, or if you never had the
            action_id (new session, bulk triage, etc.), use search_trash to
            find the message and restore_trashed_message(message_id) instead
            — that one works any time the message is still in Trash.
            """
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
        def restore_trashed_message(message_id: str, mailbox: str = "") -> str:
            """Restore a message from Trash back to the inbox, any time.

            Works as long as the message is still in Trash right now — no
            undo window, no action_id required — unlike restore_message,
            which only works for a short window right after trash_message.
            Use search_trash first if you don't already have the message_id.

            ``mailbox`` (optional) names the source mailbox for routing when
            multiple are connected (see ``trash_message``).
            """
            try:
                provider = agent._provider_for_message(message_id, mailbox or None)
                backend = agent._backends[provider]
                return _envelope_ok(
                    restore_trashed_message_impl(
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
        def search_trash(query: str = "", max_results: int = 25) -> str:
            """Find messages currently in Trash, across ALL connected mailboxes.

            Use this to locate a trashed message's id before calling
            restore_trashed_message — e.g. the undo window from trash_message
            has passed, you don't have the action_id, or you're in a new
            session. Leave query empty to list everything in Trash. query
            uses the same syntax as search_messages (from:, subject:,
            is:unread, ...); unlike search_messages this never falls back to
            INBOX — it is always scoped to Trash.
            """
            try:
                bounded = max(1, min(int(max_results or 25), 100))
                backends = agent._backends
                if not backends:
                    return _envelope_err(NO_MAILBOX_CONNECTED_MESSAGE)
                per_backend = max(1, bounded // len(backends))
                merged: List[Dict[str, Any]] = []
                mailbox_errors: List[Dict[str, Any]] = []
                for provider, backend in backends.items():
                    if len(merged) >= bounded:
                        break
                    try:
                        result = find_trashed_messages_impl(
                            backend,
                            query=query or None,
                            max_results=per_backend,
                            debug=debug_flag,
                        )
                    except ConnectorsError as exc:
                        msg = format_connector_error(exc)
                        mailbox_errors.append({"mailbox": provider, "error": msg})
                        log.warning(
                            "email search_trash: skipping %s mailbox — %s",
                            provider,
                            msg,
                        )
                        continue
                    for msg_dict in result.get("messages", []):
                        msg_dict["mailbox"] = provider
                        agent._remember_message_mailbox(msg_dict.get("id"), provider)
                        agent._remember_message_mailbox(
                            msg_dict.get("thread_id"), provider
                        )
                        merged.append(msg_dict)
                if mailbox_errors and len(mailbox_errors) == len(backends):
                    # Every connected mailbox failed — surface it loudly rather
                    # than returning ok with zero results (reads as "empty Trash").
                    raise ConnectorsError(
                        "search_trash failed on every connected mailbox: "
                        + "; ".join(
                            f"{e['mailbox']}: {e['error']}" for e in mailbox_errors
                        )
                    )
                envelope: Dict[str, Any] = {"messages": merged[:bounded]}
                if mailbox_errors:
                    envelope["mailbox_errors"] = mailbox_errors
                return _envelope_ok(envelope)
            except ConnectorsError as exc:
                return _envelope_err(format_connector_error(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")
