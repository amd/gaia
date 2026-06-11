# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for the grant migration logic: orphaned ``builtin:email`` grants must
be migrated to ``installed:email`` on startup.  Root cause 1 of #1592.

The migration runs in ``gaia.connectors.grants.migrate_legacy_agent_grants``.
It is idempotent and never overwrites an existing new-key grant.
"""

from __future__ import annotations

import pytest

from gaia.connectors.grants import (
    check_agent_grant,
    grant_agent,
    list_agent_grants,
    migrate_legacy_agent_grants,
)


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
    return tmp_path


class TestMigrateLegacyAgentGrants:
    def test_builtin_email_migrated_to_installed_email(self, fake_home):
        """Orphaned builtin:email -> installed:email on migration call."""
        scopes = ["https://www.googleapis.com/auth/gmail.readonly"]
        grant_agent("google", "builtin:email", scopes)

        migrate_legacy_agent_grants()

        listing = list_agent_grants("google")
        assert "installed:email" in listing
        assert listing["installed:email"] == scopes

    def test_migration_does_not_overwrite_existing_installed_email(self, fake_home):
        """If installed:email already exists, migration must not clobber it."""
        old_scopes = ["https://www.googleapis.com/auth/gmail.readonly"]
        new_scopes = ["https://www.googleapis.com/auth/gmail.modify"]
        grant_agent("google", "builtin:email", old_scopes)
        grant_agent("google", "installed:email", new_scopes)

        migrate_legacy_agent_grants()

        listing = list_agent_grants("google")
        # The existing installed:email entry is preserved unchanged.
        assert listing["installed:email"] == new_scopes

    def test_migration_is_idempotent(self, fake_home):
        """Calling migrate twice must not corrupt or duplicate entries."""
        scopes = ["https://www.googleapis.com/auth/gmail.readonly"]
        grant_agent("google", "builtin:email", scopes)

        migrate_legacy_agent_grants()
        migrate_legacy_agent_grants()

        listing = list_agent_grants("google")
        assert listing["installed:email"] == scopes

    def test_migration_no_op_when_no_legacy_grants(self, fake_home):
        """No grants.json or no builtin:email -- must not raise or create files."""
        migrate_legacy_agent_grants()  # must not raise

        listing = list_agent_grants("google")
        assert "installed:email" not in listing

    def test_migration_leaves_other_agents_untouched(self, fake_home):
        """Other grants (builtin:chat, custom:x:y) are not modified."""
        grant_agent("google", "builtin:email", ["s1"])
        grant_agent("google", "builtin:chat", ["s2"])
        grant_agent("google", "custom:abc:inbox", ["s3"])

        migrate_legacy_agent_grants()

        listing = list_agent_grants("google")
        assert listing["builtin:chat"] == ["s2"]
        assert listing["custom:abc:inbox"] == ["s3"]

    def test_check_agent_grant_passes_after_migration(self, fake_home):
        """After migration, check_agent_grant with installed:email returns True."""
        scopes = ["https://www.googleapis.com/auth/gmail.readonly"]
        grant_agent("google", "builtin:email", scopes)

        migrate_legacy_agent_grants()

        assert check_agent_grant("google", "installed:email", scopes) is True

    def test_builtin_email_removed_after_migration(self, fake_home):
        """After migration when no installed:email existed, builtin:email
        is removed to avoid duplicate-key confusion."""
        scopes = ["https://www.googleapis.com/auth/gmail.readonly"]
        grant_agent("google", "builtin:email", scopes)

        migrate_legacy_agent_grants()

        listing = list_agent_grants("google")
        # Old key must be gone -- only the new one remains.
        assert "builtin:email" not in listing
