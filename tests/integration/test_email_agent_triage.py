# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Integration test for end-to-end email triage against a live Lemonade.

Skipped automatically when Lemonade is not running (via ``require_lemonade``).
The test loads the synthetic stub mbox into a ``FakeGmailBackend`` and
runs the agent's triage tool end-to-end, comparing per-message
classifications to ``ground_truth.json``.

AC4 verification (single source of truth — F2 was descoped, the eval
runner extension was deferred). The accuracy gate is baseline-relative:
the test asserts ``current >= baseline - tolerance`` so a regression on
the LLM/model is detected without committing to a fragile absolute
threshold.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytestmark = pytest.mark.integration

from gaia.agents.email.agent import EmailTriageAgent  # noqa: E402
from gaia.agents.email.config import EmailAgentConfig  # noqa: E402
from gaia.agents.email.tools.llm_triage import make_llm_classifier  # noqa: E402
from gaia.agents.email.tools.read_tools import triage_inbox_impl  # noqa: E402
from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

# The committed baseline (baseline_accuracy.json) was recorded with this model;
# the accuracy gate is only apples-to-apples when the LLM-assist classifier
# uses the same one.
BASELINE_MODEL = "Qwen3.5-35B-A3B-GGUF"

FIXTURES_DIR = _REPO_ROOT / "tests" / "fixtures" / "email"
STUB_INBOX = FIXTURES_DIR / "_stub_inbox.mbox"
GROUND_TRUTH = FIXTURES_DIR / "ground_truth.json"
BASELINE = FIXTURES_DIR / "baseline_accuracy.json"


def test_triage_meets_baseline_minus_tolerance(require_lemonade, tmp_path):
    """End-to-end: triage every stub-inbox message via the heuristic fast
    path **plus LLM follow-up** (#1107), and hard-gate category accuracy at
    baseline − tolerance.

    Heuristic-uncertain messages (and always urgent-vs-actionable, which the
    heuristic refuses to commit) are re-classified by the LLM via the same
    ``make_llm_classifier`` wiring the production ``triage_inbox`` tool uses.
    The classifier runs on the baseline model so the gate is apples-to-apples
    with ``baseline_accuracy.json``.
    """
    fake_gmail = FakeGmailBackend(STUB_INBOX)
    ground_truth = json.loads(GROUND_TRUTH.read_text())
    baseline = json.loads(BASELINE.read_text())

    # Build the production LLM-assist classifier from a real agent's chat.
    agent = EmailTriageAgent(
        config=EmailAgentConfig(
            model_id=BASELINE_MODEL,
            gmail_backend=fake_gmail,
            db_path=str(tmp_path / "state.db"),
            silent_mode=True,
        )
    )
    classifier = make_llm_classifier(agent.chat)

    triage = triage_inbox_impl(fake_gmail, max_messages=100, classifier=classifier)
    results_by_id = {r["id"]: r for r in triage["results"]}

    correct_category = 0
    total_category = 0
    correct_spam = 0
    correct_phishing = 0
    total_flag = 0
    misses = []
    for msg_id, gt in ground_truth.items():
        if msg_id.startswith("_"):  # skip _meta / _comment
            continue
        result = results_by_id.get(msg_id)
        if result is None:
            continue
        total_category += 1
        total_flag += 1
        # With LLM follow-up every message is a confident decision.
        if result["category"] == gt["category"]:
            correct_category += 1
        else:
            misses.append(
                f"{msg_id}: got={result['category']} "
                f"(src={result.get('source')}), gt={gt['category']}"
            )
        if result["is_spam"] == gt["is_spam"]:
            correct_spam += 1
        if result["is_phishing"] == gt["is_phishing"]:
            correct_phishing += 1

    accuracy = correct_category / total_category if total_category else 0.0
    baseline_accuracy = baseline.get("category_accuracy", 0.5)
    tolerance = baseline.get("tolerance_pp", 5) / 100.0
    floor = baseline_accuracy - tolerance
    print(
        f"Triage accuracy (heuristic + LLM follow-up, {BASELINE_MODEL}):\n"
        f"  category: {correct_category}/{total_category} = {accuracy:.2f} "
        f"(baseline {baseline_accuracy:.2f}, floor {floor:.2f})\n"
        f"  spam:     {correct_spam}/{total_flag}\n"
        f"  phishing: {correct_phishing}/{total_flag}\n"
    )
    if misses:
        print("Misses:\n  " + "\n  ".join(misses))

    # Spam is label-driven and must stay perfect.
    assert correct_spam == total_flag, "spam classification regressed"

    # Hard gate (#1107): LLM follow-up must lift category accuracy to the
    # baseline-relative floor.
    assert accuracy >= floor, (
        f"category accuracy {accuracy:.2f} below floor {floor:.2f} "
        f"(baseline {baseline_accuracy:.2f} − {tolerance:.2f})"
    )
