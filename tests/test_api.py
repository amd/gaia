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

import asyncio
import logging
import threading
import time

import httpx
import pytest
import respx

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
            "model": "gaia",
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
        assert data["model"] == "gaia"

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
            "model": "gaia",
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

    def test_non_streaming_agent_does_not_block_health_checks(self, mocker):
        """A synchronous agent turn must yield the event loop to /health."""
        started = threading.Event()
        release = threading.Event()

        def process_query(_message):
            started.set()
            assert release.wait(timeout=5)
            return {"result": "ok"}

        fake_agent = mocker.MagicMock()
        fake_agent.process_query.side_effect = process_query

        from gaia.api import openai_server
        from gaia.api.openai_server import registry as server_registry
        from gaia.api.schemas import ChatCompletionRequest, ChatMessage

        mocker.patch.object(server_registry, "get_agent", return_value=fake_agent)
        mocker.patch.object(server_registry, "model_exists", return_value=True)
        health_started = mocker.Mock()

        async def health_probe():
            health_started()
            return {"status": "ok"}

        mocker.patch.object(openai_server, "_lemonade_health", health_probe)

        async def exercise_requests():
            completion = asyncio.create_task(
                openai_server.create_chat_completion(
                    ChatCompletionRequest(
                        model="gaia",
                        messages=[ChatMessage(role="user", content="slow request")],
                        stream=False,
                    )
                )
            )
            assert await asyncio.to_thread(started.wait, 2)

            health = asyncio.create_task(openai_server.health_check())
            await asyncio.wait_for(health, timeout=2)
            release.set()
            return await completion

        response = asyncio.run(exercise_requests())

        assert response.choices[0].message.content == "ok"
        health_started.assert_called_once_with()

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
                "model": "gaia",
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
            "/v1/chat/completions", json={"model": "gaia", "stream": False}
        )
        assert response.status_code == 422

    def test_invalid_message_role_returns_422(self):
        """Test that invalid message role returns 422 (Pydantic Literal validation)."""
        response = self.client.post(
            "/v1/chat/completions",
            json={
                "model": "gaia",
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
                "model": "gaia",
                "messages": [{"content": "test"}],
                "stream": False,
            },
        )
        assert response.status_code == 422

    def test_messages_not_array_returns_422(self):
        """Test that messages field that is not an array returns 422."""
        response = self.client.post(
            "/v1/chat/completions",
            json={"model": "gaia", "messages": "not an array", "stream": False},
        )
        assert response.status_code == 422

    def test_invalid_stream_value_returns_422(self):
        """Test that invalid stream field value returns 422."""
        response = self.client.post(
            "/v1/chat/completions",
            json={
                "model": "gaia",
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

    def test_empty_messages_returns_400(self, mocker):
        """Test that empty messages array returns 400 (no user message)."""
        from gaia.api.openai_server import registry as server_registry

        # The model 404 check runs first; stubbing it keeps a future rename of
        # the model from failing this test somewhere other than its own branch.
        mocker.patch.object(server_registry, "model_exists", return_value=True)

        response = self.client.post(
            "/v1/chat/completions",
            json={"model": "gaia", "messages": [], "stream": False},
        )
        assert response.status_code == 400
        assert "no user message" in response.json()["detail"].lower()

    def test_message_without_content_returns_400(self, mocker):
        """
        Test that message with None content returns 400.

        Content is Optional in schema (passes Pydantic), but server logic
        returns 400 because no user message content is found.
        """
        from gaia.api.openai_server import registry as server_registry

        mocker.patch.object(server_registry, "model_exists", return_value=True)

        response = self.client.post(
            "/v1/chat/completions",
            json={
                "model": "gaia",
                "messages": [{"role": "user"}],  # content defaults to None
                "stream": False,
            },
        )
        assert response.status_code == 400

    def test_only_system_message_returns_400(self, mocker):
        """Test that request with only system message returns 400."""
        from gaia.api.openai_server import registry as server_registry

        mocker.patch.object(server_registry, "model_exists", return_value=True)

        response = self.client.post(
            "/v1/chat/completions",
            json={
                "model": "gaia",
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
                "model": "gaia",
                "messages": [None, {"role": "user", "content": "test"}],
                "stream": False,
            },
        )
        assert response.status_code == 422

    # -------------------------------------------------------------------------
    # Endpoint Tests
    # -------------------------------------------------------------------------

    @respx.mock
    def test_health_endpoint_returns_component_status(self, monkeypatch):
        """Test that /health reports API, loaded LLM, and RAG status."""
        monkeypatch.setenv("LEMONADE_BASE_URL", "http://lemonade.test")
        respx.get("http://lemonade.test/api/v1/health").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "ok",
                    "all_models_loaded": [
                        {
                            "model_name": "Gemma-4-E4B-it-GGUF",
                            "type": "llm",
                        }
                    ],
                },
            )
        )

        response = self.client.get("/health")
        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "service": "gaia-api",
            "components": {
                "api": {"status": "ready"},
                "llm": {
                    "status": "ready",
                    "backend": "lemonade",
                    "model": "Gemma-4-E4B-it-GGUF",
                    "url": "http://lemonade.test/api/v1",
                },
                "rag": {"status": "not_configured"},
            },
        }

    @respx.mock
    def test_health_endpoint_degrades_when_no_llm_is_loaded(self, monkeypatch):
        """A running Lemonade catalog without a loaded LLM is not ready."""
        monkeypatch.setenv("LEMONADE_BASE_URL", "http://lemonade.test/api/v1")
        respx.get("http://lemonade.test/api/v1/health").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "ok",
                    "model_loaded": None,
                    "all_models_loaded": [
                        {
                            "model_name": "embeddinggemma-300m-GGUF",
                            "type": "embedding",
                        }
                    ],
                },
            )
        )

        response = self.client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"
        assert data["components"]["api"]["status"] == "ready"
        assert data["components"]["llm"]["status"] == "unavailable"
        assert data["components"]["llm"]["model"] is None

    @respx.mock
    def test_health_endpoint_degrades_when_lemonade_is_unreachable(self, monkeypatch):
        """Connection failures stay bounded and report an unavailable LLM."""
        monkeypatch.setenv("LEMONADE_BASE_URL", "http://lemonade.test")
        respx.get("http://lemonade.test/api/v1/health").mock(
            side_effect=httpx.ConnectError("connection refused")
        )

        started = time.monotonic()
        response = self.client.get("/health")
        elapsed = time.monotonic() - started

        assert response.status_code == 200
        assert response.json()["status"] == "degraded"
        assert response.json()["components"]["llm"]["status"] == "unavailable"
        assert elapsed < 0.5

    @respx.mock
    def test_health_endpoint_configures_bounded_lemonade_timeout(self, monkeypatch):
        """Lemonade health requests use the configured 350 ms timeout."""
        monkeypatch.setenv("LEMONADE_BASE_URL", "http://lemonade.test")

        def timeout(request):
            assert request.extensions["timeout"] == {
                "connect": 0.35,
                "read": 0.35,
                "write": 0.35,
                "pool": 0.35,
            }
            raise httpx.ConnectTimeout("request timed out", request=request)

        respx.get("http://lemonade.test/api/v1/health").mock(side_effect=timeout)

        response = self.client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "degraded"
        assert response.json()["components"]["llm"]["status"] == "unavailable"

    @respx.mock
    def test_health_endpoint_reports_lemonade_errors(self, monkeypatch):
        """An unhealthy Lemonade response is distinct from unavailability."""
        monkeypatch.setenv("LEMONADE_BASE_URL", "http://lemonade.test")
        respx.get("http://lemonade.test/api/v1/health").mock(
            return_value=httpx.Response(503, json={"status": "error"})
        )

        response = self.client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "degraded"
        assert response.json()["components"]["llm"]["status"] == "error"

    def test_models_endpoint_advertises_the_flagship(self):
        """/v1/models lists the flagship agent in OpenAI-compatible shape.

        This is what a client's model picker reads, so an empty list here means
        the picker is empty and nothing is selectable — the state this endpoint
        was in while the collapsed per-task agents were its only entries.
        """
        response = self.client.get("/v1/models")
        assert response.status_code == 200
        data = response.json()

        assert data["object"] == "list"
        ids = [m["id"] for m in data["data"]]
        assert "gaia" in ids, f"flagship not advertised; got {ids}"
        for model in data["data"]:
            assert model["object"] == "model"
            assert model["owned_by"]

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
            "/v1/chat/completions", json={"model": "gaia"}  # Missing messages
        )
        assert response.status_code == 422
        error_data = response.json()
        assert "detail" in error_data


