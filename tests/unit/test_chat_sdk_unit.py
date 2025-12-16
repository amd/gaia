# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Unit tests for Chat SDK functionality.

These tests use mocks to test SDK logic without requiring a running LLM server.
"""

import sys
import unittest
from unittest.mock import MagicMock, patch

# Add src to path for imports
sys.path.insert(0, "src")


class TestHistoryToMessages(unittest.TestCase):
    """Unit tests for _history_to_messages() helper method."""

    def setUp(self):
        """Set up test fixtures with mocked LLMClient."""
        with patch("gaia.chat.sdk.LLMClient"):
            from gaia.chat.sdk import ChatConfig, ChatSDK

            self.config = ChatConfig(
                model="test-model",
                system_prompt="You are a helpful assistant.",
                assistant_name="gaia",
            )
            self.chat = ChatSDK(self.config)

    def test_empty_history_with_system_prompt(self):
        """Test that empty history returns only system prompt."""
        messages = self.chat._history_to_messages()

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], "You are a helpful assistant.")

    def test_empty_history_no_system_prompt(self):
        """Test that empty history with no system prompt returns empty list."""
        self.chat.config.system_prompt = None
        messages = self.chat._history_to_messages()

        self.assertEqual(len(messages), 0)

    def test_user_message_conversion(self):
        """Test that user messages are correctly converted."""
        self.chat.chat_history.append("user: Hello, how are you?")
        messages = self.chat._history_to_messages()

        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")
        self.assertEqual(messages[1]["content"], "Hello, how are you?")

    def test_assistant_message_conversion(self):
        """Test that assistant messages are correctly converted."""
        self.chat.chat_history.append("user: Hello")
        self.chat.chat_history.append("gaia: Hi there!")
        messages = self.chat._history_to_messages()

        self.assertEqual(len(messages), 3)
        self.assertEqual(messages[1]["role"], "user")
        self.assertEqual(messages[2]["role"], "assistant")
        self.assertEqual(messages[2]["content"], "Hi there!")

    def test_full_conversation(self):
        """Test a full multi-turn conversation."""
        self.chat.chat_history.append("user: My name is Alice")
        self.chat.chat_history.append("gaia: Nice to meet you, Alice!")
        self.chat.chat_history.append("user: What's my name?")
        messages = self.chat._history_to_messages()

        self.assertEqual(len(messages), 4)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")
        self.assertEqual(messages[1]["content"], "My name is Alice")
        self.assertEqual(messages[2]["role"], "assistant")
        self.assertEqual(messages[2]["content"], "Nice to meet you, Alice!")
        self.assertEqual(messages[3]["role"], "user")
        self.assertEqual(messages[3]["content"], "What's my name?")

    def test_enhanced_last_message(self):
        """Test that enhanced_last_message replaces last user message content."""
        self.chat.chat_history.append("user: What is AI?")
        enhanced = "Context: AI is artificial intelligence.\n\nUser question: What is AI?"
        messages = self.chat._history_to_messages(enhanced_last_message=enhanced)

        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[1]["role"], "user")
        self.assertEqual(messages[1]["content"], enhanced)

    def test_enhanced_message_only_affects_last_user(self):
        """Test that enhanced message only affects the last user message."""
        self.chat.chat_history.append("user: First question")
        self.chat.chat_history.append("gaia: First answer")
        self.chat.chat_history.append("user: Second question")
        enhanced = "Enhanced second question"
        messages = self.chat._history_to_messages(enhanced_last_message=enhanced)

        self.assertEqual(messages[1]["content"], "First question")  # Unchanged
        self.assertEqual(messages[3]["content"], enhanced)  # Enhanced

    def test_custom_assistant_name(self):
        """Test that custom assistant name is correctly handled."""
        self.chat.config.assistant_name = "CustomBot"
        self.chat.chat_history.append("user: Hello")
        self.chat.chat_history.append("CustomBot: Hi!")
        messages = self.chat._history_to_messages()

        self.assertEqual(len(messages), 3)
        self.assertEqual(messages[2]["role"], "assistant")
        self.assertEqual(messages[2]["content"], "Hi!")


class TestNoHistoryParameter(unittest.TestCase):
    """Unit tests for the no_history parameter on send() and send_stream()."""

    def setUp(self):
        """Set up test fixtures with mocked LLMClient."""
        self.mock_llm_client = MagicMock()
        self.mock_llm_client.generate.return_value = "Test response"

        with patch("gaia.chat.sdk.LLMClient", return_value=self.mock_llm_client):
            from gaia.chat.sdk import ChatConfig, ChatSDK

            self.config = ChatConfig(
                model="test-model",
                system_prompt="System prompt",
                assistant_name="gaia",
            )
            self.chat = ChatSDK(self.config)

    def test_send_with_history_updates_chat_history(self):
        """Test that send() without no_history updates chat history."""
        self.chat.send("Hello")

        self.assertEqual(len(self.chat.chat_history), 2)
        self.assertEqual(self.chat.chat_history[0], "user: Hello")
        self.assertEqual(self.chat.chat_history[1], "gaia: Test response")

    def test_send_with_no_history_does_not_update(self):
        """Test that send() with no_history=True does not update chat history."""
        self.chat.send("Hello", no_history=True)

        self.assertEqual(len(self.chat.chat_history), 0)

    def test_send_no_history_still_uses_system_prompt(self):
        """Test that no_history still includes system prompt."""
        self.chat.send("Hello", no_history=True)

        # Check the messages passed to generate()
        call_kwargs = self.mock_llm_client.generate.call_args[1]
        messages = call_kwargs.get("messages", [])

        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], "System prompt")
        self.assertEqual(messages[1]["role"], "user")
        self.assertEqual(messages[1]["content"], "Hello")

    def test_send_stream_with_history_updates_chat_history(self):
        """Test that send_stream() without no_history updates chat history."""
        self.mock_llm_client.generate.return_value = iter(["Test ", "response"])

        chunks = list(self.chat.send_stream("Hello"))

        self.assertEqual(len(self.chat.chat_history), 2)
        self.assertEqual(self.chat.chat_history[0], "user: Hello")
        self.assertEqual(self.chat.chat_history[1], "gaia: Test response")

    def test_send_stream_with_no_history_does_not_update(self):
        """Test that send_stream() with no_history=True does not update chat history."""
        self.mock_llm_client.generate.return_value = iter(["Test ", "response"])

        chunks = list(self.chat.send_stream("Hello", no_history=True))

        self.assertEqual(len(self.chat.chat_history), 0)


class TestSendMessagesMethod(unittest.TestCase):
    """Unit tests for send_messages() and send_messages_stream() methods."""

    def setUp(self):
        """Set up test fixtures with mocked LLMClient."""
        self.mock_llm_client = MagicMock()
        self.mock_llm_client.generate.return_value = "Test response"

        with patch("gaia.chat.sdk.LLMClient", return_value=self.mock_llm_client):
            from gaia.chat.sdk import ChatConfig, ChatSDK

            self.config = ChatConfig(
                model="test-model",
                assistant_name="gaia",
            )
            self.chat = ChatSDK(self.config)

    def test_send_messages_basic(self):
        """Test basic send_messages() functionality."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
        ]

        response = self.chat.send_messages(messages)

        self.assertEqual(response.text, "Test response")
        self.assertTrue(response.is_complete)

        # Verify messages were passed to LLM
        call_kwargs = self.mock_llm_client.generate.call_args[1]
        passed_messages = call_kwargs.get("messages", [])
        self.assertEqual(len(passed_messages), 3)

    def test_send_messages_with_system_prompt(self):
        """Test that system_prompt parameter is prepended."""
        messages = [{"role": "user", "content": "Hello"}]

        self.chat.send_messages(messages, system_prompt="Be helpful")

        call_kwargs = self.mock_llm_client.generate.call_args[1]
        passed_messages = call_kwargs.get("messages", [])

        self.assertEqual(passed_messages[0]["role"], "system")
        self.assertEqual(passed_messages[0]["content"], "Be helpful")
        self.assertEqual(passed_messages[1]["role"], "user")

    def test_send_messages_skips_duplicate_system(self):
        """Test that incoming system messages are skipped if we add one."""
        messages = [
            {"role": "system", "content": "Original system"},
            {"role": "user", "content": "Hello"},
        ]

        self.chat.send_messages(messages, system_prompt="Override system")

        call_kwargs = self.mock_llm_client.generate.call_args[1]
        passed_messages = call_kwargs.get("messages", [])

        # Should only have our system prompt, not the original
        system_messages = [m for m in passed_messages if m["role"] == "system"]
        self.assertEqual(len(system_messages), 1)
        self.assertEqual(system_messages[0]["content"], "Override system")

    def test_send_messages_tool_role_conversion(self):
        """Test that tool messages are converted to assistant messages."""
        messages = [
            {"role": "user", "content": "What's the weather?"},
            {"role": "assistant", "content": "Let me check..."},
            {"role": "tool", "name": "weather_api", "content": "Sunny, 72F"},
        ]

        self.chat.send_messages(messages)

        call_kwargs = self.mock_llm_client.generate.call_args[1]
        passed_messages = call_kwargs.get("messages", [])

        # Tool message should be converted
        tool_msg = passed_messages[2]
        self.assertEqual(tool_msg["role"], "assistant")
        self.assertIn("[tool:weather_api]", tool_msg["content"])
        self.assertIn("Sunny, 72F", tool_msg["content"])

    def test_send_messages_stream(self):
        """Test send_messages_stream() functionality."""
        self.mock_llm_client.generate.return_value = iter(["Hello ", "world"])

        messages = [{"role": "user", "content": "Hi"}]
        chunks = list(self.chat.send_messages_stream(messages))

        # Should have content chunks + final chunk
        content_chunks = [c for c in chunks if not c.is_complete]
        final_chunk = [c for c in chunks if c.is_complete]

        self.assertEqual(len(content_chunks), 2)
        self.assertEqual(len(final_chunk), 1)
        self.assertEqual(content_chunks[0].text, "Hello ")
        self.assertEqual(content_chunks[1].text, "world")


