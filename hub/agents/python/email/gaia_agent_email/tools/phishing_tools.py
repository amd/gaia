# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Phishing quarantine tool — reversible, confirmation-gated.

``quarantine_phishing_message`` is registered in
``TOOLS_REQUIRING_CONFIRMATION`` at the agent level.  It MUST NOT execute
without explicit user confirmation because it removes the message from INBOX
and adds a quarantine label.

Design principles:
- Reversible: the prior label set is recorded in the action log so
  ``unquarantine_impl`` can restore the message exactly.
- Confirmation-gated: added to ``TOOLS_REQUIRING_CONFIRMATION`` — never
  auto-executes.
- No hard delete: the message stays in the mailbox with a quarantine label;
  it is NEVER permanently deleted.
- Safety gate: the tool refuses to act on a message that has not been
  flagged as phishing (``is_phishing=False``).

The quarantine label name is ``GAIA_PHISHING_QUARANTINE``.  On first use,
if that label doesn't exist in the Gmail account, the tool creates it.

Action type recorded: ``quarantine_phishing`` — the ``payload`` carries
``{"prior_labels": [...], "quarantine_label_id": "..."}`` so the undo
path can call ``unquarantine_impl``.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from gaia_agent_email import action_store
from gaia_agent_email.verbose import log_tool_call

from gaia.agents.base.tools import tool
from gaia.connectors.errors import ConnectorsError
from gaia.connectors.formatting import format_connector_error
from gaia.logger import get_logger

log = get_logger(__name__)

# The Gmail label name used to quarantine phishing messages.  This is the
# human-readable display name; the tool resolves it to a label_id at runtime.
QUARANTINE_LABEL_NAME = "GAIA_PHISHING_QUARANTINE"


def _envelope_ok(data: Any) -> str:
    return json.dumps({"ok": True, "data": data}, default=str)


def _envelope_err(message: str) -> str:
    return json.dumps({"ok": False, "error": message})


def _resolve_quarantine_label_id(gmail) -> str:
    """Return the label_id for QUARANTINE_LABEL_NAME, creating it if absent.

    Fail-loud: if the Gmail API raises during ``list_labels`` or
    ``create_label``, the exception propagates — the caller must not
    silently skip quarantine.
    """
    for label in gmail.list_labels():
        if label.get("name") == QUARANTINE_LABEL_NAME:
            return label["id"]
    # Label does not exist — create it. The backend Protocol declares
    # ``create_label(self, *, name: str)`` (keyword-only); call it as such.
    new_label = gmail.create_label(name=QUARANTINE_LABEL_NAME)
    return new_label["id"]


def quarantine_phishing_impl(
    gmail,
    db,
    *,
    message_id: str,
    is_phishing: bool,
    mailbox: Optional[str] = None,
    debug: bool = False,
) -> Dict[str, Any]:
    """Core quarantine logic.

    Args:
        gmail:       Gmail backend (real or fake).
        db:          DatabaseMixin instance for the action log.
        message_id:  The message to quarantine.
        is_phishing: Must be ``True`` — the tool refuses if this is False.
        mailbox:     Which mailbox the action hit ('google' / 'microsoft'); recorded
                     so undo routes to the right account when several are connected.
        debug:       Pass-through to ``log_tool_call``.

    Returns:
        ``{"action_id": str, "message_id": str, "quarantine_label_id": str,
           "prior_labels": list, "quarantined": True}``

    Raises:
        ValueError: if ``is_phishing`` is False — the safety gate.
        ConnectorsError / any Gmail API exception: propagated to caller.

    Ordering invariant (Adversarial B2): Gmail calls execute first; the
    action-log row is written only after both succeed.
    """
    if not is_phishing:
        raise ValueError(
            f"quarantine_phishing_impl refused to quarantine message {message_id!r}: "
            "is_phishing=False.  Only messages flagged as phishing may be quarantined."
        )

    with log_tool_call(
        "quarantine_phishing_message", {"message_id": message_id}, debug=debug
    ) as st:
        prior = gmail.get_message(message_id)
        prior_labels = list(prior.get("labelIds", []))

        quarantine_label_id = _resolve_quarantine_label_id(gmail)

        # Gmail calls first (ordering invariant).
        # Non-atomic: if add_label succeeds but archive_message raises, the
        # message keeps the quarantine label in INBOX and no undo row is written
        # (manual label removal needed). The message is never lost.
        gmail.add_label(message_id, quarantine_label_id)
        gmail.archive_message(message_id)

        # Only write the action log row after both Gmail calls succeed.
        action_id = action_store.record_action(
            db,
            action_type="quarantine_phishing",
            message_id=message_id,
            thread_id=prior.get("threadId"),
            payload={
                "prior_labels": prior_labels,
                "quarantine_label_id": quarantine_label_id,
            },
            mailbox=mailbox,
        )
        st["result_summary"] = {"action_id": action_id}
        return {
            "action_id": action_id,
            "message_id": message_id,
            "quarantine_label_id": quarantine_label_id,
            "prior_labels": prior_labels,
            "quarantined": True,
        }


