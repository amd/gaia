# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Gateway Claude models route to the gateway's Anthropic route; nothing else moves.

The OpenAI-style gateway route Lemonade calls drops ``cache_control``, so
``amd.Claude-*`` chat goes through ``GatewayClaudeProvider`` instead. These
tests pin the routing decision, the exact outgoing request (URL, auth header,
model id, cache markers — checked on the real SDK's wire, not just a mock
call), cached-token accounting, the actionable error path, and that every
other model keeps the Lemonade path unchanged.
"""

import json
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

GATEWAY_TOKEN = "gw-unit-test-token"
ANTHROPIC_BASE = "https://llm-api.amd.com/Anthropic"


class _Omit:
    pass


def _fake_anthropic():
    mod = types.ModuleType("anthropic")

    class APIStatusError(Exception):
        def __init__(self, message="boom", status_code=500):
            super().__init__(message)
            self.message = message
            self.status_code = status_code

    class AuthenticationError(APIStatusError):
        pass

    class NotFoundError(APIStatusError):
        pass

    class RateLimitError(APIStatusError):
        pass

    class APIConnectionError(Exception):
        pass

    class Anthropic:
        last_init = None

        def __init__(self, **kwargs):
            Anthropic.last_init = kwargs
            self.messages = SimpleNamespace(create=MagicMock())

    mod.Anthropic = Anthropic
    mod.Omit = _Omit
    mod.APIStatusError = APIStatusError
    mod.AuthenticationError = AuthenticationError
    mod.NotFoundError = NotFoundError
    mod.RateLimitError = RateLimitError
    mod.APIConnectionError = APIConnectionError
    return mod


@pytest.fixture()
def gateway_env(monkeypatch, tmp_path):
    """A token in the environment, no real keyring, no real gateway.json."""
    from gaia.llm import gateway

    for var in (
        gateway.GATEWAY_ENV_VAR,
        "GAIA_GATEWAY_TOKEN",
        gateway.GATEWAY_ANTHROPIC_ENV_VAR,
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(gateway.GATEWAY_ENV_VAR, GATEWAY_TOKEN)
    monkeypatch.setattr(gateway, "GATEWAY_STATE_FILE", tmp_path / "gateway.json")
    monkeypatch.setattr(gateway, "recall_token", lambda: None)
    monkeypatch.setattr("gaia.llm.local_only.cloud_blocked", lambda: False)
    return gateway


@pytest.fixture()
def fake_sdk(monkeypatch, gateway_env):
    mod = _fake_anthropic()
    monkeypatch.setattr("gaia.llm.providers.claude.anthropic", mod)
    return mod


def _gateway_client(model="amd.Claude-Opus-5-5", **kwargs):
    from gaia.llm import create_client

    return create_client("lemonade", model=model, **kwargs)


def _response(text="ok", input_tokens=10, output_tokens=5, cache_read=0, write=0):
    usage = SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=write,
    )
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=usage,
    )


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]


# ── routing ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "model_id, upstream",
    [
        ("amd.Claude-Opus-5-5", "claude-opus-5-5"),
        ("amd.Claude-Sonnet-5", "claude-sonnet-5"),
        ("amd.claude-haiku-4-5", "claude-haiku-4-5"),
        ("amd.DeepSeek-V4.1-Flash", None),
        ("amd.Gemma-4-31B", None),
        ("claude-sonnet-5", None),
        ("fireworks.Claude-Opus-5", None),
        ("Gemma-4-E4B-it-GGUF", None),
        ("amd.", None),
        ("", None),
        (None, None),
    ],
)
def test_upstream_id_only_for_amd_claude(gateway_env, model_id, upstream):
    assert gateway_env.gateway_claude_upstream_id(model_id) == upstream


def test_gateway_claude_model_gets_the_gateway_provider(fake_sdk):
    from gaia.llm.providers.claude import GatewayClaudeProvider

    with patch("gaia.llm.providers.lemonade.LemonadeClient") as lemonade:
        client = _gateway_client(base_url="http://localhost:13305/api/v1")
    assert isinstance(client, GatewayClaudeProvider)
    lemonade.assert_not_called()


def test_default_provider_routes_gateway_claude_too(fake_sdk):
    """AgentSDK and ``/model`` call create_client with no provider name."""
    from gaia.llm import create_client
    from gaia.llm.providers.claude import GatewayClaudeProvider

    client = create_client(use_claude=False, model="amd.Claude-Sonnet-5")
    assert isinstance(client, GatewayClaudeProvider)


@pytest.mark.parametrize(
    "model", ["amd.DeepSeek-V4.1-Flash", "amd.Gemma-4-31B", "Gemma-4-E4B-it-GGUF"]
)
def test_non_claude_models_keep_the_lemonade_path(fake_sdk, model):
    from gaia.llm import create_client

    with patch("gaia.llm.providers.lemonade.LemonadeClient") as lemonade:
        client = create_client(
            "lemonade", model=model, base_url="http://localhost:13305/api/v1"
        )
    assert client.provider_name == "Lemonade"
    lemonade.assert_called_once_with(
        base_url="http://localhost:13305/api/v1", model=model
    )
    assert fake_sdk.Anthropic.last_init is None


def test_agent_sdk_on_deepseek_is_unchanged(fake_sdk):
    from gaia.chat.sdk import AgentConfig, AgentSDK
    from gaia.llm.providers.lemonade import LemonadeProvider

    with patch("gaia.llm.providers.lemonade.LemonadeClient"):
        sdk = AgentSDK(AgentConfig(model="amd.DeepSeek-V4.1-Flash"))
    assert type(sdk.llm_client) is LemonadeProvider
    assert sdk.effective_model == "amd.DeepSeek-V4.1-Flash"


def test_agent_sdk_on_gateway_claude_uses_the_gateway_provider(fake_sdk):
    from gaia.chat.sdk import AgentConfig, AgentSDK
    from gaia.llm.providers.claude import GatewayClaudeProvider

    sdk = AgentSDK(AgentConfig(model="amd.Claude-Opus-5-5"))
    assert isinstance(sdk.llm_client, GatewayClaudeProvider)


def test_explicit_claude_provider_still_goes_to_anthropic(fake_sdk, monkeypatch):
    """--use-claude is a different product path and must not be redirected."""
    from gaia.llm import create_client
    from gaia.llm.providers.claude import ClaudeProvider, GatewayClaudeProvider

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-unit-test")
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    client = create_client("claude", model="claude-sonnet-5")
    assert type(client) is ClaudeProvider
    assert not isinstance(client, GatewayClaudeProvider)
    assert "base_url" not in fake_sdk.Anthropic.last_init


# ── outgoing request ────────────────────────────────────────────────────


def test_client_targets_the_anthropic_route_with_the_apim_header(fake_sdk):
    _gateway_client(api_key="lemonade-api-key")
    init = fake_sdk.Anthropic.last_init
    assert init["base_url"] == ANTHROPIC_BASE
    assert init["default_headers"] == {"Ocp-Apim-Subscription-Key": GATEWAY_TOKEN}
    # Lemonade's own key is never mistaken for the gateway token.
    assert "lemonade-api-key" not in json.dumps(init, default=str)


def test_token_sources_in_order(fake_sdk, monkeypatch, gateway_env):
    monkeypatch.delenv(gateway_env.GATEWAY_ENV_VAR)
    monkeypatch.setenv("GAIA_GATEWAY_TOKEN", "from-gaia-env")
    _gateway_client()
    assert (
        fake_sdk.Anthropic.last_init["default_headers"]["Ocp-Apim-Subscription-Key"]
        == "from-gaia-env"
    )
    monkeypatch.delenv("GAIA_GATEWAY_TOKEN")
    monkeypatch.setattr(gateway_env, "recall_token", lambda: "from-keyring")
    _gateway_client()
    assert (
        fake_sdk.Anthropic.last_init["default_headers"]["Ocp-Apim-Subscription-Key"]
        == "from-keyring"
    )


def test_request_sends_upstream_model_and_cache_markers(fake_sdk):
    client = _gateway_client()
    client._client.messages.create.return_value = _response()
    client.chat(
        [
            {"role": "system", "content": "You are GAIA."},
            {"role": "user", "content": "hi"},
        ],
        model="amd.Claude-Opus-5-5",
        tools=TOOLS,
    )
    params = client._client.messages.create.call_args.kwargs
    assert params["model"] == "claude-opus-5-5"
    # No Anthropic credential header rides along with the APIM key.
    omitted = params["extra_headers"]
    assert set(omitted) == {"X-Api-Key", "Authorization"}
    assert all(isinstance(v, _Omit) for v in omitted.values())
    assert params["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert params["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert params["messages"][-1]["content"][-1]["cache_control"] == {
        "type": "ephemeral"
    }


def test_a_non_gateway_model_kwarg_keeps_the_client_model(fake_sdk):
    client = _gateway_client("amd.Claude-Sonnet-5")
    client._client.messages.create.return_value = _response()
    client.chat([{"role": "user", "content": "hi"}], model="Gemma-4-E4B-it-GGUF")
    assert client._client.messages.create.call_args.kwargs["model"] == (
        "claude-sonnet-5"
    )


def test_real_sdk_wire_request(gateway_env, monkeypatch):
    """The actual HTTP request the anthropic SDK builds, captured in-process."""
    anthropic = pytest.importorskip("anthropic")
    httpx = pytest.importorskip("httpx")
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5-5",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 1,
                    "cache_read_input_tokens": 16217,
                    "cache_creation_input_tokens": 0,
                },
            },
        )

    real = anthropic.Anthropic

    def with_mock_transport(**kwargs):
        return real(
            http_client=httpx.Client(transport=httpx.MockTransport(handler)), **kwargs
        )

    monkeypatch.setattr(
        "gaia.llm.providers.claude.anthropic.Anthropic", with_mock_transport
    )
    # Credentials an Anthropic user has set must not be forwarded.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "bearer-should-not-leak")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://wrong.example.com")

    client = _gateway_client()
    client.chat(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
        model="amd.Claude-Opus-5-5",
    )

    (request,) = seen
    assert str(request.url) == f"{ANTHROPIC_BASE}/v1/messages"
    assert request.headers["ocp-apim-subscription-key"] == GATEWAY_TOKEN
    assert "x-api-key" not in request.headers
    assert "authorization" not in request.headers
    body = json.loads(request.content)
    assert body["model"] == "claude-opus-5-5"
    assert body["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert client.get_performance_stats()["cached_tokens"] == 16217


# ── usage ───────────────────────────────────────────────────────────────


def test_cache_reads_reported_as_cached_tokens(fake_sdk):
    client = _gateway_client()
    client._client.messages.create.return_value = _response(
        input_tokens=40, output_tokens=7, cache_read=16217, write=0
    )
    client.chat([{"role": "user", "content": "hi"}])
    stats = client.get_performance_stats()
    assert stats["cached_tokens"] == 16217
    assert stats["prompt_tokens"] == 16257
    assert stats["completion_tokens"] == 7


def test_streamed_cache_reads_reported_as_cached_tokens(fake_sdk):
    client = _gateway_client()
    start_usage = SimpleNamespace(
        input_tokens=40,
        output_tokens=1,
        cache_read_input_tokens=9000,
        cache_creation_input_tokens=0,
    )
    events = [
        SimpleNamespace(
            type="message_start", message=SimpleNamespace(usage=start_usage)
        ),
        SimpleNamespace(
            type="content_block_start",
            index=0,
            content_block=SimpleNamespace(type="text", text=""),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="text_delta", text="hello"),
        ),
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="end_turn"),
            usage=SimpleNamespace(output_tokens=3),
        ),
    ]
    client._client.messages.create.return_value = iter(events)
    assert "".join(client.chat([{"role": "user", "content": "hi"}], stream=True)) == (
        "hello"
    )
    assert client.get_performance_stats()["cached_tokens"] == 9000


def test_agent_reply_cap_stays_at_the_cloud_size(fake_sdk):
    from gaia.agents.base.agent import CLOUD_MAX_OUTPUT_TOKENS, Agent

    client = _gateway_client()
    stub = SimpleNamespace(
        max_output_tokens=None,
        chat=SimpleNamespace(llm_client=client, effective_model="amd.Claude-Opus-5-5"),
    )
    assert Agent._max_output_tokens(stub) == CLOUD_MAX_OUTPUT_TOKENS


# ── failures are loud, never a quiet switch back to Lemonade ────────────


def test_missing_token_raises_and_never_falls_back(fake_sdk, monkeypatch, gateway_env):
    monkeypatch.delenv(gateway_env.GATEWAY_ENV_VAR)
    with patch("gaia.llm.providers.lemonade.LemonadeClient") as lemonade:
        with pytest.raises(gateway_env.GatewayError, match="gaia gateway auth"):
            _gateway_client()
    lemonade.assert_not_called()


def test_unknown_gateway_host_refuses_to_send_the_token(fake_sdk, gateway_env):
    gateway_env.GatewayState(base_url="https://gw.example.com/v1").save()
    with pytest.raises(gateway_env.GatewayError, match="GAIA_GATEWAY_ANTHROPIC"):
        _gateway_client()
    assert fake_sdk.Anthropic.last_init is None


def test_anthropic_base_override_is_honoured(fake_sdk, monkeypatch, gateway_env):
    gateway_env.GatewayState(base_url="https://gw.example.com/v1").save()
    monkeypatch.setenv(
        gateway_env.GATEWAY_ANTHROPIC_ENV_VAR, "https://gw.example.com/anthropic/"
    )
    _gateway_client()
    assert fake_sdk.Anthropic.last_init["base_url"] == (
        "https://gw.example.com/anthropic"
    )


def test_local_only_refuses_the_gateway(fake_sdk, monkeypatch):
    from gaia.llm.local_only import CloudDisabledError

    monkeypatch.setattr("gaia.llm.local_only.cloud_blocked", lambda: True)
    with pytest.raises(CloudDisabledError, match="AMD LLM Gateway"):
        _gateway_client()


@pytest.mark.parametrize(
    "status, message, expected",
    [
        (401, "Access denied due to invalid subscription key", "gaia gateway auth"),
        (403, "forbidden", "gaia gateway auth"),
        (
            400,
            'Deployment of "claude-nope-9" for "AnthropicVertexChatCompletions" '
            "is not found!",
            "gaia gateway models",
        ),
        (429, "slow down", "rate limiting"),
        (502, "bad gateway", "HTTP 502"),
    ],
)
def test_gateway_errors_are_actionable(fake_sdk, status, message, expected):
    client = _gateway_client()
    error = (
        fake_sdk.AuthenticationError(message, status)
        if status == 401
        else fake_sdk.APIStatusError(message, status)
    )
    client._client.messages.create.side_effect = error
    with patch("gaia.llm.providers.lemonade.LemonadeClient") as lemonade:
        with pytest.raises(RuntimeError, match=expected) as raised:
            client.chat([{"role": "user", "content": "hi"}])
    assert "llm-api.amd.com" in str(raised.value)
    assert GATEWAY_TOKEN not in str(raised.value)
    assert raised.value.__cause__ is error
    lemonade.assert_not_called()


def test_unreachable_gateway_names_the_vpn(fake_sdk):
    client = _gateway_client()
    client._client.messages.create.side_effect = fake_sdk.APIConnectionError("dns")
    with pytest.raises(RuntimeError, match="VPN"):
        client.chat([{"role": "user", "content": "hi"}])


def test_not_a_gateway_claude_model_is_rejected(fake_sdk):
    from gaia.llm.providers.claude import GatewayClaudeProvider

    with pytest.raises(ValueError, match="not an AMD gateway Claude model"):
        GatewayClaudeProvider(model="amd.DeepSeek-V4.1-Flash")


# ── gaia gateway test ───────────────────────────────────────────────────


def _run_gateway_test(model):
    from argparse import Namespace

    from gaia.cli import handle_gateway_command
    from gaia.llm.gateway import GatewayStatus

    manager = MagicMock()
    manager.status.return_value = GatewayStatus(
        installed=True, runtime_key_set=True, models_discovered=2
    )
    args = Namespace(
        gateway_action="test", prompt="ping", model=model, token=None, token_stdin=False
    )
    with patch("gaia.llm.gateway.GatewayManager", return_value=manager):
        handle_gateway_command(args)
    return manager


def test_gateway_test_exercises_the_anthropic_route_for_claude(fake_sdk, capsys):
    created = []
    real_init = fake_sdk.Anthropic.__init__

    def init(self, **kwargs):
        real_init(self, **kwargs)
        self.messages.create.return_value = _response("pong", cache_read=0)
        created.append(self)

    fake_sdk.Anthropic.__init__ = init
    manager = _run_gateway_test("amd.Claude-Opus-5-5")

    manager.client.chat_completions.assert_not_called()
    assert created[0].messages.create.call_args.kwargs["model"] == "claude-opus-5-5"
    out = capsys.readouterr().out
    assert "Anthropic route" in out
    assert "pong" in out
    assert "from cache" in out


def test_gateway_test_reports_a_claude_failure_and_exits(fake_sdk, capsys):
    real_init = fake_sdk.Anthropic.__init__

    def init(self, **kwargs):
        real_init(self, **kwargs)
        self.messages.create.side_effect = fake_sdk.AuthenticationError("denied", 401)

    fake_sdk.Anthropic.__init__ = init
    with pytest.raises(SystemExit) as exit_info:
        _run_gateway_test("amd.Claude-Opus-5-5")
    assert exit_info.value.code == 1
    err = capsys.readouterr().err
    assert "Testing amd.Claude-Opus-5-5 failed" in err
    assert "gaia gateway auth" in err


def test_gateway_test_keeps_lemonade_for_deepseek(fake_sdk, capsys):
    manager = _run_gateway_test("amd.DeepSeek-V4.1-Flash")
    manager.client.chat_completions.assert_called_once()
    assert fake_sdk.Anthropic.last_init is None


def test_cached_tokens_reach_the_agent_turn_total(fake_sdk):
    """What `gaia -p --output-format json` sums into its cached_tokens field."""
    from gaia.agents.base.agent import _sum_cached_tokens
    from gaia.chat.sdk import AgentConfig, AgentSDK

    sdk = AgentSDK(AgentConfig(model="amd.Claude-Opus-5-5", show_stats=True))
    sdk.llm_client._client.messages.create.return_value = _response(
        input_tokens=40, output_tokens=7, cache_read=16217
    )
    reply = sdk.send_messages(
        [{"role": "user", "content": "hi"}], system_prompt="You are GAIA."
    )
    conversation = [
        {
            "role": "system",
            "content": {"type": "stats", "performance_stats": reply.stats},
        }
    ]
    assert _sum_cached_tokens(conversation) == 16217


def test_plaintext_anthropic_base_override_is_refused(
    fake_sdk, monkeypatch, gateway_env
):
    monkeypatch.setenv(gateway_env.GATEWAY_ANTHROPIC_ENV_VAR, "http://gw.example.com/a")
    with pytest.raises(gateway_env.GatewayError, match="https://"):
        _gateway_client()
    assert fake_sdk.Anthropic.last_init is None


def test_local_only_wins_over_an_unknown_gateway_host(
    fake_sdk, monkeypatch, gateway_env
):
    from gaia.llm.local_only import CloudDisabledError

    gateway_env.GatewayState(base_url="https://gw.example.com/v1").save()
    monkeypatch.setattr("gaia.llm.local_only.cloud_blocked", lambda: True)
    with pytest.raises(CloudDisabledError):
        _gateway_client()
