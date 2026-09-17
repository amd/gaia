# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for the global agent step-limit knob.

``default_max_steps()`` is the single source of truth for how many
reasoning/tool steps an agent may take. Every agent config inherits it via
``field(default_factory=default_max_steps)``, so these tests pin the
resolution rules: the built-in default, the ``GAIA_AGENT_MAX_STEPS`` runtime
override, and loud failure on a typo'd value (no silent capping).
"""

import os
import unittest
from unittest import mock

from gaia.agents.base.agent import DEFAULT_MAX_STEPS, default_max_steps


class TestDefaultMaxSteps(unittest.TestCase):
    def test_unset_returns_builtin_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GAIA_AGENT_MAX_STEPS", None)
            self.assertEqual(default_max_steps(), DEFAULT_MAX_STEPS)

    def test_env_override_is_honored(self):
        with mock.patch.dict(os.environ, {"GAIA_AGENT_MAX_STEPS": "123"}):
            self.assertEqual(default_max_steps(), 123)

    def test_empty_env_falls_back_to_default(self):
        with mock.patch.dict(os.environ, {"GAIA_AGENT_MAX_STEPS": ""}):
            self.assertEqual(default_max_steps(), DEFAULT_MAX_STEPS)

    def test_non_integer_raises_loudly(self):
        with mock.patch.dict(os.environ, {"GAIA_AGENT_MAX_STEPS": "lots"}):
            with self.assertRaises(ValueError):
                default_max_steps()

    def test_zero_means_no_limit(self):
        """0 is a real setting, not an error: it removes the ceiling.

        A fixed cap stopped the majority of real multi-step work mid-task — the
        median bug fix runs past 50 steps — so unlimited is the default and 0
        is how a caller asks for it explicitly.
        """
        with mock.patch.dict(os.environ, {"GAIA_AGENT_MAX_STEPS": "0"}):
            self.assertEqual(default_max_steps(), 0)

    def test_the_default_is_no_limit(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            os.environ.pop("GAIA_AGENT_MAX_STEPS", None)
            self.assertEqual(default_max_steps(), 0)

    def test_negative_raises_loudly(self):
        """A negative is a typo, and silently capping on one hides it."""
        for bad in ("-1", "-5", "not-a-number"):
            with mock.patch.dict(os.environ, {"GAIA_AGENT_MAX_STEPS": bad}):
                with self.assertRaises(ValueError):
                    default_max_steps()

    def test_configs_inherit_the_override_at_construction(self):
        import pytest

        # ChatAgentConfig ships with the standalone gaia-agent-chat wheel (#1102).
        pytest.importorskip("gaia_agent_chat")

        from gaia_agent_chat.agent import ChatAgentConfig

        from gaia.agents.builder.agent import BuilderAgentConfig

        with mock.patch.dict(os.environ, {"GAIA_AGENT_MAX_STEPS": "42"}):
            self.assertEqual(ChatAgentConfig().max_steps, 42)
            self.assertEqual(BuilderAgentConfig().max_steps, 42)


if __name__ == "__main__":
    unittest.main()


class TestNoLimitIsHonouredByTheLoop(unittest.TestCase):
    """The sentinel is only useful if the loop guard actually respects it."""

    def test_zero_does_not_terminate_the_loop_immediately(self):
        """A naive ``steps_taken < steps_limit`` exits at once when limit is 0.

        That would turn "no limit" into "no steps", so pin the guard's shape.
        """
        from gaia.agents.base.agent import NO_STEP_LIMIT

        def keep_going(steps_taken, steps_limit):
            return steps_limit == NO_STEP_LIMIT or steps_taken < steps_limit

        self.assertTrue(keep_going(0, NO_STEP_LIMIT))
        self.assertTrue(keep_going(500, NO_STEP_LIMIT))
        # An explicit ceiling still stops.
        self.assertTrue(keep_going(49, 50))
        self.assertFalse(keep_going(50, 50))

    def test_every_step_comparison_in_the_source_is_guarded(self):
        """No comparison against the limit may run unguarded.

        With no limit the sentinel is 0, so a bare ``steps_taken < steps_limit - 1``
        compares against -1 and is never true — the branch silently stops firing
        instead of failing. Two real cases were found this way.

        Matching is done over normalised source rather than a fixed regex,
        because the formatter wraps long conditions across lines.
        """
        import inspect
        import re

        from gaia.agents.base import agent as agent_mod

        # Collapse whitespace so a wrapped condition reads as one line.
        src = re.sub(r"\s+", " ", inspect.getsource(agent_mod))
        unguarded = []
        for m in re.finditer(r"steps_taken < steps_limit", src):
            window = src[max(0, m.start() - 120) : m.start()]
            if "NO_STEP_LIMIT" not in window:
                unguarded.append(src[max(0, m.start() - 90) : m.end() + 10])
        self.assertEqual(
            unguarded, [], f"{len(unguarded)} unguarded step comparison(s)"
        )
