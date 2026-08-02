# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for SystemDiscovery — local system scanner for day-zero bootstrap.

Tests cover: _classify_remote (URL hostname safety), _classify_path,
_classify_domain, _extract_domain, scan_all returns expected structure,
per-platform scanner dispatch (Windows / macOS / Linux), the macOS and Linux
branches for apps / bookmarks / history / email, the unsupported-platform
report, cold-state behavior, the Keychain call contract, _classify_project,
and the scan_personal_files scanner.

All tests are stdlib-only and hermetic — every scan is rooted at a temp home,
system application directories are patched out, and no subprocess is executed.
"""

import contextlib
import json
import logging
import os
import plistlib
import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.discovery import (
    SystemDiscovery,
    _classify_domain,
    _classify_path,
    _classify_project,
    _classify_remote,
    unsupported_reason,
)

DISCOVERY_LOGGER = "gaia.agents.base.discovery"

# chmod(0o000) does not deny the owner on Windows, nor root anywhere, so the
# permission-denial tests cannot create the state they assert on.
_CHMOD_DENIES = os.name != "nt" and not (hasattr(os, "geteuid") and os.geteuid() == 0)
requires_chmod_denial = pytest.mark.skipif(
    not _CHMOD_DENIES, reason="chmod cannot deny the current user (Windows or root)"
)


@pytest.fixture
def isolated_disc(tmp_path):
    """SystemDiscovery rooted at an empty temp home, with no system app dirs.

    Keeps every scan inside tmp_path so a test never reads the developer's real
    /Applications, browser profiles, registry, or mail configuration — including
    when the suite is run ON Windows, where the win32 parametrizations would
    otherwise hit the live registry and Start Menu.
    """
    disc = SystemDiscovery()
    disc._home = tmp_path
    with (
        patch("gaia.agents.base.discovery._MACOS_APP_DIRS", ()),
        patch("gaia.agents.base.discovery._LINUX_DESKTOP_DIRS", ()),
        patch("gaia.agents.base.discovery.winreg", None),
        patch.dict(os.environ, {"PROGRAMDATA": str(tmp_path)}),
    ):
        yield disc


def _write_chromium_bookmarks(profile_dir: Path, urls: list) -> None:
    """Write a minimal Chromium Bookmarks JSON file containing `urls`."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "Bookmarks").write_text(
        json.dumps(
            {
                "roots": {
                    "bookmark_bar": {
                        "type": "folder",
                        "children": [{"type": "url", "url": u} for u in urls],
                    }
                }
            }
        ),
        encoding="utf-8",
    )


def _write_places_sqlite(profile_dir: Path, url: str, visit_count: int = 7) -> None:
    """Write a minimal Firefox places.sqlite holding one bookmark and one visit."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(profile_dir / "places.sqlite"))
    try:
        conn.execute(
            "CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url TEXT, "
            "visit_count INTEGER, last_visit_date INTEGER)"
        )
        conn.execute(
            "CREATE TABLE moz_bookmarks (id INTEGER PRIMARY KEY, fk INTEGER, title TEXT)"
        )
        conn.execute(
            "INSERT INTO moz_places VALUES (1, ?, ?, ?)",
            (url, visit_count, int(time.time() * 1_000_000)),
        )
        conn.execute("INSERT INTO moz_bookmarks VALUES (1, 1, 'A bookmark')")
        conn.commit()
    finally:
        conn.close()


def _write_thunderbird_prefs(profile_dir: Path, email: str) -> None:
    """Write a minimal Thunderbird prefs.js declaring one mail identity."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "prefs.js").write_text(
        f'user_pref("mail.identity.id1.useremail", "{email}");\n', encoding="utf-8"
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
# Platform dispatch — each scanner routes to the branch for the running OS
#
# Replaces the pre-#1956 TestWindowsOnlyGuard, which asserted
# scan_installed_apps() == [] off Windows. macOS and Linux now have real
# branches, so that assertion is false; what must hold instead is that each
# platform reaches its own branch, and that a platform with no branch is
# reported rather than silently empty.
# ---------------------------------------------------------------------------


