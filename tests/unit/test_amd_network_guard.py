# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""On AMD's network Claude goes through the AMD LLM Gateway, never to Anthropic.

Covers the detection rule, the client guard (including the ways a request could
slip past it: an env base URL, a lookalike host, plain http, an alias model id,
the async client), the Claude Code CLI guard, the eval judge's explicit gateway
route — checked on the real SDK's wire — and the hybrid model/template lists.
No test reaches the network: the probe and the gateway setup are faked.
"""

import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from gaia.llm import amd_network
from gaia.llm.amd_network import DirectAnthropicBlockedError

_REAL_CONFIGURED = amd_network._gateway_configured
_REAL_ANSWERS = amd_network._gateway_answers
GATEWAY_TOKEN = "gw-unit-test-token"
GATEWAY_BASE = "https://llm-api.amd.com/Anthropic"
SRC = Path(__file__).resolve().parents[2] / "src"
HUB = Path(__file__).resolve().parents[2] / "hub"


@pytest.fixture
def no_gateway_env(monkeypatch, tmp_path):
    """No gateway key anywhere, and a gateway.json path that doesn't exist."""
    from gaia.llm import gateway

    for var in (gateway.GATEWAY_ENV_VAR, "GAIA_GATEWAY_TOKEN", "ANTHROPIC_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(gateway, "GATEWAY_STATE_FILE", tmp_path / "gateway.json")
    monkeypatch.setattr(gateway, "read_stored_token", lambda: None)
    monkeypatch.setattr(amd_network, "_gateway_configured", _REAL_CONFIGURED)
    return gateway


@pytest.fixture
def on_network(monkeypatch, no_gateway_env):
    """On AMD's network with the gateway key in GAIA's environment."""
    monkeypatch.setenv(no_gateway_env.GATEWAY_ENV_VAR, GATEWAY_TOKEN)
    monkeypatch.setattr("gaia.llm.local_only.cloud_blocked", lambda: False)
    amd_network.forget()
    assert amd_network.on_amd_network()
    return no_gateway_env


def _probe(monkeypatch, answers):
    calls = []

    def fake(*_a, **_k):
        calls.append(1)
        return answers

    monkeypatch.setattr(amd_network, "_gateway_answers", fake)
    return calls


# ── Detection ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("var", ["LEMONADE_AMD_API_KEY", "GAIA_GATEWAY_TOKEN"])
def test_a_gateway_key_in_the_environment_is_decisive_without_probing(
    monkeypatch, no_gateway_env, var
):
    monkeypatch.setenv(var, "key")
    calls = _probe(monkeypatch, False)
    verdict = amd_network.current()
    assert verdict.on_amd_network and var in verdict.reason
    assert calls == []


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_a_blank_gateway_key_is_not_a_gateway(monkeypatch, no_gateway_env, blank):
    monkeypatch.setenv("GAIA_GATEWAY_TOKEN", blank)
    monkeypatch.setenv("LEMONADE_AMD_API_KEY", blank)
    _probe(monkeypatch, False)
    assert not amd_network.on_amd_network()


def test_gateway_configured_but_dns_failing_is_still_on_the_network(
    monkeypatch, no_gateway_env
):
    monkeypatch.setenv("GAIA_GATEWAY_TOKEN", "key")
    monkeypatch.setattr(amd_network, "_gateway_answers", _REAL_ANSWERS)

    def no_dns(*_a, **_k):
        raise socket.gaierror("getaddrinfo failed")

    monkeypatch.setattr(socket, "create_connection", no_dns)
    assert amd_network.on_amd_network()


def test_a_stored_gateway_key_is_decisive(monkeypatch, no_gateway_env):
    monkeypatch.setattr(no_gateway_env, "read_stored_token", lambda: "stored")
    _probe(monkeypatch, False)
    assert "credential store" in amd_network.current().reason


def test_an_unreadable_keyring_falls_to_the_probe(monkeypatch, no_gateway_env):
    def broken():
        raise no_gateway_env.GatewayError("locked")

    monkeypatch.setattr(no_gateway_env, "read_stored_token", broken)
    _probe(monkeypatch, True)
    assert amd_network.on_amd_network()


def test_gateway_json_naming_amd_is_decisive(monkeypatch, no_gateway_env):
    no_gateway_env.GATEWAY_STATE_FILE.write_text(
        '{"base_url": "https://llm-api.amd.com/Unified/v1"}', encoding="utf-8"
    )
    _probe(monkeypatch, False)
    assert amd_network.on_amd_network()


def test_gateway_json_naming_another_gateway_falls_to_the_probe(
    monkeypatch, no_gateway_env
):
    no_gateway_env.GATEWAY_STATE_FILE.write_text(
        '{"base_url": "https://gw.example.com/v1"}', encoding="utf-8"
    )
    calls = _probe(monkeypatch, False)
    assert not amd_network.on_amd_network() and calls == [1]


def test_an_unreadable_gateway_json_counts_as_set_up(monkeypatch, no_gateway_env):
    no_gateway_env.GATEWAY_STATE_FILE.write_text("{not json", encoding="utf-8")
    _probe(monkeypatch, False)
    assert amd_network.on_amd_network()


def test_the_gateway_answering_means_on_the_network(monkeypatch, no_gateway_env):
    _probe(monkeypatch, True)
    verdict = amd_network.current()
    assert verdict.on_amd_network and "answered" in verdict.reason


def test_probe_needs_a_connection_not_just_a_dns_answer(monkeypatch):
    def refused(*_a, **_k):
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(socket, "create_connection", refused)
    assert _REAL_ANSWERS(0.1) is False

    monkeypatch.setattr(
        socket, "create_connection", lambda *a, **k: SimpleNamespace(close=lambda: 0)
    )
    assert _REAL_ANSWERS(0.1) is True


def test_a_hung_lookup_is_bounded_by_the_probe_timeout(monkeypatch):
    release = threading.Event()

    def hang(*_a, **_k):
        release.wait(5)
        raise OSError("gave up")

    monkeypatch.setattr(socket, "create_connection", hang)
    start = time.monotonic()
    assert _REAL_ANSWERS(0.1) is False
    assert time.monotonic() - start < 2
    release.set()


def test_a_positive_verdict_is_kept_for_the_process(monkeypatch, no_gateway_env):
    calls = _probe(monkeypatch, True)
    for _ in range(3):
        assert amd_network.on_amd_network()
    assert calls == [1]


def test_a_negative_verdict_is_rechecked_after_the_ttl(monkeypatch, no_gateway_env):
    now = [1000.0]
    monkeypatch.setattr(amd_network.time, "monotonic", lambda: now[0])
    calls = _probe(monkeypatch, False)
    assert not amd_network.on_amd_network()
    assert not amd_network.on_amd_network()
    assert calls == [1]
    # The VPN connects later; the next check after the TTL sees it.
    _probe(monkeypatch, True)
    now[0] += amd_network.NEGATIVE_TTL + 1
    assert amd_network.on_amd_network()


# ── The client guard ────────────────────────────────────────────────────────


def test_off_the_network_direct_anthropic_is_unchanged(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    amd_network.assert_direct_anthropic_allowed("claude-opus-5", what="x")


@pytest.mark.parametrize(
    "env_base",
    [
        None,
        "https://api.anthropic.com",
        "https://API.ANTHROPIC.COM/v1",
        "https://proxy.example.com/anthropic",
        "http://llm-api.amd.com/Anthropic",
        "https://llm-api.amd.com.evil.example/Anthropic",
        "https://evil.example/?u=https://llm-api.amd.com",
        "not a url",
    ],
)
def test_on_the_network_anything_but_the_gateway_is_refused(
    monkeypatch, on_network, env_base
):
    if env_base is not None:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", env_base)
    with pytest.raises(DirectAnthropicBlockedError) as err:
        amd_network.assert_direct_anthropic_allowed("claude-opus-5", what="The test")
    message = str(err.value)
    assert "amd.Claude-Opus-5" in message
    assert "`gaia gateway use amd.Claude-Opus-5`" in message
    assert "amd-gaia.ai/docs/guides/llm-gateway" in message


def test_an_explicit_base_url_wins_over_the_env(monkeypatch, on_network):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", GATEWAY_BASE)
    with pytest.raises(DirectAnthropicBlockedError):
        amd_network.assert_direct_anthropic_allowed(
            "claude-opus-5", "https://api.anthropic.com", what="x"
        )


@pytest.mark.parametrize(
    "base",
    [GATEWAY_BASE, "https://LLM-API.AMD.COM/Anthropic", "https://llm-api.amd.com"],
)
def test_on_the_network_the_gateway_host_is_allowed(on_network, base):
    amd_network.assert_direct_anthropic_allowed("claude-opus-5", base, what="x")


@pytest.mark.parametrize(
    "model, suggestion",
    [
        ("claude-opus-5-5", "amd.Claude-Opus-5-5"),
        ("Claude-Sonnet-5-5", "amd.Claude-Sonnet-5-5"),
        (" CLAUDE-OPUS-5 ", "amd.Claude-Opus-5"),
        ("opus", "amd.Claude-Opus-5-5"),
        ("claude-haiku-5-5", "amd.Claude-Opus-5-5"),
        (None, "amd.Claude-Opus-5-5"),
    ],
)
def test_every_id_shape_is_refused_and_named_a_gateway_model(
    on_network, model, suggestion
):
    with pytest.raises(DirectAnthropicBlockedError, match=suggestion):
        amd_network.assert_direct_anthropic_allowed(model, what="x")


def _sdk():
    built = []

    class Anthropic:
        def __init__(self, **kwargs):
            built.append(("sync", kwargs))

    class AsyncAnthropic:
        def __init__(self, **kwargs):
            built.append(("async", kwargs))

    return SimpleNamespace(Anthropic=Anthropic, AsyncAnthropic=AsyncAnthropic), built


@pytest.mark.parametrize("asynchronous", [False, True])
def test_the_client_factory_refuses_before_building(on_network, asynchronous):
    sdk, built = _sdk()
    with pytest.raises(DirectAnthropicBlockedError):
        amd_network.anthropic_client(
            sdk, model="claude-opus-5", what="x", asynchronous=asynchronous, api_key="k"
        )
    assert built == []


@pytest.mark.parametrize("asynchronous", [False, True])
def test_the_client_factory_builds_off_the_network(monkeypatch, asynchronous):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    sdk, built = _sdk()
    amd_network.anthropic_client(
        sdk, model="claude-opus-5", what="x", asynchronous=asynchronous, api_key="k"
    )
    assert built == [("async" if asynchronous else "sync", {"api_key": "k"})]


def test_the_client_factory_builds_a_gateway_client_on_the_network(on_network):
    sdk, built = _sdk()
    amd_network.anthropic_client(sdk, model="m", what="x", base_url=GATEWAY_BASE)
    assert built[0][1]["base_url"] == GATEWAY_BASE


def test_no_anthropic_client_is_built_outside_the_guard():
    """Every SDK client goes through anthropic_client; a new direct one fails here."""
    offenders = []
    for root in (SRC, HUB):
        for path in root.rglob("*.py"):
            if path.name == "amd_network.py" or "tests" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for needle in ("Anthropic(", "AsyncAnthropic", ".Anthropic\n"):
                if needle in text:
                    offenders.append(f"{path}: {needle}")
    assert offenders == []


# ── The Claude backend (ClaudeProvider) ─────────────────────────────────────


@pytest.fixture
def fake_sdk(monkeypatch):
    sdk, built = _sdk()
    sdk.Omit = object
    monkeypatch.setattr("gaia.llm.providers.claude.anthropic", sdk)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    return sdk, built


def test_the_claude_backend_is_refused_before_the_key_check(
    monkeypatch, on_network, fake_sdk
):
    from gaia.llm.providers.claude import ClaudeProvider

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("gaia.env.credential", lambda _name: None)
    with pytest.raises(DirectAnthropicBlockedError, match="amd.Claude-Opus-5-5"):
        ClaudeProvider(model="claude-opus-5-5")
    assert fake_sdk[1] == []


def test_the_claude_backend_with_an_env_base_url_at_anthropic_is_refused(
    monkeypatch, on_network, fake_sdk
):
    from gaia.llm import create_client

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-x")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    with pytest.raises(DirectAnthropicBlockedError):
        create_client("claude", model="Claude-Sonnet-5")
    assert fake_sdk[1] == []


def test_the_gateway_claude_backend_still_works_on_the_network(
    monkeypatch, on_network, fake_sdk
):
    from gaia.llm import create_client

    monkeypatch.delenv("GAIA_GATEWAY_ANTHROPIC_BASE_URL", raising=False)
    create_client("lemonade", model="amd.Claude-Opus-5-5")
    assert fake_sdk[1][0][1]["base_url"] == GATEWAY_BASE


def test_the_claude_backend_off_the_network_is_unchanged(monkeypatch, fake_sdk):
    from gaia.llm.providers.claude import ClaudeProvider

    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-x")
    ClaudeProvider(model="claude-opus-5-5")
    assert fake_sdk[1][0][1]["api_key"] == "sk-ant-api03-x"


# ── Claude Code CLI launches ────────────────────────────────────────────────


def test_a_claude_cli_run_aimed_at_anthropic_is_refused(on_network):
    with pytest.raises(DirectAnthropicBlockedError, match="api.anthropic.com"):
        amd_network.assert_claude_cli_allowed("sonnet", {}, what="x")
    amd_network.assert_claude_cli_allowed(
        "sonnet", {"ANTHROPIC_BASE_URL": GATEWAY_BASE}, what="x"
    )


def test_session_analysis_is_refused_before_the_cli_runs(monkeypatch, on_network):
    from gaia.factory.harvest import claude_cli

    monkeypatch.setattr(
        claude_cli, "resolve_claude", lambda *_a: pytest.fail("CLI was resolved")
    )
    with pytest.raises(DirectAnthropicBlockedError):
        claude_cli.run_claude("hi")


@pytest.mark.parametrize("model", ["claude-opus-5", "opus", "sonnet"])
def test_the_bench_claude_code_arm_is_refused(on_network, model):
    from gaia.eval.bench import harness

    with pytest.raises(DirectAnthropicBlockedError):
        harness.claude_code_command("task", model)


# ── The eval judge: the gateway, explicitly ─────────────────────────────────


@pytest.fixture
def judge_env(monkeypatch, on_network):
    # Credentials that would win off the network must not be used on it.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-must-not-leave")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-must-not-be-used")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN_BACKUP", "oauth-backup")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "other-bearer")
    monkeypatch.delenv("GAIA_GATEWAY_ANTHROPIC_BASE_URL", raising=False)
    return on_network


def test_the_judge_credential_is_the_gateway_on_the_network(judge_env, claude_login):
    from gaia.eval import claude_credential

    cred = claude_credential.resolve_claude_credential()
    assert cred.is_gateway and not cred.is_subscription
    assert "AMD LLM Gateway" in cred.label and GATEWAY_TOKEN not in cred.label
    assert cred.cli_flags() == []
    env = cred.cli_env(
        {"PATH": "p", "ANTHROPIC_API_KEY": "sk", "anthropic_api_key": "x"}
    )
    assert env["ANTHROPIC_BASE_URL"] == GATEWAY_BASE
    assert (
        env["ANTHROPIC_CUSTOM_HEADERS"] == f"Ocp-Apim-Subscription-Key: {GATEWAY_TOKEN}"
    )
    assert env["ANTHROPIC_AUTH_TOKEN"] == GATEWAY_TOKEN
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert not {"ANTHROPIC_API_KEY", "anthropic_api_key"} & set(env)
    env = cred.cli_env(dict(__import__("os").environ))
    assert not [k for k in env if k.startswith("CLAUDE_CODE_OAUTH_TOKEN")]
    assert "must-not-leave" not in "".join(env.values())


def test_the_judge_model_maps_and_is_announced(judge_env, capsys):
    from gaia.eval import claude_credential

    cred = claude_credential.resolve_claude_credential()
    assert cred.cli_model("claude-opus-5") == "claude-opus-5"
    assert cred.cli_model("Claude-Opus-5-5") == "claude-opus-5-5"
    assert (
        "Claude judge: amd.Claude-Opus-5 via AMD LLM Gateway" in capsys.readouterr().err
    )


@pytest.mark.parametrize("model", ["claude-haiku-5-5", "claude-fable-5", "opus", ""])
def test_a_judge_model_without_a_gateway_equivalent_is_refused(judge_env, model):
    from gaia.eval import claude_credential

    cred = claude_credential.resolve_claude_credential()
    with pytest.raises(ValueError, match="no equivalent"):
        cred.cli_model(model)


def test_off_the_network_the_judge_model_is_untouched(monkeypatch):
    from gaia.eval.claude_credential import ENV_KEY, ClaudeCredential

    assert ClaudeCredential(ENV_KEY, api_key="k").cli_model("claude-haiku-5-5") == (
        "claude-haiku-5-5"
    )


def test_on_the_network_without_a_gateway_key_the_judge_fails_loudly(
    monkeypatch, no_gateway_env
):
    from gaia.eval import claude_credential

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-x")
    _probe(monkeypatch, True)
    with pytest.raises(RuntimeError, match="never to Anthropic directly"):
        claude_credential.resolve_claude_credential()


def test_the_judge_client_sends_to_the_gateway_on_the_wire(
    monkeypatch, judge_env, capsys
):
    """The real SDK, a fake transport: URL, headers and model as sent."""
    anthropic = pytest.importorskip("anthropic")
    httpx = pytest.importorskip("httpx")
    pytest.importorskip("bs4")
    from gaia.eval import claude as eval_claude

    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    real = eval_claude.anthropic_client

    def with_transport(sdk, **kwargs):
        kwargs["http_client"] = httpx.Client(transport=httpx.MockTransport(handler))
        return real(sdk, **kwargs)

    monkeypatch.setattr(eval_claude, "anthropic_client", with_transport)
    client = eval_claude.ClaudeClient(model="claude-opus-5")
    blocks = client.get_completion("hi")
    assert blocks[0].text == "ok"
    request = sent[0]
    assert str(request.url) == f"{GATEWAY_BASE}/v1/messages"
    assert request.headers["ocp-apim-subscription-key"] == GATEWAY_TOKEN
    assert "x-api-key" not in request.headers
    assert "must-not-leave" not in str(request.headers)
    assert request.headers["authorization"] == f"Bearer {GATEWAY_TOKEN}"
    assert b'"model":"claude-opus-5"' in request.content.replace(b" ", b"")
    assert (
        "Claude judge: amd.Claude-Opus-5 via AMD LLM Gateway" in capsys.readouterr().err
    )
    assert anthropic is not None


def test_the_judge_client_refuses_a_model_the_gateway_lacks(judge_env):
    pytest.importorskip("anthropic")
    pytest.importorskip("bs4")
    from gaia.eval.claude import ClaudeClient

    with pytest.raises(ValueError, match="no equivalent"):
        ClaudeClient(model="claude-haiku-4-5")


def test_a_subscription_judge_becomes_the_gateway_judge_on_the_network(
    judge_env, claude_login
):
    pytest.importorskip("anthropic")
    pytest.importorskip("bs4")
    from gaia.eval.claude import ClaudeClient
    from gaia.eval.judge_client import make_judge_client

    client = make_judge_client(model="claude-opus-5")
    assert isinstance(client, ClaudeClient)
    assert str(client.client.base_url).rstrip("/") == GATEWAY_BASE


def test_the_flagship_judge_command_uses_the_gateway(
    monkeypatch, judge_env, claude_login
):
    from gaia.eval import flagship_tasks

    cmd = flagship_tasks.judge_command("claude-opus-5", {})
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5"
    assert "--bare" not in cmd
    with pytest.raises(ValueError, match="no equivalent"):
        flagship_tasks.judge_command("claude-haiku-4-5", {})


def test_the_eval_fixer_is_refused_on_the_network(monkeypatch, on_network, tmp_path):
    from gaia.eval import runner

    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: pytest.fail("ran"))
    scorecard = {"scenarios": [{"scenario_id": "s", "status": "FAIL"}]}
    with pytest.raises(DirectAnthropicBlockedError):
        runner.run_fix_iteration(scorecard, tmp_path, 1)
