# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for SystemDiscovery — local system scanner for day-zero bootstrap.

Tests cover: _classify_remote (URL hostname safety), _classify_path,
_classify_domain, _extract_domain, scan_all returns expected structure,
and the platform guards for Windows-only methods.

All tests are stdlib-only — no real filesystem scanning performed.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from gaia.agents.base.discovery import (
    SystemDiscovery,
    _classify_domain,
    _classify_path,
    _classify_remote,
)

# ---------------------------------------------------------------------------
# _classify_remote — URL hostname-based classification
# ---------------------------------------------------------------------------


class TestClassifyRemote:
    """_classify_remote() must use urlparse for hostname matching (no substring spoofing)."""

    def test_amd_org_is_work(self):
        assert _classify_remote("https://github.com/amd/gaia") == "work"

    def test_microsoft_org_is_work(self):
        assert _classify_remote("https://github.com/microsoft/vscode") == "work"

    def test_personal_github_is_unclassified(self):
        assert _classify_remote("https://github.com/alice/my-repo") == "unclassified"

    def test_ssh_github_is_unclassified(self):
        assert _classify_remote("git@github.com:alice/repo.git") == "unclassified"

    def test_github_in_path_does_not_spoof_github_hostname(self):
        # A URL with "github.com" in the PATH but a different hostname must NOT
        # be classified as a personal GitHub repo — this was the CodeQL CWE-20
        # vulnerability fixed by using urlparse().hostname instead of substring.
        # (The /amd/ org pattern still applies, so "work" is expected here, but
        # crucially it must NOT match the github.com hostname branch.)
        result = _classify_remote("https://evil.example.com/github.com/personal/repo")
        # evil.example.com hostname → not github.com → unclassified (no org match)
        assert result == "unclassified"

    def test_github_in_query_does_not_spoof(self):
        result = _classify_remote("https://evil.com/redirect?to=github.com")
        assert result == "unclassified"

    def test_amd_in_path_but_not_org_is_unclassified(self):
        # /amd/ as path segment on a personal repo should still match work
        # (conservative classification is fine; it's only a label hint)
        result = _classify_remote("https://github.com/alice/amd-configs")
        # Path does NOT contain /amd/ (it's /alice/amd-configs), so unclassified
        assert result == "unclassified"

    def test_empty_url_is_unclassified(self):
        assert _classify_remote("") == "unclassified"

    def test_malformed_url_is_unclassified(self):
        assert _classify_remote("not-a-url") == "unclassified"


# ---------------------------------------------------------------------------
# _classify_path — path-based context classification
# ---------------------------------------------------------------------------


class TestClassifyPath:
    def test_work_in_parts(self):
        assert _classify_path(Path("/home/user/work/project")) == "work"

    def test_projects_in_parts(self):
        assert _classify_path(Path("/home/user/projects/foo")) == "work"

    def test_personal_in_parts(self):
        assert _classify_path(Path("/home/user/personal/diary")) == "personal"

    def test_documents_is_unclassified(self):
        assert _classify_path(Path("/home/user/documents/notes")) == "unclassified"

    def test_unknown_path_is_unclassified(self):
        assert _classify_path(Path("/tmp/random/stuff")) == "unclassified"


# ---------------------------------------------------------------------------
# _classify_domain — email/URL domain classification
# ---------------------------------------------------------------------------


class TestClassifyDomain:
    def test_facebook_is_personal(self):
        assert _classify_domain("facebook.com") == "personal"

    def test_reddit_is_personal(self):
        assert _classify_domain("reddit.com") == "personal"

    def test_github_is_work(self):
        assert _classify_domain("github.com") == "work"

    def test_unknown_domain_is_unclassified(self):
        assert _classify_domain("somecompany.internal") == "unclassified"

    def test_case_insensitive(self):
        assert _classify_domain("REDDIT.COM") == "personal"


# ---------------------------------------------------------------------------
# SystemDiscovery.scan_all — structure check (no real scanning)
# ---------------------------------------------------------------------------


class TestSystemDiscoveryScanAll:
    """scan_all() must return a dict with expected keys and list values."""

    def test_scan_all_returns_dict_of_lists(self):
        # Patch all individual scan methods to return empty lists to avoid
        # touching the real filesystem during unit tests.
        disc = SystemDiscovery()
        with (
            patch.object(disc, "scan_file_system", return_value=[]),
            patch.object(disc, "scan_git_repos", return_value=[]),
            patch.object(disc, "scan_installed_apps", return_value=[]),
            patch.object(disc, "scan_browser_bookmarks", return_value=[]),
            patch.object(disc, "scan_email_accounts", return_value=[]),
        ):
            result = disc.scan_all()
        assert isinstance(result, dict)
        for key, val in result.items():
            assert isinstance(val, list), f"scan_all[{key!r}] should be a list"

    def test_scan_all_does_not_raise_on_empty_results(self):
        disc = SystemDiscovery()
        with (
            patch.object(disc, "scan_file_system", return_value=[]),
            patch.object(disc, "scan_git_repos", return_value=[]),
            patch.object(disc, "scan_installed_apps", return_value=[]),
            patch.object(disc, "scan_browser_bookmarks", return_value=[]),
            patch.object(disc, "scan_email_accounts", return_value=[]),
        ):
            result = disc.scan_all()
        assert isinstance(result, dict)

    def test_individual_scanner_exception_is_swallowed(self):
        """A failing scanner must not propagate — scan_all catches all errors."""
        disc = SystemDiscovery()
        with (
            patch.object(
                disc, "scan_file_system", side_effect=RuntimeError("disk error")
            ),
            patch.object(disc, "scan_git_repos", return_value=[]),
            patch.object(disc, "scan_installed_apps", return_value=[]),
            patch.object(disc, "scan_browser_bookmarks", return_value=[]),
            patch.object(disc, "scan_email_accounts", return_value=[]),
        ):
            # Should not raise
            result = disc.scan_all()
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Windows-only guard — scan_installed_apps returns [] on non-Windows
# ---------------------------------------------------------------------------


class TestWindowsOnlyGuard:
    @pytest.mark.skipif(sys.platform == "win32", reason="Non-Windows only")
    def test_scan_installed_apps_returns_empty_on_non_windows(self):
        disc = SystemDiscovery()
        result = disc.scan_installed_apps()
        assert result == [], "scan_installed_apps must return [] on non-Windows"
