# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for GAIA testing utilities."""

import unittest

from gaia.testing import (
    MockLLMProvider,
    MockToolExecutor,
    MockVLMClient,
    assert_agent_completed,
    assert_llm_called,
    assert_llm_prompt_contains,
    assert_no_errors,
    assert_result_has_keys,
    assert_result_value,
    assert_tool_args,
    assert_tool_called,
    assert_vlm_called,
    temp_directory,
    temp_file,
)


class TestMockLLMProvider(unittest.TestCase):
    """Tests for MockLLMProvider."""

    def test_can_be_imported(self):
        """Verify MockLLMProvider can be imported."""
        from gaia.testing import MockLLMProvider

        self.assertIsNotNone(MockLLMProvider)

    def test_returns_default_response(self):
        """Test MockLLMProvider returns default response."""
        mock = MockLLMProvider()
        result = mock.generate("test prompt")
        self.assertEqual(result, "Mock LLM response")

    def test_returns_configured_responses(self):
        """Test MockLLMProvider returns configured responses."""
        mock = MockLLMProvider(responses=["Response 1", "Response 2"])

        self.assertEqual(mock.generate("test"), "Response 1")
        self.assertEqual(mock.generate("test"), "Response 2")

    def test_cycles_through_responses(self):
        """Test responses cycle when exhausted."""
        mock = MockLLMProvider(responses=["A", "B"])

        self.assertEqual(mock.generate("1"), "A")
        self.assertEqual(mock.generate("2"), "B")
        self.assertEqual(mock.generate("3"), "A")  # Cycles back
        self.assertEqual(mock.generate("4"), "B")

    def test_tracks_call_history(self):
        """Test call history is tracked."""
        mock = MockLLMProvider()

        mock.generate("prompt 1", temperature=0.5)
        mock.generate("prompt 2", max_tokens=100)

        self.assertEqual(len(mock.call_history), 2)
        self.assertEqual(mock.call_history[0]["prompt"], "prompt 1")
        self.assertEqual(mock.call_history[0]["temperature"], 0.5)
        self.assertEqual(mock.call_history[1]["prompt"], "prompt 2")
        self.assertEqual(mock.call_history[1]["max_tokens"], 100)

    def test_was_called_property(self):
        """Test was_called property."""
        mock = MockLLMProvider()

        self.assertFalse(mock.was_called)
        mock.generate("test")
        self.assertTrue(mock.was_called)

    def test_call_count_property(self):
        """Test call_count property."""
        mock = MockLLMProvider()

        self.assertEqual(mock.call_count, 0)
        mock.generate("test")
        self.assertEqual(mock.call_count, 1)
        mock.generate("test")
        self.assertEqual(mock.call_count, 2)

    def test_last_prompt_property(self):
        """Test last_prompt property."""
        mock = MockLLMProvider()

        self.assertIsNone(mock.last_prompt)
        mock.generate("first")
        mock.generate("second")
        self.assertEqual(mock.last_prompt, "second")

    def test_reset(self):
        """Test reset clears history and index."""
        mock = MockLLMProvider(responses=["A", "B"])

        mock.generate("test")
        mock.generate("test")
        self.assertEqual(mock.call_count, 2)

        mock.reset()

        self.assertEqual(mock.call_count, 0)
        self.assertEqual(mock.generate("test"), "A")  # Starts from beginning

    def test_chat_method(self):
        """Test chat method with messages format."""
        mock = MockLLMProvider(responses=["Chat response"])

        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]

        result = mock.chat(messages)

        self.assertEqual(result, "Chat response")
        self.assertEqual(mock.call_history[0]["method"], "chat")
        self.assertEqual(mock.call_history[0]["prompt"], "Hello")

    def test_complete_alias(self):
        """Test complete is alias for generate."""
        mock = MockLLMProvider(responses=["Response"])

        result = mock.complete("test")

        self.assertEqual(result, "Response")
        self.assertEqual(mock.call_count, 1)


