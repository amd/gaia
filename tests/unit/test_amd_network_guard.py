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


@pytest.mark.parametrize(
    "where", ["LEMONADE_AMD_API_KEY", "GAIA_GATEWAY_TOKEN", "store"]
)
def test_another_gateways_key_is_no_evidence_of_amds_network(
    monkeypatch, no_gateway_env, where
):
    no_gateway_env.GATEWAY_STATE_FILE.write_text(
        '{"base_url": "https://gw.example.com/v1"}', encoding="utf-8"
    )
    if where == "store":
        monkeypatch.setattr(no_gateway_env, "read_stored_token", lambda: "stored")
    else:
        monkeypatch.setenv(where, "key")
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
    # The VPN connects later; the check after the TTL starts a refresh that sees it.
    refreshed = threading.Event()

    def answers(*_a, **_k):
        refreshed.set()
        return True

    monkeypatch.setattr(amd_network, "_gateway_answers", answers)
    now[0] += amd_network.NEGATIVE_TTL + 1
    amd_network.on_amd_network()
    assert refreshed.wait(5)
    deadline = time.perf_counter() + 5
    while not amd_network.on_amd_network() and time.perf_counter() < deadline:
        threading.Event().wait(0.02)
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
        "https://api.anthropic.com\\@llm-api.amd.com",
        "https://api.anthropic.com\\@llm-api.amd.com/Anthropic",
        "https://llm-api.amd.com@api.anthropic.com/",
        "https://llm-api.amd.com%2f@api.anthropic.com",
        "https://llm-api.amd.com /Anthropic",
        " https://llm-api.amd.com/Anthropic",
        "https://llm-api.amd.com:8443/Anthropic",
        "https://llm-api.amd.com/Anthropic?x=@api.anthropic.com",
        "https://llm-api.amd.com/Anthropic#@api.anthropic.com",
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
        ("claude-haiku-5-5", "amd.Claude-Haiku-5-5"),
        ("claude-fable-5-1", "amd.Claude-Fable-5-1"),
        ("claude-haiku-4-5", "amd.Claude-Haiku-4-5"),
        ("Claude-Sonnet-4-6", "amd.Claude-Sonnet-4-6"),
        ("claude-opus-4-7", "amd.Claude-Opus-5-5"),
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


SCANNED_ROOTS = ("src", "hub", "util", "scripts", "eval")
#: Anthropic SDK names that build a client.
_CLIENT_NAMES = {"Anthropic", "AsyncAnthropic", "Client", "AsyncClient"}


def _anthropic_client_refs(tree):
    """Places a module reaches an Anthropic client class, however it is spelled."""
    import ast

    module_aliases = {"anthropic"}
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "anthropic":
                    module_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "anthropic"
        ):
            hits += [
                f"from-import {a.name}" for a in node.names if a.name in _CLIENT_NAMES
            ]
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _CLIENT_NAMES:
            base = node.value
            if isinstance(base, ast.Name) and base.id in module_aliases:
                hits.append(f"{base.id}.{node.attr}")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in module_aliases
        ):
            hits.append(f"getattr({node.args[0].id}, ...)")
    return hits


def _python_sources():
    for root in SCANNED_ROOTS:
        base = REPO / root
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            parts = set(path.parts)
            if (
                "tests" in parts
                or "node_modules" in parts
                or path.name.startswith("test_")
            ):
                continue
            yield path


def test_no_anthropic_client_is_built_outside_the_guard():
    """Every SDK client goes through anthropic_client; a new direct one fails here."""
    import ast

    offenders = []
    for path in _python_sources():
        if path.name == "amd_network.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        offenders += [f"{path}: {hit}" for hit in _anthropic_client_refs(tree)]
    assert offenders == []


@pytest.mark.parametrize(
    "source",
    [
        "from anthropic import Anthropic as C\nC()",
        "import anthropic as a\na.Client()",
        "import anthropic\ngetattr(anthropic, 'Anthropic')()",
        "from anthropic import AsyncAnthropic",
        "import anthropic.lib as x\nimport anthropic\nanthropic.AsyncClient()",
    ],
)
def test_the_scan_catches_aliased_constructions(source):
    import ast

    assert _anthropic_client_refs(ast.parse(source))


