# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""#2764 — the triage card is reachable by more than one phrasing.

Routing itself is decided entirely by the model reading the system prompt
and tool docstrings — there is no pre-LLM router in this codebase to call
hermetically, so a live routing claim can only be proven by
``gaia eval agent`` against a running Lemonade Server (out of scope here per
the run's own constraints; see the PR description for what gate stands in
for it before merge).

What IS testable without a live model, and is exactly the shape of bug this
issue describes:

1. A **structural-property guard, scoped to the specific block that
   changed** — pre_scan_inbox's own docstring (and the system prompt's
   PRE-SCAN BEHAVIOR section) frames the tool as the default for the whole
   intent class, and names its narrower alternatives by their STABLE TOOL
   IDENTIFIERS (``check_followups``, ``check_suspicious_mail``, ...) rather
   than by an enumerated list of user sentences. Every assertion below is
   scoped to the added block, not the whole file — an unscoped check turned
   out to also match unrelated pre-existing text elsewhere in the same
   files (variable names like ``inbound``/``outbound`` in check_followups'
   *implementation*, or ``check_suspicious_mail`` already being name-dropped
   in the system prompt's PRE-SCAN section before this fix) and would have
   passed whether or not the fix landed. Scoping to the specific block each
   edit touched is what makes these checks pin THIS change rather than
   incidental text nearby.
2. A **negative guard against the exact anti-pattern #2764 forbids** — none
   of a held-out set of natural phrasings (drawn from #2762's own J1/J2/J4
   catalogs, never used to write this fix's prompt text) appear verbatim in
   the routing prompts. If a future edit "fixes" a routing miss by pasting
   in the literal phrase that failed, this test catches it: the fix is
   supposed to work by widening the RULE, never by growing the list.

**Known limitation, stated plainly:** the "default framing" check below is a
wording-pattern heuristic over a handful of alternative phrasings, not a
behavioral test — it can be satisfied by prose that never actually changes
what the model does, and it can (in principle) be defeated by a rewording
that expresses the same idea in a form none of the patterns anticipated. It
exists to catch an accidental regression (the whole paragraph is deleted, or
rewritten to reintroduce the old "triage/review/check"-only framing) between
now and whenever a live eval exists for this behavior — it is not proof the
routing works. Every property test in this module was confirmed to fail
against the pre-fix code (see the PR description for the verification
command) — that is what distinguishes it from a pin that would pass either
way.
"""

from __future__ import annotations

import re
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

# Every routing-relevant prompt surface a model actually reads, concatenated —
# used only by the negative (no-growing-phrase-list) guard below, which is
# deliberately whole-file: a hardcoded phrase anywhere in these files is the
# anti-pattern, not just in the blocks this fix touched.
_ALL_ROUTING_TEXT = "\n".join(
    [_SYSTEM_PROMPT, _READ_TOOLS_SRC, _WAITING_ON_YOU_SRC, _FOLLOWUP_SRC]
)


def _extract_block(text: str, start_marker: str, end_marker: str) -> str:
    """The substring from ``start_marker`` up to (not including) the next
    ``end_marker`` — scopes an assertion to the one docstring/section a
    change actually touched, instead of the whole file.
    """
    start = text.index(start_marker)
    end = text.index(end_marker, start + len(start_marker))
    return text[start:end]


def _window_around(text: str, needle: str, radius: int = 400) -> str:
    """``radius`` characters on each side of ``needle``'s first occurrence.

    Used where the changed text is a short inserted clause inside a large
    function body (e.g. check_followups' docstring paragraph naming
    ``pre_scan_inbox``) rather than a cleanly delimited block.
    """
    i = text.index(needle)
    return text[max(0, i - radius) : i + len(needle) + radius]


# pre_scan_inbox's own docstring/body, up to the next @tool-decorated
# function — excludes check_suspicious_mail's docstring, which separately
# and pre-existingly mentions pre_scan_inbox and is not part of this fix.
_PRE_SCAN_BLOCK = _extract_block(_READ_TOOLS_SRC, "def pre_scan_inbox(", "@tool")

# The system prompt's PRE-SCAN BEHAVIOR section specifically — the ACTIONS
# section above it already listed every tool name including check_followups
# and check_suspicious_mail before this fix, so an unscoped identifier check
# against the whole prompt would never distinguish before/after.
_PRE_SCAN_PROMPT_SECTION = _extract_block(
    _SYSTEM_PROMPT, "PRE-SCAN BEHAVIOR:", "BRIEFING & TASKS:"
)

# list_waiting_on_you's own docstring/body.
_LIST_WAITING_ON_YOU_BLOCK = _extract_block(
    _WAITING_ON_YOU_SRC, "def list_waiting_on_you(", "except"
)

# The ~800-char window around check_followups' new "pre_scan_inbox" mention —
# pre_scan_inbox was never mentioned anywhere in this file before this fix.
_FOLLOWUP_DIRECTION_WINDOW = _window_around(_FOLLOWUP_SRC, "pre_scan_inbox")

# Alternative ways of framing "this tool is the default for the whole
# question class, independent of exact wording" — several patterns, not one
# sentence, so a harmless rewording of the prose doesn't break the test.
_DEFAULT_FRAMING_PATTERNS = [
    re.compile(r"\bdefault\b.{0,150}\bopen-ended\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bopen-ended\b.{0,150}\bdefault\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bregardless of\b.{0,60}\b(words|wording|phrasing)\b", re.IGNORECASE),
]

# Tool identifiers pre_scan_inbox's own docstring must name as its narrower
# alternatives — real, stable API names, never prose.
_NARROWER_TOOL_IDENTIFIERS = ("check_followups", "check_suspicious_mail")

# Directional vocabulary (inbound vs. outbound) that must both be present in
# the window around check_followups' new reference to pre_scan_inbox — a set
# of synonyms on each side, not one exact phrase. NOTE: these terms also
# appear throughout check_followups' pre-existing implementation code
# (variable names, comments about the Sent-folder scan itself), which is why
# this check is windowed around the NEW "pre_scan_inbox" mention rather than
# run against the whole file.
_INBOUND_TERMS = re.compile(r"\b(inbound|received|receiv\w*)\b", re.IGNORECASE)
_OUTBOUND_TERMS = re.compile(r"\b(outbound|sent[- ]mail|sent mail)\b", re.IGNORECASE)

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


def _has_default_framing(text: str) -> bool:
    return any(p.search(text) for p in _DEFAULT_FRAMING_PATTERNS)


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

    def test_routing_md_names_the_narrower_tool_identifiers(self):
        text = _ROUTING_MD.read_text(encoding="utf-8")
        for identifier in (
            "pre_scan_inbox",
            "check_followups",
            "list_waiting_on_you",
        ):
            assert identifier in text


class TestPreScanIsFramedAsDefaultOverAnIntentClass:
    """Property: pre_scan_inbox's own docstring, and the system prompt's
    PRE-SCAN BEHAVIOR section, must frame the tool as the default for the
    open-ended attention question and name their narrower alternatives by
    tool identifier — never by an enumerated list of trigger sentences.
    Every assertion is scoped to the specific block this fix changed (see
    module docstring for why unscoped checks were not trustworthy).
    """

    def test_pre_scan_docstring_has_default_framing(self):
        assert _has_default_framing(_PRE_SCAN_BLOCK)

    def test_system_prompt_pre_scan_section_has_default_framing(self):
        assert _has_default_framing(_PRE_SCAN_PROMPT_SECTION)

    @pytest.mark.parametrize("identifier", _NARROWER_TOOL_IDENTIFIERS)
    def test_pre_scan_docstring_names_a_narrower_tool_by_identifier(self, identifier):
        assert identifier in _PRE_SCAN_BLOCK

    def test_system_prompt_pre_scan_section_names_check_followups(self):
        # check_suspicious_mail was already name-dropped in this section
        # before the fix (it is not new signal); check_followups was not.
        assert "check_followups" in _PRE_SCAN_PROMPT_SECTION


class TestOverlapsAreResolvedExplicitlyNotByRoutingLuck:
    def test_list_waiting_on_you_names_pre_scan_inbox_as_the_general_case(self):
        # Real overlap resolution: the narrower tool's own docstring must
        # point at the broader tool, not merely coexist with it elsewhere
        # in the file.
        assert "pre_scan_inbox" in _LIST_WAITING_ON_YOU_BLOCK

    def test_check_followups_states_both_directions_near_its_pre_scan_mention(self):
        # Both sides of the inbound/outbound distinction must appear near
        # the NEW reference to pre_scan_inbox specifically — see the
        # _INBOUND_TERMS/_OUTBOUND_TERMS comment for why this is windowed
        # rather than whole-file.
        assert _INBOUND_TERMS.search(_FOLLOWUP_DIRECTION_WINDOW)
        assert _OUTBOUND_TERMS.search(_FOLLOWUP_DIRECTION_WINDOW)


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
