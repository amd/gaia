#  Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for :mod:`gaia.coder.dev_mode` (§7.1).

The hard precondition is ``editable-install + matching origin`` — our
fixture gives us a tmp git repo initialised with a fake origin remote. The
soft gate is ``em.toml``; ``session.json`` lives under ``tmp_path``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gaia.coder import dev_mode, trust
from gaia.coder.stores import audit as audit_store

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    """Initialise a tmp git repo with a bogus origin URL and return the root.

    The repo contains a single committed file so ``git rev-parse`` returns a
    valid toplevel. The origin remote points at ``git@github.com:amd/gaia.git``
    so tests exercise the "matching origin" branch by default.
    """
    subprocess.run(
        ["git", "init", "-q", str(tmp_path)], check=True, capture_output=True
    )
    (tmp_path / "README.md").write_text("stub")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "README.md"],
        check=True,
        capture_output=True,
    )
    # Git refuses to commit without a user.email/name — set them locally so
    # the fixture is hermetic.
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.email=test@test",
            "-c",
            "user.name=test",
            "commit",
            "-qm",
            "init",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "remote",
            "add",
            "origin",
            "git@github.com:amd/gaia.git",
        ],
        check=True,
        capture_output=True,
    )
    return tmp_path


@pytest.fixture
def repo_binding_toml(tmp_path: Path) -> Path:
    """Write a minimal repo_binding.toml under tmp_path."""
    path = tmp_path / "repo_binding.toml"
    path.write_text("""
repo = "amd/gaia"
github_app_id = 12345
github_installation_id = 67890
webhook_secret_keyring_slot = "gaia-coder/webhook"
private_key_keyring_slot = "gaia-coder/pem"
""".strip())
    return path


@pytest.fixture
def em_cfg_off(tmp_path: Path) -> Path:
    """em.toml with dev_mode_self_edit=false."""
    path = tmp_path / "em.toml"
    cfg = trust.EMConfig(
        em_handle="kovtcharov-amd",
        em_channel="github-issue-comment",
        dev_mode_self_edit=False,
    )
    trust.save_em_config(path, cfg)
    return path


@pytest.fixture
def em_cfg_on(tmp_path: Path) -> Path:
    """em.toml with dev_mode_self_edit=true."""
    path = tmp_path / "em.toml"
    cfg = trust.EMConfig(
        em_handle="kovtcharov-amd",
        em_channel="github-issue-comment",
        dev_mode_self_edit=True,
        dev_mode_enabled_at="2026-04-20T00:00:00Z",
        dev_mode_enabled_reason="test",
    )
    trust.save_em_config(path, cfg)
    return path


# ---------------------------------------------------------------------------
# detect_dev_mode
# ---------------------------------------------------------------------------


def test_detect_returns_false_when_not_in_git_repo(
    tmp_path: Path,
    em_cfg_off: Path,
    repo_binding_toml: Path,
):
    """No git repo at all → editable_install=False."""
    status = dev_mode.detect_dev_mode(
        em_cfg_path=em_cfg_off,
        repo_binding_path=repo_binding_toml,
        source_root=tmp_path,  # no .git here
    )
    assert status.editable_install is False
    assert "git rev-parse" in status.reason


def test_detect_returns_true_when_origin_matches(
    fake_repo: Path, em_cfg_on: Path, repo_binding_toml: Path
):
    """Matching origin + em.toml allow = both booleans True."""
    status = dev_mode.detect_dev_mode(
        em_cfg_path=em_cfg_on,
        repo_binding_path=repo_binding_toml,
        source_root=fake_repo,
    )
    assert status.editable_install is True
    assert status.em_allowlist is True
    assert status.hard_precondition_met is True
    assert status.reason == ""


