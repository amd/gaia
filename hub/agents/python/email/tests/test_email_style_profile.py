# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Voice/style-profile tests for EmailTriageAgent (#1607).

Acceptance criteria covered:
- AC1: a local style profile is built from a sample of the user's Sent
  messages (greeting / sign-off / length / formality features).
- AC2 (deterministic form): the profile features shape the draft — the
  draft-composition system prompt carries the learned greeting/sign-off,
  and drafts stay approval-gated. The judge-scored style-match eval
  against the #1230 corpus is a follow-up (no Sent-history corpus with a
  known voice exists in-repo yet).
- AC3: the profile is local-only AND content-free — no raw Sent body text
  survives into the persisted record.
- Test-AC: draft generation (build_style_profile and draft_reply) triggers
  NO send side-effect.

Embedder is mocked (same pattern as test_email_preferences_persist.py) so
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

from gaia_agent_email.agent import EmailTriageAgent  # noqa: E402
from gaia_agent_email.config import EmailAgentConfig  # noqa: E402
from gaia_agent_email.tools.style_tools import (  # noqa: E402
    build_profile_from_bodies,
    classify_formality,
    extract_greeting,
    extract_signoff,
    strip_quoted_text,
)

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EMBEDDING_DIM = 768

_STYLE_ENTITY = "email:style_profile:google"

# A distinctive sentence that must NEVER survive into the persisted profile
# (AC3 — the profile stores derived features only, no raw Sent content).
_PRIVATE_MARKER = "the Zephyr acquisition closes at 4M on Thursday"


class _MinimalCalendarBackend:
    pass


def _fake_embed(text: str) -> np.ndarray:
    """Deterministic unit vector — keeps FAISS happy."""
    vec = np.ones(EMBEDDING_DIM, dtype=np.float32)
    vec /= np.linalg.norm(vec)
    return vec


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _sent_msg(msg_id: str, *, body: str, to: str = "Sam <sam@example.com>") -> dict:
    """One Gmail-API-shape message carrying the SENT system label."""
    return {
        "id": msg_id,
        "threadId": f"t-{msg_id}",
        "labelIds": ["SENT"],
        "snippet": body[:80],
        "internalDate": "1750000000000",
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "From", "value": "User <user@example.com>"},
                {"name": "To", "value": to},
                {"name": "Subject", "value": f"Update {msg_id}"},
                {"name": "Date", "value": "Mon, 15 Jun 2026 10:00:00 -0700"},
            ],
            "body": {"size": len(body), "data": _b64url(body)},
        },
        "sizeEstimate": len(body),
    }


def _inbox_msg(msg_id: str, *, sender: str = "Sam <sam@example.com>") -> dict:
    body = "Could you send over the latest numbers when you get a chance?"
    return {
        "id": msg_id,
        "threadId": f"t-{msg_id}",
        "labelIds": ["INBOX", "UNREAD"],
        "snippet": body[:80],
        "internalDate": "1750000100000",
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": "User <user@example.com>"},
                {"name": "Subject", "value": "Numbers?"},
                {"name": "Date", "value": "Mon, 15 Jun 2026 10:05:00 -0700"},
                {"name": "Message-ID", "value": f"<{msg_id}@example.com>"},
            ],
            "body": {"size": len(body), "data": _b64url(body)},
        },
        "sizeEstimate": len(body),
    }


_SENT_BODIES = [
    f"Hi Sam,\n\nSounds good — I'll get the numbers over to you "
    f"tomorrow. Btw {_PRIVATE_MARKER}.\n\nThanks,\nKalin",
    "Hi Priya,\n\nYeah, works for me. Let's lock in Tuesday.\n\nThanks,\nKalin",
    "Hi Dana,\n\nQuick heads up — the report is done and shared.\n\nThanks,\nKalin",
    "Hey,\n\nNo worries, take your time on the review.\n\nCheers,\nKalin",
    "Hi Lee,\n\nGreat catch, fixed and pushed.\n\nThanks,\nKalin",
]


