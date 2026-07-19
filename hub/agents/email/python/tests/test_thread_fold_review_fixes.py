# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Review-fix tests for #1889 (PR #2077 follow-ups).

Fix 1 — ceiling BEFORE decode: the 500-message ceiling must engage as a cheap
slice before any per-message MIME decode/render/join work on BOTH surfaces,
with the dropped remainder surfacing as the explicit
``[omitted N older messages]`` marker (never a silent clip).

Fix 2 — symmetric accounting: when the fold call ran on the tool surface,
``summarize_thread``'s result carries its LLM usage under ``usage`` (plain
dict via ``aggregate_usage_stats``, mirroring the REST path / #1891); the
fits path makes no extra call and has no ``usage`` key.

Hermetic: FakeGmailBackend + fake chat only.
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

from gaia_agent_email.tools import read_tools  # noqa: E402
from gaia_agent_email.tools import thread_fold  # noqa: E402
from gaia_agent_email.tools.read_tools import summarize_thread_impl  # noqa: E402
from gaia_agent_email.tools.summarize_tools import _THREAD_SYSTEM_PROMPT  # noqa: E402
from gaia_agent_email.tools.thread_fold import (  # noqa: E402
    DEFAULT_THREAD_FOLD_MESSAGE_CEILING,
)

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _thread_msg(msg_id: str, thread_id: str, body: str, order: int) -> Dict[str, Any]:
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
                {"name": "Subject", "value": "Very long thread"},
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
    """Records every call; the fold call's response carries ``.stats``."""

    def __init__(self, *, fold_stats: dict | None = None) -> None:
        self.calls: List[Dict[str, Any]] = []
        self._fold_stats = fold_stats

    def send_messages(self, messages, system_prompt="", **kwargs):
        content = messages[0].get("content", "") if messages else ""
        self.calls.append({"system_prompt": system_prompt, "content": content})
        if system_prompt == thread_fold._FOLD_SYSTEM_PROMPT:
            resp = SimpleNamespace(text="digest of older messages")
            if self._fold_stats is not None:
                resp.stats = dict(self._fold_stats)
            return resp
        return SimpleNamespace(text="thread summary")

    def fold_call_contents(self) -> List[str]:
        return [
            c["content"]
            for c in self.calls
            if c["system_prompt"] == thread_fold._FOLD_SYSTEM_PROMPT
        ]

    def summary_call_content(self) -> str:
        for c in self.calls:
            if c["system_prompt"] == _THREAD_SYSTEM_PROMPT:
                return c["content"]
        raise AssertionError("no thread-summary call was made")


# ---------------------------------------------------------------------------
# Fix 1 — ceiling engages BEFORE the per-message decode (tool surface)
# ---------------------------------------------------------------------------


def test_600_message_thread_decodes_at_most_ceiling_messages(monkeypatch):
    """The 500-message ceiling must be a pre-slice: a 600-message thread pays
    at most 500 MIME decodes (one per considered message), never 600 — and
    never a second decode pass for the fold blocks."""
    n = 600
    gmail = _backend_with_thread("thr-600", [f"BODY{i} filler text" for i in range(n)])

    real_decode = read_tools.decode_message_body
    decode_count = {"n": 0}

    def counting_decode(payload):
        decode_count["n"] += 1
        return real_decode(payload)

    monkeypatch.setattr(read_tools, "decode_message_body", counting_decode)

    chat = _CapturingChat()
    result = summarize_thread_impl(gmail, chat, thread_id="thr-600")

    assert decode_count["n"] <= DEFAULT_THREAD_FOLD_MESSAGE_CEILING
    # The true thread size is still reported, not the sliced size.
    assert result["message_count"] == n


def test_600_message_thread_omission_marker_reaches_the_model():
    """Ceiling-dropped messages surface as the explicit marker in what
    reaches the model, never silently. The marker's count covers AT LEAST the
    100 ceiling drops (the fold's own input pre-cap may add more — both
    causes accumulate into one honest number)."""
    import re

    n = 600
    gmail = _backend_with_thread("thr-600b", [f"BODY{i} filler text" for i in range(n)])
    chat = _CapturingChat()

    summarize_thread_impl(gmail, chat, thread_id="thr-600b")

    everything_sent = "\n".join(c["content"] for c in chat.calls)
    m = re.search(r"\[omitted (\d+) older messages\]", everything_sent)
    assert m, "the omission marker must reach the model"
    assert int(m.group(1)) >= n - DEFAULT_THREAD_FOLD_MESSAGE_CEILING
    # The ceiling-dropped oldest messages' bodies never reach the model.
    assert "BODY0 " not in everything_sent
    assert f"BODY{n - DEFAULT_THREAD_FOLD_MESSAGE_CEILING - 1} " not in everything_sent
    # The newest message survives.
    assert f"BODY{n - 1} " in everything_sent


def test_under_ceiling_thread_has_no_omission_marker():
    gmail = _backend_with_thread("thr-small", [f"BODY{i} text" for i in range(3)])
    chat = _CapturingChat()

    result = summarize_thread_impl(gmail, chat, thread_id="thr-small")

    assert result["message_count"] == 3
    assert "[omitted" not in chat.summary_call_content()


# ---------------------------------------------------------------------------
# Fix 1 — ceiling pre-slice on the REST surface (api_routes)
# ---------------------------------------------------------------------------