def test_detect_returns_false_when_origin_mismatches(
    fake_repo: Path, em_cfg_off: Path, repo_binding_toml: Path
):
    """Origin remote does not match binding.repo → editable_install=False."""
    subprocess.run(
        [
            "git",
            "-C",
            str(fake_repo),
            "remote",
            "set-url",
            "origin",
            "https://github.com/someone-else/other-repo.git",
        ],
        check=True,
        capture_output=True,
    )
    status = dev_mode.detect_dev_mode(
        em_cfg_path=em_cfg_off,
        repo_binding_path=repo_binding_toml,
        source_root=fake_repo,
    )
    assert status.editable_install is False
    assert "does not match" in status.reason


def test_detect_em_allowlist_is_independent_of_precondition(
    tmp_path: Path, em_cfg_on: Path, repo_binding_toml: Path
):
    """Even when precondition fails, em_allowlist reflects em.toml truthfully."""
    status = dev_mode.detect_dev_mode(
        em_cfg_path=em_cfg_on,
        repo_binding_path=repo_binding_toml,
        source_root=tmp_path,
    )
    assert status.editable_install is False
    assert status.em_allowlist is True


def test_match_origin_handles_ssh_and_https():
    """_match_origin should accept SSH, HTTPS, with and without .git."""
    assert dev_mode._match_origin("git@github.com:amd/gaia.git", "amd/gaia")
    assert dev_mode._match_origin("https://github.com/amd/gaia.git", "amd/gaia")
    assert dev_mode._match_origin("https://github.com/amd/gaia", "amd/gaia")
    assert not dev_mode._match_origin("https://github.com/amd/other", "amd/gaia")
    assert not dev_mode._match_origin("", "amd/gaia")


# ---------------------------------------------------------------------------
# enable_session / disable_session
# ---------------------------------------------------------------------------


def test_enable_session_refuses_when_precondition_fails(
    tmp_path: Path, em_cfg_off: Path
):
    """No editable install → enable_session raises DevModeError."""
    em_cfg = trust.load_em_config(em_cfg_off)
    status = dev_mode.DevModeStatus(
        editable_install=False, em_allowlist=False, reason="PyPI install"
    )
    session = tmp_path / "session.json"
    with pytest.raises(dev_mode.DevModeError, match="precondition failed"):
        dev_mode.enable_session(em_cfg, "try", session_path=session, status=status)


def test_enable_and_disable_session_roundtrip(
    fake_repo: Path,
    em_cfg_off: Path,
    repo_binding_toml: Path,
    tmp_path: Path,
):
    """Happy path: enable then disable clears the file entirely."""
    em_cfg = trust.load_em_config(em_cfg_off)
    status = dev_mode.detect_dev_mode(
        em_cfg_path=em_cfg_off,
        repo_binding_path=repo_binding_toml,
        source_root=fake_repo,
    )
    assert status.editable_install
    session = tmp_path / "sub" / "session.json"
    dev_mode.enable_session(em_cfg, "test reason", session_path=session, status=status)
    data = json.loads(session.read_text())
    assert data["dev_mode_session"] is True
    assert data["dev_mode_session_reason"] == "test reason"
    assert data["em_handle"] == em_cfg.em_handle

    dev_mode.disable_session(session_path=session)
    assert not session.exists()


def test_enable_session_requires_non_empty_reason(
    fake_repo: Path,
    em_cfg_off: Path,
    repo_binding_toml: Path,
    tmp_path: Path,
):
    """An empty reason is a fail-loudly violation, not silently accepted."""
    em_cfg = trust.load_em_config(em_cfg_off)
    status = dev_mode.DevModeStatus(editable_install=True, em_allowlist=False)
    session = tmp_path / "session.json"
    with pytest.raises(dev_mode.DevModeError, match="non-empty reason"):
        dev_mode.enable_session(em_cfg, "", session_path=session, status=status)