#: Modules that start a `claude` process, and the guard each one goes through.
_CLI_GUARDS = (
    "claude_code_route",
    "assert_claude_cli_allowed",
    "assert_direct_anthropic_allowed",
)
#: Starts `claude` without a guard, and why that is safe.
_CLI_EXEMPT = {
    # `claude auth status` reads local state only.
    "claude_credential.py",
    # Stdlib-only CI probe; workflows skip it on AMD's network (amd-network).
    "select_claude_account.py",
}


def test_every_claude_cli_launch_goes_through_the_guard():
    import re

    launches = re.compile(r"""(which|_which)\(\s*["']claude["']|def resolve_claude\(""")
    offenders = []
    for path in _python_sources():
        text = path.read_text(encoding="utf-8", errors="replace")
        if not launches.search(text) or path.name in _CLI_EXEMPT:
            continue
        if not any(guard in text for guard in _CLI_GUARDS):
            offenders.append(str(path))
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


def _capture_run(monkeypatch, module):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(module.subprocess, "run", run)
    return calls


def test_session_analysis_goes_through_the_gateway(monkeypatch, judge_env):
    from gaia.factory.harvest import claude_cli

    monkeypatch.setattr(claude_cli, "resolve_claude", lambda *_a: "claude")
    calls = _capture_run(monkeypatch, claude_cli)
    assert claude_cli.run_claude("hi") == "ok"
    argv, kwargs = calls[0]
    env = kwargs["env"]
    assert env["ANTHROPIC_BASE_URL"] == GATEWAY_BASE
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert not [k for k in env if k.upper().startswith("CLAUDE_CODE_OAUTH_TOKEN")]
    assert "ANTHROPIC_API_KEY" not in env
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5-5"
    assert "--setting-sources" in argv


def test_session_analysis_off_the_network_is_unchanged(monkeypatch):
    from gaia.factory.harvest import claude_cli

    monkeypatch.setattr(claude_cli, "resolve_claude", lambda *_a: "claude")
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    calls = _capture_run(monkeypatch, claude_cli)
    claude_cli.run_claude("hi")
    argv, kwargs = calls[0]
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert "--setting-sources" not in argv
    assert "ANTHROPIC_BASE_URL" not in kwargs["env"]


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
    assert cred.cli_flags() == list(claude_credential.GATEWAY_CLI_FLAGS)
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


@pytest.mark.parametrize("model", ["claude-opus-4-7", "claude-sonnet-4", "opus", ""])
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
        ClaudeClient(model="claude-opus-4-7")


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
        flagship_tasks.judge_command("claude-opus-4-7", {})


def test_the_eval_fixer_goes_through_the_gateway(monkeypatch, judge_env, tmp_path):
    from gaia.eval import runner

    calls = _capture_run(monkeypatch, runner)
    scorecard = {"scenarios": [{"scenario_id": "s", "status": "FAIL"}]}
    runner.run_fix_iteration(scorecard, tmp_path, 1)
    argv, kwargs = calls[0]
    assert kwargs["env"]["ANTHROPIC_BASE_URL"] == GATEWAY_BASE
    assert "must-not-leave" not in "".join(kwargs["env"].values())
    assert argv[argv.index("--model") + 1] == "claude-opus-5"


def test_the_eval_fixer_off_the_network_is_unchanged(monkeypatch, tmp_path):
    from gaia.eval import runner

    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    calls = _capture_run(monkeypatch, runner)
    scorecard = {"scenarios": [{"scenario_id": "s", "status": "FAIL"}]}
    runner.run_fix_iteration(scorecard, tmp_path, 1)
    argv, kwargs = calls[0]
    assert "--model" not in argv and "ANTHROPIC_BASE_URL" not in kwargs["env"]


# ── CI: eval jobs on AMD's network use the gateway ──────────────────────────