class TestMockVLMClient(unittest.TestCase):
    """Tests for MockVLMClient."""

    def test_can_be_imported(self):
        """Verify MockVLMClient can be imported."""
        from gaia.testing import MockVLMClient

        self.assertIsNotNone(MockVLMClient)

    def test_returns_configured_text(self):
        """Test returns configured extracted text."""
        mock = MockVLMClient(extracted_text="Extracted data")

        result = mock.extract_from_image(b"fake image bytes")

        self.assertEqual(result, "Extracted data")

    def test_tracks_call_history(self):
        """Test call history is tracked."""
        mock = MockVLMClient()

        mock.extract_from_image(b"image1", prompt="Extract text")
        mock.extract_from_image(b"image2", prompt="Get data")

        self.assertEqual(len(mock.call_history), 2)
        self.assertEqual(mock.call_history[0]["image_size"], 6)
        self.assertEqual(mock.call_history[0]["prompt"], "Extract text")
        self.assertEqual(mock.call_history[1]["image_size"], 6)

    def test_was_called_property(self):
        """Test was_called property."""
        mock = MockVLMClient()

        self.assertFalse(mock.was_called)
        mock.extract_from_image(b"test")
        self.assertTrue(mock.was_called)

    def test_check_availability(self):
        """Test check_availability returns configured value."""
        mock_available = MockVLMClient(is_available=True)
        mock_unavailable = MockVLMClient(is_available=False)

        self.assertTrue(mock_available.check_availability())
        self.assertFalse(mock_unavailable.check_availability())

    def test_extraction_results_sequence(self):
        """Test extraction_results returns in sequence."""
        mock = MockVLMClient(extraction_results=["Result 1", "Result 2"])

        self.assertEqual(mock.extract_from_image(b"img"), "Result 1")
        self.assertEqual(mock.extract_from_image(b"img"), "Result 2")
        self.assertEqual(mock.extract_from_image(b"img"), "Result 1")  # Cycles

    def test_extract_from_file(self):
        """Test extract_from_file method."""
        mock = MockVLMClient(extracted_text="File content")

        result = mock.extract_from_file("/path/to/file.png")

        self.assertEqual(result, "File content")
        self.assertEqual(mock.call_history[0]["file_path"], "/path/to/file.png")


class TestMockToolExecutor(unittest.TestCase):
    """Tests for MockToolExecutor."""

    def test_can_be_imported(self):
        """Verify MockToolExecutor can be imported."""
        from gaia.testing import MockToolExecutor

        self.assertIsNotNone(MockToolExecutor)

    def test_returns_configured_results(self):
        """Test returns configured tool results."""
        executor = MockToolExecutor(
            results={
                "search": {"results": ["item1", "item2"]},
                "create": {"id": 123},
            }
        )

        self.assertEqual(
            executor.execute("search", {"query": "test"}),
            {"results": ["item1", "item2"]},
        )
        self.assertEqual(
            executor.execute("create", {"name": "new"}),
            {"id": 123},
        )

    def test_returns_default_for_unknown_tools(self):
        """Test returns default result for unknown tools."""
        executor = MockToolExecutor(default_result={"ok": True})

        result = executor.execute("unknown_tool", {})

        self.assertEqual(result, {"ok": True})

    def test_was_tool_called(self):
        """Test was_tool_called method."""
        executor = MockToolExecutor()

        self.assertFalse(executor.was_tool_called("search"))
        executor.execute("search", {})
        self.assertTrue(executor.was_tool_called("search"))
        self.assertFalse(executor.was_tool_called("other"))

    def test_get_tool_calls(self):
        """Test get_tool_calls returns all calls for a tool."""
        executor = MockToolExecutor()

        executor.execute("search", {"q": "a"})
        executor.execute("other", {})
        executor.execute("search", {"q": "b"})

        search_calls = executor.get_tool_calls("search")
        self.assertEqual(len(search_calls), 2)
        self.assertEqual(search_calls[0]["args"]["q"], "a")
        self.assertEqual(search_calls[1]["args"]["q"], "b")

    def test_get_tool_args(self):
        """Test get_tool_args returns specific call args."""
        executor = MockToolExecutor()

        executor.execute("search", {"q": "first"})
        executor.execute("search", {"q": "second"})

        self.assertEqual(executor.get_tool_args("search", 0), {"q": "first"})
        self.assertEqual(executor.get_tool_args("search", 1), {"q": "second"})

    def test_tool_names_called(self):
        """Test tool_names_called property."""
        executor = MockToolExecutor()

        executor.execute("search", {})
        executor.execute("create", {})
        executor.execute("search", {})

        names = executor.tool_names_called
        self.assertIn("search", names)
        self.assertIn("create", names)
        self.assertEqual(len(names), 2)


class TestTempDirectory(unittest.TestCase):
    """Tests for temp_directory fixture."""

    def test_creates_directory(self):
        """Test temp_directory creates a directory."""
        with temp_directory() as tmp_dir:
            self.assertTrue(tmp_dir.exists())
            self.assertTrue(tmp_dir.is_dir())

    def test_directory_is_writable(self):
        """Test can create files in temp directory."""
        with temp_directory() as tmp_dir:
            test_file = tmp_dir / "test.txt"
            test_file.write_text("content")
            self.assertTrue(test_file.exists())
            self.assertEqual(test_file.read_text(), "content")

    def test_cleans_up_after_context(self):
        """Test directory is deleted after context exits."""
        created_path = None
        with temp_directory() as tmp_dir:
            created_path = tmp_dir
            (tmp_dir / "file.txt").write_text("data")

        self.assertFalse(created_path.exists())


class TestTempFile(unittest.TestCase):
    """Tests for temp_file fixture."""

    def test_creates_file(self):
        """Test temp_file creates a file."""
        with temp_file(content="hello") as tmp_path:
            self.assertTrue(tmp_path.exists())
            self.assertEqual(tmp_path.read_text(), "hello")

    def test_respects_suffix(self):
        """Test temp_file uses provided suffix."""
        with temp_file(suffix=".json") as tmp_path:
            self.assertTrue(tmp_path.name.endswith(".json"))

    def test_cleans_up_after_context(self):
        """Test file is deleted after context exits."""
        created_path = None
        with temp_file(content="test") as tmp_path:
            created_path = tmp_path

        self.assertFalse(created_path.exists())


