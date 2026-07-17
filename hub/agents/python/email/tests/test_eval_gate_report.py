# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the email-eval CI gate reader (packaging/eval_gate_report.py).

Locks `main()`'s `should_fail` -> exit-code contract: a breach on either gate
returns 1 (report written first), a clean/skip/missing-key gate returns 0, and
the CWD-relative `run_benchmark`/`summarize_benchmark`/`load_ground_truth`
calls are always exercised on the reachable path. These tests mock every
`gaia.eval` entry point on the loaded module; calling the real `run_benchmark`
in-process requires repo-root `PYTHONPATH` (see #2024) which this workflow
does not set.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# eval_gate_report.py is a packaging script, not part of the gaia_agent_email
# package — load it by path (do not insert into sys.modules; the script
# defines no dataclasses needing forward-ref resolution).
_PATH = Path(__file__).resolve().parents[1] / "packaging" / "eval_gate_report.py"
_spec = importlib.util.spec_from_file_location("eval_gate_report_under_test", _PATH)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def _set_model_env(monkeypatch, model="test-model"):
    monkeypatch.setenv("EMAIL_EVAL_MODEL", model)


def _gate(should_fail, **overrides):
    # shape from src/gaia/eval/quality_metrics.py:645-656 (evaluate_gate)
    base = {
        "passed": not should_fail,
        "breaches": [],
        "enforce": True,
        "should_fail": should_fail,
    }
    base.update(overrides)
    return base


def _summary(quality_gate, perf_gate):
    # shape from src/gaia/eval/benchmark.py:453-510 (summarize_benchmark)
    return {
        "quality_gate": quality_gate,
        "perf_gate": perf_gate,
        "quality": {"category_accuracy": 0.9, "phishing": {"precision": 0.95}},
        "scorecard": {"performance": {"avg_tokens_per_second": 12.3}},
    }


def _install_fakes(monkeypatch, summary):
    fake_load_gt = MagicMock(return_value={"ground": "truth"})
    fake_run_benchmark = MagicMock(return_value=[{"sentinel": "result"}])
    fake_summarize = MagicMock(return_value=summary)
    monkeypatch.setattr(mod, "load_ground_truth", fake_load_gt)
    monkeypatch.setattr(mod, "run_benchmark", fake_run_benchmark)
    monkeypatch.setattr(mod, "summarize_benchmark", fake_summarize)
    return fake_load_gt, fake_run_benchmark, fake_summarize


def _report():
    return json.loads(
        (Path("eval-out") / "gate_report.json").read_text(encoding="utf-8")
    )


def test_both_gates_pass_returns_0_and_writes_report(monkeypatch):
    _set_model_env(monkeypatch)
    summary = _summary(_gate(False), _gate(False))
    fake_gt, fake_run, fake_summarize = _install_fakes(monkeypatch, summary)

    assert mod.main() == 0

    report = _report()
    assert set(["model", "quality_gate", "perf_gate", "reported"]).issubset(
        report.keys()
    )
    assert fake_gt.called
    assert fake_run.called
    assert fake_summarize.called


def test_quality_gate_breach_returns_1_report_still_written(monkeypatch):
    _set_model_env(monkeypatch)
    summary = _summary(_gate(True), _gate(False))
    _install_fakes(monkeypatch, summary)

    assert mod.main() == 1
    # Report is written before the failing-gate check — assert it landed.
    assert (Path("eval-out") / "gate_report.json").exists()


def test_perf_gate_breach_returns_1(monkeypatch):
    _set_model_env(monkeypatch)
    summary = _summary(_gate(False), _gate(True))
    _install_fakes(monkeypatch, summary)

    assert mod.main() == 1
    assert (Path("eval-out") / "gate_report.json").exists()


def test_both_gates_breach_returns_1(monkeypatch):
    _set_model_env(monkeypatch)
    summary = _summary(_gate(True), _gate(True))
    _install_fakes(monkeypatch, summary)

    assert mod.main() == 1


def test_skipped_gate_variant_never_blocks(monkeypatch):
    _set_model_env(monkeypatch)
    # shape from src/gaia/eval/benchmark.py:472-485 and :496-504 (loud skip,
    # should_fail always False for the report-mode quality/perf gates).
    quality_gate = {
        "skipped": True,
        "reason": "no quality block in any run (ground truth not provided)",
        "axis": "category",
        "enforce": False,
        "should_fail": False,
    }
    perf_gate = {
        "skipped": True,
        "reason": "no performance_summary in any run",
        "enforce": False,
        "should_fail": False,
    }
    summary = _summary(quality_gate, perf_gate)
    _install_fakes(monkeypatch, summary)

    assert mod.main() == 0


def test_missing_should_fail_key_defaults_falsy_returns_0(monkeypatch):
    _set_model_env(monkeypatch)
    quality_gate = {"passed": True, "breaches": []}
    perf_gate = {"passed": True, "breaches": []}
    summary = _summary(quality_gate, perf_gate)
    _install_fakes(monkeypatch, summary)

    assert mod.main() == 0


def test_default_limit_and_experiments_passed_to_run_benchmark(monkeypatch):
    _set_model_env(monkeypatch)
    monkeypatch.delenv("EMAIL_EVAL_LIMIT", raising=False)
    monkeypatch.delenv("EMAIL_EVAL_EXPERIMENTS", raising=False)
    summary = _summary(_gate(False), _gate(False))
    _, fake_run, _ = _install_fakes(monkeypatch, summary)

    assert mod.main() == 0

    assert fake_run.call_args.kwargs["limit"] == 50
    assert fake_run.call_args.kwargs["experiments"] == 1
