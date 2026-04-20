# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for :mod:`gaia.coder.trust` (Phase 5, §4.2, §7.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gaia.coder import trust as trust_mod
from gaia.coder.stores import audit as audit_store


def test_em_config_roundtrip(tmp_path: Path) -> None:
    """Write em.toml, read it back, assert every field round-trips with type."""
    path = tmp_path / "em.toml"
    cfg = trust_mod.EMConfig(
        em_handle="kovtcharov-amd",
        em_channel="github-issue-comment",
        persona_name="Coda",
        dev_mode_self_edit=True,
        dev_mode_enabled_at="2026-05-02T14:23:00Z",
        dev_mode_enabled_reason="EM conversation on 2026-05-02",
        allow_state_machine_edit=False,
        auto_merge_classes=["prompt", "test", "doc"],
        current_tier=3,
    )

    trust_mod.save_em_config(path, cfg)
    assert path.exists()

    reloaded = trust_mod.load_em_config(path)

    # Every field exactly, so a drift in the writer or reader is caught.
    assert reloaded.em_handle == "kovtcharov-amd"
    assert reloaded.em_channel == "github-issue-comment"
    assert reloaded.persona_name == "Coda"
    assert reloaded.dev_mode_self_edit is True
    assert reloaded.dev_mode_enabled_at == "2026-05-02T14:23:00Z"
    assert reloaded.dev_mode_enabled_reason == "EM conversation on 2026-05-02"
    assert reloaded.allow_state_machine_edit is False
    assert reloaded.auto_merge_classes == ["prompt", "test", "doc"]
    assert reloaded.current_tier == 3


def test_em_config_missing_file_raises_trust_error(tmp_path: Path) -> None:
    """Loading a non-existent em.toml must raise TrustError (not bare ImportError)."""
    with pytest.raises(trust_mod.TrustError, match="EM config not found"):
        trust_mod.load_em_config(tmp_path / "nope.toml")


def test_em_config_rejects_empty_handle() -> None:
    """An empty em_handle bails at Pydantic validation (not silently accepted)."""
    with pytest.raises(Exception):  # pydantic.ValidationError subclass
        trust_mod.EMConfig(em_handle="", em_channel="cli")


def test_promote_requires_em_signature(tmp_path: Path) -> None:
    """promote() without a matching signature raises TrustError per §4.2."""
    cfg = trust_mod.EMConfig(em_handle="kovtcharov-amd", em_channel="cli")
    conn = audit_store.open_store(tmp_path / "audit.db")
    try:
        with pytest.raises(trust_mod.TrustError, match="signature does not match"):
            trust_mod.promote(cfg, 2, "test", "", audit_conn=conn)
        with pytest.raises(trust_mod.TrustError, match="signature does not match"):
            trust_mod.promote(cfg, 2, "test", "some-other-user", audit_conn=conn)
    finally:
        conn.close()


def test_promote_accepts_matching_signature(tmp_path: Path) -> None:
    cfg = trust_mod.EMConfig(em_handle="kovtcharov-amd", em_channel="cli")
    conn = audit_store.open_store(tmp_path / "audit.db")
    try:
        updated = trust_mod.promote(
            cfg, 3, "earned it", "kovtcharov-amd", audit_conn=conn
        )
    finally:
        conn.close()
    assert updated.current_tier == 3
    # Original cfg is untouched — promote returns a new EMConfig.
    assert cfg.current_tier == 0


def test_promote_rejects_missing_reason(tmp_path: Path) -> None:
    """§4.4 standup cadence wants a non-empty rationale."""
    cfg = trust_mod.EMConfig(em_handle="kovtcharov-amd", em_channel="cli")
    with pytest.raises(trust_mod.TrustError, match="reason is required"):
        trust_mod.promote(cfg, 2, "", "kovtcharov-amd")


def test_promote_out_of_range_tier() -> None:
    cfg = trust_mod.EMConfig(em_handle="kovtcharov-amd", em_channel="cli")
    with pytest.raises(trust_mod.TrustError, match="out of range"):
        trust_mod.promote(cfg, 6, "too high", "kovtcharov-amd")


