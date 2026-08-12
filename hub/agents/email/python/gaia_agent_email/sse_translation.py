# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Email's binding of the shared canonical SSE translator.

The translator itself lives in :mod:`gaia.ui.sse_translation` — it is agent-generic
and shared with every other sidecar. This module supplies only the email-shaped
inputs: which tool draws which card, and how a gated action is described to the
human approving it.

``CanonicalTranslator`` and ``TERMINAL_TYPES`` are re-exported so existing
importers (and the frozen binary's import graph) keep working unchanged.
"""

from __future__ import annotations

from functools import partial
from typing import Dict

from gaia.ui.sse_translation import (  # noqa: F401  (re-exported)
    TERMINAL_TYPES,
    CanonicalTranslator,
    render_labelled_summary,
)

# Mirrors ``gaia.ui.sse_handler.SSEOutputHandler._RENDER_TOOL_TO_LANG`` — the
# tool→card-key map that tells the host which typed ``tool_result.render`` card to
# draw (spec §4.2, replacing the #1000 fence-injection hack). Duplicated (not
# imported) to keep this module free of the ``gaia.ui`` handler import chain; a
# test (``test_render_tool_to_lang_maps_stay_in_sync``) pins the two dicts equal
# so this duplication can't silently drift.
_RENDER_TOOL_TO_LANG: Dict[str, str] = {
    # ``pre_scan_inbox`` deliberately draws NO card: it landed mid-turn as a
    # partial list while the model was still writing the full triage answer,
    # so the user read two overlapping views of one inbox and could not tell
    # which to act on. The triage reply is the single view; refs still resolve
    # from tool data (``resolve_needs_you_reference``), not from a render.
    # #2765: a generic ``table`` card (no new client code) so the thread
    # view renders straight from tool data instead of model prose.
    "get_thread": "table",
}

# Human labels for the confirmation-gated actions the /query stream surfaces.
# The machine action name still rides on the event's ``action`` field as
# anti-spoof metadata; this map is only for the human-readable headline.
_ACTION_LABELS: Dict[str, str] = {
    "send_now": "Send this email",
    "send_draft": "Send this draft",
    "forward_message": "Forward this email",
    "quarantine_phishing_message": "Quarantine this message as phishing",
    "unquarantine_message": "Restore this message from quarantine",
    "archive_message": "Archive this message",
}

# Argument fields worth showing in a confirmation prompt, e.g.
# ``Send this email to a@b.com — subject "Re: …"?``
_SUMMARY_DETAIL_FIELDS: Dict[str, str] = {"to": "to", "subject": "— subject"}

#: Email's ``summary_renderer`` for :class:`CanonicalTranslator`.
render_args_summary = partial(
    render_labelled_summary,
    labels=_ACTION_LABELS,
    detail_fields=_SUMMARY_DETAIL_FIELDS,
)


def build_translator(run_id: str) -> CanonicalTranslator:
    """Return a translator bound to the email agent's rendering and labels."""
    return CanonicalTranslator(
        run_id,
        agent_id="email",
        render_tool_map=_RENDER_TOOL_TO_LANG,
        action_labels=_ACTION_LABELS,
        summary_renderer=render_args_summary,
    )


__all__ = [
    "CanonicalTranslator",
    "TERMINAL_TYPES",
    "build_translator",
    "render_args_summary",
]
