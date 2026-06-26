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

    def test_non_positive_raises_loudly(self):
        for bad in ("0", "-5"):
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
