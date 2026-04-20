# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for :mod:`gaia.coder.prompt_composer` (Phase 5, §3.2, §4.6, §6.5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gaia.coder import prompt_composer


def _seed_identity_root(root: Path) -> None:
    """Write stand-in identity docs so tests are independent of the shipped ones."""
    (root / "GAIA.md").write_text("# GAIA IDENTITY\nshe/her.\n", encoding="utf-8")
    (root / "ARCHITECTURE.md").write_text("# ARCH\ncurrent mixins.\n", encoding="utf-8")
    (root / "PROJECT_MAP.md").write_text(
        "# PROJECT\namd/gaia subsystems.\n", encoding="utf-8"
    )


def test_prompt_composer_injects_three_docs(tmp_path: Path) -> None:
    """Composer returns one cacheable block per identity doc, in order."""
    _seed_identity_root(tmp_path)
    ctx = prompt_composer.LoopContext(identity_root=tmp_path)
    blocks = prompt_composer.compose_system_prompt(ctx, matched_skills=[])

    assert len(blocks) == 3
    text_all = "\n\n".join(b["text"] for b in blocks)
    assert "GAIA IDENTITY" in text_all
    assert "ARCH" in text_all
    assert "PROJECT" in text_all

    # Order: GAIA.md → ARCHITECTURE.md → PROJECT_MAP.md per §3.2.
    assert "gaia_md" in blocks[0]["text"]
    assert "architecture_md" in blocks[1]["text"]
    assert "project_map_md" in blocks[2]["text"]


def test_prompt_composer_marks_cacheable(tmp_path: Path) -> None:
    """Every identity block carries ``cache_control=ephemeral`` for prompt caching."""
    _seed_identity_root(tmp_path)
    ctx = prompt_composer.LoopContext(identity_root=tmp_path)
    blocks = prompt_composer.compose_system_prompt(ctx)

    for b in blocks:
        assert b.get("cache_control") == {"type": "ephemeral"}


def test_prompt_composer_adds_skills_after_identity(tmp_path: Path) -> None:
    _seed_identity_root(tmp_path)
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "touching-release-scripts.md").write_text(
        "Rules for release scripts.", encoding="utf-8"
    )

    ctx = prompt_composer.LoopContext(identity_root=tmp_path)
    blocks = prompt_composer.compose_system_prompt(
        ctx, matched_skills=["touching-release-scripts"]
    )

    assert len(blocks) == 4
    assert "Rules for release scripts" in blocks[3]["text"]
    assert blocks[3]["cache_control"] == {"type": "ephemeral"}
    assert 'skill name="touching-release-scripts"' in blocks[3]["text"]


def test_prompt_composer_extra_suffix_is_uncached(tmp_path: Path) -> None:
    """Per-turn facts append as a non-cacheable tail block."""
    _seed_identity_root(tmp_path)
    ctx = prompt_composer.LoopContext(
        identity_root=tmp_path,
        extra_suffix="Current tier: 3. Dev mode: ON.",
    )
    blocks = prompt_composer.compose_system_prompt(ctx)

    tail = blocks[-1]
    assert tail["text"] == "Current tier: 3. Dev mode: ON."
    assert "cache_control" not in tail


def test_prompt_composer_missing_doc_raises(tmp_path: Path) -> None:
    """Missing identity docs are surfaced, not silently skipped."""
    # Only two of three docs present.
    (tmp_path / "GAIA.md").write_text("g", encoding="utf-8")
    (tmp_path / "ARCHITECTURE.md").write_text("a", encoding="utf-8")

    ctx = prompt_composer.LoopContext(identity_root=tmp_path)
    with pytest.raises(FileNotFoundError, match="PROJECT_MAP.md"):
        prompt_composer.compose_system_prompt(ctx)


def test_prompt_composer_default_root_ships_identity_docs() -> None:
    """With no ``identity_root`` override the shipped GAIA.md is loaded.

    This is an integration-style check — it asserts the package actually
    ships the three docs (they are load-bearing and a missing file should
    break ``compose_system_prompt()`` very loudly).
    """
    blocks = prompt_composer.compose_system_prompt(prompt_composer.LoopContext())
    text_all = "\n\n".join(b["text"] for b in blocks)
    assert "GAIA.md" in text_all
    assert "ARCHITECTURE.md" in text_all
    assert "PROJECT_MAP.md" in text_all


def test_introspect_system_prompt_returns_single_string(tmp_path: Path) -> None:
    _seed_identity_root(tmp_path)
    ctx = prompt_composer.LoopContext(identity_root=tmp_path)
    text = prompt_composer.introspect_system_prompt(ctx)
    assert isinstance(text, str)
    assert "GAIA IDENTITY" in text
    assert "PROJECT" in text
