import os
import json
import time
import pytest
import logging
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
from websocket import WebSocketTimeoutException

from gaia.util import kill_process_on_port
from gaia.llm.lemonade_client import LemonadeClient

# Configure logging
logging.basicConfig(level=logging.INFO)

# Test configuration
LEMONADE_PORT = 8000
TEST_MODEL = "amd/Llama-3.2-1B-Instruct-awq-g128-int4-asym-fp16-onnx-hybrid"
TEST_DEVICE = "hybrid"

# ============================================================================
# Mock Tests
# ============================================================================


class TestLemonadeClientMock:
    """Mock test suite for the websocket-based LemonadeClient class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = LemonadeClient(
            model=TEST_MODEL, host="localhost", port=LEMONADE_PORT
        )
        self.base_url = f"http://localhost:{LEMONADE_PORT}"
        self.ws_url = f"ws://localhost:{LEMONADE_PORT}/ws"

    def test_initialization(self):
        """Test that the client initializes with the correct attributes."""
        client = LemonadeClient(model=TEST_MODEL, host="example.com", port=9000)
        assert client.model == TEST_MODEL
        assert client.host == "example.com"
        assert client.port == 9000
        assert client.base_url == "http://example.com:9000"
        assert client.ws_url == "ws://example.com:9000/ws"

    @patch("gaia.llm.lemonade_client.create_connection")
    def test_websocket_connection(self, mock_create_connection):
        """Test websocket connection establishment."""
        mock_ws = MagicMock()
        mock_create_connection.return_value = mock_ws
        self.client._connect_websocket()
        mock_create_connection.assert_called_once_with(self.ws_url, timeout=None)
        assert self.client.ws == mock_ws

    @patch("gaia.llm.lemonade_client.create_connection")
    def test_generate_completion_streaming(self, mock_create_connection):
        """Test generating a text completion in streaming mode."""
        mock_ws = MagicMock()
        mock_ws.recv.side_effect = ["First chunk", "Second chunk", "Final chunk</s>"]
        mock_create_connection.return_value = mock_ws

        chunks = list(
            self.client.generate_completion(
                "Test prompt", max_tokens=100, temperature=0.7, stream=True
            )
        )

        assert chunks == ["First chunk", "Second chunk", "Final chunk"]

    @pytest.mark.skip(reason="Chat completion not implemented yet")
    @patch("gaia.llm.lemonade_client.create_connection")
    def test_generate_chat_completion(self, mock_create_connection):
        """Test generating a chat completion."""
        mock_ws = MagicMock()
        mock_ws.recv.side_effect = ["Hello", " world!", "</s>"]
        mock_create_connection.return_value = mock_ws

        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Say hello!"},
        ]

        response = self.client.generate_chat_completion(messages, stream=False)
        sent_request = json.loads(mock_ws.send.call_args[0][0])
        expected_prompt = (
            "System: You are a helpful assistant.\nHuman: Say hello!\nAssistant:"
        )
        assert sent_request["prompt"] == expected_prompt
        assert response == "Hello world!"


# ============================================================================
# Integration Tests
# ============================================================================


class TestLemonadeClientIntegration:
    """Integration tests for the websocket-based LemonadeClient."""

    def setup_method(self):
        """Setup test fixtures."""
        print("\n=== Starting Lemonade Server ===")
        # Make sure the port is free
        kill_process_on_port(LEMONADE_PORT)

        # Initialize client and launch server
        self.client = LemonadeClient(
            model=TEST_MODEL, host="localhost", port=LEMONADE_PORT, verbose=True
        )

        # Launch server
        self.client.launch_llm_server(
            backend="oga",
            device=TEST_DEVICE,
            dtype="int4",
            max_new_tokens=100,
            cli_mode=True,
        )

    def teardown_method(self):
        """Cleanup after each test."""
        print("\n=== Stopping Lemonade Server ===")
        if hasattr(self, "client"):
            self.client.terminate_server()
        kill_process_on_port(LEMONADE_PORT)

    def test_health_check(self):
        """Test checking server health."""
        response = self.client.health_check()
        print(f"\nHealth check response: {response}")
        assert "model_loaded" in response
        assert response["model_loaded"] == TEST_MODEL

    def test_websocket_streaming(self):
        """Test websocket streaming functionality."""
        # Test streaming completion
        chunks = []
        for chunk in self.client.generate_completion(
            "Say 'Hello, World!'", stream=True, max_tokens=20
        ):
            assert isinstance(chunk, str)
            chunks.append(chunk)

        assert len(chunks) > 0
        complete_response = "".join(chunks)
        assert len(complete_response) > 0
        print(f"\nStreaming completion response: {complete_response}")

    def test_chat_completion(self):
        """Test chat completion functionality."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Say 'Hello, World!'"},
        ]

        # Test non-streaming chat
        response = self.client.generate_chat_completion(
            messages, stream=False, max_tokens=20
        )
        assert isinstance(response, str)
        assert len(response) > 0
        print(f"\nChat completion response: {response}")

        # Test streaming chat
        chunks = []
        for chunk in self.client.generate_chat_completion(
            messages, stream=True, max_tokens=20
        ):
            assert isinstance(chunk, str)
            chunks.append(chunk)

        assert len(chunks) > 0
        complete_response = "".join(chunks)
        assert len(complete_response) > 0
        print(f"\nStreaming chat completion response: {complete_response}")


if __name__ == "__main__":
    import sys

    args = ["-vs", __file__]

    # Check for -k option in command line arguments
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "-k" and i < len(sys.argv):
            args.extend(["-k", sys.argv[i + 1]])
            break

    pytest.main(args)
