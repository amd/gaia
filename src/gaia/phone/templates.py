# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The fixed sentences a call or text may carry, and the call scripts built from them.

Nothing a task, email or agent wrote is ever spoken or texted: an attacker who
can put words in a title could otherwise speak through GAIA, and voicemail
would keep them.
"""

from __future__ import annotations

from typing import Dict
from xml.sax.saxutils import escape

ALERT_KINDS = ("schedule_failed", "approval_waiting", "gaia_down", "agent", "test")

_ALERT_LINES: Dict[str, str] = {
    "schedule_failed": "A scheduled job failed.",
    "approval_waiting": "A task is waiting for your approval.",
    "gaia_down": "GAIA can't reach its model server.",
    "agent": "An agent needs your attention.",
    "test": "This is a test alert.",
}

_TITLES: Dict[str, str] = {
    "schedule_failed": "Scheduled job failed",
    "approval_waiting": "Waiting for your approval",
    "gaia_down": "GAIA can't run",
    "agent": "An agent needs you",
    "test": "Test alert",
}

ADDED_LINE = (
    "A phone number was added to your GAIA phone book. If you didn't do this, "
    "open GAIA now."
)
REMOVED_LINE = (
    "A phone number was removed from your GAIA phone book. If you didn't do this, "
    "open GAIA now."
)


def alert_line(kind: str) -> str:
    if kind not in _ALERT_LINES:
        raise ValueError(f"unknown alert kind {kind!r}")
    return _ALERT_LINES[kind]


def alert_title(kind: str) -> str:
    return _TITLES[kind]


def alert_call_sentence(kind: str) -> str:
    return f"This is GAIA on your PC. {alert_line(kind)} Open GAIA to see it."


def alert_text(kind: str) -> str:
    return f"GAIA: {alert_line(kind)} Open GAIA to see it."


def code_sentence(code: str) -> str:
    spoken = ", ".join(code)
    return f"This is GAIA. Your verification code is {spoken}. Again, {spoken}."


def code_text(code: str) -> str:
    return f"GAIA verification code: {code}. It expires in 10 minutes."


def notice_sentence(line: str) -> str:
    return f"This is GAIA on your PC. {line}"


def call_script(sentence: str, repeat: int = 2) -> str:
    """TwiML that says *sentence* *repeat* times, then hangs up."""
    said = escape(sentence)
    body = '<Pause length="1"/>'.join(f"<Say>{said}</Say>" for _ in range(repeat))
    return f"<Response>{body}<Hangup/></Response>"
