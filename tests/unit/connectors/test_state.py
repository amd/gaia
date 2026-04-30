# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
T-2 unit tests — state.json atomic store (gaia.connectors.state).

Mirrors the structure of test_grants.py: isolated fake home, atomic write
checks, error handling.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from gaia.connectors.errors import ConnectorsError
from gaia.connectors.state import (
    STATE_FILE,
    clear_connector_state,
    get_connector_state,
    list_configured_ids,
    load_state,
    set_connector_state,
)


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setattr("gaia.connectors.state.Path.home", lambda: tmp_path)
    return tmp_path


def _state_path(home: Path) -> Path:
    return home / ".gaia" / "connectors" / "state.json"


class TestPathAndMode:
    def test_creates_file_at_correct_path(self, fake_home):
        set_connector_state("google", configured=True)
        assert _state_path(fake_home).exists()

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes only")
    def test_file_mode_0600(self, fake_home):
        set_connector_state("google", configured=True)
        mode = os.stat(_state_path(fake_home)).st_mode & 0o777
        assert mode == 0o600

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes only")
    def test_parent_dir_mode_0700(self, fake_home):
        set_connector_state("google", configured=True)
        mode = os.stat(_state_path(fake_home).parent).st_mode & 0o777
        assert mode == 0o700

    def test_state_file_constant_shape(self):
        assert "connectors" in str(STATE_FILE)
        assert str(STATE_FILE).endswith("state.json")


class TestSetAndGet:
    def test_set_then_get(self):
        set_connector_state(
            "google",
            configured=True,
            account_id="alice@example.com",
            scopes=["openid", "email"],
        )
        entry = get_connector_state("google")
        assert entry is not None
        assert entry["configured"] is True
        assert entry["account_id"] == "alice@example.com"
        assert entry["scopes"] == ["openid", "email"]

    def test_get_absent_returns_none(self):
        assert get_connector_state("github") is None

    def test_set_merges_with_existing(self):
        set_connector_state("google", configured=True, account_id="a@b.com")
        set_connector_state("google", configured=True, scopes=["openid"])
        entry = get_connector_state("google")
        assert entry["account_id"] == "a@b.com"
        assert entry["scopes"] == ["openid"]

    def test_set_non_secret_fields(self):
        set_connector_state(
            "github-mcp",
            configured=True,
            non_secret_fields={"base_url": "https://api.github.com"},
        )
        entry = get_connector_state("github-mcp")
        assert entry["non_secret_fields"]["base_url"] == "https://api.github.com"

    def test_set_last_tested_at(self):
        set_connector_state(
            "google", configured=True, last_tested_at="2026-04-30T00:00:00Z"
        )
        entry = get_connector_state("google")
        assert entry["last_tested_at"] == "2026-04-30T00:00:00Z"

    def test_multiple_connectors_independent(self):
        set_connector_state("google", configured=True, account_id="a@b.com")
        set_connector_state("github-mcp", configured=True, account_id="gh-user")
        assert get_connector_state("google")["account_id"] == "a@b.com"
        assert get_connector_state("github-mcp")["account_id"] == "gh-user"


class TestClear:
    def test_clear_removes_entry(self):
        set_connector_state("google", configured=True)
        clear_connector_state("google")
        assert get_connector_state("google") is None

    def test_clear_idempotent(self):
        clear_connector_state("google")  # no-op — should not raise

    def test_clear_leaves_others_intact(self):
        set_connector_state("google", configured=True)
        set_connector_state("github-mcp", configured=True)
        clear_connector_state("google")
        assert get_connector_state("github-mcp") is not None


class TestListConfigured:
    def test_empty_returns_empty(self):
        assert list_configured_ids() == []

    def test_returns_only_configured(self):
        set_connector_state("google", configured=True)
        set_connector_state("github-mcp", configured=False)
        configured = list_configured_ids()
        assert "google" in configured
        assert "github-mcp" not in configured

    def test_after_clear_not_listed(self):
        set_connector_state("google", configured=True)
        clear_connector_state("google")
        assert "google" not in list_configured_ids()


class TestErrorHandling:
    def test_corrupted_json_raises_connectors_error(self, fake_home):
        path = _state_path(fake_home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ not valid json", encoding="utf-8")
        with pytest.raises(ConnectorsError, match="corrupted"):
            load_state()

    def test_wrong_type_raises_connectors_error(self, fake_home):
        path = _state_path(fake_home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        with pytest.raises(ConnectorsError, match="unexpected top-level type"):
            load_state()

    def test_missing_file_returns_empty(self):
        assert load_state() == {}
