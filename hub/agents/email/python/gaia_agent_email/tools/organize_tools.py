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

import uuid
from typing import Any, Dict, List, Optional

from gaia_agent_email.tools.envelope import _envelope_err, _envelope_ok
from gaia_agent_email import action_store
from gaia_agent_email.verbose import log_tool_call

from gaia.agents.base.tools import tool
from gaia.connectors.errors import ConnectorsError
from gaia.connectors.formatting import format_connector_error
from gaia.logger import get_logger

log = get_logger(__name__)

# The Gmail system label whose removal defines "archived / out of the inbox".
_INBOX_LABEL = "INBOX"


# ---------------------------------------------------------------------------
# Pure impls
# ---------------------------------------------------------------------------


def archive_message_impl(
    gmail,
    db,
    *,
    message_id: str,
    prior: Optional[Dict[str, Any]] = None,
    mailbox: Optional[str] = None,
    batch_id: Optional[str] = None,
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
        result = gmail.archive_message(message_id)
        # Verify the archive actually took effect before claiming success.
        # Gmail's modify response echoes the post-mutation labelIds; if INBOX
        # is still present the archive silently no-op'd (wrong id resolved or
        # the provider rejected the change) — fail loudly rather than record a
        # false success (#2406). Raising here, before record_action, preserves
        # the no-phantom-undo-row ordering invariant. Folder-based backends
        # (Outlook) return an id-only result with no labelIds; there the
        # returned post-archive id is the confirmation, so skip the label check.
        post_labels = (result or {}).get("labelIds")
        if post_labels is not None and _INBOX_LABEL in post_labels:
            raise RuntimeError(
                f"Archive did not take effect for message {message_id!r}: it is "
                "still in the inbox (INBOX label present after the archive call). "
                "This usually means the wrong message id was resolved or the mail "
                "provider rejected the change. Re-run the search to confirm the "
                "target message, or archive it manually in your mail client."
            )
        # Capture the post-archive id: for folder-based backends (Outlook)
        # the move returns a new id; for label-based backends (Gmail) it
        # equals the pre-archive id.
        post_archive_id = (result or {}).get("id") or message_id
        # ``batch_id`` lets a single archive be undone via ``undo_archive_batch``
        # (the REST surface mints one per archive so the UI gets an undo handle).
        action_id = action_store.record_action(
            db,
            action_type="archive",
            message_id=message_id,
            thread_id=prior.get("threadId"),
            payload={"prior_labels": prior_labels, "post_archive_id": post_archive_id},
            batch_id=batch_id,
            mailbox=mailbox,
        )
        st["result_summary"] = {"action_id": action_id}
        # Surface the identity of the message actually archived so the success
        # message can cite it (id/subject/sender), not just the sender name the
        # user typed (#2406 AC b).
        return {
            "action_id": action_id,
            "message_id": message_id,
            "post_archive_id": post_archive_id,
            "subject": _extract_subject(prior),
            "sender": _extract_sender(prior),
        }


def mark_read_impl(
    gmail, db, *, message_id: str, mailbox: Optional[str] = None, debug: bool = False
) -> Dict[str, Any]:
    with log_tool_call("mark_read", {"message_id": message_id}, debug=debug) as st:
        gmail.mark_read(message_id)
        action_id = action_store.record_action(
            db,
            action_type="mark_read",
            message_id=message_id,
            payload={},
            mailbox=mailbox,
        )
        st["result_summary"] = {"action_id": action_id}
        return {"action_id": action_id, "message_id": message_id}


def mark_unread_impl(
    gmail, db, *, message_id: str, mailbox: Optional[str] = None, debug: bool = False
) -> Dict[str, Any]:
    with log_tool_call("mark_unread", {"message_id": message_id}, debug=debug) as st:
        gmail.mark_unread(message_id)
        action_id = action_store.record_action(
            db,
            action_type="mark_unread",
            message_id=message_id,
            payload={},
            mailbox=mailbox,
        )
        st["result_summary"] = {"action_id": action_id}
        return {"action_id": action_id, "message_id": message_id}


def add_star_impl(
    gmail, db, *, message_id: str, mailbox: Optional[str] = None, debug: bool = False
) -> Dict[str, Any]:
    with log_tool_call("add_star", {"message_id": message_id}, debug=debug) as st:
        gmail.add_star(message_id)
        action_id = action_store.record_action(
            db,
            action_type="add_star",
            message_id=message_id,
            payload={},
            mailbox=mailbox,
        )
        st["result_summary"] = {"action_id": action_id}
        return {"action_id": action_id, "message_id": message_id}


def remove_star_impl(
    gmail, db, *, message_id: str, mailbox: Optional[str] = None, debug: bool = False
) -> Dict[str, Any]:
    with log_tool_call("remove_star", {"message_id": message_id}, debug=debug) as st:
        gmail.remove_star(message_id)
        action_id = action_store.record_action(
            db,
            action_type="remove_star",
            message_id=message_id,
            payload={},
            mailbox=mailbox,
        )
        st["result_summary"] = {"action_id": action_id}
        return {"action_id": action_id, "message_id": message_id}


def _resolve_label_id(
    backend, label: str, cache: Optional[Dict[Any, Dict[str, str]]] = None
) -> str:
    """Resolve a label display name OR id to a concrete label id for ``backend``.

    Gmail user labels are addressed by id (``Label_###``), not display name; the
    modify API rejects a bare name with ``Invalid label: <name>``. ``list_labels``
    returns both id and name, and the model feeds a name back into the apply call
    (#2428) — so accept either: an exact id match passes through (already an id),
    an exact display-name match resolves to its id, and a unique case-insensitive
    match is the last resort (models vary label casing). Anything else fails loud —
    never silently forwarded to the backend, never auto-created (this agent has no
    create-label capability; the caller must reference an existing label).

    ``cache`` (optional) memoizes resolutions **keyed by backend** so a batch that
    reuses one backend hits ``list_labels`` once, not once per message. It MUST be
    backend-keyed, not label-keyed: a mixed Gmail+Outlook batch resolves the same
    name to a Gmail ``Label_###`` for one message and to the Outlook category name
    (id == name) for another — a label-only key would cross-feed the wrong id.
    """
    label = label.strip()
    if cache is not None and backend in cache and label in cache[backend]:
        return cache[backend][label]
    labels = backend.list_labels()
    resolved: Optional[str] = None
    if label in {lb.get("id") for lb in labels}:  # already a valid id
        resolved = label
    else:
        for lb in labels:  # exact display-name match
            if lb.get("name") == label:
                resolved = lb.get("id")
                break
    if resolved is None:  # unique case-insensitive match (tolerate model casing)
        ci = [lb for lb in labels if (lb.get("name") or "").lower() == label.lower()]
        if len(ci) == 1:
            resolved = ci[0].get("id")
    if resolved is None:
        names = sorted({lb.get("name") for lb in labels if lb.get("name")})
        raise ValueError(
            f"Invalid label: {label!r} — no existing label has that id or display "
            f"name. Existing labels: {names}. Applying requires an existing label; "
            "call list_labels to see valid names (this agent cannot create labels)."
        )
    if cache is not None:
        cache.setdefault(backend, {})[label] = resolved
    return resolved


def label_message_impl(
    gmail,
    db,
    *,
    message_id: str,
    label_id: str,
    mailbox: Optional[str] = None,
    debug: bool = False,
) -> Dict[str, Any]:
    with log_tool_call(
        "label_message",
        {"message_id": message_id, "label_id": label_id},
        debug=debug,
    ) as st:
        # Resolve a display name (what the model gets from list_labels) to the
        # id Gmail's modify API requires; record the resolved id for undo (#2428).
        label_id = _resolve_label_id(gmail, label_id)
        gmail.add_label(message_id, label_id)
        action_id = action_store.record_action(
            db,
            action_type="add_label",
            message_id=message_id,
            payload={"label_id": label_id},
            mailbox=mailbox,
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
    mailbox: Optional[str] = None,
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
        # Resolve a display name to the id Gmail requires before any call (#2428).
        label_id = _resolve_label_id(gmail, label_id)
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
            mailbox=mailbox,
        )
        st["result_summary"] = {"action_id": action_id}
        return {"action_id": action_id, "message_id": message_id, "label_id": label_id}


def undo_archive_batch_impl(
    resolve_backend,
    db,
    *,
    batch_id: str,
    window_seconds: int,
    debug: bool = False,
) -> Dict[str, Any]:
    """Reverse a batch archive within the undo window.

    Calls ``backend.unarchive_message`` for each still-undoable ``archive``
    row, which restores it to the inbox in a provider-correct way: Gmail
    re-adds the INBOX label (stable id); Outlook moves the message back from
    the archive folder using the post-archive id recorded at archive time.

    ``resolve_backend(row) -> backend`` routes each row to the mailbox it was
    archived from (#1603 Phase 2), so a cross-mailbox batch undoes against the
    right accounts. Raises ``RuntimeError`` if the batch has no undoable rows —
    the window expired, every row was already undone, or the batch_id is
    unknown. We fail loudly rather than silently no-op so the caller surfaces it.

    Per-row failures are collected and reported but do NOT abort the rest of the
    batch: partial success is preferable to a mid-loop abort that leaves some
    messages stranded.
    """
    with log_tool_call("undo_archive_batch", {"batch_id": batch_id}, debug=debug) as st:
        rows = action_store.fetch_batch_undoable(
            db, batch_id=batch_id, window_seconds=window_seconds
        )
        if not rows:
            raise RuntimeError(
                f"undo window has expired ({window_seconds} s) or batch_id "
                f"{batch_id!r} has no undoable archive actions. Use your mail "
                "client to move the messages back to the inbox manually."
            )
        restored: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []
        for row in rows:
            if row["action_type"] != "archive":
                # batch_id is archive-only today; skip anything else rather
                # than mis-restore an unrelated action recorded under the
                # same id by a future caller.
                continue
            backend = resolve_backend(row)
            mid = row["message_id"]
            prior_labels = list(row["payload"].get("prior_labels") or [])
            # Use the post-archive id so Outlook can find the message after the
            # folder move changed its id.
            restore_id = row["payload"].get("post_archive_id") or mid
            try:
                backend.unarchive_message(restore_id, prior_labels)
            except Exception as exc:
                failed.append(
                    {
                        "message_id": mid,
                        "error": (
                            format_connector_error(exc)
                            if isinstance(exc, ConnectorsError)
                            else f"{type(exc).__name__}: {exc}"
                        ),
                    }
                )
                if debug:
                    log.exception("undo failed for %s", mid)
                continue
            action_store.mark_undone(db, action_id=row["action_id"])
            restored.append({"message_id": mid, "action_id": row["action_id"]})
        if not restored:
            # rows is non-empty (guarded above), so restoring nothing is a loud
            # failure — never return ok with restored=0. Either every row failed,
            # or the batch held no archive rows to restore.
            detail = (
                f"all {len(failed)} row(s) failed to restore. "
                f"First error: {failed[0]['error']}"
                if failed
                else f"no archive rows among the {len(rows)} batch row(s) to restore"
            )
            raise RuntimeError(f"undo_archive_batch: {detail}")
        st["result_summary"] = {"restored": len(restored), "failed": len(failed)}
        return {
            "batch_id": batch_id,
            "restored": len(restored),
            "messages": restored,
            "failed": failed,
        }


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------


def _extract_sender(msg: Dict[str, Any]) -> str:
    """Pull the ``From`` header out of a Gmail-API-shape message."""
    for h in (msg.get("payload") or {}).get("headers", []):
        if (h.get("name") or "").lower() == "from":
            return h.get("value", "")
    return ""


def _extract_subject(msg: Dict[str, Any]) -> str:
    """Pull the ``Subject`` header out of a Gmail-API-shape message."""
    for h in (msg.get("payload") or {}).get("headers", []):
        if (h.get("name") or "").lower() == "subject":
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
    resolve_backend,
    db,
    message_ids: list[str],
    *,
    op_name: str,
    op_args: tuple = (),
    action_type: str,
    action_mailbox=None,
    payload: dict | None = None,
    batch_id: str,
    debug: bool = False,
    arg_resolver=None,
) -> dict:
    """Execute a mailbox mutation for each message_id, recording each action.

    ``resolve_backend(mid) -> backend`` routes each id to its own mailbox
    (#1603 Phase 2), so a batch can span Gmail and Outlook. ``op_name`` is the
    backend method to call (e.g. ``"mark_read"``); ``op_args`` are trailing
    positional args (e.g. the label id). ``action_mailbox(mid) -> str`` records
    which mailbox the action hit so undo routes correctly.

    ``arg_resolver(backend) -> (op_args, payload)`` (optional) computes the
    positional args and action payload per backend — used by the label batches to
    resolve a display name to each provider's label id (#2428). When omitted, the
    static ``op_args``/``payload`` are used unchanged (the mark/star/archive
    batches are untouched).

    Returns ``{"succeeded": [...], "failed": [...]}``.
    """
    succeeded: list[dict] = []
    failed: list[dict] = []
    for mid in message_ids:
        try:
            backend = resolve_backend(mid)
            if arg_resolver is not None:
                op_args_eff, payload_eff = arg_resolver(backend)
            else:
                op_args_eff, payload_eff = op_args, dict(payload or {})
            getattr(backend, op_name)(mid, *op_args_eff)
            aid = action_store.record_action(
                db,
                action_type=action_type,
                message_id=mid,
                payload=dict(payload_eff),
                batch_id=batch_id,
                mailbox=action_mailbox(mid) if action_mailbox else None,
            )
            succeeded.append({"message_id": mid, "action_id": aid})
        except Exception as exc:
            failed.append({"message_id": mid, "error": f"{type(exc).__name__}: {exc}"})
            if debug:
                log.exception("batch op failed for %s", mid)
    return {"succeeded": succeeded, "failed": failed}


