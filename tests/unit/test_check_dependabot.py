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


@pytest.fixture(autouse=True)
def patch_config(tmp_path, monkeypatch):
    """Allow each test to set check_dependabot.CONFIG before calling run_check()."""
    yield  # tests set check_dependabot.CONFIG themselves


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
            [{"package-ecosystem": "pip", "directory": "/", "schedule": {"interval": "weekly"}}],
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

    def test_error_message_names_ecosystem_and_directory(self, tmp_path, monkeypatch, capsys):
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
