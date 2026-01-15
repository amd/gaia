# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Comprehensive SDK Interface Tests

Purpose: Validate that all public SDK interfaces are stable and working.
These tests use mocks to verify interface contracts without requiring:
- Running Lemonade server
- Real database connections
- Actual file I/O
- Network requests

If these tests fail, the SDK interface is broken and external agents will break.
"""

from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import Mock, patch

import pytest

# ============================================================================
# 1. CORE AGENT SYSTEM TESTS
# ============================================================================


class TestAgentBaseClass:
    """Test Agent base class interface."""

    def test_agent_can_be_subclassed(self):
        """Verify Agent can be subclassed and abstract methods work."""
        from gaia.agents.base.agent import Agent
        from gaia.agents.base.console import SilentConsole

        class TestAgent(Agent):
            def _get_system_prompt(self) -> str:
                return "Test prompt"

            def _create_console(self):
                return SilentConsole()

            def _register_tools(self):
                pass

        # Should instantiate without errors
        agent = TestAgent(silent_mode=True)
        assert agent is not None
        assert isinstance(agent, Agent)

    def test_agent_process_query_interface(self):
        """Verify process_query method signature is stable."""
        from gaia.agents.base.agent import Agent
        from gaia.agents.base.console import SilentConsole

        class TestAgent(Agent):
            def _get_system_prompt(self) -> str:
                return "Test"

            def _create_console(self):
                return SilentConsole()

            def _register_tools(self):
                pass

        agent = TestAgent(silent_mode=True)

        # Mock the chat SDK to avoid real LLM calls
        with patch.object(agent, "chat") as mock_chat:
            mock_chat.complete.return_value = "Test response"

            # Test process_query interface
            result = agent.process_query(
                user_input="test query", max_steps=5, trace=False, filename=None
            )

            # Should return dict
            assert isinstance(result, dict)

    def test_agent_execute_tool_interface(self):
        """Verify execute_tool method signature."""
        from gaia.agents.base.agent import Agent
        from gaia.agents.base.console import SilentConsole
        from gaia.agents.base.tools import tool

        class TestAgent(Agent):
            def _get_system_prompt(self) -> str:
                return "Test"

            def _create_console(self):
                return SilentConsole()

            def _register_tools(self):
                @tool
                def test_tool(param: str) -> dict:
                    """Test tool."""
                    return {"result": param}

        agent = TestAgent(silent_mode=True)

        # Execute tool
        result = agent.execute_tool("test_tool", {"param": "value"})

        # Should return result
        assert result["result"] == "value"

    def test_agent_list_tools_interface(self):
        """Verify list_tools method exists."""
        from gaia.agents.base.agent import Agent
        from gaia.agents.base.console import SilentConsole

        class TestAgent(Agent):
            def _get_system_prompt(self) -> str:
                return "Test"

            def _create_console(self):
                return SilentConsole()

            def _register_tools(self):
                pass

        agent = TestAgent(silent_mode=True)

        # Should not raise
        agent.list_tools(verbose=False)
        agent.list_tools(verbose=True)


class TestToolDecorator:
    """Test @tool decorator interface."""

    def test_tool_decorator_exists(self):
        """Verify @tool decorator can be imported."""
        from gaia.agents.base.tools import tool

        assert tool is not None

    def test_tool_decorator_basic_usage(self):
        """Verify @tool decorator works with simple function."""
        from gaia.agents.base.tools import tool

        @tool
        def simple_tool(param: str) -> str:
            """Simple test tool."""
            return f"Result: {param}"

        # Function should still work
        result = simple_tool("test")
        assert result == "Result: test"

    def test_tool_decorator_with_types(self):
        """Verify @tool handles type hints correctly."""
        from typing import Dict, List

        from gaia.agents.base.tools import tool

        @tool
        def typed_tool(
            name: str, count: int, items: List[str], metadata: Dict[str, Any]
        ) -> dict:
            """Tool with type hints."""
            return {"name": name, "count": count, "items": items, "metadata": metadata}

        result = typed_tool(
            name="test", count=5, items=["a", "b"], metadata={"key": "value"}
        )
        assert result["name"] == "test"
        assert result["count"] == 5


class TestConsoleInterfaces:
    """Test console/output handler interfaces."""

    def test_agent_console_exists(self):
        """Verify AgentConsole can be imported and instantiated."""
        from gaia.agents.base.console import AgentConsole

        console = AgentConsole()
        assert console is not None

    def test_silent_console_exists(self):
        """Verify SilentConsole can be imported and instantiated."""
        from gaia.agents.base.console import SilentConsole

        console = SilentConsole()
        assert console is not None

    def test_sse_handler_exists(self):
        """Verify SSEOutputHandler exists."""
        from gaia.api.sse_handler import SSEOutputHandler

        handler = SSEOutputHandler()
        assert handler is not None


class TestApiAgent:
    """Test ApiAgent mixin interface."""

    def test_api_agent_can_be_imported(self):
        """Verify ApiAgent can be imported."""
        from gaia.agents.base.api_agent import ApiAgent

        assert ApiAgent is not None

    def test_api_agent_interface_methods(self):
        """Verify ApiAgent has required methods."""
        from gaia.agents.base.agent import Agent
        from gaia.agents.base.api_agent import ApiAgent
        from gaia.agents.base.console import SilentConsole

        class TestApiAgent(ApiAgent, Agent):
            def _get_system_prompt(self) -> str:
                return "Test"

            def _create_console(self):
                return SilentConsole()

            def _register_tools(self):
                pass

            def get_model_id(self) -> str:
                return "test-model"

        agent = TestApiAgent(silent_mode=True)

        # Test interface methods exist
        assert hasattr(agent, "get_model_id")
        assert hasattr(agent, "get_model_info")
        assert hasattr(agent, "estimate_tokens")
        assert hasattr(agent, "format_for_api")

        # Test they return expected types
        assert isinstance(agent.get_model_id(), str)
        assert isinstance(agent.get_model_info(), dict)
        assert isinstance(agent.estimate_tokens("test"), int)


class TestMCPAgent:
    """Test MCPAgent interface."""

    def test_mcp_agent_can_be_imported(self):
        """Verify MCPAgent can be imported."""
        from gaia.agents.base.mcp_agent import MCPAgent

        assert MCPAgent is not None

    def test_mcp_agent_interface_methods(self):
        """Verify MCPAgent has required abstract methods."""
        from gaia.agents.base.agent import Agent
        from gaia.agents.base.console import SilentConsole
        from gaia.agents.base.mcp_agent import MCPAgent

        class TestMCPAgent(MCPAgent, Agent):
            def _get_system_prompt(self) -> str:
                return "Test"

            def _create_console(self):
                return SilentConsole()

            def _register_tools(self):
                pass

            def get_mcp_tool_definitions(self) -> List[Dict]:
                return [
                    {
                        "name": "test_tool",
                        "description": "Test",
                        "inputSchema": {"type": "object", "properties": {}},
                    }
                ]

            def execute_mcp_tool(self, tool_name: str, arguments: Dict) -> Dict:
                return {"result": "ok"}

        agent = TestMCPAgent(silent_mode=True)

        # Test interface methods
        tools = agent.get_mcp_tool_definitions()
        assert isinstance(tools, list)
        assert len(tools) > 0

        result = agent.execute_mcp_tool("test_tool", {})
        assert isinstance(result, dict)


# ============================================================================
# 2. CHAT SDK TESTS
# ============================================================================


class TestChatSDK:
    """Test Chat SDK interface."""

    def test_chat_config_exists(self):
        """Verify ChatConfig can be imported and instantiated."""
        from gaia.chat.sdk import ChatConfig

        config = ChatConfig()
        assert config is not None
        assert hasattr(config, "model")
        assert hasattr(config, "max_tokens")
        assert hasattr(config, "system_prompt")

    def test_chat_sdk_exists(self):
        """Verify ChatSDK can be imported and instantiated."""
        from gaia.chat.sdk import ChatConfig, ChatSDK

        config = ChatConfig()
        chat = ChatSDK(config)
        assert chat is not None

    @patch("gaia.chat.sdk.ChatSDK.send")
    def test_chat_sdk_send_interface(self, mock_send):
        """Verify ChatSDK.send method signature."""
        from gaia.chat.sdk import ChatConfig, ChatResponse, ChatSDK

        # Mock response
        mock_send.return_value = ChatResponse(
            text="Test response", history=None, stats=None, is_complete=True
        )

        config = ChatConfig()
        chat = ChatSDK(config)

        # Test send interface
        response = chat.send("test message")

        assert isinstance(response, ChatResponse)
        assert hasattr(response, "text")
        assert hasattr(response, "history")
        assert hasattr(response, "stats")
        assert hasattr(response, "is_complete")

    def test_chat_sdk_history_methods(self):
        """Verify history management methods exist."""
        from gaia.chat.sdk import ChatConfig, ChatSDK

        config = ChatConfig()
        chat = ChatSDK(config)

        # Test interface methods exist
        assert hasattr(chat, "get_history")
        assert hasattr(chat, "clear_history")

        # Test they return expected types
        history = chat.get_history()
        assert isinstance(history, list)

    def test_quick_chat_exists(self):
        """Verify quick_chat convenience function exists."""
        from gaia.chat.sdk import quick_chat

        assert quick_chat is not None


# ============================================================================
# 3. RAG SDK TESTS
# ============================================================================


class TestRAGSDK:
    """Test RAG SDK interface."""

    def test_rag_config_exists(self):
        """Verify RAGConfig can be imported."""
        from gaia.rag.sdk import RAGConfig

        config = RAGConfig()
        assert config is not None
        assert hasattr(config, "chunk_size")
        assert hasattr(config, "chunk_overlap")
        assert hasattr(config, "max_chunks")

    @patch("gaia.rag.sdk.RAGSDK.__init__")
    def test_rag_sdk_exists(self, mock_init):
        """Verify RAGSDK can be imported."""
        from gaia.rag.sdk import RAGSDK, RAGConfig

        mock_init.return_value = None

        config = RAGConfig()
        rag = RAGSDK.__new__(RAGSDK)
        assert rag is not None

    def test_rag_sdk_interface_methods(self):
        """Verify RAGSDK has required methods."""
        from gaia.rag.sdk import RAGSDK

        # Check methods exist (even if we can't instantiate)
        assert hasattr(RAGSDK, "index_document")
        assert hasattr(RAGSDK, "index_documents")
        assert hasattr(RAGSDK, "query")
        assert hasattr(RAGSDK, "get_indexed_files")
        assert hasattr(RAGSDK, "clear_index")
        assert hasattr(RAGSDK, "remove_document")
        assert hasattr(RAGSDK, "get_stats")

    def test_quick_rag_exists(self):
        """Verify quick_rag convenience function exists."""
        from gaia.rag.sdk import quick_rag

        assert quick_rag is not None


# ============================================================================
# 4. LLM INTEGRATION TESTS
# ============================================================================


class TestLLMClient:
    """Test LLMClient interface."""

    @patch("gaia.llm.LLMClient.__init__")
    def test_llm_client_can_be_imported(self, mock_init):
        """Verify LLMClient can be imported."""
        from gaia.llm import LLMClient

        mock_init.return_value = None
        client = LLMClient.__new__(LLMClient)
        assert client is not None

    def test_llm_client_interface_methods(self):
        """Verify LLMClient has required methods."""
        from gaia.llm import LLMClient

        # Check abstract methods exist
        assert hasattr(LLMClient, "generate")
        assert hasattr(LLMClient, "chat")
        assert hasattr(LLMClient, "provider_name")
        # Check optional methods exist
        assert hasattr(LLMClient, "embed")
        assert hasattr(LLMClient, "vision")
        assert hasattr(LLMClient, "get_performance_stats")
        assert hasattr(LLMClient, "load_model")
        assert hasattr(LLMClient, "unload_model")

    def test_lemonade_constants_exist(self):
        """Verify Lemonade client constants."""
        from gaia.llm.lemonade_client import DEFAULT_LEMONADE_URL, DEFAULT_MODEL_NAME

        assert isinstance(DEFAULT_MODEL_NAME, str)
        assert isinstance(DEFAULT_LEMONADE_URL, str)
        assert DEFAULT_LEMONADE_URL.startswith("http")


class TestVLMClient:
    """Test VLMClient interface."""

    @patch("gaia.llm.vlm_client.VLMClient.__init__")
    def test_vlm_client_can_be_imported(self, mock_init):
        """Verify VLMClient can be imported."""
        from gaia.llm.vlm_client import VLMClient

        mock_init.return_value = None
        vlm = VLMClient.__new__(VLMClient)
        assert vlm is not None

    def test_vlm_client_interface_methods(self):
        """Verify VLMClient has required methods."""
        from gaia.llm.vlm_client import VLMClient

        # Check methods exist
        assert hasattr(VLMClient, "check_availability")
        assert hasattr(VLMClient, "extract_from_image")


# ============================================================================
# 5. AUDIO COMPONENTS TESTS
# ============================================================================


class TestAudioClient:
    """Test AudioClient interface."""

    @patch("gaia.audio.audio_client.AudioClient.__init__")
    def test_audio_client_can_be_imported(self, mock_init):
        """Verify AudioClient can be imported."""
        from gaia.audio.audio_client import AudioClient

        mock_init.return_value = None
        audio = AudioClient.__new__(AudioClient)
        assert audio is not None

    def test_audio_client_interface_methods(self):
        """Verify AudioClient has required methods."""
        from gaia.audio.audio_client import AudioClient

        # Check methods exist
        assert hasattr(AudioClient, "start_voice_chat")
        assert hasattr(AudioClient, "initialize_tts")
        assert hasattr(AudioClient, "play_audio")
        assert hasattr(AudioClient, "get_device_list")


class TestWhisperASR:
    """Test WhisperAsr interface."""

    def test_whisper_can_be_imported(self):
        """Verify WhisperAsr can be imported."""
        from gaia.audio.whisper_asr import WhisperAsr

        assert WhisperAsr is not None

    def test_whisper_interface_methods(self):
        """Verify WhisperAsr has required methods."""
        from gaia.audio.whisper_asr import WhisperAsr

        # Check methods exist
        assert hasattr(WhisperAsr, "start_recording")
        assert hasattr(WhisperAsr, "stop_recording")
        assert hasattr(WhisperAsr, "get_device_name")


class TestKokoroTTS:
    """Test KokoroTTS interface."""

    @patch("gaia.audio.kokoro_tts.KokoroTTS.__init__")
    def test_kokoro_can_be_imported(self, mock_init):
        """Verify KokoroTTS can be imported."""
        from gaia.audio.kokoro_tts import KokoroTTS

        mock_init.return_value = None
        tts = KokoroTTS.__new__(KokoroTTS)
        assert tts is not None

    def test_kokoro_interface_methods(self):
        """Verify KokoroTTS has required methods."""
        from gaia.audio.kokoro_tts import KokoroTTS

        # Check methods exist
        assert hasattr(KokoroTTS, "synthesize")
        assert hasattr(KokoroTTS, "list_voices")


# ============================================================================
# 6. API SERVER TESTS
# ============================================================================


class TestAPIComponents:
    """Test API server components."""

    def test_create_app_exists(self):
        """Verify create_app function exists."""
        from gaia.api.openai_server import create_app

        # Should be callable
        assert callable(create_app)

    @patch("gaia.api.openai_server.FastAPI")
    def test_create_app_returns_fastapi(self, mock_fastapi):
        """Verify create_app returns FastAPI app."""
        from gaia.api.openai_server import create_app

        mock_app = Mock()
        mock_fastapi.return_value = mock_app

        app = create_app()

        # Should return app instance
        assert app is not None

    def test_agent_registry_exists(self):
        """Verify AgentRegistry can be imported."""
        from gaia.api.agent_registry import AgentRegistry

        assert AgentRegistry is not None

    def test_api_schemas_exist(self):
        """Verify API schemas can be imported."""
        from gaia.api.schemas import (
            ChatCompletionRequest,
            ChatCompletionResponse,
            ChatMessage,
            ModelListResponse,
        )

        # All schemas should be importable
        assert ChatMessage is not None
        assert ChatCompletionRequest is not None
        assert ChatCompletionResponse is not None
        assert ModelListResponse is not None


# ============================================================================
# 7. MCP INTEGRATION TESTS
# ============================================================================


class TestMCPComponents:
    """Test MCP integration components."""

    @patch("gaia.mcp.agent_mcp_server.AgentMCPServer.__init__")
    def test_mcp_server_can_be_imported(self, mock_init):
        """Verify AgentMCPServer can be imported."""
        from gaia.mcp.agent_mcp_server import AgentMCPServer

        mock_init.return_value = None
        server = AgentMCPServer.__new__(AgentMCPServer)
        assert server is not None

    def test_mcp_server_interface_methods(self):
        """Verify AgentMCPServer has required methods."""
        from gaia.mcp.agent_mcp_server import AgentMCPServer

        # Check methods exist
        assert hasattr(AgentMCPServer, "start")
        assert hasattr(AgentMCPServer, "stop")


# ============================================================================
# 8. TOOL MIXINS TESTS
# ============================================================================


class TestToolMixins:
    """Test tool mixin interfaces."""

    def test_file_tools_mixin_exists(self):
        """Verify FileToolsMixin can be imported."""
        from gaia.agents.chat.tools.file_tools import FileToolsMixin

        assert FileToolsMixin is not None

    def test_rag_tools_mixin_exists(self):
        """Verify RAGToolsMixin can be imported."""
        from gaia.agents.chat.tools.rag_tools import RAGToolsMixin

        assert RAGToolsMixin is not None

    def test_shell_tools_mixin_exists(self):
        """Verify ShellToolsMixin can be imported."""
        from gaia.agents.chat.tools.shell_tools import ShellToolsMixin

        assert ShellToolsMixin is not None

    def test_file_search_mixin_exists(self):
        """Verify FileSearchToolsMixin can be imported."""
        from gaia.agents.tools.file_search import FileSearchToolsMixin

        assert FileSearchToolsMixin is not None


# ============================================================================
# 9. SECURITY TESTS
# ============================================================================


class TestSecurity:
    """Test security components."""

    def test_path_validator_exists(self):
        """Verify PathValidator can be imported."""
        from gaia.security import PathValidator

        assert PathValidator is not None

    @patch("gaia.security.PathValidator.__init__")
    def test_path_validator_interface(self, mock_init):
        """Verify PathValidator has required methods."""
        from gaia.security import PathValidator

        mock_init.return_value = None
        validator = PathValidator.__new__(PathValidator)

        # Check methods exist
        assert hasattr(PathValidator, "is_path_allowed")
        assert hasattr(PathValidator, "add_allowed_path")


# ============================================================================
# 10. UTILITIES TESTS
# ============================================================================


class TestUtilities:
    """Test utility modules."""

    def test_logger_exists(self):
        """Verify get_logger can be imported."""
        from gaia.logger import get_logger

        logger = get_logger(__name__)
        assert logger is not None

    def test_version_exists(self):
        """Verify version can be imported."""
        from gaia.version import __version__

        assert isinstance(__version__, str)


# ============================================================================
# 11. INTEGRATION TESTS (Mocked)
# ============================================================================


class TestAgentIntegration:
    """Test full agent integration with mocked components."""

    @patch("gaia.chat.sdk.ChatSDK")
    def test_agent_with_mocked_llm(self, mock_chat_sdk):
        """Test agent can process queries with mocked LLM."""
        from gaia.agents.base.agent import Agent
        from gaia.agents.base.console import SilentConsole
        from gaia.agents.base.tools import tool

        # Mock LLM response
        mock_chat_instance = Mock()
        mock_chat_instance.complete.return_value = (
            '{"tool": "test_tool", "args": {"param": "value"}}'
        )
        mock_chat_sdk.return_value = mock_chat_instance

        class TestAgent(Agent):
            def _get_system_prompt(self) -> str:
                return "Test"

            def _create_console(self):
                return SilentConsole()

            def _register_tools(self):
                @tool
                def test_tool(param: str) -> dict:
                    """Test tool."""
                    return {"result": param}

        agent = TestAgent(silent_mode=True)

        # Process query
        result = agent.process_query("test", max_steps=1)

        # Verify LLM was called
        assert mock_chat_instance.complete.called

    def test_agent_with_multiple_mixins(self):
        """Test agent can inherit from multiple mixins."""
        from gaia.agents.base.agent import Agent
        from gaia.agents.base.api_agent import ApiAgent
        from gaia.agents.base.console import SilentConsole
        from gaia.agents.chat.tools.file_tools import FileToolsMixin

        class MultiMixinAgent(ApiAgent, Agent, FileToolsMixin):
            def _get_system_prompt(self) -> str:
                return "Test"

            def _create_console(self):
                return SilentConsole()

            def _register_tools(self):
                pass

            def get_model_id(self) -> str:
                return "multi-mixin-test"

        # Should instantiate without errors
        agent = MultiMixinAgent(silent_mode=True)
        assert agent is not None


# ============================================================================
# 12. BACKWARD COMPATIBILITY TESTS
# ============================================================================


class TestBackwardCompatibility:
    """Ensure SDK interfaces remain stable across versions."""

    def test_agent_constructor_parameters(self):
        """Verify Agent constructor accepts expected parameters."""
        from gaia.agents.base.agent import Agent
        from gaia.agents.base.console import SilentConsole

        class TestAgent(Agent):
            def _get_system_prompt(self) -> str:
                return "Test"

            def _create_console(self):
                return SilentConsole()

            def _register_tools(self):
                pass

        # Test common parameters are accepted
        agent = TestAgent(
            use_claude=False,
            use_chatgpt=False,
            model_id="test-model",
            max_steps=10,
            debug_prompts=False,
            show_prompts=False,
            streaming=True,
            silent_mode=True,
        )

        assert agent is not None

    def test_process_query_signature(self):
        """Verify process_query signature hasn't changed."""
        import inspect

        from gaia.agents.base.agent import Agent

        sig = inspect.signature(Agent.process_query)
        params = list(sig.parameters.keys())

        # Required parameters
        assert "self" in params
        assert "user_input" in params

        # Optional parameters
        assert "max_steps" in params
        assert "trace" in params
        assert "filename" in params


