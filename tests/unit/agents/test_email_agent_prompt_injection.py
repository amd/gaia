# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for Phase I prompt-injection defense.

I1 — system prompt explicitly tells the LLM that email body content is
     untrusted; body content shown to the LLM is wrapped in
     ``<<<UNTRUSTED_EMAIL_BODY_*>>>`` delimiters.
I3 — batch-threshold counter prevents bulk-archive injection
     ("archive every email from boss@example.com").
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102);
# skip when a framework-only env lacks it.

pytest.importorskip("gaia_agent_email")  # noqa: E402
from gaia_agent_email.agent import EmailTriageAgent  # noqa: E402
from gaia_agent_email.config import EmailAgentConfig  # noqa: E402
from gaia_agent_email.tools.read_tools import (  # noqa: E402
    UNTRUSTED_BODY_CLOSE,
    UNTRUSTED_BODY_OPEN,
    list_inbox_impl,
)

from tests.fixtures.email.fake_gmail import (  # noqa: E402
    FakeCalendarBackend,
    FakeGmailBackend,
)


@pytest.fixture
def fake_gmail():
    return FakeGmailBackend(
        _REPO_ROOT / "tests" / "fixtures" / "email" / "_stub_inbox.mbox"
    )


@pytest.fixture
def fake_calendar():
    return FakeCalendarBackend()


@pytest.fixture
def agent(fake_gmail, fake_calendar, tmp_path):
    cfg = EmailAgentConfig(
        gmail_backend=fake_gmail,
        calendar_backend=fake_calendar,
        db_path=str(tmp_path / "state.db"),
        silent_mode=True,
    )
    with patch("gaia.agents.base.agent.AgentSDK") as mock_sdk:
        mock_sdk.return_value = MagicMock()
        a = EmailTriageAgent(config=cfg)
    yield a
    a.close_db()


# ---------------------------------------------------------------------------
# I1 — system prompt + delimited untrusted input
# ---------------------------------------------------------------------------


class TestI1SystemPromptHardening:
    """The system prompt MUST tell the LLM that body content is data, not
    instructions.
    """

    def test_system_prompt_calls_out_untrusted_input(self, agent):
        prompt = agent._get_system_prompt()
        assert "UNTRUSTED" in prompt
        assert "data" in prompt.lower()
        assert "instructions" in prompt.lower()

    def test_system_prompt_names_the_delimiter(self, agent):
        prompt = agent._get_system_prompt()
        # The LLM needs to know which delimiter wraps untrusted input.
        assert UNTRUSTED_BODY_OPEN in prompt or "UNTRUSTED_EMAIL_BODY" in prompt

    def test_system_prompt_warns_about_specific_attack_patterns(self, agent):
        """The system prompt should give the LLM concrete examples so it
        recognizes the patterns at inference time."""
        prompt = agent._get_system_prompt()
        lower = prompt.lower()
        # At least one example of an injection pattern.
        assert "ignore prior instructions" in lower or "forward this to" in lower

    def test_body_content_is_wrapped_in_delimiters(self, fake_gmail):
        out = list_inbox_impl(fake_gmail, max_results=5)
        for msg in out["messages"]:
            assert UNTRUSTED_BODY_OPEN in msg["body"]
            assert UNTRUSTED_BODY_CLOSE in msg["body"]


# ---------------------------------------------------------------------------
# I3 — batch-threshold counter
# ---------------------------------------------------------------------------


