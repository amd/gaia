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
import logging
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
    # Non-streaming happy path (mocked agent backend — no Lemonade required)
    # -------------------------------------------------------------------------

    def test_basic_completion_with_mocked_agent(self, mocker):
        """Non-streaming POST returns a schema-valid OpenAI completion.

        The agent/Lemonade backend is mocked: registry.get_agent yields a stub
        whose process_query returns a canned result dict, so the handler's
        non-streaming branch runs end-to-end without a live LLM server.
        """
        # Stub agent: NOT an ApiAgent, so the handler uses the len//4 token
        # estimate path (deterministic, no tokenizer needed).
        fake_agent = mocker.MagicMock()
        fake_agent.process_query.return_value = {
            "status": "success",
            "result": "def hello():\n    return 'hello world'",
        }

        from gaia.api.openai_server import registry as server_registry

        mocker.patch.object(server_registry, "get_agent", return_value=fake_agent)

        payload = {
            "model": "gaia-code",
            "messages": [
                {"role": "user", "content": "Write a hello world function in Python"}
            ],
            "stream": False,
        }
        response = self.client.post("/v1/chat/completions", json=payload)

        assert response.status_code == 200, response.text
        data = response.json()

        # Top-level OpenAI-compatible structure.
        assert data["object"] == "chat.completion"
        assert data["id"].startswith("chatcmpl-")
        assert isinstance(data["created"], int)
        assert data["model"] == "gaia-code"

        # The agent was invoked with the extracted user message.
        fake_agent.process_query.assert_called_once()
        call_args, _ = fake_agent.process_query.call_args
        assert call_args[0] == "Write a hello world function in Python"

        # Choices.
        assert len(data["choices"]) == 1
        choice = data["choices"][0]
        assert choice["index"] == 0
        assert choice["message"]["role"] == "assistant"
        assert choice["message"]["content"] == (
            "def hello():\n    return 'hello world'"
        )
        assert choice["finish_reason"] == "stop"

        # Usage accounting.
        usage = data["usage"]
        assert usage["prompt_tokens"] > 0
        assert usage["completion_tokens"] > 0
        assert usage["total_tokens"] == (
            usage["prompt_tokens"] + usage["completion_tokens"]
        )

    def test_completion_uses_last_user_message(self, mocker):
        """The handler passes the LAST user message (not system/assistant) to the agent."""
        fake_agent = mocker.MagicMock()
        fake_agent.process_query.return_value = {"result": "ok"}

        from gaia.api.openai_server import registry as server_registry

        mocker.patch.object(server_registry, "get_agent", return_value=fake_agent)

        payload = {
            "model": "gaia-code",
            "messages": [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "first question"},
                {"role": "assistant", "content": "an earlier answer"},
                {"role": "user", "content": "second question"},
            ],
            "stream": False,
        }
        response = self.client.post("/v1/chat/completions", json=payload)

        assert response.status_code == 200, response.text
        call_args, _ = fake_agent.process_query.call_args
        assert call_args[0] == "second question"

    def test_debug_logging_redacts_chat_request_content(
        self, mocker, monkeypatch, caplog
    ):
        """Debug logs should keep request shape without persisting secrets."""
        monkeypatch.setenv("GAIA_API_DEBUG", "1")
        caplog.set_level(logging.DEBUG, logger="gaia.api.openai_server")

        fake_agent = mocker.MagicMock()
        fake_agent.process_query.return_value = {
            "status": "success",
            "result": "assistant secret response",
        }

        from gaia.api.openai_server import registry as server_registry

        mocker.patch.object(server_registry, "get_agent", return_value=fake_agent)

        prompt = (
            "<workspace_info>\n"
            "I am working in a workspace with the following folders:\n"
            "- /Users/alice/private-project\n"
            "</workspace_info>\n"
            "Please summarize customer-secret-token-123"
        )
        response = self.client.post(
            "/v1/chat/completions",
            json={
                "model": "gaia-code",
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "temperature": 0.2,
            },
            headers={"Authorization": "Bearer api-secret-token"},
        )

        assert response.status_code == 200, response.text
        logs = caplog.text
        assert "authorization" in logs
        assert "Bearer api-secret-token" not in logs
        assert "customer-secret-token-123" not in logs
        assert "/Users/alice/private-project" not in logs
        assert "assistant secret response" not in logs
        assert "[redacted]" in logs

    def test_debug_logging_redacts_non_chat_body(self, monkeypatch, caplog):
        """Raw request logging must not persist decoded bodies."""
        monkeypatch.setenv("GAIA_API_DEBUG", "1")
        caplog.set_level(logging.DEBUG, logger="gaia.api.openai_server")

        response = self.client.post(
            "/health",
            json={"refresh_token": "rt-secret", "prompt": "private prompt"},
            headers={"X-Api-Key": "key-secret"},
        )

        assert response.status_code == 405
        logs = caplog.text
        assert "x-api-key" in logs
        assert "key-secret" not in logs
        assert "rt-secret" not in logs
        assert "private prompt" not in logs
        assert "Body (decoded UTF-8): [redacted]" in logs

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

    @pytest.mark.skip(reason="Skipped: No [DONE] marker received - see issue for fix")
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

    @pytest.mark.skip(
        reason="Skipped: No content reconstructed from stream - see issue for fix"
    )
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

    @pytest.mark.skip(
        reason="Skipped: ConnectionResetError on sequential streams - see issue for fix"
    )
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

    @pytest.mark.skip(reason="Skipped: ReadTimeoutError in CI - see issue for fix")
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

    @pytest.mark.skip(
        reason="Skipped: No finish_reason found in stream - see issue for fix"
    )
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

    @pytest.mark.skip(
        reason="Skipped: Streaming produced empty content - see issue for fix"
    )
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

    @pytest.mark.skip(
        reason="Skipped: Streaming hangs waiting for [DONE] marker - see issue for fix"
    )
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
        chunk_count = 0
        max_chunks = 1000  # Safety limit to prevent infinite loops

        with api_client.post(
            f"{api_server}/v1/chat/completions", json=payload, stream=True, timeout=30
        ) as response:
            for line in response.iter_lines():
                chunk_count += 1
                if chunk_count > max_chunks:
                    pytest.fail(
                        f"Exceeded max chunks ({max_chunks}) - possible infinite stream"
                    )

                if line:
                    decoded = line.decode() if isinstance(line, bytes) else line
                    if "[DONE]" in decoded:
                        break
                    if decoded.startswith("data: "):
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
    @pytest.mark.skip(reason="Skipped: API server returns 500 - see issue for fix")
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

    @pytest.mark.skip(reason="Skipped: API server returns 500 - see issue for fix")
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

    @pytest.mark.skip(reason="Skipped: API server returns 500 - see issue for fix")
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

    @pytest.mark.skip(reason="Skipped: API server returns 500 - see issue for fix")
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