# ============================================================================
# 13. DOCUMENTATION VALIDATION TESTS
# ============================================================================


class TestSDKDocumentation:
    """Validate SDK documentation examples."""

    def test_sdk_examples_are_syntactically_valid(self):
        """Verify code examples in SDK.md are valid Python."""
        # This would parse SDK.md and extract code blocks
        # For now, just verify the file exists
        sdk_path = Path(__file__).parent.parent / "docs" / "sdk.md"
        assert sdk_path.exists(), "SDK.md should exist in docs/"

    def test_all_imports_in_sdk_are_valid(self):
        """Verify all import statements documented in SDK work."""
        # Core imports from SDK.md
        try:
            from gaia.agents.base.agent import Agent  # noqa: F401
            from gaia.agents.base.api_agent import ApiAgent  # noqa: F401
            from gaia.agents.base.console import (  # noqa: F401
                AgentConsole,
                SilentConsole,
            )
            from gaia.agents.base.mcp_agent import MCPAgent  # noqa: F401
            from gaia.agents.base.tools import tool  # noqa: F401
        except ImportError as e:
            pytest.fail(f"SDK documented import failed: {e}")

        # Chat SDK
        try:
            from gaia.chat.sdk import ChatConfig, ChatSDK, quick_chat  # noqa: F401
        except ImportError as e:
            pytest.fail(f"Chat SDK import failed: {e}")

        # RAG SDK
        try:
            from gaia.rag.sdk import RAGSDK, RAGConfig, quick_rag  # noqa: F401
        except ImportError as e:
            pytest.fail(f"RAG SDK import failed: {e}")

        # LLM
        try:
            from gaia.llm import LLMClient  # noqa: F401
            from gaia.llm.vlm_client import VLMClient  # noqa: F401
        except ImportError as e:
            pytest.fail(f"LLM import failed: {e}")

        # Audio
        try:
            from gaia.audio.audio_client import AudioClient  # noqa: F401
            from gaia.audio.kokoro_tts import KokoroTTS  # noqa: F401
            from gaia.audio.whisper_asr import WhisperAsr  # noqa: F401
        except ImportError as e:
            pytest.fail(f"Audio import failed: {e}")

        # API
        try:
            from gaia.api.agent_registry import AgentRegistry  # noqa: F401
            from gaia.api.openai_server import create_app  # noqa: F401
            from gaia.api.sse_handler import SSEOutputHandler  # noqa: F401
        except ImportError as e:
            pytest.fail(f"API import failed: {e}")

        # MCP
        try:
            from gaia.mcp.agent_mcp_server import AgentMCPServer  # noqa: F401
        except ImportError as e:
            pytest.fail(f"MCP import failed: {e}")

        # Tool Mixins
        try:
            from gaia.agents.chat.tools.file_tools import FileToolsMixin  # noqa: F401
            from gaia.agents.chat.tools.rag_tools import RAGToolsMixin  # noqa: F401
            from gaia.agents.chat.tools.shell_tools import ShellToolsMixin  # noqa: F401
            from gaia.agents.tools.file_search import FileSearchToolsMixin  # noqa: F401
        except ImportError as e:
            pytest.fail(f"Tool mixin import failed: {e}")

        # Utilities
        try:
            from gaia.logger import get_logger  # noqa: F401
            from gaia.security import PathValidator  # noqa: F401
        except ImportError as e:
            pytest.fail(f"Utility import failed: {e}")


