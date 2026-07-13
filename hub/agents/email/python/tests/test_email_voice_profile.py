# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Voice/style-matched drafting tests (#1607).

Acceptance criteria covered:
- AC: a local style profile is built from a sample of the user's Sent
  messages (greeting/sign-off/length/formality).
- AC: the draft-composition prompt reflects that profile — the agent's
  system prompt carries the style guidance once a profile exists.
- AC: the profile is local-only — derived features go to SQLite; raw Sent
  body content is never persisted.
- Test-AC: building the profile and drafting a reply trigger NO send
  side-effect (extends the #1264 never-auto-send invariant).

Embedder is mocked (same pattern as test_email_behavioral_learning.py) so
tests run hermetically without Lemonade.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Path / import bootstrap
# ---------------------------------------------------------------------------

# parents[0] = tests/,  [1] = email/,  [2] = python/,  [3] = agents/,
# [4] = hub/,  [5] = repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email import action_store  # noqa: E402
from gaia_agent_email.agent import EmailTriageAgent  # noqa: E402
from gaia_agent_email.config import EmailAgentConfig  # noqa: E402
from gaia_agent_email.voice_profile import (  # noqa: E402
    analyze_sent_bodies,
    render_style_guidance,
    strip_quoted_text,
)

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture data — a distinctive user voice
# ---------------------------------------------------------------------------

# Sentinel sentence that must NEVER show up in the persisted profile: the
# local-only guarantee is that we store derived features, not Sent content.
_PRIVATE_SENTINEL = "the acquisition closes on March 14 at 9am sharp"

_SENT_BODIES = [
    f"Hey Maria,\n\nQuick heads up — {_PRIVATE_SENTINEL}. Don't worry about "
    "the deck, I'll handle it!\n\nCheers,\nKalin",
    "Hey Tom,\n\nSounds good, let's do Thursday. I'll send the invite!\n\n"
    "Cheers,\nKalin",
    "Hey Priya,\n\nThanks for the summary — that's exactly what I needed. "
    "I'll loop in finance tomorrow.\n\nCheers,\nKalin",
    "Hey team,\n\nGreat work this sprint! Let's keep the momentum going.\n\n"
    "Cheers,\nKalin",
    "Hey Alex,\n\nCan't make it at 3, does 4 work? Happy to move things "
    "around if not.\n\nCheers,\nKalin",
]

EMBEDDING_DIM = 768


def _fake_embed(text: str) -> np.ndarray:
    vec = np.ones(EMBEDDING_DIM, dtype=np.float32)
    vec /= np.linalg.norm(vec)
    return vec


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def _gmail_message(
    msg_id: str,
    *,
    body: str,
    label_ids: list,
    sender: str = "user@example.com",
    to: str = "someone@example.com",
    subject: str = "Re: plans",
    internal_date: str = "1717502400000",
) -> dict:
    """Minimal Gmail-API-shape message accepted by FakeGmailBackend."""
    return {
        "id": msg_id,
        "threadId": f"t-{msg_id}",
        "labelIds": list(label_ids),
        "internalDate": internal_date,
        "snippet": body[:80],
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": to},
                {"name": "Subject", "value": subject},
                {"name": "Message-ID", "value": f"<{msg_id}@example.com>"},
            ],
            "body": {"data": _b64url(body)},
        },
    }


def _seed_sent_history(backend: FakeGmailBackend) -> None:
    for i, body in enumerate(_SENT_BODIES):
        backend.add_message(
            _gmail_message(
                f"sent-{i}",
                body=body,
                label_ids=["SENT"],
                sender="user@example.com",
                to=f"peer{i}@example.com",
                internal_date=str(1717502400000 + i),
            )
        )


def _build_agent(tmp_path: Path, backend: FakeGmailBackend) -> EmailTriageAgent:
    """EmailTriageAgent with an injected fake Gmail backend and tmp DBs."""
    cfg = EmailAgentConfig(
        gmail_backend=backend,
        calendar_backend=MagicMock(),
        db_path=str(tmp_path / "state.db"),
        memory_db_path=str(tmp_path / "memory.db"),
        silent_mode=True,
        debug=False,
    )
    with (
        patch("gaia.agents.base.agent.AgentSDK") as mock_sdk,
        patch(
            "gaia.agents.base.memory.MemoryMixin._get_embedder",
            return_value=MagicMock(),
        ),
        patch(
            "gaia.agents.base.memory.MemoryMixin._embed_text",
            side_effect=_fake_embed,
        ),
        patch(
            "gaia.agents.base.memory.MemoryMixin._backfill_embeddings",
            return_value=0,
        ),
        patch("gaia.agents.base.memory.MemoryMixin._rebuild_faiss_index"),
        patch("gaia.agents.base.memory.MemoryMixin.init_system_context"),
    ):
        mock_sdk.return_value = MagicMock()
        return EmailTriageAgent(config=cfg)


