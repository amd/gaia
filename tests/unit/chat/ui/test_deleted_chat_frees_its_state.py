# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Deleting a chat drops what the backend kept in memory for it.

These per-chat records were added on a chat's turns and never removed, so a
backend that stayed up held one of each for every chat it had ever served.
"""

from gaia.ui import _chat_helpers, session_environment

SID = "chat-to-delete"
OTHER = "chat-that-stays"


def test_a_deleted_chat_leaves_no_history_or_title_records(monkeypatch):
    for name, value in (
        ("_history_cuts", {SID: object(), OTHER: object()}),
        ("_relay_clips", {SID: {("a", 1)}, OTHER: set()}),
        ("_AUTO_TITLE_FAILURES", {SID: 2, OTHER: 1}),
        ("_AUTO_TITLE_ATTEMPTS", {SID: 3, OTHER: 1}),
        ("_AUTO_TITLE_SETTLED", {SID, OTHER}),
        ("_relayed_contexts", {SID: {"usage": {}}, OTHER: {"usage": {}}}),
    ):
        monkeypatch.setattr(_chat_helpers, name, value)

    _chat_helpers.forget_relayed_context(SID)

    for name in (
        "_history_cuts",
        "_relay_clips",
        "_AUTO_TITLE_FAILURES",
        "_AUTO_TITLE_ATTEMPTS",
        "_AUTO_TITLE_SETTLED",
        "_relayed_contexts",
    ):
        kept = getattr(_chat_helpers, name)
        assert SID not in kept, name
        assert OTHER in kept, name


def test_forgetting_a_chat_that_was_never_seen_is_harmless():
    _chat_helpers.forget_relayed_context("never-seen")
    session_environment.forget_session("never-seen")


def test_a_deleted_chat_leaves_no_switch_lock(monkeypatch):
    monkeypatch.setattr(session_environment, "_switch_locks", {})
    session_environment._switch_lock(SID)
    session_environment._switch_lock(OTHER)

    session_environment.forget_session(SID)

    assert set(session_environment._switch_locks) == {OTHER}