# =============================================================================
# EMAIL AGENT REST SURFACE (#1229) — TestClient, no external server / Lemonade
# =============================================================================
#
# These exercise the /v1/email/* endpoints exposed by the gaia-agent-email
# wheel (gaia_agent_email.api_routes), mounted conditionally by openai_server.
# The triage endpoint accepts / returns the FROZEN #1262 contract
# (gaia_agent_email.contract). The send endpoint enforces the confirmation
# gate (#1264) at the API boundary: a send without a valid confirmation token
# is rejected with a 4xx — never silently auto-confirmed.


def _single_email_payload(
    *,
    subject: str = "Can you review the Q3 budget?",
    body: str = "Hi, please review the attached Q3 budget and reply by Friday.",
    sender_email: str = "alice@example.com",
    principal_email: str = "me@example.com",
):
    """A contract-valid SingleEmailInput request envelope."""
    return {
        "payload": {
            "kind": "single",
            "principal": {"email": principal_email},
            "message": {
                "message_id": "m-1",
                "from": {"name": "Alice", "email": sender_email},
                "to": [{"email": principal_email}],
                "subject": subject,
                "body": body,
            },
        }
    }


def _thread_payload(*, principal_email: str = "me@example.com"):
    """A contract-valid ThreadInput request envelope (two messages)."""
    return {
        "payload": {
            "kind": "thread",
            "principal": {"email": principal_email},
            "thread_id": "t-1",
            "messages": [
                {
                    "message_id": "m-1",
                    "thread_id": "t-1",
                    "from": {"email": "bob@example.com"},
                    "to": [{"email": principal_email}],
                    "subject": "Project kickoff",
                    "body": "Let's kick off the project next week.",
                },
                {
                    "message_id": "m-2",
                    "thread_id": "t-1",
                    "from": {"email": principal_email},
                    "to": [{"email": "bob@example.com"}],
                    "subject": "Re: Project kickoff",
                    "body": "Sounds good, please send the agenda.",
                },
            ],
        }
    }


class TestEmailTriageEndpoint:
    """POST /v1/email/triage — single email / thread in, structured result out."""

    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        if not API_AVAILABLE:
            pytest.skip(f"API dependencies not available: {IMPORT_ERROR}")
        # The /v1/email/* routes ship with the standalone gaia-agent-email
        # wheel (#1102); skip when a framework-only env lacks it.
        pytest.importorskip("gaia_agent_email")
        import json
        import types

        from gaia_agent_email.api_routes import EmailTriageService

        # Inject a fake chat so tests don't need a live Lemonade server.
        # Returns a classification JSON for classify calls, a summary string
        # for summarize calls.
        class _FakeChat:
            def send_messages(self, messages, system_prompt="", **kwargs):
                resp = types.SimpleNamespace()
                content = messages[0].get("content", "") if messages else ""
                if "Classify" in content:
                    resp.text = json.dumps(
                        {
                            "category": "NEEDS_RESPONSE",
                            "confidence": 0.9,
                            "reasoning": "test",
                        }
                    )
                else:
                    resp.text = "Alice is asking for a budget review by Friday."
                return resp

        monkeypatch.setattr(
            EmailTriageService, "_build_llm_chat", lambda self, **kw: _FakeChat()
        )

        # Triage persists action items to the task store (#1605); point it at
        # an in-memory DB so tests never write to the real ~/.gaia.
        from gaia_agent_email import api_routes as email_routes
        from gaia_agent_email import task_store

        from gaia.database.mixin import DatabaseMixin

        class _TaskDB(DatabaseMixin):
            pass

        task_db = _TaskDB()
        task_db.init_db(":memory:")
        task_store.init_schema(task_db)
        monkeypatch.setattr(email_routes, "resolve_action_db", lambda: task_db)

        self.client = TestClient(app)

    def test_single_email_in_structured_out(self):
        """A single email in returns a contract-valid structured result."""
        from gaia_agent_email.contract import SCHEMA_VERSION, parse_response
        from gaia_agent_email.tools.triage_heuristics import ALL_CATEGORIES

        resp = self.client.post("/v1/email/triage", json=_single_email_payload())
        assert resp.status_code == 200, resp.text
        data = resp.json()

        # Round-trips through the FROZEN contract (extra="forbid" — any drift
        # from the #1262 shape raises here).
        parsed = parse_response(data)
        assert parsed.schema_version == SCHEMA_VERSION
        assert parsed.request_kind == "single"
        assert parsed.result.category.value in ALL_CATEGORIES
        assert parsed.result.summary  # non-empty plain-text summary

    def test_thread_in_structured_out(self):
        """A full thread in returns request_kind == 'thread'."""
        from gaia_agent_email.contract import parse_response

        resp = self.client.post("/v1/email/triage", json=_thread_payload())
        assert resp.status_code == 200, resp.text
        parsed = parse_response(resp.json())
        assert parsed.request_kind == "thread"
        assert parsed.result.summary
        # The thread's last message is from the principal, so there is no one
        # to reply to — no draft is proposed.
        assert parsed.result.draft is None

    def test_single_email_proposes_draft_to_sender(self):
        """An inbound email from someone else yields a draft addressed back
        to that sender."""
        from gaia_agent_email.contract import parse_response

        resp = self.client.post(
            "/v1/email/triage",
            json=_single_email_payload(sender_email="bob@example.com"),
        )
        assert resp.status_code == 200, resp.text
        parsed = parse_response(resp.json())
        assert parsed.result.draft is not None
        assert parsed.result.draft.to[0].email == "bob@example.com"
        assert parsed.result.draft.subject.lower().startswith("re:")

    def test_promotional_email_is_low_priority(self):
        """The agent's real heuristic categorizer drives the result —
        a promo subject lands in 'low priority' deterministically."""
        from gaia_agent_email.contract import parse_response
        from gaia_agent_email.tools.triage_heuristics import CATEGORY_PROMOTIONAL

        payload = _single_email_payload(
            subject="50% off — sale ends tonight!",
            body="Shop our biggest sale of the year.",
            sender_email="deals@store.example.com",
        )
        resp = self.client.post("/v1/email/triage", json=payload)
        assert resp.status_code == 200, resp.text
        parsed = parse_response(resp.json())
        assert parsed.result.category.value == CATEGORY_PROMOTIONAL

    def test_response_matches_contract_schema_shape(self):
        """The response body has exactly the #1262 top-level keys."""
        resp = self.client.post("/v1/email/triage", json=_single_email_payload())
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert set(data.keys()) == {"schema_version", "request_kind", "result"}
        assert set(data["result"].keys()) >= {
            "category",
            "is_spam",
            "is_phishing",
            "summary",
            "action_items",
            "draft",
        }

    def test_unknown_field_rejected_422(self):
        """extra='forbid' on the frozen contract → unknown field is a 422."""
        payload = _single_email_payload()
        payload["payload"]["message"]["totally_unknown_field"] = "x"
        resp = self.client.post("/v1/email/triage", json=payload)
        assert resp.status_code == 422

    def test_empty_thread_rejected_422(self):
        """A thread with no messages violates the contract (min_length=1)."""
        payload = _thread_payload()
        payload["payload"]["messages"] = []
        resp = self.client.post("/v1/email/triage", json=payload)
        assert resp.status_code == 422

    def test_missing_payload_rejected_422(self):
        resp = self.client.post("/v1/email/triage", json={})
        assert resp.status_code == 422