def unquarantine_impl(
    gmail,
    db,
    *,
    action_id: str,
    window_seconds: int,
    debug: bool = False,
) -> Dict[str, Any]:
    """Reverse a quarantine_phishing action within the undo window.

    Restores the exact label set recorded in the action payload and removes
    the quarantine label.

    Raises:
        RuntimeError: if the undo window has expired or the action_id is
            unknown — fail-loud rather than silently no-op.
    """
    with log_tool_call("unquarantine", {"action_id": action_id}, debug=debug) as st:
        action = action_store.fetch_undoable(
            db, action_id=action_id, window_seconds=window_seconds
        )
        if action is None:
            raise RuntimeError(
                f"undo window has expired ({window_seconds} s) or action_id "
                f"{action_id!r} is unknown or already undone.  The message "
                "remains in the quarantine label — move it manually in Gmail."
            )
        if action["action_type"] != "quarantine_phishing":
            raise RuntimeError(
                f"unquarantine_impl only undoes quarantine_phishing actions; "
                f"got {action['action_type']!r}"
            )
        mid = action["message_id"]
        payload = action["payload"]
        quarantine_label_id = payload.get("quarantine_label_id", "")
        prior_labels = payload.get("prior_labels", [])

        # Restore prior labels.  INBOX is among them for most messages.
        current_labels = set(gmail.get_message(mid).get("labelIds", []))
        for lab in prior_labels:
            if lab not in current_labels:
                gmail.add_label(mid, lab)

        # Remove the quarantine label if still present.
        current_labels = set(gmail.get_message(mid).get("labelIds", []))
        if quarantine_label_id and quarantine_label_id in current_labels:
            gmail.remove_label(mid, quarantine_label_id)

        action_store.mark_undone(db, action_id=action_id)
        st["result_summary"] = {"restored_message_id": mid}
        return {
            "action_id": action_id,
            "message_id": mid,
            "restored": True,
        }


# ---------------------------------------------------------------------------
# Tool mixin
# ---------------------------------------------------------------------------


class PhishingToolsMixin:
    """Registers the ``quarantine_phishing_message`` and
    ``unquarantine_message`` tools on the email agent.

    ``quarantine_phishing_message`` is confirmation-gated (added to
    ``TOOLS_REQUIRING_CONFIRMATION`` in ``agent.py``).
    ``unquarantine_message`` is the undo path — not gated.
    """

    def _register_phishing_tools(self) -> None:
        gmail = self._gmail
        db = self
        debug_flag = bool(getattr(self.config, "debug", False))
        window = int(getattr(self.config, "undo_window_seconds", 30))

        @tool
        def quarantine_phishing_message(message_id: str, is_phishing: bool) -> str:
            """Quarantine a phishing message: add the GAIA_PHISHING_QUARANTINE
            label and remove it from INBOX.  REQUIRES confirmation.
            Only acts on messages where ``is_phishing=True``.
            Reversible via ``unquarantine_message(action_id)``."""
            try:
                return _envelope_ok(
                    quarantine_phishing_impl(
                        gmail,
                        db,
                        message_id=message_id,
                        is_phishing=is_phishing,
                        debug=debug_flag,
                    )
                )
            except ValueError as exc:
                return _envelope_err(str(exc))
            except ConnectorsError as exc:
                return _envelope_err(format_connector_error(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def unquarantine_message(action_id: str) -> str:
            """Reverse a quarantine_phishing_message action within the undo
            window, restoring the message to INBOX and removing the quarantine
            label."""
            try:
                return _envelope_ok(
                    unquarantine_impl(
                        gmail,
                        db,
                        action_id=action_id,
                        window_seconds=window,
                        debug=debug_flag,
                    )
                )
            except ConnectorsError as exc:
                return _envelope_err(format_connector_error(exc))
            except RuntimeError as exc:
                # Expired-undo-window / unknown-action_id is actionable —
                # surface the message instead of a generic tool error.
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")


__all__ = [
    "QUARANTINE_LABEL_NAME",
    "PhishingToolsMixin",
    "quarantine_phishing_impl",
    "unquarantine_impl",
]