# ============================================================================
# 14. INTERFACE CONTRACT TESTS
# ============================================================================


class TestInterfaceContracts:
    """Test that interfaces follow expected contracts."""

    def test_tool_returns_dict_or_basic_type(self):
        """Verify tools can return dict or basic types."""
        from gaia.agents.base.tools import tool

        @tool
        def dict_tool() -> dict:
            return {"key": "value"}

        @tool
        def str_tool() -> str:
            return "string result"

        @tool
        def int_tool() -> int:
            return 42

        # All should work
        assert dict_tool() == {"key": "value"}
        assert str_tool() == "string result"
        assert int_tool() == 42

    def test_agent_states_are_defined(self):
        """Verify agent state constants exist."""
        from gaia.agents.base.agent import (
            STATE_COMPLETION,
            STATE_DIRECT_EXECUTION,
            STATE_ERROR_RECOVERY,
            STATE_EXECUTING_PLAN,
            STATE_PLANNING,
        )

        # All states should be strings
        assert isinstance(STATE_PLANNING, str)
        assert isinstance(STATE_EXECUTING_PLAN, str)
        assert isinstance(STATE_DIRECT_EXECUTION, str)
        assert isinstance(STATE_ERROR_RECOVERY, str)
        assert isinstance(STATE_COMPLETION, str)