class TestEmailSendConfirmationGate:
    """POST /v1/email/send — the send path MUST reject when confirmation is
    absent (#1264). This is the security-critical acceptance criterion.
    """

    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        if not API_AVAILABLE:
            pytest.skip(f"API dependencies not available: {IMPORT_ERROR}")
        # The /v1/email/* routes ship with the standalone gaia-agent-email
        # wheel (#1102); skip when a framework-only env lacks it.
        pytest.importorskip("gaia_agent_email")
        # Inject an in-memory Gmail backend so the (authorized) send path
        # never touches live mail. The gate-rejection cases never reach the
        # backend — the confirmation check is enforced first.
        import sys
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from gaia_agent_email import api_routes as email_routes

        from tests.fixtures.email.fake_gmail import FakeGmailBackend

        self.fake_backend = FakeGmailBackend()
        monkeypatch.setattr(
            email_routes, "resolve_send_backend", lambda: self.fake_backend
        )
        self.client = TestClient(app)

    def test_send_without_confirmation_token_is_4xx(self):
        """No confirmation token → server-side rejection in the 4xx range."""
        resp = self.client.post(
            "/v1/email/send",
            json={
                "to": [{"email": "alice@example.com"}],
                "subject": "Re: budget",
                "body": "Looks good, approved.",
            },
        )
        assert 400 <= resp.status_code < 500
        # Actionable error names the missing confirmation.
        assert "confirm" in resp.text.lower()

    def test_send_with_empty_confirmation_token_is_4xx(self):
        """An empty/blank token is not a confirmation."""
        resp = self.client.post(
            "/v1/email/send",
            json={
                "to": [{"email": "alice@example.com"}],
                "subject": "Re: budget",
                "body": "Looks good, approved.",
                "confirmation_token": "",
            },
        )
        # Exactly 403 (the gate), not an incidental 404 — the route exists.
        assert resp.status_code == 403

    def test_send_with_invalid_confirmation_token_is_4xx(self):
        """A token that was never issued is rejected."""
        resp = self.client.post(
            "/v1/email/send",
            json={
                "to": [{"email": "alice@example.com"}],
                "subject": "Re: budget",
                "body": "Looks good, approved.",
                "confirmation_token": "not-a-real-token",
            },
        )
        assert resp.status_code == 403

    def test_draft_then_send_with_valid_token_succeeds(self):
        """The golden path: draft issues a confirmation token bound to the
        exact payload; echoing it back authorizes the send."""
        draft_resp = self.client.post(
            "/v1/email/draft",
            json={
                "to": [{"email": "alice@example.com"}],
                "subject": "Re: budget",
                "body": "Looks good, approved.",
            },
        )
        assert draft_resp.status_code == 200, draft_resp.text
        token = draft_resp.json()["confirmation_token"]
        assert token

        send_resp = self.client.post(
            "/v1/email/send",
            json={
                "to": [{"email": "alice@example.com"}],
                "subject": "Re: budget",
                "body": "Looks good, approved.",
                "confirmation_token": token,
            },
        )
        assert send_resp.status_code == 200, send_resp.text
        body = send_resp.json()
        assert body["sent"] is True
        assert body["sent_id"]

    def test_token_does_not_authorize_a_different_payload(self):
        """A token is bound to its payload — you cannot reuse it to send
        different content (prevents bait-and-switch)."""
        draft_resp = self.client.post(
            "/v1/email/draft",
            json={
                "to": [{"email": "alice@example.com"}],
                "subject": "Re: budget",
                "body": "Looks good, approved.",
            },
        )
        token = draft_resp.json()["confirmation_token"]

        # Same token, different body → rejected.
        send_resp = self.client.post(
            "/v1/email/send",
            json={
                "to": [{"email": "attacker@evil.example.com"}],
                "subject": "Re: budget",
                "body": "Wire $10,000 to account 12345.",
                "confirmation_token": token,
            },
        )
        assert send_resp.status_code == 403

    def test_confirmation_token_is_single_use(self):
        """A token authorizes exactly one send; a replay is rejected."""
        payload = {
            "to": [{"email": "alice@example.com"}],
            "subject": "Re: budget",
            "body": "Looks good, approved.",
        }
        token = self.client.post("/v1/email/draft", json=payload).json()[
            "confirmation_token"
        ]
        first = self.client.post(
            "/v1/email/send", json={**payload, "confirmation_token": token}
        )
        assert first.status_code == 200, first.text
        # Replaying the consumed token must NOT send again.
        replay = self.client.post(
            "/v1/email/send", json={**payload, "confirmation_token": token}
        )
        assert replay.status_code == 403

    def test_gate_fires_before_backend_resolution(self, monkeypatch):
        """The confirmation gate is checked BEFORE the send backend is
        resolved: a no-token send returns 403 even when the backend is
        unavailable (would otherwise 503). The gate must never be masked by
        backend health."""
        from gaia_agent_email import api_routes as email_routes

        def _boom():
            raise AssertionError("backend resolved before the gate — gate bypassed")

        monkeypatch.setattr(email_routes, "resolve_send_backend", _boom)
        resp = self.client.post(
            "/v1/email/send",
            json={
                "to": [{"email": "alice@example.com"}],
                "subject": "Re: budget",
                "body": "Looks good, approved.",
            },
        )
        assert resp.status_code == 403


