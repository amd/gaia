# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An approval card left unanswered raises the phone alert, and answering clears it."""

import threading

from gaia.ui.approval_alerts import ApprovalWatch
from gaia.ui.sse_handler import SSEOutputHandler


def _watch(minutes):
    raised, cleared = [], []
    fired = threading.Event()

    def on_raise(key):
        raised.append(key)
        fired.set()

    watch = ApprovalWatch(
        wait_minutes=lambda: minutes, raise_alert=on_raise, clear=cleared.append
    )
    return watch, raised, cleared, fired


def test_unanswered_card_alerts_then_answer_clears():
    watch, raised, cleared, fired = _watch(0.001)
    watch.shown("approval:a")
    assert fired.wait(5)
    watch.answered("approval:a")
    assert raised == ["approval:a"] and cleared == ["approval:a"]


def test_answered_in_time_raises_nothing():
    watch, raised, cleared, _ = _watch(0.05)
    watch.shown("approval:b")
    watch.answered("approval:b")
    threading.Event().wait(3.5)
    assert raised == [] and cleared == []


def test_phone_off_never_watches():
    watch, raised, _, _ = _watch(None)
    watch.shown("approval:c")
    threading.Event().wait(0.2)
    assert raised == []


def test_handler_reports_cards(monkeypatch):
    from gaia.ui import approval_alerts

    seen = []

    class Fake:
        def shown(self, key):
            seen.append(("shown", key))

        def answered(self, key):
            seen.append(("answered", key))

    monkeypatch.setattr(approval_alerts, "WATCH", Fake())
    handler = SSEOutputHandler()
    handler._emit({"type": "permission_request", "tool": "run_shell_command"})
    handler.signal_done()
    key = handler._approval_key
    assert seen[0] == ("shown", key) and ("answered", key) in seen
    background = SSEOutputHandler(background_mode=True)
    seen.clear()
    background._emit({"type": "permission_request", "tool": "x"})
    assert ("shown", background._approval_key) not in seen