class TestLLMClientMessagesHandling(unittest.TestCase):
    """Unit tests for LLMClient messages parameter handling."""

    def test_claude_endpoint_tool_message_handling(self):
        """Test that Claude endpoint handles tool messages."""
        with patch("gaia.llm.llm_client.OpenAI"):
            with patch("gaia.llm.llm_client.CLAUDE_AVAILABLE", True):
                with patch("gaia.llm.llm_client.AnthropicClaudeClient") as mock_claude:
                    mock_instance = MagicMock()
                    mock_instance.get_completion.return_value = "Response"
                    mock_claude.return_value = mock_instance

                    from gaia.llm.llm_client import LLMClient

                    client = LLMClient(use_claude=True)

                    messages = [
                        {"role": "user", "content": "Check weather"},
                        {"role": "tool", "name": "weather", "content": "Sunny"},
                    ]

                    client.generate("", messages=messages)

                    # Verify the prompt passed to Claude includes tool message
                    call_args = mock_instance.get_completion.call_args[0][0]
                    self.assertIn("Tool (weather)", call_args)
                    self.assertIn("Sunny", call_args)

    def test_claude_endpoint_unknown_role_handling(self):
        """Test that Claude endpoint handles unknown roles gracefully."""
        with patch("gaia.llm.llm_client.OpenAI"):
            with patch("gaia.llm.llm_client.CLAUDE_AVAILABLE", True):
                with patch("gaia.llm.llm_client.AnthropicClaudeClient") as mock_claude:
                    mock_instance = MagicMock()
                    mock_instance.get_completion.return_value = "Response"
                    mock_claude.return_value = mock_instance

                    from gaia.llm.llm_client import LLMClient

                    client = LLMClient(use_claude=True)

                    messages = [
                        {"role": "user", "content": "Hello"},
                        {"role": "custom_role", "content": "Custom content"},
                    ]

                    client.generate("", messages=messages)

                    # Verify the prompt includes the unknown role (title-cased)
                    call_args = mock_instance.get_completion.call_args[0][0]
                    self.assertIn("Custom_Role:", call_args)
                    self.assertIn("Custom content", call_args)