def _invoke_tool(name: str, **kwargs) -> dict:
    from gaia.agents.base.tools import _TOOL_REGISTRY

    entry = _TOOL_REGISTRY.get(name)
    assert entry is not None, f"{name} tool not registered"
    return json.loads(entry["function"](**kwargs))


# ===========================================================================
# Pure analysis — gaia_agent_email.voice_profile
# ===========================================================================


class TestStripQuotedText:
    def test_removes_quoted_lines_and_attribution(self):
        body = (
            "Sounds good, see you then.\n\n"
            "Cheers,\nKalin\n\n"
            "On Mon, Jun 2, 2026 at 9:00 AM Maria <m@x.com> wrote:\n"
            "> Are we still on for Thursday?\n"
            "> Let me know.\n"
        )
        cleaned = strip_quoted_text(body)
        assert "Are we still on" not in cleaned
        assert "wrote:" not in cleaned
        assert "Sounds good" in cleaned

    def test_plain_body_unchanged(self):
        body = "Just the reply text.\n\nCheers,\nKalin"
        assert strip_quoted_text(body).strip() == body.strip()


class TestAnalyzeSentBodies:
    def test_extracts_dominant_greeting_and_signoff(self):
        profile = analyze_sent_bodies(_SENT_BODIES)
        assert any("hey" in g.lower() for g in profile["greetings"])
        assert any("cheers" in s.lower() for s in profile["signoffs"])
        assert profile["sample_count"] == len(_SENT_BODIES)

    def test_reports_length_and_informality_signals(self):
        profile = analyze_sent_bodies(_SENT_BODIES)
        assert profile["median_words"] > 0
        # The fixture voice uses contractions ("I'll", "let's", "can't")
        # and exclamation marks.
        assert profile["uses_contractions"] is True
        assert profile["exclamation_rate"] > 0

    def test_no_usable_bodies_raises(self):
        with pytest.raises(ValueError):
            analyze_sent_bodies([])
        with pytest.raises(ValueError):
            analyze_sent_bodies(["", "   \n  "])

    def test_curly_apostrophe_contractions_detected(self):
        # Gmail's web composer emits typographic apostrophes.
        bodies = [
            "Hey Sam,\n\nI’ll get back to you tomorrow, can’t today."
            "\n\nCheers,\nKalin"
        ] * 3
        profile = analyze_sent_bodies(bodies)
        assert profile["uses_contractions"] is True

    def test_signoff_found_above_long_signature(self):
        bodies = [
            "Hey Sam,\n\nWorks for me.\n\nCheers,\nKalin Ovtcharov\n"
            "CEO, Acme Corp\n+1 555 0100\n123 Main St, Springfield\n"
            "www.acme.example"
        ] * 3
        profile = analyze_sent_bodies(bodies)
        assert any("cheers" in s.lower() for s in profile["signoffs"])


class TestRenderStyleGuidance:
    def test_mentions_greeting_signoff_and_length(self):
        profile = analyze_sent_bodies(_SENT_BODIES)
        block = render_style_guidance(profile)
        assert "VOICE" in block
        assert "Hey" in block or "hey" in block
        assert "Cheers" in block or "cheers" in block
        assert str(profile["median_words"]) in block


# ===========================================================================
# Persistence — action_store voice-profile table
# ===========================================================================


class TestVoiceProfileStore:
    def test_round_trip_upsert_and_delete(self, tmp_path):
        backend = FakeGmailBackend()
        agent = _build_agent(tmp_path, backend)

        assert action_store.fetch_voice_profile(agent) is None

        profile = analyze_sent_bodies(_SENT_BODIES)
        action_store.save_voice_profile(agent, mailbox="google", profile=profile)
        fetched = action_store.fetch_voice_profile(agent)
        assert fetched is not None
        assert fetched["greetings"] == profile["greetings"]

        # Upsert — a rebuild replaces, never duplicates.
        profile2 = dict(profile, median_words=999)
        action_store.save_voice_profile(agent, mailbox="google", profile=profile2)
        assert action_store.fetch_voice_profile(agent)["median_words"] == 999

        action_store.delete_voice_profile(agent, mailbox="google")
        assert action_store.fetch_voice_profile(agent) is None


