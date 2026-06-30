# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Integration test for end-to-end email triage against a live Lemonade.

Skipped automatically when Lemonade is not running (via ``require_lemonade``).
The test loads the 249-message vendor-derived corpus (#1230) into a
``FakeGmailBackend`` and runs the production heuristic + LLM-assist triage
path (#1107) over it, comparing per-message classifications to
``ground_truth.json``.

The accuracy gate is baseline-relative: the test asserts
``current >= baseline - tolerance`` against ``baseline_accuracy.json`` (a real
Gemma-4-E4B-it-GGUF measurement recorded via the SAME LLM-assist path), so a
regression on the LLM/model is detected without a fragile absolute threshold.
This supersedes the earlier stub-inbox + Qwen3.5-35B baseline.
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

# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102);
# skip when a framework-only env lacks it.
import pytest  # noqa: E402

pytest.importorskip("gaia_agent_email")  # noqa: E402
from gaia_agent_email.agent import EmailTriageAgent  # noqa: E402
from gaia_agent_email.config import EmailAgentConfig  # noqa: E402
from gaia_agent_email.tools.llm_triage import make_llm_classifier  # noqa: E402
from gaia_agent_email.tools.read_tools import triage_inbox_impl  # noqa: E402

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

# The committed baseline (baseline_accuracy.json) was recorded with this model
# via the same LLM-assist path; the accuracy gate is only apples-to-apples when
# the classifier uses the same one. This is the demo model (matches the repo's
# gemma-4-e4b-* baselines).
BASELINE_MODEL = "Gemma-4-E4B-it-GGUF"

FIXTURES_DIR = _REPO_ROOT / "tests" / "fixtures" / "email"
# The committed 249-message vendor-derived corpus (#1230). Its ground_truth.json is
# keyed by the Gmail-derived id, so it aligns 1:1 with FakeGmailBackend.
CORPUS_INBOX = FIXTURES_DIR / "synthetic_inbox.mbox"
GROUND_TRUTH = FIXTURES_DIR / "ground_truth.json"
BASELINE = FIXTURES_DIR / "baseline_accuracy.json"


def test_triage_meets_baseline_minus_tolerance(require_lemonade, tmp_path):
    """End-to-end: triage every corpus message via the heuristic fast path
    **plus LLM follow-up** (#1107), and hard-gate category accuracy at
    baseline − tolerance.

    Heuristic-uncertain messages (and always urgent-vs-actionable, which the
    heuristic refuses to commit) are re-classified by the LLM via the same
    ``make_llm_classifier`` wiring the production ``triage_inbox`` tool uses.
    The classifier runs on the baseline model so the gate is apples-to-apples
    with ``baseline_accuracy.json``.
    """
    fake_gmail = FakeGmailBackend(CORPUS_INBOX)
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

    triage = triage_inbox_impl(fake_gmail, max_messages=1000, classifier=classifier)
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
    spam_accuracy = correct_spam / total_flag if total_flag else 0.0
    phishing_accuracy = correct_phishing / total_flag if total_flag else 0.0
    baseline_accuracy = baseline.get("category_accuracy", 0.5)
    baseline_spam = baseline.get("is_spam_accuracy")
    baseline_phishing = baseline.get("is_phishing_accuracy")
    tolerance = baseline.get("tolerance_pp", 5) / 100.0
    floor = baseline_accuracy - tolerance
    print(
        f"Triage accuracy (heuristic + LLM follow-up, {BASELINE_MODEL}):\n"
        f"  category: {correct_category}/{total_category} = {accuracy:.4f} "
        f"(baseline {baseline_accuracy:.4f}, floor {floor:.4f})\n"
        f"  spam:     {correct_spam}/{total_flag} = {spam_accuracy:.4f}\n"
        f"  phishing: {correct_phishing}/{total_flag} = {phishing_accuracy:.4f}\n"
    )
    if misses:
        print("Misses:\n  " + "\n  ".join(misses))

    # Hard gate (#1107): LLM follow-up must lift category accuracy to the
    # baseline-relative floor.
    assert accuracy >= floor, (
        f"category accuracy {accuracy:.4f} below floor {floor:.4f} "
        f"(baseline {baseline_accuracy:.4f} − {tolerance:.4f})"
    )

    # Spam / phishing gate: baseline-relative against the MEASURED accuracy on
    # this corpus + path. On the 249-message corpus, spam/phishing are NOT
    # perfect: the corpus carries realistic inbox-spam with no Gmail SPAM label
    # that the keyword heuristic can't catch, and the LLM follow-up only revises
    # the *category* (is_spam/is_phishing are heuristic-set), so it cannot flip
    # those flags. We therefore gate baseline-relative rather than asserting
    # 100% — a hard 100% assert would be a faked pass that hides the real
    # spam-recall ceiling. (See the spam/phishing accuracy recorded in
    # baseline_accuracy.json, measured via this exact path.)
    if baseline_spam is not None:
        spam_floor = baseline_spam - tolerance
        assert spam_accuracy >= spam_floor, (
            f"spam accuracy {spam_accuracy:.4f} below floor {spam_floor:.4f} "
            f"(baseline {baseline_spam:.4f} − {tolerance:.4f})"
        )
    if baseline_phishing is not None:
        phishing_floor = baseline_phishing - tolerance
        assert phishing_accuracy >= phishing_floor, (
            f"phishing accuracy {phishing_accuracy:.4f} below floor "
            f"{phishing_floor:.4f} (baseline {baseline_phishing:.4f} − {tolerance:.4f})"
        )
