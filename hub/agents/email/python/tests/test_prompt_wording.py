# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Locks two coverage-narration prohibitions into ``_SYSTEM_PROMPT``.

A prior merge between two independently-rewritten copies of the PRE-SCAN
BEHAVIOR paragraph silently dropped one branch's guidance sentences — no
conflict, no marker, both PRs green. These assertions check for the
*substance* of the prohibitions (not exact sentences, so a reword doesn't
break them) so a future merge that drops them fails a test instead of
passing silently.
"""

import pytest

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.agent import _SYSTEM_PROMPT  # noqa: E402


def test_scanned_and_total_unread_stated_as_separate_facts():
    assert "not a fraction" in _SYSTEM_PROMPT
    assert "X of Y unread" in _SYSTEM_PROMPT  # named as the forbidden pattern


def test_cross_mailbox_overclaim_is_explicitly_forbidden():
    assert "across your mailboxes" in _SYSTEM_PROMPT
    assert "across your accounts" in _SYSTEM_PROMPT
