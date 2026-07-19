# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Briefing + task tools mixin for ``EmailTriageAgent`` (#2110).

The briefing engine (``briefing.py``) and the triage-time task store
(``task_store.py``) existed only as REST endpoints and a side-effect of a
triage run — they were never registered as agent-loop tools. So the flagship
natural-language asks ("give me a daily briefing", "extract action items from
my recent emails") had no tool to bind to and the model silently fell back to a
raw ``pre_scan_inbox`` fence. This mixin closes that gap.

Tools registered:

- ``get_briefing(max_messages)`` — the inbox briefing. Returns the latest
  scheduled briefing when one exists, otherwise generates a fresh one now over
  every connected mailbox and persists it (same record shape the scheduler
  writes).
- ``list_tasks(status)`` — read tasks captured from prior triage runs.
- ``extract_action_items(max_messages)`` — DRIVE a fresh scan of the recent
  inbox window, extract action items from each body, persist them as tasks, and
  return them. Unlike ``list_tasks`` this does not rely on a prior triage
  having populated the store, so a cold "what do I need to do?" ask works.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from gaia_agent_email import task_store
from gaia_agent_email.gmail_backend import decode_message_body
from gaia_agent_email.tools.envelope import _envelope_err, _envelope_ok

from gaia.agents.base.tools import tool
from gaia.connectors.errors import ConnectorsError
from gaia.connectors.formatting import format_connector_error
from gaia.logger import get_logger

log = get_logger(__name__)


class BriefingToolsMixin:
    """Registers the briefing + task agent-loop tools.

    State-free at construction — the tools read live agent state
    (``self._backends`` for mailbox access, the ``DatabaseMixin`` base for the
    task store) through a closure captured at registration time.
    """

    def _register_briefing_tools(self) -> None:
        agent = self  # closure for live mailbox + db access

        @tool
        def get_briefing(max_messages: int = 25) -> str:
            """Return an inbox briefing (daily brief / morning summary).

            Use this for asks like "give me a daily briefing of my inbox",
            "morning brief", or "what's my inbox summary for today". Returns
            the most recent scheduled briefing if one has been generated;
            otherwise generates a fresh briefing now over every connected
            mailbox and persists it. The payload is the same
            ``email_pre_scan`` envelope the triage card renders — write a short
            framing sentence, do not recite the JSON.
            """
            try:
                from gaia_agent_email.briefing import (
                    load_latest_briefing,
                    persist_briefing,
                )

                record = load_latest_briefing()
                if record is None:
                    # Cold path: no scheduled briefing yet — generate one now
                    # across every connected mailbox (never a silent pick-one).
                    envelope = agent._pre_scan_all_backends(max_messages=max_messages)
                    record = {
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                        "briefing": envelope,
                    }
                    persist_briefing(record)
                return _envelope_ok(record)
            except ConnectorsError as exc:
                return _envelope_err(format_connector_error(exc))
            except Exception as exc:
                log.exception("get_briefing failed: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def list_tasks(status: str = "") -> str:
            """List tasks captured from previous email triage runs.

            Reads the durable task store populated when triage extracts action
            items. Pass ``status`` = 'open' or 'done' to filter, or leave it
            empty for all. This is read-only: it returns nothing if no triage
            has recorded tasks yet — use ``extract_action_items`` to scan the
            inbox and populate tasks from a cold start.
            """
            try:
                st = (status or "").strip().lower() or None
                if st is not None and st not in ("open", "done"):
                    return _envelope_err(
                        "status must be 'open', 'done', or empty for all tasks."
                    )
                rows = task_store.list_tasks(agent, status=st)
                return _envelope_ok({"tasks": rows, "count": len(rows)})
            except Exception as exc:
                log.exception("list_tasks failed: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def extract_action_items(max_messages: int = 25) -> str:
            """Extract action items from recent emails and save them as tasks.

            Use this for asks like "extract action items from my recent
            emails" or "what do I need to do from my inbox". Drives a fresh
            scan of the most recent inbox messages across every connected
            mailbox, extracts action items from each body, persists them as
            durable tasks (deduped per message), and returns them. Works from a
            cold start — it does NOT require a prior triage run.
            """
            try:
                from gaia_agent_email.api_routes import (
                    extract_action_items_from_body,
                )

                per_backend = max(1, int(max_messages) // max(1, len(agent._backends)))
                all_items: List[Dict[str, Any]] = []
                created_total = 0
                scanned = 0
                for provider, backend in agent._backends.items():
                    listing = backend.list_messages(
                        label_ids=["INBOX"], max_results=per_backend
                    )
                    for stub in listing.get("messages", []):
                        msg = backend.get_message(stub["id"])
                        scanned += 1
                        body_text, _ = decode_message_body(msg.get("payload") or {})
                        items = extract_action_items_from_body(body_text)
                        if not items:
                            continue
                        created = task_store.record_action_items(
                            agent, message_id=msg["id"], items=items
                        )
                        created_total += len(created)
                        for item in items:
                            all_items.append(
                                {
                                    "message_id": msg["id"],
                                    "mailbox": provider,
                                    "description": item.description,
                                    "due_hint": item.due_hint,
                                    "type": item.type,
                                    "url": item.url,
                                }
                            )
                return _envelope_ok(
                    {
                        "action_items": all_items,
                        "count": len(all_items),
                        "tasks_created": created_total,
                        "messages_scanned": scanned,
                    }
                )
            except ConnectorsError as exc:
                return _envelope_err(format_connector_error(exc))
            except Exception as exc:
                log.exception("extract_action_items failed: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")
