# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Mock providers for testing GAIA agents without real LLM/VLM services."""

import logging
import time
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)


class MockLLMProvider:
    """
    Mock LLM provider for testing agents without real API calls.

    Returns pre-configured responses instead of calling a real LLM.
    Tracks all calls for test assertions.

    Example:
        from gaia.testing import MockLLMProvider

        mock_llm = MockLLMProvider(responses=["First response", "Second response"])

        # Use in tests
        result = mock_llm.generate("Test prompt")
        assert result == "First response"

        result = mock_llm.generate("Another prompt")
        assert result == "Second response"

        # Check call history
        assert len(mock_llm.call_history) == 2
        assert mock_llm.call_history[0]["prompt"] == "Test prompt"
    """

    def __init__(
        self,
        responses: Optional[List[str]] = None,
        tool_responses: Optional[Dict[str, Any]] = None,
        default_response: str = "Mock LLM response",
    ):
        """
        Initialize mock LLM provider.

        Args:
            responses: List of responses to return in sequence.
                      Cycles back to first if more calls than responses.
            tool_responses: Dict mapping tool names to their mock results.
                           Used when simulating tool calls.
            default_response: Response when responses list is exhausted or empty.
        """
        self.responses = responses or []
        self.tool_responses = tool_responses or {}
        self.default_response = default_response
        self.call_history: List[Dict[str, Any]] = []
        self._response_index = 0

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> str:
        """
        Generate mock response.

        Args:
            prompt: Input prompt (recorded but not processed)
            system_prompt: System prompt (recorded)
            temperature: Temperature setting (recorded)
            max_tokens: Max tokens (recorded)
            **kwargs: Additional parameters (recorded)

        Returns:
            Next response from response list, or default_response
        """
        self.call_history.append(
            {
                "method": "generate",
                "prompt": prompt,
                "system_prompt": system_prompt,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "kwargs": kwargs,
                "timestamp": time.time(),
            }
        )

        if self.responses:
            response = self.responses[self._response_index % len(self.responses)]
            self._response_index += 1
            return response

        return self.default_response

    def chat(
        self,
        messages: List[Dict[str, str]],
        **kwargs,
    ) -> str:
        """
        Mock chat completion (messages format).

        Args:
            messages: List of message dicts with 'role' and 'content'
            **kwargs: Additional parameters

        Returns:
            Next response from response list
        """
        # Extract the last user message as the prompt
        prompt = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                prompt = msg.get("content", "")
                break

        self.call_history.append(
            {
                "method": "chat",
                "messages": messages,
                "prompt": prompt,
                "kwargs": kwargs,
                "timestamp": time.time(),
            }
        )

        if self.responses:
            response = self.responses[self._response_index % len(self.responses)]
            self._response_index += 1
            return response

        return self.default_response

    def stream(
        self,
        prompt: str,
        **kwargs,
    ) -> Iterator[str]:
        """
        Mock streaming response.

        Yields the full response as a single chunk for simplicity.

        Args:
            prompt: Input prompt
            **kwargs: Additional parameters

        Yields:
            Response chunks (full response as single chunk)
        """
        response = self.generate(prompt, **kwargs)
        # Update the last call to note it was streaming
        if self.call_history:
            self.call_history[-1]["method"] = "stream"
        yield response

    def complete(self, prompt: str, **kwargs) -> str:
        """Alias for generate() for compatibility."""
        return self.generate(prompt, **kwargs)

    def get_tool_response(self, tool_name: str) -> Any:
        """
        Get mock response for a tool call.

        Args:
            tool_name: Name of the tool

        Returns:
            Configured mock result or default dict
        """
        return self.tool_responses.get(tool_name, {"status": "success"})

    @property
    def was_called(self) -> bool:
        """Check if any method was called."""
        return len(self.call_history) > 0

    @property
    def call_count(self) -> int:
        """Number of times LLM was called."""
        return len(self.call_history)

    @property
    def last_prompt(self) -> Optional[str]:
        """Get the last prompt that was sent."""
        if self.call_history:
            return self.call_history[-1].get("prompt")
        return None

    def reset(self) -> None:
        """Reset call history and response index."""
        self.call_history = []
        self._response_index = 0

    def set_responses(self, responses: List[str]) -> None:
        """
        Set new responses and reset index.

        Args:
            responses: New list of responses
        """
        self.responses = responses
        self._response_index = 0


