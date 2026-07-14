# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tokens-per-triage usage metric on the tool (chat-agent) bulk-triage path
(#1891, Increment 1).

Before this increment, ``EmailTriageAgent._triage_all_backends`` built its
classifier via ``read_tools.make_llm_classifier(chat)`` -- no ``collect_stats``
threaded through -- so per-call LLM token stats for the tool path were
silently discarded even though the REST path (``EmailTriageService``) already
aggregates and reports them (#1540). This increment wires the SAME shared
``call_stats`` list through a classifier built ONCE and reused across every
connected mailbox, then attaches ``usage`` (a plain dict) and
``llm_classified_count`` to the ``triage_inbox`` tool's JSON envelope --
present ONLY when at least one LLM classify call actually happened.

None of this exists yet in this worktree -- every test here is expected to
fail (missing ``usage``/``llm_classified_count`` keys) until the increment
lands.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("gaia_agent_email")  # noqa: E402

from unittest.mock import MagicMock, patch

from gaia_agent_email.agent import EmailTriageAgent
from gaia_agent_email.config import EmailAgentConfig

# ---------------------------------------------------------------------------
# In-test spy backend (Gmail-API-shape) -- same minimal pattern as
# test_multi_inbox_triage.py, but the default message is deliberately
# NEUTRAL (no system-category label, no promo/automated-sender keyword) so
# the heuristic returns confident=False and the LLM classifier IS invoked --
# these tests exercise the usage-metric wiring, not classification itself.
# ---------------------------------------------------------------------------


def _msg(message_id: str, *, subject="Hi there", sender=None, label_ids=None):
    return {
        "id": message_id,
        "threadId": f"t-{message_id}",
        "labelIds": label_ids if label_ids is not None else ["INBOX"],
        "snippet": subject,
        "internalDate": "1000",
        "payload": {
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender or "alice@example.com"},
            ],
            "body": {"data": ""},
        },
    }


class SpyBackend:
    """Minimal GmailBackend-shaped fake holding a fixed message list."""

    def __init__(self, name: str, messages: list[dict]):
        self.name = name
        self._messages = {m["id"]: m for m in messages}

    def list_messages(
        self, *, query=None, label_ids=None, max_results=25, page_token=None
    ):
        ids = list(self._messages)[:max_results]
        return {"messages": [{"id": m, "threadId": f"t-{m}"} for m in ids]}

    def get_message(self, message_id: str):
        return self._messages[message_id]

    def get_thread(self, thread_id: str):
        return {"id": thread_id, "messages": list(self._messages.values())}


_USAGE = {
    "prompt_tokens": 50,
    "completion_tokens": 10,
    "total_tokens": 60,
    "tokens_per_second": 20.0,
}


def _classify_response_text() -> str:
    return json.dumps(
        {"category": "FYI", "is_spam": False, "confidence": 0.9, "reasoning": "t"}
    )


def _build_agent(tmp_path, monkeypatch, *, google_messages, usage=None):
    """Construct an ``EmailTriageAgent`` with ONE connected (google) backend
    whose mocked chat's ``send_messages`` response carries a Lemonade
    chat-completion-shaped ``.usage`` dict -- the shape this increment threads
    through to ``result['usage']``.
    """
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    spy = SpyBackend("google", google_messages)
    cfg = EmailAgentConfig(
        gmail_backend=spy,
        calendar_backend=object(),
        db_path=str(tmp_path / "state.db"),
        silent_mode=True,
        mail_provider="google",
    )
    with patch("gaia.agents.base.agent.AgentSDK") as mock_sdk:
        response = MagicMock()
        response.text = _classify_response_text()
        response.usage = dict(usage or _USAGE)
        mock_sdk.return_value.send_messages.return_value = response
        agent = EmailTriageAgent(config=cfg)
    return agent


def _registered_tool(name):
    from gaia.agents.base.tools import _TOOL_REGISTRY

    return _TOOL_REGISTRY[name]["function"]