class TestRealAgentCallShape:
    """Drive the endpoint with a REAL Agent subclass, not a MagicMock.

    Regression guard for the class of bug where the server passes a keyword
    argument no agent accepts. A ``MagicMock`` agent swallows any signature,
    so mock-based tests stay green while every real request raises
    ``TypeError``. These tests bind against the actual
    ``MemoryMixin.process_query`` -> ``Agent.process_query`` chain that the
    shipped agents use, so a bad call shape fails here.
    """

    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        if not API_AVAILABLE:
            pytest.skip(f"API dependencies not available: {IMPORT_ERROR}")
        # Keep the agent off the user's real ~/.gaia memory store.
        monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
        self.client = TestClient(app)

    def _real_agent(self, monkeypatch, captured):
        """A real Agent subclass with the shipped mixin chain, no LLM.

        Only ``_process_query_impl`` (the step loop, which needs a live model)
        is stubbed. Everything above it -- including the real
        ``process_query`` signatures the server actually calls -- is genuine,
        so an unexpected kwarg raises ``TypeError`` exactly as in production.
        """
        from gaia.agents.base.agent import Agent
        from gaia.agents.base.memory import MemoryMixin

        class _ProbeAgent(MemoryMixin, Agent):
            def _register_tools(self):
                pass

        def fake_impl(self, user_input, max_steps=None, trace=False, filename=None):
            captured["user_input"] = user_input
            captured["max_steps"] = max_steps
            return {"status": "success", "result": "probe answer"}

        monkeypatch.setattr(_ProbeAgent, "_process_query_impl", fake_impl)
        return _ProbeAgent(skip_lemonade=True, silent_mode=True)

    def test_non_streaming_accepts_real_agent_signature(self, monkeypatch, mocker):
        """POST /v1/chat/completions succeeds against a real Agent."""
        captured = {}
        agent = self._real_agent(monkeypatch, captured)

        from gaia.api.openai_server import registry as server_registry

        mocker.patch.object(server_registry, "get_agent", return_value=agent)

        response = self.client.post(
            "/v1/chat/completions",
            json={
                "model": "gaia",
                "messages": [{"role": "user", "content": "what is 2+2"}],
                "stream": False,
            },
        )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["choices"][0]["message"]["content"] == "probe answer"
        # The real agent received the user's message (MemoryMixin may prepend
        # dynamic context, so match on the tail).
        assert captured["user_input"].endswith("what is 2+2")

    def test_streaming_accepts_real_agent_signature(self, monkeypatch, mocker):
        """The streaming branch calls the agent with the same shape."""
        captured = {}
        agent = self._real_agent(monkeypatch, captured)

        from gaia.api.openai_server import registry as server_registry

        mocker.patch.object(server_registry, "get_agent", return_value=agent)

        with self.client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "gaia",
                "messages": [{"role": "user", "content": "what is 2+2"}],
                "stream": True,
            },
        ) as response:
            assert response.status_code == 200, response.text
            body = "".join(response.iter_text())

        assert "data: [DONE]" in body
        assert '"error"' not in body, body
        assert captured["user_input"].endswith("what is 2+2")

    def test_workspace_info_message_does_not_change_the_call(self, monkeypatch, mocker):
        """A Copilot <workspace_info> block is just message text now.

        The server used to parse it into a ``workspace_root`` kwarg that no
        agent accepted. Nothing consumes it, so it must not reappear as an
        extra argument.
        """
        captured = {}
        agent = self._real_agent(monkeypatch, captured)

        from gaia.api.openai_server import registry as server_registry

        mocker.patch.object(server_registry, "get_agent", return_value=agent)

        workspace_msg = (
            "<workspace_info>\n"
            "I am working in a workspace with the following folders:\n"
            "- /home/user/project\n"
            "</workspace_info>\n"
            "explain this repo"
        )
        response = self.client.post(
            "/v1/chat/completions",
            json={
                "model": "gaia",
                "messages": [{"role": "user", "content": workspace_msg}],
                "stream": False,
            },
        )

        assert response.status_code == 200, response.text
        assert response.json()["choices"][0]["message"]["content"] == "probe answer"


