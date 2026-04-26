# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Regression tests for the Pass-2 pytest-glob bug.

Originally :func:`gaia.coder.review.pass_2_functional._infer_test_paths`
returned strings like ``"tests/**/test_foo.py"``. pytest does not glob
``**`` — it treats the path as a literal, fails to collect, and Pass 2
short-circuits the whole review gate as ``block``. The fix expands the
``**`` ourselves via :meth:`pathlib.Path.rglob` and only feeds pytest
test files that actually exist.

These tests pin the new contract:

1. Changed source under ``src/...`` with a matching ``tests/**/test_<stem>.py``
   resolves to the real test file path, relative to ``repo_root``.
2. Changed source with no matching test file resolves to ``[]`` (and the
   caller falls through to whole-suite pytest).
3. Files already under ``tests/`` come back as-is when they exist.
"""

from __future__ import annotations

from pathlib import Path

from gaia.coder.review.pass_2_functional import _infer_test_paths


def _scaffold_repo(root: Path, sources: list[str], tests: list[str]) -> None:
    """Create empty placeholder files at *sources* and *tests* under *root*."""
    for rel in (*sources, *tests):
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# placeholder\n", encoding="utf-8")


class TestInferTestPaths:
    def test_resolves_matching_test_file(self, tmp_path: Path) -> None:
        # Layout: src/gaia/coder/foo.py + tests/coder/test_foo.py both exist.
        _scaffold_repo(
            tmp_path,
            sources=["src/gaia/coder/foo.py"],
            tests=["tests/coder/test_foo.py"],
        )
        out = _infer_test_paths(["src/gaia/coder/foo.py"], repo_root=tmp_path)
        assert out == ["tests/coder/test_foo.py"], (
            "Expected the rglob expansion to find the matching test file. "
            f"Got: {out!r}"
        )

    def test_returns_empty_when_no_matching_test(self, tmp_path: Path) -> None:
        # Layout: src/gaia/coder/bar.py exists but no tests/**/test_bar.py.
        _scaffold_repo(
            tmp_path,
            sources=["src/gaia/coder/bar.py"],
            tests=[],
        )
        # tests/ dir does not even exist — rglob must tolerate that.
        out = _infer_test_paths(["src/gaia/coder/bar.py"], repo_root=tmp_path)
        assert out == [], (
            "Expected an empty list so _run_pytest can fall through to its "
            f"whole-suite fallback. Got: {out!r}"
        )

    def test_passes_through_existing_test_file(self, tmp_path: Path) -> None:
        # Changed file is itself under tests/. Should come back as-is.
        _scaffold_repo(
            tmp_path,
            sources=[],
            tests=["tests/coder/test_loop_driver.py"],
        )
        out = _infer_test_paths(["tests/coder/test_loop_driver.py"], repo_root=tmp_path)
        assert out == [
            "tests/coder/test_loop_driver.py"
        ], f"Test paths under tests/ should be preserved verbatim. Got: {out!r}"

    def test_dedup_when_stem_and_parent_match_same_file(self, tmp_path: Path) -> None:
        # The function may emit two glob candidates per source file (stem and
        # parent). When both expand to the same file, it must appear once.
        _scaffold_repo(
            tmp_path,
            sources=["src/gaia/coder/coder.py"],
            tests=["tests/coder/test_coder.py"],
        )
        out = _infer_test_paths(["src/gaia/coder/coder.py"], repo_root=tmp_path)
        assert out == [
            "tests/coder/test_coder.py"
        ], f"Duplicate matches must be de-duped. Got: {out!r}"

    def test_skips_glob_metacharacters_in_pytest_arg(self, tmp_path: Path) -> None:
        # The fix's whole point: no returned path may contain '*'.
        _scaffold_repo(
            tmp_path,
            sources=["src/gaia/coder/baz.py"],
            tests=["tests/coder/test_baz.py"],
        )
        out = _infer_test_paths(["src/gaia/coder/baz.py"], repo_root=tmp_path)
        for entry in out:
            assert "*" not in entry, (
                f"_infer_test_paths must not return raw globs for pytest. "
                f"Got entry containing '*': {entry!r}"
            )
