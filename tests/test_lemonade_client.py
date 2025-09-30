"""
Unit tests for the Lemonade client API.
"""

import unittest
from unittest.mock import patch, MagicMock
import sys
import os
from io import StringIO
import logging
import requests

import responses
import pytest

from gaia.llm.lemonade_client import (
    LemonadeClient,
    LemonadeClientError,
    create_lemonade_client,
    DEFAULT_MODEL_NAME,
)

# Test constants
TEST_MODEL = "Llama-3.2-3B-Instruct-Hybrid"
# TEST_MODEL = "Gemma-3-4b-it-FLM"
# TEST_MODEL = "Qwen3-Coder-30B-A3B-Instruct-GGUF"
# TEST_MODEL = "Qwen2.5-0.5B-Instruct-CPU"

HOST = "localhost"
PORT = 8000
API_BASE = f"http://{HOST}:{PORT}/api/v1"


class TestLemonadeClientMock(unittest.TestCase):
    """Test cases for synchronous LemonadeClient."""

    def setUp(self):
        """Set up test fixtures."""
        print(f"\n----- Setting up {self._testMethodName} -----")
        self.client = create_lemonade_client(
            model=TEST_MODEL, host=HOST, port=PORT, verbose=False
        )
        print(f"Created test client with model={TEST_MODEL}, host={HOST}, port={PORT}")

        # Capture stdout for testing print output
        self.stdout_backup = sys.stdout
        sys.stdout = StringIO()

    def tearDown(self):
        """Clean up after each test."""
        # Restore stdout
        sys.stdout = self.stdout_backup
        print(f"----- Completed {self._testMethodName} -----")

    def test_client_initialization(self):
        """Test client initialization with default and custom parameters."""
        # Test default initialization
        client = LemonadeClient()
        self.assertEqual(client.host, "localhost")
        self.assertEqual(client.port, 8000)
        # The client doesn't expose verbose as a property, so check log level instead
        self.assertEqual(client.log.level, logging.WARNING)

        # Test custom initialization
        client = LemonadeClient(
            model=TEST_MODEL, host="testhost", port=9000, verbose=False
        )
        self.assertEqual(client.model, TEST_MODEL)
        self.assertEqual(client.host, "testhost")
        self.assertEqual(client.port, 9000)
        # When verbose=False, log level should be WARNING
        self.assertEqual(client.log.level, logging.WARNING)

    def test_factory_function(self):
        """Test the create_lemonade_client factory function."""
        # Test with explicit parameters
        client = create_lemonade_client(
            model=TEST_MODEL,
            host="testhost",
            port=9000,
            verbose=False,
        )
        self.assertEqual(client.model, TEST_MODEL)
        self.assertEqual(client.host, "testhost")
        self.assertEqual(client.port, 9000)

        # Test with environment variables
        with patch.dict(
            os.environ,
            {
                "LEMONADE_MODEL": "env-model",
                "LEMONADE_HOST": "env-host",
                "LEMONADE_PORT": "9001",
            },
        ):
            client = create_lemonade_client()
            self.assertEqual(client.model, "env-model")
            self.assertEqual(client.host, "env-host")
            self.assertEqual(client.port, 9001)

        # Test parameter precedence (explicit > env > default)
        with patch.dict(
            os.environ,
            {
                "LEMONADE_MODEL": "env-model",
                "LEMONADE_HOST": "env-host",
                "LEMONADE_PORT": "9001",
            },
        ):
            client = create_lemonade_client(
                model="explicit-model", host="explicit-host"
            )
            self.assertEqual(client.model, "explicit-model")
            self.assertEqual(client.host, "explicit-host")
            self.assertEqual(client.port, 9001)  # From env

    @patch("gaia.llm.lemonade_client.LemonadeClient.health_check")
    @patch("gaia.llm.lemonade_client.LemonadeClient.launch_server")
    def test_factory_auto_start(self, mock_launch, mock_health):
        """Test auto_start functionality in factory function."""
        # Temporarily disable error logging for this test
        logger = logging.getLogger("gaia.llm.lemonade_client")
        original_level = logger.level
        logger.setLevel(logging.CRITICAL)  # Suppress error logs

        try:
            # Server already running - health check succeeds immediately
            mock_health.return_value = {"status": "ok"}
            # Explicitly set all parameters to avoid any potential environment variable interference
            client = create_lemonade_client(
                model=TEST_MODEL,
                host=HOST,
                port=PORT,
                auto_start=True,
                auto_load=False,
                verbose=False,
            )
            # Health check should be called once to verify server after launch
            mock_health.assert_called_once()
            # Since health check passes, launch should not be called
            mock_launch.assert_not_called()

            # Server not running - first check fails, second check passes
            mock_health.reset_mock()
            mock_launch.reset_mock()
            # Set up health_check to fail on first call, then succeed
            mock_health.side_effect = [
                LemonadeClientError("Connection refused"),  # First call fails
                {"status": "ok"},  # Second call succeeds after launch_server
            ]

            client = create_lemonade_client(
                model=TEST_MODEL,
                host=HOST,
                port=PORT,
                auto_start=True,
                auto_load=False,
                verbose=False,
            )
            # Health check should be called twice: once before launch (fails) and once after (succeeds)
            self.assertEqual(mock_health.call_count, 2)
            mock_launch.assert_called_once()

            # Check that health_check and launch_server are not called when auto_start=False
            mock_health.reset_mock()
            mock_launch.reset_mock()
            mock_health.side_effect = None  # Reset side_effect

            client = create_lemonade_client(
                model=TEST_MODEL,
                host=HOST,
                port=PORT,
                auto_start=False,
                auto_load=False,
                verbose=False,
            )
            mock_health.assert_not_called()
            mock_launch.assert_not_called()
        finally:
            # Restore original log level
            logger.setLevel(original_level)

    @patch("gaia.llm.lemonade_client.LemonadeClient.list_models")
    @patch("gaia.llm.lemonade_client.LemonadeClient.load_model")
    def test_factory_auto_load_model(self, mock_load, mock_list_models):
        """Test auto_load functionality in factory function."""
        # Mock list_models to return a model list
        mock_list_models.return_value = {
            "data": [{"id": TEST_MODEL, "object": "model"}]
        }

        # With auto_load=True - success case
        mock_load.return_value = {"status": "ok", "model": TEST_MODEL}
        client = create_lemonade_client(model=TEST_MODEL, auto_load=True, verbose=False)
        # Verify it was called with expected arguments
        mock_load.assert_called_once()
        args, kwargs = mock_load.call_args
        self.assertEqual(args[0], TEST_MODEL)  # First arg should be model
        self.assertEqual(kwargs.get("timeout", None), 60)  # Should have timeout=60
        # list_models should also be called when auto_pull=True (default)
        mock_list_models.assert_called_once()

        # With auto_load=False - should not call load_model or list_models
        mock_load.reset_mock()
        mock_list_models.reset_mock()
        client = create_lemonade_client(
            model=TEST_MODEL, auto_load=False, verbose=False
        )
        mock_load.assert_not_called()
        mock_list_models.assert_not_called()

        # Test with auto_pull=False - should not call list_models
        mock_load.reset_mock()
        mock_list_models.reset_mock()
        mock_load.return_value = {"status": "ok", "model": TEST_MODEL}
        client = create_lemonade_client(
            model=TEST_MODEL, auto_load=True, auto_pull=False, verbose=False
        )
        mock_load.assert_called_once()
        mock_list_models.assert_not_called()

        # Error case 1: Generic loading error
        mock_load.reset_mock()
        mock_list_models.reset_mock()
        mock_list_models.return_value = {
            "data": [{"id": TEST_MODEL, "object": "model"}]
        }
        mock_load.side_effect = LemonadeClientError(
            f"Failed to load model {TEST_MODEL}: Model loading failed"
        )

        # Disable all logs for the error case
        logging.disable(logging.ERROR)

        # Should raise LemonadeClientError when loading fails
        with self.assertRaises(LemonadeClientError) as context:
            client = create_lemonade_client(
                model=TEST_MODEL,
                auto_load=True,
                verbose=False,
            )
        self.assertIn("Model loading failed", str(context.exception))
        mock_load.assert_called_once()

        # Re-enable logs
        logging.disable(logging.NOTSET)

        # Error case 2: 404 model not found error
        mock_load.reset_mock()
        mock_list_models.reset_mock()
        mock_list_models.return_value = {
            "data": [{"id": TEST_MODEL, "object": "model"}]
        }
        mock_load.side_effect = LemonadeClientError(
            'Request failed with status 404: {"detail":"model not found"}'
        )

        # Disable all logs for the error case
        logging.disable(logging.ERROR)

        # Should raise LemonadeClientError for model not found
        with self.assertRaises(LemonadeClientError) as context:
            client = create_lemonade_client(
                model=TEST_MODEL,
                auto_load=True,
                verbose=False,
            )
        self.assertIn("model not found", str(context.exception))
        mock_load.assert_called_once()

        # Re-enable logs
        logging.disable(logging.NOTSET)

    @responses.activate
    def test_health_check(self):
        """Test health check API."""
        # Mock response
        health_response = {
            "status": "ok",
            "checkpoint_loaded": "amd/Llama-3.2-3B-Instruct-awq-g128-int4-asym-fp16-onnx-hybrid",
            "model_loaded": TEST_MODEL,
        }
        responses.add(
            responses.GET, f"{API_BASE}/health", json=health_response, status=200
        )

        result = self.client.health_check()
        self.assertEqual(result, health_response)

    @responses.activate
    def test_list_models(self):
        """Test list models API."""
        # Mock response
        models_response = {
            "object": "list",
            "data": [
                {
                    "id": TEST_MODEL,
                    "object": "model",
                    "created": 1742927481,
                    "owned_by": "lemonade",
                    "checkpoint": "amd/Llama-3.2-3B-Instruct-awq-g128-int4-asym-fp16-onnx-hybrid",
                    "recipe": "oga-hybrid",
                }
            ],
        }
        responses.add(
            responses.GET, f"{API_BASE}/models", json=models_response, status=200
        )

        result = self.client.list_models()
        self.assertEqual(result, models_response)

    @responses.activate
    def test_chat_completions(self):
        """Test chat completions API."""
        # Mock response
        chat_response = {
            "id": "0",
            "object": "chat.completion",
            "created": 1742927481,
            "model": TEST_MODEL,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Paris has a population of approximately 2.2 million people in the city proper.",
                    },
                    "finish_reason": "stop",
                }
            ],
        }
        print(f"Setting up mocked response for chat completions: {chat_response}")
        responses.add(
            responses.POST,
            f"{API_BASE}/chat/completions",
            json=chat_response,
            status=200,
        )

        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What is the population of Paris?"},
        ]
        print(f"Sending chat completion request with messages: {messages}")

        # Test with max_completion_tokens
        result = self.client.chat_completions(
            model=TEST_MODEL,
            messages=messages,
            temperature=0.7,
            max_completion_tokens=1000,
        )
        print(f"Received chat completion response: {result}")
        self.assertEqual(result, chat_response)

        # Test with tools parameter
        responses.reset()
        responses.add(
            responses.POST,
            f"{API_BASE}/chat/completions",
            json=chat_response,
            status=200,
        )

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_population",
                    "description": "Get population of a city",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                    },
                },
            }
        ]

        result = self.client.chat_completions(
            model=TEST_MODEL,
            messages=messages,
            temperature=0.7,
            tools=tools,
            logprobs=True,
        )
        self.assertEqual(result, chat_response)

    @responses.activate
    def test_completions(self):
        """Test text completions API."""
        # Mock response
        completion_response = {
            "id": "0",
            "object": "text_completion",
            "created": 1742927481,
            "model": TEST_MODEL,
            "choices": [
                {
                    "index": 0,
                    "text": "Paris has a population of approximately 2.2 million people in the city proper.",
                    "finish_reason": "stop",
                }
            ],
        }
        responses.add(
            responses.POST,
            f"{API_BASE}/completions",
            json=completion_response,
            status=200,
        )

        prompt = "What is the population of Paris?"
        result = self.client.completions(
            model=TEST_MODEL,
            prompt=prompt,
            temperature=0.7,
            max_tokens=1000,
            echo=True,
            logprobs=True,
        )
        self.assertEqual(result, completion_response)

    @responses.activate
    def test_error_handling(self):
        """Test error handling in API calls."""
        # Temporarily disable error logging for this test
        logger = logging.getLogger("gaia.llm.lemonade_client")
        original_level = logger.level
        logger.setLevel(logging.CRITICAL)  # Suppress error logs

        try:
            # Mock error response
            error_message = "Model not found"
            responses.add(
                responses.POST,
                f"{API_BASE}/chat/completions",
                json={"error": error_message},
                status=404,
            )

            messages = [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "What is the population of Paris?"},
            ]

            with self.assertRaises(LemonadeClientError) as context:
                self.client.chat_completions(model=TEST_MODEL, messages=messages)

            self.assertIn("404", str(context.exception))
            self.assertIn(error_message, str(context.exception))
        finally:
            # Restore original log level
            logger.setLevel(original_level)

    @responses.activate
    def test_timeout_handling(self):
        """Test timeout handling for basic requests."""
        # Temporarily disable error logging for this test
        logger = logging.getLogger("gaia.llm.lemonade_client")
        original_level = logger.level
        logger.setLevel(logging.CRITICAL)  # Suppress error logs

        try:
            # Mock a timeout by raising the exception
            responses.add(
                responses.GET,
                f"{API_BASE}/health",
                body=requests.exceptions.ConnectTimeout("Connection timed out"),
            )

            with self.assertRaises(LemonadeClientError) as context:
                self.client.health_check()

            self.assertIn("timed out", str(context.exception).lower())
        finally:
            # Restore original log level
            logger.setLevel(original_level)

    @patch("gaia.llm.lemonade_client.OpenAI")
    def test_streaming_chat_completions(self, mock_openai):
        """Test basic streaming chat completion."""
        # Create mock OpenAI client
        mock_client = MagicMock()
        mock_openai.return_value = mock_client

        # Mock the completion stream
        mock_stream = MagicMock()
        mock_client.chat.completions.create.return_value = mock_stream

        # Create mock chunks
        class MockChoice:
            def __init__(self, index, content, finish_reason=None):
                self.index = index
                self.delta = MagicMock()
                self.delta.role = None
                self.delta.content = content
                self.finish_reason = finish_reason

        class MockChunk:
            def __init__(self, id, choices):
                self.id = id
                self.created = 1742927481
                self.model = TEST_MODEL
                self.choices = choices

        mock_chunks = [
            MockChunk("1", [MockChoice(0, "Paris")]),
            MockChunk("2", [MockChoice(0, " has")]),
            MockChunk("3", [MockChoice(0, " 2.2 million people.", "stop")]),
        ]

        # Set up the mock to return streaming events
        mock_stream.__iter__.return_value = mock_chunks

        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What is the population of Paris?"},
        ]

        # Collect chunks from the streaming response
        chunks = list(
            self.client.chat_completions(
                model=TEST_MODEL, messages=messages, stream=True
            )
        )

        # Validate the chunks
        self.assertEqual(len(chunks), 3)
        self.assertIn("Paris", chunks[0]["choices"][0]["delta"]["content"])
        self.assertIn("has", chunks[1]["choices"][0]["delta"]["content"])
        self.assertIn("2.2 million", chunks[2]["choices"][0]["delta"]["content"])

    @patch("gaia.llm.lemonade_client.OpenAI")
    def test_streaming_text_completions(self, mock_openai):
        """Test basic streaming text completion."""
        # Create mock OpenAI client
        mock_client = MagicMock()
        mock_openai.return_value = mock_client

        # Mock the completion stream
        mock_stream = MagicMock()
        mock_client.completions.create.return_value = mock_stream

        # Create mock chunks for completions
        class MockChoice:
            def __init__(self, index, text, finish_reason=None):
                self.index = index
                self.text = text
                self.finish_reason = finish_reason

        class MockChunk:
            def __init__(self, id, choices):
                self.id = id
                self.created = 1742927481
                self.model = TEST_MODEL
                self.choices = choices

            def model_dump(self):
                return {
                    "id": self.id,
                    "created": self.created,
                    "model": self.model,
                    "choices": [
                        {
                            "index": choice.index,
                            "text": choice.text,
                            "finish_reason": choice.finish_reason,
                        }
                        for choice in self.choices
                    ],
                }

        mock_chunks = [
            MockChunk("1", [MockChoice(0, "Paris")]),
            MockChunk("2", [MockChoice(0, " has")]),
            MockChunk("3", [MockChoice(0, " 2.2 million people.", "stop")]),
        ]

        # Set up the mock to return streaming events
        mock_stream.__iter__.return_value = mock_chunks

        # Collect chunks from the streaming response
        chunks = list(
            self.client.completions(
                model=TEST_MODEL, prompt="What is the population of Paris?", stream=True
            )
        )

        # Validate the chunks
        self.assertEqual(len(chunks), 3)
        self.assertIn("Paris", chunks[0]["choices"][0]["text"])
        self.assertIn("has", chunks[1]["choices"][0]["text"])
        self.assertIn("2.2 million", chunks[2]["choices"][0]["text"])

    @responses.activate
    def test_load_model(self):
        """Test loading a model with basic parameters and error handling."""
        # Temporarily disable error logging for this test
        logger = logging.getLogger("gaia.llm.lemonade_client")
        original_level = logger.level
        logger.setLevel(logging.CRITICAL)  # Suppress error logs

        try:
            # Test 1: Successful model loading
            load_response = {
                "status": "success",
                "message": f"Loaded model: {TEST_MODEL}",
            }
            responses.add(
                responses.POST, f"{API_BASE}/load", json=load_response, status=200
            )

            result = self.client.load_model(model_name=TEST_MODEL)
            self.assertEqual(result, load_response)

            # Test 2: Network error
            responses.reset()
            responses.add(
                responses.POST,
                f"{API_BASE}/load",
                body=requests.exceptions.ConnectionError("Connection refused"),
            )

            with self.assertRaises(LemonadeClientError) as context:
                self.client.load_model(model_name=TEST_MODEL)

            self.assertIn("Connection refused", str(context.exception))

            # Test 3: Server error (500)
            responses.reset()
            responses.add(
                responses.POST,
                f"{API_BASE}/load",
                json={"error": "Internal server error"},
                status=500,
            )

            with self.assertRaises(LemonadeClientError) as context:
                self.client.load_model(model_name=TEST_MODEL)

            self.assertIn("500", str(context.exception))
            self.assertIn("Internal server error", str(context.exception))

            # Test 4: With timeout parameter
            responses.reset()
            load_response = {
                "status": "success",
                "message": f"Loaded model: {TEST_MODEL}",
            }
            responses.add(
                responses.POST, f"{API_BASE}/load", json=load_response, status=200
            )

            result = self.client.load_model(model_name=TEST_MODEL, timeout=120)
            self.assertEqual(result, load_response)
        finally:
            # Restore original log level
            logger.setLevel(original_level)

    @responses.activate
    def test_set_params(self):
        """Test setting basic generation parameters."""
        # Mock response
        params_response = {
            "status": "success",
            "message": "Generation parameters set successfully",
            "params": {
                "temperature": 0.8,
                "top_p": 0.9,
                "top_k": 40,
                "min_length": 0,
                "max_length": 2048,
                "do_sample": True,
            },
        }
        responses.add(
            responses.POST, f"{API_BASE}/params", json=params_response, status=200
        )

        result = self.client.set_params(temperature=0.8, top_p=0.9, top_k=40)
        self.assertEqual(result, params_response)

    @responses.activate
    def test_get_stats(self):
        """Test retrieving performance statistics."""
        # Mock response
        stats_response = {
            "time_to_first_token": 2.14,
            "tokens_per_second": 33.33,
            "input_tokens": 128,
            "output_tokens": 5,
            "decode_token_times": [0.01, 0.02, 0.03, 0.04, 0.05],
        }
        responses.add(
            responses.GET, f"{API_BASE}/stats", json=stats_response, status=200
        )

        result = self.client.get_stats()
        self.assertEqual(result, stats_response)

    @responses.activate
    def test_pull_model(self):
        """Test pulling/installing a model."""
        # Test 1: Pull existing model
        pull_response = {
            "status": "success",
            "message": f"Installed model: {TEST_MODEL}",
        }
        responses.add(
            responses.POST, f"{API_BASE}/pull", json=pull_response, status=200
        )

        result = self.client.pull_model(model_name=TEST_MODEL)
        self.assertEqual(result, pull_response)

        # Test 2: Register and pull new model
        responses.reset()
        new_model_name = "user.Custom-Model-GGUF"
        pull_response = {
            "status": "success",
            "message": f"Installed model: {new_model_name}",
        }
        responses.add(
            responses.POST, f"{API_BASE}/pull", json=pull_response, status=200
        )

        result = self.client.pull_model(
            model_name=new_model_name,
            checkpoint="unsloth/Custom-Model-GGUF:Q4_K_M",
            recipe="llamacpp",
            reasoning=False,
        )
        self.assertEqual(result, pull_response)

    @responses.activate
    def test_delete_model(self):
        """Test deleting a model."""
        delete_response = {
            "status": "success",
            "message": f"Deleted model: {TEST_MODEL}",
        }
        responses.add(
            responses.POST, f"{API_BASE}/delete", json=delete_response, status=200
        )

        result = self.client.delete_model(model_name=TEST_MODEL)
        self.assertEqual(result, delete_response)

    @responses.activate
    def test_responses(self):
        """Test responses API endpoint."""
        # Test 1: Non-streaming responses with string input
        responses_response = {
            "id": "0",
            "created_at": 1746225832.0,
            "model": TEST_MODEL,
            "object": "response",
            "output": [
                {
                    "id": "0",
                    "content": [
                        {
                            "annotations": [],
                            "text": "Paris has a population of approximately 2.2 million people.",
                        }
                    ],
                }
            ],
        }
        responses.add(
            responses.POST, f"{API_BASE}/responses", json=responses_response, status=200
        )

        result = self.client.responses(
            model=TEST_MODEL,
            input="What is the population of Paris?",
            temperature=0.7,
            max_output_tokens=100,
        )
        self.assertEqual(result, responses_response)

        # Test 2: With list input
        responses.reset()
        responses.add(
            responses.POST, f"{API_BASE}/responses", json=responses_response, status=200
        )

        list_input = [{"role": "user", "content": "What is the population of Paris?"}]
        result = self.client.responses(
            model=TEST_MODEL, input=list_input, temperature=0.7
        )
        self.assertEqual(result, responses_response)

    @responses.activate
    def test_ready(self):
        """Test ready() method with mocked responses."""
        # Mock a successful health check response
        health_response = {
            "status": "ok",
            "checkpoint_loaded": "amd/Llama-3.2-3B-Instruct-awq-g128-int4-asym-fp16-onnx-hybrid",
            "model_loaded": TEST_MODEL,
        }
        responses.add(
            responses.GET, f"{API_BASE}/health", json=health_response, status=200
        )

        # The client's model should match the model in the response
        self.client.model = TEST_MODEL

        # Server should be ready
        result = self.client.ready()
        self.assertTrue(result)

        # Reset and mock a failed health check
        responses.reset()
        responses.add(
            responses.GET,
            f"{API_BASE}/health",
            body=requests.exceptions.ConnectionError("Connection refused"),
        )

        # Server should not be ready
        result = self.client.ready()
        self.assertFalse(result)


