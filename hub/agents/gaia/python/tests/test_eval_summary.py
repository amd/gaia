# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for the gaia eval gate-verdict reporter (packaging/eval_summary.py).

Ported from the email agent's test suite (one reporter shape across agents).
The reporter is the only thing that makes an enforce:false breach visible on a
PR, so the cases that matter are the ones where it could quietly report nothing:
an empty/missing eval dir, a corrupt report, a gate that was never evaluated.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[1] / "packaging" / "eval_summary.py"
_spec = importlib.util.spec_from_file_location("gaia_eval_summary", _MODULE_PATH)
eval_summary = importlib.util.module_from_spec(_spec)
sys.modules["gaia_eval_summary"] = eval_summary
_spec.loader.exec_module(eval_summary)


def _gate(passed=True, enforce=False, **extra):
    return {
        "passed": passed,
        "enforce": enforce,
        "should_fail": enforce and not passed,
        **extra,
    }


def _skipped_gate(enforce=False):
    """The LITERAL skip payload the producers emit.

    There is NO ``passed`` key, and ``should_fail`` is hardcoded False even under
    ``enforce: true`` — a hand-rolled dict with ``passed`` in it would test a
    shape nothing produces.
    """
    return {
        "skipped": True,
        "reason": "no judged scenarios in any run; quality gate cannot be evaluated",
        "enforce": enforce,
        "should_fail": False,
    }


def _all_expected(**overrides):
    """A full set of expected gates, so tests isolate the property under test."""
    gates = {key: _gate() for key in eval_summary.GATE_LABELS}
    gates.update(overrides)
    return gates


