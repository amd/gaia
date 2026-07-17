# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the voice-drafting quality eval CI gate
(packaging/eval_drafting_report.py).

Locks `main()`'s `should_fail` -> exit-code contract and the fail-loud
`ANTHROPIC_API_KEY`-absent path (no generation, no judge call, no report
written). These tests mock every `gaia.eval` entry point on the loaded module;
calling the real `generate_drafts` in-process requires repo-root `PYTHONPATH`
(see #2024) which this workflow does not set.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# eval_drafting_report.py is a packaging script, not part of the
# gaia_agent_email package — load it by path (do not insert into sys.modules).
_PATH = Path(__file__).resolve().parents[1] / "packaging" / "eval_drafting_report.py"
_spec = importlib.util.spec_from_file_location("eval_drafting_report_under_test", _PATH)
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
    # shape from src/gaia/eval/draft_quality.py:492-500 (evaluate_drafting_gate)
    base = {
        "draft_approval_rate": 0.9,
        "approval_min": 0.8,
        "passed": not should_fail,
        "breaches": [],
        "enforce": True,
        "should_fail": should_fail,
    }
    base.update(overrides)
    return base


def _summary(gate):
    # shape from src/gaia/eval/draft_quality.py:341-396 (summarize_drafting)
    return {
        "drafting_gate": gate,
        "drafting": {
            "draft_approval_rate": 0.9,
            "voice_match_mean": 0.85,
            "grounded_rate": 1.0,
            "cases_judged": 2,
            "cases_errored": 0,
        },
    }


def _install_fakes(monkeypatch, summary):
    fake_generate = MagicMock(return_value=[{"draft": "Hi — thanks!"}])
    fake_judge_drafts = MagicMock(
        return_value=[{"draft_judgement": {"approved": True}}]
    )
    fake_make_judge = MagicMock(return_value=MagicMock())
    fake_load_corpus = MagicMock(return_value={"case1": {"body": "..."}})
    fake_summarize = MagicMock(return_value=summary)
    monkeypatch.setattr(mod, "generate_drafts", fake_generate)
    monkeypatch.setattr(mod, "judge_drafts", fake_judge_drafts)
    monkeypatch.setattr(mod, "make_claude_judge", fake_make_judge)
    monkeypatch.setattr(mod, "load_drafting_corpus", fake_load_corpus)
    monkeypatch.setattr(mod, "summarize_drafting", fake_summarize)
    return (
        fake_generate,
        fake_judge_drafts,
        fake_make_judge,
        fake_load_corpus,
        fake_summarize,
    )


def _report_path():
    return Path("eval-out") / "drafting_gate_report.json"


def test_api_key_absent_returns_1_no_judge_no_report(monkeypatch):
    _set_model_env(monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    summary = _summary(_gate(False))
    (
        fake_generate,
        fake_judge_drafts,
        fake_make_judge,
        fake_load_corpus,
        fake_summarize,
    ) = _install_fakes(monkeypatch, summary)

    assert mod.main() == 1

    assert not fake_generate.called
    assert not fake_judge_drafts.called
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
        fake_judge_drafts,
        fake_make_judge,
        fake_load_corpus,
        fake_summarize,
    ) = _install_fakes(monkeypatch, summary)

    assert mod.main() == 0

    report = json.loads(_report_path().read_text(encoding="utf-8"))
    assert set(["model", "corpus", "summary"]).issubset(report.keys())
    assert fake_generate.called
    assert fake_judge_drafts.called
    assert fake_make_judge.called
    assert fake_load_corpus.called
    assert fake_summarize.called


def test_skipped_gate_variant_returns_0(monkeypatch):
    _set_model_env(monkeypatch)
    # shape from src/gaia/eval/draft_quality.py:384-392 (loud skip,
    # should_fail hardcoded False — no enforce mirroring for drafting).
    gate = {
        "skipped": True,
        "reason": (
            "no case carried a judge verdict; the draft-approval gate "
            "cannot be evaluated"
        ),
        "enforce": True,
        "should_fail": False,
    }
    summary = _summary(gate)
    _install_fakes(monkeypatch, summary)

    assert mod.main() == 0