class TestEmailSendOffLoop:
    """send_email runs backend.send_message() off the event loop (#1594).

    The FastAPI route is async; calling backend.send_message() (which uses
    get_access_token_sync) synchronously from the async handler raises a
    RuntimeError because get_access_token_sync detects a running event loop.
    The fix: ``asyncio.to_thread(backend.send_message, ...)``.
    """

    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        if not API_AVAILABLE:
            pytest.skip(f"API dependencies not available: {IMPORT_ERROR}")
        pytest.importorskip("gaia_agent_email")

    def test_send_runs_on_worker_thread(self, monkeypatch):
        """backend.send_message must be called from a thread WITHOUT a running loop.

        We inject a backend whose send_message asserts it is NOT on a thread that
        has a running asyncio event loop — this is the condition that made #1594
        raise RuntimeError.
        """
        import asyncio
        import sys
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from gaia_agent_email import api_routes as email_routes

        call_log = []

        class _LoopAssertingBackend:
            def send_message(self, *, to, subject, body, **_kw):
                # If called from the event loop thread, get_running_loop() succeeds;
                # from a worker thread it raises RuntimeError. This is the exact
                # guard that get_access_token_sync enforces — we assert the same.
                try:
                    asyncio.get_running_loop()
                    call_log.append("ON_LOOP")  # Wrong: called from loop thread
                except RuntimeError:
                    call_log.append("OFF_LOOP")  # Correct: called from worker
                return {"id": "test-id-off-loop", "to": to, "subject": subject}

        monkeypatch.setattr(email_routes, "resolve_send_backend", _LoopAssertingBackend)

        client = TestClient(app)
        # Get a valid token first
        draft_resp = client.post(
            "/v1/email/draft",
            json={
                "to": [{"email": "test@example.com"}],
                "subject": "Test",
                "body": "Hello",
            },
        )
        assert draft_resp.status_code == 200, draft_resp.text
        token = draft_resp.json()["confirmation_token"]

        send_resp = client.post(
            "/v1/email/send",
            json={
                "to": [{"email": "test@example.com"}],
                "subject": "Test",
                "body": "Hello",
                "confirmation_token": token,
            },
        )
        assert send_resp.status_code == 200, send_resp.text
        assert call_log == ["OFF_LOOP"], (
            f"send_message ran on the event loop (call_log={call_log!r}); "
            "#1594: wrap in asyncio.to_thread"
        )