class TestPlatformBranchDispatch:
    """Each of the four platform-aware scanners dispatches by sys.platform."""

    @pytest.mark.parametrize(
        "platform,branch",
        [
            ("win32", "_scan_windows_installed_apps"),
            ("darwin", "_scan_macos_installed_apps"),
            ("linux", "_scan_linux_installed_apps"),
        ],
    )
    def test_installed_apps_dispatches_to_platform_branch(self, platform, branch):
        disc = SystemDiscovery()
        sentinel = [{"content": "sentinel"}]
        with (
            patch("sys.platform", platform),
            patch.object(disc, branch, return_value=sentinel) as mock_branch,
        ):
            assert disc.scan_installed_apps() == sentinel
        mock_branch.assert_called_once_with()

    def test_installed_apps_linux2_is_treated_as_linux(self):
        """Legacy sys.platform values ('linux2') must still reach the branch."""
        disc = SystemDiscovery()
        with (
            patch("sys.platform", "linux2"),
            patch.object(
                disc, "_scan_linux_installed_apps", return_value=[]
            ) as mock_branch,
        ):
            disc.scan_installed_apps()
        mock_branch.assert_called_once_with()

    def test_safari_only_scanned_on_darwin(self, isolated_disc):
        for platform, expected in (
            ("darwin", True),
            ("linux", False),
            ("win32", False),
        ):
            with (
                patch("sys.platform", platform),
                patch.object(isolated_disc, "_extract_safari_bookmarks") as mock_book,
                patch.object(isolated_disc, "_extract_safari_history") as mock_hist,
            ):
                isolated_disc.scan_browser_bookmarks()
                isolated_disc.scan_browser_history()
            assert mock_book.called is expected, f"bookmarks on {platform}"
            assert mock_hist.called is expected, f"history on {platform}"

    @pytest.mark.parametrize(
        "platform,expected_calls,unexpected_calls",
        [
            (
                "win32",
                ["_scan_credential_manager", "_scan_outlook_registry"],
                ["_scan_macos_keychain", "_scan_apple_mail", "_scan_evolution"],
            ),
            (
                "darwin",
                ["_scan_macos_keychain", "_scan_apple_mail"],
                [
                    "_scan_credential_manager",
                    "_scan_outlook_registry",
                    "_scan_evolution",
                ],
            ),
            (
                "linux",
                ["_scan_evolution"],
                [
                    "_scan_credential_manager",
                    "_scan_outlook_registry",
                    "_scan_macos_keychain",
                    "_scan_apple_mail",
                ],
            ),
        ],
    )
    def test_email_dispatches_to_platform_branches(
        self, isolated_disc, platform, expected_calls, unexpected_calls
    ):
        names = expected_calls + unexpected_calls + ["_scan_thunderbird"]
        with contextlib.ExitStack() as stack:
            started = {
                name: stack.enter_context(patch.object(isolated_disc, name))
                for name in names
            }
            stack.enter_context(patch("sys.platform", platform))
            isolated_disc.scan_email_accounts()

        for name in expected_calls:
            assert started[name].called, f"{name} should run on {platform}"
        for name in unexpected_calls:
            assert not started[name].called, f"{name} must not run on {platform}"
        assert started["_scan_thunderbird"].called, "Thunderbird runs on all platforms"

    def test_real_platform_scanners_do_not_raise(self, isolated_disc):
        """The unpatched host platform must complete every scanner."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=44, stdout="", stderr="")
            for scan in (
                isolated_disc.scan_installed_apps,
                isolated_disc.scan_browser_bookmarks,
                isolated_disc.scan_browser_history,
                isolated_disc.scan_email_accounts,
            ):
                assert isinstance(scan(), list)


# ---------------------------------------------------------------------------
# _classify_project — project marker based classification
# ---------------------------------------------------------------------------


class TestClassifyProject:
    """_classify_project() should detect project types from marker files."""

    def test_python_package_from_pyproject(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'foo'\n")
        result = _classify_project(tmp_path, ["Python"])
        assert result == "Python package"

    def test_node_project_from_package_json(self, tmp_path):
        (tmp_path / "package.json").write_text('{"name": "myapp"}')
        result = _classify_project(tmp_path, ["JavaScript"])
        assert result == "Node.js project"

    def test_dockerfile_detected(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM python:3.11")
        result = _classify_project(tmp_path, [])
        assert result == "containerized app"

    def test_rust_from_cargo(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "mylib"\n')
        result = _classify_project(tmp_path, ["Rust"])
        assert result == "Rust project"

    def test_go_from_gomod(self, tmp_path):
        (tmp_path / "go.mod").write_text("module example.com/myapp\n")
        result = _classify_project(tmp_path, ["Go"])
        assert result == "Go module"

    def test_fallback_to_language(self, tmp_path):
        # No marker files, just languages
        result = _classify_project(tmp_path, ["TypeScript"])
        assert result == "TypeScript codebase"

    def test_empty_project(self, tmp_path):
        result = _classify_project(tmp_path, [])
        assert result == ""

    def test_permission_error_returns_language(self, tmp_path):
        # If scandir raises, should fall back to language
        with patch("os.scandir", side_effect=PermissionError("denied")):
            result = _classify_project(tmp_path, ["Python"])
        assert result == "Python codebase"


# ---------------------------------------------------------------------------
# scan_personal_files — personal file scanner
# ---------------------------------------------------------------------------


class TestScanPersonalFiles:
    """Tests for SystemDiscovery.scan_personal_files()."""

    def test_returns_list(self):
        """scan_personal_files must always return a list."""
        disc = SystemDiscovery()
        # Patch _home to an empty temp dir to avoid scanning the real home
        with patch.object(disc, "_home", Path("/nonexistent/path")):
            result = disc.scan_personal_files()
        assert isinstance(result, list)

    def test_finds_resume_files(self, tmp_path):
        """Should detect resume/CV files in Documents."""
        docs = tmp_path / "Documents"
        docs.mkdir()
        (docs / "Resume_2025.pdf").write_bytes(b"fake pdf")
        (docs / "Cover_Letter.docx").write_bytes(b"fake docx")
        (docs / "random_notes.txt").write_bytes(b"not a resume")

        disc = SystemDiscovery()
        disc._home = tmp_path
        result = disc.scan_personal_files()

        resume_facts = [r for r in result if "resume" in r.get("content", "").lower()]
        assert len(resume_facts) == 1
        assert resume_facts[0]["sensitive"] is True
        assert "Resume_2025.pdf" in resume_facts[0]["content"]

    def test_finds_config_files(self, tmp_path):
        """Should detect dotfiles and config files."""
        (tmp_path / ".gitconfig").write_text("[user]\nname = Test\n")
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        (ssh_dir / "config").write_text("Host github.com\n")
        (ssh_dir / "id_ed25519").write_text("fake key")
        (tmp_path / ".bashrc").write_text("# bash config\n")

        disc = SystemDiscovery()
        disc._home = tmp_path
        result = disc.scan_personal_files()

        config_facts = [r for r in result if "config" in r.get("content", "").lower()]
        # Should find at least one config fact and one sensitive config fact
        assert len(config_facts) >= 1

        # Check that sensitive configs are flagged
        sensitive_facts = [r for r in result if r.get("sensitive") is True]
        assert len(sensitive_facts) >= 1
        # SSH items should be sensitive
        ssh_facts = [r for r in sensitive_facts if "SSH" in r.get("content", "")]
        assert len(ssh_facts) >= 1

    def test_finds_creative_files(self, tmp_path):
        """Should detect creative project files."""
        docs = tmp_path / "Documents"
        docs.mkdir()
        (docs / "model.blend").write_bytes(b"fake blender")
        (docs / "design.psd").write_bytes(b"fake photoshop")

        disc = SystemDiscovery()
        disc._home = tmp_path
        result = disc.scan_personal_files()

        creative_facts = [
            r
            for r in result
            if any(
                kw in r.get("content", "").lower()
                for kw in ["3d", "blender", "design", "photoshop"]
            )
        ]
        assert len(creative_facts) >= 1

    def test_finds_writing_directories(self, tmp_path):
        """Should detect note-taking directories."""
        obsidian_dir = tmp_path / "Documents" / "Obsidian"
        obsidian_dir.mkdir(parents=True)
        (obsidian_dir / "note1.md").write_text("# My Note\n")
        (obsidian_dir / "note2.md").write_text("# Another Note\n")

        disc = SystemDiscovery()
        disc._home = tmp_path
        result = disc.scan_personal_files()

        writing_facts = [
            r
            for r in result
            if "writing" in r.get("content", "").lower()
            or "obsidian" in r.get("content", "").lower()
        ]
        assert len(writing_facts) >= 1

    def test_finds_data_files(self, tmp_path):
        """Should detect data/analysis files."""
        docs = tmp_path / "Documents"
        docs.mkdir()
        (docs / "data.csv").write_text("a,b,c\n1,2,3\n")
        (docs / "analysis.ipynb").write_text('{"cells": []}')
        (docs / "db.sqlite").write_bytes(b"fake sqlite")

        disc = SystemDiscovery()
        disc._home = tmp_path
        result = disc.scan_personal_files()

        data_facts = [
            r
            for r in result
            if "data" in r.get("content", "").lower()
            or "csv" in r.get("content", "").lower()
        ]
        assert len(data_facts) >= 1

    def test_nonexistent_dirs_are_skipped(self, tmp_path):
        """Should handle nonexistent directories gracefully."""
        disc = SystemDiscovery()
        disc._home = tmp_path  # Empty temp dir, no Documents etc.
        result = disc.scan_personal_files()
        # Should return empty list, not raise
        assert isinstance(result, list)

    def test_permission_error_is_handled(self, tmp_path):
        """Should not raise on PermissionError."""
        disc = SystemDiscovery()
        disc._home = tmp_path

        # Mock _scan_resume_files to raise PermissionError
        with patch.object(
            disc, "_scan_resume_files", side_effect=PermissionError("denied")
        ):
            result = disc.scan_personal_files()
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# scan_file_system — enriched file system scan
# ---------------------------------------------------------------------------


class TestScanFileSystemEnriched:
    """Tests for enriched scan_file_system output."""

    def test_results_include_file_type(self, tmp_path):
        """Each project result should have file_type='project'."""
        work_dir = tmp_path / "Work"
        work_dir.mkdir()
        project = work_dir / "myproject"
        project.mkdir()
        (project / "main.py").write_text("print('hello')")

        disc = SystemDiscovery()
        result = disc.scan_file_system(paths=[work_dir])

        project_facts = [r for r in result if r.get("file_type") == "project"]
        assert len(project_facts) >= 1

    def test_results_include_languages(self, tmp_path):
        """Each project result should have a languages list."""
        work_dir = tmp_path / "Work"
        work_dir.mkdir()
        project = work_dir / "myproject"
        project.mkdir()
        (project / "main.py").write_text("print('hello')")
        (project / "index.ts").write_text("console.log('hi')")

        disc = SystemDiscovery()
        result = disc.scan_file_system(paths=[work_dir])

        project_facts = [r for r in result if "languages" in r]
        assert len(project_facts) >= 1
        langs = project_facts[0]["languages"]
        assert isinstance(langs, list)
        assert "Python" in langs

    def test_summary_generated_for_multiple_projects(self, tmp_path):
        """Should generate a developer summary when >= 3 projects found."""
        work_dir = tmp_path / "Work"
        work_dir.mkdir()
        for name in ["project1", "project2", "project3"]:
            p = work_dir / name
            p.mkdir()
            (p / "main.py").write_text("pass")

        disc = SystemDiscovery()
        result = disc.scan_file_system(paths=[work_dir])

        # Should have 3 project facts + 1 summary profile fact
        summaries = [r for r in result if r.get("category") == "profile"]
        assert len(summaries) == 1
        assert "developer" in summaries[0]["content"].lower()
        assert "3 projects" in summaries[0]["content"]

    def test_results_include_path(self, tmp_path):
        """Each project result should include a path field."""
        work_dir = tmp_path / "Work"
        work_dir.mkdir()
        project = work_dir / "myproject"
        project.mkdir()
        (project / "app.js").write_text("// js")

        disc = SystemDiscovery()
        result = disc.scan_file_system(paths=[work_dir])

        project_facts = [r for r in result if r.get("file_type") == "project"]
        assert len(project_facts) >= 1
        assert "path" in project_facts[0]
        assert "myproject" in project_facts[0]["path"]

    def test_classification_in_content(self, tmp_path):
        """Projects with markers should have classification in content."""
        work_dir = tmp_path / "Work"
        work_dir.mkdir()
        project = work_dir / "webapp"
        project.mkdir()
        (project / "package.json").write_text('{"name": "webapp"}')
        (project / "index.js").write_text("// main")

        disc = SystemDiscovery()
        result = disc.scan_file_system(paths=[work_dir])

        project_facts = [r for r in result if r.get("file_type") == "project"]
        assert len(project_facts) >= 1
        assert "Node.js project" in project_facts[0]["content"]


# ---------------------------------------------------------------------------
# scan_all includes personal_files
# ---------------------------------------------------------------------------


class TestScanAllIncludesPersonalFiles:
    """scan_all should include personal_files in results."""

    def test_personal_files_in_scan_all(self):
        disc = SystemDiscovery()
        with patch.object(disc, "scan_personal_files", return_value=[]):
            result = disc.scan_all(sources=["personal_files"])
        assert "personal_files" in result
        assert isinstance(result["personal_files"], list)


# ---------------------------------------------------------------------------
# macOS branches — real content out of a fake ~/ (issue #1956)
# ---------------------------------------------------------------------------


class TestMacOSBranches:
    """The darwin branches read the real macOS locations under a fake home."""

    def test_installed_apps_finds_app_bundles(self, isolated_disc, tmp_path):
        apps = tmp_path / "Applications"
        apps.mkdir()
        (apps / "Blender.app").mkdir()
        (apps / "Slack.app").mkdir()
        (apps / "not-an-app.txt").write_text("ignored", encoding="utf-8")

        with patch("sys.platform", "darwin"):
            results = isolated_disc.scan_installed_apps()

        contents = [r["content"] for r in results]
        assert any("Blender" in c for c in contents)
        assert any("Slack" in c for c in contents)
        assert not any("not-an-app" in c for c in contents)
        assert all(r["entity"].startswith("app:") for r in results)
        assert {"blender", "slack"} <= {
            r["entity"].removeprefix("app:") for r in results
        }

    def test_bookmarks_read_every_chromium_profile(self, isolated_disc, tmp_path):
        chrome = tmp_path / "Library" / "Application Support" / "Google" / "Chrome"
        _write_chromium_bookmarks(chrome / "Default", ["https://github.com/amd/gaia"])
        _write_chromium_bookmarks(chrome / "Profile 1", ["https://reddit.com/r/amd"])

        with patch("sys.platform", "darwin"):
            results = isolated_disc.scan_browser_bookmarks()

        domains = {r["content"].split()[2] for r in results}
        assert "github.com" in domains, "Default profile must be read"
        assert "reddit.com" in domains, "Profile 1 must be read too"

    def test_safari_bookmarks_are_included(self, isolated_disc, tmp_path):
        safari = tmp_path / "Library" / "Safari"
        safari.mkdir(parents=True)
        with open(safari / "Bookmarks.plist", "wb") as f:
            plistlib.dump(
                {
                    "Children": [
                        {
                            "Children": [
                                {
                                    "WebBookmarkType": "WebBookmarkTypeLeaf",
                                    "URLString": "https://news.ycombinator.com/",
                                }
                            ]
                        }
                    ]
                },
                f,
                fmt=plistlib.FMT_BINARY,
            )

        with patch("sys.platform", "darwin"):
            results = isolated_disc.scan_browser_bookmarks()

        assert any("news.ycombinator.com" in r["content"] for r in results)

    def test_safari_history_is_sensitive(self, isolated_disc, tmp_path):
        safari = tmp_path / "Library" / "Safari"
        safari.mkdir(parents=True)
        conn = sqlite3.connect(str(safari / "History.db"))
        try:
            conn.execute(
                "CREATE TABLE history_items (id INTEGER PRIMARY KEY, url TEXT, "
                "visit_count INTEGER)"
            )
            conn.execute(
                "CREATE TABLE history_visits (id INTEGER PRIMARY KEY, "
                "history_item INTEGER, visit_time REAL)"
            )
            conn.execute(
                "INSERT INTO history_items VALUES (1, 'https://arxiv.org/abs/1', 12)"
            )
            # Mac absolute time == Unix time - 978307200
            conn.execute(
                "INSERT INTO history_visits VALUES (1, 1, ?)",
                (time.time() - 978307200,),
            )
            conn.commit()
        finally:
            conn.close()

        with patch("sys.platform", "darwin"):
            results = isolated_disc.scan_browser_history()

        arxiv = [r for r in results if "arxiv.org" in r["content"]]
        assert len(arxiv) == 1
        assert arxiv[0]["sensitive"] is True

    def test_firefox_profile_root_is_the_macos_one(self, isolated_disc, tmp_path):
        profile = (
            tmp_path
            / "Library"
            / "Application Support"
            / "Firefox"
            / "Profiles"
            / "abc.default"
        )
        _write_places_sqlite(profile, "https://mozilla.org/about")

        with patch("sys.platform", "darwin"):
            results = isolated_disc.scan_browser_bookmarks()

        assert any("mozilla.org" in r["content"] for r in results)

    def test_thunderbird_and_apple_mail_addresses(self, isolated_disc, tmp_path):
        _write_thunderbird_prefs(
            tmp_path / "Library" / "Thunderbird" / "Profiles" / "xyz.default",
            "tbird@example.com",
        )
        maildata = tmp_path / "Library" / "Mail" / "V10" / "MailData"
        maildata.mkdir(parents=True)
        with open(maildata / "Accounts.plist", "wb") as f:
            plistlib.dump(
                {"MailAccounts": [{"EmailAddresses": ["applemail@example.com"]}]},
                f,
                fmt=plistlib.FMT_BINARY,
            )

        with (
            patch("sys.platform", "darwin"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=44, stdout="", stderr="")
            results = isolated_disc.scan_email_accounts()

        addresses = {r["content"] for r in results}
        assert any("tbird@example.com" in a for a in addresses)
        assert any("applemail@example.com" in a for a in addresses)
        assert all(r["sensitive"] is True for r in results)
        assert all(r["entity"].startswith("service:") for r in results)

    def test_apple_mail_does_not_harvest_correspondents(self, isolated_disc, tmp_path):
        """Only the user's own account address is read, not their contacts.

        Mail plists also hold previous recipients and signature text. Those are
        other people's addresses and must not land in the user's review list.
        """
        maildata = tmp_path / "Library" / "Mail" / "V10" / "MailData"
        maildata.mkdir(parents=True)
        with open(maildata / "Accounts.plist", "wb") as f:
            plistlib.dump(
                {
                    "MailAccounts": [{"EmailAddresses": ["me@example.com"]}],
                    "PreviousRecipients": ["colleague@other-company.com"],
                    "Signature": "Reply to boss@other-company.com",
                },
                f,
                fmt=plistlib.FMT_BINARY,
            )

        with patch("sys.platform", "darwin"):
            results = []
            isolated_disc._scan_apple_mail(set(), results)

        contents = " ".join(r["content"] for r in results)
        assert "me@example.com" in contents
        assert "colleague@other-company.com" not in contents
        assert "boss@other-company.com" not in contents


# ---------------------------------------------------------------------------
# Linux branches — UNVALIDATED on real hardware; fixture-covered only
# ---------------------------------------------------------------------------


class TestLinuxBranches:
    """The linux branches read the real Linux locations under a fake home.

    No Linux hardware was available for #1956; these branches are exercised on
    the CI/dev runner via patched sys.platform only.
    """

    def test_installed_apps_from_desktop_entries(self, isolated_disc, tmp_path):
        apps = tmp_path / ".local" / "share" / "applications"
        apps.mkdir(parents=True)
        (apps / "blender.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Blender\nExec=blender %f\n",
            encoding="utf-8",
        )
        (apps / "hidden.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Hidden Tool\nNoDisplay=true\n",
            encoding="utf-8",
        )
        (apps / "link.desktop").write_text(
            "[Desktop Entry]\nType=Link\nName=Some Link\nURL=https://example.com\n",
            encoding="utf-8",
        )

        with patch("sys.platform", "linux"):
            results = isolated_disc.scan_installed_apps()

        contents = [r["content"] for r in results]
        assert any("Blender" in c for c in contents)
        assert not any("Hidden Tool" in c for c in contents)
        assert not any("Some Link" in c for c in contents)

    def test_desktop_exec_field_codes_do_not_break_parsing(
        self, isolated_disc, tmp_path
    ):
        """Exec= lines contain %U / %f, which the default interpolator rejects."""
        apps = tmp_path / ".local" / "share" / "applications"
        apps.mkdir(parents=True)
        (apps / "browser.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Firefox\n"
            "Exec=firefox %U\nIcon=firefox\n",
            encoding="utf-8",
        )

        with patch("sys.platform", "linux"):
            results = isolated_disc.scan_installed_apps()

        assert any("Firefox" in r["content"] for r in results)

    def test_flatpak_and_snap_exports_are_scanned(self, isolated_disc, tmp_path):
        flatpak = (
            tmp_path
            / ".local"
            / "share"
            / "flatpak"
            / "exports"
            / "share"
            / "applications"
        )
        flatpak.mkdir(parents=True)
        (flatpak / "org.gimp.GIMP.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=GNU Image Manipulation Program\n",
            encoding="utf-8",
        )

        with patch("sys.platform", "linux"):
            results = isolated_disc.scan_installed_apps()

        assert any("GNU Image" in r["content"] for r in results)

    def test_chromium_and_chrome_config_roots(self, isolated_disc, tmp_path):
        _write_chromium_bookmarks(
            tmp_path / ".config" / "google-chrome" / "Default",
            ["https://github.com/amd/gaia"],
        )
        _write_chromium_bookmarks(
            tmp_path / ".config" / "chromium" / "Profile 2",
            ["https://stackoverflow.com/questions/1"],
        )

        with patch("sys.platform", "linux"):
            results = isolated_disc.scan_browser_bookmarks()

        domains = {r["content"].split()[2] for r in results}
        assert {"github.com", "stackoverflow.com"} <= domains

    def test_firefox_snap_profile_root(self, isolated_disc, tmp_path):
        profile = (
            tmp_path
            / "snap"
            / "firefox"
            / "common"
            / ".mozilla"
            / "firefox"
            / "abc.default"
        )
        _write_places_sqlite(profile, "https://kernel.org/doc")

        with patch("sys.platform", "linux"):
            results = isolated_disc.scan_browser_history()

        kernel = [r for r in results if "kernel.org" in r["content"]]
        assert len(kernel) == 1
        assert kernel[0]["sensitive"] is True

    def test_thunderbird_and_evolution_addresses(self, isolated_disc, tmp_path):
        _write_thunderbird_prefs(
            tmp_path / ".thunderbird" / "xyz.default", "tbird@example.com"
        )
        sources = tmp_path / ".config" / "evolution" / "sources"
        sources.mkdir(parents=True)
        (sources / "account1.source").write_text(
            "[Data Source]\nDisplayName=Work\n\n"
            "[Mail Identity]\nAddress=evo@example.com\nName=Alex\n",
            encoding="utf-8",
        )

        with patch("sys.platform", "linux"):
            results = isolated_disc.scan_email_accounts()

        addresses = {r["content"] for r in results}
        assert any("tbird@example.com" in a for a in addresses)
        assert any("evo@example.com" in a for a in addresses)
        assert all(r["sensitive"] is True for r in results)


# ---------------------------------------------------------------------------
# Unsupported platform — reported by name, never silently empty (#1956 D1)
# ---------------------------------------------------------------------------


class TestUnsupportedPlatform:
    """unsupported_reason() is the single source of truth for "no branch here"."""

    @pytest.mark.parametrize(
        "platform,source,supported",
        [
            ("darwin", "installed_apps", True),
            ("linux", "browser_history", True),
            ("win32", "email_accounts", True),
            ("darwin", "windows_userassist", False),
            ("linux", "windows_userassist", False),
            ("win32", "macos_app_usage", False),
            ("freebsd13", "installed_apps", False),
            ("freebsd13", "browser_bookmarks", False),
            ("freebsd13", "browser_history", False),
            ("freebsd13", "email_accounts", False),
        ],
    )
    def test_truth_table(self, platform, source, supported):
        with patch("sys.platform", platform):
            reason = unsupported_reason(source)
        assert (reason is None) is supported
        if reason is not None:
            assert source in reason
            assert platform in reason

    def test_platform_neutral_sources_are_never_unsupported(self):
        for platform in ("win32", "darwin", "linux", "freebsd13"):
            with patch("sys.platform", platform):
                assert unsupported_reason("file_system") is None
                assert unsupported_reason("git_repos") is None

    @pytest.mark.parametrize(
        "scanner_name",
        [
            "scan_installed_apps",
            "scan_browser_bookmarks",
            "scan_browser_history",
            "scan_email_accounts",
        ],
    )
    def test_scanner_logs_the_skip_and_returns_empty(
        self, isolated_disc, caplog, scanner_name
    ):
        caplog.set_level(logging.INFO, logger=DISCOVERY_LOGGER)
        with patch("sys.platform", "freebsd13"):
            result = getattr(isolated_disc, scanner_name)()

        assert result == []
        source = scanner_name.removeprefix("scan_")
        assert any(
            source in rec.getMessage() and "freebsd13" in rec.getMessage()
            for rec in caplog.records
        ), f"{scanner_name} must name itself and the platform when skipped"

    def test_scan_all_reports_every_skipped_source(self, caplog):
        caplog.set_level(logging.INFO, logger=DISCOVERY_LOGGER)
        disc = SystemDiscovery()
        with patch("sys.platform", "darwin"):
            results = disc.scan_all(sources=["windows_userassist"])

        assert results == {"windows_userassist": []}
        assert any(
            "windows_userassist" in rec.getMessage() and "darwin" in rec.getMessage()
            for rec in caplog.records
        )

    def test_support_map_keys_are_all_real_sources(self):
        """A typo'd key would gate nothing and skip nothing, silently.

        `_PLATFORM_SUPPORT` only takes effect for names `scan_all` and the Agent
        UI actually dispatch, so a misspelled key is a no-op no runtime error
        would reveal.
        """
        from gaia.agents.base.discovery import _PLATFORM_SUPPORT
        from gaia.ui.routers.memory import _DISCOVERY_SOURCES

        # scan_all drops names it does not know, so a bogus key never comes back.
        disc = SystemDiscovery()
        gated = list(_PLATFORM_SUPPORT)
        with (
            patch.object(disc, "_home", Path("/nonexistent/cold-home")),
            patch("gaia.agents.base.discovery._MACOS_APP_DIRS", ()),
            patch("gaia.agents.base.discovery._LINUX_DESKTOP_DIRS", ()),
            patch("gaia.agents.base.discovery.winreg", None),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=44, stdout="", stderr="")
            dispatched = set(disc.scan_all(sources=gated))

        unknown = set(gated) - dispatched
        assert not unknown, f"_PLATFORM_SUPPORT keys not known to scan_all: {unknown}"

        ui_only_gap = set(_PLATFORM_SUPPORT) - set(_DISCOVERY_SOURCES)
        assert ui_only_gap == {"email_accounts"}, (
            "the Agent UI's source list drifted from _PLATFORM_SUPPORT; "
            f"unexpected difference: {ui_only_gap}"
        )

    def test_drift_between_map_and_branches_is_reported(self, isolated_disc, caplog):
        """A platform listed as supported but with no branch must not be quiet.

        This is the fail-open direction: editing the map is easier than writing
        a scanner, so the mismatch that ships is "map says yes, code says no".
        """
        caplog.set_level(logging.INFO, logger=DISCOVERY_LOGGER)
        with (
            patch("sys.platform", "freebsd13"),
            patch.dict(
                "gaia.agents.base.discovery._PLATFORM_SUPPORT",
                {"installed_apps": ("win32", "darwin", "linux", "freebsd13")},
            ),
        ):
            result = isolated_disc.scan_installed_apps()

        assert result == []
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, "map/branch drift must not return a quiet empty list"
        assert "drifted" in warnings[0].getMessage()

    def test_scan_all_does_not_invoke_an_unsupported_scanner(self):
        disc = SystemDiscovery()
        with (
            patch("sys.platform", "darwin"),
            patch.object(disc, "scan_windows_userassist") as mock_scan,
        ):
            disc.scan_all(sources=["windows_userassist"])
        assert not mock_scan.called


# ---------------------------------------------------------------------------
# Cold state — a brand-new user's machine, not a primed dev box
# ---------------------------------------------------------------------------


class TestColdEmptyHome:
    """An empty home returns [] from all four scanners, quietly and safely."""

    @pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
    def test_empty_home_returns_empty_without_errors(
        self, isolated_disc, caplog, platform
    ):
        caplog.set_level(logging.DEBUG, logger=DISCOVERY_LOGGER)
        with (
            patch("sys.platform", platform),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=44, stdout="", stderr="")
            assert isolated_disc.scan_installed_apps() == []
            assert isolated_disc.scan_browser_bookmarks() == []
            assert isolated_disc.scan_browser_history() == []
            assert isolated_disc.scan_email_accounts() == []

        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert not errors, f"cold start logged errors on {platform}: {errors}"

    @pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
    def test_empty_home_scan_all_returns_lists(self, isolated_disc, platform):
        with (
            patch("sys.platform", platform),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=44, stdout="", stderr="")
            results = isolated_disc.scan_all(
                sources=[
                    "installed_apps",
                    "browser_bookmarks",
                    "browser_history",
                    "email_accounts",
                ]
            )
        assert results == {
            "installed_apps": [],
            "browser_bookmarks": [],
            "browser_history": [],
            "email_accounts": [],
        }


# ---------------------------------------------------------------------------
# Keychain call contract — the call must be VALID, not merely made (#1956 D2)
# ---------------------------------------------------------------------------


class TestKeychainContractShape:
    """`security` is invoked as an argv list, attributes-only, with a timeout."""

    def _run_keychain(self, disc, stdout="", returncode=0):
        with (
            patch("sys.platform", "darwin"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                returncode=returncode, stdout=stdout, stderr=""
            )
            results = []
            disc._scan_macos_keychain(set(), results)
        return mock_run, results

    def test_argv_shape_is_exact(self, isolated_disc):
        mock_run, _ = self._run_keychain(isolated_disc, returncode=44)

        assert mock_run.call_count > 0, "no mail host was queried"
        for call in mock_run.call_args_list:
            argv = call.args[0]
            assert isinstance(argv, list), "argv must be a list, never a shell string"
            assert argv[:3] == ["security", "find-internet-password", "-s"]
            assert len(argv) == 4, f"unexpected extra arguments: {argv}"
            assert isinstance(argv[3], str) and argv[3]

    def test_never_reads_the_secret(self, isolated_disc):
        """-g returns the stored password and raises an auth prompt. Never pass it."""
        mock_run, _ = self._run_keychain(isolated_disc, returncode=44)
        for call in mock_run.call_args_list:
            assert "-g" not in call.args[0]
            assert "-w" not in call.args[0]

    def test_timeout_is_set_and_shell_is_never_used(self, isolated_disc):
        mock_run, _ = self._run_keychain(isolated_disc, returncode=44)
        for call in mock_run.call_args_list:
            assert call.kwargs.get("timeout") == 10
            assert not call.kwargs.get("shell", False)
            assert call.kwargs.get("check") is False

    def test_attribute_dump_yields_a_sensitive_fact(self, isolated_disc):
        stdout = (
            'keychain: "/Users/alex/Library/Keychains/login.keychain-db"\n'
            "class: 0x00000000\n"
            "attributes:\n"
            '    "acct"<blob>="alex@gmail.com"\n'
            '    "srvr"<blob>="imap.gmail.com"\n'
        )
        _, results = self._run_keychain(isolated_disc, stdout=stdout)

        assert results, "an address in the attribute dump must become a fact"
        assert results[0]["content"] == "Email account: alex@gmail.com"
        assert results[0]["sensitive"] is True
        assert results[0]["entity"] == "service:gmail"

    def test_item_not_found_is_quiet(self, isolated_disc, caplog):
        caplog.set_level(logging.INFO, logger=DISCOVERY_LOGGER)
        _, results = self._run_keychain(isolated_disc, returncode=44)

        assert results == []
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    def test_empty_output_on_success_is_reported(self, isolated_disc, caplog):
        """Exit 0 with no attributes means the CLI output shape changed."""
        caplog.set_level(logging.INFO, logger=DISCOVERY_LOGGER)
        _, results = self._run_keychain(isolated_disc, stdout="", returncode=0)

        assert results == []
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings, "a shape mismatch must not be silent"
        assert "output format" in warnings[0].getMessage()

    def test_missing_security_binary_is_reported(self, isolated_disc, caplog):
        caplog.set_level(logging.INFO, logger=DISCOVERY_LOGGER)
        with (
            patch("sys.platform", "darwin"),
            patch("subprocess.run", side_effect=FileNotFoundError("security")),
        ):
            results = []
            isolated_disc._scan_macos_keychain(set(), results)

        assert results == []
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings and "security" in warnings[0].getMessage()


# ---------------------------------------------------------------------------
# TCC denial — actionable, surfaced, non-fatal (#1956 D4)
# ---------------------------------------------------------------------------


class TestPermissionDenials:
    """A permission denial is reported with the remedy, not swallowed.

    Covers Safari (TCC), Apple Mail (TCC), and Evolution (ordinary file mode).
    """

    def test_history_denial_warns_and_continues(self, isolated_disc, tmp_path, caplog):
        caplog.set_level(logging.INFO, logger=DISCOVERY_LOGGER)
        safari = tmp_path / "Library" / "Safari"
        safari.mkdir(parents=True)
        (safari / "History.db").write_bytes(b"SQLite format 3\x00")
        chrome = tmp_path / "Library" / "Application Support" / "Google" / "Chrome"
        _write_chromium_bookmarks(chrome / "Default", ["https://github.com/amd/gaia"])

        with (
            patch("sys.platform", "darwin"),
            patch(
                "shutil.copy2",
                side_effect=PermissionError(1, "Operation not permitted"),
            ),
        ):
            history = isolated_disc.scan_browser_history()
            bookmarks = isolated_disc.scan_browser_bookmarks()

        assert history == []
        assert bookmarks, "a denial in one source must not abort the whole scan"

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1, f"expected one actionable warning, got {warnings}"
        message = warnings[0].getMessage()
        assert "Safari history" in message
        assert "Full Disk Access" in message
        assert "System Settings" in message

    @requires_chmod_denial
    def test_apple_mail_denial_warns(self, isolated_disc, tmp_path, caplog):
        """An unlistable ~/Library/Mail must report, not look like "no accounts".

        Regression guard: Path.glob SWALLOWS PermissionError and yields [], so
        a glob-based enumeration here was silently empty on exactly the machine
        state this feature targets — a Mac without Full Disk Access.
        """
        caplog.set_level(logging.INFO, logger=DISCOVERY_LOGGER)
        mail_dir = tmp_path / "Library" / "Mail" / "V10" / "MailData"
        mail_dir.mkdir(parents=True)
        with open(mail_dir / "Accounts.plist", "wb") as f:
            plistlib.dump({"MailAccounts": [{"EmailAddresses": ["a@b.com"]}]}, f)
        os.chmod(tmp_path / "Library" / "Mail", 0o000)

        try:
            with patch("sys.platform", "darwin"):
                results = []
                isolated_disc._scan_apple_mail(set(), results)
        finally:
            os.chmod(tmp_path / "Library" / "Mail", 0o755)

        assert results == []
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, "an unreadable ~/Library/Mail must not be silent"
        assert "Full Disk Access" in warnings[0].getMessage()

    @requires_chmod_denial
    def test_evolution_denial_warns(self, isolated_disc, tmp_path, caplog):
        """Same Path.glob swallow, Linux side."""
        caplog.set_level(logging.INFO, logger=DISCOVERY_LOGGER)
        sources = tmp_path / ".config" / "evolution" / "sources"
        sources.mkdir(parents=True)
        (sources / "a.source").write_text(
            "[Mail Identity]\nAddress=evo@example.com\n", encoding="utf-8"
        )
        os.chmod(sources, 0o000)

        try:
            with patch("sys.platform", "linux"):
                results = []
                isolated_disc._scan_evolution(set(), results)
        finally:
            os.chmod(sources, 0o755)

        assert results == []
        assert [r for r in caplog.records if r.levelno == logging.WARNING]

    def test_bookmarks_denial_warns(self, isolated_disc, tmp_path, caplog):
        caplog.set_level(logging.INFO, logger=DISCOVERY_LOGGER)
        safari = tmp_path / "Library" / "Safari"
        safari.mkdir(parents=True)
        plist_path = safari / "Bookmarks.plist"
        plist_path.write_bytes(b"bplist00")

        real_open = open

        def deny_safari(file, *args, **kwargs):
            """Deny only the TCC-protected plist; leave every other open alone."""
            if str(file) == str(plist_path):
                raise PermissionError(1, "Operation not permitted")
            return real_open(file, *args, **kwargs)

        with (
            patch("sys.platform", "darwin"),
            patch("builtins.open", side_effect=deny_safari),
        ):
            results = isolated_disc.scan_browser_bookmarks()

        assert results == []
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "Full Disk Access" in warnings[0].getMessage()
