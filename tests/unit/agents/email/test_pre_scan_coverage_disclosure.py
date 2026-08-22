# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Failing acceptance tests for pre-scan coverage-disclosure prompting (#2584).

Two instruction sites are planned to change so the model actually TELLS the
user a pre-scan only covers part of the inbox:

- ``EmailTriageAgent``'s system prompt (``_SYSTEM_PROMPT`` in
  ``gaia_agent_email.agent``), in its "PRE-SCAN BEHAVIOR:" section.
- the ``pre_scan_inbox`` tool's docstring (``read_tools.py``).

These are substring/keyword checks only -- the exact model-authored prose is
not pinned -- but the substring set is specific enough that today's prompt
(which says nothing about partial-scan honesty) cannot pass it by accident.
"""

from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")  # noqa: E402
from gaia_agent_email.agent import _SYSTEM_PROMPT  # noqa: E402

# Phrases that would tell a reader a pre-scan is a PARTIAL view of the
# inbox, not the whole thing. Deliberately more than one common word so the
# test cannot pass by an accidental, unrelated match.
_COVERAGE_HONESTY_PHRASES = (
    "not the whole inbox",
    "not your whole inbox",
    "not the entire inbox",
    "partial scan",
    "only scanned",
    "not every message",
    "out of",
)


def _has_coverage_disclosure(text: str) -> bool:
    lowered = text.lower()
    mentions_scan_size = "scanned" in lowered or "coverage" in lowered
    mentions_partial = any(p in lowered for p in _COVERAGE_HONESTY_PHRASES)
    return mentions_scan_size and mentions_partial


# Section headers in _SYSTEM_PROMPT are ALL-CAPS lines ending in ":" on
# their own line (e.g. "ACTIONS:", "PRE-SCAN BEHAVIOR:",
# "NUMBERING ITEMS IN YOUR REPLY:"). Bound a section by the NEXT such
# header rather than a fixed character count -- a fixed window silently
# truncates the section (and the test with it) whenever earlier prose in
# the same section grows, which is exactly what #2900 did by adding two
# paragraphs of narrower-tool routing guidance ahead of the coverage-
# disclosure paragraph.
_NEXT_HEADER_RE = re.compile(r"\n\n[A-Z][A-Z0-9 &/-]{3,50}:\n")


def _extract_section(prompt: str, header: str) -> str:
    start = prompt.find(header)
    assert start != -1, f"{header!r} section not found in _SYSTEM_PROMPT"
    body_start = start + len(header)
    m = _NEXT_HEADER_RE.search(prompt, body_start)
    end = m.start() if m else len(prompt)
    return prompt[start:end]


class TestSystemPromptDisclosesCoverage:
    def test_pre_scan_behavior_section_tells_the_model_to_disclose_coverage(self):
        section = _extract_section(_SYSTEM_PROMPT, "PRE-SCAN BEHAVIOR:")

        assert _has_coverage_disclosure(section), (
            "PRE-SCAN BEHAVIOR section must instruct the model to disclose "
            "that a pre-scan covers only part of the inbox (coverage "
            f"honesty, #2584); got section:\n{section}"
        )


def _make_email_agent(fake_gmail, tmp_path):
    """Construct an EmailTriageAgent with the gmail backend injected and the
    AgentSDK mocked, so tool registration runs without a live LLM.
    Mirrors the helper in ``test_pre_scan_counts.py``.
    """
    from unittest.mock import MagicMock, patch

    from gaia_agent_email.agent import EmailTriageAgent
    from gaia_agent_email.config import EmailAgentConfig

    cfg = EmailAgentConfig(
        gmail_backend=fake_gmail,
        db_path=str(tmp_path / "state.db"),
        silent_mode=True,
    )
    with (
        patch("gaia.llm.lemonade_manager.LemonadeManager.ensure_ready"),
        patch("gaia.agents.base.agent.AgentSDK") as mock_sdk,
    ):
        mock_sdk.return_value = MagicMock()
        agent = EmailTriageAgent(config=cfg)
    return agent


def _registered_tool_doc(name: str) -> str:
    from gaia.agents.base.tools import _TOOL_REGISTRY

    return inspect.getdoc(_TOOL_REGISTRY[name]["function"]) or ""


class TestPreScanToolDocstringDisclosesCoverage:
    def test_docstring_tells_the_model_to_state_scan_coverage(self, tmp_path):
        from tests.fixtures.email.fake_gmail import FakeGmailBackend

        agent = _make_email_agent(FakeGmailBackend(), tmp_path)
        try:
            doc = _registered_tool_doc("pre_scan_inbox")
        finally:
            agent.close_db()

        assert _has_coverage_disclosure(doc), (
            "pre_scan_inbox's docstring must disclose that a pre-scan is a "
            f"partial view of the inbox (coverage honesty, #2584); got:\n{doc}"
        )
