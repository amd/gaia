# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Live-browser automation for GAIA agents.

``gaia.browser`` drives a real Chromium through Playwright's Python API for
the two things the lightweight ``gaia.web`` fetcher cannot do: pages behind a
sign-in, and pages that only exist after JavaScript runs.

The agent-facing surface is
:class:`~gaia.agents.tools.browser_use_tools.BrowserUseToolsMixin`.
"""

from gaia.browser.errors import (
    BrowserError,
    BrowserLaunchFailed,
    BrowserNotInstalled,
    BrowserNotStarted,
    ElementNotFound,
    InteractionFailed,
    LoginTimedOut,
    NavigationBlocked,
    NavigationFailed,
    SessionStoreError,
)

__all__ = [
    "BrowserError",
    "BrowserLaunchFailed",
    "BrowserNotInstalled",
    "BrowserNotStarted",
    "ElementNotFound",
    "InteractionFailed",
    "LoginTimedOut",
    "NavigationBlocked",
    "NavigationFailed",
    "SessionStoreError",
]
