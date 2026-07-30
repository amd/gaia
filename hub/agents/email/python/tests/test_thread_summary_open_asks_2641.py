# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Failing tests for #2641 — thread summaries keep the newest message's open asks.

The reported defect: a thread summary reflected the opening question and an
early reply but dropped the newest message entirely, even though the newest
message carried the thread's only open ask (a status question) plus a
concrete meeting proposal. Root-cause analysis (see the accepted plan)
established two things:

1. The newest message already survives verbatim in the transcript sent to the
   model, in BOTH the fits-budget and the over-budget fold branches — this is
   not a truncation bug in ``_thread_message_blocks``/``_format_thread_for_summary``.
2. Both ``_THREAD_SYSTEM_PROMPT`` and ``_build_thread_user_prompt`` only ever
   instructed the model to protect EARLY content ("do not drop a decision
   raised early..."); nothing asked it to protect what is still OPEN in the
   newest message. That is the actual defect this change fixes, plus wiring
   the existing deterministic ``detect_meeting_request_heuristic`` signal
   (computed over the newest message's own decoded body) into the user
   prompt so a meeting proposal is named from the SIGNAL, not left to
   free-form generation.

TDD red state: this module imports ``THREAD_SUMMARY_CHAR_LIMIT`` (does not
exist yet) from ``summarize_tools`` and calls ``_build_thread_user_prompt``
with a ``meeting_detected`` keyword (does not exist yet) — both raise
(ImportError at collection time / TypeError at call time) until the
implementation lands.

Per the adversarial reflection (C1-C4 in the plan):
- Assertions target the PROMPT actually sent to ``chat.send_messages``
  (via ``_CapturingChat``), never the free-form text a live LLM would
  generate — temperature=0.0 is not bit-determinism on quantized GPU
  kernels, so an output-string assertion does not belong in this suite.
- The aging guard (no invented asks/meetings once the newest message has
  none) is asserted STRUCTURALLY: the heuristic itself returns
  ``is_meeting_request=False`` on the truncated fixture's new-newest body,
  AND the prompt sent to the model contains no meeting-injection text at
  all — never an output-substring check, which fails both ways (a
  broken "always inject" implementation could pass by luck; a correct one
  could fail on an innocuous paraphrase).
- The meeting-signal wiring never surfaces ``MeetingDetection.signals`` or
  ``.reason`` (raw, sender-authored substrings) — only a generic,
  non-authoritative, body-scoped note. ``_build_thread_user_prompt`` takes a
  plain ``bool``, so there is no argument through which those raw fields
  could even reach the prompt.

Privacy: every fixture below uses only synthetic ``example.invalid``
addresses and invented, non-sensitive PR/status content — never the real
thread that produced this bug report.

Hermetic: FakeGmailBackend + a local fake chat only, no Lemonade, no network.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Tuple

import pytest

# parents[0] = tests/, [1] = email/, [2] = python/, [3] = agents/, [4] = hub/,
# [5] = repo-root — needed so ``tests.fixtures`` resolves.
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.tools import thread_fold  # noqa: E402
from gaia_agent_email.tools.calendar_tools import (  # noqa: E402
    detect_meeting_request_heuristic,
)
from gaia_agent_email.tools.read_tools import (  # noqa: E402
    ReadToolsMixin,
    _build_thread_user_prompt,
    summarize_thread_impl,
)

# EXPECTED ImportError until #2641 lands — this is the red state.
from gaia_agent_email.tools.summarize_tools import (  # noqa: E402
    _THREAD_SYSTEM_PROMPT,
    DEFAULT_SUMMARY_CHAR_LIMIT,
    THREAD_SUMMARY_CHAR_LIMIT,
)

from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402
from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_SUBJECT = "Docs PR for connectors guide"
_ALICE = "alice@example.invalid"
_BOB = "bob@example.invalid"

# A 4-message synthetic thread mirroring the reported shape: an opening ask,
# an early reply, a status update, and — the newest message — a status
# question plus a concrete meeting proposal. All content is invented.
_MSG1 = (
    _ALICE,
    "Could we get the connectors guide PR merged before the release cut? "
    "It documents the new Google scopes flow.",
)
_MSG2 = (
    _BOB,
    "Reviewed, looks solid. One nit: the scopes table needs the "
    "workspace-only row called out.",
)
_MSG3_NO_ASK = (
    _ALICE,
    "Fixed the scopes table nit and pushed. Still waiting on CI before merge.",
)
_MSG4_STATUS_AND_MEETING_ASK = (
    _BOB,
    "Is the pull request's CI still green, any update? Also, any chance to "
    "meet Thursday at 9am to walk through the last review comments?",
)


def _four_message_thread() -> List[Tuple[str, str]]:
    """The full fixture — open ask + meeting proposal only in message [4]."""
    return [_MSG1, _MSG2, _MSG3_NO_ASK, _MSG4_STATUS_AND_MEETING_ASK]


def _three_message_thread_aging_guard() -> List[Tuple[str, str]]:
    """Same thread with the asking message [4] removed (the aging-guard
    fixture, #2641/C3). The new newest message (``_MSG3_NO_ASK``) must
    genuinely carry no ask/meeting language of its own, or the guard proves
    nothing — verified directly in ``test_aging_guard_*`` below."""
    return [_MSG1, _MSG2, _MSG3_NO_ASK]


def _over_budget_thread_with_newest_ask() -> List[Tuple[str, str]]:
    """20 dense-filler messages forcing the #1889 fold path; the newest
    (last) message carries the same status + meeting ask as
    ``_MSG4_STATUS_AND_MEETING_ASK`` plus a verbatim marker, so AC5 (the
    fold path must carry the newest message's open asks too) is provable.
    Mirrors ``test_read_tools_thread_fold.py``'s ``_over_budget_bodies``.
    """
    bodies: List[Tuple[str, str]] = []
    for i in range(20):
        if i == 19:
            body = (
                "Is the pull request's CI still green, any update? Any "
                "chance to meet Thursday at 9am to walk through the last "
                "review comments? NEWESTVERBATIM" + "z" * 4000
            )
        elif i == 0:
            body = "OLDESTVERBATIM" + "a" * 4000
        else:
            body = f"MID{i}FILLER" + "m" * 4000
        sender = _ALICE if i % 2 == 0 else _BOB
        bodies.append((sender, body))
    return bodies


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _thread_msg(
    msg_id: str, *, thread_id: str, sender: str, body: str, order: int
) -> Dict[str, Any]:
    return {
        "id": msg_id,
        "threadId": thread_id,
        "labelIds": ["INBOX"],
        "snippet": body[:200],
        "internalDate": str(1_750_000_000_000 + order),
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "Subject", "value": _SUBJECT},
                {"name": "From", "value": sender},
                {"name": "To", "value": "me@example.invalid"},
                {"name": "Date", "value": "Mon, 1 Jan 2026 00:00:00 +0000"},
            ],
            "body": {"data": _b64url(body), "size": len(body.encode("utf-8"))},
        },
        "sizeEstimate": len(body),
    }


