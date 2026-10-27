# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Local only: no GAIA LLM request may reach a cloud model.

The guard is an allowlist over Lemonade's catalog, so these tests try to slip a
cloud request past it: unknown ids, router collections, ids that collide under
Lemonade's lenient matching, positional arguments, method defaults, a catalog
that can't be read, a cold client whose server moved, a toggle mid-session, and
an unreadable policy file. Network calls fail loudly if reached, so a refusal
that happens *after* the request would still fail the test.
"""

import ast
import json
import re
from pathlib import Path

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia.llm import cloud_keys, gateway
from gaia.llm import lemonade_client as lemonade_client_module
from gaia.llm import local_only
from gaia.llm.lemonade_client import LemonadeClient
from gaia.llm.local_only import CloudDisabledError, NetworkPolicy

LOCAL = "Gemma-4-E4B-it-GGUF"
CATALOG = {
    LOCAL: {"id": LOCAL, "recipe": "llamacpp"},
    "nomic-embed-text-v2-moe-GGUF": {
        "id": "nomic-embed-text-v2-moe-GGUF",
        "recipe": "llamacpp",
    },
    "SDXL-Turbo": {"id": "SDXL-Turbo", "recipe": "sd-cpp"},
    "fireworks.glm-5p3-flash": {
        "id": "fireworks.glm-5p3-flash",
        "recipe": "cloud",
        "cloud_provider": "fireworks",
    },
    "amd.gpt-oss-120b": {
        "id": "amd.gpt-oss-120b",
        "recipe": "cloud",
        "cloud_provider": "amd",
    },
    "smart-router": {"id": "smart-router", "recipe": "collection.router"},
    "omni-bundle": {"id": "omni-bundle", "recipe": "collection.omni"},
    "mystery-model": {"id": "mystery-model", "recipe": "brand-new-recipe"},
    # Same name under Lemonade's lenient matching (case, user. prefix).
    "Twin-Model": {"id": "Twin-Model", "recipe": "llamacpp"},
    "user.twin-model": {"id": "user.twin-model", "recipe": "collection.router"},
}


@pytest.fixture
def policy_file(tmp_path, monkeypatch):
    path = tmp_path / "policy.json"
    monkeypatch.setenv("GAIA_POLICY_FILE", str(path))
    return path


@pytest.fixture
def local_only_on(policy_file):
    local_only.set_policy("local_ai_only")
    return policy_file


class _NetworkReached(AssertionError):
    pass


@pytest.fixture
def no_network(monkeypatch):
    """Any HTTP request at all fails the test: refusals must come first."""

    def boom(*args, **kwargs):
        raise _NetworkReached(f"network reached: {args[:2]}")

    for name in ("post", "get", "request"):
        monkeypatch.setattr(requests, name, boom)
    monkeypatch.setattr(requests.Session, "request", boom)
    return boom


class _BodyEntered(Exception):
    """Raised from inside a method body: proof the guard let the call through."""


@pytest.fixture
def follows(monkeypatch):
    """Record ``_follow_gaia_lemonade`` calls. The guard follows with no model
    (so no key replay); ``chat_completions``' body follows with its model, so a
    call carrying a model proves the body was entered."""
    calls = []

    def follow(self, model=None):
        calls.append(model)
        if model is not None:
            raise _BodyEntered

    monkeypatch.setattr(LemonadeClient, "_follow_gaia_lemonade", follow)
    return calls


@pytest.fixture
def client(monkeypatch, follows):
    c = LemonadeClient.__new__(LemonadeClient)
    c._model_metadata = {k: dict(v) for k, v in CATALOG.items()}
    c.model = LOCAL
    c.base_url = "http://127.0.0.1:13305/api/v1"
    monkeypatch.setattr(
        LemonadeClient,
        "list_models",
        lambda self, show_all=False, timeout=None: {"data": list(CATALOG.values())},
    )
    return c


# ── The policy file ────────────────────────────────────────────────────────


def test_no_policy_file_means_cloud_allowed(policy_file):
    assert not policy_file.exists()
    assert local_only.effective_policy() is NetworkPolicy.CLOUD_ALLOWED
    assert not local_only.cloud_blocked()


def test_set_policy_round_trips_and_keeps_other_keys(policy_file):
    policy_file.write_text(json.dumps({"future_key": 7}), encoding="utf-8")
    assert local_only.set_policy("LOCAL_AI_ONLY ") is NetworkPolicy.LOCAL_AI_ONLY
    data = json.loads(policy_file.read_text(encoding="utf-8"))
    assert data == {"future_key": 7, "network_policy": "local_ai_only"}
    assert local_only.cloud_blocked()
    assert not list(policy_file.parent.glob(".policy-*.tmp"))


@pytest.mark.parametrize(
    "content", ["{not json", "[]", '{"network_policy": "sometimes"}', "\x00\x01"]
)
def test_unreadable_policy_fails_closed(policy_file, content):
    policy_file.write_text(content, encoding="utf-8")
    assert local_only.effective_policy() is NetworkPolicy.LOCAL_AI_ONLY


def test_offline_and_unknown_levels_are_refused(policy_file):
    with pytest.raises(ValueError, match="isn't available yet"):
        local_only.set_policy("offline")
    with pytest.raises(ValueError, match="Unknown network_policy"):
        local_only.set_policy("cloud_please")
    assert not policy_file.exists()


def test_offline_written_by_hand_still_blocks_cloud(policy_file):
    policy_file.write_text(json.dumps({"network_policy": "offline"}))
    assert local_only.cloud_blocked()


def test_every_back_to_back_toggle_is_seen(policy_file):
    """Both levels serialize to the same length and Windows mtimes are coarse,
    so a stat-keyed cache would miss some of these. None may be missed."""
    for _ in range(200):
        local_only.set_policy("local_ai_only")
        assert local_only.cloud_blocked()
        local_only.set_policy("cloud_allowed")
        assert not local_only.cloud_blocked()


# ── The allowlist ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "model, why",
    [
        ("fireworks.glm-5p3-flash", "is a cloud model"),
        ("amd.gpt-oss-120b", "is a cloud model"),
        ("smart-router", "can forward requests to the cloud"),
        ("omni-bundle", "can forward requests to the cloud"),
        ("mystery-model", "isn't one GAIA knows"),
        ("vercel.claude-opus-5", "doesn't list it"),
        ("claude-opus-5", "doesn't list it"),
    ],
)
def test_anything_not_confirmed_local_is_refused(
    local_only_on, client, no_network, model, why
):
    with pytest.raises(CloudDisabledError, match=why):
        client.chat_completions(model, [{"role": "user", "content": "hi"}])


@pytest.mark.parametrize("alias", ["twin-model", "TWIN-MODEL", "user.Twin-Model"])
def test_an_alias_matching_a_local_and_a_router_entry_is_refused(
    local_only_on, client, no_network, alias
):
    with pytest.raises(CloudDisabledError, match="several catalog entries"):
        client.chat_completions(alias, [])


def test_an_exact_id_wins_over_lenient_matches(local_only_on, client, follows):
    with pytest.raises(_BodyEntered):
        client.chat_completions("Twin-Model", [])


def test_a_lenient_match_to_one_local_entry_is_allowed(local_only_on, client, follows):
    with pytest.raises(_BodyEntered):
        client.chat_completions("user.gemma-4-e4b-it-gguf", [])


def test_an_entry_without_a_recipe_is_refused(local_only_on):
    with pytest.raises(CloudDisabledError, match="without saying where it runs"):
        local_only.assert_model_permitted("bare-model", lambda m: [None])


def test_a_catalog_that_cannot_be_read_refuses(local_only_on):
    def unreadable(model):
        raise requests.ConnectionError("connection refused")

    with pytest.raises(CloudDisabledError, match="couldn't read"):
        local_only.assert_model_permitted(LOCAL, unreadable)


def test_no_model_named_is_refused(local_only_on):
    with pytest.raises(CloudDisabledError, match="names no model"):
        local_only.assert_model_permitted(None, lambda m: ["llamacpp"])


def test_cloud_allowed_never_consults_the_catalog(policy_file):
    def must_not_run(model):
        raise AssertionError("catalog read while cloud is allowed")

    local_only.assert_model_permitted("fireworks.glm-5p3-flash", must_not_run)


def test_the_error_says_how_to_turn_it_off(local_only_on, client, no_network):
    with pytest.raises(CloudDisabledError) as e:
        client.chat_completions("fireworks.glm-5p3-flash", [])
    assert "gaia config set network_policy cloud_allowed" in str(e.value)
    assert "Settings › Privacy" in str(e.value)


# ── Every inference method, every way of naming the model ─────────────────


def test_keyword_and_positional_models_are_both_checked(
    local_only_on, client, no_network
):
    with pytest.raises(CloudDisabledError):
        client.completions(model="fireworks.glm-5p3-flash", prompt="x")
    with pytest.raises(CloudDisabledError):
        client.completions("fireworks.glm-5p3-flash", "x")
    with pytest.raises(CloudDisabledError):
        client.responses("amd.gpt-oss-120b", "x")


def test_embeddings_check_the_model_they_fall_back_to(
    local_only_on, client, no_network
):
    client.model = "fireworks.glm-5p3-flash"
    with pytest.raises(CloudDisabledError):
        client.embeddings("text")  # no model passed: uses self.model


def test_generate_image_checks_its_default_model(
    local_only_on, client, no_network, monkeypatch
):
    del client._model_metadata["SDXL-Turbo"]
    listed = [m for m in CATALOG.values() if m["id"] != "SDXL-Turbo"]
    monkeypatch.setattr(
        LemonadeClient,
        "list_models",
        lambda self, show_all=False, timeout=None: {"data": listed},
    )
    with pytest.raises(CloudDisabledError, match="SDXL-Turbo"):
        client.generate_image("a cat")


def test_a_local_model_reaches_the_method_body(local_only_on, client, follows):
    with pytest.raises(_BodyEntered):
        client.chat_completions(LOCAL, [{"role": "user", "content": "hi"}])
    assert follows == [None, LOCAL]  # the guard's follow, then the body's


def test_a_cold_client_follows_a_moved_server_before_reading_the_catalog(
    local_only_on, monkeypatch
):
    """Empty cache, Lemonade restarted on a new port: the guard must re-point
    the client first, or its catalog read hits the dead address forever."""
    c = LemonadeClient.__new__(LemonadeClient)
    c._model_metadata = {}
    c.model = LOCAL
    c.base_url = "http://127.0.0.1:1/api/v1"  # the old, dead server
    moved = "http://127.0.0.1:13305/api/v1"
    seen = []

    def follow(self, model=None):
        seen.append(model)
        if len(seen) >= 2:
            raise _BodyEntered
        self.base_url = moved

    def list_models(self, show_all=False, timeout=None):
        assert self.base_url == moved, "catalog read before following the server"
        return {"data": list(CATALOG.values())}

    monkeypatch.setattr(LemonadeClient, "_follow_gaia_lemonade", follow)
    monkeypatch.setattr(LemonadeClient, "list_models", list_models)
    with pytest.raises(_BodyEntered):
        c.chat_completions(LOCAL, [])
    assert seen[0] is None  # no model passed: no cloud key replay


def test_switching_on_mid_session_blocks_the_next_call(
    policy_file, client, no_network, follows
):
    local_only.set_policy("cloud_allowed")
    with pytest.raises(_BodyEntered):
        client.chat_completions("fireworks.glm-5p3-flash", [])
    follows.clear()
    local_only.set_policy("local_ai_only")
    with pytest.raises(CloudDisabledError):
        client.chat_completions("fireworks.glm-5p3-flash", [])


#: Model management talks to Lemonade about models but never runs one, and
#: must keep working under Local only so a local model can still be pulled.
MODEL_MANAGEMENT = {
    "pull_model",
    "pull_model_stream",
    "load_model",
    "unload_model",
    "delete_model",
    "ensure_model_downloaded",
    "download_agent_models",
}
INFERENCE = re.compile(
    r"/(chat/)?completions|/responses|/embeddings|/images/generations|"
    r"/audio/|OpenAI\("
)


def _client_methods():
    """``LemonadeClient``'s methods read from the file: source and decorators.

    Static on purpose: another test may have monkeypatched a method onto the
    class, and that must not change what this guard test sees.
    """
    text = Path(lemonade_client_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(text)
    cls = next(
        n
        for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name == "LemonadeClient"
    )
    methods = {}
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            guarded = any(
                "guard_inference" in ast.get_source_segment(text, d)
                for d in node.decorator_list
            )
            methods[node.name] = (ast.get_source_segment(text, node), guarded)
    return methods


def _methods_reaching_inference():
    """Public methods that hit an inference endpoint, directly or via a helper."""
    methods = _client_methods()
    # A method reaches inference if it names an endpoint itself, or refers to
    # (calls or passes as a callable) a method that does, followed to a fixed
    # point so a chain of private helpers is still caught.
    reaching = {n for n, (src, _) in methods.items() if INFERENCE.search(src)}
    while True:
        grown = {
            n
            for n, (src, _) in methods.items()
            if n not in reaching
            and any(re.search(rf"self\.{h}\b", src) for h in reaching)
        }
        if not grown:
            break
        reaching |= grown
    return {n for n in reaching if not n.startswith("_")}, methods


def test_every_inference_method_is_guarded():
    reaching, methods = _methods_reaching_inference()
    unguarded = sorted(n for n in reaching - MODEL_MANAGEMENT if not methods[n][1])
    assert not unguarded, (
        f"LemonadeClient methods reach an inference endpoint without "
        f"@guard_inference: {unguarded}. Decorate them in lemonade_client.py."
    )
    assert {"chat_completions", "completions", "responses", "embeddings"} <= reaching


def test_model_management_stays_unguarded():
    methods = _client_methods()
    for name in MODEL_MANAGEMENT & methods.keys():
        assert not methods[name][1], name


def test_the_decorator_marks_what_it_wraps():
    for name in ("chat_completions", "completions", "responses", "embeddings"):
        assert getattr(LemonadeClient.__dict__[name], "__local_only_guarded__", False)


# ── Other providers and keys ───────────────────────────────────────────────


def test_claude_provider_cannot_be_built(local_only_on):
    from gaia.llm.providers.claude import ClaudeProvider

    with pytest.raises(CloudDisabledError, match="Anthropic Claude"):
        ClaudeProvider(api_key="sk-test")


def test_a_claude_provider_built_earlier_is_refused_at_the_call(policy_file):
    from gaia.llm.providers.claude import ClaudeProvider

    provider = ClaudeProvider.__new__(ClaudeProvider)

    class _Client:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise AssertionError("Anthropic reached")

    provider._client = _Client()
    provider._build_params = lambda *a, **k: ({}, {}, "")
    provider._resolve_model = lambda m: "claude-opus-5"
    provider._last_ttft_seconds = None
    local_only.set_policy("local_ai_only")
    with pytest.raises(CloudDisabledError):
        provider.chat([{"role": "user", "content": "hi"}])


def test_create_client_claude_is_refused(local_only_on):
    from gaia.llm.factory import create_client

    with pytest.raises(CloudDisabledError):
        create_client("claude", api_key="sk-test")


def test_the_eval_claude_client_is_refused(local_only_on, monkeypatch):
    from gaia.eval.claude import ClaudeClient

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    with pytest.raises(CloudDisabledError, match="Anthropic Claude"):
        ClaudeClient()


def test_no_key_is_replayed_to_lemonade(local_only_on, no_network, monkeypatch):
    monkeypatch.setattr(
        cloud_keys, "recall_key", lambda p: pytest.fail("key read under Local only")
    )
    with pytest.raises(cloud_keys.CloudKeyBlockedError) as e:
        cloud_keys.ensure_authenticated("fireworks")
    # Existing callers catch CloudKeyError and show its message.
    assert isinstance(e.value, cloud_keys.CloudKeyError)
    assert isinstance(e.value, CloudDisabledError)
    assert "Local only is on" in str(e.value)


def test_gateway_token_is_not_handed_over(local_only_on, no_network):
    manager = gateway.GatewayManager.__new__(gateway.GatewayManager)
    with pytest.raises(gateway.GatewayBlockedError):
        manager.set_token("tok")
    with pytest.raises(gateway.GatewayBlockedError):
        manager.ensure_authenticated()
    with pytest.raises(gateway.GatewayBlockedError):
        manager.install("https://gateway.example/v1", api_key="tok")
    assert issubclass(gateway.GatewayBlockedError, gateway.GatewayError)


def test_keys_flow_again_once_cloud_is_allowed(policy_file, monkeypatch):
    local_only.set_policy("cloud_allowed")
    monkeypatch.setattr(cloud_keys, "_check", lambda p: p)
    reached = {}

    class _Stop(Exception):
        pass

    def fake_users_server(base_url):
        reached["past_gate"] = True
        raise _Stop

    monkeypatch.setattr("gaia.llm.lemonade_choice.is_users_server", fake_users_server)

    class _Client:
        def credential_base_url(self):
            return "http://127.0.0.1:13305/api/v1"

    with pytest.raises(_Stop):
        cloud_keys.ensure_authenticated("fireworks", client=_Client())
    assert reached["past_gate"]


# ── Daemon key routes: a refusal is not transient ──────────────────────────

TOKEN = "daemon-client-token"


@pytest.mark.allow_network  # TestClient talks ASGI over a local socket
def test_daemon_key_routes_answer_409_not_503(local_only_on):
    from gaia.daemon.gateway_routes import build_gateway_router
    from gaia.daemon.provider_key_routes import build_provider_key_router

    app = FastAPI()
    app.include_router(build_gateway_router(TOKEN))
    app.include_router(build_provider_key_router(TOKEN))
    http = TestClient(app)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    for path in (
        "/daemon/v1/gateway/authenticate",
        "/daemon/v1/providers/fireworks/authenticate",
    ):
        resp = http.post(path, headers=auth)
        assert resp.status_code == 409, (path, resp.text)
        assert "Local only is on" in resp.json()["detail"]
