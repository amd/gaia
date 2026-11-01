# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for util/check_dependabot.py.

Each test writes an ephemeral dependabot.yml fragment to a tmp_path and
monkeypatches CONFIG so run_check() reads the test file instead of the
real .github/dependabot.yml.
"""

import sys
from pathlib import Path

import pytest
import yaml

# Ensure util/ is importable regardless of where pytest is invoked from.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util"))

import check_dependabot  # noqa: E402


def _write_config(tmp_path: Path, updates: list) -> Path:
    """Write a minimal dependabot.yml with the given updates list."""
    cfg = {"version": 2, "updates": updates}
    p = tmp_path / "dependabot.yml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    return p


_REAL_TRACKED_MANIFESTS = check_dependabot._tracked_manifests
_REAL_DIRECTORY_EXISTS = check_dependabot._directory_exists


@pytest.fixture(autouse=True)
def patch_config(tmp_path, monkeypatch):
    """Tests set CONFIG themselves; lockfile coverage is off unless a test opts in."""
    monkeypatch.setattr(check_dependabot, "_tracked_manifests", lambda: [])
    monkeypatch.setattr(check_dependabot, "_directory_exists", lambda d: True)
    yield


# ---------------------------------------------------------------------------
# Happy-path
# ---------------------------------------------------------------------------


class TestRunCheckPassingCases:
    def test_valid_pip_entry(self, tmp_path, monkeypatch):
        p = _write_config(
            tmp_path,
            [
                {
                    "package-ecosystem": "pip",
                    "directory": "/",
                    "open-pull-requests-limit": 5,
                    "schedule": {"interval": "weekly"},
                }
            ],
        )
        monkeypatch.setattr(check_dependabot, "CONFIG", p)
        assert check_dependabot.run_check() == 0

    def test_valid_npm_entry_with_groups(self, tmp_path, monkeypatch):
        p = _write_config(
            tmp_path,
            [
                {
                    "package-ecosystem": "npm",
                    "directory": "/",
                    "open-pull-requests-limit": 5,
                    "schedule": {"interval": "weekly"},
                    "groups": {"all-deps": {"patterns": ["*"]}},
                }
            ],
        )
        monkeypatch.setattr(check_dependabot, "CONFIG", p)
        assert check_dependabot.run_check() == 0

    def test_absent_limit_key_treated_as_live(self, tmp_path, monkeypatch):
        """No open-pull-requests-limit key → Dependabot default → should pass."""
        p = _write_config(
            tmp_path,
            [
                {
                    "package-ecosystem": "pip",
                    "directory": "/",
                    "schedule": {"interval": "weekly"},
                }
            ],
        )
        monkeypatch.setattr(check_dependabot, "CONFIG", p)
        assert check_dependabot.run_check() == 0

    def test_multiple_valid_entries(self, tmp_path, monkeypatch):
        p = _write_config(
            tmp_path,
            [
                {
                    "package-ecosystem": "pip",
                    "directory": "/",
                    "open-pull-requests-limit": 5,
                    "schedule": {"interval": "weekly"},
                },
                {
                    "package-ecosystem": "npm",
                    "directory": "/ui",
                    "open-pull-requests-limit": 5,
                    "schedule": {"interval": "weekly"},
                    "groups": {"ui-deps": {"patterns": ["*"]}},
                },
                {
                    "package-ecosystem": "github-actions",
                    "directory": "/",
                    "open-pull-requests-limit": 5,
                    "schedule": {"interval": "weekly"},
                },
            ],
        )
        monkeypatch.setattr(check_dependabot, "CONFIG", p)
        assert check_dependabot.run_check() == 0


# ---------------------------------------------------------------------------
# Invariant 1: open-pull-requests-limit == 0 must be rejected
# ---------------------------------------------------------------------------


class TestLimitZeroRejected:
    def test_integer_zero_rejected(self, tmp_path, monkeypatch):
        p = _write_config(
            tmp_path,
            [
                {
                    "package-ecosystem": "pip",
                    "directory": "/",
                    "open-pull-requests-limit": 0,
                    "schedule": {"interval": "weekly"},
                }
            ],
        )
        monkeypatch.setattr(check_dependabot, "CONFIG", p)
        assert check_dependabot.run_check() == 1

    def test_quoted_zero_rejected(self, tmp_path, monkeypatch):
        """YAML '0' (quoted string) must also be caught — guards the int() cast."""
        raw = 'version: 2\nupdates:\n  - package-ecosystem: "pip"\n    directory: "/"\n    open-pull-requests-limit: "0"\n    schedule:\n      interval: "weekly"\n'
        p = tmp_path / "dependabot.yml"
        p.write_text(raw, encoding="utf-8")
        monkeypatch.setattr(check_dependabot, "CONFIG", p)
        assert check_dependabot.run_check() == 1

    def test_error_message_names_ecosystem_and_directory(
        self, tmp_path, monkeypatch, capsys
    ):
        p = _write_config(
            tmp_path,
            [
                {
                    "package-ecosystem": "npm",
                    "directory": "/apps/ui",
                    "open-pull-requests-limit": 0,
                    "schedule": {"interval": "weekly"},
                    "groups": {"all": {"patterns": ["*"]}},
                }
            ],
        )
        monkeypatch.setattr(check_dependabot, "CONFIG", p)
        check_dependabot.run_check()
        stderr = capsys.readouterr().err
        assert "npm" in stderr
        assert "/apps/ui" in stderr

    def test_non_integer_limit_rejected(self, tmp_path, monkeypatch, capsys):
        """A non-numeric limit value (e.g. 'five') should produce an error, not a traceback."""
        raw = 'version: 2\nupdates:\n  - package-ecosystem: "pip"\n    directory: "/"\n    open-pull-requests-limit: "five"\n    schedule:\n      interval: "weekly"\n'
        p = tmp_path / "dependabot.yml"
        p.write_text(raw, encoding="utf-8")
        monkeypatch.setattr(check_dependabot, "CONFIG", p)
        result = check_dependabot.run_check()
        assert result == 1
        stderr = capsys.readouterr().err
        assert "non-integer" in stderr


# ---------------------------------------------------------------------------
# Invariant 2: npm entries without groups: must be rejected
# ---------------------------------------------------------------------------


class TestNpmGroupsRequired:
    def test_npm_without_groups_rejected(self, tmp_path, monkeypatch):
        p = _write_config(
            tmp_path,
            [
                {
                    "package-ecosystem": "npm",
                    "directory": "/",
                    "open-pull-requests-limit": 5,
                    "schedule": {"interval": "weekly"},
                }
            ],
        )
        monkeypatch.setattr(check_dependabot, "CONFIG", p)
        assert check_dependabot.run_check() == 1

    def test_pip_without_groups_allowed(self, tmp_path, monkeypatch):
        """groups: is only required for npm — other ecosystems should pass without it."""
        p = _write_config(
            tmp_path,
            [
                {
                    "package-ecosystem": "pip",
                    "directory": "/",
                    "open-pull-requests-limit": 5,
                    "schedule": {"interval": "weekly"},
                }
            ],
        )
        monkeypatch.setattr(check_dependabot, "CONFIG", p)
        assert check_dependabot.run_check() == 0

    def test_github_actions_without_groups_allowed(self, tmp_path, monkeypatch):
        p = _write_config(
            tmp_path,
            [
                {
                    "package-ecosystem": "github-actions",
                    "directory": "/",
                    "open-pull-requests-limit": 5,
                    "schedule": {"interval": "weekly"},
                }
            ],
        )
        monkeypatch.setattr(check_dependabot, "CONFIG", p)
        assert check_dependabot.run_check() == 0

    def test_error_message_mentions_groups(self, tmp_path, monkeypatch, capsys):
        p = _write_config(
            tmp_path,
            [
                {
                    "package-ecosystem": "npm",
                    "directory": "/ui",
                    "open-pull-requests-limit": 5,
                    "schedule": {"interval": "weekly"},
                }
            ],
        )
        monkeypatch.setattr(check_dependabot, "CONFIG", p)
        check_dependabot.run_check()
        assert "groups" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Config file error handling
# ---------------------------------------------------------------------------


class TestConfigFileErrors:
    def test_missing_file_returns_1(self, tmp_path, monkeypatch):
        monkeypatch.setattr(check_dependabot, "CONFIG", tmp_path / "nonexistent.yml")
        assert check_dependabot.run_check() == 1

    def test_invalid_yaml_returns_1(self, tmp_path, monkeypatch):
        p = tmp_path / "dependabot.yml"
        p.write_text("version: 2\nupdates: [\n  invalid", encoding="utf-8")
        monkeypatch.setattr(check_dependabot, "CONFIG", p)
        assert check_dependabot.run_check() == 1

    def test_missing_updates_key_returns_1(self, tmp_path, monkeypatch):
        p = tmp_path / "dependabot.yml"
        p.write_text("version: 2\n", encoding="utf-8")
        monkeypatch.setattr(check_dependabot, "CONFIG", p)
        assert check_dependabot.run_check() == 1


# ---------------------------------------------------------------------------
# Lockfile coverage (#3525)
# ---------------------------------------------------------------------------


def _npm(directory=None, directories=None):
    entry = {
        "package-ecosystem": "npm",
        "schedule": {"interval": "weekly"},
        "groups": {"all": {"patterns": ["*"]}},
    }
    if directory is not None:
        entry["directory"] = directory
    if directories is not None:
        entry["directories"] = directories
    return entry


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A fake checkout: set `.files` to the tracked manifests, dirs are created."""
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(check_dependabot, "REPO_ROOT", root)
    monkeypatch.setattr(check_dependabot, "_directory_exists", _REAL_DIRECTORY_EXISTS)

    class Repo:
        files: list = []

        def track(self, *paths):
            for path in paths:
                (root / path).parent.mkdir(parents=True, exist_ok=True)
            self.files = list(paths)
            monkeypatch.setattr(check_dependabot, "_tracked_manifests", lambda: paths)

        def config(self, updates):
            monkeypatch.setattr(
                check_dependabot, "CONFIG", _write_config(tmp_path, updates)
            )

    return Repo()


