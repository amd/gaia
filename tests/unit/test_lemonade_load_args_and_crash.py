# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A chat load keeps the llama.cpp args a user saved, and a load that crashes
llama-server is not re-sent on every turn (#6221, #6222).

GAIA's two-slot chat flags replaced the args a user had saved for their own
model (``-np 1 --spec-type draft-mtp ...``), llama.cpp b12020 aborted on them,
and every chat turn then re-sent the identical load: 4 attempts per call, each
of which Lemonade itself retries once.
"""

import json

import pytest
import responses

from gaia.llm import lemonade_client as lc
from gaia.llm.lemonade_client import (
    CHAT_LLAMACPP_ARGS,
    EMBEDDER_LLAMACPP_ARGS,
    LemonadeClient,
    LemonadeClientError,
    users_llamacpp_args,
)

BASE = "http://localhost:13305/api/v1"
USER_MODEL = "user.gemma-4-E4B-it-qat-GGUF-UD-Q4_K_XL"
LISTED_AS = "gemma-4-E4B-it-qat-GGUF-UD-Q4_K_XL"  # /models strips ``user.``
USER_ARGS = "--spec-draft-n-max 2 --spec-type draft-mtp -ctk q8_0 -ctv q8_0 -fa 1 -np 1"
STOCK = "Qwen3-30B-A3B-Instruct-2507-GGUF"
CRASH = {
    "error": {"message": "llama-server failed to start", "type": "model_load_error"}
}


def _entry(model_id, recipe="llamacpp", **options):
    entry = {"id": model_id, "recipe": recipe, "downloaded": True}
    if options:
        entry["recipe_options"] = options
    return entry


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(lc, "preferred_llamacpp_backend", lambda: None)
    monkeypatch.setattr(lc.time, "sleep", lambda _s: None)
    monkeypatch.setattr(LemonadeClient, "is_users_server", lambda self: False)


@pytest.fixture
def client():
    return LemonadeClient(base_url=BASE, verbose=False)


def _load_bodies(rsps):
    return [
        json.loads(c.request.body)
        for c in rsps.calls
        if c.request.method == "POST" and c.request.url.endswith("/load")
    ]


def _load(client, model, catalog, status=200, body=None, **kwargs):
    """Run one load against a mocked Lemonade; return (error, /load bodies)."""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(responses.GET, f"{BASE}/models", json={"data": catalog})
        rsps.add(
            responses.POST,
            f"{BASE}/load",
            json=body if body is not None else {"status": "success"},
            status=status,
        )
        error = None
        try:
            client.load_model(model, prompt=False, **kwargs)
        except LemonadeClientError as e:
            error = e
        return error, _load_bodies(rsps)


# ── The user's saved args win over GAIA's chat flags ─────────────────────────


def test_a_model_with_saved_args_loads_with_them_not_the_chat_flags(client):
    error, bodies = _load(
        client,
        USER_MODEL,
        [_entry(LISTED_AS, llamacpp_args=USER_ARGS, ctx_size=65536)],
        ctx_size=65536,
    )
    assert error is None
    assert len(bodies) == 1
    sent = bodies[0]
    # Sent back explicitly: a request without them plus save_options would
    # erase them from recipe_options.json.
    assert sent["llamacpp_args"] == USER_ARGS
    assert "--kv-unified" not in sent["llamacpp_args"]
    assert sent["ctx_size"] == 65536
    # A save replaces the model's whole entry in recipe_options.json.
    assert "save_options" not in sent


def test_a_model_without_user_args_still_saves_its_window(client):
    _, bodies = _load(client, STOCK, [_entry(STOCK)], ctx_size=65536)
    assert bodies[0]["save_options"] is True


def test_args_saved_since_the_last_load_are_read_fresh(client):
    _load(client, STOCK, [_entry(STOCK, llamacpp_args="-np 1")], ctx_size=65536)
    _, bodies = _load(
        client, STOCK, [_entry(STOCK, llamacpp_args="-np 1 -fa 1")], ctx_size=65536
    )
    assert bodies[0]["llamacpp_args"] == "-np 1 -fa 1"


def test_an_unreadable_catalog_fails_the_load_loudly(client, monkeypatch):
    monkeypatch.setattr(client, "_model_recipe", lambda _m: "llamacpp")
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(responses.GET, f"{BASE}/models", status=500, body="boom")
        with pytest.raises(LemonadeClientError, match="saved for"):
            client.load_model(STOCK, ctx_size=65536, prompt=False)
        assert _load_bodies(rsps) == []


def test_the_user_prefix_matches_either_way_round(client):
    _, bodies = _load(
        client,
        LISTED_AS,
        [_entry(USER_MODEL, llamacpp_args=USER_ARGS)],
        ctx_size=32768,
    )
    assert bodies[0]["llamacpp_args"] == USER_ARGS


@pytest.mark.parametrize(
    "options",
    [
        {},  # no recipe_options at all
        {"ctx_size": 65536},  # options saved, but no args
        {"llamacpp_args": ""},
        {"llamacpp_args": "   "},
        {"llamacpp_args": None},
        {"llamacpp_args": 7},  # not a string
        {"llamacpp_args": CHAT_LLAMACPP_ARGS},  # GAIA saved its own flags
        {"llamacpp_args": "  " + CHAT_LLAMACPP_ARGS.replace(" ", "  ") + " "},
        {"llamacpp_args": "--parallel 1"},  # what GAIA saved before the 2 slots
    ],
)
def test_no_user_args_still_gets_the_chat_flags(client, options):
    _, bodies = _load(client, STOCK, [_entry(STOCK, **options)], ctx_size=65536)
    assert bodies[0]["llamacpp_args"] == CHAT_LLAMACPP_ARGS


def test_recipe_options_of_the_wrong_type_is_ignored():
    assert users_llamacpp_args({"recipe_options": "weird"}) is None
    assert users_llamacpp_args(None) is None


def test_another_models_saved_args_are_not_borrowed(client):
    catalog = [_entry("Other-GGUF", llamacpp_args=USER_ARGS), _entry(STOCK)]
    _, bodies = _load(client, STOCK, catalog, ctx_size=65536)
    assert bodies[0]["llamacpp_args"] == CHAT_LLAMACPP_ARGS


def test_the_callers_own_args_still_win_over_saved_ones(client):
    _, bodies = _load(
        client,
        USER_MODEL,
        [_entry(LISTED_AS, llamacpp_args=USER_ARGS)],
        ctx_size=8192,
        llamacpp_args="--ubatch-size 512",
    )
    assert bodies[0]["llamacpp_args"] == "--ubatch-size 512"


def test_a_non_llamacpp_model_gets_no_args(client):
    catalog = [_entry("gemma4-it-e2b-FLM", recipe="flm", llamacpp_args=USER_ARGS)]
    _, bodies = _load(client, "gemma4-it-e2b-FLM", catalog, ctx_size=32768)
    assert "llamacpp_args" not in bodies[0]


def test_a_load_without_ctx_size_sends_no_args(client):
    _, bodies = _load(client, USER_MODEL, [_entry(LISTED_AS, llamacpp_args=USER_ARGS)])
    assert "llamacpp_args" not in bodies[0]


def test_the_embedder_keeps_its_own_args(client, monkeypatch):
    monkeypatch.setattr(lc, "manages_embedder_backend", lambda _m: True)
    monkeypatch.setattr(client, "_default_llamacpp_backend", lambda: "vulkan")
    _, bodies = _load(
        client,
        lc.DEFAULT_EMBEDDING_MODEL,
        [_entry("embeddinggemma-300m-GGUF", llamacpp_args=USER_ARGS)],
        ctx_size=2048,
    )
    assert bodies[0]["llamacpp_args"] == EMBEDDER_LLAMACPP_ARGS


def test_slot_count_reads_one_slot_from_the_users_args(client):
    health = {
        "all_models_loaded": [
            {
                "model_name": USER_MODEL,
                "recipe_options": {"llamacpp_args": USER_ARGS},
            }
        ]
    }
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(responses.GET, f"{BASE}/health", json=health)
        assert client.slot_count(LISTED_AS) == 1


# ── A crashing load fails once, loudly, and is not re-sent every turn ────────


def test_a_crash_names_the_chat_flags_and_how_to_override_them(client):
    error, bodies = _load(
        client, STOCK, [_entry(STOCK)], status=500, body=CRASH, ctx_size=65536
    )
    assert error is not None
    assert len(bodies) == 1 + lc.DEFAULT_MODEL_LOAD_RETRIES
    message = str(error)
    assert "llama-server failed to start" in message
    assert CHAT_LLAMACPP_ARGS in message
    assert "save_options" in message
    assert "amd-gaia.ai/docs/reference/troubleshooting" in message


def test_a_crash_with_the_users_args_says_they_were_used(client):
    error, _ = _load(
        client,
        USER_MODEL,
        [_entry(LISTED_AS, llamacpp_args=USER_ARGS)],
        status=500,
        body=CRASH,
        ctx_size=65536,
    )
    assert USER_ARGS in str(error)
    assert "saved for it in Lemonade" in str(error)
    assert CHAT_LLAMACPP_ARGS not in str(error)


def test_the_same_crashing_load_asked_again_is_refused_unsent(client):
    catalog = [_entry(STOCK)]
    _, bodies = _load(client, STOCK, catalog, status=500, body=CRASH, ctx_size=65536)
    assert len(bodies) == 1 + lc.DEFAULT_MODEL_LOAD_RETRIES
    # Next turn, a fresh client (the UI makes one per request).
    for _ in range(3):
        again, bodies = _load(
            LemonadeClient(base_url=BASE, verbose=False),
            STOCK,
            catalog,
            status=500,
            body=CRASH,
            ctx_size=65536,
        )
        assert bodies == []
        assert "again yet" in str(again)
        assert CHAT_LLAMACPP_ARGS in str(again), "the remedy must carry over"


def test_the_refusal_ends_after_the_cooldown(client, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(lc.time, "monotonic", lambda: now[0])
    catalog = [_entry(STOCK)]
    _load(client, STOCK, catalog, status=500, body=CRASH, ctx_size=65536)
    now[0] += lc.MODEL_LOAD_CRASH_COOLDOWN_S - 1
    _, bodies = _load(client, STOCK, catalog, status=500, body=CRASH, ctx_size=65536)
    assert bodies == []
    now[0] += 1
    _, bodies = _load(client, STOCK, catalog, ctx_size=65536)
    assert len(bodies) == 1


@pytest.mark.parametrize("model, listed", [(USER_MODEL, LISTED_AS), (STOCK, STOCK)])
def test_saving_args_for_the_model_lets_the_load_through_at_once(client, model, listed):
    _load(client, model, [_entry(listed)], status=500, body=CRASH, ctx_size=65536)
    error, bodies = _load(
        client, model, [_entry(listed, llamacpp_args=USER_ARGS)], ctx_size=65536
    )
    assert error is None
    assert [b["llamacpp_args"] for b in bodies] == [USER_ARGS]


def test_each_turns_ensure_load_crashes_llama_server_only_once(client, monkeypatch):
    """The per-turn path: every chat request runs _ensure_model_loaded."""
    monkeypatch.setattr(
        client, "get_status", lambda **_: lc.LemonadeStatus(running=True)
    )
    monkeypatch.setattr(client, "get_model_max_context_window", lambda *a, **k: None)
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(responses.GET, f"{BASE}/models", json={"data": [_entry(STOCK)]})
        rsps.add(responses.POST, f"{BASE}/load", json=CRASH, status=500)
        errors = []
        for _turn in range(3):
            with pytest.raises(LemonadeClientError) as caught:
                client._ensure_model_loaded(STOCK)
            errors.append(str(caught.value))
        assert len(_load_bodies(rsps)) == 1 + lc.DEFAULT_MODEL_LOAD_RETRIES
    assert all(CHAT_LLAMACPP_ARGS in e for e in errors)
    assert all("again yet" in e for e in errors[1:])


@pytest.mark.parametrize(
    "change",
    [
        {"ctx_size": 32768},
        {"llamacpp_args": "--parallel 1"},
    ],
)
def test_a_load_with_different_options_is_not_refused(client, change):
    _load(client, STOCK, [_entry(STOCK)], status=500, body=CRASH, ctx_size=65536)
    kwargs = {"ctx_size": 65536, **change}
    _, bodies = _load(client, STOCK, [_entry(STOCK)], **kwargs)
    assert len(bodies) == 1


def test_a_different_backend_choice_is_not_refused(client, monkeypatch):
    _load(client, STOCK, [_entry(STOCK)], status=500, body=CRASH, ctx_size=65536)
    monkeypatch.setattr(lc, "preferred_llamacpp_backend", lambda: "cpu")
    _, bodies = _load(client, STOCK, [_entry(STOCK)], ctx_size=65536)
    assert [b.get("llamacpp_backend") for b in bodies] == ["cpu"]


def test_another_server_or_model_is_not_refused(client):
    _load(client, STOCK, [_entry(STOCK)], status=500, body=CRASH, ctx_size=65536)
    _, bodies = _load(client, "Other-GGUF", [_entry("Other-GGUF")], ctx_size=65536)
    assert len(bodies) == 1
    other = LemonadeClient(base_url="http://localhost:9999/api/v1", verbose=False)
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(
            responses.GET,
            "http://localhost:9999/api/v1/models",
            json={"data": [_entry(STOCK)]},
        )
        rsps.add(
            responses.POST,
            "http://localhost:9999/api/v1/load",
            json={"status": "success"},
        )
        other.load_model(STOCK, ctx_size=65536, prompt=False)
        assert len(_load_bodies(rsps)) == 1


def test_a_transient_fault_that_clears_is_still_retried_and_not_remembered(client):
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(responses.GET, f"{BASE}/models", json={"data": [_entry(STOCK)]})
        rsps.add(responses.POST, f"{BASE}/load", json=CRASH, status=500)
        rsps.add(responses.POST, f"{BASE}/load", json={"status": "success"})
        client.load_model(STOCK, ctx_size=65536, prompt=False)
        assert len(_load_bodies(rsps)) == 2
    assert lc._CRASHED_LOADS == {}
    _, bodies = _load(client, STOCK, [_entry(STOCK)], ctx_size=65536)
    assert len(bodies) == 1


def test_a_success_clears_an_earlier_crash_of_that_model(client):
    _load(client, STOCK, [_entry(STOCK)], status=500, body=CRASH, ctx_size=65536)
    _load(client, STOCK, [_entry(STOCK)], ctx_size=32768)
    assert lc._CRASHED_LOADS == {}


@pytest.mark.parametrize(
    "body",
    [
        {"error": {"message": "model not found", "type": "not_found"}},
        {"error": {"message": "out of memory", "type": "server_error"}},
    ],
)
def test_other_load_failures_are_not_remembered(client, body):
    error, bodies = _load(
        client, STOCK, [_entry(STOCK)], status=500, body=body, ctx_size=65536
    )
    assert error is not None
    assert len(bodies) == 1, "non-transient failures were never retried"
    assert lc._CRASHED_LOADS == {}


def test_retries_off_still_remembers_the_crash(client):
    _, bodies = _load(
        client,
        STOCK,
        [_entry(STOCK)],
        status=500,
        body=CRASH,
        ctx_size=65536,
        load_retries=0,
    )
    assert len(bodies) == 1
    _, bodies = _load(
        client, STOCK, [_entry(STOCK)], status=500, body=CRASH, ctx_size=65536
    )
    assert bodies == []