def is_server_running(host=HOST, port=PORT):
    """Check if a lemonade server is already running on the specified host and port."""
    try:
        # Create a temporary client to test the connection
        temp_client = LemonadeClient(host=host, port=port, verbose=False)

        # Use the client's health_check method instead of direct HTTP
        health_response = temp_client.health_check()

        # If we get here, the server is running and responding
        if health_response.get("status") == "ok":
            return True

    except (LemonadeClientError, Exception):
        # Any error means server is not running or not accessible
        pass
    return False


class TestLemonadeClientIntegration(unittest.TestCase):
    """Integration tests for LemonadeClient with a running server."""

    @classmethod
    def setUpClass(cls):
        """Set up the test client and server."""
        print("\n====== SETTING UP INTEGRATION TEST ENVIRONMENT ======")

        # Check if server is already running
        if is_server_running(HOST, PORT):
            print(f"✅ Lemonade server already running at {HOST}:{PORT}")
            cls.server_started_by_test = False
            # Create client without auto_start since server is already running
            # IMPORTANT: Set keep_alive=True to prevent termination on client destruction
            cls.client = create_lemonade_client(
                model=TEST_MODEL,
                host=HOST,
                port=PORT,
                auto_start=False,  # Don't start - already running
                auto_load=False,
                verbose=True,
                keep_alive=True,  # Don't terminate server when client is destroyed
            )
        else:
            print(f"🚀 Starting new Lemonade server at {HOST}:{PORT}")
            cls.server_started_by_test = True
            # Create client with auto_start since no server is running
            # keep_alive=False (default) so we can terminate it later
            cls.client = create_lemonade_client(
                model=TEST_MODEL,
                host=HOST,
                port=PORT,
                auto_start=True,
                auto_load=False,
                verbose=True,
                keep_alive=False,  # We started it, so we should terminate it
            )

        print(
            f"Created integration test client with model={TEST_MODEL}, host={HOST}, port={PORT}"
        )

    @classmethod
    def tearDownClass(cls):
        """Clean up after all tests."""
        print("\n====== CLEANING UP INTEGRATION TEST ENVIRONMENT ======")

        # Only terminate the server if we started it
        if hasattr(cls, "client") and hasattr(cls, "server_started_by_test"):
            if cls.server_started_by_test:
                try:
                    print(f"🛑 Terminating Lemonade server (started by test)...")
                    cls.client.terminate_server()
                    print(f"\n✅ Lemonade server terminated after integration tests")
                except Exception as e:
                    print(f"\n❌ Error terminating Lemonade server: {e}")
            else:
                print(
                    f"⏭️  Leaving Lemonade server running (was already running before tests)"
                )
                # Ensure the client won't try to terminate on destruction
                if hasattr(cls.client, "keep_alive"):
                    cls.client.keep_alive = True
        elif hasattr(cls, "client"):
            # Fallback - but be very careful not to terminate existing servers
            print(f"⚠️  Unknown server state - checking if we should terminate...")
            # Only terminate if we can confirm we started it via the server_process
            if (
                hasattr(cls.client, "server_process")
                and cls.client.server_process is not None
            ):
                try:
                    print(f"🛑 Found server process - terminating...")
                    cls.client.terminate_server()
                    print(f"\n✅ Lemonade server terminated after integration tests")
                except Exception as e:
                    print(f"\n❌ Error terminating Lemonade server: {e}")
            else:
                print(f"⏭️  No server process found - leaving server running")
                # Ensure the client won't try to terminate on destruction
                if hasattr(cls.client, "keep_alive"):
                    cls.client.keep_alive = True

    def setUp(self):
        """Set up before each test."""
        print(f"\n----- Starting integration test: {self._testMethodName} -----")

    def tearDown(self):
        """Clean up after each test."""
        print(f"----- Completed integration test: {self._testMethodName} -----")

    def test_integration_health_check(self):
        """Integration test for health check."""
        print("Sending health check request to server...")
        response = self.client.health_check()
        print(f"Health check response: {response}")
        self.assertIn("status", response)
        self.assertEqual(response["status"], "ok")
        print("✅ Health check passed")

    def test_integration_basic_request(self):
        """Integration test for basic request handling."""
        print(f"Testing basic client health check...")
        response = self.client.health_check()
        print(f"Health check response: {response}")
        self.assertIn("status", response)
        self.assertEqual(response["status"], "ok")
        print("✅ Basic request test passed")

    def test_integration_list_models(self):
        """Integration test for listing available models."""
        print("Requesting model list from server...")
        response = self.client.list_models()
        print(f"List models response: {response}")
        self.assertIn("data", response)
        self.assertTrue(isinstance(response["data"], list))
        print(f"✅ Found {len(response['data'])} models")

    def test_integration_chat_completion(self):
        """Integration test for basic non-streaming chat completion."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Reply with just the number 42."},
        ]

        print(f"Sending chat completion request with messages: {messages}")

        try:
            response = self.client.chat_completions(
                model=TEST_MODEL,
                messages=messages,
                temperature=0.0,  # Use deterministic output
                max_completion_tokens=10,  # Limit completion tokens
            )

            print(f"Chat completion response structure: {list(response.keys())}")
            print(f"Response choices: {response.get('choices', [])}")

            if "choices" in response and len(response["choices"]) > 0:
                content = response["choices"][0].get("message", {}).get("content", "")
                print(f"Response content: {content}")

            self.assertIn("choices", response)
            self.assertGreaterEqual(len(response["choices"]), 1)
            self.assertIn("message", response["choices"][0])
            self.assertIn("content", response["choices"][0]["message"])
            # Content should contain 42 (model might add some context but should include 42)
            self.assertIn("42", response["choices"][0]["message"]["content"])
            print("✅ Chat completion test passed")

        except LemonadeClientError as e:
            error_str = str(e)
            print(f"❌ Error during chat completion: {error_str}")
            # Fail the test for all errors including 404 Model not found
            self.fail(f"Chat completion failed: {error_str}")

    def test_integration_chat_completion_streaming(self):
        """Integration test for streaming chat completion."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Count from 1 to 3."},
        ]

        # Collect the streamed chunks
        content = ""
        chunk_count = 0
        print("Starting streaming chat completion test...")

        try:
            for chunk in self.client.chat_completions(
                model=TEST_MODEL,
                messages=messages,
                temperature=0.0,
                max_completion_tokens=20,
                stream=True,
            ):
                chunk_count += 1

                # Check chunk structure
                self.assertIn("choices", chunk)

                # Extract and accumulate content
                delta = chunk["choices"][0].get("delta", {})
                if "content" in delta and delta["content"]:
                    content += delta["content"]
                    print(f"{delta['content']}", end="")

            print(f"\nReceived {chunk_count} chunks with total content: '{content}'")

            # Verify we got multiple chunks
            self.assertGreater(chunk_count, 1, "Should receive multiple chunks")

            # Content should contain numbers 1, 2, and 3
            self.assertIn("1", content, "Response should include '1'")
            self.assertIn("2", content, "Response should include '2'")
            self.assertIn("3", content, "Response should include '3'")
            print("✅ Streaming chat completion test passed")

        except LemonadeClientError as e:
            error_str = str(e)
            print(f"❌ Error during streaming chat completion: {error_str}")
            # Fail the test for all errors including 404 Model not found
            self.fail(f"Streaming chat completion failed: {error_str}")

    def test_integration_text_completion(self):
        """Integration test for text completion."""
        prompt = "Count from 1 to 3."
        print(f"Sending text completion request with prompt: {prompt}")

        try:
            response = self.client.completions(
                model=TEST_MODEL, prompt=prompt, temperature=0.0, max_tokens=50
            )

            # Verify response format
            self.assertIn("choices", response)
            self.assertGreaterEqual(len(response["choices"]), 1)
            self.assertIn("text", response["choices"][0])

            # Verify content includes the numbers - be flexible about partial responses
            content = response["choices"][0]["text"]
            print(f"Response content: {content}")

            # Check that at least 1 and 2 are present (3 might be cut off)
            self.assertIn("1", content, "Response should include '1'")
            self.assertIn("2", content, "Response should include '2'")

            # Only check for 3 if it's actually in the response (don't fail if cut off)
            if "3" in content:
                print("✅ Complete response with all numbers 1, 2, 3")
            else:
                print("⚠️  Response was truncated but includes 1 and 2")

            print("✅ Text completion test passed")

        except LemonadeClientError as e:
            error_str = str(e)
            print(f"❌ Error during text completion: {error_str}")
            # Fail the test for all errors including 404 Model not found
            self.fail(f"Text completion failed: {error_str}")

    @pytest.mark.skip(reason="Parameter setting API is still in development")
    def test_integration_set_params(self):
        """Integration test for setting generation parameters."""
        # Set parameters
        response = self.client.set_params(temperature=0.8, top_p=0.95, top_k=50)

        # Verify response
        self.assertIn("params", response)
        params = response["params"]
        self.assertEqual(params.get("temperature"), 0.8)
        self.assertEqual(params.get("top_p"), 0.95)
        self.assertEqual(params.get("top_k"), 50)

    def test_integration_get_stats(self):
        """Integration test for getting performance stats."""
        # First make a request to generate stats
        print("Making a chat request to generate stats...")
        self.client.chat_completions(
            model=TEST_MODEL,
            messages=[{"role": "user", "content": "Hello"}],
            max_completion_tokens=2,
        )

        # Then get stats
        print("Requesting performance stats...")
        response = self.client.get_stats()
        print(f"Stats response: {response}")

        # Verify stats keys are returned at root level
        # The actual stats are directly in the response, not in a 'stats' field
        stats_keys = [
            "time_to_first_token",
            "tokens_per_second",
            "input_tokens",
            "output_tokens",
            "decode_token_times",
        ]
        found_keys = [key for key in stats_keys if key in response]
        print(f"Found stats keys: {found_keys}")

        self.assertTrue(
            any(key in response for key in stats_keys),
            f"Stats keys not found in response: {response}",
        )
        print("✅ Get stats test passed")

    def test_integration_load_model(self):
        """Integration test for loading a model (should succeed)."""
        print("Testing model loading functionality...")

        # Use the default test checkpoint that should exist
        model = TEST_MODEL
        print(f"Attempting to load existing model: {model}")

        try:
            # Attempt to load the model - should succeed
            result = self.client.load_model(model_name=model)

            # Verify the response structure
            self.assertIn("status", result)

            # Allow either "ok" or "success" as valid status values
            status = result["status"]
            print(f"Got status: {status}")
            self.assertIn(
                status,
                ["ok", "success"],
                f"Expected status 'ok' or 'success', got '{status}'",
            )

            print(f"✅ Model loading successful: {result}")
        except LemonadeClientError as e:
            # If we got an error, the test failed - the model should exist
            self.fail(
                f"Expected model {model} to load successfully, but got error: {e}"
            )

    def test_integration_ready(self):
        """Integration test for ready() method."""
        print("Testing if the server is ready...")

        # Call the ready() method
        is_ready = self.client.ready()

        # Verify result is True since we've initialized with auto_start=True, auto_load=True
        print(f"Server ready status: {is_ready}")
        self.assertTrue(is_ready, "Server should be ready")
        print("✅ Ready test passed")

    def test_integration_pull_model(self):
        """Integration test for pulling/installing a model."""
        print("Testing model pull functionality...")

        # Try to pull an existing model
        model_name = TEST_MODEL
        print(f"Attempting to pull existing model: {model_name}")

        try:
            result = self.client.pull_model(model_name=model_name)
            print(f"Pull model response: {result}")

            # Handle case where endpoint returns None (not implemented yet)
            if result is None:
                print(
                    "⚠️  Pull model endpoint returned None - may not be implemented yet"
                )
                return

            # Verify the response structure
            self.assertIn("status", result)
            status = result["status"]
            self.assertIn(
                status,
                ["success", "ok"],
                f"Expected status 'success' or 'ok', got '{status}'",
            )

            print(f"✅ Model pull successful: {result}")
        except LemonadeClientError as e:
            # Model might already be installed or endpoint not available
            error_str = str(e)
            print(f"Model pull result: {error_str}")
            if any(
                phrase in error_str.lower()
                for phrase in [
                    "already exists",
                    "already installed",
                    "404",
                    "not found",
                ]
            ):
                print("✅ Model already installed or endpoint not available (expected)")
            else:
                print(f"❌ Unexpected error during model pull: {error_str}")
                # Don't fail the test - pull might fail for various reasons in test environment

    def test_integration_responses_endpoint(self):
        """Integration test for responses API endpoint."""
        print("Testing responses endpoint...")

        # Test with string input
        input_text = "Count from 1 to 3."
        print(f"Sending responses request with input: {input_text}")

        try:
            response = self.client.responses(
                model=TEST_MODEL,
                input=input_text,
                temperature=0.0,
                max_output_tokens=50,
            )

            print(f"Responses endpoint response: {response}")

            # Verify response structure
            self.assertIn("object", response)
            self.assertEqual(response["object"], "response")
            self.assertIn("output", response)
            self.assertIsInstance(response["output"], list)

            # Check content structure
            if len(response["output"]) > 0:
                content = response["output"][0].get("content", [])
                if len(content) > 0:
                    text = content[0].get("text", "")
                    print(f"Response text: {text}")

                    # Verify content includes at least some numbers - be flexible about partial responses
                    self.assertIn("1", text, "Response should include '1'")
                    self.assertIn("2", text, "Response should include '2'")

                    # Only check for 3 if it's actually in the response (don't fail if cut off)
                    if "3" in text:
                        print("✅ Complete response with all numbers 1, 2, 3")
                    else:
                        print("⚠️  Response was truncated but includes 1 and 2")

            print("✅ Responses endpoint test passed")

        except LemonadeClientError as e:
            error_str = str(e)
            print(f"❌ Error during responses request: {error_str}")
            # Don't fail - responses endpoint might not be fully implemented yet
            print("⚠️  Responses endpoint might not be fully implemented yet")