class TestTriageUsageMetric:
    def test_triage_inbox_attaches_usage_when_llm_classifies(
        self, tmp_path, monkeypatch
    ):
        """One message needing LLM escalation -> one classify call -> usage
        + llm_classified_count attached to the triage_inbox JSON envelope."""
        agent = _build_agent(tmp_path, monkeypatch, google_messages=[_msg("m1")])
        try:
            envelope = json.loads(_registered_tool("triage_inbox")(20))
            assert envelope["ok"] is True, envelope
            data = envelope["data"]
            assert data["usage"] == _USAGE
            assert data["llm_classified_count"] == 1
        finally:
            agent.close_db()

    def test_triage_inbox_usage_total_tokens_is_json_int(self, tmp_path, monkeypatch):
        """``usage`` must round-trip through the JSON envelope as a plain
        int -- proving it is a plain dict, not a pydantic model that the
        envelope's ``default=str`` fallback would have stringified."""
        agent = _build_agent(tmp_path, monkeypatch, google_messages=[_msg("m1")])
        try:
            envelope = json.loads(_registered_tool("triage_inbox")(20))
            total = envelope["data"]["usage"]["total_tokens"]
            assert isinstance(total, int)
            assert not isinstance(total, str)
            assert total == 60
        finally:
            agent.close_db()

    def test_triage_inbox_llm_classified_count_sums_across_backends(
        self, tmp_path, monkeypatch
    ):
        """Two backends, one message each needing LLM escalation -> the
        classifier must be built ONCE and its shared call_stats list reused
        (not rebuilt per backend), so llm_classified_count == 2, not 1, and
        usage sums across BOTH backends' calls."""
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        spy_g = SpyBackend("google", [_msg("g1")])
        spy_m = SpyBackend("microsoft", [_msg("m1")])
        cfg = EmailAgentConfig(
            gmail_backend=spy_g,
            outlook_backend=spy_m,
            calendar_backend=object(),
            db_path=str(tmp_path / "state.db"),
            silent_mode=True,
            mail_provider=None,
        )
        with patch("gaia.agents.base.agent.AgentSDK") as mock_sdk:
            response = MagicMock()
            response.text = _classify_response_text()
            response.usage = dict(_USAGE)
            mock_sdk.return_value.send_messages.return_value = response
            agent = EmailTriageAgent(config=cfg)
        try:
            envelope = json.loads(_registered_tool("triage_inbox")(20))
            assert envelope["ok"] is True, envelope
            data = envelope["data"]
            assert data["llm_classified_count"] == 2
            assert data["usage"]["total_tokens"] == _USAGE["total_tokens"] * 2
            assert data["usage"]["prompt_tokens"] == _USAGE["prompt_tokens"] * 2
            assert data["usage"]["completion_tokens"] == _USAGE["completion_tokens"] * 2
        finally:
            agent.close_db()

    def test_triage_inbox_no_usage_when_heuristic_confident_only(
        self, tmp_path, monkeypatch
    ):
        """Heuristic-confident-only run (no LLM call at all, out of scope for
        pre_scan too per #1891) -> neither 'usage' nor 'llm_classified_count'
        is present in the envelope data -- absent, not zero/null."""
        # CATEGORY_UPDATES -> category=FYI, confident=True, and (since the
        # category resolves non-PROMOTIONAL) spam_confident=True too, so
        # needs_llm is False and the classifier is never invoked.
        confident_msg = _msg(
            "m1", subject="Weekly digest", label_ids=["INBOX", "CATEGORY_UPDATES"]
        )
        agent = _build_agent(tmp_path, monkeypatch, google_messages=[confident_msg])
        try:
            envelope = json.loads(_registered_tool("triage_inbox")(20))
            assert envelope["ok"] is True, envelope
            data = envelope["data"]
            assert "usage" not in data
            assert "llm_classified_count" not in data
        finally:
            agent.close_db()
