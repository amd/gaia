# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the follow-up detection eval CI gate
(packaging/eval_followup_report.py).

Locks `main()`'s `should_fail` -> exit-code contract (report mode never fails
the build), the threshold-loading path (bars come from the committed manifest,
never inlined in the script), and the absence-of-a-judge-credential contract
unique to this suite: the follow-up detector is deterministic, so NO judge
credential is required and the eval must still run and score.

These tests mock every `gaia.eval` entry point on the loaded module, so they
run without the email agent stack and without a repo-root `PYTHONPATH` — the
same isolation as `test_eval_action_item_report.py`.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# eval_followup_report.py is a packaging script, not part of the
# gaia_agent_email package — load it by path (do not insert into sys.modules).
_PATH = Path(__file__).resolve().parents[1] / "packaging" / "eval_followup_report.py"
_spec = importlib.util.spec_from_file_location("eval_followup_report_under_test", _PATH)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


@pytest.fixture(autouse=True)
def _no_judge_credential(monkeypatch):
    """No judge credential AND no `claude` CLI — this eval must not need either.

    The drafting / action-item suites fail loudly here; the follow-up eval is
    deterministic and must proceed regardless. Deleting both credentials (and
    hiding the CLI) is what makes that difference testable.
    """
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("gaia.eval.judge_client.shutil.which", lambda _name: None)
    monkeypatch.delenv("EMAIL_EVAL_MODEL", raising=False)


def _gate(should_fail, **overrides):
    # shape from src/gaia/eval/followup_quality.py (evaluate_followup_gate)
    base = {
        "f1": 0.9,
        "recall": 0.9,
        "precision": 0.9,
        "passed": not should_fail,
        "breaches": [],
        "enforce": True,
        "should_fail": should_fail,
    }
    base.update(overrides)
    return base


def _summary(gate):
    # shape from src/gaia/eval/followup_quality.py (summarize_followups)
    return {
        "followup_gate": gate,
        "followups": {
            "precision": 0.9,
            "recall": 0.9,
            "f1": 0.9,
            "tp": 1,
            "fp": 0,
            "fn": 0,
            "tn": 1,
            "hard_negatives_correct": 1,
            "hard_negatives_total": 1,
            "hard_negative_correct_rate": 1.0,
            "cases_scored": 2,
            "cases_errored": 0,
        },
    }


def _install_fakes(monkeypatch, summary):
    fake_generate = MagicMock(
        return_value=[
            {"case_id": "fu-001", "predicted": True, "error": "", "duration_ms": 1}
        ]
    )
    fake_load_corpus = MagicMock(return_value={"fu-001": {"scenario": "..."}})
    fake_score = MagicMock(
        return_value=[{"followup_match": {"tp": 1, "fp": 0, "fn": 0, "tn": 0}}]
    )
    fake_summarize = MagicMock(return_value=summary)
    fake_thresholds = MagicMock(
        return_value=MagicMock(
            f1_min=0.7, recall_min=0.8, precision_min=0.6, enforce=False
        )
    )
    monkeypatch.setattr(mod, "generate_detections", fake_generate)
    monkeypatch.setattr(mod, "load_followup_corpus", fake_load_corpus)
    monkeypatch.setattr(mod, "score_generations", fake_score)
    monkeypatch.setattr(mod, "summarize_followups", fake_summarize)
    monkeypatch.setattr(mod, "load_default_followup_thresholds", fake_thresholds)
    return fake_generate, fake_load_corpus, fake_score, fake_summarize, fake_thresholds


def _report_path(out_dir="eval-out"):
    return Path(out_dir) / "followups_report.json"


def test_runs_and_scores_with_no_judge_credential(monkeypatch):
    """The deterministic detector needs no judge — the eval must still run."""
    summary = _summary(_gate(False))
    fakes = _install_fakes(monkeypatch, summary)

    assert mod.main([]) == 0

    for fake in fakes:
        assert fake.called


def test_thresholds_come_from_the_committed_manifest(monkeypatch):
    """Bars are loaded from the manifest, never inlined in the script."""
    _install_fakes(monkeypatch, _summary(_gate(False)))
    fake_thresholds = mod.load_default_followup_thresholds

    assert mod.main([]) == 0

    fake_thresholds.assert_called_once_with()


