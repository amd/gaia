# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for DockerAgent (gaia_agent_docker).

DockerAgent is an MCPAgent; ``skip_lemonade=True`` lets it construct
without a live backend. Tool assertions use subset checks because the
agent shares the process-global tool registry.
"""

import unittest


class TestDockerAgentImport(unittest.TestCase):
    def test_can_import(self):
        from gaia_agent_docker import DockerAgent

        self.assertIsNotNone(DockerAgent)


class TestDockerAgentInit(unittest.TestCase):
    def _make(self):
        from gaia_agent_docker import DockerAgent

        return DockerAgent(skip_lemonade=True, silent_mode=True)

    def test_constructs_without_backend(self):
        agent = self._make()
        self.assertIsNotNone(agent)

    def test_default_model_is_coding_model(self):
        agent = self._make()
        self.assertEqual(agent.model_id, "Qwen3.5-35B-A3B-GGUF")

    def test_system_prompt_is_docker_focused(self):
        agent = self._make()
        prompt = agent._get_system_prompt()
        self.assertIsInstance(prompt, str)
        self.assertIn("Docker", prompt)
        self.assertIn("Dockerfile", prompt)

    def test_registers_docker_tools(self):
        agent = self._make()
        tools = set(agent._tools_registry)
        for expected in (
            "analyze_directory",
            "save_dockerfile",
            "build_image",
            "run_container",
        ):
            self.assertIn(expected, tools)


if __name__ == "__main__":
    unittest.main()