class TestNormalizeMessageContent(unittest.TestCase):
    """Unit tests for _normalize_message_content() method."""

    def setUp(self):
        """Set up test fixtures."""
        with patch("gaia.chat.sdk.LLMClient"):
            from gaia.chat.sdk import ChatConfig, ChatSDK

            self.chat = ChatSDK(ChatConfig())

    def test_string_content(self):
        """Test that string content is returned as-is."""
        result = self.chat._normalize_message_content("Hello world")
        self.assertEqual(result, "Hello world")

    def test_list_with_text_blocks(self):
        """Test handling of OpenAI-style content blocks."""
        content = [
            {"type": "text", "text": "First part"},
            {"type": "text", "text": "Second part"},
        ]
        result = self.chat._normalize_message_content(content)
        self.assertIn("First part", result)
        self.assertIn("Second part", result)

    def test_dict_content(self):
        """Test that dict content is JSON serialized."""
        content = {"key": "value"}
        result = self.chat._normalize_message_content(content)
        self.assertIn("key", result)
        self.assertIn("value", result)


def run_unit_tests():
    """Run unit tests with detailed output."""
    print("Running Chat SDK Unit Tests (no server required)")
    print("=" * 60)

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add all test classes
    suite.addTests(loader.loadTestsFromTestCase(TestHistoryToMessages))
    suite.addTests(loader.loadTestsFromTestCase(TestNoHistoryParameter))
    suite.addTests(loader.loadTestsFromTestCase(TestSendMessagesMethod))
    suite.addTests(loader.loadTestsFromTestCase(TestLLMClientMessagesHandling))
    suite.addTests(loader.loadTestsFromTestCase(TestNormalizeMessageContent))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "=" * 60)
    if result.wasSuccessful():
        print("ALL UNIT TESTS PASSED")
    else:
        print("UNIT TESTS FAILED")
        print(f"Failures: {len(result.failures)}")
        print(f"Errors: {len(result.errors)}")

    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_unit_tests()
    sys.exit(0 if success else 1)
