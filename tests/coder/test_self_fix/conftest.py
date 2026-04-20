# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Fixtures for the self-correction loop tests.

The self-fix modules do real git / pytest / subprocess work. These fixtures
prepare an isolated tmp git repo with a ``coder`` branch so the tests can
exercise branch creation, differential verify, and publish without touching
the real amd/gaia checkout.
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from typing import Callable

import pytest

from gaia.coder.stores import feedback as feedback_store


@pytest.fixture()
def tmp_git_repo(tmp_path: Path) -> Path:
    """Return a path to a freshly-initialised git repo.

    Layout:
    * root with a ``README.md`` committed on ``main``,
    * a ``coder`` branch branched off ``main`` (§5.6 — self-fix PRs target
      ``coder`` never ``main``),
    * a ``tests/coder/regression/`` dir pre-created so
      :func:`write_regression_test` can write into it,
    * a ``src/gaia/coder/sample.py`` under ``coder`` with a known-buggy
      comment the triage classifier can point at.
    """
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "test")
    _git(root, "config", "commit.gpgsign", "false")

    (root / "README.md").write_text("# test repo\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "initial")

    _git(root, "checkout", "-q", "-b", "coder")

    # Buggy sample file — the triage tests use this as a candidate_file.
    sample_dir = root / "src" / "gaia" / "coder"
    sample_dir.mkdir(parents=True, exist_ok=True)
    (sample_dir / "sample.py").write_text(
        "# gaia-coder sample module\n"
        "def classify_failure(err):\n"
        "    # BUG: cache collision on timestamped errors\n"
        "    return err\n",
        encoding="utf-8",
    )
    (root / "tests" / "coder" / "regression").mkdir(parents=True, exist_ok=True)

    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "seed coder branch")
    return root


@pytest.fixture()
def feedback_db_path(tmp_path: Path) -> Path:
    """Fresh feedback.db under ``tmp_path``."""
    path = tmp_path / "feedback.db"
    conn = feedback_store.open_store(path)
    conn.close()
    return path


@pytest.fixture()
def memory_db_path(tmp_path: Path) -> Path:
    """Fresh memory.db under ``tmp_path``."""
    from gaia.coder.stores import memory as memory_store

    path = tmp_path / "memory.db"
    conn = memory_store.open_store(path)
    conn.close()
    return path


@pytest.fixture()
def seed_feedback(feedback_db_path: Path) -> Callable[..., str]:
    """Factory fixture: insert one pending feedback row and return its id."""

    def _seed(
        body: str = "classify_failure misfires on timestamped errors",
        severity: str = "high",
        from_handle: str = "test-em",
        context_url: str = "https://github.com/amd/gaia/pull/9999",
        feedback_id: str | None = None,
    ) -> str:
        fid = feedback_id or f"fb-{uuid.uuid4().hex[:8]}"
        conn = feedback_store.open_store(feedback_db_path)
        try:
            feedback_store.insert_row(
                conn,
                feedback_store.FeedbackRow(
                    id=fid,
                    received_at="2026-04-20T00:00:00+00:00",
                    from_handle=from_handle,
                    channel="cli",
                    severity=severity,
                    body=body,
                    context_url=context_url,
                ),
            )
        finally:
            conn.close()
        return fid

    return _seed


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Run ``git <args...>`` with check=True and text capture."""
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )
