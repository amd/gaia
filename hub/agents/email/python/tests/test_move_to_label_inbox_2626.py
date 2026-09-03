# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Regression tests for restoring a message with ``move_to_label`` (#2626)."""

import json
from types import SimpleNamespace

import pytest

from gaia_agent_email import action_store
from gaia_agent_email.tools.organize_tools import OrganizeToolsMixin, move_to_label_impl

from gaia.agents.base.tools import _TOOL_REGISTRY, get_tool_metadata
from gaia.database.mixin import DatabaseMixin


class _FakeMailbox:
    def __init__(self):
        self.messages = {
            "outside": {"labelIds": ["Label_1"]},
            "inbox": {"labelIds": ["INBOX"]},
        }
        self.labels = [
            {"id": "INBOX", "name": "INBOX"},
            {"id": "Label_1", "name": "Archive target"},
        ]
        self.archive_calls = 0

    def list_labels(self):
        return list(self.labels)

    def get_message(self, message_id):
        return {"labelIds": list(self.messages[message_id]["labelIds"])}

    def add_label(self, message_id, label_id):
        labels = self.messages[message_id]["labelIds"]
        if label_id not in labels:
            labels.append(label_id)

    def archive_message(self, message_id):
        self.archive_calls += 1
        labels = self.messages[message_id]["labelIds"]
        if "INBOX" in labels:
            labels.remove("INBOX")


class _DB(DatabaseMixin):
    pass


def _make_db():
    db = _DB()
    db.init_db(":memory:")
    action_store.init_schema(db)
    return db


class _FakeAgent(OrganizeToolsMixin, DatabaseMixin):
    """Minimal host that registers the real organize tool closures."""

    def __init__(self, mailbox):
        self.config = SimpleNamespace(debug=False, undo_window_seconds=30)
        self._backends = {"google": mailbox}
        self._providers = {
            message_id: "google" for message_id in mailbox.messages
        }
        self._organize_batch_id = "test-batch"
        self._last_archive_batch_id = None
        self.init_db(":memory:")
        action_store.init_schema(self)
        self._register_organize_tools()

    def _organize_batch_threshold_exceeded(self):
        return False

    def _provider_for_message(self, message_id, mailbox=None):
        return self._providers[message_id]

    def _backend_for_message(self, message_id):
        return self._backends[self._providers[message_id]]

    def _record_organize_op(self, message_id, sender):
        pass


@pytest.fixture(autouse=True)
def _preserve_tool_registry():
    snapshot = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def test_move_to_label_inbox_restores_without_archiving():
    mailbox = _FakeMailbox()

    move_to_label_impl(mailbox, _make_db(), message_id="outside", label_id="INBOX")

    assert "INBOX" in mailbox.messages["outside"]["labelIds"]
    assert mailbox.archive_calls == 0


def test_move_to_label_non_inbox_target_still_archives():
    mailbox = _FakeMailbox()

    move_to_label_impl(
        mailbox, _make_db(), message_id="inbox", label_id="Archive target"
    )

    assert "Label_1" in mailbox.messages["inbox"]["labelIds"]
    assert "INBOX" not in mailbox.messages["inbox"]["labelIds"]
    assert mailbox.archive_calls == 1


def test_move_to_label_batch_restores_every_message_without_archiving():
    mailbox = _FakeMailbox()
    mailbox.messages.update(
        {
            "outside-2": {"labelIds": ["Label_1"]},
            "outside-3": {"labelIds": ["Label_1"]},
        }
    )
    agent = _FakeAgent(mailbox)

    move_batch = get_tool_metadata("move_to_label_batch")["function"]
    result = json.loads(move_batch(["outside", "outside-2", "outside-3"], "INBOX"))

    assert result["ok"] is True, result
    assert len(result["data"]["succeeded"]) == 3
    assert result["data"]["failed"] == []
    assert all(
        "INBOX" in mailbox.messages[mid]["labelIds"]
        for mid in ("outside", "outside-2", "outside-3")
    )
    assert mailbox.archive_calls == 0

    agent.close_db()