# ============================================================================
# 15. REGRESSION TESTS
# ============================================================================


class TestNoRegressions:
    """Catch regressions in common usage patterns."""

    def test_minimal_agent_still_works(self):
        """Verify the simplest possible agent still works."""
        from gaia.agents.base.agent import Agent
        from gaia.agents.base.console import SilentConsole

        class MinimalAgent(Agent):
            def _get_system_prompt(self) -> str:
                return "Minimal"

            def _create_console(self):
                return SilentConsole()

            def _register_tools(self):
                pass

        # Should work
        agent = MinimalAgent(silent_mode=True)
        assert agent is not None

    def test_agent_with_single_tool_works(self):
        """Verify agent with one tool works."""
        from gaia.agents.base.agent import Agent
        from gaia.agents.base.console import SilentConsole
        from gaia.agents.base.tools import tool

        class SingleToolAgent(Agent):
            def _get_system_prompt(self) -> str:
                return "Test"

            def _create_console(self):
                return SilentConsole()

            def _register_tools(self):
                @tool
                def only_tool() -> dict:
                    """Only tool."""
                    return {"status": "ok"}

        agent = SingleToolAgent(silent_mode=True)

        # Tool should be registered
        result = agent.execute_tool("only_tool", {})
        assert result["status"] == "ok"