def test_report_records_the_loaded_bars_and_the_gate_verdict(monkeypatch):
    summary = _summary(_gate(False))
    _install_fakes(monkeypatch, summary)

    assert mod.main([]) == 0

    report = json.loads(_report_path().read_text(encoding="utf-8"))
    assert set(["model", "corpus", "detector", "summary"]).issubset(report.keys())
    # Round-tripped through JSON, so compare by value (the fixture carries no
    # non-JSON types — a difference here would mean the script mutated it).
    assert report["summary"] == summary
    assert report["detector"] == "deterministic-latest-outbound"
    # Detection is deterministic, so the model is a label, not a Lemonade id.
    assert report["model"] == mod.DEFAULT_MODEL_ID


def test_model_label_comes_from_the_env_when_set(monkeypatch):
    monkeypatch.setenv("EMAIL_EVAL_MODEL", "someone/else")
    _install_fakes(monkeypatch, _summary(_gate(False)))

    assert mod.main([]) == 0

    report = json.loads(_report_path().read_text(encoding="utf-8"))
    assert report["model"] == "someone/else"


def test_enforce_false_breach_returns_0_and_still_writes_the_report(monkeypatch):
    """Report mode: a breached bar is REPORTED, never a build failure.

    `should_fail` is already False in report mode (evaluate_followup_gate
    computes it as enforce AND not passed), so the script's exit code stays 0
    and the numbers still land in the uploaded artifact.
    """
    gate = _gate(False, passed=False, enforce=False)
    gate["breaches"] = [{"metric": "f1", "value": 0.58, "min": 0.7}]
    _install_fakes(monkeypatch, _summary(gate))

    assert mod.main([]) == 0
    assert _report_path().exists()


def test_enforced_gate_breach_returns_1(monkeypatch):
    """With enforce:true, a breach must fail the build like every sibling gate."""
    _install_fakes(monkeypatch, _summary(_gate(True)))

    assert mod.main([]) == 1


@pytest.mark.parametrize(
    "gate",
    [_gate(False), {"passed": True, "breaches": []}],
    ids=["should_fail_false", "should_fail_key_missing"],
)
def test_falsy_gate_returns_0(monkeypatch, gate):
    _install_fakes(monkeypatch, _summary(gate))

    assert mod.main([]) == 0


def test_skipped_gate_variant_returns_1(monkeypatch):
    # summarize_followups' loud skip: no case carried a verdict. Nothing was
    # scored, so the eval proved nothing — report mode must not hide that.
    gate = {
        "skipped": True,
        "reason": (
            "no case carried a follow-up verdict; the follow-up gate "
            "cannot be evaluated"
        ),
        "enforce": False,
        "should_fail": False,
    }
    _install_fakes(monkeypatch, _summary(gate))

    assert mod.main([]) == 1
    assert not _report_path().exists()


def test_zero_verdicts_return_1_even_in_report_mode(monkeypatch):
    # Every case errored: `predicted` is None for all of them. The gate would
    # report itself "skipped" and exit 0, which reads as a green CI step for a
    # run that scored nothing.
    fake_generate = MagicMock(
        return_value=[
            {"case_id": "fu-001", "predicted": None, "error": "import failed"},
            {"case_id": "fu-002", "predicted": None, "error": "import failed"},
        ]
    )
    gate = {
        "skipped": True,
        "reason": "no case carried a follow-up verdict",
        "enforce": False,
        "should_fail": False,
    }
    fakes = _install_fakes(monkeypatch, _summary(gate))
    monkeypatch.setattr(mod, "generate_detections", fake_generate)

    assert mod.main([]) == 1

    # Fails at generation, before scoring — there is nothing to score.
    assert fakes[2].called is False
    assert not _report_path().exists()


def test_out_dir_is_overridable(monkeypatch):
    _install_fakes(monkeypatch, _summary(_gate(False)))

    assert mod.main(["--out-dir", "custom-out"]) == 0

    report = json.loads(_report_path("custom-out").read_text(encoding="utf-8"))
    assert report["corpus"] == mod.CORPUS_PATH
