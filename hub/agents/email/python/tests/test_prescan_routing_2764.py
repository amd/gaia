# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""#2764 — the triage card is reachable by more than one phrasing.

Routing itself is decided entirely by the model reading the system prompt
and tool docstrings — there is no pre-LLM router in this codebase to call
hermetically, so a live routing claim can only be proven by
``gaia eval agent`` against a running Lemonade Server (out of scope here per
the run's own constraints; see the PR description for the category/baseline
to run before merge).

What IS testable without a live model, and is exactly the shape of bug this
issue describes:

1. A **content-regression guard** — the intent-class/exclusion-criteria
   language this fix adds is actually present in the docstrings and system
   prompt, and the decision table (``ROUTING.md``) documents all four jobs.
2. A **negative guard against the exact anti-pattern #2764 forbids** — none
   of a held-out set of natural phrasings (drawn from #2762's own J1/J2/J4
   catalogs, never used to write this fix's prompt text) appear verbatim in
   the routing prompts. If a future edit "fixes" a routing miss by pasting
   in the literal phrase that failed, this test catches it: the fix is
   supposed to work by widening the RULE, never by growing the list.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# parents[0]=tests/, [1]=python/, [2]=email/, [3]=agents/, [4]=hub/, [5]=repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.agent import _SYSTEM_PROMPT  # noqa: E402

_PKG_DIR = Path(__file__).resolve().parents[1] / "gaia_agent_email"
_ROUTING_MD = _PKG_DIR / "tools" / "ROUTING.md"
_READ_TOOLS_SRC = (_PKG_DIR / "tools" / "read_tools.py").read_text(encoding="utf-8")
_WAITING_ON_YOU_SRC = (
    _PKG_DIR / "tools" / "waiting_on_you_tools.py"
).read_text(encoding="utf-8")
_FOLLOWUP_SRC = (_PKG_DIR / "tools" / "followup_tools.py").read_text(encoding="utf-8")

# Every routing-relevant prompt surface a model actually reads, concatenated.
_ALL_ROUTING_TEXT = "\n".join(
    [_SYSTEM_PROMPT, _READ_TOOLS_SRC, _WAITING_ON_YOU_SRC, _FOLLOWUP_SRC]
)

# Phrasings drawn verbatim from #2762's own J1/J2/J4 catalogs, deliberately
# NOT used anywhere while writing this fix's prompt text. If any of these
# shows up verbatim in a routing prompt, the "fix" was a phrase-list append,
# not a rule change.
_HELD_OUT_PHRASINGS = [
    "what do I need to deal with today",
    "is there anything I should look at before I log off",
    "did anyone ever reply to me",
    "who owes me a response",
    "anything I need to be somewhere for",
    "do I have anything scheduled from email",
]


class TestRoutingDecisionTableIsWrittenDown:
    def test_routing_md_exists(self):
        assert _ROUTING_MD.is_file(), (
            "ROUTING.md must exist — the decision must be written down, "
            "not left incidental (#2764)"
        )

    def test_routing_md_documents_all_four_jobs(self):
        text = _ROUTING_MD.read_text(encoding="utf-8")
        for job_marker in ("J1", "J2", "J3", "J4"):
            assert job_marker in text

    def test_routing_md_states_direction_disambiguator(self):
        text = _ROUTING_MD.read_text(encoding="utf-8")
        assert "direction" in text.lower()
        assert "pre_scan_inbox" in text and "check_followups" in text

    def test_routing_md_resolves_list_waiting_on_you_overlap(self):
        text = _ROUTING_MD.read_text(encoding="utf-8")
        assert "list_waiting_on_you" in text
        assert "not a J1 destination" in text or "not a second router target" in text


class TestPreScanIsTheDocumentedDefault:
    """pre_scan_inbox must be framed as the default for the whole intent
    class, not gated on "triage"/"review"/"check" wording.
    """

    def test_pre_scan_docstring_states_default_framing(self):
        assert "DEFAULT tool for any open-ended question" in _READ_TOOLS_SRC

    def test_pre_scan_docstring_names_exclusion_signals_not_a_phrase_list(self):
        # The divert-away signals are STRUCTURAL categories (sent mail,
        # named person/thread, calendar language, suspicious-only) —
        # never an enumerated list of literal user sentences.
        assert "check_followups" in _READ_TOOLS_SRC
        assert "ROUTING.md" in _READ_TOOLS_SRC

    def test_system_prompt_states_default_framing(self):
        assert "DEFAULT tool for any open-ended question" in _SYSTEM_PROMPT
        assert "not gated on literal" in _SYSTEM_PROMPT.lower() or (
            "NOT gated on literal" in _SYSTEM_PROMPT
        )


class TestOverlapsAreResolvedExplicitlyNotByRoutingLuck:
    def test_list_waiting_on_you_redirects_to_pre_scan_inbox(self):
        assert "pre_scan_inbox" in _WAITING_ON_YOU_SRC
        assert "NOT the tool for a general" in _WAITING_ON_YOU_SRC

    def test_check_followups_states_direction_disambiguation(self):
        assert "DIRECTION disambiguates" in _FOLLOWUP_SRC
        assert "pre_scan_inbox" in _FOLLOWUP_SRC


class TestNoGrowingPhraseList:
    """The exact anti-pattern #2764 forbids: recreating the bug by pasting
    literal failing phrasings into the prompt instead of widening the rule.
    """

    @pytest.mark.parametrize("phrasing", _HELD_OUT_PHRASINGS)
    def test_held_out_phrasing_is_not_hardcoded(self, phrasing):
        assert phrasing not in _ALL_ROUTING_TEXT, (
            f"held-out phrasing {phrasing!r} appears verbatim in a routing "
            "prompt — routing must generalize via the stated rule/exclusion "
            "criteria, never via an enumerated phrase match (#2764)"
        )
