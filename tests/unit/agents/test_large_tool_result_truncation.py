# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Regression tests for large tool-result truncation (#2620).

Today, an oversized tool result gets shortened by slicing the serialized
JSON string in half and gluing the two ends back together -- almost always
cutting through the middle of a list item, which corrupts the JSON the
assistant reads on its next turn. These tests build realistic oversized
payloads (Gmail/Outlook-shaped envelope items: id/threadId/sender/subject/
snippet/labelIds) and assert:

  - the truncated output always parses (AC1)
  - only whole items are dropped, never a mid-item slice (AC2)
  - the size cap derives from the device profile, conservatively (AC3)
  - payloads already under the cap are returned untouched (AC4)
  - the truncation event reaches the real module logger even when the
    console is silent (AC5)
  - the three prose call sites keep returning a plain string rather than a
    JSON envelope (AC7 / reflection C2)
"""

from __future__ import annotations

import ast
import inspect
import json
import logging

import pytest

import gaia.agents.base.agent as agent_module
from gaia.agents.base.agent import Agent
from gaia.agents.base.console import SilentConsole
from gaia.llm.lemonade_client import truncation_budget

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


class _TestAgent(Agent):
    """Minimal concrete Agent: no LLM/network access, no tools."""

    def _get_system_prompt(self):
        return "test"

    def _register_tools(self):
        pass


def make_agent(**kwargs) -> _TestAgent:
    kwargs.setdefault("silent_mode", True)
    kwargs.setdefault("skip_lemonade", True)
    return _TestAgent(**kwargs)


def _email_item(i: int) -> dict:
    """A realistic Gmail/Outlook envelope item, not filler text -- so a
    mid-string cut actually reproduces the reported corruption."""
    return {
        "id": f"msg-{i:04d}",
        "threadId": f"thread-{i:04d}",
        "sender": f"person{i}@example.com",
        "subject": f"Re: quarterly planning sync #{i} -- action needed by Friday",
        "snippet": (
            f"Hi team, following up on item {i} from yesterday's call. "
            "Please review the attached notes and let me know if you have "
            "any concerns before we finalize the roadmap for next quarter."
        ),
        "labelIds": ["INBOX", "UNREAD", "CATEGORY_PERSONAL"],
    }


def _messages_payload(min_chars: int = 35000, key: str = "messages") -> dict:
    """Build ``{key: [...]}`` whose compact JSON is at least ``min_chars`` long."""
    items = []
    i = 0
    while True:
        items.append(_email_item(i))
        if len(json.dumps({key: items})) >= min_chars:
            return {key: items}
        i += 1


def _bare_list_payload(min_chars: int = 35000) -> list:
    items = []
    i = 0
    while len(json.dumps(items)) < min_chars:
        items.append(_email_item(i))
        i += 1
    return items


# ---------------------------------------------------------------------------
# AC1 -- always-valid JSON
# ---------------------------------------------------------------------------


class TestAlwaysValidJson:
    def test_messages_dict(self):
        agent = make_agent()
        payload = _messages_payload(key="messages")
        result = agent._truncate_large_content(payload, max_chars=20000, as_json=True)
        parsed = json.loads(result)  # must not raise
        assert parsed["truncated"] is True

    def test_awaiting_reply_dict(self):
        agent = make_agent()
        payload = _messages_payload(key="awaiting_reply")
        result = agent._truncate_large_content(payload, max_chars=20000, as_json=True)
        parsed = json.loads(result)
        assert parsed["truncated"] is True

    def test_issues_dict_jira_shape(self):
        agent = make_agent()
        payload = _messages_payload(key="issues")
        result = agent._truncate_large_content(payload, max_chars=20000, as_json=True)
        parsed = json.loads(result)
        assert parsed["truncated"] is True

    def test_bare_list(self):
        agent = make_agent()
        items = _bare_list_payload()
        result = agent._truncate_large_content(items, max_chars=20000, as_json=True)
        parsed = json.loads(result)
        assert isinstance(parsed, list)

    def test_oversized_dict_with_no_list_member(self):
        """The true fallback: an oversized dict of scalars, nothing to drop."""
        agent = make_agent()
        payload = {"summary": "x" * 40000, "count": 5}
        result = agent._truncate_large_content(payload, max_chars=20000, as_json=True)
        parsed = json.loads(result)
        assert parsed["truncated"] is True
        assert parsed["original_chars"] > 20000

    def test_single_item_exceeding_budget_alone(self):
        """Reflection C3 floor: one item alone bigger than the cap must
        still produce parseable JSON rather than a mid-slice."""
        agent = make_agent()
        payload = {"messages": [{"id": "only-one", "body": "z" * 40000}]}
        result = agent._truncate_large_content(payload, max_chars=20000, as_json=True)
        json.loads(result)  # must not raise

    def test_combined_multiple_list_fields(self):
        """Reflection A2: multiple list-valued fields -- the largest is
        trimmed first, repeating until the payload fits."""
        agent = make_agent()
        big = _messages_payload(min_chars=30000, key="messages")["messages"]
        small = [_email_item(i) for i in range(3)]
        payload = {"messages": big, "awaiting_reply": small}
        result = agent._truncate_large_content(payload, max_chars=20000, as_json=True)
        parsed = json.loads(result)
        assert len(parsed["messages"]) < len(big)


# ---------------------------------------------------------------------------
# AC2 -- whole items only, dropped count reported
# ---------------------------------------------------------------------------


class TestWholeItemsOnly:
    def test_surviving_items_match_originals_and_are_a_clean_prefix(self):
        agent = make_agent()
        payload = _messages_payload(key="messages")
        original_items = payload["messages"]
        result = agent._truncate_large_content(payload, max_chars=20000, as_json=True)
        parsed = json.loads(result)
        kept = parsed["messages"]

        assert len(kept) < len(original_items)
        # Every surviving item is byte-identical to its original counterpart
        # -- never a prefix/suffix fragment of one.
        assert kept == original_items[: len(kept)]
        assert parsed["truncated"] is True
        assert parsed["truncated_fields"]["messages"]["total"] == len(original_items)
        assert parsed["truncated_fields"]["messages"]["returned"] == len(kept)


# ---------------------------------------------------------------------------
# AC3 -- cap derives from the device profile, conservatively
# ---------------------------------------------------------------------------


class TestDeviceProfileBudget:
    def test_npu_unchanged(self):
        assert truncation_budget("npu") == (30000, 20000)

    def test_unset_device_gets_conservative_cap(self):
        # Reflection C1: None must NOT earn the larger GPU budget.
        assert truncation_budget(None) == (30000, 20000)

    def test_gpu_doubles(self):
        assert truncation_budget("gpu") == (60000, 40000)

    def test_cpu_doubles(self):
        assert truncation_budget("cpu") == (60000, 40000)

    def test_no_inline_30000_literal_left_in_handle_large_tool_result(self):
        src = inspect.getsource(Agent._handle_large_tool_result)
        assert "30000" not in src


# ---------------------------------------------------------------------------
# AC4 -- under the cap is untouched
# ---------------------------------------------------------------------------


class TestUnderCapUntouched:
    def test_dict_untouched_no_marker_no_log(self, caplog):
        agent = make_agent()
        payload = {"messages": [_email_item(0), _email_item(1)]}
        caplog.set_level(logging.WARNING, logger="gaia.agents.base.agent")
        caplog.clear()

        result = agent._truncate_large_content(payload, max_chars=20000, as_json=True)

        assert result == json.dumps(payload, default=agent._json_serialize_fallback)
        assert "truncated" not in result
        assert len(caplog.records) == 0

    def test_list_untouched(self):
        agent = make_agent()
        payload = [_email_item(0), _email_item(1)]
        result = agent._truncate_large_content(payload, max_chars=20000, as_json=True)
        assert result == json.dumps(payload, default=agent._json_serialize_fallback)

    def test_test_results_bypass_still_untouched_regardless_of_size(self):
        # Deliberately out of scope (#2620) -- the code agent needs full
        # failing-test output intact no matter how large.
        agent = make_agent()
        huge = {"test_results": "F" * 50000}
        result = agent._truncate_large_content(huge, max_chars=100, as_json=True)
        assert result == json.dumps(huge, default=agent._json_serialize_fallback)
        assert len(result) > 100


# ---------------------------------------------------------------------------
# AC5 -- the event reaches the real logger
# ---------------------------------------------------------------------------


class TestLoggerReceivesWarning:
    def test_handle_large_tool_result_logs_once_with_silent_console(self, caplog):
        agent = make_agent(device=None)
        assert isinstance(agent.console, SilentConsole)

        caplog.set_level(logging.WARNING, logger="gaia.agents.base.agent")
        caplog.clear()

        payload = _messages_payload(key="messages")
        original_len = len(
            json.dumps(payload, default=agent._json_serialize_fallback)
        )
        conversation: list = []

        notices = []
        agent.console.print_info = lambda msg: notices.append(msg)

        agent._handle_large_tool_result("list_inbox", payload, conversation)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        message = warnings[0].getMessage()
        assert str(original_len) in message
        assert "30000" in message  # unset device -> NPU-conservative threshold
        assert len(notices) == 1  # console.print_info still fires exactly once

    def test_handle_large_tool_result_no_log_under_cap(self, caplog):
        agent = make_agent(device=None)
        caplog.set_level(logging.WARNING, logger="gaia.agents.base.agent")
        caplog.clear()

        small = {"messages": [_email_item(0)]}
        conversation: list = []
        result = agent._handle_large_tool_result("list_inbox", small, conversation)

        assert result == small
        assert len(caplog.records) == 0


# ---------------------------------------------------------------------------
# AC7 / reflection C2 -- prose call sites stay prose
# ---------------------------------------------------------------------------


class TestProseCallSitesStayProse:
    def test_default_is_prose_not_json_envelope(self):
        agent = make_agent()
        payload = {"summary": "y" * 5000}
        result = agent._truncate_large_content(payload, max_chars=500)
        assert "...[truncated]..." in result
        with pytest.raises(json.JSONDecodeError):
            json.loads(result)

    def test_create_tool_message_uses_prose_path(self):
        # _create_tool_message (agent.py ~2547) must not opt into as_json.
        src = inspect.getsource(Agent._create_tool_message)
        assert "as_json=True" not in src

    def test_as_json_true_used_exactly_once_in_module(self):
        # Only _handle_large_tool_result's internal call needs guaranteed
        # JSON; every other call site (_create_tool_message, the plan-context
        # and error-recovery prompts) stays on the prose default. AST-based
        # (not a text search) so a docstring mentioning the literal
        # ``as_json=True`` spelling can't produce a false match.
        tree = ast.parse(inspect.getsource(agent_module))
        hits = [
            kw
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for kw in node.keywords
            if kw.arg == "as_json"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value is True
        ]
        assert len(hits) == 1


# ---------------------------------------------------------------------------
# Misc invariants (constraints section)
# ---------------------------------------------------------------------------


class TestSignatureAndScalarInvariants:
    def test_max_chars_default_matches_docstring(self):
        sig = inspect.signature(Agent._truncate_large_content)
        assert sig.parameters["max_chars"].default == 20000

    def test_short_string_passthrough(self):
        agent = make_agent()
        assert agent._truncate_large_content("hello", max_chars=20000) == "hello"

    def test_issues_and_list_branches_no_longer_end_in_bracket_slice(self):
        # AC6: the two latent unsafe branches are gone from the source.
        src = inspect.getsource(Agent._truncate_large_content)
        assert "[:max_chars]" not in src
