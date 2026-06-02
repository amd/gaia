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
import uuid
from typing import Any, Dict, List, Optional

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


def undo_archive_batch_impl(
    gmail,
    db,
    *,
    batch_id: str,
    window_seconds: int,
    debug: bool = False,
) -> Dict[str, Any]:
    """Reverse a batch archive within the undo window.

    Re-adds the labels that ``archive`` removed (INBOX, plus any other
    label the message carried before) for every still-undoable ``archive``
    row sharing ``batch_id``, then marks each row undone.

    Raises ``RuntimeError`` if the batch has no undoable rows — the window
    expired, every row was already undone, or the batch_id is unknown. We
    fail loudly rather than silently no-op so the caller surfaces it.
    """
    with log_tool_call("undo_archive_batch", {"batch_id": batch_id}, debug=debug) as st:
        rows = action_store.fetch_batch_undoable(
            db, batch_id=batch_id, window_seconds=window_seconds
        )
        if not rows:
            raise RuntimeError(
                f"undo window has expired ({window_seconds} s) or batch_id "
                f"{batch_id!r} has no undoable archive actions. Use Gmail to "
                "move the messages back to the inbox manually."
            )
        restored: List[Dict[str, Any]] = []
        for row in rows:
            if row["action_type"] != "archive":
                # batch_id is archive-only today; skip anything else rather
                # than mis-restore an unrelated action recorded under the
                # same id by a future caller.
                continue
            mid = row["message_id"]
            prior_labels = set(row["payload"].get("prior_labels") or [])
            current = set(gmail.get_message(mid).get("labelIds", []))
            # Archive only ever removes labels (INBOX); re-add whatever the
            # message carried before that it no longer has.
            for lab in prior_labels - current:
                gmail.add_label(mid, lab)
            action_store.mark_undone(db, action_id=row["action_id"])
            restored.append({"message_id": mid, "action_id": row["action_id"]})
        st["result_summary"] = {"restored": len(restored)}
        return {"batch_id": batch_id, "restored": len(restored), "messages": restored}


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


# ---------------------------------------------------------------------------
# Batch helpers — execute a single Gmail API mutation per message id,
# record each action with the shared batch_id, and collect partial results.
# ---------------------------------------------------------------------------


def _coerce_ids(message_ids):
    """Ensure message_ids is a list of strings. LLMs send comma-separated strings."""
    if message_ids is None:
        return []
    if isinstance(message_ids, list):
        return message_ids
    if isinstance(message_ids, str):
        return [
            x.strip() for x in message_ids.replace(";", ",").split(",") if x.strip()
        ]
    return []


def _run_batch(
    _gmail,
    db,
    message_ids: list[str],
    *,
    gmail_op,
    action_type: str,
    payload: dict | None = None,
    batch_id: str,
    debug: bool = False,
) -> dict:
    """Execute a Gmail mutation for each message_id, recording each action.

    Returns ``{"succeeded": [...], "failed": [...]}``.
    """
    succeeded: list[dict] = []
    failed: list[dict] = []
    for mid in message_ids:
        try:
            gmail_op(mid)
            aid = action_store.record_action(
                db,
                action_type=action_type,
                message_id=mid,
                payload=dict(payload or {}),
                batch_id=batch_id,
            )
            succeeded.append({"message_id": mid, "action_id": aid})
        except Exception as exc:
            failed.append({"message_id": mid, "error": f"{type(exc).__name__}: {exc}"})
            if debug:
                log.exception("batch op failed for %s", mid)
    return {"succeeded": succeeded, "failed": failed}


def _run_batch_with_prior(
    gmail,
    db,
    message_ids: list[str],
    *,
    gmail_op,
    action_type: str,
    prior_fn,
    payload_fn,
    batch_id: str,
    debug: bool = False,
) -> dict:
    """Same as _run_batch but fetches per-message prior state first.

    ``prior_fn(msg) -> prior`` is called once per message.
    ``payload_fn(msg, prior) -> dict`` builds the action payload.
    """
    succeeded: list[dict] = []
    failed: list[dict] = []
    for mid in message_ids:
        try:
            msg = gmail.get_message(mid)
            prior = prior_fn(msg)
            gmail_op(mid)
            aid = action_store.record_action(
                db,
                action_type=action_type,
                message_id=mid,
                thread_id=msg.get("threadId"),
                payload=payload_fn(msg, prior),
                batch_id=batch_id,
            )
            succeeded.append({"message_id": mid, "action_id": aid})
        except Exception as exc:
            failed.append({"message_id": mid, "error": f"{type(exc).__name__}: {exc}"})
            if debug:
                log.exception("batch op (with prior) failed for %s", mid)
    return {"succeeded": succeeded, "failed": failed}


