# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for FileIOAgent (gaia_agent_fileio).

These cover construction without a live Lemonade backend, the default
config, the system prompt, and registration of the file/shell/screenshot
tools the agent is built from. Tool assertions use subset checks because
non-isolating agents share the process-global tool registry.
"""

import unittest


class TestFileIOAgentImport(unittest.TestCase):
    def test_can_import(self):
        from gaia_agent_fileio.agent import FileIOAgent, FileIOAgentConfig

        self.assertIsNotNone(FileIOAgent)
        self.assertIsNotNone(FileIOAgentConfig)


class TestFileIOAgentConfig(unittest.TestCase):
    def test_defaults(self):
        from gaia_agent_fileio.agent import FileIOAgentConfig

        from gaia.agents.base.agent import default_max_steps

        cfg = FileIOAgentConfig()
        self.assertFalse(cfg.use_claude)
        self.assertFalse(cfg.use_chatgpt)
        self.assertIsNone(cfg.model_id)
        self.assertEqual(cfg.max_steps, default_max_steps())


class TestFileIOAgentInit(unittest.TestCase):
    def _make(self):
        from gaia_agent_fileio.agent import FileIOAgent, FileIOAgentConfig

        # skip_lemonade is hardcoded in the agent, so no backend is required.
        return FileIOAgent(FileIOAgentConfig())

    def test_constructs_without_backend(self):
        agent = self._make()
        self.assertIsNotNone(agent)
        self.assertIsNotNone(agent.config)

    def test_system_prompt_is_file_focused(self):
        agent = self._make()
        prompt = agent._get_system_prompt()
        self.assertIsInstance(prompt, str)
        self.assertIn("FileIOAgent", prompt)

    def test_registers_file_tools(self):
        agent = self._make()
        tools = set(agent._tools_registry)
        # The agent composes file_io + file_search + shell + screenshot mixins.
        for expected in ("read_file", "write_file", "edit_file", "run_shell_command"):
            self.assertIn(expected, tools)


if __name__ == "__main__":
    unittest.main()
