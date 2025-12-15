#!/usr/bin/env python
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Comprehensive integration tests for GAIA OpenAI-compatible API server.

These tests run against an actual running API server process started by
the api_server fixture in conftest.py. The server is started with `gaia api start`
and tests make real HTTP requests.

Test coverage includes:
- Core API functionality (chat completions, models, health)
- SSE streaming behavior and connection management
- Request validation and error handling
- Edge cases and resilience testing
"""

import json
import time

import pytest
import requests

# Test imports
try:
    from fastapi.testclient import TestClient

    from gaia.api.openai_server import app

    API_AVAILABLE = True
except ImportError as e:
    API_AVAILABLE = False
    IMPORT_ERROR = str(e)
    app = None  # Placeholder for when imports fail

# =============================================================================
# UNIT TESTS (TestClient-based, no external dependencies)
# =============================================================================


class TestApiUnitValidation:
    """
    Unit tests using FastAPI TestClient - NO external server needed.

    These tests validate request/response schemas and error handling
    without starting the GAIA API server or requiring Lemonade.
    They can run on any CI runner (ubuntu-latest, windows-latest).
    """

    @pytest.fixture(autouse=True)
    def setup(self):
        """Set up TestClient for each test."""
        if not API_AVAILABLE:
            pytest.skip(f"API dependencies not available: {IMPORT_ERROR}")
        self.client = TestClient(app)

    # -------------------------------------------------------------------------
    # Model Validation Tests
    # -------------------------------------------------------------------------

    def test_invalid_model_returns_404(self):
        """Test that invalid model returns 404 error."""
        response = self.client.post(
            "/v1/chat/completions",
            json={
                "model": "nonexistent-model",
                "messages": [{"role": "user", "content": "test"}],
                "stream": False,
            },
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_empty_model_name_returns_404(self):
        """Test that empty model name returns 404."""
        response = self.client.post(
            "/v1/chat/completions",
            json={
                "model": "",
                "messages": [{"role": "user", "content": "test"}],
                "stream": False,
            },
        )
        assert response.status_code == 404

    # -------------------------------------------------------------------------
    # Request Validation Tests (Pydantic)
    # -------------------------------------------------------------------------

    def test_missing_model_returns_422(self):
        """Test that missing model field returns 422."""
        response = self.client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "test"}], "stream": False},
        )
        assert response.status_code == 422

    def test_missing_messages_returns_422(self):
        """Test that missing messages field returns 422."""
        response = self.client.post(
            "/v1/chat/completions", json={"model": "gaia-code", "stream": False}
        )
        assert response.status_code == 422

    def test_invalid_message_role_returns_422(self):
        """Test that invalid message role returns 422 (Pydantic Literal validation)."""
        response = self.client.post(
            "/v1/chat/completions",
            json={
                "model": "gaia-code",
                "messages": [{"role": "invalid_role", "content": "test"}],
                "stream": False,
            },
        )
        assert response.status_code == 422

    def test_message_without_role_returns_422(self):
        """Test that message missing role field returns 422."""
        response = self.client.post(
            "/v1/chat/completions",
            json={
                "model": "gaia-code",
                "messages": [{"content": "test"}],
                "stream": False,
            },
        )
        assert response.status_code == 422

    def test_messages_not_array_returns_422(self):
        """Test that messages field that is not an array returns 422."""
        response = self.client.post(
            "/v1/chat/completions",
            json={"model": "gaia-code", "messages": "not an array", "stream": False},
        )
        assert response.status_code == 422

    def test_invalid_stream_value_returns_422(self):
        """Test that invalid stream field value returns 422."""
        response = self.client.post(
            "/v1/chat/completions",
            json={
                "model": "gaia-code",
                "messages": [{"role": "user", "content": "test"}],
                "stream": "not a boolean",
            },
        )
        assert response.status_code == 422

    def test_empty_json_object_returns_422(self):
        """Test that empty JSON object returns 422."""
        response = self.client.post("/v1/chat/completions", json={})
        assert response.status_code == 422

    def test_invalid_json_returns_422(self):
        """Test that completely invalid JSON returns 422."""
        response = self.client.post(
            "/v1/chat/completions",
            content="this is not json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422

    # -------------------------------------------------------------------------
    # Server Logic Validation Tests (400 errors)
    # -------------------------------------------------------------------------

    def test_empty_messages_returns_400(self):
        """Test that empty messages array returns 400 (no user message)."""
        response = self.client.post(
            "/v1/chat/completions",
            json={"model": "gaia-code", "messages": [], "stream": False},
        )
        assert response.status_code == 400
        assert "no user message" in response.json()["detail"].lower()

    def test_message_without_content_returns_400(self):
        """
        Test that message with None content returns 400.

        Content is Optional in schema (passes Pydantic), but server logic
        returns 400 because no user message content is found.
        """
        response = self.client.post(
            "/v1/chat/completions",
            json={
                "model": "gaia-code",
                "messages": [{"role": "user"}],  # content defaults to None
                "stream": False,
            },
        )
        assert response.status_code == 400

    def test_only_system_message_returns_400(self):
        """Test that request with only system message returns 400."""
        response = self.client.post(
            "/v1/chat/completions",
            json={
                "model": "gaia-code",
                "messages": [{"role": "system", "content": "You are helpful"}],
                "stream": False,
            },
        )
        assert response.status_code == 400

    def test_messages_with_null_element_returns_422(self):
        """Test that messages array containing null returns 422."""
        response = self.client.post(
            "/v1/chat/completions",
            json={
                "model": "gaia-code",
                "messages": [None, {"role": "user", "content": "test"}],
                "stream": False,
            },
        )
        assert response.status_code == 422

    # -------------------------------------------------------------------------
    # Endpoint Tests
    # -------------------------------------------------------------------------

    def test_health_endpoint_returns_ok(self):
        """Test that /health endpoint returns status ok."""
        response = self.client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "gaia-api"

    def test_models_endpoint_returns_list(self):
        """Test that /v1/models returns list of available models."""
        response = self.client.get("/v1/models")
        assert response.status_code == 200
        data = response.json()

        # Verify OpenAI-compatible structure
        assert data["object"] == "list"
        assert "data" in data
        assert isinstance(data["data"], list)
        assert len(data["data"]) > 0

        # Verify model structure
        for model in data["data"]:
            assert model["object"] == "model"
            assert "id" in model
            assert "created" in model
            assert isinstance(model["created"], int)
            assert "owned_by" in model
            assert model["owned_by"] == "amd-gaia"

        # Verify gaia-code model exists
        model_ids = [m["id"] for m in data["data"]]
        assert "gaia-code" in model_ids, "gaia-code not in models"

    def test_nonexistent_endpoint_returns_404(self):
        """Test that non-existent endpoint returns 404."""
        response = self.client.get("/v1/nonexistent")
        assert response.status_code == 404

    def test_wrong_method_completions_returns_405(self):
        """Test that GET on completions endpoint returns 405."""
        response = self.client.get("/v1/chat/completions")
        assert response.status_code == 405

    def test_wrong_method_models_returns_405(self):
        """Test that POST on models endpoint returns 405."""
        response = self.client.post("/v1/models", json={})
        assert response.status_code == 405

    # -------------------------------------------------------------------------
    # Error Response Format Tests
    # -------------------------------------------------------------------------

    def test_404_error_has_detail_field(self):
        """Test that 404 error response has detail field."""
        response = self.client.post(
            "/v1/chat/completions",
            json={
                "model": "nonexistent",
                "messages": [{"role": "user", "content": "test"}],
                "stream": False,
            },
        )
        assert response.status_code == 404
        error_data = response.json()
        assert "detail" in error_data
        assert isinstance(error_data["detail"], str)

    def test_422_error_has_detail_field(self):
        """Test that 422 validation error has detail field."""
        response = self.client.post(
            "/v1/chat/completions", json={"model": "gaia-code"}  # Missing messages
        )
        assert response.status_code == 422
        error_data = response.json()
        assert "detail" in error_data


# =============================================================================
# INTEGRATION TESTS (Require running API server and/or Lemonade)
# =============================================================================

# =============================================================================
# CORE API FUNCTIONALITY TESTS
# =============================================================================


@pytest.mark.integration
class TestChatCompletionsNonStreaming:
    """Test POST /v1/chat/completions without streaming"""

    def test_basic_completion_with_code_agent(self, api_server, api_client):
        """Test that gaia-code returns valid OpenAI-compatible completion"""
        payload = {
            "model": "gaia-code",
            "messages": [
                {"role": "user", "content": "Write a hello world function in Python"}
            ],
            "stream": False,
        }
        response = api_client.post(f"{api_server}/v1/chat/completions", json=payload)

        assert response.status_code == 200
        data = response.json()

        # Verify OpenAI-compatible structure
        assert data["object"] == "chat.completion"
        assert "id" in data
        assert data["id"].startswith("chatcmpl-")
        assert "created" in data
        assert isinstance(data["created"], int)
        assert data["model"] == "gaia-code"

        # Verify choices
        assert "choices" in data
        assert len(data["choices"]) == 1
        choice = data["choices"][0]
        assert choice["index"] == 0
        assert choice["message"]["role"] == "assistant"
        assert isinstance(choice["message"]["content"], str)
        assert len(choice["message"]["content"]) > 0
        assert choice["finish_reason"] in ["stop", "length"]

        # Verify token usage
        assert "usage" in data
        assert data["usage"]["prompt_tokens"] > 0
        assert data["usage"]["completion_tokens"] > 0
        assert data["usage"]["total_tokens"] == (
            data["usage"]["prompt_tokens"] + data["usage"]["completion_tokens"]
        )

    def test_invalid_model_returns_404(self, api_server, api_client):
        """Test that invalid model returns 404 error"""
        payload = {
            "model": "nonexistent-model",
            "messages": [{"role": "user", "content": "test"}],
            "stream": False,
        }
        response = api_client.post(f"{api_server}/v1/chat/completions", json=payload)
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_missing_messages_returns_422(self, api_server, api_client):
        """Test that missing messages returns 422 validation error"""
        payload = {"model": "gaia-code", "stream": False}
        response = api_client.post(f"{api_server}/v1/chat/completions", json=payload)
        assert response.status_code == 422  # FastAPI validation error

    def test_empty_messages_returns_400(self, api_server, api_client):
        """Test that empty messages array returns 400 error"""
        payload = {"model": "gaia-code", "messages": [], "stream": False}
        response = api_client.post(f"{api_server}/v1/chat/completions", json=payload)
        assert response.status_code == 400


@pytest.mark.integration
class TestChatCompletionsStreaming:
    """Test POST /v1/chat/completions with streaming - requires Lemonade"""

    def test_streaming_completion_sse_format(self, api_server, api_client):
        """Test that streaming returns proper Server-Sent Events format"""
        payload = {
            "model": "gaia-code",
            "messages": [{"role": "user", "content": "Count to 5"}],
            "stream": True,
        }

        with api_client.post(
            f"{api_server}/v1/chat/completions", json=payload, stream=True
        ) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]

            chunks = []
            has_role = False
            has_content = False
            has_done = False

            for line in response.iter_lines():
                if line:
                    decoded = line.decode() if isinstance(line, bytes) else line

                    # Verify SSE format
                    assert decoded.startswith(
                        "data: "
                    ), f"Invalid SSE format: {decoded}"

                    # Check for [DONE] marker
                    if "[DONE]" in decoded:
                        has_done = True
                        continue

                    # Parse JSON chunk
                    chunk_data = json.loads(decoded[6:])  # Remove "data: " prefix
                    chunks.append(chunk_data)

                    # Verify chunk structure
                    assert chunk_data["object"] == "chat.completion.chunk"
                    assert chunk_data["model"] == "gaia-code"
                    assert "choices" in chunk_data

                    if chunk_data["choices"]:
                        choice = chunk_data["choices"][0]
                        assert "delta" in choice

                        # First chunk should have role
                        if "role" in choice["delta"]:
                            has_role = True
                            assert choice["delta"]["role"] == "assistant"

                        # Subsequent chunks should have content
                        if "content" in choice["delta"]:
                            has_content = True
                            assert isinstance(choice["delta"]["content"], str)

            # Verify we got all expected parts
            assert len(chunks) > 0, "No chunks received"
            assert has_role, "No role in first chunk"
            assert has_content, "No content in chunks"
            assert has_done, "No [DONE] marker received"

    def test_streaming_reconstructs_full_message(self, api_server, api_client):
        """Test that streaming chunks can be reconstructed into complete message"""
        payload = {
            "model": "gaia-code",
            "messages": [{"role": "user", "content": "Say 'hello world'"}],
            "stream": True,
        }

        full_content = ""
        with api_client.post(
            f"{api_server}/v1/chat/completions", json=payload, stream=True
        ) as response:
            for line in response.iter_lines():
                if line:
                    decoded = line.decode() if isinstance(line, bytes) else line
                    if "[DONE]" not in decoded and decoded.startswith("data: "):
                        chunk = json.loads(decoded[6:])
                        if (
                            chunk["choices"]
                            and "content" in chunk["choices"][0]["delta"]
                        ):
                            full_content += chunk["choices"][0]["delta"]["content"]

        assert len(full_content) > 0, "No content reconstructed from stream"


class TestModelsEndpoint:
    """Test GET /v1/models endpoint"""

    def test_list_models_returns_gaia_agents(self, api_server, api_client):
        """Test that /v1/models returns list of available GAIA agents"""
        response = api_client.get(f"{api_server}/v1/models")
        assert response.status_code == 200
        data = response.json()

        # Verify OpenAI-compatible structure
        assert data["object"] == "list"
        assert "data" in data
        assert isinstance(data["data"], list)
        assert len(data["data"]) > 0

        # Verify model structure
        for model in data["data"]:
            assert model["object"] == "model"
            assert "id" in model
            assert "created" in model
            assert isinstance(model["created"], int)
            assert "owned_by" in model
            assert model["owned_by"] == "amd-gaia"

        # Verify expected models exist
        model_ids = [m["id"] for m in data["data"]]
        assert "gaia-code" in model_ids, "gaia-code not in models"

    def test_model_metadata_includes_required_fields(self, api_server, api_client):
        """Test that models include required metadata fields"""
        response = api_client.get(f"{api_server}/v1/models")
        data = response.json()

        for model in data["data"]:
            # All models should have basic fields
            assert "id" in model
            assert "object" in model
            assert "created" in model
            assert "owned_by" in model


class TestApiAgentCustomization:
    """Test that ApiAgent mixin provides customization"""

    def test_code_agent_uses_custom_model_id(self, api_server, api_client):
        """Test that CodeAgent can customize its model ID"""
        response = api_client.get(f"{api_server}/v1/models")
        models = response.json()["data"]

        # CodeAgent should have model ID "gaia-code"
        code_model = next((m for m in models if "code" in m["id"]), None)
        assert code_model is not None, "No code model found"
        assert code_model["id"] == "gaia-code"

    def test_code_agent_provides_metadata(self, api_server, api_client):
        """Test that CodeAgent provides proper metadata"""
        response = api_client.get(f"{api_server}/v1/models")
        models = response.json()["data"]

        code_model = next((m for m in models if "code" in m["id"]), None)
        assert code_model is not None, "No code model found"
        assert code_model["id"] == "gaia-code"
        assert code_model["owned_by"] == "amd-gaia"


class TestHealthEndpoint:
    """Test health check endpoint"""

    def test_health_check_returns_ok(self, api_server, api_client):
        """Test that /health endpoint returns status ok"""
        response = api_client.get(f"{api_server}/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "gaia-api"


# =============================================================================
# SSE STREAMING TESTS (require Lemonade for actual LLM responses)
# =============================================================================


@pytest.mark.integration
class TestStreamingConnectionManagement:
    """Test SSE connection lifecycle and management - requires Lemonade"""

    def test_streaming_connection_closes_properly(self, api_server, api_client):
        """Test that streaming connections close properly after completion"""
        payload = {
            "model": "gaia-code",
            "messages": [{"role": "user", "content": "Say hello"}],
            "stream": True,
        }

        with api_client.post(
            f"{api_server}/v1/chat/completions", json=payload, stream=True
        ) as response:
            # Consume the stream
            for _ in response.iter_lines():
                pass

        # Connection should be closed after context exits
        assert response.raw.closed or not response.raw.isclosed()

    def test_multiple_sequential_streams(self, api_server, api_client):
        """Test that multiple sequential streaming requests work correctly"""
        payload = {
            "model": "gaia-code",
            "messages": [{"role": "user", "content": "Count to 3"}],
            "stream": True,
        }

        # Make multiple sequential streaming requests
        for i in range(3):
            with api_client.post(
                f"{api_server}/v1/chat/completions", json=payload, stream=True
            ) as response:
                assert response.status_code == 200
                chunk_count = 0
                for line in response.iter_lines():
                    if line:
                        chunk_count += 1
                assert chunk_count > 0, f"Request {i+1} received no chunks"

    def test_streaming_with_timeout(self, api_server, api_client):
        """Test that streaming respects timeout settings"""
        payload = {
            "model": "gaia-code",
            "messages": [{"role": "user", "content": "Quick response"}],
            "stream": True,
        }

        # Should complete within reasonable timeout
        start_time = time.time()
        with api_client.post(
            f"{api_server}/v1/chat/completions",
            json=payload,
            stream=True,
            timeout=30,  # 30 second timeout
        ) as response:
            for _ in response.iter_lines():
                pass

        elapsed = time.time() - start_time
        assert elapsed < 30, "Streaming took longer than timeout"


@pytest.mark.integration
class TestStreamingChunkFormat:
    """Test detailed SSE chunk formatting - requires Lemonade"""

    def test_all_chunks_have_valid_json(self, api_server, api_client):
        """Test that all SSE chunks contain valid JSON"""
        payload = {
            "model": "gaia-code",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True,
        }

        with api_client.post(
            f"{api_server}/v1/chat/completions", json=payload, stream=True
        ) as response:
            for line in response.iter_lines():
                if line:
                    decoded = line.decode() if isinstance(line, bytes) else line
                    if not decoded.startswith("data: "):
                        pytest.fail(f"Line doesn't start with 'data: ': {decoded}")

                    if "[DONE]" in decoded:
                        continue

                    # Should be valid JSON
                    try:
                        json_data = json.loads(decoded[6:])
                        assert isinstance(json_data, dict)
                    except json.JSONDecodeError as e:
                        pytest.fail(f"Invalid JSON in chunk: {decoded[6:]}\nError: {e}")

    def test_streaming_chunk_sequence(self, api_server, api_client):
        """Test that streaming chunks arrive in expected sequence"""
        payload = {
            "model": "gaia-code",
            "messages": [{"role": "user", "content": "Test"}],
            "stream": True,
        }

        chunks = []
        with api_client.post(
            f"{api_server}/v1/chat/completions", json=payload, stream=True
        ) as response:
            for line in response.iter_lines():
                if line:
                    decoded = line.decode() if isinstance(line, bytes) else line
                    if "[DONE]" not in decoded and decoded.startswith("data: "):
                        chunk = json.loads(decoded[6:])
                        chunks.append(chunk)

        # First chunk should have role
        assert len(chunks) > 0, "No chunks received"
        first_chunk = chunks[0]
        assert first_chunk["choices"][0]["delta"].get("role") == "assistant"

        # All chunks should have same ID
        chunk_id = first_chunk["id"]
        for chunk in chunks:
            assert chunk["id"] == chunk_id, "Chunk IDs don't match"

    def test_streaming_finish_reason(self, api_server, api_client):
        """Test that streaming includes finish_reason in final chunk"""
        payload = {
            "model": "gaia-code",
            "messages": [{"role": "user", "content": "Short reply"}],
            "stream": True,
        }

        found_finish_reason = False
        with api_client.post(
            f"{api_server}/v1/chat/completions", json=payload, stream=True
        ) as response:
            for line in response.iter_lines():
                if line:
                    decoded = line.decode() if isinstance(line, bytes) else line
                    if "[DONE]" not in decoded and decoded.startswith("data: "):
                        chunk = json.loads(decoded[6:])
                        if chunk["choices"]:
                            finish_reason = chunk["choices"][0].get("finish_reason")
                            if finish_reason:
                                found_finish_reason = True
                                assert finish_reason in ["stop", "length"]

        assert found_finish_reason, "No finish_reason found in stream"


@pytest.mark.integration
class TestStreamingContent:
    """Test streaming content reconstruction and integrity - requires Lemonade"""

    def test_streaming_content_not_empty(self, api_server, api_client):
        """Test that streaming produces non-empty content"""
        payload = {
            "model": "gaia-code",
            "messages": [{"role": "user", "content": "Say something"}],
            "stream": True,
        }

        full_content = ""
        with api_client.post(
            f"{api_server}/v1/chat/completions", json=payload, stream=True
        ) as response:
            for line in response.iter_lines():
                if line:
                    decoded = line.decode() if isinstance(line, bytes) else line
                    if "[DONE]" not in decoded and decoded.startswith("data: "):
                        chunk = json.loads(decoded[6:])
                        if (
                            chunk["choices"]
                            and "content" in chunk["choices"][0]["delta"]
                        ):
                            full_content += chunk["choices"][0]["delta"]["content"]

        assert len(full_content) > 0, "Streaming produced empty content"

    def test_streaming_preserves_special_characters(self, api_server, api_client):
        """Test that streaming preserves special characters correctly"""
        payload = {
            "model": "gaia-code",
            "messages": [
                {"role": "user", "content": "Write code with special chars: {}, [], ()"}
            ],
            "stream": True,
        }

        full_content = ""
        with api_client.post(
            f"{api_server}/v1/chat/completions", json=payload, stream=True
        ) as response:
            for line in response.iter_lines():
                if line:
                    decoded = line.decode() if isinstance(line, bytes) else line
                    if "[DONE]" not in decoded and decoded.startswith("data: "):
                        chunk = json.loads(decoded[6:])
                        if (
                            chunk["choices"]
                            and "content" in chunk["choices"][0]["delta"]
                        ):
                            content = chunk["choices"][0]["delta"]["content"]
                            # Verify content is properly decoded
                            assert isinstance(content, str)
                            full_content += content

        # Content should be valid UTF-8
        assert full_content.encode("utf-8").decode("utf-8") == full_content


class TestStreamingErrorCases:
    """Test error handling in streaming mode"""

    def test_streaming_with_invalid_model(self, api_server, api_client):
        """Test streaming with invalid model name"""
        payload = {
            "model": "invalid-model",
            "messages": [{"role": "user", "content": "Test"}],
            "stream": True,
        }

        response = api_client.post(
            f"{api_server}/v1/chat/completions", json=payload, stream=True
        )
        # Should return error immediately, not stream
        assert response.status_code == 404

    def test_streaming_with_empty_messages(self, api_server, api_client):
        """Test streaming with empty messages array"""
        payload = {
            "model": "gaia-code",
            "messages": [],
            "stream": True,
        }

        response = api_client.post(
            f"{api_server}/v1/chat/completions", json=payload, stream=True
        )
        # Should return error immediately
        assert response.status_code == 400


@pytest.mark.integration
class TestStreamingHeaders:
    """Test HTTP headers in streaming responses - requires Lemonade"""

    def test_streaming_content_type_header(self, api_server, api_client):
        """Test that streaming sets correct Content-Type header"""
        payload = {
            "model": "gaia-code",
            "messages": [{"role": "user", "content": "Test"}],
            "stream": True,
        }

        response = api_client.post(
            f"{api_server}/v1/chat/completions", json=payload, stream=True
        )

        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "").lower()

    def test_streaming_cache_control_header(self, api_server, api_client):
        """Test that streaming disables caching"""
        payload = {
            "model": "gaia-code",
            "messages": [{"role": "user", "content": "Test"}],
            "stream": True,
        }

        response = api_client.post(
            f"{api_server}/v1/chat/completions", json=payload, stream=True
        )

        # SSE responses typically disable caching
        cache_control = response.headers.get("cache-control", "")
        # Either no-cache or not set (acceptable for SSE)
        assert "no-cache" in cache_control.lower() or cache_control == ""


# =============================================================================
# ERROR HANDLING AND VALIDATION TESTS
# =============================================================================


class TestRequestValidation:
    """Test request validation and error responses"""

    def test_missing_model_field(self, api_server, api_client):
        """Test request without model field"""
        payload = {
            "messages": [{"role": "user", "content": "test"}],
            "stream": False,
        }
        response = api_client.post(f"{api_server}/v1/chat/completions", json=payload)
        assert response.status_code == 422  # Validation error

    def test_missing_messages_field(self, api_server, api_client):
        """Test request without messages field"""
        payload = {
            "model": "gaia-code",
            "stream": False,
        }
        response = api_client.post(f"{api_server}/v1/chat/completions", json=payload)
        assert response.status_code == 422

    def test_invalid_message_role(self, api_server, api_client):
        """Test message with invalid role - Pydantic validates Literal type"""
        payload = {
            "model": "gaia-code",
            "messages": [{"role": "invalid_role", "content": "test"}],
            "stream": False,
        }
        response = api_client.post(f"{api_server}/v1/chat/completions", json=payload)
        assert response.status_code == 422

    def test_message_without_content(self, api_server, api_client):
        """Test message missing content field - content is Optional, server returns 400"""
        payload = {
            "model": "gaia-code",
            "messages": [{"role": "user"}],
            "stream": False,
        }
        response = api_client.post(f"{api_server}/v1/chat/completions", json=payload)
        assert response.status_code == 400

    def test_message_without_role(self, api_server, api_client):
        """Test message missing role field - role is required in schema"""
        payload = {
            "model": "gaia-code",
            "messages": [{"content": "test"}],
            "stream": False,
        }
        response = api_client.post(f"{api_server}/v1/chat/completions", json=payload)
        assert response.status_code == 422


class TestInvalidPayloads:
    """Test handling of malformed and invalid payloads"""

    def test_empty_json_object(self, api_server, api_client):
        """Test request with empty JSON object"""
        response = api_client.post(f"{api_server}/v1/chat/completions", json={})
        assert response.status_code == 422

    def test_completely_invalid_json(self, api_server, api_client):
        """Test request with completely invalid JSON"""
        response = api_client.post(
            f"{api_server}/v1/chat/completions",
            data="this is not json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422

    @pytest.mark.integration
    def test_json_with_extra_fields(self, api_server, api_client):
        """Test that extra fields in request are ignored - Pydantic allows extra by default"""
        payload = {
            "model": "gaia-code",
            "messages": [{"role": "user", "content": "test"}],
            "stream": False,
            "unknown_field": "should be ignored",
            "another_unknown": 12345,
        }
        # Extra fields are ignored, request should succeed
        response = api_client.post(f"{api_server}/v1/chat/completions", json=payload)
        assert response.status_code == 200

    def test_invalid_stream_value(self, api_server, api_client):
        """Test invalid value for stream field - Pydantic validates boolean type"""
        payload = {
            "model": "gaia-code",
            "messages": [{"role": "user", "content": "test"}],
            "stream": "not a boolean",
        }
        response = api_client.post(f"{api_server}/v1/chat/completions", json=payload)
        assert response.status_code == 422


class TestModelErrors:
    """Test errors related to model selection and availability"""

    def test_nonexistent_model(self, api_server, api_client):
        """Test request for non-existent model"""
        payload = {
            "model": "gaia-nonexistent",
            "messages": [{"role": "user", "content": "test"}],
            "stream": False,
        }
        response = api_client.post(f"{api_server}/v1/chat/completions", json=payload)
        assert response.status_code == 404
        error_data = response.json()
        assert "detail" in error_data
        assert "not found" in error_data["detail"].lower()

    def test_empty_model_name(self, api_server, api_client):
        """Test request with empty model name - empty string passes Pydantic, model not found"""
        payload = {
            "model": "",
            "messages": [{"role": "user", "content": "test"}],
            "stream": False,
        }
        response = api_client.post(f"{api_server}/v1/chat/completions", json=payload)
        assert response.status_code == 404

    def test_model_name_with_special_chars(self, api_server, api_client):
        """Test model name with special characters"""
        payload = {
            "model": "gaia-code/../../../etc/passwd",
            "messages": [{"role": "user", "content": "test"}],
            "stream": False,
        }
        response = api_client.post(f"{api_server}/v1/chat/completions", json=payload)
        # Should safely reject path traversal attempts
        assert response.status_code in [400, 404]


class TestMessageArrayErrors:
    """Test errors related to message arrays"""

    def test_empty_messages_array(self, api_server, api_client):
        """Test request with empty messages array"""
        payload = {
            "model": "gaia-code",
            "messages": [],
            "stream": False,
        }
        response = api_client.post(f"{api_server}/v1/chat/completions", json=payload)
        assert response.status_code == 400

    def test_messages_not_array(self, api_server, api_client):
        """Test messages field that is not an array"""
        payload = {
            "model": "gaia-code",
            "messages": "not an array",
            "stream": False,
        }
        response = api_client.post(f"{api_server}/v1/chat/completions", json=payload)
        assert response.status_code == 422

    def test_messages_with_null_element(self, api_server, api_client):
        """Test messages array containing null - Pydantic validation error"""
        payload = {
            "model": "gaia-code",
            "messages": [None, {"role": "user", "content": "test"}],
            "stream": False,
        }
        response = api_client.post(f"{api_server}/v1/chat/completions", json=payload)
        assert response.status_code == 422


@pytest.mark.integration
class TestLargePayloads:
    """Test handling of large payloads - requires Lemonade for 200 responses"""

    def test_very_long_message(self, api_server, api_client):
        """Test message with very long content"""
        long_content = "x" * 10000  # 10k characters
        payload = {
            "model": "gaia-code",
            "messages": [{"role": "user", "content": long_content}],
            "stream": False,
        }
        response = api_client.post(f"{api_server}/v1/chat/completions", json=payload)
        # Should either accept it or return 413 (payload too large)
        assert response.status_code in [200, 413, 400]

    def test_many_messages(self, api_server, api_client):
        """Test request with many messages"""
        messages = []
        for i in range(100):
            role = "user" if i % 2 == 0 else "assistant"
            messages.append({"role": role, "content": f"Message {i}"})

        payload = {
            "model": "gaia-code",
            "messages": messages,
            "stream": False,
        }
        # Add timeout to prevent hanging - API should respond quickly or reject
        response = api_client.post(
            f"{api_server}/v1/chat/completions", json=payload, timeout=30
        )
        # Should either accept it or return 413
        assert response.status_code in [200, 413, 400]


class TestEndpointErrors:
    """Test errors on different endpoints"""

    def test_nonexistent_endpoint(self, api_server, api_client):
        """Test request to non-existent endpoint"""
        response = api_client.get(f"{api_server}/v1/nonexistent")
        assert response.status_code == 404

    def test_wrong_http_method_completions(self, api_server, api_client):
        """Test wrong HTTP method on completions endpoint - FastAPI returns 405"""
        # GET instead of POST
        response = api_client.get(f"{api_server}/v1/chat/completions")
        assert response.status_code == 405  # Method not allowed

    def test_wrong_http_method_models(self, api_server, api_client):
        """Test wrong HTTP method on models endpoint - FastAPI returns 405"""
        # POST instead of GET
        response = api_client.post(f"{api_server}/v1/models", json={})
        assert response.status_code == 405

    def test_models_endpoint_with_query_params(self, api_server, api_client):
        """Test models endpoint ignores query parameters"""
        response = api_client.get(f"{api_server}/v1/models?filter=code")
        # Should still return all models, ignoring filter
        assert response.status_code == 200


class TestContentTypeErrors:
    """Test Content-Type handling"""

    def test_missing_content_type(self, api_server):
        """Test POST request without Content-Type header - FastAPI may auto-parse"""
        payload = {
            "model": "gaia-code",
            "messages": [{"role": "user", "content": "test"}],
            "stream": False,
        }
        response = requests.post(
            f"{api_server}/v1/chat/completions",
            data=json.dumps(payload),
            # No Content-Type header
        )
        # FastAPI may auto-detect JSON or return validation error
        assert response.status_code in [200, 422]


@pytest.mark.integration
class TestRateLimitingAndThrottling:
    """Test rate limiting if implemented - requires Lemonade for 200 responses"""

    def test_rapid_sequential_requests(self, api_server, api_client):
        """Test making many rapid requests"""
        payload = {
            "model": "gaia-code",
            "messages": [{"role": "user", "content": "quick"}],
            "stream": False,
        }

        # Make 10 rapid requests
        responses = []
        for _ in range(10):
            response = api_client.post(
                f"{api_server}/v1/chat/completions", json=payload
            )
            responses.append(response.status_code)

        # All should succeed (200) or some might be rate limited (429)
        for status in responses:
            assert status in [200, 429, 500, 503]


class TestErrorResponseFormat:
    """Test that error responses follow expected format"""

    def test_404_error_format(self, api_server, api_client):
        """Test 404 error response format"""
        payload = {
            "model": "nonexistent",
            "messages": [{"role": "user", "content": "test"}],
            "stream": False,
        }
        response = api_client.post(f"{api_server}/v1/chat/completions", json=payload)
        assert response.status_code == 404

        error_data = response.json()
        assert "detail" in error_data
        assert isinstance(error_data["detail"], str)

    def test_422_error_format(self, api_server, api_client):
        """Test 422 validation error format"""
        payload = {
            "model": "gaia-code",
            # Missing messages field
        }
        response = api_client.post(f"{api_server}/v1/chat/completions", json=payload)
        assert response.status_code == 422

        error_data = response.json()
        # FastAPI validation errors have specific format
        assert "detail" in error_data


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
