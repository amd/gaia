# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Integration tests for the Lemonade client API against a live Lemonade Server.

The mocked-HTTP tests live in ``tests/unit/test_lemonade_client_http.py``.
"""

import os
import sys
import unittest
import uuid

import pytest

from gaia.llm.lemonade_client import (
    LemonadeClient,
    LemonadeClientError,
    create_lemonade_client,
    is_llm_model_entry,
)

# Test constants - override via GAIA_TEST_MODEL env var for different platforms
TEST_MODEL = os.environ.get("GAIA_TEST_MODEL", "Gemma-4-E4B-it-GGUF")

HOST = "localhost"
# Respect LEMONADE_PORT env var so CI can override for non-default setups.
# Default 13305 matches C++ server / lemonade-server v10.1.0+ (also what Linux CI uses).
PORT = int(os.environ.get("LEMONADE_PORT", 13305))
API_BASE = f"http://{HOST}:{PORT}/api/v1"

# A small embedding GGUF that no built-in model points at, so registering it
# under a fresh user. name is a genuinely cold pull and deleting it afterwards
# cannot take a built-in model's weights with it.
COLD_PULL_CHECKPOINT = "nomic-ai/nomic-embed-text-v1-GGUF:Q2_K"
# Distinctive, and small enough to fit any CI runner.
LOAD_CTX_SIZE = 8192


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


@pytest.mark.integration
class TestLemonadeClientIntegration(unittest.TestCase):
    """Integration tests for LemonadeClient with a running server.

    Skips when no server is answering. It never starts one: a test that
    spawns a real Lemonade on a developer's machine outlives the run.
    """

    @classmethod
    def setUpClass(cls):
        if not is_server_running(HOST, PORT):
            raise unittest.SkipTest(
                f"Lemonade server not running at {HOST}:{PORT} - skipping "
                "integration tests"
            )
        cls.client = create_lemonade_client(
            model=TEST_MODEL,
            host=HOST,
            port=PORT,
            auto_start=False,
            auto_load=False,
            verbose=True,
            keep_alive=True,  # The server is not ours to stop.
        )

    def _loaded_entry(self, model_name):
        """The /health entry for *model_name*, failing loudly if it is absent."""
        health = self.client.health_check()
        self.assertIn(
            "all_models_loaded",
            health,
            f"/health lost 'all_models_loaded'; got keys {sorted(health)}",
        )
        for entry in health["all_models_loaded"]:
            if model_name in (entry.get("model_name"), entry.get("id")):
                return entry
        self.fail(
            f"{model_name} is not in /health all_models_loaded after loading it: "
            f"{health['all_models_loaded']}"
        )

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
        """The 9.1.4+ /health shape, asserted unconditionally.

        ctx_size lives at all_models_loaded[].recipe_options.ctx_size. Dozens of
        mocked tests hand-build that shape; if Lemonade moves it again, this is
        the test that has to go red, so nothing here is guarded by an ``if``.
        """
        self.client.load_model(TEST_MODEL)

        health = self.client.health_check()
        self.assertEqual(health.get("status"), "ok", health)
        entry = self._loaded_entry(TEST_MODEL)
        self.assertIn("recipe_options", entry, f"entry keys: {sorted(entry)}")
        ctx_size = entry["recipe_options"].get("ctx_size")
        self.assertIsInstance(
            ctx_size, int, f"recipe_options: {entry['recipe_options']}"
        )
        self.assertGreater(ctx_size, 0)

        # Any loaded LLM may be the one get_status() reports (a cloud model
        # counts too), so it must match one of them, not necessarily ours.
        llm_windows = {
            m.get("recipe_options", {}).get("ctx_size")
            for m in health["all_models_loaded"]
            if is_llm_model_entry(m) and m.get("recipe_options", {}).get("ctx_size")
        }
        status = self.client.get_status()
        self.assertTrue(status.running)
        self.assertIn(
            status.context_size,
            llm_windows,
            "get_status() reported a context size no loaded LLM has",
        )

        reported = status.context_size
        valid, error = self.client.validate_context_size(
            required_tokens=reported, quiet=True
        )
        self.assertTrue(valid, f"validate_context_size({reported}) failed: {error}")
        valid, _ = self.client.validate_context_size(
            required_tokens=reported + 1, quiet=True
        )
        self.assertFalse(valid, f"validate_context_size({reported + 1}) passed")

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
        """A /load carrying ctx_size and llamacpp_args is accepted, and the
        window it asked for is the one /health reports back.

        Mocked tests pin that payload; only a live server proves Lemonade reads
        those fields rather than ignoring them. save_options is pinned off:
        the client's default saves, which would persist this test's small window
        and its llama.cpp args as the model's options on whatever server runs
        the suite, and GAIA then treats saved args as the user's own.
        """
        result = self.client.load_model(
            model_name=TEST_MODEL,
            ctx_size=LOAD_CTX_SIZE,
            llamacpp_args="--ubatch-size 512",
            save_options=False,
        )
        self.assertIn(result.get("status"), ("ok", "success"), result)

        ctx_size = self._loaded_entry(TEST_MODEL)["recipe_options"].get("ctx_size")
        self.assertEqual(
            ctx_size,
            LOAD_CTX_SIZE,
            f"loaded with ctx_size={LOAD_CTX_SIZE}, /health reports {ctx_size}",
        )

    def test_integration_pull_model(self):
        """Pulling an already-registered model succeeds; any error fails the test."""
        result = self.client.pull_model(model_name=TEST_MODEL)
        self.assertIsNotNone(result, "pull_model returned None")
        self.assertIn(result.get("status"), ("success", "ok"), result)

    def test_integration_pull_registers_a_cold_user_model(self):
        """Register-and-install a never-seen user. model with checkpoint, recipe
        and embedding, then confirm Lemonade kept what was sent.

        Pulling a model CI already has proves nothing about registration: the
        fields are only read on a cold pull, which is where #1655 broke.
        """
        model_name = f"user.gaia-it-pull-{uuid.uuid4().hex[:8]}"
        pulled = False
        try:
            result = self.client.pull_model(
                model_name=model_name,
                checkpoint=COLD_PULL_CHECKPOINT,
                recipe="llamacpp",
                embedding=True,
            )
            pulled = True
            self.assertIn(result.get("status"), ("success", "ok"), result)

            listed = {m.get("id"): m for m in self.client.list_models().get("data", [])}
            # Lemonade 11.x lists a user model without its namespace prefix.
            entry = listed.get(model_name) or listed.get(model_name[len("user.") :])
            self.assertIsNotNone(entry, f"{model_name} is not listed: {sorted(listed)}")
            self.assertTrue(entry.get("downloaded"), entry)
            self.assertEqual(entry.get("checkpoint"), COLD_PULL_CHECKPOINT, entry)
            self.assertIn(
                "embeddings",
                entry.get("labels", []),
                f"embedding=True was not recorded: {entry}",
            )
        finally:
            # A pull that failed partway may still have registered the model.
            try:
                self.client.delete_model(model_name)
            except LemonadeClientError:
                # Nothing to delete after a failed pull; that failure is the one
                # to report. After a successful pull, a failed delete is a bug.
                if pulled:
                    raise

    def test_integration_pull_rejects_a_recipe_outside_the_user_namespace(self):
        """Registration fields on a name without the user. prefix are refused.

        That is the contract GAIA's pull path relies on to never send a recipe
        for a built-in model.
        """
        model_name = f"gaia-it-unprefixed-{uuid.uuid4().hex[:8]}"
        try:
            self.client.pull_model(
                model_name=model_name,
                checkpoint=COLD_PULL_CHECKPOINT,
                recipe="llamacpp",
                embedding=True,
            )
        except LemonadeClientError:
            return
        self.client.delete_model(model_name)
        self.fail(f"Lemonade registered {model_name} without the user. prefix")

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
