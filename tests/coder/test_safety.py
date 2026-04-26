# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for :mod:`gaia.coder.safety` — the trust-contract enforcement seam.

The eight cases below cover every gate in :func:`enforce_action`:

* tier check (deny + allow paths),
* dev-mode gate for self-edit (deny when off, allow when on),
* repo-binding ``forbidden_paths`` (deny on glob match),
* repo-binding ``allowed_branches`` (deny + allow),
* license gate (block GPL, allow MIT),
* unknown action (deny conservatively).

Every test injects ``em_config_path`` and ``repo_binding_path`` so production
state under ``~/.gaia/coder/`` is never consulted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gaia.coder.safety import (
    ActionContext,
    ActionDenied,
    enforce_action,
)

# ---------------------------------------------------------------------------
# Helpers — write minimal, valid em.toml / repo_binding.toml under tmp_path
# ---------------------------------------------------------------------------


def _write_em(
    tmp: Path,
    *,
    tier: int,
    dev_mode_self_edit: bool = False,
) -> Path:
    """Write a tier-N ``em.toml`` and return its path."""
    p = tmp / "em.toml"
    p.write_text(
        'em_handle = "test-em"\n'
        'em_channel = "github-issue-comment"\n'
        f"dev_mode_self_edit = {'true' if dev_mode_self_edit else 'false'}\n"
        "allow_state_machine_edit = false\n"
        "auto_merge_classes = []\n"
        f"current_tier = {tier}\n",
        encoding="utf-8",
    )
    return p


