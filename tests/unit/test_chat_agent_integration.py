# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for ChatAgent initialization, tool registration, and cleanup."""

from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.chat.agent import ChatAgent, ChatAgentConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# All ChatAgent construction in these tests patches RAGSDK and RAGConfig so
# that no real LLM or RAG backend is needed.
_RAG_PATCHES = (
    "gaia.agents.chat.agent.RAGSDK",
    "gaia.agents.chat.agent.RAGConfig",
)


def _build_agent(**config_overrides) -> ChatAgent:
    """Build a ChatAgent with silent_mode and the given config overrides.

    RAGSDK/RAGConfig are always patched out so no external service is required.
    """
    defaults = {"silent_mode": True}
    defaults.update(config_overrides)
    config = ChatAgentConfig(**defaults)
    with patch(_RAG_PATCHES[0]), patch(_RAG_PATCHES[1]):
        return ChatAgent(config)


# ---------------------------------------------------------------------------
# ChatAgentConfig defaults
# ---------------------------------------------------------------------------


class TestChatAgentConfigDefaults:
    """Verify ChatAgentConfig default values for the new feature flags."""

    def test_enable_filesystem_default_true(self):
        config = ChatAgentConfig()
        assert config.enable_filesystem is True

    def test_enable_scratchpad_default_true(self):
        config = ChatAgentConfig()
        assert config.enable_scratchpad is True

    def test_enable_browser_default_true(self):
        config = ChatAgentConfig()
        assert config.enable_browser is True

    def test_filesystem_scan_depth_default_3(self):
        config = ChatAgentConfig()
        assert config.filesystem_scan_depth == 3


# ---------------------------------------------------------------------------
# FileSystem index initialization
# ---------------------------------------------------------------------------


class TestFileSystemIndexInit:
    """ChatAgent._fs_index lifecycle depending on enable_filesystem flag."""

    def test_fs_index_initialized_when_enabled(self):
        """_fs_index should be set when enable_filesystem=True."""
        agent = _build_agent(
            enable_filesystem=True,
            enable_scratchpad=False,
            enable_browser=False,
        )
        assert agent._fs_index is not None

    def test_fs_index_none_when_disabled(self):
        """_fs_index should remain None when enable_filesystem=False."""
        agent = _build_agent(
            enable_filesystem=False,
            enable_scratchpad=False,
            enable_browser=False,
        )
        assert agent._fs_index is None

    def test_fs_index_graceful_import_error(self):
        """If FileSystemIndexService cannot be imported, _fs_index stays None."""
        with patch(
            "gaia.agents.chat.agent.RAGSDK"
        ), patch(
            "gaia.agents.chat.agent.RAGConfig"
        ), patch.dict(
            "sys.modules",
            {"gaia.filesystem.index": None},
        ):
            # The import inside __init__ will fail because the module is None
            config = ChatAgentConfig(
                silent_mode=True,
                enable_filesystem=True,
                enable_scratchpad=False,
                enable_browser=False,
            )
            # Patch the import so it raises ImportError
            original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

            def _fake_import(name, *args, **kwargs):
                if name == "gaia.filesystem.index":
                    raise ImportError("mocked import failure")
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=_fake_import):
                agent = ChatAgent(config)

            assert agent._fs_index is None


# ---------------------------------------------------------------------------
# Scratchpad initialization
# ---------------------------------------------------------------------------


class TestScratchpadInit:
    """ChatAgent._scratchpad lifecycle depending on enable_scratchpad flag."""

    def test_scratchpad_initialized_when_enabled(self):
        """_scratchpad should be set when enable_scratchpad=True."""
        agent = _build_agent(
            enable_filesystem=False,
            enable_scratchpad=True,
            enable_browser=False,
        )
        assert agent._scratchpad is not None

    def test_scratchpad_none_when_disabled(self):
        """_scratchpad should remain None when enable_scratchpad=False."""
        agent = _build_agent(
            enable_filesystem=False,
            enable_scratchpad=False,
            enable_browser=False,
        )
        assert agent._scratchpad is None

    def test_scratchpad_graceful_import_error(self):
        """If ScratchpadService cannot be imported, _scratchpad stays None."""
        original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def _fake_import(name, *args, **kwargs):
            if name == "gaia.scratchpad.service":
                raise ImportError("mocked import failure")
            return original_import(name, *args, **kwargs)

        config = ChatAgentConfig(
            silent_mode=True,
            enable_filesystem=False,
            enable_scratchpad=True,
            enable_browser=False,
        )
        with patch(_RAG_PATCHES[0]), patch(_RAG_PATCHES[1]), patch(
            "builtins.__import__", side_effect=_fake_import
        ):
            agent = ChatAgent(config)

        assert agent._scratchpad is None


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