def _write(tmp_path: Path, name: str, payload: dict) -> None:
    (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")


def test_expected_gates_are_quality_then_perf():
    """The gaia pipeline currently produces exactly these two, in this order."""
    assert list(eval_summary.GATE_LABELS) == ["quality_gate", "perf_gate"]


def test_finds_gates_at_top_level_and_nested(tmp_path):
    """Matches both real report shapes: top-level gates, and gates under summary."""
    _write(tmp_path, "gate_report.json", {"quality_gate": _gate()})
    _write(tmp_path, "perf_gate_report.json", {"summary": {"perf_gate": _gate()}})

    gates, unreadable = eval_summary.collect(tmp_path)

    assert set(gates) == {"quality_gate", "perf_gate"}
    assert unreadable == []


def test_breach_is_reported_as_a_warning_even_though_it_did_not_fail_the_build(
    tmp_path,
):
    """The whole point: enforce:false exits 0, so the breach must surface here."""
    _write(tmp_path, "gate_report.json", _all_expected(perf_gate=_gate(passed=False)))

    gates, _ = eval_summary.collect(tmp_path)
    markdown, warnings = eval_summary.render(gates, [])

    assert "**BREACH** (advisory)" in markdown
    assert len(warnings) == 1
    assert "Performance" in warnings[0]


def test_enforced_breach_is_labelled_blocking(tmp_path):
    _write(
        tmp_path,
        "gate_report.json",
        _all_expected(perf_gate=_gate(passed=False, enforce=True)),
    )

    gates, _ = eval_summary.collect(tmp_path)
    markdown, warnings = eval_summary.render(gates, [])

    assert "**BREACH** (BLOCKING)" in markdown
    assert len(warnings) == 1


def test_all_pass_produces_no_warnings(tmp_path):
    _write(tmp_path, "gate_report.json", _all_expected())

    gates, _ = eval_summary.collect(tmp_path)
    markdown, warnings = eval_summary.render(gates, [])

    assert warnings == []
    assert markdown.count("| pass |") == len(eval_summary.GATE_LABELS)


def test_skipped_gate_is_found_and_never_rendered_as_a_pass(tmp_path):
    """The skip payload has no `passed` key; dropping it would let a run that
    measured nothing report "all gates passed" — and the producers hardcode
    should_fail:False on a skip, so this reporter is the only catch."""
    _write(tmp_path, "gate_report.json", _all_expected(quality_gate=_skipped_gate()))

    gates, _ = eval_summary.collect(tmp_path)
    markdown, warnings = eval_summary.render(gates, [])

    assert "quality_gate" in gates, "a skipped gate must still be discovered"
    assert "not evaluated" in markdown
    assert "| pass |" in markdown  # the other gate still passes
    assert len(warnings) == 1
    assert "missing evidence, not as a pass" in warnings[0]


def test_skip_is_flagged_even_when_the_manifest_enforces(tmp_path):
    """The producers hardcode should_fail:False on a skip — enforce cannot save us."""
    _write(
        tmp_path,
        "gate_report.json",
        _all_expected(perf_gate=_skipped_gate(enforce=True)),
    )

    gates, _ = eval_summary.collect(tmp_path)
    _, warnings = eval_summary.render(gates, [])

    assert gates["perf_gate"]["should_fail"] is False
    assert len(warnings) == 1


def test_expected_gate_with_no_report_is_flagged_not_silently_omitted(tmp_path):
    """A step that died before writing must not read as "the other passed"."""
    _write(tmp_path, "gate_report.json", {"quality_gate": _gate()})

    gates, _ = eval_summary.collect(tmp_path)
    markdown, warnings = eval_summary.render(gates, [])

    assert markdown.count("no verdict produced") == 1
    assert len(warnings) == 1
    assert all("missing evidence" in w for w in warnings)


def test_known_gates_are_listed_in_pipeline_order(tmp_path):
    _write(
        tmp_path,
        "gate_report.json",
        {"perf_gate": _gate(), "quality_gate": _gate()},
    )

    gates, _ = eval_summary.collect(tmp_path)
    markdown, _ = eval_summary.render(gates, [])

    assert markdown.index("Judged scenario quality") < markdown.index("Performance")


def test_no_reports_says_so_loudly_instead_of_reporting_a_pass(tmp_path):
    markdown, warnings = eval_summary.render({}, [])

    assert "No gate report was produced" in markdown
    assert "Do NOT read this as a pass." in markdown
    assert len(warnings) == 1


def test_corrupt_report_is_surfaced_not_swallowed(tmp_path):
    (tmp_path / "gate_report.json").write_text("{not json", encoding="utf-8")

    gates, unreadable = eval_summary.collect(tmp_path)
    markdown, warnings = eval_summary.render(gates, unreadable)

    assert gates == {}
    assert len(unreadable) == 1
    assert "Unreadable report files" in markdown
    assert any("unreadable" in w for w in warnings)


def test_unknown_gate_is_reported_under_its_raw_key(tmp_path):
    """A future gate (e.g. a sequence-pin gate) must not silently vanish."""
    _write(tmp_path, "gate_report.json", {"honesty_gate": _gate(passed=False)})

    gates, _ = eval_summary.collect(tmp_path)
    markdown, warnings = eval_summary.render(gates, [])

    assert "honesty_gate" in markdown
    assert warnings


def test_non_gate_objects_with_a_passed_key_are_ignored(tmp_path):
    """Only ``*_gate`` objects are verdicts; per-scenario results also carry `passed`."""
    _write(tmp_path, "gate_report.json", {"scenarios": [{"passed": False, "id": "s1"}]})

    gates, _ = eval_summary.collect(tmp_path)

    assert gates == {}


def test_conflicting_duplicate_verdicts_are_surfaced(tmp_path):
    """Two reports disagreeing about one gate is missing evidence, not a pass."""
    _write(tmp_path, "a_report.json", {"quality_gate": _gate(passed=True)})
    _write(tmp_path, "b_report.json", {"quality_gate": _gate(passed=False)})

    gates, unreadable = eval_summary.collect(tmp_path)

    assert len(unreadable) == 1
    assert "conflicting verdict" in unreadable[0]
    # The first-seen verdict is kept, but the conflict is flagged alongside it.
    assert "quality_gate" in gates


@pytest.mark.parametrize("exists", [True, False])
def test_main_always_exits_zero_and_appends_the_summary(
    tmp_path, monkeypatch, exists, capsys
):
    """It is a reporter: it never turns a passing run red nor rescues a failing one."""
    eval_dir = tmp_path / "eval-out"
    if exists:
        eval_dir.mkdir()
        _write(eval_dir, "gate_report.json", {"perf_gate": _gate(passed=False)})
    summary_file = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))

    assert eval_summary.main([str(eval_dir)]) == 0

    written = summary_file.read_text(encoding="utf-8")
    assert "GAIA agent eval - gate verdicts" in written
    assert "::warning::" in capsys.readouterr().out