class TestLockfileCoverage:
    def test_unwatched_npm_lockfile_rejected(self, repo, capsys):
        repo.track("website/package-lock.json")
        repo.config([_npm("/")])
        assert check_dependabot.run_check() == 1
        err = capsys.readouterr().err
        assert "website/package-lock.json" in err and "#3525" in err

    def test_unwatched_go_module_rejected(self, repo, capsys):
        repo.track("tui/go.mod")
        repo.config([_npm("/tui")])
        assert check_dependabot.run_check() == 1
        assert "`gomod`" in capsys.readouterr().err

    def test_wrong_ecosystem_does_not_count(self, repo):
        """A pip entry on /website is not an npm entry on /website."""
        repo.track("website/package-lock.json")
        repo.config(
            [{"package-ecosystem": "pip", "directory": "/website", "schedule": {}}]
        )
        assert check_dependabot.run_check() == 1

    def test_directory_entry_covers_lockfile(self, repo):
        repo.track("website/package-lock.json")
        repo.config([_npm("/website")])
        assert check_dependabot.run_check() == 0

    @pytest.mark.parametrize("spelling", ["/website", "website", "/website/"])
    def test_directories_list_covers_lockfile(self, repo, spelling):
        repo.track("website/package-lock.json", "docs/package-lock.json")
        repo.config([_npm(directories=["/docs", spelling])])
        assert check_dependabot.run_check() == 0

    def test_directories_glob_covers_lockfile(self, repo):
        repo.track(
            "hub/agents/a/npm/package-lock.json", "hub/agents/b/npm/package-lock.json"
        )
        repo.config([_npm(directories=["/hub/agents/*/npm"])])
        assert check_dependabot.run_check() == 0

    def test_root_entry_does_not_cover_nested_lockfile(self, repo):
        """Dependabot's `/` watches the root manifest only, not the whole tree."""
        repo.track("package-lock.json", "website/package-lock.json")
        repo.config([_npm("/")])
        assert check_dependabot.run_check() == 1

    def test_parent_directory_does_not_cover_child(self, repo):
        repo.track("workers/admin/package-lock.json")
        repo.config([_npm("/workers")])
        (check_dependabot.REPO_ROOT / "workers").mkdir(exist_ok=True)
        assert check_dependabot.run_check() == 1

    def test_excluded_directory_is_skipped(self, repo, monkeypatch):
        repo.track("tests/electron/package-lock.json")
        repo.config([_npm("/")])
        monkeypatch.setattr(
            check_dependabot, "EXCLUDED", {"tests/electron": "test harness"}
        )
        assert check_dependabot.run_check() == 0

    def test_every_exclusion_carries_a_reason(self):
        for directory, reason in check_dependabot.EXCLUDED.items():
            assert reason.strip(), f"EXCLUDED[{directory!r}] has no reason"

    def test_entry_pointing_at_a_missing_directory_rejected(self, repo, capsys):
        repo.track()
        repo.config([_npm("/hub/agents/email/node")])
        assert check_dependabot.run_check() == 1
        assert "does not exist" in capsys.readouterr().err

    def test_git_failure_is_loud(self, monkeypatch, tmp_path):
        monkeypatch.setattr(check_dependabot, "REPO_ROOT", tmp_path / "missing")
        with pytest.raises(RuntimeError, match="git ls-files"):
            _REAL_TRACKED_MANIFESTS()


class TestRealRepository:
    def test_real_config_covers_every_tracked_lockfile(self, monkeypatch):
        """Regression guard for #3525 against the real dependabot.yml and tree."""
        monkeypatch.setattr(
            check_dependabot, "_tracked_manifests", _REAL_TRACKED_MANIFESTS
        )
        monkeypatch.setattr(
            check_dependabot, "_directory_exists", _REAL_DIRECTORY_EXISTS
        )
        monkeypatch.setattr(
            check_dependabot, "CONFIG", REPO_ROOT / ".github" / "dependabot.yml"
        )
        assert check_dependabot.run_check() == 0

    def test_real_listing_finds_the_published_surfaces(self):
        found = set(_REAL_TRACKED_MANIFESTS())
        for path in (
            "tui/go.mod",
            "website/package-lock.json",
            "hub/agents/gaia/npm/package-lock.json",
        ):
            assert path in found
        assert not any("node_modules/" in p for p in found)
