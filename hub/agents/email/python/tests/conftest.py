# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Shared fixtures for the gaia-agent-email package's own test suite.

Resets ``gaia_agent_email.model_select``'s success-only cache before AND
after every test in this directory, so a cached resolution from one test
can never silently short-circuit another test's fake ``requests.get``
(order-dependent flakiness).
"""

import pytest
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


@pytest.fixture(autouse=True)
def _reset_model_select_cache_between_tests():
    # ``model_select`` does not exist yet at RED time (#1439) -- this
    # autouse fixture must not break every OTHER already-passing test in
    # this directory by erroring at setup before the module lands.
    try:
        from gaia_agent_email.model_select import _reset_model_select_cache
    except ImportError:
        yield
        return
    _reset_model_select_cache()
    yield
    _reset_model_select_cache()
