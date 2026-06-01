# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the activation file-watcher (#1226).

The watcher closes the cross-process gap: a ``gaia connectors activations``
CLI write lands in ``~/.gaia/connectors/activations.json`` from a separate
process, and the long-running UI server polls that ledger to emit
``connector.activation.changed`` to its SSE clients. The autouse ``_autouse_
isolate_home`` fixture in ``conftest.py`` already redirects the ledger path to
a per-test ``tmp_path``.
"""

from __future__ import annotations

import pytest

from gaia.connectors import activations, events
from gaia.connectors.activation_watcher import ActivationWatcher, diff_ledgers


class _RecordingEmitter:
    def __init__(self):
        self.events = []

    async def emit(self, event_type, payload):
        self.events.append((event_type, dict(payload)))


@pytest.fixture
def recording_emitter():
    rec = _RecordingEmitter()
    events.set_emitter(rec)
    try:
        yield rec
    finally:
        events.reset_emitter()


class TestDiffLedgers:
    def test_activation_added(self):
        assert diff_ledgers({}, {"mcp-x": {"a": True}}) == [("mcp-x", "a", True)]

    def test_deactivation_removed(self):
        # Deactivate deletes the entry; absence => inactive.
        assert diff_ledgers({"mcp-x": {"a": True}}, {}) == [("mcp-x", "a", False)]

    def test_no_change_returns_empty(self):
        assert diff_ledgers({"mcp-x": {"a": True}}, {"mcp-x": {"a": True}}) == []

    def test_multiple_changes(self):
        old = {"mcp-x": {"a": True}}
        new = {"mcp-x": {"b": True}}
        assert sorted(diff_ledgers(old, new)) == [
            ("mcp-x", "a", False),
            ("mcp-x", "b", True),
        ]


class TestPollOnce:
    async def test_emits_on_external_activation(self, recording_emitter):
        watcher = ActivationWatcher()  # seeded with empty snapshot
        activations.activate_agent("mcp-x", "builtin:chat")

        changes = await watcher.poll_once()

        assert ("mcp-x", "builtin:chat", True) in changes
        assert (
            "connector.activation.changed",
            {"connector_id": "mcp-x", "agent_id": "builtin:chat", "active": True},
        ) in recording_emitter.events

    async def test_emits_on_external_deactivation(self, recording_emitter):
        activations.activate_agent("mcp-x", "builtin:chat")
        watcher = ActivationWatcher()
        # snapshot now includes the activation
        watcher.note_local_write("mcp-x", "builtin:chat", True)
        recording_emitter.events.clear()

        activations.deactivate_agent("mcp-x", "builtin:chat")
        changes = await watcher.poll_once()

        assert ("mcp-x", "builtin:chat", False) in changes
        assert (
            "connector.activation.changed",
            {"connector_id": "mcp-x", "agent_id": "builtin:chat", "active": False},
        ) in recording_emitter.events

    async def test_no_change_emits_nothing(self, recording_emitter):
        watcher = ActivationWatcher()
        assert await watcher.poll_once() == []
        assert recording_emitter.events == []

    async def test_note_local_write_suppresses_duplicate(self, recording_emitter):
        # Simulates an in-process write that already emitted: advancing the
        # snapshot must make the next poll a no-op (no double event).
        watcher = ActivationWatcher()
        activations.activate_agent("mcp-x", "builtin:chat")
        watcher.note_local_write("mcp-x", "builtin:chat", True)

        assert await watcher.poll_once() == []
        assert recording_emitter.events == []

    async def test_note_local_write_does_not_mask_other_pair(self, recording_emitter):
        # An in-process write to one pair must not swallow a concurrent
        # out-of-process change to a *different* pair.
        watcher = ActivationWatcher()
        activations.activate_agent("mcp-x", "builtin:chat")  # in-process write
        watcher.note_local_write("mcp-x", "builtin:chat", True)
        activations.activate_agent("mcp-x", "builtin:code")  # external write

        changes = await watcher.poll_once()

        assert changes == [("mcp-x", "builtin:code", True)]
