# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Local only on the paths that don't go through LemonadeClient's methods.

Speech, Perplexity search, the eval and harvest Claude CLIs, the tool-cost
benchmark, saved cloud models, the Agent UI restore, error classification, and
the cold-machine / restarting-server cases of the guard itself. Every network
entry point is made to fail loudly, so a refusal that comes after the request
would still fail the test.
"""

import inspect
import json

import pytest
import requests

from gaia.llm import local_only
from gaia.llm.lemonade_client import LemonadeClient
from gaia.llm.local_only import CloudDisabledError

CATALOG = [
    {
        "id": "Whisper-Large-v3-Turbo",
        "recipe": "whispercpp",
        "labels": ["transcription"],
    },
    {"id": "kokoro-v1", "recipe": "kokoro", "labels": ["tts"]},
    {"id": "fireworks.whisper", "recipe": "cloud", "labels": ["transcription"]},
]


@pytest.fixture
def local_only_on(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_POLICY_FILE", str(tmp_path / "policy.json"))
    local_only.set_policy("local_ai_only")


@pytest.fixture
def no_network(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError(f"network reached: {args[:2]}")

    for name in ("post", "get", "request"):
        monkeypatch.setattr(requests, name, boom)
    monkeypatch.setattr(requests.Session, "post", boom)
    return boom


# ── Speech ─────────────────────────────────────────────────────────────────


def _audio(cls, model, monkeypatch):
    client = cls.__new__(cls)
    client.base_url = "http://127.0.0.1:13305/api/v1"
    client.model = model
    client.api_key = None
    client.timeout = 5
    client._session = requests.Session()
    monkeypatch.setattr(cls, "catalog", lambda self: list(CATALOG))
    return client


def test_tts_refuses_a_cloud_voice_before_sending(
    local_only_on, no_network, monkeypatch
):
    from gaia.audio.lemonade_tts import LemonadeTTSClient

    tts = _audio(LemonadeTTSClient, "fireworks.whisper", monkeypatch)
    with pytest.raises(CloudDisabledError, match="cloud model"):
        tts.synthesize("hello")


def test_tts_with_a_local_voice_gets_past_the_check(local_only_on, monkeypatch):
    from gaia.audio.lemonade_tts import LemonadeTTSClient

    tts = _audio(LemonadeTTSClient, "kokoro-v1", monkeypatch)
    sent = []
    monkeypatch.setattr(
        requests.Session, "post", lambda self, url, **kw: sent.append(url) or 1 / 0
    )
    with pytest.raises(ZeroDivisionError):
        tts.synthesize("hello")
    assert sent == [tts.speech_url]


@pytest.mark.parametrize(
    "model, refused",
    [
        ("fireworks.whisper", True),
        ("unknown-asr", True),
        ("Whisper-Large-v3-Turbo", False),
    ],
)
def test_asr_check(local_only_on, no_network, monkeypatch, model, refused):
    from gaia.audio.lemonade_asr import LemonadeASRClient

    asr = _audio(LemonadeASRClient, model, monkeypatch)
    if refused:
        with pytest.raises(CloudDisabledError):
            asr._refuse_unless_local()
    else:
        asr._refuse_unless_local()


def test_transcribe_checks_before_any_request():
    from gaia.audio.lemonade_asr import LemonadeASRClient

    src = inspect.getsource(LemonadeASRClient.transcribe)
    assert src.index("_refuse_unless_local()") < src.index("_post_multipart(")


# ── Perplexity web search ──────────────────────────────────────────────────


def test_perplexity_search_refuses_under_local_only(local_only_on, monkeypatch):
    from gaia.mcp.external_services import PerplexityService

    service = PerplexityService.__new__(PerplexityService)
    service.env = {"PERPLEXITY_API_KEY": "pplx-test"}
    service.call_tool = lambda *a, **k: pytest.fail("Perplexity reached")
    result = service.search_web("my private question")
    assert result["success"] is False
    assert "Local only is on" in result["error"]


# ── Claude CLIs and the eval tools ─────────────────────────────────────────


def test_eval_judge_cli_refuses(local_only_on, monkeypatch):
    from gaia.eval import judge_client

    monkeypatch.setattr(
        judge_client.subprocess, "run", lambda *a, **k: pytest.fail("claude ran")
    )
    client = judge_client.ClaudeCliClient.__new__(judge_client.ClaudeCliClient)
    with pytest.raises(CloudDisabledError, match="Claude Code CLI"):
        client.get_completion("judge this")


def test_harvest_claude_refuses(local_only_on, monkeypatch):
    from gaia.factory.harvest import claude_cli

    monkeypatch.setattr(
        claude_cli.subprocess, "run", lambda *a, **k: pytest.fail("claude ran")
    )
    with pytest.raises(CloudDisabledError):
        claude_cli.run_claude("summarise my sessions")


def test_bench_claude_command_refuses(local_only_on):
    from gaia.eval.bench import harness

    with pytest.raises(CloudDisabledError):
        harness.claude_code_command("task", "claude-opus-5")


def test_scenario_preflight_reports_local_only(local_only_on, monkeypatch):
    from gaia.eval import runner

    src = inspect.getsource(runner.preflight_check)
    assert "cloud_blocked()" in src and "Local only is on" in src


def test_eval_claude_client_refuses_each_call_after_a_switch(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    from gaia.eval import claude as eval_claude

    monkeypatch.setenv("GAIA_POLICY_FILE", str(tmp_path / "policy.json"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    # CI's unit environment has no anthropic SDK; a stub module stands in for it.
    stub = MagicMock()
    monkeypatch.setattr(eval_claude, "anthropic", stub)
    sdk = stub.Anthropic
    client = eval_claude.ClaudeClient()  # built while cloud is allowed
    local_only.set_policy("local_ai_only")
    with pytest.raises(CloudDisabledError):
        client.get_completion("judge this")
    with pytest.raises(CloudDisabledError):
        client.count_tokens("judge this")
    sdk.return_value.messages.create.assert_not_called()
    sdk.return_value.messages.count_tokens.assert_not_called()


def test_tool_cost_refuses_a_cloud_model(local_only_on, monkeypatch):
    from gaia.eval import tool_cost

    monkeypatch.setattr(
        LemonadeClient,
        "list_models",
        lambda self, show_all=False, timeout=None: {"data": list(CATALOG)},
    )
    with pytest.raises(CloudDisabledError):
        tool_cost._refuse_unless_local(
            "fireworks.whisper", "http://127.0.0.1:13305/api/v1"
        )


# ── The guard on a cold machine and a restarting server ────────────────────


def test_a_declared_user_model_is_allowed_before_its_first_pull(local_only_on):
    from gaia.llm.lemonade_client import MODELS

    declared = next(
        r for r in MODELS.values() if r.recipe and r.model_id.startswith("user.")
    )
    recipes = local_only.recipes_in_catalog(declared.model_id, {}, lambda: {"data": []})
    assert recipes == [declared.recipe]
    local_only.assert_model_permitted(declared.model_id, lambda m: recipes)


def test_the_guard_reads_the_catalog_through_the_restart_wrapper(
    local_only_on, monkeypatch
):
    c = LemonadeClient.__new__(LemonadeClient)
    c._model_metadata = {}
    c.model = "Gemma-4-E4B-it-GGUF"
    wrapped = []

    class _Entered(Exception):
        pass

    def survive(self, call, model):
        result = call()
        wrapped.append(result)
        return result

    def follow(self, model=None):
        if model is not None:
            raise _Entered

    monkeypatch.setattr(LemonadeClient, "_survive_gaia_lemonade_stop", survive)
    monkeypatch.setattr(LemonadeClient, "_follow_gaia_lemonade", follow)
    monkeypatch.setattr(
        LemonadeClient,
        "list_models",
        lambda self, show_all=False, timeout=None: {
            "data": [{"id": "Gemma-4-E4B-it-GGUF", "recipe": "llamacpp"}]
        },
    )
    with pytest.raises(_Entered):
        c.chat_completions("Gemma-4-E4B-it-GGUF", [])
    # The guard's catalog lookup (it returns the recipes) went through it.
    assert ["llamacpp"] in wrapped


def test_the_policy_path_follows_gaia_home(tmp_path, monkeypatch):
    monkeypatch.delenv("GAIA_POLICY_FILE", raising=False)
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_CONFIG_DIR", str(tmp_path / "configdir"))
    assert local_only.policy_path() == tmp_path / "home" / "policy.json"


def test_a_corrupt_policy_is_logged_once(tmp_path, monkeypatch, caplog):
    path = tmp_path / "policy.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("GAIA_POLICY_FILE", str(path))
    local_only._last_read_error[0] = None
    with caplog.at_level("ERROR"):
        for _ in range(5):
            assert local_only.cloud_blocked()
    assert (
        sum("Could not read the network policy" in r.message for r in caplog.records)
        == 1
    )


# ── The refusal keeps its words through error classification ───────────────


def test_classification_keeps_the_local_only_message():
    from gaia.llm.providers.lemonade import (
        LemonadeLocalOnlyError,
        classify_lemonade_exception,
    )

    original = CloudDisabledError(
        "Local only is on and GAIA couldn't read Lemonade's model catalog to "
        "confirm that 'x' isn't a cloud model (connection refused)."
    )
    try:
        try:
            raise original
        except CloudDisabledError as e:
            raise RuntimeError("agent step failed") from e
    except RuntimeError as wrapped:
        typed = classify_lemonade_exception(wrapped)
    assert isinstance(typed, LemonadeLocalOnlyError)
    assert typed.retryable is False
    assert typed.user_message == str(original)

    flattened = classify_lemonade_exception(ValueError(f"LLM error: {original}"))
    assert isinstance(flattened, LemonadeLocalOnlyError)
    assert "connection refused" in flattened.user_message


# ── Saved cloud models ─────────────────────────────────────────────────────


def test_switching_on_clears_saved_cloud_models(local_only_on, monkeypatch):
    from gaia import config as config_mod

    config_mod.GAIA_CONFIG_FILE.write_text(
        json.dumps(
            {
                "default_model": "amd.gpt-oss-120b",
                "last_model": "fireworks.glm-5p3-flash",
                "last_provider": "fireworks",
                "profile": "chat",
            }
        ),
        encoding="utf-8",
    )
    cleared = local_only.clear_saved_cloud_models()
    assert cleared == [
        ("default_model", "amd.gpt-oss-120b"),
        ("last_model", "fireworks.glm-5p3-flash"),
    ]
    saved = json.loads(config_mod.GAIA_CONFIG_FILE.read_text(encoding="utf-8"))
    assert saved["default_model"] is None and saved["last_model"] is None
    assert saved["last_provider"] is None and saved["profile"] == "chat"


def test_local_saved_models_are_kept(local_only_on):
    from gaia import config as config_mod

    config_mod.GAIA_CONFIG_FILE.write_text(
        json.dumps({"default_model": "Qwen3.6-35B-A3B-GGUF"}), encoding="utf-8"
    )
    assert local_only.clear_saved_cloud_models() == []
    saved = json.loads(config_mod.GAIA_CONFIG_FILE.read_text(encoding="utf-8"))
    assert saved["default_model"] == "Qwen3.6-35B-A3B-GGUF"


# ── Review round: model switch, SDK sub-resources, keys, bench gateway ──────


def test_the_flagship_model_switch_refuses_a_cloud_model(local_only_on):
    stdio = pytest.importorskip("gaia_agent.stdio")
    switch_model = stdio.switch_model

    for target in ("fireworks.glm-5p3-flash", "amd.gpt-oss-120b", "claude-opus-5"):
        with pytest.raises(RuntimeError, match="Local only is on"):
            switch_model(object(), target)


def test_switching_on_clears_a_saved_claude_model(local_only_on):
    from gaia import config as config_mod

    config_mod.GAIA_CONFIG_FILE.write_text(
        json.dumps({"last_model": "claude-opus-5", "last_provider": "claude"}),
        encoding="utf-8",
    )
    assert local_only.clear_saved_cloud_models() == [("last_model", "claude-opus-5")]


def test_keys_a_running_lemonade_holds_are_taken_back(monkeypatch):
    from gaia.llm import cloud_keys

    calls = []
    monkeypatch.setattr(
        "gaia.llm.lemonade_launcher.gaia_runs_lemonade", lambda *a: True
    )
    monkeypatch.setattr(
        "gaia.llm.lemonade_client.running_embedded_lemonade_url",
        lambda: "http://127.0.0.1:13305/api/v1",
    )
    held = {"fireworks": {"runtime_key_set": True}, "amd": {"runtime_key_set": False}}
    monkeypatch.setattr(cloud_keys, "_provider_entry", lambda base, p: held.get(p))
    monkeypatch.setattr(
        cloud_keys,
        "_request",
        lambda base, method, path, payload=None: calls.append((method, path)) or {},
    )
    assert cloud_keys.drop_runtime_keys() == ["fireworks"]
    assert calls == [("DELETE", "cloud/auth/fireworks")]


def test_no_lemonade_running_means_no_keys_to_take_back(monkeypatch):
    from gaia.llm import cloud_keys

    monkeypatch.setattr(
        "gaia.llm.lemonade_launcher.gaia_runs_lemonade", lambda *a: True
    )
    monkeypatch.setattr(
        "gaia.llm.lemonade_client.running_embedded_lemonade_url", lambda: None
    )
    monkeypatch.setattr(
        cloud_keys,
        "_provider_entry",
        lambda *a: pytest.fail("Lemonade asked while stopped"),
    )
    assert cloud_keys.drop_runtime_keys() == []


@pytest.mark.allow_network  # the bench gateway listens on 127.0.0.1
def test_the_bench_gateway_refuses_a_cloud_model(local_only_on, monkeypatch):
    import http.server
    import threading

    from gaia.eval.bench.gateway import Gateway

    forwarded = []

    class _Upstream(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = json.dumps(
                {"data": [{"id": "fireworks.glm-5p3-flash", "recipe": "cloud"}]}
            ).encode()
            self.send_response(200)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            forwarded.append(self.path)
            self.send_response(200)
            self.send_header("content-length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

    upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{upstream.server_address[1]}/api/v1"
        with Gateway(base, api_key=None) as gw:
            reply = requests.post(
                f"{gw.url}/api/v1/chat/completions",
                json={"model": "fireworks.glm-5p3-flash", "messages": []},
                timeout=10,
            )
        assert reply.status_code == 403
        assert "Local only is on" in reply.json()["error"]["message"]
        assert forwarded == []
    finally:
        upstream.shutdown()