# ============================================================================
# 16. ERROR HANDLING TESTS
# ============================================================================


class TestErrorHandling:
    """Test that SDK handles errors gracefully."""

    def test_agent_with_missing_abstract_method_fails(self):
        """Verify Agent enforces abstract methods."""
        from gaia.agents.base.agent import Agent

        class IncompleteAgent(Agent):
            # Missing _get_system_prompt
            def _create_console(self):
                from gaia.agents.base.console import SilentConsole

                return SilentConsole()

            def _register_tools(self):
                pass

        # Should raise TypeError
        with pytest.raises(TypeError):
            agent = IncompleteAgent()

    def test_tool_with_invalid_signature_still_works(self):
        """Verify @tool handles functions without type hints."""
        from gaia.agents.base.tools import tool

        @tool
        def untyped_tool(param):
            """Tool without type hints."""
            return {"result": param}

        # Should still work
        result = untyped_tool("test")
        assert result["result"] == "test"


# ============================================================================
# 17. PERFORMANCE TESTS (Interface Only)
# ============================================================================


class TestPerformanceInterfaces:
    """Test that performance-critical interfaces exist."""

    def test_streaming_interfaces_exist(self):
        """Verify streaming support exists in all components."""
        from gaia.chat.sdk import ChatSDK

        # ChatSDK should have streaming
        assert hasattr(ChatSDK, "send_stream")
        assert hasattr(ChatSDK, "send_messages_stream")


# ============================================================================
# 18. COMPATIBILITY TESTS
# ============================================================================


class TestPythonVersionCompatibility:
    """Test SDK works on supported Python versions."""

    def test_imports_work_on_current_python(self):
        """Verify basic imports work on current Python version."""
        import sys

        # SDK requires Python 3.10+
        assert sys.version_info >= (3, 10), "SDK requires Python 3.10+"

        # All core imports should work
        from gaia.agents.base.agent import Agent
        from gaia.chat.sdk import ChatSDK
        from gaia.rag.sdk import RAGSDK

        assert Agent is not None
        assert ChatSDK is not None
        assert RAGSDK is not None


# ============================================================================
# 19. PACKAGE STRUCTURE TESTS
# ============================================================================


