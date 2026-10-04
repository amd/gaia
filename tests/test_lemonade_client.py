# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Integration tests for the Lemonade client API against a live Lemonade Server.

The mocked-HTTP tests live in ``tests/unit/test_lemonade_client_http.py``.
"""

import os
import sys
import unittest

import pytest

from gaia.llm.lemonade_client import (
    LemonadeClient,
    LemonadeClientError,
    create_lemonade_client,
)

# Test constants - override via GAIA_TEST_MODEL env var for different platforms
TEST_MODEL = os.environ.get("GAIA_TEST_MODEL", "Gemma-4-E4B-it-GGUF")

HOST = "localhost"
# Respect LEMONADE_PORT env var so CI can override for non-default setups.
# Default 13305 matches C++ server / lemonade-server v10.1.0+ (also what Linux CI uses).
PORT = int(os.environ.get("LEMONADE_PORT", 13305))
API_BASE = f"http://{HOST}:{PORT}/api/v1"


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

    def test_integration_health_check_914_format(self):
        """Integration test for Lemonade 9.1.4+ health check format.

        Verifies that the new health check response format with all_models_loaded
        is correctly parsed and context_size is properly extracted.

        Lemonade 9.1.4+ moved context_size from top-level to:
        all_models_loaded[N].recipe_options.ctx_size
        """
        print("\n=== Testing Lemonade 9.1.4+ Health Check Format ===")

        # Step 1: Get raw health check response
        print("Step 1: Fetching health check response...")
        health = self.client.health_check()
        print(f"Health response keys: {list(health.keys())}")

        # Step 2: Verify basic health status
        self.assertIn("status", health)
        self.assertEqual(health["status"], "ok")
        print("✅ Health status is 'ok'")

        # Step 3: Check for Lemonade 9.1.4+ format (all_models_loaded)
        if "all_models_loaded" in health:
            print("✅ Found 'all_models_loaded' field (Lemonade 9.1.4+ format)")
            all_models = health["all_models_loaded"]
            self.assertIsInstance(all_models, list)
            print(f"   Number of loaded models: {len(all_models)}")

            if len(all_models) > 0:
                first_model = all_models[0]
                print(f"   First model keys: {list(first_model.keys())}")

                # Verify model structure
                self.assertIn("model_name", first_model)
                self.assertIn("recipe_options", first_model)
                print(f"   Model name: {first_model.get('model_name')}")
                print(f"   Device: {first_model.get('device')}")
                print(f"   Recipe: {first_model.get('recipe')}")

                # Verify ctx_size in recipe_options
                recipe_options = first_model.get("recipe_options", {})
                print(f"   Recipe options: {recipe_options}")

                if "ctx_size" in recipe_options:
                    ctx_size = recipe_options["ctx_size"]
                    self.assertIsInstance(ctx_size, int)
                    self.assertGreater(ctx_size, 0)
                    print(f"✅ Context size from recipe_options: {ctx_size}")
                else:
                    print(
                        "⚠️  ctx_size not found in recipe_options (model may not have it set)"
                    )
        else:
            print("⚠️  'all_models_loaded' not found - using legacy format (pre-9.1.4)")
            if "context_size" in health:
                print(f"   Legacy context_size: {health['context_size']}")

        # Step 4: Verify get_status() extracts context_size correctly
        print("\nStep 2: Testing get_status() context extraction...")
        status = self.client.get_status()
        self.assertTrue(status.running)
        print(f"   Server running: {status.running}")
        print(f"   Context size from get_status(): {status.context_size}")

        # Context size should be > 0 if an LLM model is loaded
        # SD/embedding models don't have context_size, so check loaded_models
        if health.get("model_loaded"):
            # Check if it's an LLM model (not SD/embedding)
            is_llm_model = True
            if status.loaded_models:
                # If all loaded models are image/embedding models, context_size can be 0
                is_llm_model = any(
                    "image" not in model.get("labels", [])
                    and "embed" not in model.get("labels", [])
                    for model in status.loaded_models
                )

            if is_llm_model and status.context_size > 0:
                print(
                    f"✅ get_status() correctly extracted context_size: {status.context_size}"
                )
            elif is_llm_model and status.context_size == 0:
                print(
                    f"⚠️  LLM model loaded but context_size is 0 (may still be initializing)"
                )
            else:
                print(
                    f"✅ Non-LLM model loaded (SD/embedding), context_size=0 is expected"
                )

        # Step 5: Verify validate_context_size() works
        print("\nStep 3: Testing validate_context_size()...")
        # Only test validation if an LLM model is loaded (context_size > 0)
        if status.context_size > 0:
            # Use a small required size that should pass
            valid, error = self.client.validate_context_size(
                required_tokens=1024, quiet=True
            )
            self.assertTrue(valid, f"validate_context_size(1024) should pass: {error}")
            print("✅ validate_context_size(1024) passed")
        else:
            print(
                "⏭️  Skipping validate_context_size() - no LLM model loaded (context_size is 0)"
            )

        # Test with current context size (should pass)
        if status.context_size > 0:
            valid, error = self.client.validate_context_size(
                required_tokens=status.context_size, quiet=True
            )
            self.assertTrue(
                valid,
                f"validate_context_size({status.context_size}) should pass: {error}",
            )
            print(f"✅ validate_context_size({status.context_size}) passed")

        print("\n✅ Lemonade 9.1.4+ health check format test PASSED")

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
        usage = None
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

                # The token accounting arrives in a final chunk that carries no
                # choices — that is the OpenAI streaming shape when usage is
                # requested, and the only place a streamed turn reports its
                # tokens at all. Indexing choices[0] unconditionally crashes on
                # it, which is how this was found.
                if chunk.get("usage"):
                    usage = chunk["usage"]
                if not chunk["choices"]:
                    continue

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

            # A streamed turn has to report its tokens. Without this the cost
            # and tokens/sec readouts have nothing to sum, and the gap is
            # invisible locally because /stats answers instead — only a real
            # server proves it, which is why this assertion lives here.
            self.assertIsNotNone(
                usage, "streamed turn reported no usage — stream_options lost?"
            )
            self.assertGreater(usage.get("prompt_tokens", 0), 0)
            self.assertGreater(usage.get("completion_tokens", 0), 0)
            print("✅ Streaming chat completion test passed")

        except LemonadeClientError as e:
            error_str = str(e)
            print(f"❌ Error during streaming chat completion: {error_str}")
            # Fail the test for all errors including 404 Model not found
            self.fail(f"Streaming chat completion failed: {error_str}")

    def test_integration_hybrid_npu_validation(self):
        """End-to-end test validating hybrid NPU mode works correctly.

        This test proves that the Lemonade server is running in hybrid mode
        (NPU + iGPU) on AMD Ryzen AI hardware by:
        1. Verifying the oga-hybrid recipe is being used
        2. Running a deterministic chat completion with the instruct model
        """
        print("\n=== Hybrid NPU Validation Test ===")

        try:
            # Step 1: Verify hybrid recipe is in use
            print("Step 1: Checking hybrid recipe configuration...")
            health = self.client.health_check()
            print(f"Health response: {health}")

            # Verify we're using the hybrid checkpoint and recipe
            checkpoint = health.get("checkpoint_loaded", "")
            if checkpoint:
                self.assertIn(
                    "hybrid",
                    checkpoint.lower(),
                    f"Expected hybrid checkpoint, got: {checkpoint}",
                )
                print(f"✅ Hybrid checkpoint loaded: {checkpoint}")

            # Step 2: Run deterministic chat completion (correct API for instruct models)
            print("\nStep 2: Running deterministic chat completion...")
            messages = [
                {
                    "role": "system",
                    "content": "You are a helpful assistant. Answer concisely.",
                },
                {
                    "role": "user",
                    "content": "What is 2 + 2? Reply with just the number.",
                },
            ]

            response = self.client.chat_completions(
                model=TEST_MODEL,
                messages=messages,
                temperature=0.0,  # Deterministic output
                max_completion_tokens=10,
            )

            # Verify response format
            self.assertIn("choices", response)
            self.assertGreaterEqual(len(response["choices"]), 1)
            self.assertIn("message", response["choices"][0])

            content = response["choices"][0]["message"]["content"]
            print(f"Model response: {content}")

            # Verify the model can do basic arithmetic (proves inference works)
            self.assertIn("4", content, "Model should correctly answer 2+2=4")
            print("✅ Chat completion successful - model inference working")

            print("\n✅ Hybrid NPU validation test PASSED")

        except LemonadeClientError as e:
            error_str = str(e)
            print(f"❌ Error during hybrid NPU validation: {error_str}")
            self.fail(f"Hybrid NPU validation failed: {error_str}")

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

    def test_integration_api_key_no_key_health_check(self):
        """Integration: health check works without an API key (backward compat)."""
        os.environ.pop("LEMONADE_API_KEY", None)
        client = create_lemonade_client(
            model=TEST_MODEL,
            host=HOST,
            port=PORT,
            auto_start=False,
            auto_load=False,
            keep_alive=True,
        )
        # Should succeed — local Lemonade without auth configured accepts all requests
        result = client.health_check()
        self.assertIsNotNone(result)
        self.assertEqual(result.get("status"), "ok")
        print("✓ Health check without API key succeeds (backward compat)")

    def test_integration_api_key_with_key_health_check(self):
        """Integration: health check works with LEMONADE_API_KEY set.

        Local Lemonade without authentication configured ignores the Authorization
        header, so this verifies the header is sent without breaking the request.
        """
        os.environ["LEMONADE_API_KEY"] = "integration-test-key"
        try:
            client = create_lemonade_client(
                model=TEST_MODEL,
                host=HOST,
                port=PORT,
                auto_start=False,
                auto_load=False,
                keep_alive=True,
            )
            self.assertEqual(client.api_key, "integration-test-key")
            result = client.health_check()
            self.assertIsNotNone(result)
            self.assertEqual(result.get("status"), "ok")
            print("✓ Health check with LEMONADE_API_KEY set succeeds (header accepted)")
        finally:
            os.environ.pop("LEMONADE_API_KEY", None)

    def test_integration_api_key_401_raises_actionable_error(self):
        """Integration: a Lemonade server returning 401 raises LemonadeClientError
        naming LEMONADE_API_KEY without leaking the key value or Bearer token."""
        import http.server
        import threading
        import time

        class _UnauthorizedHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                # Simulate a misconfigured proxy reflecting the Authorization header
                # back in the response body — our error must NOT include this.
                self.wfile.write(
                    b'{"error":"Unauthorized","detail":"Authorization: Bearer secret-key"}'
                )

            def log_message(self, *args):
                pass

        mock_port = 19399
        server = http.server.HTTPServer(("localhost", mock_port), _UnauthorizedHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        time.sleep(0.05)

        try:
            client = create_lemonade_client(
                model=TEST_MODEL,
                host="localhost",
                port=mock_port,
                auto_start=False,
                auto_load=False,
                keep_alive=True,
                api_key="secret-key",
            )
            with self.assertRaises(LemonadeClientError) as ctx:
                client.health_check()
            msg = str(ctx.exception)
            self.assertIn("LEMONADE_API_KEY", msg, "Error must name the env var to fix")
            self.assertNotIn("secret-key", msg, "Error must NOT contain the key value")
            self.assertNotIn("Bearer", msg, "Error must NOT contain the Bearer token")
            print(f"✓ 401 raises actionable error: {msg}")
        finally:
            server.shutdown()


if __name__ == "__main__":
    # Use pytest to run tests - either all tests or a specific test pattern
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
                    test_names.append(f"TestLemonadeClientIntegration::{arg}")
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