def _write_binding(
    tmp: Path,
    *,
    forbidden: list[str] | None = None,
    allowed_branches: list[str] | None = None,
) -> Path:
    """Write a minimal valid ``repo_binding.toml`` and return its path."""
    p = tmp / "repo_binding.toml"
    forb = forbidden or []
    allowed = allowed_branches or []
    forb_str = ", ".join(f'"{x}"' for x in forb)
    allowed_str = ", ".join(f'"{x}"' for x in allowed)
    p.write_text(
        'repo = "amd/gaia"\n'
        "github_app_id = 1\n"
        "github_installation_id = 1\n"
        'webhook_secret_keyring_slot = "slot-a"\n'
        'private_key_keyring_slot = "slot-b"\n'
        f"allowed_branches = [{allowed_str}]\n"
        f"forbidden_paths = [{forb_str}]\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture()
def safe_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Tmp dir + a stub ``dev_mode.is_enabled`` that defaults to ``False``.

    Individual tests override the dev-mode return value as needed by patching
    ``gaia.coder.safety.dev_mode_mod.is_enabled``.
    """
    monkeypatch.setattr(
        "gaia.coder.safety.dev_mode_mod.is_enabled",
        lambda **kw: False,
    )
    return tmp_path


# ---------------------------------------------------------------------------
# 1. Tier gate
# ---------------------------------------------------------------------------


class TestTierGate:
    def test_tier_0_denies_write_file(self, safe_tmp: Path) -> None:
        em = _write_em(safe_tmp, tier=0)
        with pytest.raises(ActionDenied, match="requires tier"):
            enforce_action(
                ActionContext(action="write_file", paths=("notes.txt",)),
                em_config_path=em,
            )

    def test_tier_2_allows_write_file_outside_self(self, safe_tmp: Path) -> None:
        em = _write_em(safe_tmp, tier=2)
        # No self-edit path; no binding present → only the tier gate runs.
        enforce_action(
            ActionContext(action="write_file", paths=("docs/notes.md",)),
            em_config_path=em,
            repo_binding_path=safe_tmp / "no-such-binding.toml",
        )

    def test_unknown_action_denied(self, safe_tmp: Path) -> None:
        em = _write_em(safe_tmp, tier=5)
        with pytest.raises(ActionDenied, match="not registered"):
            enforce_action(
                ActionContext(action="self_destruct"),
                em_config_path=em,
            )


# ---------------------------------------------------------------------------
# 2. Dev-mode gate
# ---------------------------------------------------------------------------


class TestDevModeGate:
    def test_self_edit_denied_when_dev_mode_off(self, safe_tmp: Path) -> None:
        em = _write_em(safe_tmp, tier=4)  # tier 4 is "self-coder" per §4.2
        # safe_tmp fixture already stubs is_enabled → False.
        with pytest.raises(ActionDenied, match="dev mode is OFF"):
            enforce_action(
                ActionContext(
                    action="edit_file",
                    paths=("src/gaia/coder/foo.py",),
                ),
                em_config_path=em,
            )

    def test_self_edit_allowed_when_dev_mode_on(
        self, safe_tmp: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        em = _write_em(safe_tmp, tier=4, dev_mode_self_edit=True)
        monkeypatch.setattr(
            "gaia.coder.safety.dev_mode_mod.is_enabled",
            lambda **kw: True,
        )
        # Should not raise.
        enforce_action(
            ActionContext(
                action="edit_file",
                paths=("src/gaia/coder/foo.py",),
            ),
            em_config_path=em,
        )


# ---------------------------------------------------------------------------
# 3 & 4. Repo binding gates
# ---------------------------------------------------------------------------


class TestRepoBindingGate:
    def test_forbidden_path_blocks_write(self, safe_tmp: Path) -> None:
        em = _write_em(safe_tmp, tier=2)
        binding = _write_binding(
            safe_tmp,
            forbidden=[".github/workflows/release-*"],
        )
        with pytest.raises(ActionDenied, match="forbidden_paths"):
            enforce_action(
                ActionContext(
                    action="write_file",
                    paths=(".github/workflows/release-deploy.yml",),
                ),
                em_config_path=em,
                repo_binding_path=binding,
            )

    def test_disallowed_branch_blocks_open_pr(self, safe_tmp: Path) -> None:
        em = _write_em(safe_tmp, tier=2)
        binding = _write_binding(
            safe_tmp,
            allowed_branches=["auto/gaia-coder/*", "coder"],
        )
        with pytest.raises(ActionDenied, match="allowed_branches"):
            enforce_action(
                ActionContext(action="open_pr", branch="experiment/yolo"),
                em_config_path=em,
                repo_binding_path=binding,
            )

    def test_allowed_branch_passes_open_pr(self, safe_tmp: Path) -> None:
        em = _write_em(safe_tmp, tier=2)
        binding = _write_binding(
            safe_tmp,
            allowed_branches=["auto/gaia-coder/*", "coder"],
        )
        # Should not raise — glob match on auto/gaia-coder/*.
        enforce_action(
            ActionContext(action="open_pr", branch="auto/gaia-coder/fb-77"),
            em_config_path=em,
            repo_binding_path=binding,
        )


# ---------------------------------------------------------------------------
# 5. License gate
# ---------------------------------------------------------------------------


class TestLicenseGate:
    def test_gpl_blocked(self, safe_tmp: Path) -> None:
        em = _write_em(safe_tmp, tier=2)
        with pytest.raises(ActionDenied, match="blocked list"):
            enforce_action(
                ActionContext(
                    action="write_file",
                    paths=("vendor/foo.py",),
                    license_text="GPL-3.0",
                ),
                em_config_path=em,
            )

    def test_mit_allowed(self, safe_tmp: Path) -> None:
        em = _write_em(safe_tmp, tier=2)
        # Should not raise — MIT is on the permissive allowlist.
        enforce_action(
            ActionContext(
                action="write_file",
                paths=("vendor/foo.py",),
                license_text="MIT",
            ),
            em_config_path=em,
        )

    def test_unknown_license_denied(self, safe_tmp: Path) -> None:
        em = _write_em(safe_tmp, tier=2)
        with pytest.raises(ActionDenied, match="neither permissive nor"):
            enforce_action(
                ActionContext(
                    action="write_file",
                    paths=("vendor/foo.py",),
                    license_text="WTFPL",
                ),
                em_config_path=em,
            )


# ---------------------------------------------------------------------------
# 6. Defaults — em.toml absent ⇒ tier 0
# ---------------------------------------------------------------------------


class TestDefaults:
    def test_missing_em_toml_defaults_to_tier_0(self, safe_tmp: Path) -> None:
        # No em.toml at all — read should still pass (tier 0+).
        enforce_action(
            ActionContext(action="read_file", paths=("README.md",)),
            em_config_path=safe_tmp / "absent.toml",
        )
        # …but write should fail.
        with pytest.raises(ActionDenied, match="requires tier"):
            enforce_action(
                ActionContext(action="write_file", paths=("README.md",)),
                em_config_path=safe_tmp / "absent.toml",
            )