# ===========================================================================
# Tool — build_voice_profile / clear_voice_profile
# ===========================================================================


class TestBuildVoiceProfileTool:
    def test_reads_sent_label_only(self, tmp_path):
        backend = FakeGmailBackend()
        _seed_sent_history(backend)
        # An INBOX message that must NOT contribute to the profile.
        backend.add_message(
            _gmail_message(
                "inbox-1",
                body="Dear Sir or Madam, kindly find attached the invoice.",
                label_ids=["INBOX"],
                sender="vendor@example.com",
                to="user@example.com",
            )
        )
        _build_agent(tmp_path, backend)

        result = _invoke_tool("build_voice_profile")
        assert result["ok"], result
        assert result["data"]["sample_count"] == len(_SENT_BODIES)

        list_calls = [
            kwargs
            for method, kwargs in backend.transport.calls
            if method == "list_messages"
        ]
        assert list_calls, "build_voice_profile never listed messages"
        assert all(kwargs["label_ids"] == ["SENT"] for kwargs in list_calls)

    def test_prompt_gains_voice_block_after_build(self, tmp_path):
        backend = FakeGmailBackend()
        _seed_sent_history(backend)
        agent = _build_agent(tmp_path, backend)

        assert "VOICE" not in agent._get_system_prompt()
        result = _invoke_tool("build_voice_profile")
        assert result["ok"], result
        prompt = agent._get_system_prompt()
        assert "VOICE" in prompt
        assert "hey" in prompt.lower()
        assert "cheers" in prompt.lower()

    def test_profile_persists_across_restart(self, tmp_path):
        backend = FakeGmailBackend()
        _seed_sent_history(backend)
        _build_agent(tmp_path, backend)
        result = _invoke_tool("build_voice_profile")
        assert result["ok"], result

        # New agent instance, same state.db — profile must survive.
        agent2 = _build_agent(tmp_path, FakeGmailBackend())
        assert "VOICE" in agent2._get_system_prompt()

    def test_stored_profile_has_no_raw_sent_content(self, tmp_path):
        backend = FakeGmailBackend()
        _seed_sent_history(backend)
        agent = _build_agent(tmp_path, backend)
        result = _invoke_tool("build_voice_profile")
        assert result["ok"], result

        row = agent.query("SELECT profile_json FROM email_voice_profile", {}, one=True)
        assert row is not None
        assert _PRIVATE_SENTINEL not in row["profile_json"]

    def test_no_sent_history_errors_actionably(self, tmp_path):
        _build_agent(tmp_path, FakeGmailBackend())
        result = _invoke_tool("build_voice_profile")
        assert result["ok"] is False
        assert "sent" in result["error"].lower()

    def test_clear_voice_profile_removes_block(self, tmp_path):
        backend = FakeGmailBackend()
        _seed_sent_history(backend)
        agent = _build_agent(tmp_path, backend)
        result = _invoke_tool("build_voice_profile")
        assert result["ok"], result
        assert "VOICE" in agent._get_system_prompt()

        result = _invoke_tool("clear_voice_profile")
        assert result["ok"], result
        assert "VOICE" not in agent._get_system_prompt()


# ===========================================================================
# Never-auto-send — profile build + draft produce zero send side-effects
# ===========================================================================


class TestNoSendSideEffect:
    def test_build_and_draft_cause_no_send_calls(self, tmp_path):
        backend = FakeGmailBackend()
        _seed_sent_history(backend)
        backend.add_message(
            _gmail_message(
                "inbox-reply-me",
                body="Are we still on for Thursday?",
                label_ids=["INBOX"],
                sender="maria@example.com",
                to="user@example.com",
            )
        )
        _build_agent(tmp_path, backend)

        result = _invoke_tool("build_voice_profile")
        assert result["ok"], result
        result = _invoke_tool(
            "draft_reply",
            message_id="inbox-reply-me",
            body="Hey Maria,\n\nYes — see you Thursday!\n\nCheers,\nKalin",
        )
        assert result["ok"], result

        methods = {method for method, _ in backend.transport.calls}
        assert "create_draft" in methods
        forbidden = {"send_draft", "send_message"}
        assert not (
            methods & forbidden
        ), f"send side-effect detected: {methods & forbidden}"
