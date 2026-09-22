# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Pins the session guard that stops the suite opening a real browser.

``connectors.flow.start_authorization`` fires the browser launch from a
fire-and-forget task, so a test's own ``monkeypatch`` can be torn down before
the launch runs and the real ``webbrowser.open`` gets called — popping a Google
consent screen on the developer's desktop mid-test-run.
"""

import webbrowser

import pytest


@pytest.mark.parametrize("launcher", ["open", "open_new", "open_new_tab"])
def test_browser_launchers_are_blocked(launcher):
    with pytest.raises(RuntimeError, match="tried to open a real browser"):
        getattr(webbrowser, launcher)("https://example.invalid")


def test_a_local_patch_restores_to_the_guard_not_the_real_launcher():
    """The teardown of a per-test patch must not re-arm the real launcher."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("webbrowser.open", lambda *_a, **_k: True)
        assert webbrowser.open("https://example.invalid") is True

    with pytest.raises(RuntimeError, match="tried to open a real browser"):
        webbrowser.open("https://example.invalid")
