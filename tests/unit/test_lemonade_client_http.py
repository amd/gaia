# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Unit tests for the Lemonade client API, with the HTTP boundary mocked.

No live server: every request is answered by ``responses`` (or a patched
OpenAI client), and tests assert the shape of what the client sends.
"""

import json
import logging
import os
import sys
import tempfile
import unittest
from io import StringIO
from unittest.mock import MagicMock, patch

import requests
import responses

from gaia.llm.lemonade_client import (
    CHAT_LLAMACPP_ARGS,
    GPU_CTX_SIZE,
    LemonadeAuthError,
    LemonadeClient,
    LemonadeClientError,
    create_lemonade_client,
    lemonade_auth_headers,
    resolve_lemonade_api_key,
)
from gaia.llm.lemonade_launcher import LemonadeTooling

TEST_MODEL = "Gemma-4-E4B-it-GGUF"

HOST = "localhost"
PORT = 13305
API_BASE = f"http://{HOST}:{PORT}/api/v1"


class TestLemonadeClientMock(unittest.TestCase):
    """Test cases for synchronous LemonadeClient."""

    def setUp(self):
        """Set up test fixtures."""
        print(f"\n----- Setting up {self._testMethodName} -----")
        # GAIA_CTX_SIZE would change the ctx_size the /load body is asserted on.
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("GAIA_CTX_SIZE", None)
        # A developer's own embedded Lemonade (its port and API key under
        # ~/.gaia) would otherwise stand in for the defaults asserted here.
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        os.environ["GAIA_HOME"] = home.name
        for name in (
            "LEMONADE_BASE_URL",
            "LEMONADE_API_KEY",
            "LEMONADE_PORT",
            "GAIA_LEMONADE_EMBEDDED",
        ):
            os.environ.pop(name, None)
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

    @staticmethod
    def _mock_cold_load(model=TEST_MODEL):
        """Answer the pre-request ctx check for a downloaded, non-resident model."""
        responses.add(
            responses.GET,
            f"{API_BASE}/health",
            json={"status": "ok", "model_loaded": None, "all_models_loaded": []},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{API_BASE}/models",
            json={"data": [{"id": model, "recipe": "llamacpp", "downloaded": True}]},
            status=200,
        )
        responses.add(
            responses.POST,
            f"{API_BASE}/load",
            json={"status": "success", "message": f"Loaded model: {model}"},
            status=200,
        )

    def _assert_one_load(self, model=TEST_MODEL):
        """The request carried GAIA's window and the single-slot pin."""
        loads = [c for c in responses.calls if c.request.url.endswith("/load")]
        self.assertEqual(len(loads), 1, [c.request.url for c in responses.calls])
        self.assertEqual(
            json.loads(loads[0].request.body),
            {
                "model_name": model,
                "ctx_size": GPU_CTX_SIZE,
                "llamacpp_args": CHAT_LLAMACPP_ARGS,
            },
        )

    def test_client_initialization(self):
        """Test client initialization with default and custom parameters."""
        # Test default initialization
        client = LemonadeClient()
        self.assertEqual(client.host, "localhost")
        self.assertEqual(client.port, 13305)
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
        # Mock response - Lemonade 9.1.4+ format
        health_response = {
            "status": "ok",
            "model_loaded": TEST_MODEL,
            "version": "9.1.4",
            "all_models_loaded": [
                {
                    "backend_url": "http://127.0.0.1:8001/v1",
                    "checkpoint": "amd/Llama-3.2-3B-Instruct-awq-g128-int4-asym-fp16-onnx-hybrid",
                    "device": "gpu",
                    "model_name": TEST_MODEL,
                    "recipe": "oga-hybrid",
                    "recipe_options": {
                        "ctx_size": 8192,
                    },
                    "type": "llm",
                }
            ],
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
        # Cold start: downloaded but not resident, so the client loads it at
        # GAIA's window before the request.
        self._mock_cold_load()
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

        result = self.client.chat_completions(
            model=TEST_MODEL,
            messages=messages,
            temperature=0.7,
            max_completion_tokens=1000,
        )
        self.assertEqual(result, chat_response)
        self._assert_one_load()
        self.assertTrue(responses.calls[-1].request.url.endswith("/chat/completions"))
        body = json.loads(responses.calls[-1].request.body)
        self.assertEqual(body["model"], TEST_MODEL)
        self.assertEqual(body["messages"], messages)
        self.assertEqual(body["temperature"], 0.7)
        self.assertEqual(body["max_completion_tokens"], 1000)
        self.assertFalse(body.get("stream", False))

        # Warm: resident at the full window, so no second /load.
        responses.reset()
        responses.add(
            responses.GET,
            f"{API_BASE}/health",
            json={
                "status": "ok",
                "model_loaded": TEST_MODEL,
                "all_models_loaded": [
                    {
                        "model_name": TEST_MODEL,
                        "recipe": "llamacpp",
                        "recipe_options": {"ctx_size": GPU_CTX_SIZE},
                        "type": "llm",
                    }
                ],
            },
            status=200,
        )
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
        self.assertFalse(
            any(c.request.url.endswith("/load") for c in responses.calls),
            "a model resident at the full window must not be reloaded",
        )
        body = json.loads(responses.calls[-1].request.body)
        self.assertEqual(body["tools"], tools)
        self.assertTrue(body["logprobs"])

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
                # Disable auto_download to prevent retry logic from calling /load endpoint
                self.client.chat_completions(
                    model=TEST_MODEL, messages=messages, auto_download=False
                )

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

    @responses.activate
    @patch("gaia.llm.lemonade_client.OpenAI")
    def test_streaming_chat_completions(self, mock_openai):
        """Test basic streaming chat completion."""
        self._mock_cold_load()
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
        self._assert_one_load()
        kwargs = mock_client.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["model"], TEST_MODEL)
        self.assertEqual(kwargs["messages"], messages)
        self.assertTrue(kwargs["stream"])

    @responses.activate
    @patch("gaia.llm.lemonade_client.OpenAI")
    def test_streaming_text_completions(self, mock_openai):
        """Test basic streaming text completion."""
        self._mock_cold_load()
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
        self._assert_one_load()
        kwargs = mock_client.completions.create.call_args.kwargs
        self.assertEqual(kwargs["model"], TEST_MODEL)
        self.assertEqual(kwargs["prompt"], "What is the population of Paris?")
        self.assertTrue(kwargs["stream"])

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

    # ------------------------------------------------------------------
    # Transient "llama-server failed to start" load retry
    # ------------------------------------------------------------------

    _TRANSIENT_LOAD_BODY = {
        "error": {
            "code": "model_load_error",
            "message": (
                f"Failed to load model '{TEST_MODEL}': llama-server failed to start"
            ),
            "type": "model_load_error",
        }
    }

    def test_is_transient_load_error_classification(self):
        """Only the backend-startup fault counts as transient."""
        self.assertTrue(
            self.client._is_transient_load_error("llama-server failed to start")
        )
        # Corrupt-download and missing-model failures are NOT transient.
        self.assertFalse(
            self.client._is_transient_load_error("download validation failed")
        )
        self.assertFalse(self.client._is_transient_load_error("model not found"))

    @responses.activate
    def test_load_model_retries_transient_then_succeeds(self):
        """A transient backend-startup failure is retried and recovers."""
        logger = logging.getLogger("gaia.llm.lemonade_client")
        original_level = logger.level
        logger.setLevel(logging.CRITICAL)
        try:
            success = {"status": "success", "message": f"Loaded model: {TEST_MODEL}"}
            # First call fails transiently, the retry succeeds.
            responses.add(
                responses.POST,
                f"{API_BASE}/load",
                json=self._TRANSIENT_LOAD_BODY,
                status=500,
            )
            responses.add(responses.POST, f"{API_BASE}/load", json=success, status=200)
            with patch("gaia.llm.lemonade_client.time.sleep") as mock_sleep:
                result = self.client.load_model(model_name=TEST_MODEL)
            self.assertEqual(result, success)
            self.assertEqual(len(responses.calls), 2)  # initial + one retry
            mock_sleep.assert_called_once()  # backed off once before retrying
        finally:
            logger.setLevel(original_level)

    @responses.activate
    def test_load_model_retries_exhausted_raises_loudly(self):
        """Persistent transient failure re-raises with context after retries."""
        logger = logging.getLogger("gaia.llm.lemonade_client")
        original_level = logger.level
        logger.setLevel(logging.CRITICAL)
        try:
            responses.add(
                responses.POST,
                f"{API_BASE}/load",
                json=self._TRANSIENT_LOAD_BODY,
                status=500,
            )
            with patch("gaia.llm.lemonade_client.time.sleep"):
                with self.assertRaises(LemonadeClientError) as ctx:
                    self.client.load_model(model_name=TEST_MODEL, load_retries=2)
            self.assertIn("llama-server failed to start", str(ctx.exception))
            # A crash that outlives the retries names the fix, not just the
            # symptom (#1831).
            self.assertIn("GGML_VK_DISABLE_COOPMAT=1", str(ctx.exception))
            # 1 initial attempt + 2 retries = 3 load calls.
            self.assertEqual(len(responses.calls), 3)
        finally:
            logger.setLevel(original_level)

    @responses.activate
    def test_load_model_no_retry_when_disabled(self):
        """load_retries=0 fails immediately without retrying or sleeping."""
        logger = logging.getLogger("gaia.llm.lemonade_client")
        original_level = logger.level
        logger.setLevel(logging.CRITICAL)
        try:
            responses.add(
                responses.POST,
                f"{API_BASE}/load",
                json=self._TRANSIENT_LOAD_BODY,
                status=500,
            )
            with patch("gaia.llm.lemonade_client.time.sleep") as mock_sleep:
                with self.assertRaises(LemonadeClientError):
                    self.client.load_model(model_name=TEST_MODEL, load_retries=0)
            self.assertEqual(len(responses.calls), 1)  # no retry
            mock_sleep.assert_not_called()
        finally:
            logger.setLevel(original_level)

    @responses.activate
    def test_load_model_does_not_retry_non_transient(self):
        """A non-transient 500 (e.g. generic) is NOT retried."""
        logger = logging.getLogger("gaia.llm.lemonade_client")
        original_level = logger.level
        logger.setLevel(logging.CRITICAL)
        try:
            responses.add(
                responses.POST,
                f"{API_BASE}/load",
                json={"error": "Internal server error"},
                status=500,
            )
            with patch("gaia.llm.lemonade_client.time.sleep") as mock_sleep:
                with self.assertRaises(LemonadeClientError):
                    self.client.load_model(model_name=TEST_MODEL, load_retries=2)
            self.assertEqual(len(responses.calls), 1)  # failed immediately
            mock_sleep.assert_not_called()
        finally:
            logger.setLevel(original_level)

    @responses.activate
    def test_load_model_retry_stops_when_error_turns_corrupt(self):
        """A retry error that turns corrupt stops the loop and enters repair."""
        logger = logging.getLogger("gaia.llm.lemonade_client")
        original_level = logger.level
        logger.setLevel(logging.CRITICAL)
        try:
            corrupt_body = {
                "error": {
                    "code": "model_load_error",
                    "message": (
                        f"Failed to load model '{TEST_MODEL}': "
                        f"download validation failed"
                    ),
                    "type": "model_load_error",
                }
            }
            # First call fails transiently; the retry surfaces a corrupt
            # download. The loop must break (no further retries) and the
            # corrupt-repair path must take over.
            responses.add(
                responses.POST,
                f"{API_BASE}/load",
                json=self._TRANSIENT_LOAD_BODY,
                status=500,
            )
            responses.add(
                responses.POST,
                f"{API_BASE}/load",
                json=corrupt_body,
                status=500,
            )
            with patch("gaia.llm.lemonade_client.time.sleep") as mock_sleep:
                with patch.object(
                    self.client, "_consume_pull_stream", return_value=False
                ) as mock_pull:
                    with self.assertRaises(LemonadeClientError) as ctx:
                        self.client.load_model(
                            model_name=TEST_MODEL, load_retries=3, prompt=False
                        )
            # 1 initial + 1 retry, NOT 4: the corrupt error stopped the loop.
            self.assertEqual(len(responses.calls), 2)
            mock_sleep.assert_called_once()
            # The repair path ran (resume attempted) instead of more retries.
            mock_pull.assert_called_once_with(TEST_MODEL, "resume")
            self.assertIn("download validation failed", str(ctx.exception))
        finally:
            logger.setLevel(original_level)

    @responses.activate
    def test_unload_model_scoped_vs_global(self):
        """unload_model(name) targets only that model; unload_model() stays global.

        Regression guard for #1544: the RAG embedder refresh must scope its
        /unload to the embedder so the co-resident chat model is not evicted.
        The no-arg form keeps the historical global behavior other callers rely
        on (chat preflight, init, UI eviction).
        """
        unload_response = {"status": "success", "message": "unloaded"}

        # Scoped: model_name is sent in the request body.
        responses.add(
            responses.POST, f"{API_BASE}/unload", json=unload_response, status=200
        )
        embed_model = "user.embeddinggemma-300m-GGUF"
        result = self.client.unload_model(embed_model)
        self.assertEqual(result, unload_response)
        self.assertEqual(
            json.loads(responses.calls[-1].request.body),
            {"model_name": embed_model},
        )

        # Global (no arg): no model_name → no JSON body, unloads everything.
        responses.reset()
        responses.add(
            responses.POST, f"{API_BASE}/unload", json=unload_response, status=200
        )
        result = self.client.unload_model()
        self.assertEqual(result, unload_response)
        self.assertIsNone(responses.calls[-1].request.body)

    @responses.activate
    def test_unload_model_ignore_if_not_loaded(self):
        """Cold-start guard (#1544): Lemonade 404s "Model not loaded" when the
        embedder slot is empty. ignore_if_not_loaded turns that one benign case
        into a no-op; every other failure — and the default strict mode — still
        raises, so a global unload or a real outage is never swallowed.
        """
        embed_model = "user.embeddinggemma-300m-GGUF"
        not_loaded = {"error": f"Model not loaded: {embed_model}"}

        # 404 "not loaded" + flag ON → no-op, no raise.
        responses.add(responses.POST, f"{API_BASE}/unload", json=not_loaded, status=404)
        result = self.client.unload_model(embed_model, ignore_if_not_loaded=True)
        self.assertEqual(result.get("status"), "not_loaded")

        # 404 "not loaded" + flag OFF (default) → raises.
        responses.reset()
        responses.add(responses.POST, f"{API_BASE}/unload", json=not_loaded, status=404)
        with self.assertRaises(LemonadeClientError):
            self.client.unload_model(embed_model)

        # A different failure (500) is NOT swallowed even with the flag ON.
        responses.reset()
        responses.add(
            responses.POST,
            f"{API_BASE}/unload",
            json={"error": "Internal server error"},
            status=500,
        )
        with self.assertRaises(LemonadeClientError):
            self.client.unload_model(embed_model, ignore_if_not_loaded=True)

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

    def test_get_stats_merges_model_load_seconds_when_a_load_happened(self):
        """#2924: get_stats() must surface the client-measured load time
        alongside Lemonade's own (load-blind) generation stats."""
        stats_response = {"time_to_first_token": 7.6, "tokens_per_second": 20.0}
        self.client._last_model_load_seconds = 36.9
        with patch.object(self.client, "_send_request", return_value=stats_response):
            result = self.client.get_stats()
        self.assertEqual(result["model_load_seconds"], 36.9)
        self.assertEqual(result["time_to_first_token"], 7.6)
        # The original dict must not be mutated in place.
        self.assertNotIn("model_load_seconds", stats_response)

    def test_get_stats_omits_model_load_seconds_when_no_load_happened(self):
        """The common warm-path shape is unchanged from before #2924."""
        stats_response = {"time_to_first_token": 7.7}
        self.client._last_model_load_seconds = None
        with patch.object(self.client, "_send_request", return_value=stats_response):
            result = self.client.get_stats()
        self.assertEqual(result, stats_response)
        self.assertNotIn("model_load_seconds", result)

    def test_ensure_model_loaded_locked_times_an_actual_load(self):
        """A cold load (model not resident) must set _last_model_load_seconds
        to the wall-clock time load_model() actually took."""
        monotonic_values = iter([100.0, 136.9])
        with (
            patch.object(self.client, "get_status", return_value={"loaded_models": []}),
            patch.object(self.client, "list_models", return_value={"data": []}),
            patch.object(self.client, "load_model", return_value={"status": "success"}),
            patch(
                "gaia.llm.lemonade_client.time.monotonic",
                side_effect=lambda: next(monotonic_values),
            ),
        ):
            self.client._ensure_model_loaded_locked(TEST_MODEL)
        self.assertAlmostEqual(self.client._last_model_load_seconds, 36.9)

    def test_ensure_model_loaded_locked_leaves_none_when_already_resident(self):
        """A warm call (model already loaded at sufficient ctx) is a fast
        no-op — it must never report a load duration."""
        self.client._last_model_load_seconds = 99.0  # stale, from an earlier cold call
        loaded_entry = {
            "id": TEST_MODEL,
            "recipe_options": {"ctx_size": 65536},
        }
        with (
            patch.object(
                self.client,
                "get_status",
                return_value={"loaded_models": [loaded_entry]},
            ),
            patch.object(self.client, "_find_loaded_entry", return_value=loaded_entry),
        ):
            self.client._ensure_model_loaded_locked(TEST_MODEL)
        self.assertIsNone(self.client._last_model_load_seconds)

    def test_a_cold_load_is_announced_before_and_after(self):
        """The chat turn's longest silent wait is a load; the listener is what
        lets the live line say so."""
        seen = []
        self.client.model_load_listener = lambda model, state: seen.append(
            (model, state)
        )
        with (
            patch.object(self.client, "get_status", return_value={"loaded_models": []}),
            patch.object(
                self.client,
                "list_models",
                return_value={"data": [{"id": TEST_MODEL, "downloaded": True}]},
            ),
            patch.object(self.client, "load_model", return_value={"status": "success"}),
        ):
            self.client._ensure_model_loaded_locked(TEST_MODEL)
        self.assertEqual(seen, [(TEST_MODEL, "loading"), (TEST_MODEL, "loaded")])

    def test_a_first_run_download_is_announced_as_a_download(self):
        seen = []
        self.client.model_load_listener = lambda model, state: seen.append(state)
        with (
            patch.object(self.client, "get_status", return_value={"loaded_models": []}),
            patch.object(
                self.client,
                "list_models",
                return_value={"data": [{"id": TEST_MODEL, "downloaded": False}]},
            ),
            patch.object(self.client, "load_model", return_value={"status": "success"}),
        ):
            self.client._ensure_model_loaded_locked(TEST_MODEL)
        self.assertEqual(seen, ["downloading", "loaded"])

    def test_a_resident_model_announces_nothing(self):
        seen = []
        self.client.model_load_listener = lambda model, state: seen.append(state)
        loaded_entry = {"id": TEST_MODEL, "recipe_options": {"ctx_size": 65536}}
        with (
            patch.object(
                self.client,
                "get_status",
                return_value={"loaded_models": [loaded_entry]},
            ),
            patch.object(self.client, "_find_loaded_entry", return_value=loaded_entry),
        ):
            self.client._ensure_model_loaded_locked(TEST_MODEL)
        self.assertEqual(seen, [])

    def test_ensure_model_loaded_locked_never_records_a_failed_load(self):
        """A load that raises must not leave a stale/partial duration behind
        — never misattribute latency to a request that never got a response."""
        with (
            patch.object(self.client, "get_status", return_value={"loaded_models": []}),
            patch.object(self.client, "list_models", return_value={"data": []}),
            patch.object(
                self.client,
                "load_model",
                side_effect=LemonadeClientError("boom"),
            ),
        ):
            with self.assertRaises(LemonadeClientError):
                self.client._ensure_model_loaded_locked(TEST_MODEL)
        self.assertIsNone(self.client._last_model_load_seconds)

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
    def test_pull_embedding_model_request_shape(self):
        """The embedder registration must send a VALID /pull body, not just any
        pull. EmbeddingGemma is a custom user-model: the request must carry the
        ``user.`` prefix, the checkpoint, ``recipe=llamacpp`` AND ``embedding=true``
        together. The ``embedding`` flag is what sets the 'embeddings' label —
        omitting it reproduces the #1745 501 "server does not support embeddings".
        Asserts shape (per CLAUDE.md: mocks prove validity, not just invocation).
        """
        from gaia.llm.lemonade_client import (
            DEFAULT_EMBEDDING_CHECKPOINT,
            DEFAULT_EMBEDDING_MODEL,
            MODELS,
        )

        # The registry entry that drives init/RAG/code-index registration.
        mr = MODELS["embeddinggemma"]
        self.assertEqual(mr.model_id, DEFAULT_EMBEDDING_MODEL)
        self.assertTrue(mr.model_id.startswith("user."))
        self.assertEqual(mr.checkpoint, DEFAULT_EMBEDDING_CHECKPOINT)
        self.assertEqual(mr.recipe, "llamacpp")
        self.assertTrue(mr.embedding)
        self.assertFalse(mr.tool_calling)

        responses.add(
            responses.POST,
            f"{API_BASE}/pull",
            json={"status": "success", "message": "ok"},
            status=200,
        )

        self.client.pull_model(
            model_name=mr.model_id,
            checkpoint=mr.checkpoint,
            recipe=mr.recipe,
            embedding=mr.embedding,
        )

        body = json.loads(responses.calls[-1].request.body)
        self.assertEqual(body["model_name"], DEFAULT_EMBEDDING_MODEL)
        self.assertTrue(body["model_name"].startswith("user."))
        self.assertEqual(body["checkpoint"], DEFAULT_EMBEDDING_CHECKPOINT)
        self.assertEqual(body["recipe"], "llamacpp")
        self.assertIs(body["embedding"], True)

    @responses.activate
    def test_ensure_model_downloaded_matches_user_namespace_display_id(self):
        """A ``user.``-registered model is listed by /v1/models under its STRIPPED
        id (e.g. ``embeddinggemma-300m-GGUF``), but referenced elsewhere as
        ``user.embeddinggemma-300m-GGUF``. ensure_model_downloaded must treat the
        two as the same model — otherwise the availability check never matches and
        it re-pulls forever (observed hang against a live Lemonade server).
        """
        from gaia.llm.lemonade_client import DEFAULT_EMBEDDING_MODEL, _model_ids_match

        # The matcher is namespace-tolerant and case-insensitive, but not a
        # substring match (must not confuse distinct models).
        self.assertTrue(
            _model_ids_match(DEFAULT_EMBEDDING_MODEL, "embeddinggemma-300m-GGUF")
        )
        self.assertTrue(_model_ids_match("user.Foo-GGUF", "foo-gguf"))
        self.assertFalse(
            _model_ids_match(DEFAULT_EMBEDDING_MODEL, "embed-gemma-300m-FLM")
        )

        # Server lists the stripped id; requesting the user.-prefixed name must
        # resolve to "already downloaded" without issuing a pull.
        responses.add(
            responses.GET,
            f"{API_BASE}/models",
            json={"data": [{"id": "embeddinggemma-300m-GGUF", "downloaded": True}]},
            status=200,
        )
        result = self.client.ensure_model_downloaded(
            DEFAULT_EMBEDDING_MODEL, show_progress=False
        )
        self.assertTrue(result)
        # No /pull call was made — only the /models availability probe.
        self.assertTrue(all("/pull" not in c.request.url for c in responses.calls))

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
    def test_pull_model_stream(self):
        """Test pulling a model with streaming progress updates."""
        # Mock SSE response for streaming pull
        sse_response = (
            "event: progress\n"
            'data: {"file":"model.gguf","file_index":1,"total_files":2,'
            '"bytes_downloaded":536870912,"bytes_total":2684354560,"percent":20}\n\n'
            "event: progress\n"
            'data: {"file":"model.gguf","file_index":1,"total_files":2,'
            '"bytes_downloaded":1073741824,"bytes_total":2684354560,"percent":40}\n\n'
            "event: progress\n"
            'data: {"file":"config.json","file_index":2,"total_files":2,'
            '"bytes_downloaded":1024,"bytes_total":1024,"percent":100}\n\n'
            "event: complete\n"
            'data: {"file_index":2,"total_files":2,"percent":100}\n\n'
        )

        responses.add(
            responses.POST,
            f"{API_BASE}/pull",
            body=sse_response,
            status=200,
            content_type="text/event-stream",
        )

        # Collect events from streaming pull
        events = []

        for event in self.client.pull_model_stream(model_name=TEST_MODEL):
            events.append(event)

        # Verify we got all events
        self.assertEqual(len(events), 4)
        self.assertEqual(events[0]["event"], "progress")
        self.assertEqual(events[0]["percent"], 20)
        self.assertEqual(events[1]["percent"], 40)
        self.assertEqual(events[2]["file"], "config.json")
        self.assertEqual(events[3]["event"], "complete")
        self.assertEqual(events[3]["percent"], 100)

    @responses.activate
    def test_pull_by_name_registers_a_known_user_model(self):
        """A user. model pulled by name alone (the auto-download path) must carry
        its registration, or Lemonade has nothing to pull; a built-in must carry
        none, since a recipe on a built-in pull is a 400 (#1655)."""
        from gaia.llm.lemonade_client import (
            DEFAULT_MODEL_NAME,
            FLASH_OPTION_MODEL_NAME,
            find_model_requirement,
        )

        responses.add(
            responses.POST,
            f"{API_BASE}/pull",
            body='event: complete\ndata: {"percent":100}\n\n',
            status=200,
            content_type="text/event-stream",
        )
        list(self.client.pull_model_stream(model_name=FLASH_OPTION_MODEL_NAME))
        list(self.client.pull_model_stream(model_name=DEFAULT_MODEL_NAME))

        flash = json.loads(responses.calls[0].request.body)
        mr = find_model_requirement(FLASH_OPTION_MODEL_NAME)
        self.assertEqual(flash["model_name"], FLASH_OPTION_MODEL_NAME)
        self.assertEqual(flash["checkpoint"], mr.checkpoint)
        self.assertEqual(flash["recipe"], mr.recipe)
        self.assertEqual(flash["mmproj"], mr.mmproj)
        self.assertTrue(flash["vision"] and flash["reasoning"])

        builtin = json.loads(responses.calls[1].request.body)
        self.assertNotIn("checkpoint", builtin)
        self.assertNotIn("recipe", builtin)

    @responses.activate
    def test_flash_pull_registers_it_as_a_user_model(self):
        """Lemonade does not ship Flash, so the pull must carry its whole
        registration: checkpoint, recipe, projector and labels (#1655)."""
        from gaia.llm.lemonade_client import FLASH_OPTION_MODEL_NAME

        responses.add(
            responses.POST,
            f"{API_BASE}/pull",
            body='event: complete\ndata: {"percent":100}\n\n',
            status=200,
            content_type="text/event-stream",
        )
        list(self.client.pull_model_stream(model_name=FLASH_OPTION_MODEL_NAME))

        self.assertEqual(
            json.loads(responses.calls[0].request.body),
            {
                "model_name": "user.Qwen3.8-Flash-Next-GGUF",
                "stream": True,
                "checkpoint": "unsloth/Qwen3.8-Flash-Next-GGUF:UD-IQ3_XXS",
                "recipe": "llamacpp",
                "reasoning": True,
                "vision": True,
                "mmproj": "mmproj-F16.gguf",
            },
        )

    @responses.activate
    def test_large_default_pulls_by_name_only(self):
        """Qwen3.6 35B A3B is a Lemonade built-in: the pull names it and nothing
        else, because a recipe on a built-in pull is a 400 (#1655)."""
        from gaia.llm.lemonade_client import LARGE_DEFAULT_MODEL_NAME

        self.assertFalse(LARGE_DEFAULT_MODEL_NAME.startswith("user."))
        responses.add(
            responses.POST,
            f"{API_BASE}/pull",
            body='event: complete\ndata: {"percent":100}\n\n',
            status=200,
            content_type="text/event-stream",
        )
        list(self.client.pull_model_stream(model_name=LARGE_DEFAULT_MODEL_NAME))

        self.assertEqual(
            json.loads(responses.calls[0].request.body),
            {"model_name": LARGE_DEFAULT_MODEL_NAME, "stream": True},
        )

    @responses.activate
    def test_pull_model_stream_error(self):
        """Test handling errors during streaming model pull."""
        # Mock SSE response with error
        sse_response = (
            "event: progress\n"
            'data: {"file":"model.gguf","file_index":1,"total_files":1,'
            '"bytes_downloaded":100,"bytes_total":1000,"percent":10}\n\n'
            "event: error\n"
            'data: {"error":"Network error: connection reset"}\n\n'
        )

        responses.add(
            responses.POST,
            f"{API_BASE}/pull",
            body=sse_response,
            status=200,
            content_type="text/event-stream",
        )

        # Pull should raise error after yielding progress and error events
        events = []
        with self.assertRaises(LemonadeClientError) as context:
            for event in self.client.pull_model_stream(model_name=TEST_MODEL):
                events.append(event)

        # Verify we got progress and error events before exception
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event"], "progress")
        self.assertEqual(events[1]["event"], "error")
        self.assertIn("Network error", str(context.exception))

    @responses.activate
    def test_ensure_model_downloaded_when_not_present(self):
        """Test ensure_model_downloaded downloads model when not present."""
        # First call to list_models: model not downloaded
        models_response_not_downloaded = {
            "data": [{"id": TEST_MODEL, "downloaded": False}]
        }
        # Second call: model is now downloaded (after pull completes)
        models_response_downloaded = {"data": [{"id": TEST_MODEL, "downloaded": True}]}
        responses.add(
            responses.GET,
            f"{API_BASE}/models",
            json=models_response_not_downloaded,
            status=200,
        )
        responses.add(
            responses.GET,
            f"{API_BASE}/models",
            json=models_response_downloaded,
            status=200,
        )

        # Mock pull endpoint (non-streaming)
        pull_response = {
            "status": "success",
            "model_name": TEST_MODEL,
        }
        responses.add(
            responses.POST,
            f"{API_BASE}/pull",
            json=pull_response,
            status=200,
        )

        # The waiter polls every 30 s; don't spend that in a unit test.
        with patch("gaia.llm.lemonade_client.time.sleep") as mock_sleep:
            result = self.client.ensure_model_downloaded(
                TEST_MODEL, show_progress=False
            )

        self.assertTrue(result)
        mock_sleep.assert_called()
        pulls = [c for c in responses.calls if c.request.url.endswith("/pull")]
        self.assertEqual(len(pulls), 1)
        # A built-in is pulled by name alone; a recipe would make Lemonade 400.
        self.assertEqual(json.loads(pulls[0].request.body), {"model_name": TEST_MODEL})

    def test_get_required_models_for_chat(self):
        """Test get_required_models returns correct models for chat agent."""
        model_ids = self.client.get_required_models("chat")

        self.assertIn("Gemma-4-E4B-it-GGUF", model_ids)
        self.assertIn("user.embeddinggemma-300m-GGUF", model_ids)

    def test_get_required_models_for_minimal(self):
        """Test get_required_models returns correct models for minimal agent."""
        model_ids = self.client.get_required_models("minimal")

        self.assertIn("Gemma-4-E4B-it-GGUF", model_ids)
        self.assertEqual(len(model_ids), 1)

    def test_get_required_models_all(self):
        """Test get_required_models returns all unique models."""
        model_ids = self.client.get_required_models("all")

        # All profiles use gemma-4-e4b; some also use embeddinggemma
        self.assertIn("Gemma-4-E4B-it-GGUF", model_ids)
        self.assertIn("user.embeddinggemma-300m-GGUF", model_ids)
        self.assertEqual(len(model_ids), 2)

    def test_get_required_models_unknown_agent(self):
        """Test get_required_models returns empty list for unknown agent."""
        model_ids = self.client.get_required_models("nonexistent")
        self.assertEqual(len(model_ids), 0)

    @responses.activate
    def test_check_model_available_true(self):
        """Test check_model_available returns True when model is available."""
        # check_model_available uses list_models(show_all=True) which calls /models
        models_response = {
            "data": [
                {"id": TEST_MODEL, "downloaded": True},
                {"id": "other-model", "downloaded": False},
            ]
        }
        responses.add(
            responses.GET,
            f"{API_BASE}/models",
            json=models_response,
            status=200,
        )

        result = self.client.check_model_available(TEST_MODEL)
        self.assertTrue(result)

    @responses.activate
    def test_check_model_available_false(self):
        """Test check_model_available returns False when model is not available."""
        # check_model_available uses list_models(show_all=True) which calls /models
        models_response = {
            "data": [
                {"id": TEST_MODEL, "downloaded": False},
            ]
        }
        responses.add(
            responses.GET,
            f"{API_BASE}/models",
            json=models_response,
            status=200,
        )

        result = self.client.check_model_available(TEST_MODEL)
        self.assertFalse(result)

    @responses.activate
    def test_check_model_available_not_found(self):
        """Test check_model_available returns False when model not in list."""
        # check_model_available uses list_models(show_all=True) which calls /models
        models_response = {"data": []}
        responses.add(
            responses.GET,
            f"{API_BASE}/models",
            json=models_response,
            status=200,
        )

        result = self.client.check_model_available(TEST_MODEL)
        self.assertFalse(result)

    @responses.activate
    def test_download_agent_models_all_available(self):
        """Test download_agent_models skips already available models."""
        # Mock /models endpoint (used by check_model_available via list_models)
        models_response = {
            "data": [
                {"id": "Gemma-4-E4B-it-GGUF", "downloaded": True},
            ]
        }
        responses.add(
            responses.GET,
            f"{API_BASE}/models",
            json=models_response,
            status=200,
        )

        result = self.client.download_agent_models("minimal")

        self.assertTrue(result["success"])
        self.assertEqual(len(result["models"]), 1)
        self.assertEqual(result["models"][0]["status"], "already_available")
        self.assertTrue(result["models"][0]["skipped"])

    @responses.activate
    def test_download_agent_models_downloads_missing(self):
        """Test download_agent_models downloads missing models."""
        # Mock /models endpoint (used by check_model_available via list_models)
        models_response = {
            "data": [
                {"id": "Gemma-4-E4B-it-GGUF", "downloaded": False},
            ]
        }
        responses.add(
            responses.GET,
            f"{API_BASE}/models",
            json=models_response,
            status=200,
        )

        # Mock SSE response for streaming pull
        sse_response = (
            "event: progress\n"
            'data: {"file":"model.gguf","file_index":1,"total_files":1,'
            '"bytes_downloaded":1000,"bytes_total":1000,"percent":100}\n\n'
            "event: complete\n"
            'data: {"file_index":1,"total_files":1,"percent":100}\n\n'
        )
        responses.add(
            responses.POST,
            f"{API_BASE}/pull",
            body=sse_response,
            status=200,
            content_type="text/event-stream",
        )

        result = self.client.download_agent_models("minimal")

        self.assertTrue(result["success"])
        self.assertEqual(len(result["models"]), 1)
        self.assertEqual(result["models"][0]["status"], "completed")
        self.assertFalse(result["models"][0]["skipped"])

    @responses.activate
    def test_validate_context_size_sufficient(self):
        """Test validate_context_size returns True when context is sufficient."""
        # Lemonade 9.1.4+ format: ctx_size in all_models_loaded[N].recipe_options
        health_response = {
            "status": "ok",
            "model_loaded": TEST_MODEL,
            "version": "9.1.4",
            "all_models_loaded": [
                {
                    "backend_url": "http://127.0.0.1:8001/v1",
                    "checkpoint": "amd/Llama-3.2-3B-Instruct-GGUF",
                    "device": "gpu",
                    "model_name": TEST_MODEL,
                    "recipe": "llamacpp",
                    "recipe_options": {
                        "ctx_size": 32768,
                    },
                    "type": "llm",
                }
            ],
        }
        responses.add(
            responses.GET, f"{API_BASE}/health", json=health_response, status=200
        )

        valid, error = self.client.validate_context_size(
            required_tokens=32768, quiet=True
        )

        self.assertTrue(valid)
        self.assertIsNone(error)

    @responses.activate
    def test_validate_context_size_skips_non_llm_entry(self):
        """A non-LLM entry sorting first must not shadow the LLM's ctx_size."""
        health_response = {
            "status": "ok",
            "model_loaded": TEST_MODEL,
            "version": "9.1.4",
            "all_models_loaded": [
                {
                    "model_name": "Whisper-Large-v3-Turbo",
                    "type": "transcription",
                    "recipe_options": {"ctx_size": 4096},
                },
                {
                    "model_name": TEST_MODEL,
                    "type": "llm",
                    "recipe_options": {"ctx_size": 65536},
                },
            ],
        }
        responses.add(
            responses.GET, f"{API_BASE}/health", json=health_response, status=200
        )

        valid, error = self.client.validate_context_size(
            required_tokens=32768, quiet=True
        )

        self.assertTrue(valid)
        self.assertIsNone(error)

    @responses.activate
    def test_validate_context_size_insufficient(self):
        """Test validate_context_size returns False when context is insufficient."""
        # Lemonade 9.1.4+ format: ctx_size in all_models_loaded[N].recipe_options
        health_response = {
            "status": "ok",
            "model_loaded": TEST_MODEL,
            "version": "9.1.4",
            "all_models_loaded": [
                {
                    "backend_url": "http://127.0.0.1:8001/v1",
                    "checkpoint": "amd/Llama-3.2-3B-Instruct-GGUF",
                    "device": "gpu",
                    "model_name": TEST_MODEL,
                    "recipe": "llamacpp",
                    "recipe_options": {
                        "ctx_size": 4096,  # Less than required
                    },
                    "type": "llm",
                }
            ],
        }
        responses.add(
            responses.GET, f"{API_BASE}/health", json=health_response, status=200
        )

        # The restart hint depends on what this host has installed; pin a
        # legacy install so the rendered command is the same everywhere.
        legacy = LemonadeTooling(
            found=True,
            kind="legacy",
            client_path="lemonade-server",
            server_launcher="lemonade-server",
        )
        with (
            patch("gaia.llm.lemonade_launcher.gaia_runs_lemonade", return_value=False),
            patch("gaia.llm.lemonade_launcher.resolve_lemonade", return_value=legacy),
        ):
            valid, error = self.client.validate_context_size(
                required_tokens=32768, quiet=True
            )

        self.assertFalse(valid)
        self.assertIsNotNone(error)
        self.assertIn("4096", error)
        self.assertIn("--ctx-size 32768", error)

    @responses.activate
    def test_validate_context_size_health_failure(self):
        """Test validate_context_size returns True on health check failure (don't block)."""
        responses.add(
            responses.GET,
            f"{API_BASE}/health",
            body=requests.exceptions.ConnectionError("Connection refused"),
        )

        valid, error = self.client.validate_context_size(
            required_tokens=32768, quiet=True
        )

        # Should return True to not block on connection errors
        self.assertTrue(valid)
        self.assertIsNone(error)

    @responses.activate
    def test_validate_context_size_legacy_format(self):
        """Test validate_context_size with older Lemonade format (pre-9.1.4)."""
        # Legacy format: context_size at top level (pre-9.1.4)
        health_response = {
            "status": "ok",
            "context_size": 32768,
            "model_loaded": TEST_MODEL,
            "checkpoint_loaded": "amd/Llama-3.2-3B-Instruct-GGUF",
        }
        responses.add(
            responses.GET, f"{API_BASE}/health", json=health_response, status=200
        )

        valid, error = self.client.validate_context_size(
            required_tokens=32768, quiet=True
        )

        self.assertTrue(valid)
        self.assertIsNone(error)

    @responses.activate
    def test_get_status_context_size_ignores_non_llm_models(self):
        """get_status() must report the LLM's ctx_size, not a transcription
        or other non-embedding model's, even if that model sorts first in
        all_models_loaded (issue: Whisper-Large-v3-Turbo's ctx_size=4096 was
        being reported instead of Qwen3-8B-GGUF's ctx_size=65536)."""
        health_response = {
            "status": "ok",
            "model_loaded": TEST_MODEL,
            "version": "9.1.4",
            "all_models_loaded": [
                {
                    "backend_url": "http://127.0.0.1:8001/v1",
                    "checkpoint": "amd/Whisper-Large-v3-Turbo",
                    "device": "cpu",
                    "model_name": "Whisper-Large-v3-Turbo",
                    "recipe": "llamacpp",
                    "recipe_options": {
                        "ctx_size": 4096,
                    },
                    "type": "transcription",
                },
                {
                    "backend_url": "http://127.0.0.1:8002/v1",
                    "checkpoint": "amd/Qwen3-8B-GGUF",
                    "device": "gpu",
                    "model_name": "Qwen3-8B-GGUF",
                    "recipe": "llamacpp",
                    "recipe_options": {
                        "ctx_size": 65536,
                    },
                    "type": "llm",
                },
            ],
        }
        responses.add(
            responses.GET, f"{API_BASE}/health", json=health_response, status=200
        )
        responses.add(
            responses.GET,
            f"{API_BASE}/models",
            json={"object": "list", "data": []},
            status=200,
        )

        status = self.client.get_status()

        self.assertEqual(status.context_size, 65536)

    # ------------------------------------------------------------------
    # LEMONADE_API_KEY support (issue #1139)
    # ------------------------------------------------------------------

    def _without_env(self, var):
        """Return a context manager that removes ``var`` from os.environ."""
        return patch.dict(
            os.environ, {k: v for k, v in os.environ.items() if k != var}, clear=True
        )

    def test_resolve_lemonade_api_key_explicit_wins_over_env(self):
        with patch.dict(os.environ, {"LEMONADE_API_KEY": "abc"}):
            self.assertEqual(resolve_lemonade_api_key("xyz"), "xyz")

    def test_resolve_lemonade_api_key_env_fallback(self):
        with patch.dict(os.environ, {"LEMONADE_API_KEY": "abc"}):
            self.assertEqual(resolve_lemonade_api_key(None), "abc")

    def test_resolve_lemonade_api_key_unset_returns_none(self):
        with self._without_env("LEMONADE_API_KEY"):
            self.assertIsNone(resolve_lemonade_api_key(None))

    def test_resolve_lemonade_api_key_empty_string_treated_as_unset(self):
        with patch.dict(os.environ, {"LEMONADE_API_KEY": ""}):
            self.assertIsNone(resolve_lemonade_api_key(None))

    def test_resolve_lemonade_api_key_whitespace_treated_as_unset(self):
        with patch.dict(os.environ, {"LEMONADE_API_KEY": "   "}):
            self.assertIsNone(resolve_lemonade_api_key(None))

    def test_lemonade_auth_headers_with_key(self):
        self.assertEqual(lemonade_auth_headers("abc"), {"Authorization": "Bearer abc"})

    def test_lemonade_auth_headers_no_key(self):
        self.assertEqual(lemonade_auth_headers(None), {})
        self.assertEqual(lemonade_auth_headers(""), {})

    def test_lemonade_client_stores_api_key_from_kwarg(self):
        with self._without_env("LEMONADE_API_KEY"):
            client = LemonadeClient(api_key="abc")
        self.assertEqual(getattr(client, "api_key", None), "abc")

    def test_lemonade_client_reads_api_key_from_env(self):
        with patch.dict(os.environ, {"LEMONADE_API_KEY": "abc"}):
            client = LemonadeClient()
        self.assertEqual(getattr(client, "api_key", None), "abc")

    def test_lemonade_client_api_key_none_when_unset(self):
        with self._without_env("LEMONADE_API_KEY"):
            client = LemonadeClient()
        self.assertIsNone(getattr(client, "api_key", "sentinel"))

    @responses.activate
    def test_send_request_includes_auth_header_when_key_set(self):
        responses.add(
            responses.GET, f"{API_BASE}/health", json={"status": "ok"}, status=200
        )
        client = LemonadeClient(host=HOST, port=PORT, api_key="abc", verbose=False)
        client.health_check()
        self.assertEqual(
            responses.calls[0].request.headers.get("Authorization"), "Bearer abc"
        )

    @responses.activate
    def test_send_request_omits_auth_header_when_no_key(self):
        responses.add(
            responses.GET, f"{API_BASE}/health", json={"status": "ok"}, status=200
        )
        with self._without_env("LEMONADE_API_KEY"):
            client = LemonadeClient(host=HOST, port=PORT, verbose=False)
            client.health_check()
        self.assertNotIn("Authorization", responses.calls[0].request.headers)

    @patch("gaia.llm.lemonade_client.LemonadeClient._ensure_model_loaded")
    @patch("gaia.llm.lemonade_client.OpenAI")
    def test_openai_client_passes_real_key_when_set(self, mock_openai, _mock_ensure):
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_stream = MagicMock()
        mock_stream.__iter__.return_value = iter([])
        mock_client.chat.completions.create.return_value = mock_stream

        client = LemonadeClient(host=HOST, port=PORT, api_key="abc", verbose=False)
        list(
            client.chat_completions(
                model=TEST_MODEL,
                messages=[{"role": "user", "content": "hi"}],
                stream=True,
            )
        )

        self.assertEqual(mock_openai.call_args.kwargs.get("api_key"), "abc")

    @patch("gaia.llm.lemonade_client.LemonadeClient._ensure_model_loaded")
    @patch("gaia.llm.lemonade_client.OpenAI")
    def test_openai_client_uses_placeholder_when_no_key(
        self, mock_openai, _mock_ensure
    ):
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_stream = MagicMock()
        mock_stream.__iter__.return_value = iter([])
        mock_client.chat.completions.create.return_value = mock_stream

        with self._without_env("LEMONADE_API_KEY"):
            client = LemonadeClient(host=HOST, port=PORT, verbose=False)
        list(
            client.chat_completions(
                model=TEST_MODEL,
                messages=[{"role": "user", "content": "hi"}],
                stream=True,
            )
        )

        self.assertEqual(mock_openai.call_args.kwargs.get("api_key"), "lemonade")

    @responses.activate
    def test_chat_completions_nonstream_includes_auth_header(self):
        self._mock_cold_load()
        responses.add(
            responses.POST,
            f"{API_BASE}/chat/completions",
            json={"id": "0", "choices": [{"message": {"content": "hi"}}]},
            status=200,
        )
        client = LemonadeClient(host=HOST, port=PORT, api_key="abc", verbose=False)
        client.chat_completions(
            model=TEST_MODEL, messages=[{"role": "user", "content": "hi"}]
        )
        self._assert_one_load()
        # The pre-request /health, /models and /load must carry the key too.
        self.assertEqual(
            {
                c.request.url: c.request.headers.get("Authorization")
                for c in responses.calls
            },
            {c.request.url: "Bearer abc" for c in responses.calls},
        )
        self.assertTrue(responses.calls[-1].request.url.endswith("/chat/completions"))

    def _chat_body(self, model, **kwargs):
        responses.add(
            responses.POST,
            f"{API_BASE}/chat/completions",
            json={"id": "0", "choices": [{"message": {"content": "hi"}}]},
            status=200,
        )
        client = LemonadeClient(host=HOST, port=PORT, verbose=False)
        with patch.object(LemonadeClient, "_ensure_model_loaded"):
            client.chat_completions(
                model=model, messages=[{"role": "user", "content": "hi"}], **kwargs
            )
        return json.loads(responses.calls[-1].request.body)

    @responses.activate
    def test_large_default_thinks_because_gaia_says_so(self):
        """The thinking mode is GAIA's choice, sent on every request, not the
        chat template's default."""
        from gaia.llm.lemonade_client import LARGE_DEFAULT_MODEL_NAME

        body = self._chat_body(LARGE_DEFAULT_MODEL_NAME)
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": True})

    @responses.activate
    def test_caller_thinking_choice_wins(self):
        from gaia.llm.lemonade_client import LARGE_DEFAULT_MODEL_NAME

        body = self._chat_body(
            LARGE_DEFAULT_MODEL_NAME,
            chat_template_kwargs={"enable_thinking": False, "preserve_thinking": True},
        )
        self.assertEqual(
            body["chat_template_kwargs"],
            {"enable_thinking": False, "preserve_thinking": True},
        )

    @responses.activate
    def test_a_model_without_a_thinking_choice_sends_none(self):
        body = self._chat_body("Gemma-4-E4B-it-GGUF")
        self.assertNotIn("chat_template_kwargs", body)

    @patch("gaia.llm.lemonade_client.LemonadeClient._ensure_model_loaded")
    @patch("gaia.llm.lemonade_client.OpenAI")
    def test_streamed_request_carries_the_thinking_choice(
        self, mock_openai, _mock_ensure
    ):
        from gaia.llm.lemonade_client import LARGE_DEFAULT_MODEL_NAME

        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_stream = MagicMock()
        mock_stream.__iter__.return_value = iter([])
        mock_client.chat.completions.create.return_value = mock_stream

        client = LemonadeClient(host=HOST, port=PORT, verbose=False)
        list(
            client.chat_completions(
                model=LARGE_DEFAULT_MODEL_NAME,
                messages=[{"role": "user", "content": "hi"}],
                stream=True,
            )
        )
        sent = mock_client.chat.completions.create.call_args.kwargs
        self.assertEqual(
            sent["extra_body"]["chat_template_kwargs"], {"enable_thinking": True}
        )

    @responses.activate
    def test_completions_nonstream_includes_auth_header(self):
        responses.add(
            responses.POST,
            f"{API_BASE}/completions",
            json={"id": "0", "choices": [{"text": "hi"}]},
            status=200,
        )
        client = LemonadeClient(host=HOST, port=PORT, api_key="abc", verbose=False)
        client.completions(model=TEST_MODEL, prompt="hi")
        self.assertEqual(
            responses.calls[0].request.headers.get("Authorization"), "Bearer abc"
        )

    @responses.activate
    def test_pull_model_stream_includes_auth_header_on_initial_post(self):
        sse_body = (
            "event: complete\n"
            'data: {"file_index":1,"total_files":1,"percent":100}\n\n'
        )
        responses.add(
            responses.POST,
            f"{API_BASE}/pull",
            body=sse_body,
            status=200,
            content_type="text/event-stream",
        )
        client = LemonadeClient(host=HOST, port=PORT, api_key="abc", verbose=False)
        # Consume the generator fully; the terminating "complete" event ends parsing.
        list(client.pull_model_stream(model_name=TEST_MODEL))
        self.assertEqual(
            responses.calls[0].request.headers.get("Authorization"), "Bearer abc"
        )

    @responses.activate
    def test_responses_endpoint_includes_auth_header(self):
        responses.add(
            responses.POST,
            f"{API_BASE}/responses",
            json={"id": "0", "output": [{"content": [{"text": "hi"}]}]},
            status=200,
        )
        client = LemonadeClient(host=HOST, port=PORT, api_key="abc", verbose=False)
        client.responses(model=TEST_MODEL, input="hi")
        self.assertEqual(
            responses.calls[0].request.headers.get("Authorization"), "Bearer abc"
        )

    @responses.activate
    def test_api_key_never_appears_in_logs(self):
        responses.add(
            responses.GET, f"{API_BASE}/health", json={"status": "ok"}, status=200
        )
        # Force DEBUG level on the lemonade_client logger so the
        # "Lemonade API key configured" debug entry is captured.
        # ``assertLogs`` only changes the watched logger's level; child
        # loggers retain their inherited level which defaults to INFO.
        lc_logger = logging.getLogger("gaia.llm.lemonade_client")
        prev_level = lc_logger.level
        lc_logger.setLevel(logging.DEBUG)
        try:
            with self.assertLogs("gaia.llm.lemonade_client", level=logging.DEBUG) as cm:
                client = LemonadeClient(
                    host=HOST, port=PORT, api_key="supersecret-1139", verbose=True
                )
                client.health_check()
            joined = "\n".join(cm.output)
            self.assertNotIn(
                "supersecret-1139",
                joined,
                f"API key value leaked into log output:\n{joined}",
            )
        finally:
            lc_logger.setLevel(prev_level)

    def test_each_client_snapshots_env_at_construction_time(self):
        with patch.dict(os.environ, {"LEMONADE_API_KEY": "A"}):
            client_a = LemonadeClient()
        with patch.dict(os.environ, {"LEMONADE_API_KEY": "B"}):
            client_b = LemonadeClient()
        self.assertEqual(getattr(client_a, "api_key", None), "A")
        self.assertEqual(getattr(client_b, "api_key", None), "B")

    @responses.activate
    def test_401_error_message_names_LEMONADE_API_KEY(self):
        responses.add(
            responses.GET,
            f"{API_BASE}/health",
            json={"error": "Unauthorized"},
            status=401,
        )
        # Suppress error logs for the duration of this test.
        logging.disable(logging.CRITICAL)
        try:
            client = LemonadeClient(
                host=HOST, port=PORT, api_key="wrong-key", verbose=False
            )
            with self.assertRaises(LemonadeClientError) as ctx:
                client.health_check()
            self.assertIn("LEMONADE_API_KEY", str(ctx.exception))
        finally:
            logging.disable(logging.NOTSET)

    @responses.activate
    def test_401_response_does_not_leak_authorization_header_in_error_message(self):
        # Simulate a misbehaving reverse proxy that reflects the incoming
        # Authorization header in its 401 body. The user-facing error
        # message must NOT carry that string through.
        responses.add(
            responses.GET,
            f"{API_BASE}/health",
            body="401 Unauthorized — request was: Authorization: Bearer supersecret-1139",
            status=401,
        )
        logging.disable(logging.CRITICAL)
        try:
            client = LemonadeClient(
                host=HOST,
                port=PORT,
                api_key="supersecret-1139",
                verbose=False,
            )
            with self.assertRaises(LemonadeClientError) as ctx:
                client.health_check()
            msg = str(ctx.exception)
            self.assertNotIn("Bearer", msg, "Bearer keyword leaked into error message")
            self.assertNotIn(
                "supersecret-1139", msg, "API key value leaked into error message"
            )
        finally:
            logging.disable(logging.NOTSET)

    @patch("gaia.llm.lemonade_client.LemonadeClient._ensure_model_loaded")
    @patch("gaia.llm.lemonade_client.OpenAI")
    def test_openai_authentication_error_surfaces_actionable_message(
        self, mock_openai, _mock_ensure
    ):
        import openai

        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        # The OpenAI SDK requires keyword args including ``response`` and ``body``;
        # use ``MagicMock(spec=...)`` to avoid SDK version coupling.
        mock_client.chat.completions.create.side_effect = openai.AuthenticationError(
            message="OpenAI internal detail with key",
            response=MagicMock(status_code=401),
            body={"error": "Unauthorized"},
        )

        logging.disable(logging.CRITICAL)
        try:
            client = LemonadeClient(
                host=HOST, port=PORT, api_key="wrong-key", verbose=False
            )
            with self.assertRaises(LemonadeClientError) as ctx:
                list(
                    client.chat_completions(
                        model=TEST_MODEL,
                        messages=[{"role": "user", "content": "hi"}],
                        stream=True,
                    )
                )
            msg = str(ctx.exception)
            self.assertIn("LEMONADE_API_KEY", msg)
            self.assertNotIn(
                "OpenAI internal detail",
                msg,
                "OpenAI exception text leaked into error message",
            )
        finally:
            logging.disable(logging.NOTSET)

    @patch("gaia.llm.lemonade_client.LemonadeClient._ensure_model_loaded")
    @patch("gaia.llm.lemonade_client.OpenAI")
    def test_openai_authentication_error_on_text_completions_streaming(
        self, mock_openai, _mock_ensure
    ):
        """Mirror of the chat-streaming test for the text-streaming path."""
        import openai

        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_client.completions.create.side_effect = openai.AuthenticationError(
            message="OpenAI internal detail with key",
            response=MagicMock(status_code=401),
            body={"error": "Unauthorized"},
        )

        logging.disable(logging.CRITICAL)
        try:
            client = LemonadeClient(
                host=HOST, port=PORT, api_key="wrong-key", verbose=False
            )
            with self.assertRaises(LemonadeClientError) as ctx:
                list(client.completions(model=TEST_MODEL, prompt="hi", stream=True))
            msg = str(ctx.exception)
            self.assertIn("LEMONADE_API_KEY", msg)
            self.assertNotIn("OpenAI internal detail", msg)
        finally:
            logging.disable(logging.NOTSET)

    @responses.activate
    def test_get_status_propagates_401_instead_of_returning_not_running(self):
        # get_status() must raise LemonadeAuthError on 401 rather than
        # swallowing it and returning status.running=False.
        responses.add(
            responses.GET,
            f"{API_BASE}/health",
            json={"error": "Unauthorized"},
            status=401,
        )
        client = LemonadeClient(
            host=HOST, port=PORT, api_key="wrong-key", verbose=False
        )
        with self.assertRaises(LemonadeAuthError) as ctx:
            client.get_status()
        self.assertIn("LEMONADE_API_KEY", str(ctx.exception))

    @responses.activate
    @patch("gaia.llm.lemonade_client.LemonadeClient._ensure_model_loaded")
    def test_401_does_not_trigger_auto_download_retry(self, _mock_ensure):
        # Wrong key → 401 from chat completions. The auto-download retry
        # path inside ``_execute_with_auto_download`` must NOT call load_model
        # (which would otherwise try to "download" the model on a server we
        # cannot authenticate to). ``_ensure_model_loaded`` is mocked so the
        # only path that could call ``load_model`` is the retry wrapper itself.
        responses.add(
            responses.POST,
            f"{API_BASE}/chat/completions",
            json={"error": "Unauthorized"},
            status=401,
        )
        logging.disable(logging.CRITICAL)
        try:
            client = LemonadeClient(
                host=HOST, port=PORT, api_key="wrong-key", verbose=False
            )
            with patch.object(client, "load_model") as mock_load:
                with self.assertRaises(LemonadeClientError):
                    client.chat_completions(
                        model=TEST_MODEL,
                        messages=[{"role": "user", "content": "hi"}],
                    )
                self.assertEqual(
                    mock_load.call_count,
                    0,
                    "load_model was called on a 401 — auto-download retry must not fire",
                )
        finally:
            logging.disable(logging.NOTSET)


