# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for DocumentQAAgent (gaia_agent_docqa).

The agent constructs a RAGSDK at init (no network/model load at construction)
and registers RAG + file tools. ``skip_lemonade=True`` is hardcoded, so no
live backend is required. Tool assertions use subset checks because the agent
shares the process-global tool registry.
"""

import unittest


class TestDocumentQAAgentImport(unittest.TestCase):
    def test_can_import(self):
        from gaia_agent_docqa.agent import DocumentQAAgent, DocumentQAAgentConfig

        self.assertIsNotNone(DocumentQAAgent)
        self.assertIsNotNone(DocumentQAAgentConfig)


class TestDocumentQAAgentConfig(unittest.TestCase):
    def test_defaults(self):
        from gaia.agents.base.agent import default_max_steps
        from gaia_agent_docqa.agent import DocumentQAAgentConfig

        cfg = DocumentQAAgentConfig()
        self.assertFalse(cfg.use_claude)
        self.assertIsNone(cfg.model_id)
        self.assertEqual(cfg.max_steps, default_max_steps())
        self.assertIsNone(cfg.rag_documents)


class TestDocumentQAAgentInit(unittest.TestCase):
    def _make(self):
        from gaia_agent_docqa.agent import DocumentQAAgent, DocumentQAAgentConfig

        return DocumentQAAgent(DocumentQAAgentConfig())

    def test_constructs_without_backend(self):
        agent = self._make()
        self.assertIsNotNone(agent)
        self.assertIsNotNone(agent.config)

    def test_system_prompt_is_doc_focused(self):
        agent = self._make()
        prompt = agent._get_system_prompt()
        self.assertIsInstance(prompt, str)
        self.assertIn("DocumentQAAgent", prompt)

    def test_registers_rag_tools(self):
        agent = self._make()
        tools = set(agent._tools_registry)
        # RAG mixin tools the agent is built from.
        for expected in ("index_document", "index_directory", "list_indexed_documents"):
            self.assertIn(expected, tools)


if __name__ == "__main__":
    unittest.main()
