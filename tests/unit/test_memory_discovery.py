# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for SystemDiscovery — local system scanner for day-zero bootstrap.

Tests cover: _classify_remote (URL hostname safety), _classify_path,
_classify_domain, _extract_domain, scan_all returns expected structure,
the platform guards for Windows-only methods, _classify_project,
and the scan_personal_files scanner.

All tests are stdlib-only — no real filesystem scanning performed.
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.discovery import (
    SystemDiscovery,
    _classify_domain,
    _classify_path,
    _classify_project,
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