REPO = SRC.parent
WORKFLOWS = REPO / ".github" / "workflows"
#: Workflows that drive or judge with Claude on self-hosted runners.
EVAL_WORKFLOWS = (
    "eval_flagship.yml",
    "eval_scenario_lane.yml",
    "gaia_scorecard_refresh.yml",
    "test_gaia_agent_eval.yml",
    "email_scorecard_refresh.yml",
)


def _check_script():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "check_amd_network", REPO / "util" / "check_amd_network.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ci_check_off_the_network_passes_and_says_so(monkeypatch, tmp_path, capsys):
    out = tmp_path / "out"
    monkeypatch.delenv("GATEWAY_SECRET", raising=False)
    assert _check_script().main(["--github-output", str(out)]) == 0
    assert out.read_text() == "on=false\n"


def test_ci_check_on_the_network_without_a_key_fails_naming_the_secret(
    monkeypatch, no_gateway_env, tmp_path, capsys
):
    _probe(monkeypatch, True)
    monkeypatch.delenv("GATEWAY_SECRET", raising=False)
    out = tmp_path / "out"
    assert _check_script().main(["--github-output", str(out), "--what", "Lane x"]) == 1
    assert out.read_text() == "on=true\n"
    printed = capsys.readouterr().out
    assert "::error::Lane x" in printed and "LEMONADE_AMD_API_KEY" in printed


@pytest.mark.parametrize("secret", ["key", "  key  "])
def test_ci_check_on_the_network_with_the_secret_passes(
    monkeypatch, no_gateway_env, tmp_path, secret
):
    _probe(monkeypatch, True)
    monkeypatch.setenv("GATEWAY_SECRET", secret)
    assert _check_script().main(["--github-output", str(tmp_path / "o")]) == 0


def test_ci_check_the_secret_alone_never_makes_a_runner_on_network(
    monkeypatch, no_gateway_env, tmp_path
):
    """A GitHub-hosted runner gets the secret too; it must still read as off."""
    _probe(monkeypatch, False)
    monkeypatch.setenv("GATEWAY_SECRET", "key")
    out = tmp_path / "o"
    assert _check_script().main(["--github-output", str(out)]) == 0
    assert out.read_text() == "on=false\n"


@pytest.mark.parametrize("name", EVAL_WORKFLOWS)
def test_eval_workflows_route_claude_through_the_gateway_on_amds_network(name):
    import yaml

    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    steps = [s for job in doc["jobs"].values() for s in job.get("steps", [])]
    checks = [s for s in steps if s.get("uses") == "./.github/actions/amd-network"]
    assert checks, f"{name} never checks for AMD's network"
    for check in checks:
        assert check["with"]["gateway-key"] == "${{ secrets.LEMONADE_AMD_API_KEY }}"
    # Only on the network, so a GitHub-hosted runner never reads as on it.
    gateway_env = (
        "GAIA_GATEWAY_TOKEN: ${{ steps.amd_net.outputs.on == 'true' && "
        "secrets.LEMONADE_AMD_API_KEY || '' }}"
    )
    assert gateway_env in text
    picks = [s for s in steps if s.get("uses") == "./.github/actions/claude-credential"]
    for pick in picks:
        assert "steps.amd_net.outputs.on != 'true'" in str(
            pick.get("if")
        ), f"{name} probes Anthropic directly even on AMD's network"


def test_the_scenario_lane_declares_the_gateway_secret():
    import yaml

    doc = yaml.safe_load((WORKFLOWS / "eval_scenario_lane.yml").read_text("utf-8"))
    trigger = doc.get("on") or doc.get(True)
    assert "LEMONADE_AMD_API_KEY" in trigger["workflow_call"]["secrets"]


# ── The flagship agent reports refused ids to the TUI ───────────────────────


def test_the_flagship_reports_no_refused_ids_off_the_network():
    stdio = pytest.importorskip("gaia_agent.stdio")
    assert stdio.claude_unavailable() == {}