def _seed_sent(gmail: FakeGmailBackend) -> None:
    for i, body in enumerate(_SENT_BODIES):
        gmail.add_message(_sent_msg(f"s{i}", body=body))


def _build_agent(
    tmp_path: Path,
    gmail: FakeGmailBackend,
    *,
    memory_disabled: bool = False,
) -> EmailTriageAgent:
    """Build EmailTriageAgent with an injected FakeGmailBackend and tmp dbs."""
    cfg = EmailAgentConfig(
        gmail_backend=gmail,
        calendar_backend=_MinimalCalendarBackend(),
        db_path=str(tmp_path / "state.db"),
        memory_db_path=str(tmp_path / "memory.db"),
        silent_mode=True,
        debug=False,
    )

    def _do_build() -> EmailTriageAgent:
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

    if memory_disabled:
        prior = os.environ.get("GAIA_MEMORY_DISABLED")
        os.environ["GAIA_MEMORY_DISABLED"] = "1"
        try:
            return _do_build()
        finally:
            if prior is None:
                del os.environ["GAIA_MEMORY_DISABLED"]
            else:
                os.environ["GAIA_MEMORY_DISABLED"] = prior
    return _do_build()


def _invoke(tool_name: str, *args, **kwargs) -> dict:
    from gaia.agents.base.tools import _TOOL_REGISTRY

    entry = _TOOL_REGISTRY.get(tool_name)
    assert entry is not None, f"{tool_name} not registered"
    return json.loads(entry["function"](*args, **kwargs))


_SEND_METHODS = {"send_draft", "send_message"}


def _sent_calls(gmail: FakeGmailBackend) -> list:
    return [c for c in gmail.transport.calls if c[0] in _SEND_METHODS]


# ---------------------------------------------------------------------------
# Deterministic feature extraction (pure functions)
# ---------------------------------------------------------------------------


class TestFeatureExtraction:
    def test_greeting_with_name_becomes_template(self):
        assert extract_greeting("Hi Bob,\n\nbody") == "Hi {name},"
        assert extract_greeting("Hello Maria!\ntext") == "Hello {name}!"

    def test_greeting_without_name_kept_verbatim(self):
        assert extract_greeting("Hey,\n\nbody") == "Hey,"

    def test_non_greeting_first_line_yields_none(self):
        assert extract_greeting("The report is attached.\n\nThanks,") is None

    def test_signoff_with_name(self):
        body = "Hi,\n\nAll set.\n\nThanks,\nKalin"
        assert extract_signoff(body) == "Thanks,\nKalin"

    def test_signoff_phrase_only(self):
        assert extract_signoff("Hi,\n\nAll set.\n\nCheers") == "Cheers"

    def test_no_signoff_yields_none(self):
        assert extract_signoff("Hi,\n\nSee the attached report today.") is None

    def test_strip_quoted_text_removes_reply_history(self):
        body = (
            "Sounds good.\n\nThanks,\nKalin\n\n"
            "On Mon, Jun 15, 2026 Sam <sam@example.com> wrote:\n"
            "> secret quoted content\n> more quoted"
        )
        stripped = strip_quoted_text(body)
        assert "quoted" not in stripped
        assert stripped.startswith("Sounds good.")

    def test_classify_formality(self):
        assert classify_formality("Hey, yeah sounds good! btw thx") == "casual"
        assert (
            classify_formality("Dear Dr. Smith, please find attached. Sincerely,")
            == "formal"
        )

    def test_build_profile_is_deterministic_and_aggregates(self):
        profile_a = build_profile_from_bodies(list(_SENT_BODIES), mailbox="google")
        profile_b = build_profile_from_bodies(list(_SENT_BODIES), mailbox="google")
        profile_b["built_at"] = profile_a["built_at"]
        assert profile_a == profile_b
        assert profile_a["greeting"] == "Hi {name},"
        assert profile_a["signoff"] == "Thanks,\nKalin"
        assert profile_a["sample_size"] == len(_SENT_BODIES)
        assert profile_a["formality"] == "casual"
        assert profile_a["median_word_count"] > 0