class TestApiCorsPolicy:
    """CORS must never allow wildcard origins together with credentials."""

    @pytest.fixture(autouse=True)
    def setup(self):
        if not API_AVAILABLE:
            pytest.skip(f"API dependencies not available: {IMPORT_ERROR}")
        self.client = TestClient(app)

    def _cors_kwargs(self):
        from fastapi.middleware.cors import CORSMiddleware

        return next(m.kwargs for m in app.user_middleware if m.cls is CORSMiddleware)

    def test_no_wildcard_origin_with_credentials(self):
        """The installed middleware must not combine '*' origins with credentials."""
        kwargs = self._cors_kwargs()
        if "*" in kwargs.get("allow_origins", []):
            assert kwargs.get("allow_credentials") is not True

    def test_default_origins_are_localhost_only(self):
        kwargs = self._cors_kwargs()
        assert "*" not in kwargs.get("allow_origins", [])
        assert kwargs.get("allow_origin_regex")

    def test_non_local_origin_is_not_allowed(self):
        """A random website must not receive CORS approval."""
        response = self.client.options(
            "/v1/models",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.headers.get("access-control-allow-origin") is None

    def test_localhost_origin_is_allowed(self):
        """Local browser clients (any port) keep working."""
        for origin in ("http://localhost:3000", "http://127.0.0.1:4200"):
            response = self.client.options(
                "/v1/models",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "GET",
                },
            )
            assert response.status_code == 200
            assert response.headers.get("access-control-allow-origin") == origin

    def test_env_var_adds_explicit_origin(self, monkeypatch):
        from gaia.api.local_http import cors_config as _cors_config

        monkeypatch.setenv(
            "GAIA_API_CORS_ORIGINS", "https://myapp.example.com, https://other.example"
        )
        cfg = _cors_config()
        assert cfg["allow_origins"] == [
            "https://myapp.example.com",
            "https://other.example",
        ]
        assert cfg["allow_credentials"] is True

    def test_blank_env_var_does_not_become_wildcard(self, monkeypatch):
        from gaia.api.local_http import cors_config as _cors_config

        monkeypatch.setenv("GAIA_API_CORS_ORIGINS", "")
        cfg = _cors_config()
        assert cfg["allow_origins"] == []
        assert cfg["allow_origin_regex"]

    def test_explicit_wildcard_disables_credentials(self, monkeypatch):
        from gaia.api.local_http import cors_config as _cors_config

        monkeypatch.setenv("GAIA_API_CORS_ORIGINS", "*")
        cfg = _cors_config()
        assert cfg["allow_origins"] == ["*"]
        assert cfg["allow_credentials"] is False


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
        payload = {"model": "gaia", "stream": False}
        response = api_client.post(f"{api_server}/v1/chat/completions", json=payload)
        assert response.status_code == 422  # FastAPI validation error


