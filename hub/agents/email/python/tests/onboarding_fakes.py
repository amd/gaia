# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Shared fakes for the mailbox-onboarding test modules (#2469, #2590).

A uniquely-named module, NOT ``conftest.py`` — CI (``test_email_agent.yml``)
runs pytest over several roots in one command (this dir,
``tests/unit/email/``, ``tests/unit/agents/email/``, ...). Under prepend
import mode every one of those directories lands on ``sys.path``, so a bare
``from conftest import ...`` is ambiguous across roots and whichever
same-named ``conftest.py`` sys.path resolves first wins — silently importing
the WRONG fakes (or failing outright) for every root but one. ``conftest.py``
itself is fine (pytest discovers it per-directory, never imported by name);
only an explicit cross-file import of it is the trap. A uniquely-named
module has no such collision.
"""

from __future__ import annotations

from gaia_agent_email import question as q


class ScriptedConsole:
    """Records every question asked and replies from a fixed script.

    Shared by every mailbox-onboarding test module (#2469, #2590) so the real
    ``question.ask()`` — its casefold option-matching, its strict-rejection
    ``ValueError``, its sensitive-echo suppression — always runs unmodified.
    A second, drifted copy of this fake is how a test module stops exercising
    that behaviour without anyone noticing.
    """

    def __init__(self, answers):
        self.answers = list(answers)
        self.asked = []
        self.info = []

    def request_user_input_blocking(self, **kwargs):
        self.asked.append(kwargs)
        if not self.answers:
            return q.NO_RESPONSE
        return self.answers.pop(0)

    def print_info(self, message):
        self.info.append(message)


class FakeAgent:
    def __init__(self, answers=(), can_answer_questions=True):
        self.console = ScriptedConsole(answers)
        self.can_answer_questions = can_answer_questions


__all__ = ["FakeAgent", "ScriptedConsole"]
