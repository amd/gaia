# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for gaia.eval.fixture_paths (non-editable-install robustness, #2047)."""

import pytest

from gaia.eval import fixture_paths
from gaia.eval.fixture_paths import resolve_repo_fixture


def test_resolves_committed_manifest_in_editable_checkout():
    """The editable-checkout layout still resolves a real committed manifest."""
    path = resolve_repo_fixture("email", "drafting_gate_thresholds.json")
    assert path.is_file()
    assert path.parts[-4:] == (
        "tests",
        "fixtures",
        "email",
        "drafting_gate_thresholds.json",
    )


def test_missing_fixture_fails_loud_with_actionable_message():
    """A fixture absent from every root raises a FileNotFoundError naming the fix."""
    with pytest.raises(FileNotFoundError) as exc:
        resolve_repo_fixture("email", "does_not_exist.json")
    msg = str(exc.value)
    assert "does_not_exist.json" in msg
    assert "GAIA_REPO_ROOT" in msg
    assert "pip install -e" in msg


def test_env_root_used_when_module_layout_absent(tmp_path, monkeypatch):
    """GAIA_REPO_ROOT resolves the fixture when the editable layout lacks it."""
    # Point the module-relative candidate at a dir with no tests/fixtures tree,
    # simulating a non-editable install under site-packages.
    fake_pkg = tmp_path / "site-packages" / "gaia" / "eval" / "fixture_paths.py"
    fake_pkg.parent.mkdir(parents=True)
    fake_pkg.write_text("")
    monkeypatch.setattr(fixture_paths, "__file__", str(fake_pkg))

    repo = tmp_path / "checkout"
    manifest = repo / "tests" / "fixtures" / "email" / "quality_gate_thresholds.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}")
    monkeypatch.setenv("GAIA_REPO_ROOT", str(repo))
    monkeypatch.chdir(tmp_path)  # keep CWD out of the way

    assert resolve_repo_fixture("email", "quality_gate_thresholds.json") == manifest
