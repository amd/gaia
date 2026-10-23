# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Loading the active adapter, the eval gate, and when a nightly run may start."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import responses

from gaia.learning import serving
from gaia.learning.analyze import Probe
from gaia.learning.config import LearningConfig
from gaia.learning.errors import LearningError
from gaia.learning.evaluate import REGRESSION_PROBES, Score, decide, score
from gaia.learning.gates import check_gates, in_window
from gaia.learning.registry import AdapterRegistry
from gaia.llm.lemonade_client import (
    CHAT_LLAMACPP_ARGS,
    LemonadeClient,
    LemonadeClientError,
)

from .conftest import write_gguf

BASE = "http://localhost:13305/api/v1"
MODEL = "Gemma-4-E4B-it-GGUF"


def _activate(aid="20260101-010000"):
    reg = AdapterRegistry()
    gguf = write_gguf(reg.new_dir(aid) / "adapter.gguf")
    reg.save({"id": aid, "base_model": MODEL, "status": "candidate", "created_at": aid})
    reg.activate(aid)
    return gguf


# -- serving ----------------------------------------------------------------


def test_no_active_adapter_adds_nothing(lhome):
    assert serving.lora_args(MODEL) == ""
    assert serving.chat_llamacpp_args(MODEL, CHAT_LLAMACPP_ARGS) == CHAT_LLAMACPP_ARGS


def test_active_adapter_adds_lora(lhome):
    gguf = _activate()
    assert serving.chat_llamacpp_args(MODEL, "--x 1") == f"--x 1 --lora {gguf}"
    # Only the base it was trained on.
    assert serving.lora_args("Qwen3.6-35B-A3B-GGUF") == ""


def test_missing_active_file_fails_loudly(lhome):
    _activate().unlink()
    with pytest.raises(LearningError, match="gaia learn deactivate"):
        serving.lora_args(MODEL)


@pytest.mark.skipif(
    __import__("os").name == "nt", reason="Windows uses the 8.3 short path"
)
def test_a_path_with_spaces_is_refused(tmp_path):
    p = tmp_path / "with space" / "a.gguf"
    with pytest.raises(LearningError, match="contains a space"):
        serving._arg_path(p)


def _sent_load(client):
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(
            responses.GET,
            f"{BASE}/models",
            json={"data": [{"id": MODEL, "recipe": "llamacpp", "downloaded": True}]},
        )
        with patch.object(
            client, "_post_load_with_transient_retry", return_value={}
        ) as post:
            client.load_model(MODEL, prompt=False, ctx_size=65536)
    return post.call_args.args[1]


def test_lemonade_chat_load_carries_the_active_adapter(lhome):
    """The request Lemonade receives — not just that we called load."""
    client = LemonadeClient(base_url=BASE, verbose=False)
    assert _sent_load(client)["llamacpp_args"] == CHAT_LLAMACPP_ARGS
    gguf = _activate()
    sent = _sent_load(client)
    flags = sent["llamacpp_args"].split()
    assert flags[: len(CHAT_LLAMACPP_ARGS.split())] == CHAT_LLAMACPP_ARGS.split()
    assert flags[flags.index("--lora") + 1] == str(gguf)
    assert sent["ctx_size"] == 65536


def test_lemonade_load_with_a_broken_adapter_raises_a_client_error(lhome):
    _activate().unlink()
    client = LemonadeClient(base_url=BASE, verbose=False)
    with pytest.raises(LemonadeClientError, match="missing its file"):
        _sent_load(client)


class _Client:
    base_url = BASE

    def __init__(self):
        self.calls = []

    def unload_model(self, model, ignore_if_not_loaded=False):
        self.calls.append(("unload", model))

    def load_model(self, model, **kw):
        self.calls.append(("load", model, kw["llamacpp_args"]))


def test_reload_variants(lhome):
    gguf = _activate()
    c = _Client()
    serving.reload_with_adapter(c, MODEL)
    serving.reload_with_adapter(c, MODEL, base_only=True)
    serving.reload_with_adapter(c, MODEL, adapter_gguf=gguf)
    loads = [x[2] for x in c.calls if x[0] == "load"]
    assert "--lora" in loads[0] and "--lora" not in loads[1] and str(gguf) in loads[2]
    assert c.calls[0] == ("unload", MODEL)