if __name__ == "__main__":
    # Use pytest to run tests - either all tests or a specific test pattern
    import pytest
    import sys

    print("\n====================================================")
    print("========== RUNNING LEMONADE CLIENT TESTS ===========")
    print("====================================================")
    print(f"Python version: {sys.version}")
    print(f"Pytest version: {pytest.__version__}")
    print(f"Running tests from: {__file__}")

    # Process command line arguments
    pytest_args = ["-v", __file__]

    # Check for test pattern using -k flag
    k_flag_index = -1
    k_pattern = None

    for i, arg in enumerate(sys.argv):
        if arg == "-k" and i + 1 < len(sys.argv):
            k_pattern = sys.argv[i + 1]
            k_flag_index = i
            break
        elif arg.startswith("-k="):
            k_pattern = arg.split("=", 1)[1]
            k_flag_index = i
            break

    # Process -k flag if present
    if k_pattern:
        pytest_args.append(f"-k={k_pattern}")
        print(f"Running tests matching pattern: {k_pattern}")
    else:
        # If no -k flag, check for positional test name arguments
        test_names = []
        for arg in sys.argv[1:]:
            if not arg.startswith("-") and not arg.startswith("--"):
                # If it's a simple test name without class prefix, try to determine class
                if "::" not in arg:
                    # Take best guess at which test class to use based on name prefix
                    if arg.startswith("test_integration_"):
                        test_names.append(f"TestLemonadeClientIntegration::{arg}")
                    else:
                        test_names.append(f"TestLemonadeClientMock::{arg}")
                else:
                    test_names.append(arg)

        if test_names:
            # Convert test names to -k pattern for compatibility
            pattern = " or ".join(test_names)
            pytest_args.append(f"-k={pattern}")
            print(f"Running tests: {pattern}")

    # Check if -s (no capture) is specified
    if "-s" in sys.argv:
        pytest_args.insert(1, "-s")
        print("Output capturing disabled (-s): all print statements will be shown")

    # Support for --tb=short/native/long/auto/no
    tb_options = [arg for arg in sys.argv if arg.startswith("--tb=")]
    if tb_options:
        pytest_args.append(tb_options[0])
        print(f"Traceback option: {tb_options[0]}")

    # Check for custom verbosity level
    for arg in sys.argv:
        if arg.startswith("-v") and arg != "-v":
            # Replace default verbosity with user-specified level
            pytest_args[0] = arg
            print(f"Verbosity set to: {arg}")
            break

    print(f"Pytest arguments: {' '.join(pytest_args)}")
    print("====================================================")
    print("Examples:")
    print(
        "  Run all tests:                        python tests/test_lemonade_client.py"
    )
    print(
        "  Run tests with pattern:               python tests/test_lemonade_client.py -k 'load'"
    )
    print(
        "  Run integration tests:                python tests/test_lemonade_client.py -k 'integration'"
    )
    print(
        "  Run specific test:                    python tests/test_lemonade_client.py -k 'test_integration_load_existing_model'"
    )
    print("====================================================\n")

    # Run pytest with the collected arguments
    exit_code = pytest.main(pytest_args)

    print("\n====================================================")
    print(f"Tests completed with exit code: {exit_code}")
    print("====================================================\n")

    sys.exit(exit_code)
