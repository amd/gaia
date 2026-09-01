# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Regression tests for restoring a message with ``move_to_label`` (#2626)."""

from gaia_agent_email import action_store
from gaia_agent_email.tools.organize_tools import move_to_label_impl

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