class TestAssertions(unittest.TestCase):
    """Tests for assertion helpers."""

    def test_assert_llm_called_success(self):
        """Test assert_llm_called passes when LLM was called."""
        mock = MockLLMProvider()
        mock.generate("test")

        # Should not raise
        assert_llm_called(mock)
        assert_llm_called(mock, times=1)

    def test_assert_llm_called_failure(self):
        """Test assert_llm_called fails when LLM wasn't called."""
        mock = MockLLMProvider()

        with self.assertRaises(AssertionError):
            assert_llm_called(mock)

    def test_assert_llm_called_wrong_count(self):
        """Test assert_llm_called fails with wrong count."""
        mock = MockLLMProvider()
        mock.generate("test")
        mock.generate("test")

        with self.assertRaises(AssertionError):
            assert_llm_called(mock, times=1)

    def test_assert_llm_prompt_contains_success(self):
        """Test assert_llm_prompt_contains passes when text found."""
        mock = MockLLMProvider()
        mock.generate("Hello world")

        assert_llm_prompt_contains(mock, "world")

    def test_assert_llm_prompt_contains_failure(self):
        """Test assert_llm_prompt_contains fails when text not found."""
        mock = MockLLMProvider()
        mock.generate("Hello world")

        with self.assertRaises(AssertionError):
            assert_llm_prompt_contains(mock, "missing")

    def test_assert_vlm_called_success(self):
        """Test assert_vlm_called passes when VLM was called."""
        mock = MockVLMClient()
        mock.extract_from_image(b"test")

        assert_vlm_called(mock)
        assert_vlm_called(mock, times=1)

    def test_assert_vlm_called_failure(self):
        """Test assert_vlm_called fails when VLM wasn't called."""
        mock = MockVLMClient()

        with self.assertRaises(AssertionError):
            assert_vlm_called(mock)

    def test_assert_tool_called_success(self):
        """Test assert_tool_called passes when tool was called."""
        executor = MockToolExecutor()
        executor.execute("search", {"q": "test"})

        assert_tool_called(executor, "search")
        assert_tool_called(executor, "search", times=1)

    def test_assert_tool_called_failure(self):
        """Test assert_tool_called fails when tool wasn't called."""
        executor = MockToolExecutor()

        with self.assertRaises(AssertionError):
            assert_tool_called(executor, "search")

    def test_assert_tool_args_success(self):
        """Test assert_tool_args passes with matching args."""
        executor = MockToolExecutor()
        executor.execute("search", {"query": "test", "limit": 10})

        assert_tool_args(executor, "search", {"query": "test"})
        assert_tool_args(executor, "search", {"limit": 10})

    def test_assert_tool_args_failure(self):
        """Test assert_tool_args fails with mismatched args."""
        executor = MockToolExecutor()
        executor.execute("search", {"query": "test"})

        with self.assertRaises(AssertionError):
            assert_tool_args(executor, "search", {"query": "wrong"})

    def test_assert_result_has_keys_success(self):
        """Test assert_result_has_keys passes with all keys."""
        result = {"answer": "yes", "status": "ok"}

        assert_result_has_keys(result, ["answer", "status"])

    def test_assert_result_has_keys_failure(self):
        """Test assert_result_has_keys fails with missing keys."""
        result = {"answer": "yes"}

        with self.assertRaises(AssertionError):
            assert_result_has_keys(result, ["answer", "missing"])

    def test_assert_result_value_success(self):
        """Test assert_result_value passes with matching value."""
        result = {"status": "success"}

        assert_result_value(result, "status", "success")

    def test_assert_result_value_failure(self):
        """Test assert_result_value fails with mismatched value."""
        result = {"status": "error"}

        with self.assertRaises(AssertionError):
            assert_result_value(result, "status", "success")

    def test_assert_agent_completed_success(self):
        """Test assert_agent_completed passes with valid result."""
        result = {"answer": "The result"}

        assert_agent_completed(result)

    def test_assert_agent_completed_with_string(self):
        """Test assert_agent_completed handles string results."""
        assert_agent_completed("Some response")

    def test_assert_agent_completed_failure_with_error(self):
        """Test assert_agent_completed fails when result has error."""
        result = {"error": "Something went wrong"}

        with self.assertRaises(AssertionError):
            assert_agent_completed(result)

    def test_assert_no_errors_success(self):
        """Test assert_no_errors passes with clean result."""
        result = {"answer": "ok", "status": "success"}

        assert_no_errors(result)

    def test_assert_no_errors_failure(self):
        """Test assert_no_errors fails with error in result."""
        result = {"error": "Failed"}

        with self.assertRaises(AssertionError):
            assert_no_errors(result)


if __name__ == "__main__":
    unittest.main()
