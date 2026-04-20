# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Regression tests for the #827 + #828 auto-review fixes.

Each test corresponds to one of the six fixes in the follow-up PR:
1. Path traversal in import_with_attribution (#827 critical)
2. License filter silent-drop (#827 important)
3. gh_pr_merge hardcoded --admin (#827 important)
4. Webhook signature round-trip discrimination (#827 important)
5. add_instrumented_trace logger.debug NameError (#828 important — also in test_debug_tools)
6. diff_behavior HEAD restoration (#828 important — also in test_debug_tools)
"""

from __future__ import annotations

import pytest

from gaia.coder import oss_reuse


def test_import_with_attribution_rejects_absolute_dest_path(monkeypatch, tmp_path):
    """Absolute dest_path must NOT escape repo_root. Cf. #827 critical."""
    # Set up licensing bypasses so we get to the path-traversal check.
    monkeypatch.setattr(oss_reuse, "_lookup_repo_license", lambda _: "MIT")
    monkeypatch.setattr(oss_reuse, "_fetch_raw", lambda _: "content")
    monkeypatch.setattr(oss_reuse, "_append_notices_entry", lambda **_: True)

    mixin = oss_reuse.OSSReuseMixin()
    mixin.register_oss_reuse_tools()
    # Reach the registered tool via the module-level registry.
    from gaia.agents.base.tools import get_tool_metadata

    tool_meta = get_tool_metadata("import_with_attribution")
    assert tool_meta is not None
    tool_fn = tool_meta["function"]

    with pytest.raises(oss_reuse.AttributionError, match="escapes repo_root"):
        tool_fn(
            source_url=f"https://github.com/octocat/Hello-World/blob/{'a' * 40}/file.py",
            commit_sha="a" * 40,
            dest_path="/etc/passwd",
            attribution_note="note",
            repo_root=str(tmp_path),
        )


def test_import_with_attribution_rejects_dotdot_traversal(monkeypatch, tmp_path):
    """``../`` traversal must be rejected. Cf. #827 critical."""
    monkeypatch.setattr(oss_reuse, "_lookup_repo_license", lambda _: "MIT")
    monkeypatch.setattr(oss_reuse, "_fetch_raw", lambda _: "content")
    monkeypatch.setattr(oss_reuse, "_append_notices_entry", lambda **_: True)

    mixin = oss_reuse.OSSReuseMixin()
    mixin.register_oss_reuse_tools()
    from gaia.agents.base.tools import get_tool_metadata

    tool_fn = get_tool_metadata("import_with_attribution")["function"]

    with pytest.raises(oss_reuse.AttributionError, match="escapes repo_root"):
        tool_fn(
            source_url=f"https://github.com/octocat/Hello-World/blob/{'a' * 40}/file.py",
            commit_sha="a" * 40,
            dest_path="../../../etc/passwd",
            attribution_note="note",
            repo_root=str(tmp_path),
        )


def test_license_filter_rejects_unknown_spdx_instead_of_silent_drop():
    """Unknown SPDX ids must raise, not silently drop. Cf. #827 important."""
    with pytest.raises(ValueError, match="Unknown SPDX"):
        oss_reuse._validate_license_filter(["MIT", "MIT-License"])  # typo


def test_license_filter_still_accepts_known_permissive_subset():
    """Happy path: subset of permissive returns exactly that subset."""
    assert oss_reuse._validate_license_filter(["MIT", "Apache-2.0"]) == frozenset(
        {"MIT", "Apache-2.0"}
    )


def test_license_filter_rejects_blocked():
    """Blocked licenses still raise LicenseIncompatibleError."""
    with pytest.raises(oss_reuse.LicenseIncompatibleError):
        oss_reuse._validate_license_filter(["MIT", "GPL-3.0"])


def test_gh_pr_merge_no_admin_by_default(monkeypatch):
    """gh_pr_merge must NOT pass --admin unless admin_override=True. Cf. #827."""
    from gaia.coder.tools import github as gh_mod

    captured = []
    monkeypatch.setattr(gh_mod, "_run_gh", lambda argv: captured.append(argv) or "")

    mixin = gh_mod.GitHubToolsMixin()
    mixin.register_github_tools()
    from gaia.agents.base.tools import get_tool_metadata

    tool_fn = get_tool_metadata("gh_pr_merge")["function"]
    tool_fn(number=123, method="squash")
    assert "--admin" not in captured[0]


def test_gh_pr_merge_admin_override_explicit(monkeypatch):
    """admin_override=True does add --admin."""
    from gaia.coder.tools import github as gh_mod

    captured = []
    monkeypatch.setattr(gh_mod, "_run_gh", lambda argv: captured.append(argv) or "")

    mixin = gh_mod.GitHubToolsMixin()
    mixin.register_github_tools()
    from gaia.agents.base.tools import get_tool_metadata

    tool_fn = get_tool_metadata("gh_pr_merge")["function"]
    tool_fn(number=123, method="squash", admin_override=True)
    assert "--admin" in captured[0]
