# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Real Git operations including retries after an interrupted job record write."""

import subprocess

import pytest

from gaia.engineering.repository import Repository


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    remote = tmp_path / "remote"
    remote.mkdir()
    git(remote, "init")
    git(remote, "config", "user.email", "test@example.invalid")
    git(remote, "config", "user.name", "Fixture")
    (remote / "hello.txt").write_text("baseline")
    git(remote, "add", ".")
    git(remote, "commit", "-m", "baseline")
    return Repository(tmp_path / "cache", remote=str(remote)), remote


def test_clone_worktree_retry_preserves_base_and_work(repo):
    cache, remote = repo
    original = cache.ensure_clone()["head"]
    job_id = "a" * 32
    first = cache.create_worktree(job_id)
    from pathlib import Path

    path = Path(first["path"])
    (path / "hello.txt").write_text("unfinished private local work")
    (remote / "hello.txt").write_text("upstream advanced")
    git(remote, "add", ".")
    git(remote, "commit", "-m", "advance")
    assert cache.ensure_clone()["head"] != original
    retry = cache.create_worktree(job_id)
    assert retry == first
    assert retry["base_commit"] == original
    assert (path / "hello.txt").read_text() == "unfinished private local work"
    second = cache.create_worktree("b" * 32)
    assert second["base_commit"] != original
    assert second["path"] != first["path"]


def test_bad_paths_and_wrong_cached_remote_refused(repo):
    cache, _ = repo
    cache.ensure_clone()
    with pytest.raises(ValueError):
        cache.create_worktree("../outside")
    with pytest.raises(ValueError):
        cache.create_worktree("a" * 32, "--evil")
    cache.git(
        "--git-dir",
        str(cache.repo),
        "remote",
        "set-url",
        "origin",
        "https://example.invalid/wrong",
    )
    with pytest.raises(ValueError, match="remote"):
        cache.ensure_clone()


def test_service_worktree_gate_idempotency_and_preview(repo, tmp_path):
    from gaia.engineering.service import EngineeringService

    cache, _ = repo
    cache.ensure_clone()
    service = EngineeringService(tmp_path / "service", developer_mode=True)
    service.repository = cache
    job = service.share("codex", "Fix reproduction", "synthetic only")
    with pytest.raises(PermissionError):
        service.prepare_worktree(job["id"], "codex", "request-1", job["revision"])
    service.report_diagnosis(
        job["id"], "codex", "Configuration verified; isolated patch proposed"
    )
    service.approve_code(
        job["id"], expected_revision=service.status(job["id"])["revision"]
    )
    revision = service.status(job["id"])["revision"]
    first = service.prepare_worktree(job["id"], "codex", "request-1", revision)
    assert service.prepare_worktree(job["id"], "codex", "request-1", revision) == first
    preview = service.register_preview(
        job["id"],
        "codex",
        "tui",
        first["base_commit"],
        "Use the native app's isolated terminal",
    )
    assert preview["verification"] == "reported"
    assert preview["process_owner"] == "coding_app"
    service.revoke(job["id"])
    with pytest.raises(PermissionError):
        service.prepare_worktree(job["id"], "codex", "request-1", revision)