class TestChatAgentCleanup:
    """Verify cleanup behaviour, in particular web-client teardown."""

    def test_web_client_close_called_during_cleanup(self):
        """ChatAgent.__del__ should call _web_client.close()."""
        agent = _build_agent(
            enable_browser=True,
            enable_filesystem=False,
            enable_scratchpad=False,
        )
        # Replace the real web client with a mock so we can inspect calls
        mock_client = MagicMock()
        agent._web_client = mock_client

        # Invoke cleanup explicitly (same code path as __del__)
        agent.__del__()

        mock_client.close.assert_called_once()


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    """Verify _register_tools delegates to all expected mixin registration methods."""

    def test_register_tools_calls_mixin_registrations(self):
        """_register_tools should call register_filesystem_tools, register_scratchpad_tools,
        and register_browser_tools among others."""
        agent = _build_agent(
            enable_filesystem=False,
            enable_scratchpad=False,
            enable_browser=False,
        )
        with patch.object(agent, "register_rag_tools") as m_rag, \
             patch.object(agent, "register_file_tools") as m_file, \
             patch.object(agent, "register_shell_tools") as m_shell, \
             patch.object(agent, "register_filesystem_tools") as m_fs, \
             patch.object(agent, "register_scratchpad_tools") as m_sp, \
             patch.object(agent, "register_browser_tools") as m_br:
            agent._register_tools()

        m_fs.assert_called_once()
        m_sp.assert_called_once()
        m_br.assert_called_once()

    def test_filesystem_tool_names_registered(self):
        """After full init, filesystem tool names should be in the tool registry."""
        agent = _build_agent(
            enable_filesystem=True,
            enable_scratchpad=False,
            enable_browser=False,
        )
        tool_names = list(agent.get_tools_info().keys())
        expected_fs_tools = [
            "browse_directory",
            "tree",
            "file_info",
            "find_files",
            "read_file",
            "bookmark",
        ]
        for name in expected_fs_tools:
            assert name in tool_names, f"Expected filesystem tool '{name}' not found in registered tools"

    def test_scratchpad_tool_names_registered(self):
        """After full init, scratchpad tool names should be in the tool registry."""
        agent = _build_agent(
            enable_filesystem=False,
            enable_scratchpad=True,
            enable_browser=False,
        )
        tool_names = list(agent.get_tools_info().keys())
        expected_sp_tools = [
            "create_table",
            "insert_data",
            "query_data",
            "list_tables",
            "drop_table",
        ]
        for name in expected_sp_tools:
            assert name in tool_names, f"Expected scratchpad tool '{name}' not found in registered tools"


# ---------------------------------------------------------------------------
# System prompt content
# ---------------------------------------------------------------------------


class TestSystemPromptContent:
    """Verify the system prompt contains expected sections for new features."""

    @pytest.fixture(autouse=True)
    def _build(self):
        """Build agent once for the class; expose prompt."""
        self.agent = _build_agent(
            enable_filesystem=True,
            enable_scratchpad=True,
            enable_browser=True,
        )
        self.prompt = self.agent._get_system_prompt()

    def test_prompt_includes_file_system_tools_section(self):
        assert "FILE SYSTEM TOOLS" in self.prompt

    def test_prompt_includes_data_analysis_workflow_section(self):
        assert "DATA ANALYSIS WORKFLOW" in self.prompt

    def test_prompt_includes_browser_tools_section(self):
        assert "BROWSER TOOLS" in self.prompt

    def test_prompt_includes_directory_browsing_workflow_section(self):
        assert "DIRECTORY BROWSING WORKFLOW" in self.prompt


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
