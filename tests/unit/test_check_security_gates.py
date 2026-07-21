# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for the bandit HIGH security gate (util/check_security_gates.py)."""

import json
import sys
from pathlib import Path

import pytest

# util/ is not a package; add it to the path so we can import the module.
_UTIL_DIR = Path(__file__).resolve().parents[2] / "util"
sys.path.insert(0, str(_UTIL_DIR))

import check_security_gates  # noqa: E402
from check_security_gates import (  # noqa: E402
    bandit_finding_key,
    load_baseline,
    new_bandit_highs,
    parse_bandit_json,
    run_bandit,
)


def _finding(path, test_id, severity="HIGH", line=10):
    return {
        "filename": path,
        "test_id": test_id,
        "issue_severity": severity,
        "line_number": line,
        "issue_text": f"{test_id} issue",
    }


class TestBanditFindingKey:
    def test_key_is_path_and_test_id(self):
        f = _finding("src/gaia/util.py", "B602", line=42)
        assert bandit_finding_key(f) == ("src/gaia/util.py", "B602")

    def test_key_normalizes_windows_separators(self):
        f = _finding("src\\gaia\\util.py", "B602")
        assert bandit_finding_key(f) == ("src/gaia/util.py", "B602")

    def test_key_ignores_line_number(self):
        a = _finding("src/gaia/util.py", "B602", line=10)
        b = _finding("src/gaia/util.py", "B602", line=999)
        assert bandit_finding_key(a) == bandit_finding_key(b)


class TestNewBanditHighs:
    def test_empty_baseline_reports_all_highs(self):
        results = [
            _finding("src/gaia/a.py", "B602"),
            _finding("src/gaia/b.py", "B605"),
        ]
        new = new_bandit_highs(results, [])
        assert len(new) == 2

    def test_only_high_severity_reported(self):
        results = [
            _finding("src/gaia/a.py", "B602", severity="HIGH"),
            _finding("src/gaia/b.py", "B101", severity="LOW"),
            _finding("src/gaia/c.py", "B303", severity="MEDIUM"),
        ]
        new = new_bandit_highs(results, [])
        assert [r["filename"] for r in new] == ["src/gaia/a.py"]

    def test_baseline_dict_entries_are_excluded(self):
        results = [_finding("src/gaia/a.py", "B602")]
        baseline = [{"path": "src/gaia/a.py", "test_id": "B602"}]
        assert new_bandit_highs(results, baseline) == []

    def test_baseline_pair_entries_are_excluded(self):
        results = [_finding("src/gaia/a.py", "B602")]
        baseline = [["src/gaia/a.py", "B602"]]
        assert new_bandit_highs(results, baseline) == []

    def test_baseline_matches_regardless_of_line_number(self):
        # A cosmetic line shift must not re-trip a baselined finding.
        results = [_finding("src/gaia/a.py", "B602", line=500)]
        baseline = [{"path": "src/gaia/a.py", "test_id": "B602"}]
        assert new_bandit_highs(results, baseline) == []

    def test_baseline_normalizes_windows_paths(self):
        results = [_finding("src\\gaia\\a.py", "B602")]
        baseline = [{"path": "src/gaia/a.py", "test_id": "B602"}]
        assert new_bandit_highs(results, baseline) == []

    def test_non_baselined_high_still_reported_when_others_allowlisted(self):
        results = [
            _finding("src/gaia/a.py", "B602"),  # allowlisted
            _finding("src/gaia/b.py", "B605"),  # new
        ]
        baseline = [{"path": "src/gaia/a.py", "test_id": "B602"}]
        new = new_bandit_highs(results, baseline)
        assert [r["filename"] for r in new] == ["src/gaia/b.py"]

    def test_same_test_id_different_file_not_masked(self):
        results = [_finding("src/gaia/b.py", "B602")]
        baseline = [{"path": "src/gaia/a.py", "test_id": "B602"}]
        assert len(new_bandit_highs(results, baseline)) == 1


class TestParseBanditJson:
    def test_strips_progress_bar_prefix(self):
        raw = 'Working... 100%\n{"results": [], "errors": []}'
        assert parse_bandit_json(raw) == {"results": [], "errors": []}

    def test_plain_json(self):
        assert parse_bandit_json('{"results": []}') == {"results": []}

    def test_no_json_raises(self):
        with pytest.raises(ValueError):
            parse_bandit_json("Working... no json here")


class TestLoadBaseline:
    def test_missing_file_returns_empty(self, tmp_path):
        assert load_baseline(tmp_path / "nope.json") == []

    def test_bare_list(self, tmp_path):
        p = tmp_path / "baseline.json"
        p.write_text(json.dumps([{"path": "a.py", "test_id": "B602"}]))
        assert load_baseline(p) == [{"path": "a.py", "test_id": "B602"}]

    def test_findings_key(self, tmp_path):
        p = tmp_path / "baseline.json"
        p.write_text(json.dumps({"findings": [{"path": "a.py", "test_id": "B602"}]}))
        assert load_baseline(p) == [{"path": "a.py", "test_id": "B602"}]


class TestRunBanditErrors:
    def test_missing_binary_raises_runtimeerror(self, monkeypatch):
        # A missing bandit binary makes subprocess.run raise FileNotFoundError;
        # it must surface as an actionable RuntimeError, not crash the caller.
        def _boom(*_a, **_k):
            raise FileNotFoundError("bandit")

        monkeypatch.setattr(check_security_gates.subprocess, "run", _boom)
        with pytest.raises(RuntimeError, match="bandit is not installed"):
            run_bandit("src/gaia")


class TestRepoBaselineIsEmpty:
    """The shipped baseline must stay empty — all pre-existing HIGH were fixed."""

    def test_repo_baseline_has_no_findings(self):
        repo_root = Path(__file__).resolve().parents[2]
        baseline = load_baseline(repo_root / ".bandit-baseline.json")
        assert baseline == [], (
            "New HIGH findings must be fixed, not baselined. "
            f"Unexpected allowlist entries: {baseline}"
        )
