# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Hermetic unit tests for SystemDiscovery pure classification helpers.

These tests exercise only the pure/classifier helpers and the directory-walk
skip logic. No real scan of the user's home directory, no network, no git, no
account data — all filesystem inputs are crafted under ``tmp_path`` and the
home directory is redirected to an isolated temp dir.
"""

from pathlib import Path

import pytest

from gaia.agents.base.discovery import (
    SystemDiscovery,
    _categorize_app,
    _classify_domain,
    _classify_path,
    _classify_project,
    _classify_remote,
    _detect_languages,
    _extract_domain,
    _is_hidden,
)

# ---------------------------------------------------------------------------
# _is_hidden
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        (".git", True),
        (".ssh", True),
        ("Projects", False),
        ("node_modules", False),  # skip-listed but not "hidden" by dot rule
        ("", False),
    ],
)
def test_is_hidden(name, expected):
    assert _is_hidden(name) is expected


# ---------------------------------------------------------------------------
# _classify_path — location-based context
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "parts,expected",
    [
        (("home", "user", "Work", "repo"), "work"),
        (("home", "user", "Projects", "repo"), "work"),
        (("home", "user", "Personal", "stuff"), "personal"),
        (("home", "user", "Documents", "thing"), "unclassified"),
        (("home", "user", "random"), "unclassified"),
    ],
)
def test_classify_path(parts, expected):
    # _classify_path lowercases parts, so casing is irrelevant.
    assert _classify_path(Path(*parts)) == expected


def test_classify_path_is_case_insensitive():
    assert _classify_path(Path("/home/user/WORK/repo")) == "work"
    assert _classify_path(Path("/home/user/PERSONAL/x")) == "personal"


# ---------------------------------------------------------------------------
# _classify_remote — git remote URL context (hostname-parsed, no spoofing)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/amd/gaia.git", "work"),
        ("https://github.com/microsoft/vscode", "work"),
        ("https://github.com/google/jax", "work"),
        ("https://github.com/someuser/sideproject", "unclassified"),
        ("https://gitlab.com/someuser/thing", "unclassified"),
        ("", "unclassified"),
    ],
)
def test_classify_remote(url, expected):
    assert _classify_remote(url) == expected


def test_classify_remote_does_not_spoof_via_hostname():
    # A malicious host that merely contains "github.com" as a substring in the
    # path must NOT be treated as github.com — classification stays unclassified.
    assert _classify_remote("https://evil.example/github.com/amd") == "unclassified"


# ---------------------------------------------------------------------------
# _classify_domain — bookmark/history domain context
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "domain,expected",
    [
        ("github.com", "work"),
        ("stackoverflow.com", "work"),
        ("facebook.com", "personal"),
        ("netflix.com", "personal"),
        ("example.com", "unclassified"),
        ("GitHub.com", "work"),  # case-insensitive
    ],
)
def test_classify_domain(domain, expected):
    assert _classify_domain(domain) == expected


# ---------------------------------------------------------------------------
# _extract_domain — URL -> bare domain
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.github.com/amd/gaia", "github.com"),
        ("http://example.com:8080/path?q=1#frag", "example.com"),
        ("https://Sub.Example.COM/x", "sub.example.com"),
        ("ftp://files.example.org/a/b", "files.example.org"),
        ("  https://github.com/x  ", "github.com"),
    ],
)
def test_extract_domain(url, expected):
    assert _extract_domain(url) == expected


# ---------------------------------------------------------------------------
# _categorize_app — keyword-based app categorization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "app_name,expected",
    [
        ("Visual Studio Code", "IDE"),
        ("PyCharm Community Edition", "IDE"),
        ("Docker Desktop", "DevTool"),
        ("Google Chrome", "Browser"),
        ("Slack", "Communication"),
        ("Blender", "Creative"),
        ("Notion", "Productivity"),
        ("Some Totally Unknown App", "Other"),
    ],
)
def test_categorize_app(app_name, expected):
    assert _categorize_app(app_name) == expected


def test_categorize_app_first_keyword_match_wins():
    # Documents current behavior: matching is first-category-wins by substring,
    # so "Obsidian" matches the Creative keyword "obs" (OBS) before reaching the
    # Productivity list. Guards against silent reordering of _APP_CATEGORIES.
    assert _categorize_app("Obsidian") == "Creative"


# ---------------------------------------------------------------------------
# _detect_languages — extension counting with hidden/skip-dir exclusion
# ---------------------------------------------------------------------------


def test_detect_languages_counts_and_sorts(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 2\n")
    (tmp_path / "c.ts").write_text("const z = 3\n")
    langs = _detect_languages(tmp_path, max_depth=2)
    # Python (2 files) should outrank TypeScript (1 file).
    assert langs[0] == "Python"
    assert "TypeScript" in langs


def test_detect_languages_skips_hidden_and_skip_dirs(tmp_path):
    # A real source file at the top level.
    (tmp_path / "main.py").write_text("print('hi')\n")
    # Files inside skip-listed / hidden dirs must NOT be counted.
    nm = tmp_path / "node_modules"
    nm.mkdir()
    (nm / "dep.js").write_text("module.exports = {}\n")
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "lib.py").write_text("# vendored\n")
    hidden = tmp_path / ".secret"
    hidden.mkdir()
    (hidden / "leak.rs").write_text("fn main() {}\n")

    langs = _detect_languages(tmp_path, max_depth=2)
    assert langs == ["Python"]
    # JavaScript/Rust came only from excluded dirs, so they must be absent.
    assert "JavaScript" not in langs
    assert "Rust" not in langs


def test_detect_languages_ignores_doc_and_markup_extensions(tmp_path):
    (tmp_path / "README.md").write_text("# doc\n")
    (tmp_path / "page.html").write_text("<html></html>\n")
    (tmp_path / "style.css").write_text("body{}\n")
    # No "real" code language present -> empty result.
    assert _detect_languages(tmp_path, max_depth=2) == []


# ---------------------------------------------------------------------------
# _classify_project — marker/language based project classification
# ---------------------------------------------------------------------------


def test_classify_project_detects_python_package(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert _classify_project(tmp_path, ["Python"]) == "Python package"


def test_classify_project_detects_node_project(tmp_path):
    (tmp_path / "package.json").write_text("{}\n")
    assert _classify_project(tmp_path, ["JavaScript"]) == "Node.js project"


def test_classify_project_falls_back_to_language(tmp_path):
    # No markers -> language-based classification.
    assert _classify_project(tmp_path, ["Rust"]) == "Rust codebase"


def test_classify_project_empty_when_nothing_known(tmp_path):
    assert _classify_project(tmp_path, []) == ""


# ---------------------------------------------------------------------------
# Directory-walk skip logic — scan_file_system / scan_git_repos must NOT emit
# hidden or skip-listed directories as discovered facts.
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_discovery(tmp_path, monkeypatch):
    """SystemDiscovery whose home points at an empty isolated temp dir.

    Guards against any accidental scan of the real user home directory.
    """
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    return SystemDiscovery()


def test_scan_file_system_excludes_hidden_and_skip_dirs(isolated_discovery, tmp_path):
    work = tmp_path / "Work"
    work.mkdir()

    # A legitimate project directory with code.
    real = work / "myproject"
    real.mkdir()
    (real / "app.py").write_text("print('hi')\n")

    # Hidden directory — private, must NOT be emitted as a discovered project.
    hidden = work / ".private"
    hidden.mkdir()
    (hidden / "secret.py").write_text("password = 'x'\n")

    # Skip-listed directory — must NOT be emitted as a project either.
    nm = work / "node_modules"
    nm.mkdir()
    (nm / "index.js").write_text("module.exports = {}\n")

    facts = isolated_discovery.scan_file_system(paths=[work])

    project_facts = [f for f in facts if f.get("file_type") == "project"]
    emitted_names = {f["path"] for f in project_facts}

    assert str(real) in emitted_names
    assert str(hidden) not in emitted_names
    assert str(nm) not in emitted_names
    # No fact should reference the private/system directory in its content.
    for f in facts:
        assert ".private" not in f["content"]
        assert "node_modules" not in f["content"]


def test_scan_file_system_skips_missing_paths(isolated_discovery, tmp_path):
    # Nonexistent override path -> no crash, empty result.
    assert isolated_discovery.scan_file_system(paths=[tmp_path / "nope"]) == []


def test_scan_git_repos_excludes_hidden_dirs(isolated_discovery, tmp_path):
    work = tmp_path / "Work"
    work.mkdir()

    # A real git repo (minimal .git with config + HEAD).
    repo = work / "realrepo"
    repo.mkdir()
    git_dir = repo / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text(
        '[remote "origin"]\n\turl = https://github.com/someuser/realrepo.git\n'
    )
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
    (repo / "lib.py").write_text("x = 1\n")

    # A hidden directory that itself contains a git repo — the walk skips hidden
    # dirs, so this private repo must NOT surface as a discovered fact.
    hidden_parent = work / ".hidden"
    hidden_parent.mkdir()
    hidden_repo = hidden_parent / "privaterepo"
    hidden_repo.mkdir()
    hgit = hidden_repo / ".git"
    hgit.mkdir()
    (hgit / "config").write_text(
        '[remote "origin"]\n\turl = https://github.com/secret/privaterepo.git\n'
    )
    (hgit / "HEAD").write_text("ref: refs/heads/main\n")

    facts = isolated_discovery.scan_git_repos(paths=[work])
    contents = " ".join(f["content"] for f in facts)

    assert "realrepo" in contents
    assert "privaterepo" not in contents
    assert "secret" not in contents


def test_scan_git_repos_parses_remote_and_branch(isolated_discovery, tmp_path):
    work = tmp_path / "Work"
    work.mkdir()
    repo = work / "gaia"
    repo.mkdir()
    git_dir = repo / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text(
        '[remote "origin"]\n\turl = https://github.com/amd/gaia.git\n'
    )
    (git_dir / "HEAD").write_text("ref: refs/heads/develop\n")

    facts = isolated_discovery.scan_git_repos(paths=[work])
    assert len(facts) == 1
    fact = facts[0]
    assert "gaia" in fact["content"]
    assert "github.com/amd/gaia.git" in fact["content"]
    assert "branch: develop" in fact["content"]
    # Remote points at /amd/ -> classified as work context.
    assert fact["context"] == "work"