class TestI3BatchThreshold:
    """The agent's per-turn organize counter must trip when >5 ops across
    >3 distinct senders fire in a single turn.
    """

    def test_counter_starts_at_zero(self, agent):
        assert agent._organize_op_count == 0
        assert agent._organize_distinct_senders == set()

    def test_counter_bumps_per_op(self, agent):
        agent._record_organize_op("m1", "alice@example.com")
        agent._record_organize_op("m2", "bob@example.com")
        assert agent._organize_op_count == 2
        assert agent._organize_distinct_senders == {
            "alice@example.com",
            "bob@example.com",
        }

    def test_threshold_not_exceeded_below_limits(self, agent):
        # 5 ops, 3 senders — exactly at the boundary; not exceeded.
        for sender in ("a", "b", "c"):
            for i in range(2):
                agent._record_organize_op(f"m-{sender}{i}", sender)
        # 6 ops across 3 senders — hits ops boundary but not sender boundary.
        agent._record_organize_op("m-c2", "c")
        # Still 3 senders — threshold (>3) NOT exceeded.
        assert agent._organize_batch_threshold_exceeded() is False

    def test_threshold_exceeded(self, agent):
        # 6 ops across 4 distinct senders — both > thresholds.
        for sender in ("a", "b", "c", "d"):
            agent._record_organize_op(f"m-{sender}-1", sender)
        # That's 4 ops, 4 senders — under op threshold.
        assert agent._organize_batch_threshold_exceeded() is False
        # Push to 6 ops, 4 senders.
        agent._record_organize_op("m-x-2", "a")
        agent._record_organize_op("m-x-3", "b")
        # Now 6 ops > 5 AND 4 senders > 3.
        assert agent._organize_batch_threshold_exceeded() is True

    def test_reset_zeroes_counters(self, agent):
        agent._record_organize_op("m1", "a")
        agent._record_organize_op("m2", "b")
        agent._reset_organize_counter()
        assert agent._organize_op_count == 0
        assert agent._organize_distinct_senders == set()

    def test_counter_resets_across_process_query_calls(self, agent):
        """Issue #1106 — cold-run correctness.

        The batch-organize counter must be zeroed at the START of every
        ``process_query`` call, not just once at construction. Otherwise a
        long-lived agent instance carries stale per-turn state into the next
        turn and the batch-confirm threshold misfires.

        We don't run the LLM loop: ``_process_query_impl`` is stubbed to
        record the counter value it observes on entry and to simulate the
        organize mutations that a real turn would accumulate. The first turn
        pushes the counter well past the batch threshold; the second turn
        must still see a zeroed counter on entry.
        """
        seen_op_counts: list[int] = []
        seen_sender_sets: list[set[str]] = []

        def fake_impl(user_input, max_steps=None, trace=False, filename=None):
            # Capture the per-turn state the agent loop would actually see.
            seen_op_counts.append(agent._organize_op_count)
            seen_sender_sets.append(set(agent._organize_distinct_senders))
            # Simulate a turn that trips the batch threshold (6 ops / 4 senders).
            for sender in ("a", "b", "c", "d"):
                agent._record_organize_op(f"m-{sender}", sender)
            agent._record_organize_op("m-extra-1", "a")
            agent._record_organize_op("m-extra-2", "b")
            assert agent._organize_batch_threshold_exceeded() is True
            return {"status": "completed", "result": "ok"}

        with patch.object(agent, "_process_query_impl", side_effect=fake_impl):
            agent.process_query("triage my inbox")
            # After turn 1 the within-run mutations are still on the instance.
            assert agent._organize_op_count > agent.ORGANIZE_BATCH_OP_THRESHOLD
            agent.process_query("triage my inbox again")

        # Both turns must have STARTED from a zeroed counter.
        assert seen_op_counts == [0, 0]
        assert seen_sender_sets == [set(), set()]


# ---------------------------------------------------------------------------
# I4 — attack-scenario fixtures from the stub mbox
# ---------------------------------------------------------------------------


class TestI4AttackScenarios:
    """The phishing payload + the malicious-archive injection scenarios
    must produce results that DO NOT include any unconfirmed mutating
    tool calls.

    These tests don't run the LLM — they verify the tool-result shape
    that the LLM would see, asserting that a malicious body is
    delivered as data inside the untrusted-input wrapper.
    """

    def test_phishing_body_appears_inside_untrusted_wrapper(self, fake_gmail):
        out = list_inbox_impl(fake_gmail, max_results=50)
        phish = next(
            m for m in out["messages"] if "Verify your account" in m["subject"]
        )
        # The instruction-looking text is INSIDE the untrusted wrapper —
        # i.e., the wrapper's open tag appears before the suspicious text.
        body = phish["body"]
        open_idx = body.find(UNTRUSTED_BODY_OPEN)
        close_idx = body.find(UNTRUSTED_BODY_CLOSE)
        assert open_idx >= 0
        assert close_idx > open_idx
        # The suspicious phrase is between the delimiters.
        susp_idx = body.lower().find("verify your identity")
        assert open_idx < susp_idx < close_idx