# -- eval gate --------------------------------------------------------------


class _ProbeClient:
    """Answers each question from a dict; first_tool probes get a tool call."""

    def __init__(self, answers, tool=None):
        self.answers, self.tool, self.sent = answers, tool, []

    def chat_completions(self, **kw):
        self.sent.append(kw)
        q = kw["messages"][0]["content"]
        if kw.get("tools") and self.tool:
            msg = {
                "content": "",
                "tool_calls": [{"function": {"name": self.tool, "arguments": "{}"}}],
            }
        else:
            msg = {"content": self.answers.get(q, "I don't know.")}
        return {"choices": [{"message": msg}]}


def _general_answers():
    return {p.question: " ".join(p.must_mention) for p in REGRESSION_PROBES}


def test_score_asks_with_thinking_off_and_no_reload():
    probes = [
        Probe("What port?", must_mention=["7443"]),
        Probe(
            "Build?",
            kind="first_tool",
            expect_tool="query_specific_file",
            tools=[{"type": "function"}],
        ),
    ]
    c = _ProbeClient(
        {**_general_answers(), "What port?": "It is 7443."}, tool="query_specific_file"
    )
    s = score(c, MODEL, "x", probes)
    assert (s.learned_passed, s.learned_total) == (2, 2)
    assert s.regression_passed == s.regression_total == len(REGRESSION_PROBES)
    assert all(k["chat_template_kwargs"] == {"enable_thinking": False} for k in c.sent)
    assert all(k["temperature"] == 0.0 and k["auto_download"] is False for k in c.sent)


def _s(label, learned, reg_pass):
    s = Score(label, learned_passed=learned, learned_total=4)
    from gaia.learning.evaluate import ProbeResult

    s.results = [
        ProbeResult(f"g{i}", "regression", ok, "") for i, ok in enumerate(reg_pass)
    ]
    return s


def test_decide_promotes_only_on_a_real_gain():
    base = _s("base model", 1, [True, True, False])
    good = {"base_eval_loss": 2.0, "eval_loss": 0.5}
    assert decide(_s("c", 3, [True, True, False]), base, base, good).promote
    # Lost a general answer the base model gets right.
    d = decide(_s("c", 4, [True, False, True]), base, base, good)
    assert not d.promote and "lost 1 general" in d.reason
    # Held-out loss did not improve.
    assert not decide(
        _s("c", 4, [True, True, True]),
        base,
        base,
        {"base_eval_loss": 1.0, "eval_loss": 1.2},
    ).promote
    # Not better than the adapter already in use.
    incumbent = _s("active adapter", 3, [True, True, False])
    d = decide(_s("c", 3, [True, True, False]), incumbent, base, good)
    assert not d.promote and "no better than the active adapter" in d.reason


# -- gates ------------------------------------------------------------------


@pytest.mark.parametrize(
    "hhmm, start, end, inside",
    [
        ("02:00", "01:00", "06:00", True),
        ("06:00", "01:00", "06:00", False),
        ("23:30", "23:00", "05:00", True),
        ("04:59", "23:00", "05:00", True),
        ("12:00", "23:00", "05:00", False),
    ],
)
def test_window(hhmm, start, end, inside):
    h, m = map(int, hhmm.split(":"))
    assert in_window(datetime(2026, 1, 1, h, m), start, end) is inside


def _gate(**over):
    now = datetime.now().astimezone().replace(hour=2, minute=0)
    kw = dict(
        now=now,
        last_activity=now - timedelta(hours=3),
        last_scheduled_start=None,
        toolchain_ready=True,
        ac_power=True,
    )
    kw.update(over)
    return check_gates(LearningConfig(enabled=True), **kw)


def test_gates_open_at_night_with_the_user_away():
    assert _gate().open