def _thread_request(n: int, body: str):
    from gaia_agent_email.contract import (
        EmailAddress,
        EmailMessage,
        EmailTriageRequest,
        ThreadInput,
    )

    msgs = [
        EmailMessage(
            message_id=f"m{i}",
            subject="Very long thread",
            from_=EmailAddress(email=f"user{i}@example.com"),
            body=f"MSG{i}MARK {body}",
        )
        for i in range(n)
    ]
    payload = ThreadInput(
        thread_id="thr-rest-600",
        messages=msgs,
        principal=EmailAddress(email="me@example.com"),
    )
    return EmailTriageRequest(payload=payload)


class _RestCapturingChat:
    """Classify JSON for classify calls, digest for fold calls, else summary."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def send_messages(self, messages, system_prompt="", **kwargs):
        import json

        content = messages[0].get("content", "") if messages else ""
        self.calls.append({"system_prompt": system_prompt, "content": content})
        if system_prompt == thread_fold._FOLD_SYSTEM_PROMPT:
            return SimpleNamespace(text="digest of older messages")
        if "Classify" in content:
            return SimpleNamespace(
                text=json.dumps(
                    {"category": "NEEDS_RESPONSE", "confidence": 0.9, "reasoning": "t"}
                )
            )
        return SimpleNamespace(text="thread summary")


def test_rest_600_message_thread_is_pre_sliced_with_marker():
    """A 600-message ThreadInput considers only the most recent 500 messages;
    the drop surfaces as the omission marker (count covers at least the 100
    ceiling drops; the fold's input pre-cap may accumulate more) and the
    summary prefix still reports the TRUE thread size."""
    import re

    from gaia_agent_email.api_routes import EmailTriageService

    n = 600
    # Large bodies force the over-budget fold path.
    request = _thread_request(n, "x" * 2000)
    chat = _RestCapturingChat()

    response = EmailTriageService().triage_request(request, chat=chat)

    everything_sent = "\n".join(c["content"] for c in chat.calls)
    m = re.search(r"\[omitted (\d+) older messages\]", everything_sent)
    assert m, "the omission marker must reach the model"
    assert int(m.group(1)) >= n - DEFAULT_THREAD_FOLD_MESSAGE_CEILING
    assert "MSG0MARK" not in everything_sent  # ceiling-dropped, pre-join
    assert f"MSG{n - 1}MARK" in everything_sent  # newest survives
    assert f"Thread of {n} messages." in response.result.summary


def test_rest_600_tiny_messages_fits_path_still_marks_omission():
    """When the SLICED join fits the budget (tiny bodies), the ceiling drop
    still surfaces as the trailing marker — bounded and visible even without
    a fold."""
    from gaia_agent_email.api_routes import EmailTriageService

    n = 600
    request = _thread_request(n, "hi")
    chat = _RestCapturingChat()

    EmailTriageService().triage_request(request, chat=chat)

    marker = f"[omitted {n - DEFAULT_THREAD_FOLD_MESSAGE_CEILING} older messages]"
    everything_sent = "\n".join(c["content"] for c in chat.calls)
    assert marker in everything_sent
    # Fits path: no fold call was made.
    assert all(
        c["system_prompt"] != thread_fold._FOLD_SYSTEM_PROMPT for c in chat.calls
    )
    assert "MSG0MARK" not in everything_sent


# ---------------------------------------------------------------------------
# Fix 2 — fold usage surfaces in summarize_thread's result (tool surface)
# ---------------------------------------------------------------------------


def _over_budget_bodies(n: int = 20) -> List[str]:
    return [f"MSG{i}FILL" + "m" * 4000 for i in range(n)]


def test_fold_path_result_carries_fold_usage():
    gmail = _backend_with_thread("thr-usage", _over_budget_bodies())
    chat = _CapturingChat(
        fold_stats={
            "input_tokens": 1200,
            "output_tokens": 80,
            "tokens_per_second": 40.0,
        }
    )

    result = summarize_thread_impl(gmail, chat, thread_id="thr-usage")

    assert chat.fold_call_contents(), "precondition: the fold ran"
    usage = result["usage"]
    assert isinstance(usage, dict)  # plain dict per the #1891 serialization rule
    assert usage["prompt_tokens"] == 1200
    assert usage["completion_tokens"] == 80
    assert usage["total_tokens"] == 1280
    assert usage["tokens_per_second"] == pytest.approx(40.0)


def test_fits_path_result_has_no_usage_key():
    gmail = _backend_with_thread("thr-fits-u", [f"BODY{i} text" for i in range(3)])
    chat = _CapturingChat(fold_stats={"input_tokens": 999, "output_tokens": 9})

    result = summarize_thread_impl(gmail, chat, thread_id="thr-fits-u")

    assert not chat.fold_call_contents(), "precondition: no fold on the fits path"
    assert "usage" not in result


def test_fold_ran_but_stats_missing_yields_no_usage_key():
    """A chat double whose fold response carries no .stats must not fabricate
    a usage block — absent stats means absent accounting, never zeros."""
    gmail = _backend_with_thread("thr-nostats", _over_budget_bodies())
    chat = _CapturingChat(fold_stats=None)

    result = summarize_thread_impl(gmail, chat, thread_id="thr-nostats")

    assert chat.fold_call_contents(), "precondition: the fold ran"
    assert "usage" not in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
