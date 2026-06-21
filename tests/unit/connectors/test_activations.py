# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Per-agent activations ledger at ``~/.gaia/connectors/activations.json``
(issue #1005).

Coverage mirrors ``test_grants.py``:

- ``activate_agent`` writes the file at the right path with mode 0600 and
  parent dir 0700 (POSIX); skipped on Windows where POSIX modes do not apply.
- Atomic write via ``tempfile.mkstemp`` + ``os.replace`` — no leftover
  tempfiles after a successful write.
- Round-trip through ``deactivate_agent`` and ``list_agent_activations``.
- ``is_agent_active`` returns False for absent entries (least-privilege
  opt-in) and True after activation.
- A corrupted ledger raises ``ConnectorsError`` with an actionable message
  naming the file path and the ``rm`` recovery command.
- Concurrent activate calls do not corrupt the file (per-process lock).
- Namespaced agent IDs (builtin vs custom with same bare id) are isolated.
- Schema version is persisted and parsed.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

from gaia.connectors.activations import (
    ACTIVATIONS_FILE,
    SCHEMA_VERSION,
    activate_agent,
    deactivate_agent,
    is_agent_active,
    list_agent_activations,
    load_activations,
    revoke_all_activations_for,
)
from gaia.connectors.errors import ConnectorsError


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setattr("gaia.connectors.activations.Path.home", lambda: tmp_path)
    return tmp_path


def _activations_path(home):
    return home / ".gaia" / "connectors" / "activations.json"


class TestPathAndMode:
    def test_activate_creates_file_at_correct_path(self, fake_home):
        activate_agent("github", "builtin:chat")
        assert _activations_path(fake_home).exists()

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes only")
    def test_file_mode_0600(self, fake_home):
        activate_agent("github", "builtin:chat")
        path = _activations_path(fake_home)
        assert os.stat(path).st_mode & 0o777 == 0o600

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes only")
    def test_parent_dir_mode_0700(self, fake_home):
        activate_agent("github", "builtin:chat")
        path = _activations_path(fake_home)
        assert os.stat(path.parent).st_mode & 0o777 == 0o700

    def test_activations_file_constant_matches_path_shape(self, fake_home):
        assert "connectors" in str(ACTIVATIONS_FILE)
        assert str(ACTIVATIONS_FILE).endswith("activations.json")


class TestRoundTrip:
    def test_activate_then_list(self, fake_home):
        activate_agent("github", "builtin:chat")
        assert list_agent_activations("github") == {"builtin:chat": True}

    def test_two_agents_independent(self, fake_home):
        activate_agent("github", "builtin:chat")
        activate_agent("github", "custom:abc:inbox")
        assert list_agent_activations("github") == {
            "builtin:chat": True,
            "custom:abc:inbox": True,
        }

    def test_deactivate_removes_only_target(self, fake_home):
        activate_agent("github", "builtin:chat")
        activate_agent("github", "custom:abc:inbox")
        deactivate_agent("github", "builtin:chat")
        assert list_agent_activations("github") == {"custom:abc:inbox": True}

    def test_deactivate_unknown_is_idempotent(self, fake_home):
        deactivate_agent("github", "nobody")  # must not raise

    def test_activate_is_idempotent(self, fake_home):
        activate_agent("github", "builtin:chat")
        activate_agent("github", "builtin:chat")
        assert list_agent_activations("github") == {"builtin:chat": True}

    def test_load_activations_empty_when_no_file(self, fake_home):
        assert load_activations() == {}

    def test_deactivate_then_reactivate(self, fake_home):
        activate_agent("github", "builtin:chat")
        deactivate_agent("github", "builtin:chat")
        assert not is_agent_active("github", "builtin:chat")
        activate_agent("github", "builtin:chat")
        assert is_agent_active("github", "builtin:chat")


class TestIsActive:
    def test_absent_returns_false(self, fake_home):
        # Least-privilege opt-in: absence means inactive.
        assert is_agent_active("github", "builtin:chat") is False

    def test_true_after_activate(self, fake_home):
        activate_agent("github", "builtin:chat")
        assert is_agent_active("github", "builtin:chat") is True

    def test_false_after_deactivate(self, fake_home):
        activate_agent("github", "builtin:chat")
        deactivate_agent("github", "builtin:chat")
        assert is_agent_active("github", "builtin:chat") is False

    def test_unrelated_connector_unaffected(self, fake_home):
        activate_agent("github", "builtin:chat")
        assert is_agent_active("filesystem", "builtin:chat") is False


class TestRevokeAllForConnector:
    def test_revoke_all_clears_every_agent(self, fake_home):
        activate_agent("github", "builtin:chat")
        activate_agent("github", "builtin:code")
        revoked = revoke_all_activations_for("github")
        assert sorted(revoked) == ["builtin:chat", "builtin:code"]
        assert list_agent_activations("github") == {}

    def test_revoke_all_for_unknown_is_noop(self, fake_home):
        assert revoke_all_activations_for("nonexistent") == []

    def test_revoke_all_does_not_touch_other_connectors(self, fake_home):
        activate_agent("github", "builtin:chat")
        activate_agent("filesystem", "builtin:chat")
        revoke_all_activations_for("github")
        assert list_agent_activations("filesystem") == {"builtin:chat": True}


class TestAtomicity:
    def test_atomic_replace_does_not_leave_tempfile(self, fake_home):
        activate_agent("github", "builtin:chat")
        connectors_dir = _activations_path(fake_home).parent
        leftovers = [p.name for p in connectors_dir.iterdir() if p.suffix == ".tmp"]
        assert leftovers == [], f"unexpected tempfile leftovers: {leftovers}"

    def test_concurrent_activations_do_not_corrupt(self, fake_home):
        async def driver():
            await asyncio.gather(
                *[
                    asyncio.to_thread(activate_agent, "github", f"agent_{i}")
                    for i in range(20)
                ]
            )

        asyncio.run(driver())
        listing = list_agent_activations("github")
        assert len(listing) == 20
        for i in range(20):
            assert listing[f"agent_{i}"] is True


class TestSchemaVersion:
    def test_written_file_carries_version_field(self, fake_home):
        activate_agent("github", "builtin:chat")
        path = _activations_path(fake_home)
        on_disk = json.loads(path.read_text())
        assert on_disk["version"] == SCHEMA_VERSION
        assert on_disk["activations"] == {"github": {"builtin:chat": True}}

    def test_loader_tolerates_unknown_future_version(self, fake_home):
        # Forward-compat: a reader on schema v1 must keep working if a
        # future writer bumps the version field and adds keys we ignore.
        path = _activations_path(fake_home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "version": 99,
                    "activations": {"github": {"builtin:chat": True}},
                    "future_field": "ignored",
                }
            )
        )
        assert list_agent_activations("github") == {"builtin:chat": True}