def _run_batch_with_prior(
    resolve_backend,
    db,
    message_ids: list[str],
    *,
    backend_op,
    action_type: str,
    action_mailbox=None,
    prior_fn,
    payload_fn,
    batch_id: str,
    debug: bool = False,
) -> dict:
    """Same as _run_batch but fetches per-message prior state first.

    ``resolve_backend(mid) -> backend`` routes per message. ``backend_op(backend,
    mid) -> result`` performs the mutation on the resolved backend. ``prior_fn(msg)
    -> prior`` is called once per message; ``payload_fn(msg, prior, op_result) ->
    dict`` builds the action payload (op_result is the backend_op return value).
    """
    succeeded: list[dict] = []
    failed: list[dict] = []
    for mid in message_ids:
        try:
            backend = resolve_backend(mid)
            msg = backend.get_message(mid)
            prior = prior_fn(msg)
            op_result = backend_op(backend, mid)
            aid = action_store.record_action(
                db,
                action_type=action_type,
                message_id=mid,
                thread_id=msg.get("threadId"),
                payload=payload_fn(msg, prior, op_result),
                batch_id=batch_id,
                mailbox=action_mailbox(mid) if action_mailbox else None,
            )
            succeeded.append({"message_id": mid, "action_id": aid})
        except Exception as exc:
            failed.append({"message_id": mid, "error": f"{type(exc).__name__}: {exc}"})
            if debug:
                log.exception("batch op (with prior) failed for %s", mid)
    return {"succeeded": succeeded, "failed": failed}