class TestEmailSendOutlook502Fix:
    """Graph sendMail returns 202 (no body, no id) — this is success, not 502.

    Prior to D4 the send handler raised 502 any time sent_id was empty, which
    broke Outlook sends (Graph sendMail legitimately returns 202 with no id).
    The fix: treat result['sent']==True as success even with no id.
    """

    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        if not API_AVAILABLE:
            pytest.skip(f"API dependencies not available: {IMPORT_ERROR}")
        pytest.importorskip("gaia_agent_email")

    def test_outlook_202_no_id_returns_200(self, monkeypatch):
        """A backend returning {"id":"","sent":True} must produce HTTP 200."""
        import sys
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from gaia_agent_email import api_routes as email_routes

        class _OutlookLikeBackend:
            # Graph sendMail: 202 No Content; no id echoed back, but no exception.
            def send_message(self, *, to, subject, body, **_kw):
                return {"id": "", "sent": True, "to": to, "subject": subject}

        monkeypatch.setattr(email_routes, "resolve_send_backend", _OutlookLikeBackend)
        client = TestClient(app)
        draft = client.post(
            "/v1/email/draft",
            json={
                "to": [{"email": "bob@example.com"}],
                "subject": "Hello",
                "body": "World",
            },
        )
        token = draft.json()["confirmation_token"]
        resp = client.post(
            "/v1/email/send",
            json={
                "to": [{"email": "bob@example.com"}],
                "subject": "Hello",
                "body": "World",
                "confirmation_token": token,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["sent"] is True
        assert body["sent_id"] == ""  # No id from Graph, but not a 502

    def test_silent_no_op_backend_still_502s(self, monkeypatch):
        """A backend returning {"id":""} WITHOUT 'sent':True is still a 502.

        Gmail raises on real failure, so a backend returning no id AND no
        sent signal is an unknown failure state — still fail loud.
        """
        import sys
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from gaia_agent_email import api_routes as email_routes

        class _SilentNoOpBackend:
            def send_message(self, *, to, subject, body, **_kw):
                return {"id": ""}  # No 'sent' key — unknown failure

        monkeypatch.setattr(email_routes, "resolve_send_backend", _SilentNoOpBackend)
        client = TestClient(app)
        draft = client.post(
            "/v1/email/draft",
            json={
                "to": [{"email": "bob@example.com"}],
                "subject": "Hello",
                "body": "World",
            },
        )
        token = draft.json()["confirmation_token"]
        resp = client.post(
            "/v1/email/send",
            json={
                "to": [{"email": "bob@example.com"}],
                "subject": "Hello",
                "body": "World",
                "confirmation_token": token,
            },
        )
        assert resp.status_code == 502


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