def test_the_flagship_reports_every_direct_id_on_the_network(on_network):
    stdio = pytest.importorskip("gaia_agent.stdio")
    refused = stdio.claude_unavailable()
    assert set(refused) == set(stdio.CLAUDE_MODELS)
    assert refused["claude-opus-5"]["use"] == "amd.Claude-Opus-5"
    assert "AMD LLM Gateway" in refused["claude-opus-5"]["reason"]


def test_the_eval_driver_runs_claude_code_against_the_gateway(judge_env, claude_login):
    from gaia.eval import runner

    flags, env = runner._driver_auth()
    assert "--bare" not in flags
    assert env["ANTHROPIC_BASE_URL"] == GATEWAY_BASE
    assert env["ANTHROPIC_CUSTOM_HEADERS"].startswith("Ocp-Apim-Subscription-Key: ")
    assert "ANTHROPIC_API_KEY" not in env and "CLAUDE_CODE_OAUTH_TOKEN" not in env


#: The AMD LLM Gateway's Claude models, from its live /Unified/v1/models.
GATEWAY_CLAUDE_CATALOG = {
    f"amd.Claude-{name}"
    for name in (
        "Fable-5",
        "Fable-5-1",
        "Fable-5-2",
        "Haiku-4-5",
        "Haiku-5-5",
        "Opus-4-5",
        "Opus-4-6",
        "Opus-4-8",
        "Opus-5",
        "Opus-5-5",
        "Sonnet-4-5",
        "Sonnet-4-6",
        "Sonnet-5",
        "Sonnet-5-5",
    )
}


def test_every_mapping_targets_a_model_the_gateway_serves():
    targets = set(amd_network.GATEWAY_EQUIVALENTS.values())
    assert targets <= GATEWAY_CLAUDE_CATALOG
    assert amd_network.DEFAULT_GATEWAY_CLAUDE in GATEWAY_CLAUDE_CATALOG
    # Each catalog model is reachable from its own direct id.
    assert targets == GATEWAY_CLAUDE_CATALOG


def test_the_baseline_judge_model_has_a_gateway_equivalent():
    import json

    for meta in (REPO / "tests" / "fixtures" / "eval_baselines").glob("*/meta.json"):
        model = json.loads(meta.read_text(encoding="utf-8")).get("eval_model")
        if model:
            assert amd_network.gateway_equivalent(model), f"{meta}: {model}"


# ── Hardening: route override, per-request checks, pin, proxy, no-probe ────


@pytest.mark.parametrize(
    "route",
    [
        "https://api.anthropic.com",
        "https://proxy.example.com/Anthropic",
        "https://api.anthropic.com\\@llm-api.amd.com",
    ],
)
def test_a_gateway_route_override_off_the_gateway_is_refused(
    monkeypatch, judge_env, route
):
    from gaia.eval import claude_credential

    monkeypatch.setenv("GAIA_GATEWAY_ANTHROPIC_BASE_URL", route)
    with pytest.raises(RuntimeError, match="only to the AMD LLM Gateway"):
        claude_credential.resolve_claude_credential()


def test_the_driver_checks_its_environment_before_running(monkeypatch, judge_env):
    from gaia.eval import runner

    monkeypatch.setattr(
        runner,
        "_driver_auth",
        lambda: ([], {"ANTHROPIC_BASE_URL": "https://x.example"}),
    )
    monkeypatch.setattr(runner, "_run_driver", lambda *a, **k: pytest.fail("ran"))
    with pytest.raises(DirectAnthropicBlockedError):
        runner.run_scenario_subprocess(
            None, {"id": "s"}, Path("."), "http://127.0.0.1:1", "claude-opus-5", "1", 5
        )


def _vpn_connects(monkeypatch):
    """Off the network until now; the next check sees the gateway set up."""
    monkeypatch.setattr(amd_network, "_gateway_configured", lambda: "vpn up")


