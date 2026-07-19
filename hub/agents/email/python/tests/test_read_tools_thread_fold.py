# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Failing tests for #1889 surface 2 — ``summarize_thread_impl`` budget gating.

The token-budget gate replaces today's ``DEFAULT_THREAD_TRANSCRIPT_CHARS``
(24000) char cap as the "does it fit" criterion:

- FITS (``estimate_tokens(full_render) <= thread_budget_tokens()``): the
  transcript sent is exactly ``_format_thread_for_summary(ordered,
  max_total_transcript_chars=None)`` — the full, unclipped render.
- OVER BUDGET: the latest message's block is kept verbatim; the older blocks
  are folded via ONE ``thread_fold.fold_older_blocks`` call.

``thread_fold`` does not exist on the current tip — the module-level import is
EXPECTED to raise ImportError until #1889 lands (the intended red state). The
differential fits-path test exercises pre-existing, unchanged behavior and is
allowed to pass once the imports resolve.

Hermetic: FakeGmailBackend + fake chat only, no Lemonade, no network.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

# parents[0] = tests/, [1] = email/, [2] = python/, [3] = agents/, [4] = hub/,
# [5] = repo-root.
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

# EXPECTED ImportError until #1889 lands — this is the red state.
from gaia_agent_email.tools import thread_fold  # noqa: E402
from gaia_agent_email.tools.read_tools import (  # noqa: E402
    DEFAULT_BODY_LIMIT_CHARS,
    DEFAULT_THREAD_TRANSCRIPT_CHARS,
    _format_thread_for_summary,
    _thread_message_sort_key,
    summarize_thread_impl,
)
from gaia_agent_email.tools.summarize_tools import (  # noqa: E402
    _THREAD_SYSTEM_PROMPT,
    EmailSummarizeError,
)

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _thread_msg(msg_id: str, thread_id: str, body: str, order: int) -> Dict[str, Any]:
    """Minimal single-part text/plain Gmail-API message in a thread.

    ``order`` seeds ``internalDate`` so the summarizer's chronological sort is
    deterministic. Bodies should be space-free filler so the decoder's
    ``.strip()`` is a no-op and the intended length round-trips.
    """
    return {
        "id": msg_id,
        "threadId": thread_id,
        "labelIds": ["INBOX"],
        "snippet": body[:120],
        "internalDate": str(1_750_000_000_000 + order),
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "Subject", "value": "Project thread"},
                {"name": "From", "value": f"user{order}@example.com"},
                {"name": "To", "value": "me@example.com"},
                {"name": "Date", "value": "Mon, 1 Jan 2026 00:00:00 +0000"},
            ],
            "body": {"data": _b64url(body), "size": len(body.encode("utf-8"))},
        },
        "sizeEstimate": len(body),
    }


def _backend_with_thread(thread_id: str, bodies: List[str]) -> FakeGmailBackend:
    gmail = FakeGmailBackend(user_email="me@example.com")
    for i, body in enumerate(bodies):
        gmail.add_message(_thread_msg(f"{thread_id}-m{i}", thread_id, body, order=i))
    return gmail


class _CapturingChat:
    """Records every ``send_messages`` call; answers each with a fixed text.

    Distinguishes the fold call (``_FOLD_SYSTEM_PROMPT``) from the final
    thread-summary call (``_THREAD_SYSTEM_PROMPT``) so tests can inspect what
    reached the summary prompt after folding.
    """

    def __init__(
        self,
        *,
        digest: str = "CONDENSED_DIGEST_MARKER",
        summary: str = "thread summary",
    ) -> None:
        self.calls: List[Dict[str, Any]] = []
        self._digest = digest
        self._summary = summary

    def send_messages(self, messages, system_prompt="", **kwargs):
        content = messages[0].get("content", "") if messages else ""
        self.calls.append({"system_prompt": system_prompt, "content": content})
        if system_prompt == thread_fold._FOLD_SYSTEM_PROMPT:
            return SimpleNamespace(text=self._digest)
        return SimpleNamespace(text=self._summary)

    def summary_call_content(self) -> str:
        for c in self.calls:
            if c["system_prompt"] == _THREAD_SYSTEM_PROMPT:
                return c["content"]
        raise AssertionError("no thread-summary call was made")


