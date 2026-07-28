# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``draft_reply`` / ``draft_forward`` ask for content instead of drafting (#2524).

Asked to draft a reply or forward, the agent located the source message
correctly (message-id resolution, #2403, already worked) and then asked the
user to supply the finished reply/forward text — the thing it was asked to
write. No draft was produced.

Root cause: neither tool's docstring (the schema the tool-calling model
actually sees — ``_build_openai_tool_schemas`` puts the *whole* docstring
verbatim into the function's ``description``, and per-parameter descriptions
are never populated, see ``gaia.agents.base.tools.tool``) nor the
unconditional system prompt ever told the model that composing ``body`` is
its own job. The only place that said "write the draft body yourself" was
``voice_profile.render_style_guidance`` — appended ONLY once a voice profile
has been learned from enough Sent-mail history (``agent.py:_get_system_prompt``),
which does not exist for a fresh mailbox. That explains why ``draft_forward``
asked too, even though its ``body`` parameter was already *optional*
(``= ""``) — this was never a required-parameter problem, it was a missing
authorship contract.

The fix adds that contract in the two unconditional, always-present spots:
the ``draft_reply`` / ``draft_forward`` docstrings and the base
REPLYING/DRAFTING section of the system prompt.

Because the actual "ask instead of draft" behavior is a choice the LLM makes
when generating tool-call arguments, it cannot be exercised hermetically
(no Lemonade, per repo convention for this test tier). Tests here instead
pin the two contracts that are the direct, verifiable fix: the tool-schema
description text the model receives, and the unconditional system-prompt
text. Both assertions fail against the pre-fix docstrings/prompt and pass
after. The remaining tests are regression guards on the tool's actual
runtime behavior: explicit user text still passes through verbatim, and no
send-capable backend method is ever reachable from a drafting call — the
send/forward confirmation floor is untouched.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# parents[0] = tests/, [1] = python/, [2] = email/, [3] = agents/,
# [4] = hub/, [5] = repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.agent import _SYSTEM_PROMPT, EmailTriageAgent  # noqa: E402
from gaia_agent_email.config import EmailAgentConfig  # noqa: E402

from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402
from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402


class _MinimalCalendarBackend:
    pass


def _msg(
    msg_id: str, *, sender: str = "boss@example.com", subject: str = "Q3 plan"
) -> dict:
    return {
        "id": msg_id,
        "threadId": msg_id,
        "internalDate": "1700000000000",
        "snippet": "Can you take a look at the Q3 plan and get back to me?",
        "labelIds": ["INBOX", "UNREAD"],
        "payload": {
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": "Thu, 23 Jul 2026 09:00:00 +0000"},
            ]
        },
    }


def _build_agent(tmp_path: Path, backend: FakeGmailBackend) -> EmailTriageAgent:
    """Build a hermetic EmailTriageAgent — FakeGmailBackend, no Lemonade.

    Memory is disabled outright (unneeded here, and it sidesteps FAISS/
    embedder mocking entirely) via env var, same pattern used by
    test_email_behavioral_learning.py's ``memory_disabled=True`` path.
    """
    import os

    cfg = EmailAgentConfig(
        gmail_backend=backend,
        calendar_backend=_MinimalCalendarBackend(),
        db_path=str(tmp_path / "state.db"),
        memory_db_path=str(tmp_path / "memory.db"),
        silent_mode=True,
        debug=False,
    )
    old = os.environ.get("GAIA_MEMORY_DISABLED")
    os.environ["GAIA_MEMORY_DISABLED"] = "1"
    try:
        with patch("gaia.agents.base.agent.AgentSDK") as mock_sdk:
            mock_sdk.return_value = MagicMock()
            return EmailTriageAgent(config=cfg)
    finally:
        if old is None:
            del os.environ["GAIA_MEMORY_DISABLED"]
        else:
            os.environ["GAIA_MEMORY_DISABLED"] = old


def _call_tool(name: str, *args, **kwargs) -> dict:
    """Invoke a registered tool by name exactly as the agent loop would."""
    entry = _TOOL_REGISTRY.get(name)
    assert entry is not None, f"{name} tool not registered"
    return json.loads(entry["function"](*args, **kwargs))


# ---------------------------------------------------------------------------
# The actual fix: schema/prompt contracts the tool-calling model reads.
# These fail against the pre-fix docstrings/prompt and pass after.
# ---------------------------------------------------------------------------