def _backend_with_bodies(
    thread_id: str, senders_and_bodies: List[Tuple[str, str]]
) -> FakeGmailBackend:
    gmail = FakeGmailBackend(user_email="me@example.invalid")
    for i, (sender, body) in enumerate(senders_and_bodies):
        gmail.add_message(
            _thread_msg(
                f"{thread_id}-m{i}",
                thread_id=thread_id,
                sender=sender,
                body=body,
                order=i,
            )
        )
    return gmail


class _CapturingChat:
    """Records every ``send_messages`` call; distinguishes the fold call
    (``_FOLD_SYSTEM_PROMPT``) from the final thread-summary call
    (``_THREAD_SYSTEM_PROMPT``) so tests can inspect exactly what reached
    the summary prompt. Mirrors ``test_read_tools_thread_fold.py``.
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
        self.calls.append(
            {"system_prompt": system_prompt, "content": content, "kwargs": dict(kwargs)}
        )
        if system_prompt == thread_fold._FOLD_SYSTEM_PROMPT:
            return SimpleNamespace(text=self._digest)
        return SimpleNamespace(text=self._summary)

    def summary_call_content(self) -> str:
        for c in self.calls:
            if c["system_prompt"] == _THREAD_SYSTEM_PROMPT:
                return c["content"]
        raise AssertionError("no thread-summary call was made")


class _Host(ReadToolsMixin):
    """Minimal EmailTriageAgent stand-in, mirrors test_read_tools_body_limit.py."""

    def __init__(self, backend: FakeGmailBackend, chat: Any) -> None:
        self._gmail = backend
        self._backends = {"google": backend}
        self._message_mailbox: Dict[str, str] = {}
        self.config = SimpleNamespace(debug=False)
        self.chat = chat

    def _remember_message_mailbox(self, message_id, provider):
        if message_id:
            self._message_mailbox[message_id] = provider

    def _backend_for_message(self, message_id, explicit_mailbox=None):
        provider = explicit_mailbox or self._message_mailbox.get(message_id)
        if provider is None:
            if len(self._backends) == 1:
                return next(iter(self._backends.values()))
            raise ValueError("ambiguous mailbox in test stub")
        backend = self._backends.get(provider)
        if backend is None:
            raise ValueError("mailbox not connected in test stub")
        return backend


# ---------------------------------------------------------------------------
# AC — "The prompt guards recent content as explicitly as early content."
# ---------------------------------------------------------------------------


def test_thread_system_prompt_protects_open_items_in_newest_message():
    assert "still open in the newest message" in _THREAD_SYSTEM_PROMPT.lower()


def test_user_prompt_protects_open_items_in_newest_message():
    prompt = _build_thread_user_prompt("Subject", "transcript body")
    assert "still-open asks" in prompt.lower()


def test_user_prompt_carries_meeting_note_only_when_detected():
    with_note = _build_thread_user_prompt("s", "t", meeting_detected=True)
    without_note = _build_thread_user_prompt("s", "t", meeting_detected=False)
    assert "appears to propose a meeting time" in with_note.lower()
    assert "appears to propose a meeting time" not in without_note.lower()


# ---------------------------------------------------------------------------
# AC — "The newest message's asks reach the summary" (fits-budget path)
# ---------------------------------------------------------------------------


def test_fits_path_prompt_carries_newest_message_status_question_and_time():
    gmail = _backend_with_bodies("thr-fits", _four_message_thread())
    chat = _CapturingChat()

    summarize_thread_impl(gmail, chat, thread_id="thr-fits")

    sent = chat.summary_call_content()
    assert "Thursday" in sent
    assert "9am" in sent
    assert "pull request's CI still green" in sent
    # No fold on this small thread.
    assert all(
        c["system_prompt"] != thread_fold._FOLD_SYSTEM_PROMPT for c in chat.calls
    )


# ---------------------------------------------------------------------------
# AC — "A detected meeting proposal is named, from the signal."
# ---------------------------------------------------------------------------


def test_fits_path_prompt_carries_the_meeting_signal_note():
    gmail = _backend_with_bodies("thr-meet", _four_message_thread())
    chat = _CapturingChat()

    summarize_thread_impl(gmail, chat, thread_id="thr-meet")

    sent = chat.summary_call_content()
    assert "appears to propose a meeting time" in sent.lower()


def test_meeting_signal_is_a_real_heuristic_detection_not_assumed():
    # Independently confirms the deterministic heuristic actually fires on
    # this fixture's newest body — the prompt-level assertion above is only
    # meaningful because this is true.
    detection = detect_meeting_request_heuristic(
        _SUBJECT, _MSG4_STATUS_AND_MEETING_ASK[1]
    )
    assert detection.is_meeting_request is True
    assert detection.confidence == "high"


# ---------------------------------------------------------------------------
# AC — "Aging guard — no invented asks." (structural, C3)
# ---------------------------------------------------------------------------


def test_aging_guard_heuristic_reports_no_meeting_on_truncated_thread():
    # (a) The heuristic itself, run on the truncated fixture's new-newest
    # body, must report no meeting — otherwise this guard proves nothing.
    detection = detect_meeting_request_heuristic(_SUBJECT, _MSG3_NO_ASK[1])
    assert detection.is_meeting_request is False


def test_aging_guard_prompt_carries_no_meeting_injection_when_none_detected():
    # (b) Structural: the prompt actually sent contains no meeting-injection
    # text at all — never an output-prose check, so a broken "always
    # inject" implementation cannot pass by luck, and a correct
    # implementation cannot fail on an innocuous paraphrase.
    gmail = _backend_with_bodies("thr-noask", _three_message_thread_aging_guard())
    chat = _CapturingChat()

    summarize_thread_impl(gmail, chat, thread_id="thr-noask")

    sent = chat.summary_call_content()
    assert "appears to propose a meeting time" not in sent.lower()


# ---------------------------------------------------------------------------
# AC — "The fold path is covered too."
# ---------------------------------------------------------------------------


def test_fold_path_prompt_still_carries_newest_ask_and_meeting_note():
    gmail = _backend_with_bodies("thr-big", _over_budget_thread_with_newest_ask())
    chat = _CapturingChat(digest="CONDENSED_DIGEST_MARKER")

    summarize_thread_impl(gmail, chat, thread_id="thr-big")

    # Confirm this really is the over-budget branch.
    assert any(
        c["system_prompt"] == thread_fold._FOLD_SYSTEM_PROMPT for c in chat.calls
    )
    sent = chat.summary_call_content()
    assert "NEWESTVERBATIM" in sent
    assert "Thursday" in sent
    assert "9am" in sent
    assert "appears to propose a meeting time" in sent.lower()
    # Older content was condensed, never sent verbatim.
    assert "OLDESTVERBATIM" + "a" * 4000 not in sent


def test_fold_path_aging_guard_no_meeting_note_when_newest_has_no_ask():
    bodies = _over_budget_thread_with_newest_ask()
    # Replace the newest (asking) message with dense filler carrying no
    # ask/meeting language, keeping the thread just as over-budget.
    bodies[-1] = (_BOB, "STATUSONLY" + "z" * 4000)
    detection = detect_meeting_request_heuristic(_SUBJECT, bodies[-1][1])
    assert detection.is_meeting_request is False

    gmail = _backend_with_bodies("thr-big-noask", bodies)
    chat = _CapturingChat(digest="CONDENSED_DIGEST_MARKER")

    summarize_thread_impl(gmail, chat, thread_id="thr-big-noask")

    assert any(
        c["system_prompt"] == thread_fold._FOLD_SYSTEM_PROMPT for c in chat.calls
    )
    sent = chat.summary_call_content()
    assert "appears to propose a meeting time" not in sent.lower()


# ---------------------------------------------------------------------------
# Thread summary char-limit raise (300 cannot hold several messages'
# decisions plus a new open ask plus a meeting time — see the plan's
# "RESOLVED by review" section).
# ---------------------------------------------------------------------------


def test_thread_summary_char_limit_is_larger_than_single_message_default():
    assert THREAD_SUMMARY_CHAR_LIMIT > DEFAULT_SUMMARY_CHAR_LIMIT


def test_summarize_thread_tool_wrapper_uses_the_raised_thread_limit():
    long_text = "Decision on scopes table. " * 20
    expected = long_text.strip()
    # Precondition: long enough to prove the raise, short enough to fit it.
    assert DEFAULT_SUMMARY_CHAR_LIMIT < len(expected) <= THREAD_SUMMARY_CHAR_LIMIT

    gmail = _backend_with_bodies("thr-limit", _four_message_thread())
    chat = _CapturingChat(summary=long_text)
    host = _Host(gmail, chat)

    _TOOL_REGISTRY.clear()
    host._register_read_tools()
    fn = _TOOL_REGISTRY["summarize_thread"]["function"]

    raw = fn(thread_id="thr-limit")
    payload = json.loads(raw)

    assert payload["ok"] is True
    # Would be hard-capped at 300 chars if the wrapper still used the
    # single-message default — proves THREAD_SUMMARY_CHAR_LIMIT is actually
    # wired into the registered tool, not just available as a constant.
    assert payload["data"]["summary"] == expected


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
