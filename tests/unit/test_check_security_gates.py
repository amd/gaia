# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for util/check_security_gates.py (the suppression review gate).

Covers the scanner (which comment forms count as a security suppression), the
allowlist loader (justification required), the violation diff, and a backtest
proving the gate flags the exact suppression that hid the hub tar-slip.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util"))

import check_security_gates as gates  # noqa: E402


def _write(tmp_path: Path, rel: str, body: str) -> None:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


# ---------------------------------------------------------------------------
# find_suppressions
# ---------------------------------------------------------------------------


def test_detects_noqa_bandit_code(tmp_path):
    _write(tmp_path, "src/a.py", "x = 1  # noqa: S202 - trust me\n")
    found = gates.find_suppressions(tmp_path)
    assert found == [{"path": "src/a.py", "rule": "S202", "line": 1}]


def test_detects_nosec_with_and_without_code(tmp_path):
    _write(
        tmp_path,
        "src/b.py",
        "a()  # nosec B104 - opt-in\nb()  # nosec\n",
    )
    found = {(f["path"], f["rule"]) for f in gates.find_suppressions(tmp_path)}
    assert ("src/b.py", "B104") in found
    assert ("src/b.py", "nosec") in found


def test_ignores_non_security_noqa_codes(tmp_path):
    # SLF001 (flake8-self) and E402 are not bandit security codes — must be ignored.
    _write(
        tmp_path, "src/c.py", "z = obj._x  # noqa: SLF001\nimport os  # noqa: E402\n"
    )
    assert gates.find_suppressions(tmp_path) == []


def test_scans_hub_not_just_src(tmp_path):
    _write(tmp_path, "hub/agents/x/y.py", "run()  # nosec\n")
    found = gates.find_suppressions(tmp_path)
    assert found and found[0]["path"] == "hub/agents/x/y.py"


def test_excludes_vendored_dirs(tmp_path):
    _write(tmp_path, "src/node_modules/dep.py", "x = 1  # noqa: S202\n")
    assert gates.find_suppressions(tmp_path) == []


# ---------------------------------------------------------------------------
# violations
# ---------------------------------------------------------------------------


def test_allowlisted_suppression_is_not_a_violation():
    supp = [{"path": "src/a.py", "rule": "S603", "line": 5}]
    allow = {("src/a.py", "S603")}
    assert gates.suppression_violations(supp, allow) == []


def test_unlisted_suppression_is_a_violation():
    supp = [{"path": "src/a.py", "rule": "S202", "line": 5}]
    assert gates.suppression_violations(supp, set()) == supp


# ---------------------------------------------------------------------------
# load_allowlist
# ---------------------------------------------------------------------------


def test_allowlist_requires_justification(tmp_path, monkeypatch):
    f = tmp_path / ".security-suppressions.json"
    f.write_text(
        '{"suppressions": [{"path": "src/a.py", "rule": "S603"}]}', encoding="utf-8"
    )
    monkeypatch.setattr(gates, "SUPPRESSIONS_FILE", f)
    with pytest.raises(ValueError, match="justification"):
        gates.load_allowlist()


def test_allowlist_loads_valid_entries(tmp_path, monkeypatch):
    f = tmp_path / ".security-suppressions.json"
    f.write_text(
        '{"suppressions": [{"path": "src/a.py", "rule": "S603", '
        '"justification": "constructed args"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(gates, "SUPPRESSIONS_FILE", f)
    assert gates.load_allowlist() == {("src/a.py", "S603")}


# ---------------------------------------------------------------------------
# Backtest: the gate would have caught the hub tar-slip (CWE-22)
# ---------------------------------------------------------------------------


def test_backtest_flags_the_tarslip_suppression(tmp_path):
    """The tar-slip shipped as `tf.extractall(...)  # noqa: S202 - hub artifacts
    are trusted`. With that line present and NOT in the allowlist, the gate must
    flag it — i.e. this gate would have forced review before it merged."""
    _write(
        tmp_path,
        "src/gaia/hub/installer.py",
        "with tarfile.open(p) as tf:\n"
        "    tf.extractall(d)  # noqa: S202 - hub artifacts are trusted\n",
    )
    found = gates.find_suppressions(tmp_path)
    violations = gates.suppression_violations(found, allowlist=set())
    assert any(v["rule"] == "S202" for v in violations)


# ---------------------------------------------------------------------------
# The real repo passes its own gate
# ---------------------------------------------------------------------------


def test_repo_suppressions_all_reviewed():
    """Every real suppression in the tree is in the committed allowlist."""
    ok, _msgs = gates.check_suppressions()
    assert ok is True