def test_a_claude_backend_built_off_the_network_stops_once_it_is_on(
    monkeypatch, fake_sdk
):
    from gaia.llm.providers.claude import ClaudeProvider

    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-x")
    provider = ClaudeProvider(model="claude-opus-5-5")
    provider._client = SimpleNamespace(
        messages=SimpleNamespace(create=lambda **k: pytest.fail("request sent"))
    )
    _vpn_connects(monkeypatch)
    with pytest.raises(DirectAnthropicBlockedError):
        provider.chat([{"role": "user", "content": "hi"}], stream=False)
    with pytest.raises(DirectAnthropicBlockedError):
        list(provider.chat([{"role": "user", "content": "hi"}], stream=True))


def test_the_eval_client_built_off_the_network_stops_once_it_is_on(monkeypatch):
    pytest.importorskip("anthropic")
    pytest.importorskip("bs4")
    from gaia.eval.claude import ClaudeClient

    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-x")
    client = ClaudeClient(model="claude-opus-5")
    client.client.messages.create = lambda **k: pytest.fail("request sent")
    _vpn_connects(monkeypatch)
    with pytest.raises(DirectAnthropicBlockedError):
        client.get_completion("hi")


def test_the_cli_judge_built_off_the_network_stops_once_it_is_on(
    monkeypatch, claude_login
):
    from gaia.eval import judge_client

    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    client = judge_client.ClaudeCliClient(model="claude-opus-5")
    monkeypatch.setattr(
        judge_client.subprocess, "run", lambda *a, **k: pytest.fail("ran")
    )
    _vpn_connects(monkeypatch)
    with pytest.raises(DirectAnthropicBlockedError):
        client.get_completion("hi")


def test_the_pin_forces_on_without_probing(monkeypatch, no_gateway_env):
    monkeypatch.setenv("GAIA_AMD_NETWORK", "1")
    calls = _probe(monkeypatch, False)
    assert amd_network.on_amd_network() and calls == []


@pytest.mark.parametrize("value", ["0", "false", "off", "yes", "2"])
def test_the_pin_has_no_off_value(monkeypatch, no_gateway_env, value):
    monkeypatch.setenv("GAIA_AMD_NETWORK", value)
    monkeypatch.setenv("GAIA_GATEWAY_TOKEN", "key")
    with pytest.raises(ValueError, match="only accepted value is 1"):
        amd_network.current()


def _fake_proxy(reply):
    """A one-shot local proxy that answers CONNECT with *reply*."""
    import socketserver

    seen = []

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            seen.append(self.request.recv(256))
            self.request.sendall(reply)

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()
    return server, seen


@pytest.mark.allow_network  # a local fake proxy only
def test_a_proxy_that_connects_to_the_gateway_means_on_the_network():
    server, seen = _fake_proxy(b"HTTP/1.1 200 Connection established\r\n\r\n")
    with server:
        assert amd_network._connect_via_proxy(server.server_address, 2.0)
    assert seen[0].startswith(b"CONNECT llm-api.amd.com:443 ")


@pytest.mark.allow_network  # a local fake proxy only
def test_a_proxy_that_refuses_the_gateway_is_not_evidence():
    server, _ = _fake_proxy(b"HTTP/1.1 403 Forbidden\r\n\r\n")
    with server:
        assert not amd_network._connect_via_proxy(server.server_address, 2.0)


def test_the_probe_uses_https_proxy_unless_bypassed(monkeypatch):
    for var in ("HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp:8080")
    assert amd_network._https_proxy() == ("proxy.corp", 8080)
    monkeypatch.setenv("NO_PROXY", ".amd.com")
    assert amd_network._https_proxy() is None


def test_a_stale_negative_is_refreshed_without_blocking(monkeypatch, no_gateway_env):
    now = [1000.0]
    monkeypatch.setattr(amd_network.time, "monotonic", lambda: now[0])
    _probe(monkeypatch, False)
    assert not amd_network.on_amd_network()
    release = threading.Event()
    done = threading.Event()

    def slow(*_a, **_k):
        release.wait(5)
        done.set()
        return True

    monkeypatch.setattr(amd_network, "_gateway_answers", slow)
    now[0] += amd_network.NEGATIVE_TTL + 1
    start = time.perf_counter()
    assert not amd_network.on_amd_network()
    assert time.perf_counter() - start < 0.5
    release.set()
    assert done.wait(5)
    deadline = time.perf_counter() + 5
    while not amd_network.on_amd_network() and time.perf_counter() < deadline:
        threading.Event().wait(0.02)
    assert amd_network.on_amd_network()