def test_enable_session_writes_audit_row(
    fake_repo: Path,
    em_cfg_off: Path,
    repo_binding_toml: Path,
    tmp_path: Path,
):
    """enable_session appends one row to audit.log.db when a conn is provided."""
    em_cfg = trust.load_em_config(em_cfg_off)
    status = dev_mode.DevModeStatus(editable_install=True, em_allowlist=False)
    audit_conn = audit_store.open_store(tmp_path / "audit.db")
    try:
        dev_mode.enable_session(
            em_cfg,
            "trial",
            session_path=tmp_path / "session.json",
            status=status,
            audit_conn=audit_conn,
        )
        rows = audit_store.list_rows(audit_conn)
    finally:
        audit_conn.close()
    assert len(rows) == 1
    assert rows[0].tool_name == "dev_mode.enable_session"
    payload = json.loads(rows[0].args_json)
    assert payload["reason"] == "trial"
    assert payload["scope"] == "session"


# ---------------------------------------------------------------------------
# enable_permanent
# ---------------------------------------------------------------------------


def test_enable_permanent_updates_em_toml(
    fake_repo: Path,
    em_cfg_off: Path,
    repo_binding_toml: Path,
    tmp_path: Path,
):
    """enable_permanent flips dev_mode_self_edit=true in em.toml."""
    em_cfg = trust.load_em_config(em_cfg_off)
    status = dev_mode.DevModeStatus(editable_install=True, em_allowlist=False)
    updated = dev_mode.enable_permanent(
        em_cfg, "long-term dev", em_cfg_path=em_cfg_off, status=status
    )
    assert updated.dev_mode_self_edit is True
    assert updated.dev_mode_enabled_reason == "long-term dev"

    # Reload to confirm persistence.
    reloaded = trust.load_em_config(em_cfg_off)
    assert reloaded.dev_mode_self_edit is True


def test_enable_permanent_refuses_when_precondition_fails(em_cfg_off: Path):
    em_cfg = trust.load_em_config(em_cfg_off)
    status = dev_mode.DevModeStatus(editable_install=False, em_allowlist=False)
    with pytest.raises(dev_mode.DevModeError, match="hard precondition failed"):
        dev_mode.enable_permanent(em_cfg, "x", em_cfg_path=em_cfg_off, status=status)


# ---------------------------------------------------------------------------
# is_enabled
# ---------------------------------------------------------------------------


def test_is_enabled_false_without_precondition(
    tmp_path: Path, em_cfg_on: Path, repo_binding_toml: Path
):
    """em.toml says yes, but no editable install → is_enabled=False."""
    assert not dev_mode.is_enabled(
        em_cfg_path=em_cfg_on,
        repo_binding_path=repo_binding_toml,
        source_root=tmp_path,
    )


def test_is_enabled_true_via_em_toml(
    fake_repo: Path, em_cfg_on: Path, repo_binding_toml: Path, tmp_path: Path
):
    """Hard precondition + em.toml allow = True."""
    assert dev_mode.is_enabled(
        em_cfg_path=em_cfg_on,
        repo_binding_path=repo_binding_toml,
        source_root=fake_repo,
        session_path=tmp_path / "no-session.json",
    )


def test_is_enabled_true_via_session_flag_alone(
    fake_repo: Path, em_cfg_off: Path, repo_binding_toml: Path, tmp_path: Path
):
    """session.json flag alone is sufficient when precondition passes."""
    session = tmp_path / "session.json"
    session.write_text(json.dumps({"dev_mode_session": True}))
    assert dev_mode.is_enabled(
        em_cfg_path=em_cfg_off,
        repo_binding_path=repo_binding_toml,
        source_root=fake_repo,
        session_path=session,
    )


def test_corrupt_session_file_raises(tmp_path: Path):
    """Bad JSON surfaces as DevModeError (no silent fallback)."""
    session = tmp_path / "session.json"
    session.write_text("{not json}")
    with pytest.raises(dev_mode.DevModeError, match="not valid JSON"):
        dev_mode._read_session(session)
