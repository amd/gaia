# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for AnalystAgent (gaia_agent_analyst).

AnalystAgent isolates its tool set via ``_TOOL_REGISTRY.clear()`` +
``_snapshot_tools()``, so its instance registry contains exactly the
scratchpad tools — these tests assert that exact set. The scratchpad
DB is pointed at a temp path to avoid touching the user's home dir.
"""

import unittest

from gaia.testing import temp_directory


class TestAnalystAgentImport(unittest.TestCase):
    def test_can_import(self):
        from gaia_agent_analyst import AnalystAgent, AnalystAgentConfig

        self.assertIsNotNone(AnalystAgent)
        self.assertIsNotNone(AnalystAgentConfig)


class TestAnalystAgentConfig(unittest.TestCase):
    def test_defaults(self):
        from gaia_agent_analyst import AnalystAgentConfig

        from gaia.agents.base.agent import default_max_steps

        cfg = AnalystAgentConfig()
        self.assertFalse(cfg.use_claude)
        self.assertIsNone(cfg.model_id)
        self.assertEqual(cfg.max_steps, default_max_steps())
        self.assertTrue(cfg.scratchpad_db_path)


class TestAnalystAgentInit(unittest.TestCase):
    def _make(self, tmp_dir):
        from gaia_agent_analyst import AnalystAgent, AnalystAgentConfig

        cfg = AnalystAgentConfig(scratchpad_db_path=str(tmp_dir / "scratchpad.db"))
        return AnalystAgent(cfg)

    def test_constructs_without_backend(self):
        with temp_directory() as tmp_dir:
            agent = self._make(tmp_dir)
            try:
                self.assertIsNotNone(agent)
            finally:
                agent.close()

    def test_system_prompt_mentions_scratchpad(self):
        with temp_directory() as tmp_dir:
            agent = self._make(tmp_dir)
            try:
                prompt = agent._get_system_prompt()
                self.assertIn("AnalystAgent", prompt)
                self.assertIn("scratchpad", prompt.lower())
            finally:
                agent.close()

    def test_isolated_tool_set_is_scratchpad_only(self):
        with temp_directory() as tmp_dir:
            agent = self._make(tmp_dir)
            try:
                self.assertEqual(
                    set(agent._tools_registry),
                    {
                        "create_table",
                        "drop_table",
                        "insert_data",
                        "list_tables",
                        "query_data",
                    },
                )
            finally:
                agent.close()


if __name__ == "__main__":
    unittest.main()