def test_listing_without_an_anthropic_key_never_probes(monkeypatch, no_gateway_env):
    from gaia.llm.hybrid_templates import build_inventory

    monkeypatch.setattr(
        amd_network, "_gateway_answers", lambda *a, **k: pytest.fail("probed")
    )
    inv = build_inventory(
        {"data": []},
        {"cloud": {"providers": []}},
        anthropic_key=False,
        local_only=False,
    )
    assert not inv.amd_network


def test_the_flagship_ping_without_an_anthropic_key_never_probes(
    monkeypatch, no_gateway_env
):
    stdio = pytest.importorskip("gaia_agent.stdio")
    monkeypatch.setattr("gaia.env.credential", lambda name: None)
    monkeypatch.setattr(
        amd_network, "_gateway_answers", lambda *a, **k: pytest.fail("probed")
    )
    assert stdio.claude_unavailable() == {}


def _claude_steps(job):
    for step in job.get("steps", []) or []:
        uses, run = str(step.get("uses", "")), str(step.get("run", ""))
        if (
            "claude-code-action" in uses
            or "claude-credential" in uses
            or "gaia eval agent" in run
            or "judge_client" in run
            or "eval tasks judge" in run
            or "eval_drafting_report" in run
        ):
            yield step


def test_every_self_hosted_job_that_runs_claude_checks_for_amds_network():
    import yaml

    offenders = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for name, job in (doc.get("jobs") or {}).items():
            runs_on = str(job.get("runs-on", ""))
            if "self-hosted" not in runs_on and "inputs.runs-on" not in runs_on:
                continue
            if not list(_claude_steps(job)):
                continue
            steps = job.get("steps", [])
            if not any(s.get("uses") == "./.github/actions/amd-network" for s in steps):
                offenders.append(f"{path.name}:{name}")
            if "1" not in str((job.get("env") or {}).get("GAIA_AMD_NETWORK", "")):
                offenders.append(f"{path.name}:{name} (no GAIA_AMD_NETWORK pin)")
    assert offenders == []


def test_no_anthropic_credential_reaches_a_step_on_amds_network():
    import re

    pattern = re.compile(
        r"^\s+(CLAUDE_CODE_OAUTH_TOKEN(?:_BACKUP)?|ANTHROPIC_API_KEY): (.+)$", re.M
    )
    offenders = []
    for name in EVAL_WORKFLOWS:
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            value = match.group(2)
            if "steps.claude_account" in value and (
                "amd_net.outputs.on != 'true'" not in value
            ):
                offenders.append(f"{name}: {match.group(0).strip()[:80]}")
    assert offenders == []


def test_the_doc_walkthrough_uses_the_gateway_on_amds_network():
    import yaml

    doc = yaml.safe_load(
        (WORKFLOWS / "claude-weekly-doc-walkthrough.yml").read_text(encoding="utf-8")
    )
    steps = {s.get("id"): s for s in doc["jobs"]["walkthrough"]["steps"]}
    for step_id in ("executor", "judge"):
        step = steps[step_id]
        assert "amd_net.outputs.on == 'true'" in step["env"]["ANTHROPIC_BASE_URL"]
        assert "llm-api.amd.com/Anthropic" in step["env"]["ANTHROPIC_BASE_URL"]
        assert "LEMONADE_AMD_API_KEY" in step["env"]["ANTHROPIC_CUSTOM_HEADERS"]
        assert "amd_net.outputs.on != 'true'" in step["with"]["claude_code_oauth_token"]


def test_the_offline_list_also_marks_direct_claude_unavailable(on_network):
    from gaia.llm.hybrid_templates import offline_inventory

    inv = offline_inventory("down", anthropic_key=True, local_only=False)
    offer = inv.offers["claude-opus-5-5"]
    assert offer.available is False and "amd.Claude-Opus-5-5" in offer.reason