class TestCorruptedFileRecovery:
    def test_corrupted_activations_raises_actionable_error(self, fake_home):
        path = _activations_path(fake_home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ this is not valid json")

        with pytest.raises(ConnectorsError) as exc:
            load_activations()

        msg = str(exc.value)
        assert str(path) in msg
        assert "rm" in msg.lower()

    def test_wrong_top_level_shape_raises_actionable_error(self, fake_home):
        path = _activations_path(fake_home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(["this", "is", "a", "list"]))

        with pytest.raises(ConnectorsError) as exc:
            load_activations()
        assert str(path) in str(exc.value)


class TestNamespacedAgentIds:
    def test_builtin_and_custom_with_same_aid_are_separate(self, fake_home):
        activate_agent("github", "builtin:chat")
        activate_agent("github", "custom:abc:chat")
        listing = list_agent_activations("github")
        assert listing == {
            "builtin:chat": True,
            "custom:abc:chat": True,
        }

    def test_deactivate_one_does_not_affect_other(self, fake_home):
        activate_agent("github", "builtin:chat")
        activate_agent("github", "custom:abc:chat")
        deactivate_agent("github", "custom:abc:chat")
        listing = list_agent_activations("github")
        assert "builtin:chat" in listing
        assert "custom:abc:chat" not in listing