class _FoldFailingChat:
    """Raises on the fold call to prove the failure propagates (never a
    silent fall back to the clipped/un-folded transcript)."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def send_messages(self, messages, system_prompt="", **kwargs):
        content = messages[0].get("content", "") if messages else ""
        self.calls.append({"system_prompt": system_prompt, "content": content})
        if system_prompt == thread_fold._FOLD_SYSTEM_PROMPT:
            raise RuntimeError("fold call boom")
        return SimpleNamespace(text="unreached summary")


# ---------------------------------------------------------------------------
# Differential fits-path test (pre-existing behavior; may pass once imports OK)
# ---------------------------------------------------------------------------


def test_fits_path_sends_full_none_capped_render_byte_for_byte():
    from gaia_agent_email.context_budget import estimate_tokens, thread_budget_tokens

    # A small thread comfortably under the token budget.
    bodies = ["alpha" * 50, "beta" * 50, "gamma" * 50]
    gmail = _backend_with_thread("thr-fits", bodies)
    ordered = sorted(
        gmail.get_thread("thr-fits")["messages"], key=_thread_message_sort_key
    )

    # Never hand-author the expected transcript — derive it from the renderer.
    expected_transcript = _format_thread_for_summary(
        ordered,
        per_message_body_limit=DEFAULT_BODY_LIMIT_CHARS,
        max_total_transcript_chars=None,
    )
    assert estimate_tokens(expected_transcript) <= thread_budget_tokens()

    chat = _CapturingChat()
    summarize_thread_impl(gmail, chat, thread_id="thr-fits")

    # The full None-capped render must appear byte-for-byte in what was sent.
    sent = chat.summary_call_content()
    assert expected_transcript in sent
    # Fits path -> no fold call.
    assert all(
        c["system_prompt"] != thread_fold._FOLD_SYSTEM_PROMPT for c in chat.calls
    )


# ---------------------------------------------------------------------------
# Over-budget fold path
# ---------------------------------------------------------------------------


def _over_budget_bodies() -> List[str]:
    """20 messages * 4000 dense chars. Full None-capped transcript estimates
    ~20757 tokens > thread_budget_tokens() (13824), so the fold path fires."""
    bodies = []
    for i in range(20):
        if i == 19:
            bodies.append("NEWESTVERBATIM" + "z" * 4000)
        elif i == 0:
            bodies.append("OLDESTVERBATIM" + "a" * 4000)
        else:
            bodies.append(f"MID{i}FILLER" + "m" * 4000)
    return bodies


def test_over_budget_folds_older_and_keeps_latest_verbatim():
    gmail = _backend_with_thread("thr-big", _over_budget_bodies())
    chat = _CapturingChat(digest="CONDENSED_DIGEST_MARKER")

    summarize_thread_impl(gmail, chat, thread_id="thr-big")

    # A fold call happened.
    assert any(
        c["system_prompt"] == thread_fold._FOLD_SYSTEM_PROMPT for c in chat.calls
    )
    sent = chat.summary_call_content()
    # Latest message survives verbatim; older bodies are condensed into the digest.
    assert "NEWESTVERBATIM" in sent
    assert "CONDENSED_DIGEST_MARKER" in sent
    # The oldest message's distinctive body is NOT sent verbatim (it was folded).
    assert "OLDESTVERBATIM" + "a" * 4000 not in sent


def test_over_budget_fold_failure_propagates_as_summarize_error():
    gmail = _backend_with_thread("thr-big2", _over_budget_bodies())
    chat = _FoldFailingChat()
    # ThreadFoldError is an EmailSummarizeError subclass, so this covers both.
    with pytest.raises(EmailSummarizeError):
        summarize_thread_impl(gmail, chat, thread_id="thr-big2")


# ---------------------------------------------------------------------------
# Boundary test: the 24K-chars-to-token-budget band now goes UNCLIPPED
# ---------------------------------------------------------------------------


def test_char_cap_band_now_sends_full_unclipped_transcript():
    """Announced behavior change (#1889): a thread whose OLD char-cap render
    (max_total_transcript_chars=24000) WOULD have been clipped, but whose token
    estimate stays under thread_budget_tokens(), now goes UNCLIPPED.

    Math (verified against the estimator): 8 messages * 4000 space-free chars.
      - raw body chars = 8 * 4000 = 32000 > 24000  -> OLD char cap would clip
        (fair_share = max(200, 24000 // 8) = 3000 < 4000 per-message body).
      - full None-capped transcript ~= 33198 chars, and being space-free the
        char estimate dominates: ~33198 // 4 = 8299 tokens <= 13824 budget.
    So the token gate says FITS and the full render is sent, unclipped.
    """
    from gaia_agent_email.context_budget import estimate_tokens, thread_budget_tokens

    bodies = ["x" * 4000 for _ in range(8)]
    gmail = _backend_with_thread("thr-band", bodies)
    ordered = sorted(
        gmail.get_thread("thr-band")["messages"], key=_thread_message_sort_key
    )

    full_render = _format_thread_for_summary(
        ordered,
        per_message_body_limit=DEFAULT_BODY_LIMIT_CHARS,
        max_total_transcript_chars=None,
    )
    old_capped = _format_thread_for_summary(
        ordered,
        per_message_body_limit=DEFAULT_BODY_LIMIT_CHARS,
        max_total_transcript_chars=DEFAULT_THREAD_TRANSCRIPT_CHARS,
    )
    # Precondition sanity: old cap clips, token estimate still fits.
    assert "...[truncated]" in old_capped
    assert estimate_tokens(full_render) <= thread_budget_tokens()

    chat = _CapturingChat()
    summarize_thread_impl(gmail, chat, thread_id="thr-band")
    sent = chat.summary_call_content()

    # New behavior: the full unclipped transcript is sent — no fair-share clip.
    assert "...[truncated]" not in sent
    for body in bodies:
        assert body in sent  # every original 4000-char body survives verbatim
    # No fold (it fit).
    assert all(
        c["system_prompt"] != thread_fold._FOLD_SYSTEM_PROMPT for c in chat.calls
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