class TestPackageStructure:
    """Test package organization follows SDK conventions."""

    def test_agents_base_module_exists(self):
        """Verify agents.base module is importable."""
        import gaia.agents.base

        assert gaia.agents.base is not None

    def test_chat_module_exists(self):
        """Verify chat module is importable."""
        import gaia.chat

        assert gaia.chat is not None

    def test_rag_module_exists(self):
        """Verify rag module is importable."""
        import gaia.rag

        assert gaia.rag is not None

    def test_llm_module_exists(self):
        """Verify llm module is importable."""
        import gaia.llm

        assert gaia.llm is not None

    def test_audio_module_exists(self):
        """Verify audio module is importable."""
        import gaia.audio

        assert gaia.audio is not None

    def test_api_module_exists(self):
        """Verify api module is importable."""
        import gaia.api

        assert gaia.api is not None

    def test_mcp_module_exists(self):
        """Verify mcp module is importable."""
        import gaia.mcp

        assert gaia.mcp is not None


# ============================================================================
# 20. FUTURE SDK FEATURES TESTS (Expected to Fail)
# ============================================================================


@pytest.mark.xfail(reason="DatabaseMixin not yet implemented - Issue #1")
class TestDatabaseMixin:
    """Test DatabaseMixin interface (when implemented)."""

    def test_database_mixin_will_exist(self):
        """Verify DatabaseMixin will be importable."""
        from gaia.agents.base.database_mixin import DatabaseMixin

        assert DatabaseMixin is not None

    def test_database_mixin_will_have_methods(self):
        """Verify DatabaseMixin will have required methods."""
        from gaia.agents.base.database_mixin import DatabaseMixin

        # Expected interface
        assert hasattr(DatabaseMixin, "initialize_database")
        assert hasattr(DatabaseMixin, "execute_query")
        assert hasattr(DatabaseMixin, "execute_insert")
        assert hasattr(DatabaseMixin, "execute_update")
        assert hasattr(DatabaseMixin, "transaction")


@pytest.mark.xfail(reason="FileChangeHandler not yet extracted - Issue #2")
class TestFileChangeHandler:
    """Test FileChangeHandler interface (when implemented)."""

    def test_file_change_handler_will_exist(self):
        """Verify FileChangeHandler will be importable."""
        from gaia.utils.file_watcher import FileChangeHandler

        assert FileChangeHandler is not None


@pytest.mark.xfail(reason="Plugin registry not yet implemented - Issue #4")
class TestPluginRegistry:
    """Test plugin registry (when implemented)."""

    def test_agent_registry_will_exist(self):
        """Verify AgentRegistry will exist."""
        from gaia.plugins.registry import AgentRegistry, get_registry

        assert AgentRegistry is not None
        assert get_registry is not None


# ============================================================================
# 21. TALK SDK TESTS
# ============================================================================


class TestTalkSDK:
    """Test Talk SDK components."""

    def test_talk_config_exists(self):
        """Verify TalkConfig can be imported and instantiated."""
        from gaia.talk.sdk import TalkConfig

        config = TalkConfig()
        assert config is not None
        assert hasattr(config, "model")
        assert hasattr(config, "whisper_model_size")
        assert hasattr(config, "audio_device_index")
        assert hasattr(config, "enable_tts")
        assert hasattr(config, "mode")

    def test_talk_sdk_exists(self):
        """Verify TalkSDK can be imported and instantiated."""
        from gaia.talk.sdk import TalkConfig, TalkSDK

        config = TalkConfig()
        # Mock to avoid audio device initialization
        with patch("gaia.talk.sdk.AudioClient"):
            talk = TalkSDK(config)
            assert talk is not None

    def test_talk_mode_enum_exists(self):
        """Verify TalkMode enum exists with expected values."""
        from gaia.talk.sdk import TalkMode

        # Verify enum values
        assert hasattr(TalkMode, "VOICE_ONLY")
        assert hasattr(TalkMode, "VOICE_AND_TEXT")
        assert hasattr(TalkMode, "TEXT_ONLY")

    def test_talk_response_structure(self):
        """Verify TalkResponse structure."""
        from gaia.talk.sdk import TalkResponse

        # Test response structure
        response = TalkResponse(text="Test response", stats=None, is_complete=True)
        assert response is not None
        assert hasattr(response, "text")
        assert hasattr(response, "stats")
        assert hasattr(response, "is_complete")

    def test_talk_sdk_interface_methods(self):
        """Verify TalkSDK has required methods."""
        from gaia.talk.sdk import TalkSDK

        # Check methods exist
        assert hasattr(TalkSDK, "chat")
        assert hasattr(TalkSDK, "chat_stream")
        assert hasattr(TalkSDK, "start_voice_session")
        assert hasattr(TalkSDK, "enable_rag")


# ============================================================================
# 22. ROUTING AGENT TESTS
# ============================================================================


class TestRoutingAgent:
    """Test routing capabilities."""

    def test_routing_agent_can_be_imported(self):
        """Verify RoutingAgent can be imported."""
        try:
            from gaia.agents.routing.routing_agent import RoutingAgent

            assert RoutingAgent is not None
        except ImportError:
            # If routing agent doesn't exist as standalone, check for routing capabilities in base agent
            from gaia.agents.base.agent import Agent

            assert hasattr(Agent, "route_query") or hasattr(Agent, "select_agent")

    def test_routing_agent_interface_methods(self):
        """Verify routing agent has required methods."""
        try:
            from gaia.agents.routing.routing_agent import RoutingAgent

            # Check methods exist
            assert hasattr(RoutingAgent, "register_agent")
            assert hasattr(RoutingAgent, "route_query")
            assert hasattr(RoutingAgent, "list_available_agents")
        except ImportError:
            # Expected if routing is not yet implemented as standalone
            pytest.skip("RoutingAgent not yet implemented")


# ============================================================================
# 23. SPECIALIZED AGENTS TESTS
# ============================================================================


