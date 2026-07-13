# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the daily-briefing quality eval CI gate
(packaging/eval_briefing_report.py).

Locks `main()`'s `should_fail` -> exit-code contract, the fail-loud
`ANTHROPIC_API_KEY`-absent path, and — unique to briefing — the enforcing
gate's asymmetric skip: a total generation/judge outage sets
`should_fail = thresholds.enforce`, so under the committed `enforce: true`
manifest a skip BLOCKS the build (returns 1), unlike the report-mode
siblings. These tests mock every `gaia.eval` entry point on the loaded
module; calling the real `generate_briefings` in-process requires repo-root
`PYTHONPATH` (see #2024) which this workflow does not set.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# eval_briefing_report.py is a packaging script, not part of the
# gaia_agent_email package — load it by path (do not insert into sys.modules).
_PATH = Path(__file__).resolve().parents[1] / "packaging" / "eval_briefing_report.py"
_spec = importlib.util.spec_from_file_location("eval_briefing_report_under_test", _PATH)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


@pytest.fixture(autouse=True)
def _sentinel_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")


def _set_model_env(monkeypatch, model="test-model"):
    monkeypatch.setenv("EMAIL_EVAL_MODEL", model)


def _gate(should_fail, **overrides):
    # shape from src/gaia/eval/briefing_quality.py:583-591 (evaluate_briefing_gate,
    # same passed/breaches/enforce/should_fail contract as the sibling gates)
    base = {
        "briefing_approval_rate": 0.9,
        "passed": not should_fail,
        "breaches": [],
        "enforce": True,
        "should_fail": should_fail,
    }
    base.update(overrides)
    return base


def _summary(gate):
    # shape from src/gaia/eval/briefing_quality.py:380-446 (summarize_briefings)
    return {
        "briefing_gate": gate,
        "briefing": {
            "briefing_approval_rate": 0.9,
            "must_include_recall_mean": 0.95,
            "faithful_rate": 1.0,
            "hallucination_free_rate": 1.0,
            "cases_judged": 2,
            "cases_errored": 0,
        },
    }


def _install_fakes(monkeypatch, summary):
    fake_generate = MagicMock(return_value=[{"briefing": "Your morning briefing"}])
    fake_judge_briefings = MagicMock(
        return_value=[{"briefing_judgement": {"approved": True}}]
    )
    fake_make_judge = MagicMock(return_value=MagicMock())
    fake_load_corpus = MagicMock(return_value={"case1": {"inbox": []}})
    fake_summarize = MagicMock(return_value=summary)
    monkeypatch.setattr(mod, "generate_briefings", fake_generate)
    monkeypatch.setattr(mod, "judge_briefings", fake_judge_briefings)
    monkeypatch.setattr(mod, "make_claude_judge", fake_make_judge)
    monkeypatch.setattr(mod, "load_briefing_corpus", fake_load_corpus)
    monkeypatch.setattr(mod, "summarize_briefings", fake_summarize)
    return (
        fake_generate,
        fake_judge_briefings,
        fake_make_judge,
        fake_load_corpus,
        fake_summarize,
    )


def _report_path():
    return Path("eval-out") / "briefing_gate_report.json"


def test_api_key_absent_returns_1_no_judge_no_report(monkeypatch):
    _set_model_env(monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    summary = _summary(_gate(False))
    (
        fake_generate,
        fake_judge_briefings,
        fake_make_judge,
        fake_load_corpus,
        fake_summarize,
    ) = _install_fakes(monkeypatch, summary)

    assert mod.main() == 1

    assert not fake_generate.called
    assert not fake_judge_briefings.called
    assert not fake_make_judge.called
    assert not fake_load_corpus.called
    assert not fake_summarize.called
    # eval-out/ itself IS created before the key check — characterize, don't
    # assert its absence; only the report file must be missing.
    assert not _report_path().exists()


def test_gate_breach_returns_1(monkeypatch):
    _set_model_env(monkeypatch)
    summary = _summary(_gate(True))
    _install_fakes(monkeypatch, summary)

    assert mod.main() == 1


@pytest.mark.parametrize(
    "gate",
    [_gate(False), {"passed": True, "breaches": []}],
    ids=["should_fail_false", "should_fail_key_missing"],
)
def test_falsy_gate_returns_0_and_writes_report(monkeypatch, gate):
    _set_model_env(monkeypatch)
    summary = _summary(gate)
    (
        fake_generate,
        fake_judge_briefings,
        fake_make_judge,
        fake_load_corpus,
        fake_summarize,
    ) = _install_fakes(monkeypatch, summary)

    assert mod.main() == 0

    report = json.loads(_report_path().read_text(encoding="utf-8"))
    assert set(["model", "corpus", "summary"]).issubset(report.keys())
    assert fake_generate.called
    assert fake_judge_briefings.called
    assert fake_make_judge.called
    assert fake_load_corpus.called
    assert fake_summarize.called


def test_skipped_gate_variant_blocks_the_build(monkeypatch):
    _set_model_env(monkeypatch)
    # shape from src/gaia/eval/briefing_quality.py:434-442 — briefing is the
    # ENFORCING gate: its skip mirrors enforce into should_fail, so a total
    # generation/judge outage under the committed enforce:true manifest must
    # FAIL the build (asymmetric vs the report-mode siblings' hardcoded False).
    gate = {
        "skipped": True,
        "reason": (
            "no case carried a judge verdict; the briefing-quality gate "
            "could not be evaluated"
        ),
        "enforce": True,
        "should_fail": True,
    }
    summary = _summary(gate)
    _install_fakes(monkeypatch, summary)

    assert mod.main() == 1