class TestBodyAuthorshipContract:
    """The model must be told IT authors the body — not the user."""

    def test_draft_reply_description_tells_model_to_write_body_itself(self, tmp_path):
        backend = FakeGmailBackend(user_email="me@example.com")
        backend.add_message(_msg("m1"))
        _build_agent(tmp_path, backend)

        description = _TOOL_REGISTRY["draft_reply"]["description"]
        lowered = description.lower()
        assert "you write it" in lowered or "compose" in lowered, (
            "draft_reply's docstring (the schema a tool-calling model receives "
            "as its function description, per _build_openai_tool_schemas) must "
            "instruct the model to compose `body` itself instead of waiting on "
            "the user to supply finished prose"
        )
        assert "do not ask the user" in lowered or "never ask the user" in lowered

    def test_draft_forward_description_tells_model_to_write_body_itself(self, tmp_path):
        backend = FakeGmailBackend(user_email="me@example.com")
        backend.add_message(_msg("m1"))
        _build_agent(tmp_path, backend)

        description = _TOOL_REGISTRY["draft_forward"]["description"]
        lowered = description.lower()
        assert "you write it" in lowered
        assert "do not ask the user" in lowered or "never ask the user" in lowered

    def test_system_prompt_establishes_authorship_unconditionally(self):
        """The base (always-present) system prompt, not the voice-profile
        fragment that only exists once Sent-mail history has been learned —
        a fresh mailbox with no learned voice profile never sees that
        fragment (agent.py:_get_system_prompt), so the base prompt is the
        only unconditional home for this instruction.
        """
        lowered = _SYSTEM_PROMPT.lower()
        assert "you write the reply/forward body yourself" in lowered
        assert "never ask the user to" in lowered


# ---------------------------------------------------------------------------
# Regression guards on actual tool behavior.
# ---------------------------------------------------------------------------


class TestExplicitContentHonoredVerbatim:
    def test_draft_reply_uses_explicit_body_verbatim(self, tmp_path):
        backend = FakeGmailBackend(user_email="me@example.com")
        backend.add_message(_msg("m1"))
        _build_agent(tmp_path, backend)

        exact_text = "Sounds good, let's sync Thursday at 2pm."
        result = _call_tool("draft_reply", "m1", exact_text)
        assert result["ok"] is True
        assert result["data"]["body_preview"] == exact_text

        create_calls = [c for c in backend.transport.calls if c[0] == "create_draft"]
        assert len(create_calls) == 1
        assert create_calls[0][1]["body"] == exact_text

    def test_draft_forward_uses_explicit_note_verbatim(self, tmp_path):
        backend = FakeGmailBackend(user_email="me@example.com")
        backend.add_message(_msg("m1"))
        _build_agent(tmp_path, backend)

        note = "FYI, thought this was relevant."
        result = _call_tool("draft_forward", "m1", "colleague@example.com", note)
        assert result["ok"] is True

        create_calls = [c for c in backend.transport.calls if c[0] == "create_draft"]
        assert len(create_calls) == 1
        assert create_calls[0][1]["body"].startswith(note)


class TestDraftingProducesNonEmptyDraft:
    def test_draft_reply_with_composed_body_creates_draft(self, tmp_path):
        """Regression guard: a body an agent COMPOSED (not user-dictated) still
        reaches create_draft untouched — nothing downstream re-validates or
        discards agent-authored prose.
        """
        backend = FakeGmailBackend(user_email="me@example.com")
        backend.add_message(_msg("m1"))
        _build_agent(tmp_path, backend)

        composed = (
            "Thanks for sending this over — I'll review the Q3 plan and get "
            "back to you by end of week."
        )
        result = _call_tool("draft_reply", "m1", composed)
        assert result["ok"] is True
        assert result["data"]["draft_id"]
        assert result["data"]["body_preview"]


# ---------------------------------------------------------------------------
# Drafting still NEVER sends — the confirmation floor is untouched.
# ---------------------------------------------------------------------------


class TestSendSurfaceUntouched:
    def test_send_confirmation_floor_unchanged(self):
        """send_draft/send_now stay confirm-gated; draft_reply/draft_forward
        stay outside the confirmation floor (they were already safe — the
        fix must not move that line either direction).
        """
        floor = EmailTriageAgent.CONFIRMATION_REQUIRED_TOOLS
        assert "send_draft" in floor
        assert "send_now" in floor
        assert "draft_reply" not in floor
        assert "draft_forward" not in floor

    def test_no_send_capable_method_called_by_drafting(self, tmp_path):
        backend = FakeGmailBackend(user_email="me@example.com")
        backend.add_message(_msg("m1"))
        _build_agent(tmp_path, backend)

        _call_tool("draft_reply", "m1", "Thanks, will follow up.")
        _call_tool("draft_forward", "m1", "colleague@example.com", "FYI")

        method_names = {c[0] for c in backend.transport.calls}
        assert "send_message" not in method_names
        assert "send_draft" not in method_names
