# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
T-6a (AC7, A7): per-agent grants ledger at ``~/.gaia/connections/grants.json``.

Coverage:
- ``grant_agent`` writes the file at the right path with mode 0600 and
  parent dir 0700 (POSIX); xfail on Windows where POSIX modes don't apply.
- Atomic write via ``tempfile.mkstemp`` + ``os.replace`` — no
  ``FileExistsError`` on Windows, no half-written file on crash.
- Round-trip through ``revoke_agent_grant`` and ``list_agent_grants``.
- ``check_agent_grant`` returns True only when granted scopes cover required.
- A corrupted ``grants.json`` raises ``ConnectionsError`` with an actionable
  message naming the file path and the ``rm`` recovery command.
- Concurrent grant calls don't corrupt the file (per-process lock).
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

from gaia.connections.errors import ConnectionsError
from gaia.connections.grants import (
    GRANTS_FILE,
    check_agent_grant,
    grant_agent,
    list_agent_grants,
    load_grants,
    revoke_agent_grant,
)


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setattr("gaia.connections.grants.Path.home", lambda: tmp_path)
    return tmp_path


def _grants_path(home):
    return home / ".gaia" / "connections" / "grants.json"


class TestPathAndMode:
    def test_grant_creates_file_at_correct_path(self, fake_home):
        grant_agent("google", "builtin:chat", ["s1"])
        path = _grants_path(fake_home)
        assert path.exists()

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes only")
    def test_file_mode_0600(self, fake_home):
        grant_agent("google", "builtin:chat", ["s1"])
        path = _grants_path(fake_home)
        mode = os.stat(path).st_mode & 0o777
        assert mode == 0o600

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes only")
    def test_parent_dir_mode_0700(self, fake_home):
        grant_agent("google", "builtin:chat", ["s1"])
        path = _grants_path(fake_home)
        mode = os.stat(path.parent).st_mode & 0o777
        assert mode == 0o700

    def test_grants_file_constant_matches_runtime_path(self, fake_home):
        # The exported GRANTS_FILE constant resolves at module-load time;
        # tests monkeypatch Path.home AFTER import. Use the function path
        # in tests, but verify the constant is what callers expect.
        assert "connections" in str(GRANTS_FILE)
        assert str(GRANTS_FILE).endswith("grants.json")


class TestRoundTrip:
    def test_grant_then_list(self, fake_home):
        grant_agent("google", "builtin:chat", ["s1", "s2"])
        listing = list_agent_grants("google")
        assert listing == {"builtin:chat": ["s1", "s2"]}

    def test_two_agents_independent(self, fake_home):
        grant_agent("google", "builtin:chat", ["s1"])
        grant_agent("google", "custom:abc:inbox", ["s2"])
        listing = list_agent_grants("google")
        assert listing == {
            "builtin:chat": ["s1"],
            "custom:abc:inbox": ["s2"],
        }

    def test_revoke_removes_only_target(self, fake_home):
        grant_agent("google", "builtin:chat", ["s1"])
        grant_agent("google", "custom:abc:inbox", ["s2"])
        revoke_agent_grant("google", "builtin:chat")
        listing = list_agent_grants("google")
        assert listing == {"custom:abc:inbox": ["s2"]}

    def test_revoke_unknown_is_idempotent(self, fake_home):
        revoke_agent_grant("google", "nonexistent")  # must not raise

    def test_grant_overwrites_prior_scopes(self, fake_home):
        grant_agent("google", "builtin:chat", ["s1"])
        grant_agent("google", "builtin:chat", ["s2", "s3"])
        listing = list_agent_grants("google")
        assert listing == {"builtin:chat": ["s2", "s3"]}

    def test_load_grants_empty_when_no_file(self, fake_home):
        assert load_grants() == {}


class TestCheckGrant:
    def test_no_grant_returns_false(self, fake_home):
        assert check_agent_grant("google", "builtin:chat", ["s1"]) is False

    def test_exact_scope_match_returns_true(self, fake_home):
        grant_agent("google", "builtin:chat", ["s1"])
        assert check_agent_grant("google", "builtin:chat", ["s1"]) is True

    def test_superset_grant_covers_subset_required(self, fake_home):
        grant_agent("google", "builtin:chat", ["s1", "s2"])
        assert check_agent_grant("google", "builtin:chat", ["s1"]) is True

    def test_missing_one_scope_returns_false(self, fake_home):
        grant_agent("google", "builtin:chat", ["s1"])
        assert check_agent_grant("google", "builtin:chat", ["s1", "s2"]) is False


class TestAtomicity:
    def test_atomic_replace_does_not_leave_tempfile(self, fake_home):
        # tempfile.mkstemp + os.replace must not leave any .grants_*.tmp
        # files in the connections dir after a successful write.
        grant_agent("google", "builtin:chat", ["s1"])
        connections_dir = _grants_path(fake_home).parent
        leftovers = [p.name for p in connections_dir.iterdir() if p.suffix == ".tmp"]
        assert leftovers == [], f"unexpected tempfile leftovers: {leftovers}"

    def test_concurrent_grants_do_not_corrupt(self, fake_home):
        # Run many grants concurrently from one event loop. The per-process
        # asyncio.Lock prevents interleaved writes from clobbering each other.
        async def driver():
            await asyncio.gather(
                *[
                    asyncio.to_thread(
                        grant_agent, "google", f"agent_{i}", [f"scope_{i}"]
                    )
                    for i in range(20)
                ]
            )

        asyncio.run(driver())
        listing = list_agent_grants("google")
        assert len(listing) == 20
        for i in range(20):
            assert listing[f"agent_{i}"] == [f"scope_{i}"]


class TestCorruptedFileRecovery:
    def test_corrupted_grants_raises_actionable_error(self, fake_home):
        # A7: a malformed JSON file must raise ConnectionsError naming the
        # exact path and the recovery command, not silently brick the
        # subsystem with KeyError on every call.
        path = _grants_path(fake_home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ this is not valid json")
        path.chmod(0o600)

        with pytest.raises(ConnectionsError) as exc:
            load_grants()

        msg = str(exc.value)
        assert str(path) in msg
        assert "rm" in msg.lower() or "delete" in msg.lower()


class TestNamespacedAgentIds:
    """Plan amendment A9: grants are keyed by namespaced agent id, not
    bare AGENT_ID. A custom agent claiming a built-in's AGENT_ID does not
    inherit the built-in's grants because the keys differ
    (``builtin:chat`` vs ``custom:abc:chat``)."""

    def test_builtin_and_custom_with_same_aid_are_separate(self, fake_home):
        grant_agent("google", "builtin:chat", ["builtin-scope"])
        grant_agent("google", "custom:abc:chat", ["custom-scope"])
        listing = list_agent_grants("google")
        assert listing == {
            "builtin:chat": ["builtin-scope"],
            "custom:abc:chat": ["custom-scope"],
        }

    def test_revoke_one_does_not_affect_other(self, fake_home):
        grant_agent("google", "builtin:chat", ["b"])
        grant_agent("google", "custom:abc:chat", ["c"])
        revoke_agent_grant("google", "custom:abc:chat")
        listing = list_agent_grants("google")
        assert "builtin:chat" in listing
        assert "custom:abc:chat" not in listing
