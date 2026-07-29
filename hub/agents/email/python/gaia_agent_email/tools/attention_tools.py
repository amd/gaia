# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Attention-view read-model aggregation (#2582).

Merges four signals that each already exist as their own read-only tool into
one "what needs you" view, rendered without a user prompt when the email
agent opens:

- inbound waiting-on-you items (``detect_waiting_on_you_impl``, #2581)
- meeting proposals found during the scan (``triage_inbox_impl``'s
  ``is_meeting_request``, #2583)
- unreviewed messages the heuristic was not confident about
  (``needs_review_decision``, #2584)
- open action items from prior triage (``task_store``, #2110/#2525)

Calls the underlying tools directly rather than deriving from the pre-scan
envelope (``EmailPreScanResult``): that envelope's ``informational_count`` is
a bare count with no per-message rows, so a meeting proposal in a
confidently-classified informational message (e.g. a Gmail CATEGORY_UPDATES
label) would be silently invisible if this view were built on top of it
instead of the raw ``triage_inbox_impl`` scan. Measured on a real mailbox:
that bucket was 98 of 100 messages in the largest run.

Read-only throughout: every call here is a list/get, never a mutation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from gaia_agent_email.tools.read_tools import (
    _fetch_total_unread,
    needs_review_decision,
    triage_inbox_impl,
)
from gaia_agent_email.tools.waiting_on_you_tools import (
    DEFAULT_MAX_INBOX_SCAN as _WAITING_ON_YOU_DEFAULT_MAX_INBOX,
)
from gaia_agent_email.tools.waiting_on_you_tools import (
    detect_waiting_on_you_impl,
)

from gaia.connectors.errors import ConnectorsError
from gaia.connectors.formatting import format_connector_error
from gaia.logger import get_logger

log = get_logger(__name__)

# How many INBOX messages to scan for meeting proposals / needs-review per
# mailbox. Larger than the pre-scan default (25) because this view's whole
# point is to catch what a smaller scan would miss — but still bounded, per
# #2581's MAX_INBOX_SCAN_CAP precedent, so a caller can't request an
# unbounded mailbox sweep.
DEFAULT_ATTENTION_SCAN_MESSAGES = 100
MAX_ATTENTION_SCAN_MESSAGES = 200


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _needs_review_item(r: Mapping[str, Any], provider: Optional[str]) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "kind": "needs_review",
        "message_id": r.get("id"),
        "thread_id": r.get("thread_id"),
        "sender": r.get("from", ""),
        "subject": r.get("subject", ""),
        "why": r.get("rationale")
        or "the heuristic was not confident about this message's category",
    }
    if provider:
        item["mailbox"] = provider
    return item


def _meeting_item(r: Mapping[str, Any], provider: Optional[str]) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "kind": "meeting_request",
        "message_id": r.get("id"),
        "thread_id": r.get("thread_id"),
        "sender": r.get("from", ""),
        "subject": r.get("subject", ""),
        "why": "looks like it's proposing a meeting or a time to talk",
    }
    if provider:
        item["mailbox"] = provider
    return item


def _waiting_on_you_item(
    w: Mapping[str, Any], provider: Optional[str]
) -> Dict[str, Any]:
    age_days = w.get("age_days", 0)
    item: Dict[str, Any] = {
        "kind": "waiting_on_you",
        "message_id": w.get("message_id"),
        "thread_id": w.get("thread_id"),
        "sender": w.get("sender", ""),
        "subject": w.get("subject", ""),
        "why": f"waiting {age_days}d on your reply",
    }
    if provider:
        item["mailbox"] = provider
    return item


def _action_item(task: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "kind": "action_item",
        "message_id": task.get("message_id"),
        "thread_id": None,
        "sender": "",
        "subject": task.get("description", ""),
        "why": "open action item from a previous triage",
        "due_hint": task.get("due_hint"),
    }


def _scan_one_backend(
    backend: Any,
    *,
    provider: Optional[str],
    max_messages: int,
    waiting_on_you_max_inbox: int,
    debug: bool,
) -> Dict[str, Any]:
    """Run the triage scan + waiting-on-you detection against one mailbox.

    Returns a dict with ``items``, ``scanned``, ``total_unread``, and
    ``scan_truncated``. Raises whatever the backend raises (``ConnectorsError``
    family) — the caller decides whether that's a per-mailbox partial failure
    or a total one.
    """
    triage = triage_inbox_impl(
        backend, max_messages=max_messages, label_ids=["INBOX"], debug=debug
    )
    results = triage["results"]

    items: List[Dict[str, Any]] = []
    for r in results:
        if r.get("is_spam") or r.get("is_phishing"):
            # Out of scope for this view — the pre-scan card already
            # surfaces spam/phishing as an actionable warning; duplicating
            # that here would just be noise.
            continue
        if r.get("is_meeting_request"):
            items.append(_meeting_item(r, provider))
        elif needs_review_decision(r):
            items.append(_needs_review_item(r, provider))

    waiting_budget = waiting_on_you_max_inbox or _WAITING_ON_YOU_DEFAULT_MAX_INBOX
    waiting = detect_waiting_on_you_impl(backend, max_inbox=waiting_budget, debug=debug)
    for w in waiting["waiting_on_you"]:
        items.append(_waiting_on_you_item(w, provider))

    return {
        "items": items,
        "scanned": len(results),
        "total_unread": _fetch_total_unread(backend),
        # Mirrors detect_waiting_on_you_impl's own truncation heuristic: a
        # scan that returned exactly the ceiling likely didn't see everything.
        "scan_truncated": len(results) >= max_messages
        or bool(waiting.get("scan_truncated")),
    }


def build_attention_view_impl(
    backends: Mapping[str, Any],
    *,
    max_messages: int = DEFAULT_ATTENTION_SCAN_MESSAGES,
    waiting_on_you_max_inbox: int = 0,
    action_db: Any = None,
    debug: bool = False,
) -> Dict[str, Any]:
    """Aggregate the attention-view read-model across every connected mailbox.

    ``action_db`` is an optional ``DatabaseMixin``-compatible handle (see
    ``gaia_agent_email.task_store``); when given, open action items from
    prior triage are included. When omitted, the action_item signal is
    simply absent — this function never fails because the task store wasn't
    wired in.

    Raises ``ConnectorsError`` when EVERY connected mailbox fails (mirroring
    ``merge_pre_scan_backends``' own rule) rather than returning an envelope
    that reads as "nothing needs you" when nothing could actually be scanned.
    A partial failure (some mailboxes ok, others not) is recorded in
    ``coverage.mailbox_errors`` / ``coverage.degraded`` instead, with the
    surviving mailboxes' results still returned.
    """
    if not backends:
        raise ValueError(
            "build_attention_view_impl requires at least one connected "
            "mailbox backend"
        )

    max_messages = max(1, min(int(max_messages), MAX_ATTENTION_SCAN_MESSAGES))
    multi = len(backends) > 1

    items: List[Dict[str, Any]] = []
    scanned_total = 0
    total_unread_total = 0
    total_unread_unknown = False
    scan_truncated = False
    mailbox_errors: List[Dict[str, Any]] = []

    for provider, backend in backends.items():
        try:
            out = _scan_one_backend(
                backend,
                provider=provider if multi else None,
                max_messages=max_messages,
                waiting_on_you_max_inbox=waiting_on_you_max_inbox,
                debug=debug,
            )
        except ConnectorsError as exc:
            msg = format_connector_error(exc)
            mailbox_errors.append({"mailbox": provider, "error": msg})
            log.warning("attention view: skipping %s mailbox — %s", provider, msg)
            continue
        items.extend(out["items"])
        scanned_total += out["scanned"]
        if out["total_unread"] is None:
            total_unread_unknown = True
        else:
            total_unread_total += out["total_unread"]
        scan_truncated = scan_truncated or out["scan_truncated"]

    if mailbox_errors and len(mailbox_errors) == len(backends):
        raise ConnectorsError(
            "every connected mailbox failed during the attention scan: "
            + "; ".join(f"{e['mailbox']}: {e['error']}" for e in mailbox_errors)
        )

    if action_db is not None:
        from gaia_agent_email import task_store

        for task in task_store.list_tasks(action_db, status="open"):
            items.append(_action_item(task))

    coverage: Dict[str, Any] = {
        "scanned": scanned_total,
        "total_unread": None if total_unread_unknown else total_unread_total,
        "scan_truncated": scan_truncated,
        "degraded": bool(mailbox_errors),
    }
    if mailbox_errors:
        coverage["mailbox_errors"] = mailbox_errors

    return {
        "kind": "email_attention",
        "items": items,
        "coverage": coverage,
        "generated_at": _utcnow_iso(),
        "cache_age_seconds": 0.0,
        "stale": False,
    }


__all__ = [
    "DEFAULT_ATTENTION_SCAN_MESSAGES",
    "MAX_ATTENTION_SCAN_MESSAGES",
    "build_attention_view_impl",
]