def test_demote_is_immediate(tmp_path: Path) -> None:
    """§4.2: demotions require no justification and no signature."""
    cfg = trust_mod.EMConfig(
        em_handle="kovtcharov-amd", em_channel="cli", current_tier=3
    )
    conn = audit_store.open_store(tmp_path / "audit.db")
    try:
        # Empty reason and no signature are both acceptable for demote.
        updated = trust_mod.demote(cfg, "", audit_conn=conn)
    finally:
        conn.close()
    assert updated.current_tier == 2


def test_demote_explicit_target_must_be_lower() -> None:
    cfg = trust_mod.EMConfig(
        em_handle="kovtcharov-amd", em_channel="cli", current_tier=2
    )
    with pytest.raises(trust_mod.TrustError, match="must be strictly below"):
        trust_mod.demote(cfg, "oops", to_tier=3)


def test_tier_history_returns_chronological_events(tmp_path: Path) -> None:
    """tier_history() reconstructs promotions+demotions from the audit log."""
    cfg = trust_mod.EMConfig(em_handle="kovtcharov-amd", em_channel="cli")
    conn = audit_store.open_store(tmp_path / "audit.db")
    try:
        cfg = trust_mod.promote(cfg, 1, "start", "kovtcharov-amd", audit_conn=conn)
        cfg = trust_mod.promote(cfg, 3, "earned", "kovtcharov-amd", audit_conn=conn)
        cfg = trust_mod.demote(cfg, "burned out", audit_conn=conn)
        history = trust_mod.tier_history(conn)
    finally:
        conn.close()

    events = [(h["event"], h["from_tier"], h["to_tier"]) for h in history]
    assert events == [
        ("promote", 0, 1),
        ("promote", 1, 3),
        ("demote", 3, 2),
    ]


def test_capability_tier_labels_match_spec() -> None:
    """Labels are part of the CLI contract; a typo here breaks the §4.2 template."""
    assert trust_mod.CapabilityTier.TIER_0.label == "Read-only observer"
    assert trust_mod.CapabilityTier.TIER_1.label == "Drafter"
    assert trust_mod.CapabilityTier.TIER_2.label == "Branch author (PR-gated)"
    assert trust_mod.CapabilityTier.TIER_3.label == "Self-maintainer"
    assert trust_mod.CapabilityTier.TIER_4.label == "Self-coder"
    assert trust_mod.CapabilityTier.TIER_5.label == "Trusted integrator"


def test_repo_binding_loads_minimal_toml(tmp_path: Path) -> None:
    """§15.6 repo_binding.toml round-trips through RepoBinding()."""
    path = tmp_path / "repo_binding.toml"
    path.write_text(
        'repo = "amd/gaia"\n'
        "github_app_id = 123456\n"
        "github_installation_id = 7890123\n"
        'webhook_secret_keyring_slot = "gaia-coder/github-webhook-secret"\n'
        'private_key_keyring_slot = "gaia-coder/github-app-private-key"\n'
        'allowed_branches = ["auto/gaia-coder/*", "coder"]\n'
        'forbidden_paths = [".github/workflows/release-*"]\n'
        "future_field = 42\n",  # Tests that unknown keys are ignored.
    )
    rb = trust_mod.load_repo_binding(path)
    assert rb.repo == "amd/gaia"
    assert rb.github_app_id == 123456
    assert rb.allowed_branches == ["auto/gaia-coder/*", "coder"]


def test_repo_binding_rejects_bad_shape(tmp_path: Path) -> None:
    path = tmp_path / "rb.toml"
    path.write_text(
        'repo = "not-a-slash-separated-name"\n'
        "github_app_id = 1\n"
        "github_installation_id = 1\n"
        'webhook_secret_keyring_slot = "x"\n'
        'private_key_keyring_slot = "y"\n',
    )
    with pytest.raises(Exception):  # pydantic ValidationError
        trust_mod.load_repo_binding(path)