class TestHealthEndpoint:
    """Test health check endpoint"""

    def test_health_check_returns_components(self, api_server, api_client):
        """Test that /health returns the component-level contract."""
        response = api_client.get(f"{api_server}/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ("ok", "degraded")
        assert data["service"] == "gaia-api"
        assert data["components"]["api"] == {"status": "ready"}
        assert data["components"]["llm"]["status"] in (
            "ready",
            "unavailable",
            "error",
        )
        assert data["components"]["rag"] == {"status": "not_configured"}


# =============================================================================
# SSE STREAMING TESTS (require Lemonade for actual LLM responses)
# =============================================================================


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
            "model": "gaia",
            "stream": False,
        }
        response = api_client.post(f"{api_server}/v1/chat/completions", json=payload)
        assert response.status_code == 422

    def test_invalid_message_role(self, api_server, api_client):
        """Test message with invalid role - Pydantic validates Literal type"""
        payload = {
            "model": "gaia",
            "messages": [{"role": "invalid_role", "content": "test"}],
            "stream": False,
        }
        response = api_client.post(f"{api_server}/v1/chat/completions", json=payload)
        assert response.status_code == 422

    def test_message_without_role(self, api_server, api_client):
        """Test message missing role field - role is required in schema"""
        payload = {
            "model": "gaia",
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

    def test_invalid_stream_value(self, api_server, api_client):
        """Test invalid value for stream field - Pydantic validates boolean type"""
        payload = {
            "model": "gaia",
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

    def test_messages_not_array(self, api_server, api_client):
        """Test messages field that is not an array"""
        payload = {
            "model": "gaia",
            "messages": "not an array",
            "stream": False,
        }
        response = api_client.post(f"{api_server}/v1/chat/completions", json=payload)
        assert response.status_code == 422

    def test_messages_with_null_element(self, api_server, api_client):
        """Test messages array containing null - Pydantic validation error"""
        payload = {
            "model": "gaia",
            "messages": [None, {"role": "user", "content": "test"}],
            "stream": False,
        }
        response = api_client.post(f"{api_server}/v1/chat/completions", json=payload)
        assert response.status_code == 422


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
            "model": "gaia",
            # Missing messages field
        }
        response = api_client.post(f"{api_server}/v1/chat/completions", json=payload)
        assert response.status_code == 422

        error_data = response.json()
        # FastAPI validation errors have specific format
        assert "detail" in error_data


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