@pytest.mark.parametrize(
    "over, reason",
    [
        ({"last_activity": "recent"}, "used"),
        ({"ac_power": False}, "battery"),
        ({"toolchain_ready": False}, "toolchain"),
        ({"last_scheduled_start": "tonight"}, "already ran"),
    ],
)
def test_each_gate_closes(over, reason):
    now = datetime.now().astimezone().replace(hour=2, minute=0)
    if over.get("last_activity") == "recent":
        over["last_activity"] = now - timedelta(minutes=5)
    if over.get("last_scheduled_start") == "tonight":
        over["last_scheduled_start"] = now.replace(hour=1, minute=15)
    result = _gate(now=now, **over)
    assert not result.open and any(reason in r for r in result.reasons)


def test_last_nights_run_does_not_close_tonight():
    now = datetime.now().astimezone().replace(hour=2, minute=0)
    assert _gate(now=now, last_scheduled_start=now - timedelta(days=1)).open


def test_disabled_is_closed():
    now = datetime.now(timezone.utc)
    r = check_gates(
        LearningConfig(),
        now=now,
        last_activity=None,
        last_scheduled_start=None,
        toolchain_ready=True,
        ac_power=True,
    )
    assert not r.open and "off" in r.reasons[0]


# -- the chat path keeps the right adapter loaded ---------------------------


def _ensure_with_loaded(client, loaded_args):
    from gaia.llm.lemonade_client import LemonadeStatus

    entry = {
        "id": MODEL,
        "recipe": "llamacpp",
        "recipe_options": {"ctx_size": 65536, "llamacpp_args": loaded_args},
    }
    with (
        patch.object(
            client,
            "get_status",
            return_value=LemonadeStatus(running=True, loaded_models=[entry]),
        ),
        patch.object(client, "_find_loaded_entry", return_value=entry),
        patch.object(client, "get_model_max_context_window", return_value=None),
        patch("gaia.llm.lemonade_client.resolve_ctx_size", return_value=65536),
        patch.object(client, "list_models", return_value={"data": []}),
        patch.object(client, "unload_model") as unload,
        patch.object(client, "load_model") as load,
    ):
        client._ensure_model_loaded_locked(MODEL)
    return unload, load


def test_a_model_left_without_the_active_adapter_is_reloaded(lhome):
    """A learning cycle killed mid-run leaves the model loaded base-only."""
    _activate()
    client = LemonadeClient(base_url=BASE, verbose=False)
    unload, load = _ensure_with_loaded(client, CHAT_LLAMACPP_ARGS)
    unload.assert_called_once()
    load.assert_called_once()


def test_a_model_left_on_a_candidate_is_reloaded(lhome, tmp_path):
    client = LemonadeClient(base_url=BASE, verbose=False)
    unload, load = _ensure_with_loaded(
        client, f"{CHAT_LLAMACPP_ARGS} --lora {tmp_path / 'cand.gguf'}"
    )
    unload.assert_called_once()
    load.assert_called_once()


def test_the_right_adapter_already_loaded_is_left_alone(lhome):
    gguf = _activate()
    client = LemonadeClient(base_url=BASE, verbose=False)
    unload, load = _ensure_with_loaded(client, f"{CHAT_LLAMACPP_ARGS} --lora {gguf}")
    unload.assert_not_called()
    load.assert_not_called()


def test_users_without_learning_see_no_reload(lhome):
    client = LemonadeClient(base_url=BASE, verbose=False)
    unload, load = _ensure_with_loaded(client, CHAT_LLAMACPP_ARGS)
    unload.assert_not_called()
    load.assert_not_called()


def test_a_failed_load_with_an_adapter_names_it(lhome):
    gguf = _activate()
    client = LemonadeClient(base_url=BASE, verbose=False)
    with patch.object(
        client,
        "_load_model_leased",
        side_effect=LemonadeClientError("llama-server failed to start"),
    ):
        with pytest.raises(LemonadeClientError) as err:
            client.load_model(MODEL, prompt=False, ctx_size=65536)
    assert str(gguf) in str(err.value) and "gaia learn deactivate" in str(err.value)


def test_a_failed_load_without_an_adapter_is_unchanged(lhome):
    client = LemonadeClient(base_url=BASE, verbose=False)
    with patch.object(
        client, "_load_model_leased", side_effect=LemonadeClientError("boom")
    ):
        with pytest.raises(LemonadeClientError, match="^boom$"):
            client.load_model(MODEL, prompt=False, ctx_size=65536)
