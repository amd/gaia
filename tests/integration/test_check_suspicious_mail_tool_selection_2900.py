# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Live tool-selection regression harness for #2900 / PR #2910.

The live phrasing sweep in PR #2910 found ``check_suspicious_mail`` (added
by that PR) was never selected by the model, even for the most direct
phrasing — ``pre_scan_inbox`` was called instead on 4 of 5 unrehearsed
phrasings, and the 5th asked for clarification. That is an AC#1 failure:
the scoped tool exists but is never reached.

This test reproduces that sweep locally against a hermetic
``FakeGmailBackend`` (no live mailbox) so the tool-selection behavior has a
committed, re-runnable regression gate instead of living only in a PR
comment. Each phrasing runs against a FRESH ``EmailTriageAgent`` instance —
same methodology the live sweep used (a fresh session per phrasing; a
mid-sweep contamination from session reuse was caught and redone there) —
and the ACTUAL tool call is captured from ``process_query``'s returned
``conversation``, not inferred from the rendered answer text.

Per #2762 (Email Agent Validation Index) the only compliant lever for this
class of bug is the tools' OWN descriptions (docstrings) and general
system-prompt guidance — never a phrase-matcher keyed on the user's
question. This test verifies the OUTCOME of that lever (does the model
actually pick the right tool now), not the wording change itself.

Skipped automatically when Lemonade is not running (``require_lemonade``).
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytestmark = pytest.mark.integration

pytest.importorskip("gaia_agent_email")
from gaia_agent_email.agent import EmailTriageAgent  # noqa: E402
from gaia_agent_email.config import EmailAgentConfig  # noqa: E402

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

FIXTURES_DIR = _REPO_ROOT / "tests" / "fixtures" / "email"
# The committed 249-message vendor-derived corpus (#1230) already used by
# test_email_agent_triage.py — it carries real phishing/spam-flagged
# messages, satisfying #2900's "fixture inbox with a suspicious message plus
# normal mail" verification recipe without hand-building fixtures.
CORPUS_INBOX = FIXTURES_DIR / "synthetic_inbox.mbox"

# The NPU shipping default (gaia_agent_email/model_select.py,
# resolve_default_email_model auto-selects this whenever an NPU is present
# and the model is servable) — the harder case, and the exact model the
# PR #2910 live sweep measured 0/5 against. Override for a portable smoke
# run on hardware that can't serve the FLM recipe, e.g.:
#   GAIA_EMAIL_HARNESS_MODEL=Gemma-4-E4B-it-GGUF pytest ...
# A run on the override model is NOT equivalent evidence for the NPU
# default — it only proves the wording didn't regress tool selection on
# whatever model actually ran.
DEFAULT_HARNESS_MODEL = "gemma4-it-e2b-FLM"
HARNESS_MODEL = os.environ.get("GAIA_EMAIL_HARNESS_MODEL", DEFAULT_HARNESS_MODEL)

# Verbatim phrasings from the PR #2910 live sweep (5 unrehearsed, written
# without naming any tool — per #2762's sweep bar).
SCOPED_PHRASINGS = [
    "is there anything suspicious in my inbox?",
    "anything sketchy come through today?",
    "did I get any phishing attempts?",
    "should I be worried about any of these emails?",
    "flag anything that looks off.",
]
# AC#2 control from #2900: a genuinely general ask must still reach the
# general tool — the narrowing must never suppress the intended full-triage
# flow. The system prompt's "MUST be used instead" imperative (added to fix
# AC#1 tool selection) is exactly the kind of change that could over-steer a
# general ask toward the narrow tool, so this covers several unrehearsed
# general phrasings, not just one.
CONTROL_PHRASINGS = [
    "triage my inbox",
    "what's in my inbox",
    "catch me up",
    "what needs me today",
]


def _tool_names_called(outcome: Dict[str, Any]) -> List[str]:
    """Tool names actually invoked this turn, from the conversation trace —
    never inferred from the rendered answer text (the live sweep's own
    caveat: several pre_scan_inbox turns rendered short prose, not the
    four-bucket card, yet the tool was still the wrong one)."""
    return [
        entry["name"]
        for entry in outcome.get("conversation", [])
        if entry.get("role") == "tool" and entry.get("name")
    ]


def _fresh_agent(tmp_path: Path, model_id: str = HARNESS_MODEL) -> EmailTriageAgent:
    fake_gmail = FakeGmailBackend(CORPUS_INBOX)
    return EmailTriageAgent(
        config=EmailAgentConfig(
            model_id=model_id,
            gmail_backend=fake_gmail,
            db_path=str(tmp_path / f"state-{uuid.uuid4().hex}.db"),
            silent_mode=True,
        )
    )


def test_check_suspicious_mail_selected_for_scoped_phrasings(
    require_lemonade, tmp_path
):
    """Re-runs the #2910 live sweep's 5 phrasings, one fresh agent per
    phrasing, and tallies tool selection exactly as the live sweep reported:
    N = alone, K = also called pre_scan_inbox, never = neither.

    Pre-fix measured result (PR #2910, gemma4-it-e2b-FLM): N=0/5, K=4/5,
    1/5 asked for clarification (no tool). This is a regression gate against
    reverting to that state, not a per-phrasing hard requirement — tool
    selection is model behavior, so a single flaky phrasing failing while
    the rest succeed is a real (if narrower) improvement, not a full
    regression back to the pre-fix state this test exists to catch.
    """
    results: Dict[str, List[str]] = {}
    for phrasing in SCOPED_PHRASINGS:
        agent = _fresh_agent(tmp_path)
        outcome = agent.process_query(phrasing)
        results[phrasing] = _tool_names_called(outcome)

    alone = sum(1 for tools in results.values() if tools == ["check_suspicious_mail"])
    also_pre_scan = sum(
        1
        for tools in results.values()
        if "check_suspicious_mail" in tools and "pre_scan_inbox" in tools
    )
    never_narrow = sum(
        1 for tools in results.values() if "check_suspicious_mail" not in tools
    )

    print(f"\ncheck_suspicious_mail tool-selection sweep ({HARNESS_MODEL}):")
    for phrasing, tools in results.items():
        print(f"  {phrasing!r}: {tools}")
    print(
        f"  N (narrow tool alone) = {alone}/{len(SCOPED_PHRASINGS)}, "
        f"K (also called pre_scan_inbox) = {also_pre_scan}/{len(SCOPED_PHRASINGS)}, "
        f"never selected = {never_narrow}/{len(SCOPED_PHRASINGS)}"
    )

    assert never_narrow < len(SCOPED_PHRASINGS), (
        "check_suspicious_mail was never selected for any of the 5 live-sweep "
        f"phrasings on {HARNESS_MODEL} — this reproduces the exact #2900/#2910 "
        f"AC#1 failure. Per-phrasing results: {results}"
    )


@pytest.mark.parametrize("phrasing", CONTROL_PHRASINGS)
def test_general_triage_control_unaffected(require_lemonade, tmp_path, phrasing):
    """AC#2 control: a genuinely general request must still reach
    pre_scan_inbox — narrowing check_suspicious_mail's selection must not
    suppress the general triage path."""
    agent = _fresh_agent(tmp_path)
    outcome = agent.process_query(phrasing)
    tools = _tool_names_called(outcome)
    print(f"\ncontrol {phrasing!r} ({HARNESS_MODEL}): {tools}")
    assert "pre_scan_inbox" in tools, (
        f"general triage request must still reach pre_scan_inbox, got {tools} "
        f"on {HARNESS_MODEL}"
    )
