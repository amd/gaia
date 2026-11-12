# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The browser's missing-install remedy fits the kind of GAIA install (#7209).

The installed agent is a self-contained binary: pip has nothing to install into
and there is no `python` to run Playwright's installer, so naming either sends
the user down a dead end.
"""

import sys

import pytest

from gaia.browser.errors import BrowserLaunchFailed, BrowserNotInstalled


@pytest.fixture
def frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)


@pytest.fixture
def source(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)


def test_an_installed_build_is_never_told_to_run_pip(frozen):
    message = str(BrowserNotInstalled("No module named 'playwright'"))
    assert "pip install" not in message
    assert "python -m playwright" not in message
    assert "isn't bundled in this GAIA build" in message
    assert "Detail: No module named 'playwright'" in message


def test_a_source_install_gets_both_install_steps(source):
    message = str(BrowserNotInstalled())
    assert "[browser]" in message
    assert "-m playwright install chromium" in message
    assert sys.executable in message


def test_an_installed_build_is_not_told_to_repair_with_python(frozen):
    message = str(BrowserLaunchFailed("crashed"))
    assert "python -m playwright" not in message
    assert "GAIA_BROWSER_HEADLESS=1" in message


def test_a_source_install_can_repair_the_browser(source):
    assert "-m playwright install chromium" in str(BrowserLaunchFailed("crashed"))