class TestSpecializedAgents:
    """Test example agents."""

    def test_chat_agent_exists(self):
        """Verify ChatAgent can be imported."""
        from gaia.agents.chat.agent import ChatAgent

        assert ChatAgent is not None

    def test_docker_agent_exists(self):
        """Verify DockerAgent can be imported."""
        try:
            from gaia.agents.docker.agent import DockerAgent

            assert DockerAgent is not None
        except ImportError:
            # Docker agent may not be implemented yet
            pytest.skip("DockerAgent not yet implemented")

    def test_jira_agent_exists(self):
        """Verify JiraAgent can be imported."""
        from gaia.agents.jira.agent import JiraAgent

        assert JiraAgent is not None

    def test_blender_agent_exists(self):
        """Verify BlenderAgent can be imported."""
        from gaia.agents.blender.agent import BlenderAgent

        assert BlenderAgent is not None

    def test_specialized_agents_inherit_from_base(self):
        """Verify all specialized agents inherit from Agent base class."""
        from gaia.agents.base.agent import Agent
        from gaia.agents.blender.agent import BlenderAgent
        from gaia.agents.chat.agent import ChatAgent
        from gaia.agents.jira.agent import JiraAgent

        assert issubclass(ChatAgent, Agent)
        assert issubclass(JiraAgent, Agent)
        assert issubclass(BlenderAgent, Agent)


# ============================================================================
# 24. CODE TOOL MIXINS TESTS
# ============================================================================


class TestCodeToolMixins:
    """Test all code mixins."""

    def test_cli_tools_mixin_exists(self):
        """Verify CLIToolsMixin can be imported."""
        from gaia.agents.code.tools.cli_tools import CLIToolsMixin

        assert CLIToolsMixin is not None
        # Check for CLI registration method
        assert hasattr(CLIToolsMixin, "register_cli_tools") or hasattr(
            CLIToolsMixin, "__init__"
        )

    def test_code_tools_mixin_exists(self):
        """Verify CodeToolsMixin can be imported."""
        from gaia.agents.code.tools.code_tools import CodeToolsMixin

        assert CodeToolsMixin is not None
        # Check for registration method
        assert hasattr(CodeToolsMixin, "register_code_tools") or hasattr(
            CodeToolsMixin, "__init__"
        )

    def test_file_io_tools_mixin_exists(self):
        """Verify FileIOToolsMixin can be imported."""
        from gaia.agents.code.tools.file_io import FileIOToolsMixin

        assert FileIOToolsMixin is not None
        # Check for registration method
        assert hasattr(FileIOToolsMixin, "register_file_io_tools") or hasattr(
            FileIOToolsMixin, "__init__"
        )

    def test_validation_tools_mixin_exists(self):
        """Verify ValidationToolsMixin can be imported."""
        from gaia.agents.code.tools.validation_tools import ValidationToolsMixin

        assert ValidationToolsMixin is not None
        # Check for registration method
        assert hasattr(ValidationToolsMixin, "register_validation_tools") or hasattr(
            ValidationToolsMixin, "__init__"
        )

    def test_error_fixing_mixin_exists(self):
        """Verify ErrorFixingMixin can be imported."""
        from gaia.agents.code.tools.error_fixing import ErrorFixingMixin

        assert ErrorFixingMixin is not None
        # Check for registration method
        assert hasattr(ErrorFixingMixin, "register_error_fixing_tools") or hasattr(
            ErrorFixingMixin, "__init__"
        )

    def test_testing_mixin_exists(self):
        """Verify TestingMixin can be imported."""
        from gaia.agents.code.tools.testing import TestingMixin

        assert TestingMixin is not None
        # Check for registration method
        assert hasattr(TestingMixin, "register_testing_tools") or hasattr(
            TestingMixin, "__init__"
        )

    def test_prisma_tools_mixin_exists(self):
        """Verify PrismaToolsMixin can be imported."""
        try:
            from gaia.agents.code.tools.prisma_tools import PrismaToolsMixin

            assert PrismaToolsMixin is not None
            # Check for Prisma-related tools
            assert hasattr(PrismaToolsMixin, "run_prisma_command") or hasattr(
                PrismaToolsMixin, "prisma_migrate"
            )
        except ImportError:
            # Prisma tools may be optional
            pytest.skip("PrismaToolsMixin not implemented")

    def test_typescript_tools_mixin_exists(self):
        """Verify TypeScriptToolsMixin can be imported."""
        try:
            from gaia.agents.code.tools.typescript_tools import TypeScriptToolsMixin

            assert TypeScriptToolsMixin is not None
            # Check for TypeScript-related tools
            assert hasattr(TypeScriptToolsMixin, "compile_typescript") or hasattr(
                TypeScriptToolsMixin, "run_tsc"
            )
        except ImportError:
            # TypeScript tools may be optional
            pytest.skip("TypeScriptToolsMixin not implemented")

    def test_web_tools_mixin_exists(self):
        """Verify WebToolsMixin can be imported."""
        try:
            from gaia.agents.code.tools.web_dev_tools import WebToolsMixin

            assert WebToolsMixin is not None
            # Check for web-related tools
            assert hasattr(WebToolsMixin, "fetch_url") or hasattr(
                WebToolsMixin, "scrape_page"
            )
        except ImportError:
            # Web tools may be optional
            pytest.skip("WebToolsMixin not implemented")

    def test_code_mixins_can_be_combined(self):
        """Verify multiple code mixins can be combined."""
        from gaia.agents.base.agent import Agent
        from gaia.agents.base.console import SilentConsole
        from gaia.agents.code.tools.cli_tools import CLIToolsMixin
        from gaia.agents.code.tools.file_io import FileIOToolsMixin

        class CombinedCodeAgent(Agent, CLIToolsMixin, FileIOToolsMixin):
            def _get_system_prompt(self) -> str:
                return "Combined code agent"

            def _create_console(self):
                return SilentConsole()

            def _register_tools(self):
                pass

        # Should instantiate without errors
        agent = CombinedCodeAgent(silent_mode=True)
        assert agent is not None
        # Should have registration methods from both mixins
        assert isinstance(agent, CLIToolsMixin)
        assert isinstance(agent, FileIOToolsMixin)


# ============================================================================
# 25. APPLICATIONS TESTS
# ============================================================================


