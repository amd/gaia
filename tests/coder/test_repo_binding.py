# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for :mod:`gaia.coder.repo_binding` (§5.11, §15.6).

Every test injects its own ``keyring_getter`` and ``gh_runner`` — no real
keyring, no real ``gh`` CLI, no real network.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import textwrap
from pathlib import Path

import pytest

from gaia.coder.repo_binding import (
    DoctorResult,
    RepoBinding,
    RepoBindingError,
    agents_md_entry,
    doctor,
    load_repo_binding,
    verify_webhook_signature,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _write_toml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "repo_binding.toml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


VALID_TOML = """\
repo = "amd/gaia"
github_app_id = 123456
github_installation_id = 7890123
webhook_secret_keyring_slot = "gaia-coder/github-webhook-secret"
private_key_keyring_slot = "gaia-coder/github-app-private-key"
allowed_branches = ["auto/gaia-coder/*", "auto/gaia-coder-heal/*", "coder"]
forbidden_paths = [".github/workflows/release-*", "scripts/release/**"]
"""


def _valid_binding() -> RepoBinding:
    return RepoBinding(
        repo="amd/gaia",
        github_app_id=123456,
        github_installation_id=7890123,
        webhook_secret_keyring_slot="gaia-coder/github-webhook-secret",
        private_key_keyring_slot="gaia-coder/github-app-private-key",
        allowed_branches=["coder"],
        forbidden_paths=[".github/workflows/release-*"],
    )


# ---------------------------------------------------------------------------
# load_repo_binding
# ---------------------------------------------------------------------------


def test_load_repo_binding_valid(tmp_path):
    p = _write_toml(tmp_path, VALID_TOML)
    binding = load_repo_binding(p)
    assert binding.repo == "amd/gaia"
    assert binding.github_app_id == 123456
    assert "coder" in binding.allowed_branches


def test_load_repo_binding_missing_file(tmp_path):
    p = tmp_path / "missing.toml"
    with pytest.raises(RepoBindingError, match="not found"):
        load_repo_binding(p)


def test_load_repo_binding_malformed_toml(tmp_path):
    p = _write_toml(tmp_path, "repo = 'amd/gaia\n")  # unclosed quote
    with pytest.raises(RepoBindingError, match="malformed TOML"):
        load_repo_binding(p)


def test_load_repo_binding_rejects_bad_repo_slug(tmp_path):
    bad = VALID_TOML.replace('repo = "amd/gaia"', 'repo = "no-slash-here"')
    p = _write_toml(tmp_path, bad)
    with pytest.raises(RepoBindingError, match="failed validation"):
        load_repo_binding(p)


def test_load_repo_binding_rejects_negative_app_id(tmp_path):
    bad = VALID_TOML.replace(
        "github_app_id = 123456", "github_app_id = -1"
    )
    p = _write_toml(tmp_path, bad)
    with pytest.raises(RepoBindingError):
        load_repo_binding(p)


def test_load_repo_binding_missing_required_field(tmp_path):
    # Drop the `github_installation_id` line.
    bad = "\n".join(
        line
        for line in VALID_TOML.splitlines()
        if "github_installation_id" not in line
    )
    p = _write_toml(tmp_path, bad)
    with pytest.raises(RepoBindingError):
        load_repo_binding(p)


# ---------------------------------------------------------------------------
# doctor — aggregated checks
# ---------------------------------------------------------------------------


def _happy_gh_runner(binding: RepoBinding):
    def _run(argv):
        if argv[:2] == ["api", "/app"]:
            return json.dumps({"id": binding.github_app_id})
        if argv[:2] == ["api", f"/repos/{binding.repo}/branches/coder"]:
            return json.dumps(
                {"name": "coder", "commit": {"sha": "deadbeef"}}
            )
        raise AssertionError(f"unexpected argv: {argv}")

    return _run


def _happy_keyring_getter():
    PEM = b"-----BEGIN RSA PRIVATE KEY-----\n<fake>\n-----END RSA PRIVATE KEY-----\n"
    SECRET = b"shhh-its-a-secret"

    def _get(slot):
        if "private-key" in slot:
            return PEM
        if "webhook-secret" in slot:
            return SECRET
        raise KeyError(slot)

    return _get


def test_doctor_all_checks_pass():
    binding = _valid_binding()
    result = doctor(
        binding,
        keyring_getter=_happy_keyring_getter(),
        gh_runner=_happy_gh_runner(binding),
    )
    assert isinstance(result, DoctorResult)
    assert result.green is True
    names = {c.name for c in result.checks}
    assert names == {
        "github_app_install",
        "private_key_decrypts",
        "webhook_signature_round_trip",
        "coder_branch_exists",
    }


def test_doctor_reports_wrong_app_id():
    binding = _valid_binding()

    def _mismatch(argv):
        if argv[:2] == ["api", "/app"]:
            return json.dumps({"id": 999999})
        return _happy_gh_runner(binding)(argv)

    result = doctor(
        binding,
        keyring_getter=_happy_keyring_getter(),
        gh_runner=_mismatch,
    )
    assert result.green is False
    failed = {c.name for c in result.failed()}
    assert "github_app_install" in failed


def test_doctor_reports_missing_coder_branch():
    binding = _valid_binding()

    def _no_branch(argv):
        if argv[:2] == ["api", "/app"]:
            return json.dumps({"id": binding.github_app_id})
        if "/branches/coder" in argv[1]:
            raise RuntimeError("HTTP 404")
        raise AssertionError(argv)

    result = doctor(
        binding,
        keyring_getter=_happy_keyring_getter(),
        gh_runner=_no_branch,
    )
    assert result.green is False
    failed_names = {c.name for c in result.failed()}
    assert "coder_branch_exists" in failed_names


def test_doctor_reports_empty_keyring_value():
    binding = _valid_binding()

    def _empty(slot):
        return b""

    result = doctor(
        binding,
        keyring_getter=_empty,
        gh_runner=_happy_gh_runner(binding),
    )
    assert result.green is False


def test_doctor_reports_non_pem_private_key():
    binding = _valid_binding()

    def _junk(slot):
        if "private-key" in slot:
            return b"not a pem"
        return b"secret"

    result = doctor(
        binding,
        keyring_getter=_junk,
        gh_runner=_happy_gh_runner(binding),
    )
    failed = {c.name for c in result.failed()}
    assert "private_key_decrypts" in failed


def test_doctor_aggregates_all_failures_not_short_circuit():
    """Doctor should report every failure, not short-circuit on the first."""
    binding = _valid_binding()

    def _broken_getter(slot):
        raise RuntimeError("keyring unavailable")

    def _broken_gh(argv):
        raise RuntimeError("network down")

    result = doctor(
        binding, keyring_getter=_broken_getter, gh_runner=_broken_gh
    )
    assert result.green is False
    assert len(result.failed()) == 4  # every check reports


# ---------------------------------------------------------------------------
# verify_webhook_signature — the §15.5 primitive
# ---------------------------------------------------------------------------


def test_verify_webhook_signature_round_trip():
    secret = b"hunter2"
    body = b'{"action":"opened"}'
    signature = (
        "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    )
    assert verify_webhook_signature(secret, body, signature) is True


def test_verify_webhook_signature_rejects_wrong_secret():
    body = b"x"
    signature = (
        "sha256=" + hmac.new(b"right", body, hashlib.sha256).hexdigest()
    )
    assert verify_webhook_signature(b"wrong", body, signature) is False


def test_verify_webhook_signature_rejects_missing_prefix():
    body = b"x"
    digest = hmac.new(b"s", body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(b"s", body, digest) is False  # no prefix


# ---------------------------------------------------------------------------
# agents_md_entry (§5.11 discoverability)
# ---------------------------------------------------------------------------


def test_agents_md_entry_mentions_repo_and_forbidden():
    binding = _valid_binding()
    entry = agents_md_entry(binding)
    assert "amd/gaia" in entry
    assert "release-*" in entry
    assert "`coder`" in entry
    assert "never `main`" in entry


def test_agents_md_entry_handles_empty_forbidden_paths():
    binding = RepoBinding(
        repo="x/y",
        github_app_id=1,
        github_installation_id=1,
        webhook_secret_keyring_slot="a/b",
        private_key_keyring_slot="c/d",
        allowed_branches=[],
        forbidden_paths=[],
    )
    entry = agents_md_entry(binding)
    assert "none configured" in entry
