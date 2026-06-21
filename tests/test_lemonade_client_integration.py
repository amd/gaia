# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Real-server integration tests for the Lemonade client.

These exercise every Lemonade Server capability GAIA actually depends on
against a *running* Lemonade server. They are gated by the ``require_lemonade``
fixture (tests/conftest.py) so the whole suite SKIPS cleanly when no server is
listening on localhost:13305 — it never hard-fails on a missing backend.

Why real (not mock) tests: mocks prove "we called it", not "the call is valid".
The canonical bug they cannot catch is #1655 — the model-pull sent ``recipe=``
for a *built-in* Lemonade model, which Lemonade 400s. Every mock passed; only a
real round-trip against the server caught it. ``TestLemonadePullContract`` locks
that contract down.

Models used (already pulled/loaded in CI, see test_lemonade_client.yml):
  - LLM / tool-calling: ``Qwen3-0.6B-GGUF`` (small, fast)
  - Embeddings smoke:   ``nomic-embed-text-v2-moe-GGUF`` (768-dim)

Embedding coverage lives in tests/test_lemonade_embeddings.py; this file only
adds a minimal non-redundant smoke check.
"""

import pytest

from gaia.llm.lemonade_client import (
    DEFAULT_REQUEST_TIMEOUT,
    LemonadeClient,
    LemonadeClientError,
    LemonadeStatus,
    is_tool_calling_model,
    lemonade_auth_headers,
)

# Small tool-calling LLM that CI pulls + loads. Keeping it tiny keeps these
# tests fast while still exercising the real chat/completions/tools paths.
LLM_MODEL = "Qwen3-0.6B-GGUF"
EMBED_MODEL = "nomic-embed-text-v2-moe-GGUF"


class TestLemonadeServerStatus:
    """Health, readiness, status, and context-size introspection."""

    @pytest.fixture
    def client(self, require_lemonade):
        return LemonadeClient(verbose=False)

    def test_health_check_returns_ok(self, client):
        health = client.health_check()
        assert isinstance(health, dict), "health_check should return a dict"
        assert health.get("status") == "ok", f"server not healthy: {health}"
        # Lemonade reports its version in /health; GAIA's status/version
        # checks rely on this field being present.
        assert "version" in health, "health response missing 'version' field"

    def test_ready_true_when_server_up(self, client):
        assert client.ready() is True, "ready() should be True with a healthy server"

    def test_get_status_real_fields(self, client):
        status = client.get_status()
        assert isinstance(status, LemonadeStatus)
        assert status.running is True, "status.running should be True"
        assert status.error is None, f"status.error should be None, got {status.error}"
        assert status.version, "status.version should be populated from /health"
        # loaded_models is enriched health data; each entry must expose the
        # backward-compat keys GAIA consumers read.
        assert isinstance(status.loaded_models, list)
        for entry in status.loaded_models:
            assert "id" in entry and "model_name" in entry
            assert "recipe_options" in entry

    def test_validate_context_size_returns_tuple(self, client):
        success, error = client.validate_context_size(required_tokens=2048)
        assert isinstance(success, bool)
        # On success error must be None; on failure it must be an actionable string.
        if success:
            assert error is None
        else:
            assert isinstance(error, str) and error


class TestLemonadeSystemInfo:
    """System/device enumeration and stats endpoints."""

    @pytest.fixture
    def client(self, require_lemonade):
        return LemonadeClient(verbose=False)

    def test_get_system_info_devices(self, client):
        info = client.get_system_info()
        assert isinstance(info, dict)
        # GAIA's hardware/device selection reads the 'devices' map.
        assert "devices" in info, f"system-info missing 'devices': {list(info)}"
        assert isinstance(info["devices"], dict)

    def test_get_system_info_verbose(self, client):
        info = client.get_system_info(verbose=True)
        assert isinstance(info, dict)
        assert "devices" in info

    def test_get_stats_returns_dict(self, client):
        # /stats reflects the last request's perf counters; shape, not content,
        # is what GAIA depends on.
        stats = client.get_stats()
        assert isinstance(stats, dict)


class TestLemonadeCatalog:
    """Model catalog: list/details/availability/loaded checks."""

    @pytest.fixture
    def client(self, require_lemonade):
        return LemonadeClient(verbose=False)

    def test_list_models_shape(self, client):
        models = client.list_models()
        assert isinstance(models, dict)
        assert "data" in models, "list_models response must contain 'data'"
        assert isinstance(models["data"], list)
        # Every entry is an OpenAI-style model object with an id.
        for m in models["data"]:
            assert "id" in m

    def test_list_models_show_all_has_labels(self, client):
        catalog = client.list_models(show_all=True)
        assert isinstance(catalog, dict)
        data = catalog.get("data", [])
        assert data, "show_all catalog should not be empty"
        # show_all adds discovery fields GAIA's downloader relies on.
        sample = data[0]
        assert "id" in sample
        assert "downloaded" in sample, "show_all entries must expose 'downloaded'"

    def test_get_model_details_known_model(self, client):
        details = client.get_model_details(LLM_MODEL)
        assert isinstance(details, dict)
        assert details.get("id") == LLM_MODEL
        # GAIA reads checkpoint/recipe from model details.
        assert "checkpoint" in details
        assert "recipe" in details

    def test_get_model_details_unknown_raises(self, client):
        # Unknown model => 404 => LemonadeClientError (fail loudly, no fallback).
        with pytest.raises(LemonadeClientError):
            client.get_model_details("definitely-not-a-real-model-xyz-123")

    def test_check_model_available_true(self, client):
        # CI pulls LLM_MODEL, so it must report as downloaded/available.
        assert client.check_model_available(LLM_MODEL) is True

    def test_check_model_available_false_for_unknown(self, client):
        assert client.check_model_available("not-a-real-model-xyz-123") is False


class TestLemonadePullContract:
    """The #1655 contract: pulling a BUILT-IN model must not send ``recipe=``.

    #1655: GAIA's model-pull sent ``recipe=`` for a built-in Lemonade model,
    which Lemonade 400s on a fresh pull. Every mock passed because the HTTP
    layer was stubbed. These tests capture the *outgoing* request shape against
    the real server and assert the built-in path omits ``recipe``.
    """

    @pytest.fixture
    def client(self, require_lemonade):
        return LemonadeClient(verbose=False)

    def test_pull_builtin_model_omits_recipe_and_succeeds(self, client):
        captured = {}
        original_send = client._send_request

        def spy(method, url, data=None, timeout=DEFAULT_REQUEST_TIMEOUT):
            if url.endswith("/pull"):
                captured["data"] = data
            return original_send(method, url, data, timeout=timeout)

        client._send_request = spy

        # LLM_MODEL is a built-in already cached in CI, so this is fast.
        result = client.pull_model(LLM_MODEL, timeout=600)

        assert isinstance(result, dict), "pull of a built-in model should succeed"
        assert "data" in captured, "spy did not observe the /pull request"
        sent = captured["data"]
        # The crux of #1655: a built-in pull carries only the model name —
        # no recipe/checkpoint, which is what triggered the 400.
        assert sent == {"model_name": LLM_MODEL}, f"unexpected pull payload: {sent}"
        assert "recipe" not in sent
        assert "checkpoint" not in sent

    def test_ensure_model_downloaded_idempotent(self, client):
        # Already-present built-in => returns True without re-downloading.
        assert client.ensure_model_downloaded(LLM_MODEL, show_progress=False) is True


class TestLemonadeLoadUnload:
    """load_model (explicit ctx), _ensure_model_loaded, and unload variants.

    Ordered so the destructive unload tests run last; the chat tests in other
    classes call auto_download=True and reload the model as needed.
    """

    @pytest.fixture
    def client(self, require_lemonade):
        return LemonadeClient(verbose=False)

    def test_load_model_with_ctx_size(self, client):
        result = client.load_model(LLM_MODEL, ctx_size=4096, prompt=False)
        assert isinstance(result, dict)
        assert client.check_model_loaded(LLM_MODEL) is True

    def test_ensure_model_loaded_loads_model(self, client):
        client._ensure_model_loaded(LLM_MODEL, auto_download=True)
        # After _ensure_model_loaded the model must be resident.
        assert client.check_model_loaded(LLM_MODEL) is True

    def test_unload_ignore_if_not_loaded_is_noop(self, client):
        # Scoped unload of a model that isn't loaded must be a silent no-op,
        # not a raise (RAG's embedder-refresh path relies on this).
        result = client.unload_model(
            "not-a-loaded-model-xyz-123", ignore_if_not_loaded=True
        )
        assert isinstance(result, dict)
        assert result.get("status") == "not_loaded"

    def test_unload_scoped_then_global(self, client):
        # Ensure something is loaded, then scoped-unload it by name.
        client.load_model(LLM_MODEL, ctx_size=4096, prompt=False)
        scoped = client.unload_model(LLM_MODEL)
        assert isinstance(scoped, dict)

        # Global unload (no model_name) clears all and resets client.model.
        global_unload = client.unload_model()
        assert isinstance(global_unload, dict)
        assert client.model is None


class TestLemonadeChatCompletions:
    """Non-streaming and streaming chat completions against a real model."""

    @pytest.fixture
    def client(self, require_lemonade):
        return LemonadeClient(verbose=False)

    def test_chat_completions_non_streaming(self, client):
        response = client.chat_completions(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": "Reply with the single word: pong"}],
            max_completion_tokens=64,
            temperature=0.0,
        )
        assert isinstance(response, dict)
        assert response.get("choices"), f"no choices in response: {response}"
        message = response["choices"][0]["message"]
        assert message.get("role") == "assistant"
        # Qwen3-0.6B is a reasoning model: short generations land in
        # reasoning_content with content still empty. Either proves a real
        # round-trip produced text.
        produced = (message.get("content") or "") + (
            message.get("reasoning_content") or ""
        )
        assert produced.strip(), f"no text produced: {message}"

    def test_chat_completions_streaming(self, client):
        chunks = list(
            client.chat_completions(
                model=LLM_MODEL,
                messages=[{"role": "user", "content": "Count: 1 2 3"}],
                max_completion_tokens=64,
                temperature=0.0,
                stream=True,
            )
        )
        assert chunks, "streaming returned no chunks"
        for chunk in chunks:
            assert chunk.get("object") == "chat.completion.chunk"
            assert "choices" in chunk
        # Aggregate both content and reasoning_content deltas — a reasoning
        # model streams its thoughts via reasoning_content.
        produced = "".join(
            (c["choices"][0]["delta"].get("content") or "")
            + (c["choices"][0]["delta"].get("reasoning_content") or "")
            for c in chunks
            if c.get("choices")
        )
        assert produced.strip(), "streamed deltas produced no text content"


class TestLemonadeCompletions:
    """Non-streaming and streaming text completions."""

    @pytest.fixture
    def client(self, require_lemonade):
        return LemonadeClient(verbose=False)

    def test_completions_non_streaming(self, client):
        response = client.completions(
            model=LLM_MODEL,
            prompt="The opposite of hot is",
            max_tokens=16,
            temperature=0.0,
        )
        assert isinstance(response, dict)
        assert response.get("choices"), f"no choices in response: {response}"
        assert isinstance(response["choices"][0].get("text"), str)

    def test_completions_streaming(self, client):
        chunks = list(
            client.completions(
                model=LLM_MODEL,
                prompt="List three colors:",
                max_tokens=16,
                temperature=0.0,
                stream=True,
            )
        )
        assert chunks, "streaming completions returned no chunks"
        for chunk in chunks:
            assert "choices" in chunk


class TestLemonadeToolCalling:
    """Real tools=[...] round-trip + the is_tool_calling_model mapping."""

    @pytest.fixture
    def client(self, require_lemonade):
        return LemonadeClient(verbose=False)

    def test_is_tool_calling_model_mapping(self, client):
        # Known GGUF LLMs support native tool calls via --jinja.
        assert is_tool_calling_model(LLM_MODEL) is True
        assert is_tool_calling_model("Gemma-4-E4B-it-GGUF") is True
        # The NPU/FLM build 500s on a tools payload — explicitly False.
        assert is_tool_calling_model("gemma4-it-e2b-FLM") is False
        # Embedding models don't tool-call.
        assert is_tool_calling_model(EMBED_MODEL) is False
        # Unknown GGUF defaults optimistic; None is False.
        assert is_tool_calling_model("some-unknown-gguf") is True
        assert is_tool_calling_model(None) is False

    def test_chat_completions_with_tools_accepted(self, client):
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get the current weather for a city",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {
                                "type": "string",
                                "description": "City name, e.g. Paris",
                            }
                        },
                        "required": ["city"],
                    },
                },
            }
        ]
        response = client.chat_completions(
            model=LLM_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": "What is the weather in Paris? Use the get_weather tool.",
                }
            ],
            tools=tools,
            max_completion_tokens=128,
            temperature=0.0,
        )
        # The server must accept a tools payload and return a valid shape.
        assert isinstance(response, dict)
        assert response.get("choices"), f"no choices: {response}"
        choice = response["choices"][0]
        message = choice.get("message", {})
        tool_calls = message.get("tool_calls")
        if tool_calls:
            # If the model chose to call the tool, validate the call shape.
            call = tool_calls[0]
            assert call.get("type") == "function"
            assert call["function"]["name"] == "get_weather"
            assert "arguments" in call["function"]
        else:
            # Otherwise the request was still accepted and produced content.
            assert isinstance(message.get("content"), str)


class TestLemonadeEmbeddingsSmoke:
    """Minimal embeddings smoke check (full coverage: test_lemonade_embeddings.py)."""

    @pytest.fixture
    def client(self, require_lemonade):
        return LemonadeClient(verbose=False)

    def test_embeddings_768_dim(self, client):
        response = client.embeddings("hello world", model=EMBED_MODEL, timeout=120)
        assert isinstance(response, dict)
        assert response.get("data"), f"no embedding data: {response}"
        vector = response["data"][0]["embedding"]
        assert len(vector) == 768, "nomic embeddings should be 768-dimensional"


class TestLemonadeErrorClassification:
    """Fail-loudly error paths: invalid models, deletes, auth headers."""

    @pytest.fixture
    def client(self, require_lemonade):
        return LemonadeClient(verbose=False)

    def test_invalid_model_chat_raises(self, client):
        # An unregistered model must surface a loud error, not silently fall back.
        with pytest.raises(LemonadeClientError):
            client.chat_completions(
                model="totally-not-registered-model-xyz-123",
                messages=[{"role": "user", "content": "hi"}],
                max_completion_tokens=8,
                auto_download=False,
            )

    def test_delete_nonexistent_model_raises(self, client):
        # NON-destructive: deleting a model that doesn't exist must error,
        # and we never touch a model CI depends on.
        with pytest.raises(LemonadeClientError):
            client.delete_model("definitely-not-a-real-model-xyz-123")

    def test_auth_header_sent_when_key_configured(self, require_lemonade):
        # The Bearer header is built from the configured key; CI's server is
        # unauthenticated so it tolerates the header (a real 401 test needs an
        # authenticated server, which CI does not run).
        keyed = LemonadeClient(verbose=False, api_key="test-key-123")
        headers = keyed._auth_headers()
        assert headers == {"Authorization": "Bearer test-key-123"}
        # Requests still succeed against an unauthenticated server.
        assert keyed.ready() is True

    def test_auth_headers_empty_when_no_key(self, require_lemonade):
        # Gated on require_lemonade so the whole file skips uniformly with no
        # server (this assertion itself is a pure-function contract check).
        assert lemonade_auth_headers(None) == {}
        assert lemonade_auth_headers("") == {}
        assert lemonade_auth_headers("abc") == {"Authorization": "Bearer abc"}
