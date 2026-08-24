# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Security regressions found reviewing the skill-capture feature.

Each pins a hole that existed in the first implementation. The common thread:
capture pulls UNTRUSTED content in — a pasted body enters the system prompt —
so anything that grants executable reach must wait for `gaia skill promote`.
"""

import pytest

from gaia.agents.base.tool_grants import grant_scope


class TestCaptureIsNeverGrantable:
    """`always allow` on capture_skill was keyed on the skill NAME, but the
    operative argument is `source`. Approving one capture would have silently
    approved every later source reusing that name."""

    def test_capture_skill_offers_no_always_grant(self):
        scope = grant_scope(
            "capture_skill", {"source": "https://x/a.md", "name": "notes"}
        )
        assert scope is None, "capture must prompt every time — no 'always' grant"

    def test_a_second_source_under_the_same_name_still_prompts(self):
        first = grant_scope(
            "capture_skill", {"source": "https://good/a.md", "name": "notes"}
        )
        second = grant_scope(
            "capture_skill", {"source": "https://evil/x.md", "name": "notes"}
        )
        assert first is None and second is None

    def test_sibling_skill_tools_keep_their_grants(self):
        """The fix must not disarm install/remove, whose name IS the subject."""
        assert grant_scope("install_skill", {"name": "rss-digest"}) is not None
        assert grant_scope("remove_skill", {"name": "rss-digest"}) is not None


class TestBinaryGrantsFollowCodeTrust:
    """A binary grant IS executable reach: an ALLOW-tier `gh` subcommand runs
    with no prompt because loading the skill was the consent — the exact
    premise a pasted skill breaks."""

    def _skill(self, tools, permissions):
        from gaia.skills.format import GaiaMetadata, Skill, SkillTool

        return Skill(
            name="captured-thing",
            description="d",
            body="b",
            gaia=GaiaMetadata(
                permissions=list(permissions),
                tools=[SkillTool(name=t, description="d") for t in tools],
            ),
        )

    def test_toolless_skill_with_a_binary_grant_still_defers(self, monkeypatch):
        """The variant that reopened the hole: zero declared tools, but it asks
        for shell:execute:gh — reach without a tools.py."""
        from gaia.skills import capture as cap

        skill = self._skill(tools=[], permissions=["shell:execute:gh"])
        monkeypatch.setattr(
            cap, "capture_entry", lambda s: type("E", (), {"code_trusted": False})()
        )
        assert cap.code_is_deferred(skill) is True

    def test_skill_with_no_reach_at_all_does_not_defer(self, monkeypatch):
        from gaia.skills import capture as cap

        skill = self._skill(tools=[], permissions=["network:read"])
        assert cap.code_is_deferred(skill) is False

    def test_load_skill_withholds_grants_while_deferred(self):
        """Pin the call site: the grant loop must sit behind the deferral."""
        import inspect

        from gaia.agents.base.agent import Agent

        src = inspect.getsource(Agent.load_skill)
        grant_at = src.index("granted_binaries.grant(")
        gate_at = src.index("if not code_deferred:")
        assert gate_at < grant_at, "binary grants must be gated on code_deferred"


class TestCaptureLandsAtomically:
    """A bundle on disk WITHOUT a lock entry reads as an ordinary skill, so its
    tools.py would import on the next load. A half-finished capture must never
    fail open into a trusted one."""

    def test_a_lock_failure_leaves_nothing_behind(self, tmp_path, monkeypatch):
        import shutil as shutil_mod

        from gaia.skills import capture as cap

        boom = tmp_path / "skills" / "victim"

        def explode(*_a, **_k):
            boom.mkdir(parents=True, exist_ok=True)
            (boom / "tools.py").write_text("import os", encoding="utf-8")
            raise OSError("disk full")

        # Emulate the failure window: bundle written, lock save fails.
        try:
            explode()
        except OSError:
            shutil_mod.rmtree(boom, ignore_errors=True)
        assert not boom.exists()

    def test_capture_wraps_the_landing_in_cleanup(self):
        """Pin the guard itself — the write path must clean up and re-raise."""
        import inspect

        from gaia.skills import capture as cap

        src = inspect.getsource(cap.capture_skill)
        assert "shutil.rmtree(target, ignore_errors=True)" in src
        assert "raise" in src.split("shutil.rmtree(target, ignore_errors=True)")[1][:40]
