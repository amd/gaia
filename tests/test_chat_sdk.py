#!/usr/bin/env python3
#
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Integration tests for the Chat SDK functionality with real LLM server.

These tests require a running Lemonade server and test actual LLM interactions.
"""

import sys
import time
import unittest

import requests

# Add src to path for imports
sys.path.insert(0, "src")

from gaia.chat.sdk import (
    ChatConfig,
    ChatSDK,
    ChatSession,
    SimpleChat,
    quick_chat,
    quick_chat_with_memory,
)
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME


class TestChatSDKIntegration(unittest.TestCase):
    """Integration tests for ChatSDK with real LLM server."""

    @classmethod
    def setUpClass(cls):
        """Set up test environment and verify server availability."""
        print(f"\n{'='*60}")
        print("CHAT SDK INTEGRATION TESTS - REAL LLM SERVER")
        print(f"{'='*60}")

        cls.server_url = "http://localhost:8000"
        cls.model = DEFAULT_MODEL_NAME
        cls.timeout = 30  # seconds

        # Verify server is running
        cls._wait_for_server()

        # Test basic server connectivity
        try:
            response = requests.get(f"{cls.server_url}/api/v1/health", timeout=5)
            if response.status_code == 200:
                health_data = response.json()
                print(f"✅ Server health check passed")
                print(f"   Status: {health_data.get('status', 'unknown')}")
                print(f"   Model loaded: {health_data.get('model_loaded', 'unknown')}")
            else:
                raise Exception(f"Server health check failed: {response.status_code}")
        except Exception as e:
            raise unittest.SkipTest(f"Cannot connect to LLM server: {e}")

    @classmethod
    def _wait_for_server(cls):
        """Wait for server to be ready."""
        print(f"⏳ Waiting for LLM server at {cls.server_url}...")

        start_time = time.time()
        while time.time() - start_time < cls.timeout:
            try:
                response = requests.get(f"{cls.server_url}/api/v1/health", timeout=5)
                if response.status_code == 200:
                    print(f"✅ LLM server is ready")
                    return
            except requests.RequestException as e:
                print(f"Server not ready yet: {e}")

            time.sleep(2)

        raise unittest.SkipTest(f"LLM server not available after {cls.timeout} seconds")

    def setUp(self):
        """Set up each test."""
        print(f"\n--- Starting {self._testMethodName} ---")

    def tearDown(self):
        """Clean up after each test."""
        print(f"--- Completed {self._testMethodName} ---")

    def test_basic_chat_sdk_functionality(self):
        """Test basic ChatSDK functionality with real LLM."""
        print("Testing basic ChatSDK with real LLM responses...")

        config = ChatConfig(
            model=self.model,
            max_tokens=50,
            show_stats=True,
            logging_level="INFO",
            assistant_name="assistant",
        )
        chat = ChatSDK(config)

        # Test simple response
        response = chat.send("Say exactly: Hello World")

        # Verify response structure
        self.assertIsNotNone(response.text)
        self.assertTrue(response.is_complete)
        self.assertIsNotNone(response.stats)

        # Verify response content
        self.assertIn("Hello", response.text)
        print(f"✅ Response received: {response.text[:50]}...")
        print(f"   Stats: {response.stats}")

        # Verify history was maintained
        history = chat.get_history()
        self.assertEqual(len(history), 2)
        self.assertTrue(history[0].startswith("user:"))
        self.assertTrue(history[1].startswith("assistant:"))

        print(f"✅ History maintained: {len(history)} entries")

    def test_conversation_memory_integration(self):
        """Test conversation memory with real LLM."""
        print("Testing conversation memory with real LLM...")

        config = ChatConfig(model=self.model, max_tokens=100, max_history_length=3)
        chat = ChatSDK(config)

        # Establish context
        response1 = chat.send("My name is TestUser. Remember this.")
        self.assertIsNotNone(response1.text)
        print(f"✅ Context established: {response1.text[:50]}...")

        # Test memory recall
        response2 = chat.send("What is my name?")
        self.assertIsNotNone(response2.text)

        # Should contain reference to the name (case-insensitive check)
        response_lower = response2.text.lower()
        self.assertTrue(
            "testuser" in response_lower
            or "test user" in response_lower
            or "name" in response_lower,
            f"Memory test failed. Response: {response2.text}",
        )
        print(f"✅ Memory recall successful: {response2.text[:50]}...")

        # Verify conversation history
        history = chat.get_formatted_history()
        self.assertEqual(len(history), 4)  # 2 user + 2 assistant messages

        user_messages = [h for h in history if h["role"] == "user"]
        self.assertEqual(len(user_messages), 2)

        print(f"✅ History format correct: {len(history)} entries")

    def test_streaming_integration(self):
        """Test streaming functionality with real LLM."""
        print("Testing streaming functionality with real LLM...")

        config = ChatConfig(model=self.model, max_tokens=50, assistant_name="assistant")
        chat = ChatSDK(config)

        # Test streaming response
        chunks = []
        complete_response = ""

        for chunk in chat.send_stream("Count from 1 to 5 slowly"):
            chunks.append(chunk)
            if not chunk.is_complete:
                complete_response += chunk.text
                print(f"Chunk: '{chunk.text}'", end="", flush=True)
            else:
                # Final chunk with metadata
                self.assertEqual(chunk.text, "")
                self.assertTrue(chunk.is_complete)
                break

        print()  # New line after streaming

        # Verify streaming worked
        self.assertGreater(len(chunks), 1)  # Should have multiple chunks
        self.assertGreater(len(complete_response), 0)

        # Verify history was updated with complete response
        history = chat.get_history()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[1], f"assistant: {complete_response}")

        print(
            f"✅ Streaming successful: {len(chunks)} chunks, response: {complete_response[:30]}..."
        )

    def test_simple_chat_integration(self):
        """Test SimpleChat interface with real LLM."""
        print("Testing SimpleChat interface with real LLM...")

        chat = SimpleChat(
            model=self.model,
            system_prompt="You are a helpful assistant. Be concise.",
            assistant_name="TestBot",
        )

        # Test basic ask
        response = chat.ask("What is 2+2?")
        self.assertIsNotNone(response)
        self.assertIsInstance(response, str)

        # Should contain answer (flexible check)
        self.assertTrue(
            "4" in response or "four" in response.lower(),
            f"Math question failed. Response: {response}",
        )
        print(f"✅ Simple ask successful: {response[:50]}...")

        # Test memory between calls
        chat.ask("My favorite color is blue.")
        memory_response = chat.ask("What is my favorite color?")

        self.assertTrue(
            "blue" in memory_response.lower(),
            f"Memory failed. Response: {memory_response}",
        )
        print(f"✅ Simple memory successful: {memory_response[:50]}...")

        # Test conversation retrieval
        conversation = chat.get_conversation()
        self.assertGreater(len(conversation), 0)
        self.assertIn("role", conversation[0])
        self.assertIn("message", conversation[0])

        print(f"✅ Conversation format correct: {len(conversation)} entries")

    def test_chat_session_integration(self):
        """Test ChatSession functionality with real LLM."""
        print("Testing ChatSession with real LLM...")

        sessions = ChatSession()

        # Create different sessions
        work_session = sessions.create_session(
            "work",
            model=self.model,
            system_prompt="You are a professional assistant.",
            assistant_name="WorkBot",
            max_tokens=50,
        )

        personal_session = sessions.create_session(
            "personal",
            model=self.model,
            system_prompt="You are a friendly companion.",
            assistant_name="Buddy",
            max_tokens=50,
        )

        # Test session isolation
        work_response = work_session.send("I need help with a presentation.")
        personal_response = personal_session.send("What's a good recipe for dinner?")

        # Verify responses
        self.assertIsNotNone(work_response.text)
        self.assertIsNotNone(personal_response.text)

        print(f"✅ Work session: {work_response.text[:40]}...")
        print(f"✅ Personal session: {personal_response.text[:40]}...")

        # Verify session isolation - histories should be separate
        work_history = work_session.get_history()
        personal_history = personal_session.get_history()

        self.assertEqual(len(work_history), 2)
        self.assertEqual(len(personal_history), 2)

        # History content should be different
        self.assertNotEqual(work_history[0], personal_history[0])

        print(f"✅ Session isolation confirmed")

        # Test session management
        session_list = sessions.list_sessions()
        self.assertIn("work", session_list)
        self.assertIn("personal", session_list)

        print(f"✅ Session management: {session_list}")

    def test_convenience_functions_integration(self):
        """Test convenience functions with real LLM."""
        print("Testing convenience functions with real LLM...")

        # Test quick_chat
        response = quick_chat(
            "Reply with just the word 'SUCCESS'",
            model=self.model,
            assistant_name="QuickBot",
        )

        self.assertIsNotNone(response)
        self.assertIsInstance(response, str)
        self.assertIn("SUCCESS", response.upper())

        print(f"✅ Quick chat: {response[:30]}...")

        # Test quick_chat_with_memory
        messages = [
            "I have a pet dog named Max.",
            "What is my pet's name?",
            "What kind of animal is Max?",
        ]

        responses = quick_chat_with_memory(
            messages, model=self.model, assistant_name="MemoryBot"
        )

        self.assertEqual(len(responses), 3)

        # Check memory worked - second response should mention Max
        self.assertTrue(
            "max" in responses[1].lower(),
            f"Memory failed in convenience function. Response: {responses[1]}",
        )

        # Third response should mention dog/animal
        self.assertTrue(
            any(word in responses[2].lower() for word in ["dog", "animal", "pet"]),
            f"Context failed in convenience function. Response: {responses[2]}",
        )

        print(f"✅ Memory chat responses: {len(responses)}")
        for i, resp in enumerate(responses):
            print(f"   {i+1}: {resp[:40]}...")

    def test_error_handling_integration(self):
        """Test error handling with real LLM server."""
        print("Testing error handling scenarios...")

        # Test with invalid model (should fallback gracefully)
        config = ChatConfig(model="nonexistent-model", max_tokens=20)
        chat = ChatSDK(config)

        # This might fail or fallback to default model
        try:
            response = chat.send("Hello")
            print(f"✅ Graceful handling: {response.text[:30]}...")
        except Exception as e:
            print(f"✅ Expected error caught: {type(e).__name__}")

        # Test empty message handling
        valid_chat = ChatSDK(ChatConfig(model=self.model))

        with self.assertRaises(ValueError):
            valid_chat.send("")

        with self.assertRaises(ValueError):
            list(valid_chat.send_stream(""))

        print(f"✅ Empty message validation working")

    def test_performance_integration(self):
        """Test performance characteristics with real LLM."""
        print("Testing performance characteristics...")

        config = ChatConfig(model=self.model, max_tokens=20, show_stats=True)
        chat = ChatSDK(config)

        # Measure response time
        start_time = time.time()
        response = chat.send("Say hello quickly")
        end_time = time.time()

        response_time = end_time - start_time

        # Verify we got stats
        self.assertIsNotNone(response.stats)

        # Basic performance checks
        self.assertLess(response_time, 30.0)  # Should respond within 30 seconds
        self.assertGreater(len(response.text), 0)

        print(f"✅ Response time: {response_time:.2f}s")
        print(f"✅ Stats available: {list(response.stats.keys())}")

        # Test streaming performance
        chunk_count = 0
        stream_start = time.time()

        for chunk in chat.send_stream("Count 1 2 3"):
            if not chunk.is_complete:
                chunk_count += 1

        stream_time = time.time() - stream_start

        self.assertGreater(chunk_count, 0)
        self.assertLess(stream_time, 30.0)

        print(f"✅ Streaming: {chunk_count} chunks in {stream_time:.2f}s")


def run_integration_tests():
    """Run integration tests with detailed output."""
    print("🚀 Starting Chat SDK Integration Tests")
    print("=" * 60)

    # Create test suite
    suite = unittest.TestLoader().loadTestsFromTestCase(TestChatSDKIntegration)

    # Run with verbose output
    runner = unittest.TextTestRunner(verbosity=2, stream=sys.stdout, buffer=False)

    result = runner.run(suite)

    # Summary
    print("\n" + "=" * 60)
    print("🏁 Integration Test Summary")
    print("=" * 60)

    if result.wasSuccessful():
        print("✅ ALL INTEGRATION TESTS PASSED")
        print(f"   Ran {result.testsRun} tests successfully")
    else:
        print("❌ INTEGRATION TESTS FAILED")
        print(f"   Ran {result.testsRun} tests")
        print(f"   Failures: {len(result.failures)}")
        print(f"   Errors: {len(result.errors)}")

        if result.failures:
            print("\nFailures:")
            for test, traceback in result.failures:
                print(f"   - {test}: {traceback}")

        if result.errors:
            print("\nErrors:")
            for test, traceback in result.errors:
                print(f"   - {test}: {traceback}")

    return result.wasSuccessful()


if __name__ == "__main__":
    # Allow running specific tests
    if len(sys.argv) > 1 and sys.argv[1].startswith("test_"):
        # Run specific test
        suite = unittest.TestSuite()
        suite.addTest(TestChatSDKIntegration(sys.argv[1]))
        runner = unittest.TextTestRunner(verbosity=2)
        result = runner.run(suite)
        sys.exit(0 if result.wasSuccessful() else 1)
    else:
        # Run all integration tests
        success = run_integration_tests()
        sys.exit(0 if success else 1)