class TestLaunchServerModernLegacyDispatch(unittest.TestCase):
    """AC7 (issue #316): launch_server() must build its Popen argv/env from
    resolve_lemonade()/build_start_command() rather than the hardcoded
    ["lemonade-server", "serve"] literal, so it works against both modern
    (LemonadeServer.exe / lemond) and legacy (lemonade-server) tooling.
    """

    @patch(
        "gaia.llm.lemonade_client.LemonadeClient._classify_port_listeners",
        return_value=([], []),
    )
    @patch("subprocess.Popen")
    @patch("gaia.llm.lemonade_client.build_start_command")
    @patch("gaia.llm.lemonade_client.resolve_lemonade")
    def test_launch_server_uses_resolved_modern_command(
        self, mock_resolve, mock_build_cmd, mock_popen, mock_kill_port
    ):
        """When resolve_lemonade() resolves to modern tooling, launch_server()'s
        Popen call must match exactly what build_start_command() produced —
        not the old hardcoded legacy argv literal."""
        from gaia.llm.lemonade_launcher import LemonadeTooling, StartSpec

        mock_resolve.return_value = LemonadeTooling(
            found=True,
            kind="modern",
            client_path=r"C:\lemonade_server\bin\lemonade.exe",
            server_launcher=r"C:\lemonade_server\bin\LemonadeServer.exe",
        )
        mock_build_cmd.return_value = StartSpec(
            argv=[r"C:\lemonade_server\bin\LemonadeServer.exe", "--silent"],
            env={"LEMONADE_CTX_SIZE": "32768"},
        )
        mock_popen.return_value = MagicMock()

        client = LemonadeClient(host=HOST, port=PORT, verbose=False)
        # health_check would normally gate this via _classify_port_listeners —
        # patch it out directly since launch_server() calls it unconditionally
        # today; the "skip when already healthy" behavior is asserted
        # separately below.
        with patch.dict(os.environ, {"GAIA_TEST_SENTINEL": "1"}):
            with patch.object(client, "health_check", return_value=None):
                with patch("socket.create_connection"):
                    client.launch_server(background="silent", ctx_size=32768)

        mock_popen.assert_called_once()
        call_args, call_kwargs = mock_popen.call_args
        argv = call_args[0] if call_args else call_kwargs.get("args")
        self.assertEqual(
            argv, [r"C:\lemonade_server\bin\LemonadeServer.exe", "--silent"]
        )
        env = call_kwargs.get("env", {})
        self.assertEqual(env.get("LEMONADE_CTX_SIZE"), "32768")
        # spec.env must be MERGED into the parent environment — a bare
        # Popen(argv, env=spec.env) drops PATH/LOCALAPPDATA and breaks
        # LemonadeServer.exe.
        self.assertEqual(env.get("GAIA_TEST_SENTINEL"), "1")
        self.assertIn("PATH", env)

    @patch(
        "gaia.llm.lemonade_client.LemonadeClient._classify_port_listeners",
        return_value=([], []),
    )
    @patch("subprocess.Popen")
    @patch("gaia.llm.lemonade_client.build_start_command")
    @patch("gaia.llm.lemonade_client.resolve_lemonade")
    def test_launch_server_legacy_fallback_unchanged(
        self, mock_resolve, mock_build_cmd, mock_popen, mock_kill_port
    ):
        """When resolve_lemonade() resolves to legacy tooling, the Popen argv
        shape must remain byte-identical to today's:
        ["lemonade-server", "serve", "--ctx-size", "N"] — regression guard."""
        from gaia.llm.lemonade_launcher import LemonadeTooling, StartSpec

        mock_resolve.return_value = LemonadeTooling(
            found=True,
            kind="legacy",
            client_path="lemonade-server",
            server_launcher="lemonade-server",
        )
        mock_build_cmd.return_value = StartSpec(
            argv=["lemonade-server", "serve", "--ctx-size", "32768"],
            env={},
        )
        mock_popen.return_value = MagicMock()

        client = LemonadeClient(host=HOST, port=PORT, verbose=False)
        with patch.object(client, "health_check", return_value=None):
            with patch("socket.create_connection"):
                client.launch_server(background="silent", ctx_size=32768)

        mock_popen.assert_called_once()
        call_args, call_kwargs = mock_popen.call_args
        argv = call_args[0] if call_args else call_kwargs.get("args")
        self.assertEqual(argv, ["lemonade-server", "serve", "--ctx-size", "32768"])

    @patch("gaia.llm.lemonade_client.terminate_pid")
    @patch(
        "gaia.llm.lemonade_client.LemonadeClient._classify_port_listeners",
        return_value=([], []),
    )
    @patch("subprocess.Popen")
    @patch("gaia.llm.lemonade_client.build_start_command")
    @patch("gaia.llm.lemonade_client.resolve_lemonade")
    def test_launch_server_skips_kill_when_already_healthy(
        self, mock_resolve, mock_build_cmd, mock_popen, mock_classify, mock_kill_port
    ):
        """The port must not even be inspected, let alone freed, when
        health_check() already reports OK at entry to launch_server() —
        a healthy server already listening should not be killed."""
        from gaia.llm.lemonade_launcher import LemonadeTooling, StartSpec

        mock_resolve.return_value = LemonadeTooling(
            found=True,
            kind="legacy",
            client_path="lemonade-server",
            server_launcher="lemonade-server",
        )
        mock_build_cmd.return_value = StartSpec(
            argv=["lemonade-server", "serve", "--ctx-size", "32768"],
            env={},
        )
        mock_popen.return_value = MagicMock()

        client = LemonadeClient(host=HOST, port=PORT, verbose=False)
        with patch.object(client, "health_check", return_value={"status": "ok"}):
            with patch("socket.create_connection"):
                client.launch_server(background="silent", ctx_size=32768)

        mock_classify.assert_not_called()
        mock_kill_port.assert_not_called()