# ---------------------------------------------------------------------------
# build_style_profile tool (AC1 + AC3 + no-send)
# ---------------------------------------------------------------------------


class TestBuildStyleProfile:
    def test_builds_profile_from_sent_sample(self, tmp_path):
        gmail = FakeGmailBackend()
        _seed_sent(gmail)
        agent = _build_agent(tmp_path, gmail)
        try:
            result = _invoke("build_style_profile")
            assert result["ok"] is True, f"build_style_profile failed: {result}"
            profile = result["data"]["profile"]
            assert profile["greeting"] == "Hi {name},"
            assert profile["signoff"] == "Thanks,\nKalin"
            assert profile["sample_size"] == len(_SENT_BODIES)
            assert result["data"]["persisted"] is True
        finally:
            agent.close_db()

    def test_build_triggers_no_send_side_effect(self, tmp_path):
        """Test-AC: profile building never sends or even drafts anything."""
        gmail = FakeGmailBackend()
        _seed_sent(gmail)
        agent = _build_agent(tmp_path, gmail)
        try:
            result = _invoke("build_style_profile")
            assert result["ok"] is True
            assert _sent_calls(gmail) == []
            assert not any(c[0] == "create_draft" for c in gmail.transport.calls)
        finally:
            agent.close_db()

    def test_profile_stores_no_raw_sent_content(self, tmp_path):
        """AC3: only derived features are persisted — never body text."""
        gmail = FakeGmailBackend()
        _seed_sent(gmail)
        agent = _build_agent(tmp_path, gmail)
        try:
            assert _invoke("build_style_profile")["ok"] is True
        finally:
            agent.close_db()

        from gaia.agents.base.memory_store import MemoryStore

        store = MemoryStore(tmp_path / "memory.db")
        rows = store.get_by_entity(_STYLE_ENTITY)
        assert len(rows) == 1, f"expected 1 profile record, got {len(rows)}"
        assert _PRIVATE_MARKER not in rows[0]["content"]
        # Recipient names must not leak either — the greeting is a template.
        assert "Sam" not in rows[0]["content"]

    def test_rebuild_keeps_single_record(self, tmp_path):
        gmail = FakeGmailBackend()
        _seed_sent(gmail)
        agent = _build_agent(tmp_path, gmail)
        try:
            for _ in range(3):
                assert _invoke("build_style_profile")["ok"] is True
        finally:
            agent.close_db()

        from gaia.agents.base.memory_store import MemoryStore

        store = MemoryStore(tmp_path / "memory.db")
        assert len(store.get_by_entity(_STYLE_ENTITY)) == 1

    def test_insufficient_sent_history_fails_actionably(self, tmp_path):
        gmail = FakeGmailBackend()
        gmail.add_message(_sent_msg("only", body="Hi,\n\nok.\n\nThanks,\nKalin"))
        agent = _build_agent(tmp_path, gmail)
        try:
            result = _invoke("build_style_profile")
            assert result["ok"] is False
            assert "Sent" in result["error"]
            assert "at least" in result["error"]
        finally:
            agent.close_db()

    def test_memory_disabled_builds_in_process_only(self, tmp_path):
        gmail = FakeGmailBackend()
        _seed_sent(gmail)
        agent = _build_agent(tmp_path, gmail, memory_disabled=True)
        try:
            result = _invoke("build_style_profile")
            assert result["ok"] is True
            assert result["data"]["persisted"] is False
            assert "google" in agent._style_profiles
        finally:
            agent.close_db()


# ---------------------------------------------------------------------------
# AC2 (deterministic): the profile shapes the draft-composition prompt,
# and draft_reply stays a draft — no send side-effect.
# ---------------------------------------------------------------------------