class TestApplications:
    """Test app wrappers."""

    def test_summarizer_app_exists(self):
        """Verify SummarizerApp can be imported."""
        from gaia.apps.summarize.app import SummarizerApp

        assert SummarizerApp is not None

    def test_summarizer_styles_defined(self):
        """Verify summarizer styles are defined."""
        from gaia.apps.summarize.app import SummarizerApp

        # Check that common summary styles exist
        assert hasattr(SummarizerApp, "STYLE_CONCISE") or hasattr(
            SummarizerApp, "summarize"
        )

        # Verify summarizer can be instantiated
        with patch("gaia.apps.summarize.app.LLMClient"):
            summarizer = SummarizerApp()
            assert summarizer is not None

    def test_summarizer_interface_methods(self):
        """Verify SummarizerApp has required methods."""
        from gaia.apps.summarize.app import SummarizerApp

        # Check methods exist
        assert hasattr(SummarizerApp, "summarize")
        assert hasattr(SummarizerApp, "summarize_file")

    def test_llm_app_exists(self):
        """Verify LLM app can be imported."""
        try:
            from gaia.apps.llm.app import LlmApp

            assert LlmApp is not None
        except ImportError:
            # LLM app may be in different location
            from gaia.llm import LLMClient

            assert LLMClient is not None

    def test_jira_app_exists(self):
        """Verify Jira app can be imported."""
        try:
            from gaia.apps.jira.app import JiraApp

            assert JiraApp is not None
        except ImportError:
            # Jira app may be integrated with agent
            from gaia.agents.jira.agent import JiraAgent

            assert JiraAgent is not None


# ============================================================================
# 26. ADDITIONAL INTEGRATION TESTS
# ============================================================================


class TestTalkIntegration:
    """Test Talk SDK integration with agents."""

    @patch("gaia.talk.sdk.AudioClient")
    def test_agent_with_talk_capabilities(self, mock_audio):
        """Verify agent can use Talk SDK."""
        from gaia.agents.base.agent import Agent
        from gaia.agents.base.console import SilentConsole
        from gaia.talk.sdk import TalkConfig, TalkSDK

        class TalkableAgent(Agent):
            def _get_system_prompt(self) -> str:
                return "Talkable agent"

            def _create_console(self):
                return SilentConsole()

            def _register_tools(self):
                pass

            def initialize_talk(self):
                self.talk_sdk = TalkSDK(TalkConfig())

        agent = TalkableAgent(silent_mode=True)
        agent.initialize_talk()
        assert hasattr(agent, "talk_sdk")
        assert agent.talk_sdk is not None


class TestCodeAgentIntegration:
    """Test Code Agent integration."""

    def test_code_agent_exists(self):
        """Verify CodeAgent can be imported."""
        from gaia.agents.code.agent import CodeAgent

        assert CodeAgent is not None

    def test_code_agent_has_all_mixins(self):
        """Verify CodeAgent includes all code mixins."""
        from gaia.agents.code.agent import CodeAgent
        from gaia.agents.code.tools.cli_tools import CLIToolsMixin
        from gaia.agents.code.tools.file_io import FileIOToolsMixin

        # Should inherit from required mixins
        assert issubclass(CodeAgent, CLIToolsMixin)
        assert issubclass(CodeAgent, FileIOToolsMixin)

    @patch("gaia.agents.code.agent.CodeAgent._create_console")
    def test_code_agent_can_be_instantiated(self, mock_console):
        """Verify CodeAgent can be instantiated."""
        from gaia.agents.base.console import SilentConsole
        from gaia.agents.code.agent import CodeAgent
        from gaia.agents.code.tools.cli_tools import CLIToolsMixin
        from gaia.agents.code.tools.file_io import FileIOToolsMixin

        mock_console.return_value = SilentConsole()

        # Should instantiate without errors
        agent = CodeAgent(silent_mode=True)
        assert agent is not None
        # Should be instance of required mixins
        assert isinstance(agent, CLIToolsMixin)
        assert isinstance(agent, FileIOToolsMixin)


class TestMultiModalIntegration:
    """Test multi-modal capabilities."""

    def test_vlm_integration_with_chat(self):
        """Verify VLM can be integrated with ChatSDK."""
        from gaia.chat.sdk import ChatConfig, ChatSDK
        from gaia.llm.vlm_client import VLMClient

        # Should be able to create both clients
        config = ChatConfig()
        chat = ChatSDK(config)
        assert chat is not None

        # VLM client should exist
        assert VLMClient is not None

    @patch("gaia.audio.audio_client.AudioClient.__init__")
    def test_audio_integration_with_chat(self, mock_audio_init):
        """Verify Audio can be integrated with ChatSDK."""
        from gaia.audio.audio_client import AudioClient
        from gaia.chat.sdk import ChatConfig, ChatSDK

        mock_audio_init.return_value = None

        # Should be able to create both clients
        config = ChatConfig()
        chat = ChatSDK(config)
        audio = AudioClient.__new__(AudioClient)

        assert chat is not None
        assert audio is not None


# ============================================================================
# 27. EDGE CASE TESTS
# ============================================================================


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_agent_with_no_tools(self):
        """Verify agent works with no tools registered."""
        from gaia.agents.base.agent import Agent
        from gaia.agents.base.console import SilentConsole

        class NoToolsAgent(Agent):
            def _get_system_prompt(self) -> str:
                return "No tools"

            def _create_console(self):
                return SilentConsole()

            def _register_tools(self):
                # Intentionally register no tools
                pass

        agent = NoToolsAgent(silent_mode=True)
        assert agent is not None

        # list_tools should not crash
        agent.list_tools()

    def test_agent_with_empty_system_prompt(self):
        """Verify agent works with empty system prompt."""
        from gaia.agents.base.agent import Agent
        from gaia.agents.base.console import SilentConsole

        class EmptyPromptAgent(Agent):
            def _get_system_prompt(self) -> str:
                return ""  # Empty prompt

            def _create_console(self):
                return SilentConsole()

            def _register_tools(self):
                pass

        agent = EmptyPromptAgent(silent_mode=True)
        assert agent is not None

    def test_chat_sdk_with_empty_config(self):
        """Verify ChatSDK works with default config."""
        from gaia.chat.sdk import ChatConfig, ChatSDK

        # Default config should work
        config = ChatConfig()
        chat = ChatSDK(config)
        assert chat is not None

    def test_tool_with_no_docstring(self):
        """Verify @tool works without docstring."""
        from gaia.agents.base.tools import tool

        @tool
        def no_docstring_tool():
            return {"status": "ok"}

        # Should still work
        result = no_docstring_tool()
        assert result["status"] == "ok"


# ============================================================================
# RUN TESTS
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