class MockVLMClient:
    """
    Mock VLM client for testing image processing without real API calls.

    Returns pre-configured text instead of processing images.
    Tracks all calls for test assertions.

    Example:
        from gaia.testing import MockVLMClient

        mock_vlm = MockVLMClient(
            extracted_text='{"name": "John", "dob": "1990-01-01"}'
        )

        # Inject into agent
        agent = MyAgent()
        agent.vlm = mock_vlm

        # Test extraction
        result = agent.extract_form("test.png")

        # Verify VLM was called
        assert mock_vlm.was_called
        assert mock_vlm.call_count == 1
    """

    def __init__(
        self,
        extracted_text: str = "Mock extracted text",
        extraction_results: Optional[List[str]] = None,
        is_available: bool = True,
    ):
        """
        Initialize mock VLM client.

        Args:
            extracted_text: Default text to return from extract_from_image()
            extraction_results: List of results to return in sequence
            is_available: Whether check_availability() returns True
        """
        self.extracted_text = extracted_text
        self.extraction_results = extraction_results or []
        self.is_available = is_available
        self.call_history: List[Dict[str, Any]] = []
        self._result_index = 0

    def check_availability(self) -> bool:
        """
        Check if VLM is available.

        Returns:
            Configured is_available value
        """
        return self.is_available

    def extract_from_image(
        self,
        image_bytes: bytes,
        prompt: Optional[str] = None,
        **kwargs,
    ) -> str:
        """
        Mock image text extraction.

        Args:
            image_bytes: Image data (recorded but not processed)
            prompt: Extraction prompt (recorded)
            **kwargs: Additional parameters

        Returns:
            Pre-configured extracted text
        """
        self.call_history.append(
            {
                "method": "extract_from_image",
                "image_size": len(image_bytes) if image_bytes else 0,
                "prompt": prompt,
                "kwargs": kwargs,
                "timestamp": time.time(),
            }
        )

        if self.extraction_results:
            result = self.extraction_results[
                self._result_index % len(self.extraction_results)
            ]
            self._result_index += 1
            return result

        return self.extracted_text

    def extract_from_file(
        self,
        file_path: str,
        prompt: Optional[str] = None,
        **kwargs,
    ) -> str:
        """
        Mock file-based extraction.

        Args:
            file_path: Path to image file
            prompt: Extraction prompt

        Returns:
            Pre-configured extracted text
        """
        self.call_history.append(
            {
                "method": "extract_from_file",
                "file_path": file_path,
                "prompt": prompt,
                "kwargs": kwargs,
                "timestamp": time.time(),
            }
        )

        if self.extraction_results:
            result = self.extraction_results[
                self._result_index % len(self.extraction_results)
            ]
            self._result_index += 1
            return result

        return self.extracted_text

    def describe_image(
        self,
        image_bytes: bytes,
        prompt: Optional[str] = None,
        **kwargs,
    ) -> str:
        """
        Mock image description.

        Args:
            image_bytes: Image data
            prompt: Description prompt

        Returns:
            Pre-configured text
        """
        return self.extract_from_image(image_bytes, prompt, **kwargs)

    @property
    def was_called(self) -> bool:
        """Check if any extraction method was called."""
        return len(self.call_history) > 0

    @property
    def call_count(self) -> int:
        """Number of times extraction was called."""
        return len(self.call_history)

    @property
    def last_prompt(self) -> Optional[str]:
        """Get the last prompt that was sent."""
        if self.call_history:
            return self.call_history[-1].get("prompt")
        return None

    def reset(self) -> None:
        """Reset call history and result index."""
        self.call_history = []
        self._result_index = 0

    def set_extracted_text(self, text: str) -> None:
        """
        Set new extracted text.

        Args:
            text: New text to return
        """
        self.extracted_text = text


class MockToolExecutor:
    """
    Mock tool executor for testing tool calls.

    Tracks tool calls and returns configurable results.

    Example:
        from gaia.testing import MockToolExecutor

        executor = MockToolExecutor(
            results={
                "search": {"results": ["item1", "item2"]},
                "create_record": {"id": 123, "status": "created"},
            }
        )

        result = executor.execute("search", {"query": "test"})
        assert result == {"results": ["item1", "item2"]}

        assert executor.was_tool_called("search")
        assert executor.get_tool_args("search") == {"query": "test"}
    """

    def __init__(
        self,
        results: Optional[Dict[str, Any]] = None,
        default_result: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize mock tool executor.

        Args:
            results: Dict mapping tool names to their results
            default_result: Default result for unknown tools
        """
        self.results = results or {}
        self.default_result = default_result or {"status": "success"}
        self.call_history: List[Dict[str, Any]] = []

    def execute(self, tool_name: str, args: Dict[str, Any]) -> Any:
        """
        Execute a mock tool.

        Args:
            tool_name: Name of the tool
            args: Tool arguments

        Returns:
            Configured result for the tool
        """
        self.call_history.append(
            {
                "tool": tool_name,
                "args": args,
                "timestamp": time.time(),
            }
        )

        return self.results.get(tool_name, self.default_result)

    def was_tool_called(self, tool_name: str) -> bool:
        """
        Check if a specific tool was called.

        Args:
            tool_name: Name of the tool

        Returns:
            True if tool was called at least once
        """
        return any(call["tool"] == tool_name for call in self.call_history)

    def get_tool_calls(self, tool_name: str) -> List[Dict[str, Any]]:
        """
        Get all calls to a specific tool.

        Args:
            tool_name: Name of the tool

        Returns:
            List of call records for that tool
        """
        return [call for call in self.call_history if call["tool"] == tool_name]

    def get_tool_args(self, tool_name: str, call_index: int = 0) -> Optional[Dict]:
        """
        Get arguments from a specific tool call.

        Args:
            tool_name: Name of the tool
            call_index: Which call to get (0 = first call)

        Returns:
            Arguments dict or None if not found
        """
        calls = self.get_tool_calls(tool_name)
        if call_index < len(calls):
            return calls[call_index]["args"]
        return None

    @property
    def tool_names_called(self) -> List[str]:
        """Get list of all tool names that were called."""
        return list(set(call["tool"] for call in self.call_history))

    def reset(self) -> None:
        """Reset call history."""
        self.call_history = []