class OrganizeToolsMixin:
    def _register_organize_tools(self) -> None:
        db = self
        debug_flag = bool(getattr(self.config, "debug", False))
        window = int(getattr(self.config, "undo_window_seconds", 30))
        agent = self  # batch-threshold counter + per-message backend routing

        def _check_threshold() -> Optional[str]:
            """Return error message if Phase I3 threshold is exceeded, else None.

            Called BEFORE each organize op so the offending op never
            actually fires. The counter has already been bumped for
            prior ops in the turn.
            """
            if agent._organize_batch_threshold_exceeded():
                return _BATCH_THRESHOLD_ERROR
            return None

        # Per-message backend routing for batch tools (#1603 Phase 2). A batch's
        # ids may span mailboxes; each is routed to the mailbox it came from.
        def _batch_backend(mid: str):
            return agent._backend_for_message(mid)

        def _batch_provider(mid: str) -> str:
            return agent._provider_for_message(mid)

        @tool
        def archive_message(message_id: str, mailbox: str = "") -> str:
            """Archive a message (remove from INBOX). Reversible via restore_message.

            ``mailbox`` (optional) names the source mailbox ('google' /
            'microsoft') from triage output so the action routes correctly when
            multiple mailboxes are connected.
            """
            try:
                if (err := _check_threshold()) is not None:
                    return _envelope_err(err)
                provider = agent._provider_for_message(message_id, mailbox or None)
                backend = agent._backends[provider]
                # Single fetch — used for both prior_labels (impl) and sender
                # (counter). Avoids a redundant round-trip.
                prior = backend.get_message(message_id)
                agent._record_organize_op(message_id, _extract_sender(prior))
                return _envelope_ok(
                    archive_message_impl(
                        backend,
                        db,
                        message_id=message_id,
                        prior=prior,
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
        def mark_read(message_id: str, mailbox: str = "") -> str:
            """Mark a message as read. ``mailbox`` routes when multiple connected."""
            try:
                if (err := _check_threshold()) is not None:
                    return _envelope_err(err)
                provider = agent._provider_for_message(message_id, mailbox or None)
                # mark_read does not need prior_labels; only bump
                # the counter with what we know — the message_id
                # alone is enough since distinct-sender counting
                # treats unknown senders as "" (one bucket).
                agent._record_organize_op(message_id, "")
                return _envelope_ok(
                    mark_read_impl(
                        agent._backends[provider],
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
        def mark_unread(message_id: str, mailbox: str = "") -> str:
            """Mark a message as unread. ``mailbox`` routes when multiple connected."""
            try:
                if (err := _check_threshold()) is not None:
                    return _envelope_err(err)
                provider = agent._provider_for_message(message_id, mailbox or None)
                agent._record_organize_op(message_id, "")
                return _envelope_ok(
                    mark_unread_impl(
                        agent._backends[provider],
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
        def add_star(message_id: str, mailbox: str = "") -> str:
            """Star a message. ``mailbox`` routes when multiple connected."""
            try:
                if (err := _check_threshold()) is not None:
                    return _envelope_err(err)
                provider = agent._provider_for_message(message_id, mailbox or None)
                agent._record_organize_op(message_id, "")
                return _envelope_ok(
                    add_star_impl(
                        agent._backends[provider],
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
        def remove_star(message_id: str, mailbox: str = "") -> str:
            """Remove the star from a message. ``mailbox`` routes when multiple connected."""
            try:
                if (err := _check_threshold()) is not None:
                    return _envelope_err(err)
                provider = agent._provider_for_message(message_id, mailbox or None)
                agent._record_organize_op(message_id, "")
                return _envelope_ok(
                    remove_star_impl(
                        agent._backends[provider],
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
        def label_message(message_id: str, label_id: str, mailbox: str = "") -> str:
            """Add a label to a message.

            ``label_id`` may be the label's display name (e.g. ``Newsletters``, as
            returned by ``list_labels``) or its id (e.g. ``Label_1``); the name is
            resolved to an id automatically. ``mailbox`` (optional) routes when
            multiple mailboxes are connected.
            """
            try:
                if (err := _check_threshold()) is not None:
                    return _envelope_err(err)
                provider = agent._provider_for_message(message_id, mailbox or None)
                agent._record_organize_op(message_id, "")
                return _envelope_ok(
                    label_message_impl(
                        agent._backends[provider],
                        db,
                        message_id=message_id,
                        label_id=label_id,
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
        def move_to_label(message_id: str, label_id: str, mailbox: str = "") -> str:
            """Move a message out of INBOX into a label.

            ``label_id`` may be the label's display name or its id; the name is
            resolved to an id automatically. ``mailbox`` (optional) routes when
            multiple mailboxes are connected.
            """
            try:
                if (err := _check_threshold()) is not None:
                    return _envelope_err(err)
                provider = agent._provider_for_message(message_id, mailbox or None)
                backend = agent._backends[provider]
                # Single fetch — for prior_labels + sender.
                prior = backend.get_message(message_id)
                agent._record_organize_op(message_id, _extract_sender(prior))
                return _envelope_ok(
                    move_to_label_impl(
                        backend,
                        db,
                        message_id=message_id,
                        label_id=label_id,
                        prior=prior,
                        mailbox=provider,
                        debug=debug_flag,
                    )
                )
            except ConnectorsError as exc:
                return _envelope_err(format_connector_error(exc))
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
                    _batch_backend,
                    db,
                    message_ids,
                    op_name="mark_read",
                    action_type="mark_read",
                    action_mailbox=_batch_provider,
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
                return _envelope_err(format_connector_error(exc))
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
                    _batch_backend,
                    db,
                    message_ids,
                    op_name="mark_unread",
                    action_type="mark_unread",
                    action_mailbox=_batch_provider,
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
                return _envelope_err(format_connector_error(exc))
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
                    _batch_backend,
                    db,
                    message_ids,
                    op_name="add_star",
                    action_type="add_star",
                    action_mailbox=_batch_provider,
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
                return _envelope_err(format_connector_error(exc))
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
                    _batch_backend,
                    db,
                    message_ids,
                    op_name="remove_star",
                    action_type="remove_star",
                    action_mailbox=_batch_provider,
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
                return _envelope_err(format_connector_error(exc))
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
                    _msg: Dict[str, Any],
                    prior_labels: List[str],
                    op_result: Optional[Dict[str, Any]],
                ) -> Dict[str, Any]:
                    # Record the post-archive id so undo can find the message even
                    # when the backend changed its id (Outlook folder-move semantics).
                    post_id = (op_result or {}).get("id") or _msg["id"]
                    return {"prior_labels": prior_labels, "post_archive_id": post_id}

                result = _run_batch_with_prior(
                    _batch_backend,
                    db,
                    message_ids,
                    backend_op=lambda backend, mid: backend.archive_message(mid),
                    action_type="archive",
                    action_mailbox=_batch_provider,
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
                return _envelope_err(format_connector_error(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def undo_archive_batch(batch_id: str) -> str:
            """Undo a batch archive by its batch_id, restoring every message
            to the inbox within the undo window. Reverses archive_message_batch."""
            try:
                result = undo_archive_batch_impl(
                    agent._backend_for_action,
                    db,
                    batch_id=batch_id,
                    window_seconds=window,
                    debug=debug_flag,
                )
                # Learning loop: an undone auto-archive is a correction. Attribute
                # each restored action back to its trust scope (no-op for
                # user-initiated archives, which were never indexed as autonomy).
                capture = getattr(agent, "note_action_undone", None)
                if capture is not None:
                    for entry in result.get("messages", []):
                        action_id = entry.get("action_id")
                        if action_id:
                            capture(action_id)
                return _envelope_ok(result)
            except ConnectorsError as exc:
                return _envelope_err(format_connector_error(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def label_message_batch(message_ids: list[str], label_id: str) -> str:
            """Add a label to multiple messages in one call. Use for 3+ messages.

            ``label_id`` may be a label display name (e.g. ``Newsletters``) or an
            id (e.g. ``Label_1``); the name is resolved to each message's provider
            id automatically.
            """
            if not message_ids:
                return _envelope_ok({"total": 0, "succeeded": [], "failed": []})
            message_ids = _coerce_ids(message_ids)
            if (err := _check_threshold()) is not None:
                return _envelope_err(err)
            try:
                batch_id = uuid.uuid4().hex
                label_id_local = label_id  # closure capture
                label_cache: dict = {}  # backend-keyed memo (see _resolve_label_id)

                def _label_resolver(backend):
                    resolved = _resolve_label_id(backend, label_id_local, label_cache)
                    return (resolved,), {"label_id": resolved}

                result = _run_batch(
                    _batch_backend,
                    db,
                    message_ids,
                    op_name="add_label",
                    action_type="add_label",
                    action_mailbox=_batch_provider,
                    batch_id=batch_id,
                    debug=debug_flag,
                    arg_resolver=_label_resolver,
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
                return _envelope_err(format_connector_error(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def move_to_label_batch(message_ids: list[str], label_id: str) -> str:
            """Move multiple messages out of INBOX into a label in one call. Use for 3+ messages.

            ``label_id`` may be a label display name or an id; the name is
            resolved to each message's provider id automatically.
            """
            if not message_ids:
                return _envelope_ok({"total": 0, "succeeded": [], "failed": []})
            message_ids = _coerce_ids(message_ids)
            if (err := _check_threshold()) is not None:
                return _envelope_err(err)
            try:
                batch_id = uuid.uuid4().hex
                label_id_local = label_id
                label_cache: dict = {}  # backend-keyed memo (see _resolve_label_id)

                def _move_op(backend, mid: str) -> str:
                    resolved = _resolve_label_id(backend, label_id_local, label_cache)
                    backend.add_label(mid, resolved)
                    backend.archive_message(mid)
                    return resolved

                def _move_prior_fn(msg: Dict[str, Any]) -> List[str]:
                    return list(msg.get("labelIds", []))

                def _move_payload_fn(
                    _msg: Dict[str, Any],
                    prior_labels: List[str],
                    op_result: Optional[str] = None,
                ) -> Dict[str, Any]:
                    return {
                        "label_id": op_result or label_id_local,
                        "prior_labels": prior_labels,
                    }

                result = _run_batch_with_prior(
                    _batch_backend,
                    db,
                    message_ids,
                    backend_op=_move_op,
                    action_type="move_to_label",
                    action_mailbox=_batch_provider,
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
                return _envelope_err(format_connector_error(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")