class OrganizeToolsMixin:
    def _register_organize_tools(self) -> None:
        gmail = self._gmail
        db = self
        debug_flag = bool(getattr(self.config, "debug", False))
        window = int(getattr(self.config, "undo_window_seconds", 30))
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

        # ---- Batch organize tools (for 3+ messages in one call) ----------

        @tool
        def mark_read_batch(message_ids: list[str]) -> str:
            """Mark multiple messages as read in one call. Use for 3+ messages."""
            if not message_ids:
                return _envelope_ok({"total": 0, "succeeded": [], "failed": []})
            message_ids = _coerce_ids(message_ids)
            if (err := _check_threshold()) is not None:
                return _envelope_err(err)
            try:
                batch_id = uuid.uuid4().hex
                result = _run_batch(
                    gmail,
                    db,
                    message_ids,
                    gmail_op=gmail.mark_read,
                    action_type="mark_read",
                    batch_id=batch_id,
                    debug=debug_flag,
                )
                for _mid in message_ids:
                    agent._record_organize_op(_mid, "")
                return _envelope_ok(
                    {
                        "batch_id": batch_id,
                        "total": len(message_ids),
                        "succeeded": result["succeeded"],
                        "failed": result["failed"],
                    }
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def mark_unread_batch(message_ids: list[str]) -> str:
            """Mark multiple messages as unread in one call. Use for 3+ messages."""
            if not message_ids:
                return _envelope_ok({"total": 0, "succeeded": [], "failed": []})
            message_ids = _coerce_ids(message_ids)
            if (err := _check_threshold()) is not None:
                return _envelope_err(err)
            try:
                batch_id = uuid.uuid4().hex
                result = _run_batch(
                    gmail,
                    db,
                    message_ids,
                    gmail_op=gmail.mark_unread,
                    action_type="mark_unread",
                    batch_id=batch_id,
                    debug=debug_flag,
                )
                for _mid in message_ids:
                    agent._record_organize_op(_mid, "")
                return _envelope_ok(
                    {
                        "batch_id": batch_id,
                        "total": len(message_ids),
                        "succeeded": result["succeeded"],
                        "failed": result["failed"],
                    }
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def add_star_batch(message_ids: list[str]) -> str:
            """Star multiple messages in one call. Use for 3+ messages."""
            if not message_ids:
                return _envelope_ok({"total": 0, "succeeded": [], "failed": []})
            message_ids = _coerce_ids(message_ids)
            if (err := _check_threshold()) is not None:
                return _envelope_err(err)
            try:
                batch_id = uuid.uuid4().hex
                result = _run_batch(
                    gmail,
                    db,
                    message_ids,
                    gmail_op=gmail.add_star,
                    action_type="add_star",
                    batch_id=batch_id,
                    debug=debug_flag,
                )
                for _mid in message_ids:
                    agent._record_organize_op(_mid, "")
                return _envelope_ok(
                    {
                        "batch_id": batch_id,
                        "total": len(message_ids),
                        "succeeded": result["succeeded"],
                        "failed": result["failed"],
                    }
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def remove_star_batch(message_ids: list[str]) -> str:
            """Remove star from multiple messages in one call. Use for 3+ messages."""
            if not message_ids:
                return _envelope_ok({"total": 0, "succeeded": [], "failed": []})
            message_ids = _coerce_ids(message_ids)
            if (err := _check_threshold()) is not None:
                return _envelope_err(err)
            try:
                batch_id = uuid.uuid4().hex
                result = _run_batch(
                    gmail,
                    db,
                    message_ids,
                    gmail_op=gmail.remove_star,
                    action_type="remove_star",
                    batch_id=batch_id,
                    debug=debug_flag,
                )
                for _mid in message_ids:
                    agent._record_organize_op(_mid, "")
                return _envelope_ok(
                    {
                        "batch_id": batch_id,
                        "total": len(message_ids),
                        "succeeded": result["succeeded"],
                        "failed": result["failed"],
                    }
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def archive_message_batch(message_ids: list[str]) -> str:
            """Archive multiple messages (remove from INBOX) in one call. Use for 3+ messages."""
            if not message_ids:
                return _envelope_ok({"total": 0, "succeeded": [], "failed": []})
            message_ids = _coerce_ids(message_ids)
            if (err := _check_threshold()) is not None:
                return _envelope_err(err)
            try:
                batch_id = uuid.uuid4().hex

                def _archive_prior_fn(msg: Dict[str, Any]) -> List[str]:
                    return list(msg.get("labelIds", []))

                def _archive_payload_fn(
                    _msg: Dict[str, Any], prior_labels: List[str]
                ) -> Dict[str, Any]:
                    return {"prior_labels": prior_labels}

                result = _run_batch_with_prior(
                    gmail,
                    db,
                    message_ids,
                    gmail_op=gmail.archive_message,
                    action_type="archive",
                    prior_fn=_archive_prior_fn,
                    payload_fn=_archive_payload_fn,
                    batch_id=batch_id,
                    debug=debug_flag,
                )
                for _mid in message_ids:
                    agent._record_organize_op(_mid, "")
                return _envelope_ok(
                    {
                        "batch_id": batch_id,
                        "total": len(message_ids),
                        "succeeded": result["succeeded"],
                        "failed": result["failed"],
                    }
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def undo_archive_batch(batch_id: str) -> str:
            """Undo a batch archive by its batch_id, restoring every message
            to the inbox within the undo window. Reverses archive_message_batch."""
            try:
                return _envelope_ok(
                    undo_archive_batch_impl(
                        gmail,
                        db,
                        batch_id=batch_id,
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
        def label_message_batch(message_ids: list[str], label_id: str) -> str:
            """Add a label to multiple messages in one call. Use for 3+ messages."""
            if not message_ids:
                return _envelope_ok({"total": 0, "succeeded": [], "failed": []})
            message_ids = _coerce_ids(message_ids)
            if (err := _check_threshold()) is not None:
                return _envelope_err(err)
            try:
                batch_id = uuid.uuid4().hex
                label_id_local = label_id  # closure capture

                def _label_op(mid: str) -> None:
                    gmail.add_label(mid, label_id_local)

                result = _run_batch(
                    gmail,
                    db,
                    message_ids,
                    gmail_op=_label_op,
                    action_type="add_label",
                    payload={"label_id": label_id_local},
                    batch_id=batch_id,
                    debug=debug_flag,
                )
                for _mid in message_ids:
                    agent._record_organize_op(_mid, "")
                return _envelope_ok(
                    {
                        "batch_id": batch_id,
                        "total": len(message_ids),
                        "succeeded": result["succeeded"],
                        "failed": result["failed"],
                    }
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def move_to_label_batch(message_ids: list[str], label_id: str) -> str:
            """Move multiple messages out of INBOX into a label in one call. Use for 3+ messages."""
            if not message_ids:
                return _envelope_ok({"total": 0, "succeeded": [], "failed": []})
            message_ids = _coerce_ids(message_ids)
            if (err := _check_threshold()) is not None:
                return _envelope_err(err)
            try:
                batch_id = uuid.uuid4().hex
                label_id_local = label_id

                def _move_op(mid: str) -> None:
                    gmail.add_label(mid, label_id_local)
                    gmail.archive_message(mid)

                def _move_prior_fn(msg: Dict[str, Any]) -> List[str]:
                    return list(msg.get("labelIds", []))

                def _move_payload_fn(
                    _msg: Dict[str, Any], prior_labels: List[str]
                ) -> Dict[str, Any]:
                    return {
                        "label_id": label_id_local,
                        "prior_labels": prior_labels,
                    }

                result = _run_batch_with_prior(
                    gmail,
                    db,
                    message_ids,
                    gmail_op=_move_op,
                    action_type="move_to_label",
                    prior_fn=_move_prior_fn,
                    payload_fn=_move_payload_fn,
                    batch_id=batch_id,
                    debug=debug_flag,
                )
                for _mid in message_ids:
                    agent._record_organize_op(_mid, "")
                return _envelope_ok(
                    {
                        "batch_id": batch_id,
                        "total": len(message_ids),
                        "succeeded": result["succeeded"],
                        "failed": result["failed"],
                    }
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")