class TestProfileShapesDraftComposition:
    def test_prompt_has_no_style_section_before_build(self, tmp_path):
        gmail = FakeGmailBackend()
        _seed_sent(gmail)
        agent = _build_agent(tmp_path, gmail)
        try:
            assert "VOICE & STYLE PROFILE (learned locally" not in agent.system_prompt
        finally:
            agent.close_db()

    def test_prompt_carries_profile_features_after_build(self, tmp_path):
        gmail = FakeGmailBackend()
        _seed_sent(gmail)
        agent = _build_agent(tmp_path, gmail)
        try:
            # Populate the lazy prompt cache first — the tool must refresh it
            # mid-session, not rely on first access happening after the build.
            assert "VOICE & STYLE PROFILE (learned locally" not in agent.system_prompt
            assert _invoke("build_style_profile")["ok"] is True
            prompt = agent.system_prompt
            assert "VOICE & STYLE PROFILE (learned locally" in prompt
            assert "Hi {name}," in prompt
            assert "Thanks,\nKalin" in prompt
            assert "casual" in prompt
        finally:
            agent.close_db()

    def test_draft_reply_creates_draft_but_never_sends(self, tmp_path):
        """Test-AC: draft generation triggers NO send side-effect."""
        gmail = FakeGmailBackend()
        _seed_sent(gmail)
        gmail.add_message(_inbox_msg("in1"))
        agent = _build_agent(tmp_path, gmail)
        try:
            assert _invoke("build_style_profile")["ok"] is True
            result = _invoke(
                "draft_reply",
                "in1",
                "Hi Sam,\n\nNumbers attached.\n\nThanks,\nKalin",
            )
            assert result["ok"] is True, f"draft_reply failed: {result}"
            assert result["data"]["draft_id"]
            drafted = [c for c in gmail.transport.calls if c[0] == "create_draft"]
            assert len(drafted) == 1
            assert _sent_calls(gmail) == [], (
                "draft_reply must never send — found send calls: "
                f"{_sent_calls(gmail)}"
            )
        finally:
            agent.close_db()

    def test_clear_style_profile_removes_prompt_section(self, tmp_path):
        gmail = FakeGmailBackend()
        _seed_sent(gmail)
        agent = _build_agent(tmp_path, gmail)
        try:
            assert _invoke("build_style_profile")["ok"] is True
            assert "VOICE & STYLE PROFILE (learned locally" in agent.system_prompt
            result = _invoke("clear_style_profile")
            assert result["ok"] is True
            assert result["data"]["cleared"] == ["google"]
            assert "VOICE & STYLE PROFILE (learned locally" not in agent.system_prompt
        finally:
            agent.close_db()


# ---------------------------------------------------------------------------
# Persistence across restart (local-only storage)
# ---------------------------------------------------------------------------


class TestProfilePersistsAcrossRestart:
    def test_profile_survives_restart(self, tmp_path):
        gmail = FakeGmailBackend()
        _seed_sent(gmail)
        agent_a = _build_agent(tmp_path, gmail)
        try:
            assert _invoke("build_style_profile")["ok"] is True
        finally:
            agent_a.close_db()

        agent_b = _build_agent(tmp_path, FakeGmailBackend())
        try:
            assert "google" in agent_b._style_profiles
            profile = agent_b._style_profiles["google"]
            assert profile["greeting"] == "Hi {name},"
            assert profile["signoff"] == "Thanks,\nKalin"
            assert "VOICE & STYLE PROFILE (learned locally" in agent_b.system_prompt
        finally:
            agent_b.close_db()

    def test_clear_persists_across_restart(self, tmp_path):
        gmail = FakeGmailBackend()
        _seed_sent(gmail)
        agent_a = _build_agent(tmp_path, gmail)
        try:
            assert _invoke("build_style_profile")["ok"] is True
            assert _invoke("clear_style_profile")["ok"] is True
        finally:
            agent_a.close_db()

        agent_b = _build_agent(tmp_path, FakeGmailBackend())
        try:
            assert agent_b._style_profiles == {}
            assert "VOICE & STYLE PROFILE (learned locally" not in agent_b.system_prompt
        finally:
            agent_b.close_db()
