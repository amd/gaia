# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A proactive audit that cannot reach Claude stops once, and says why, in plain words.

The October failures were the weekly usage limit, but the runs reported "Upload
findings failed" and "the audit did not run". The log shapes below are the real
claude-code-action execution_file shape from run 39025883874.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "audit" / "require_claude.py"


@pytest.fixture(scope="module")
def gate():
    spec = importlib.util.spec_from_file_location("require_claude", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def log(tmp_path: Path, result: str, is_error: bool, reply: str = "") -> str:
    events = [{"type": "system", "subtype": "init", "model": "claude-opus-5-5"}]
    if reply:
        events.append(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": reply}]},
            }
        )
    events.append(
        {"type": "result", "subtype": "success", "is_error": is_error, "result": result}
    )
    path = tmp_path / "claude-execution-output.json"
    path.write_text(json.dumps(events, indent=2), encoding="utf-8")
    return str(path)


WEEKLY = "You've hit your weekly limit · resets Oct 21, 3pm (UTC)"


def test_an_answering_account_lets_the_run_go(gate, tmp_path):
    result = gate.gate(
        "success", log(tmp_path, "ok", False, reply="ok"), "primary", "Tonight's audit"
    )
    assert result == (0, "ok", "", "")


def test_the_weekly_limit_stops_the_run_and_names_account_and_reset(gate, tmp_path):
    code, verdict, error, summary = gate.gate(
        "failure", log(tmp_path, WEEKLY, True), "secondary", "Tonight's nightly audit"
    )
    assert (code, verdict) == (1, "quota")
    assert "secondary Claude account is out of quota" in summary
    assert "resets Oct 21, 3pm (UTC)." in summary
    assert "next scheduled run covers everything since" in summary
    assert "claude-account-switch.yml" in error
    # Truthful about HOW the switch runs: this run dispatches it.
    assert "request-account-switch" in error
    assert "triggers" not in error


def test_a_refusal_reported_as_success_is_still_caught(gate, tmp_path):
    """The action can report success while the body is the refusal."""
    code, verdict, _, summary = gate.gate(
        "success", log(tmp_path, WEEKLY, False, reply=WEEKLY), "primary", "Audit"
    )
    assert (code, verdict) == (1, "quota")
    assert "out of quota" in summary


def test_a_dead_token_is_named_as_a_token_problem_not_quota(gate, tmp_path):
    code, verdict, error, summary = gate.gate(
        "failure",
        log(tmp_path, "Invalid API key · Please run /login", True),
        "primary",
        "Audit",
    )
    assert (code, verdict) == (1, "credential")
    assert "token was rejected" in summary
    assert "out of quota" not in summary
    assert "claude setup-token" in error


def test_no_execution_log_means_the_run_does_not_start(gate, tmp_path):
    code, verdict, error, _ = gate.gate(
        "failure", str(tmp_path / "missing.json"), "primary", "Audit"
    )
    assert (code, verdict) == (1, "no-log")
    assert "wrote no execution log" in error


def test_a_success_without_the_expected_reply_is_not_a_go(gate, tmp_path):
    code, verdict, _, summary = gate.gate(
        "success",
        log(tmp_path, "", False, reply="I cannot help with that"),
        "primary",
        "Audit",
    )
    assert (code, verdict) == (1, "unknown")
    assert "did not answer the probe" in summary


def test_cli_appends_the_summary_and_prints_one_error(gate, tmp_path, capsys):
    summary = tmp_path / "summary.md"
    summary.write_text("earlier\n", encoding="utf-8")
    code = gate.main(
        [
            "--conclusion",
            "failure",
            "--log",
            log(tmp_path, WEEKLY, True),
            "--account",
            "primary",
            "--what",
            "Tonight's security audit",
            "--summary",
            str(summary),
        ]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert out.count("::error") == 1
    assert out.startswith(
        "::error title=Claude unavailable::Tonight's security audit skipped"
    )
    assert summary.read_text(encoding="utf-8").startswith("earlier\n## ⛔")


@pytest.mark.parametrize(
    "result, is_error, reply, verdict",
    [
        ("ok", False, "ok", "ok"),
        (WEEKLY, True, "", "quota"),
        ("Invalid API key · Please run /login", True, "", "credential"),
    ],
)
def test_cli_writes_the_verdict_the_switch_job_reads(
    gate, tmp_path, result, is_error, reply, verdict
):
    """Only `quota` makes the workflow dispatch claude-account-switch.yml."""
    output = tmp_path / "github_output"
    gate.main(
        [
            "--conclusion",
            "failure" if is_error else "success",
            "--log",
            log(tmp_path, result, is_error, reply=reply),
            "--account",
            "primary",
            "--what",
            "Audit",
            "--summary",
            str(tmp_path / "s.md"),
            "--github-output",
            str(output),
        ]
    )
    assert output.read_text(encoding="utf-8") == f"verdict={verdict}\n"
