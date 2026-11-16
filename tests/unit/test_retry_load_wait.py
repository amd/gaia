# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A request stuck behind another app's model load on a shared Lemonade waits
for that load, visibly and within its own limit, instead of the 120 s retry
budget running out first. No test sleeps: the clock is fake.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from gaia.llm import lemonade_client as lc
from gaia.llm import retry

MODEL = "Gemma-4-E4B-it-GGUF"
BIG = "Qwen3-30B-A3B-Instruct-2507-GGUF"


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _health(status):
    entries = [
        {"model_name": MODEL, "type": "llm", "recipe": "llamacpp", "status": "ready"}
    ]
    if status:
        entries.append(
            {"model_name": BIG, "type": "llm", "recipe": "llamacpp", "status": status}
        )
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"status": "ok", "all_models_loaded": entries}
    return response


def _ok():
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"choices": [{"message": {"content": "hi"}}]}
    return response


def _client(clock):
    client = lc.LemonadeClient(base_url="http://127.0.0.1:1/api/v1", verbose=False)
    client._retry_sleep = clock.sleep
    client._retry_clock = clock
    return client


def test_chat_waits_out_another_apps_cold_load():
    """The first attempt spends 150 s queued behind a 30B load, then drops
    when the load evicts the chat model. It is retried once the load ends."""
    clock = FakeClock()
    client = _client(clock)

    def post(*_args, **_kwargs):
        if post.calls == 0:
            post.calls += 1
            clock.now += 150
            raise requests.exceptions.ConnectionError("connection reset by peer")
        return _ok()

    post.calls = 0
    health = iter([_health("loading"), _health("loading"), _health(None)])
    with (
        patch.object(lc.requests, "post", side_effect=post),
        patch.object(lc.requests, "get", side_effect=lambda *a, **k: next(health)),
    ):
        result = client.chat_completions(
            MODEL, [{"role": "user", "content": "hi"}], auto_download=False
        )
    assert result["choices"][0]["message"]["content"] == "hi"
    assert client.last_request_retries == 1


def test_load_wait_is_bounded_and_named():
    clock = FakeClock()
    retrier = retry.ModelCallRetrier(
        retry.RetryPolicy(load_wait=60),
        sleep=clock.sleep,
        clock=clock,
        server_loading=lambda: f"Lemonade is loading {BIG}",
    )

    def call():
        raise requests.exceptions.ConnectionError("reset")

    state = retry.RetryState()
    with pytest.raises(requests.exceptions.ConnectionError) as info:
        retrier.call(call, state=state)
    assert retry.retry_state_of(info.value) is state
    assert state.exhausted and BIG in state.last_reason
    assert 60 <= clock.now <= 61
    assert state.attempts == 1


def test_load_wait_is_announced():
    clock = FakeClock()
    seen = []
    loading = iter([f"Lemonade is loading {BIG}"] * 3 + [None])
    retrier = retry.ModelCallRetrier(
        retry.RetryPolicy(),
        sleep=clock.sleep,
        clock=clock,
        server_loading=lambda: next(loading),
    )
    calls = []

    def call():
        calls.append(1)
        if len(calls) == 1:
            raise requests.exceptions.ConnectionError("reset")
        return "ok"

    with retry.model_retry_notices(seen.append):
        assert retrier.call(call) == "ok"
    assert seen and isinstance(seen[0], retry.RetryNotice)
    assert BIG in seen[0].reason


def test_no_load_reported_keeps_the_deadline():
    """Without a reported load the 120 s budget applies exactly as before."""
    clock = FakeClock()
    retrier = retry.ModelCallRetrier(
        retry.RetryPolicy(),
        sleep=clock.sleep,
        clock=clock,
        server_loading=lambda: None,
    )

    def call():
        clock.now += 150
        raise requests.exceptions.ConnectionError("reset")

    state = retry.RetryState()
    with pytest.raises(requests.exceptions.ConnectionError):
        retrier.call(call, state=state)
    assert state.attempts == 1 and state.exhausted


def test_load_wait_stops_on_cancel():
    clock = FakeClock()
    retrier = retry.ModelCallRetrier(
        retry.RetryPolicy(),
        sleep=clock.sleep,
        clock=clock,
        server_loading=lambda: f"Lemonade is loading {BIG}",
    )

    def call():
        raise requests.exceptions.ConnectionError("reset")

    with retry.model_retry_notices(
        lambda _n: None, is_cancelled=lambda: clock.now >= 10
    ):
        with pytest.raises(requests.exceptions.ConnectionError):
            retrier.call(call)
    assert clock.now < 20


def test_load_wait_env(monkeypatch):
    assert retry.RetryPolicy.from_env().load_wait == retry.DEFAULT_LOAD_WAIT
    monkeypatch.setenv(retry.LOAD_WAIT_ENV, "900")
    assert retry.RetryPolicy.from_env().load_wait == 900
    monkeypatch.setenv(retry.LOAD_WAIT_ENV, "soon")
    with pytest.raises(ValueError, match=retry.LOAD_WAIT_ENV):
        retry.RetryPolicy.from_env()


@pytest.mark.parametrize(
    "status, expected",
    [("loading", f"Lemonade is loading {BIG}"), ("ready", None), (None, None)],
)
def test_server_load_probe(status, expected):
    client = lc.LemonadeClient(base_url="http://127.0.0.1:1/api/v1", verbose=False)
    with patch.object(lc.requests, "get", return_value=_health(status)):
        assert client._server_load_in_progress() == expected


def test_server_load_probe_ignores_cloud_entries():
    client = lc.LemonadeClient(base_url="http://127.0.0.1:1/api/v1", verbose=False)
    response = _health(None)
    response.json.return_value["all_models_loaded"].append(
        {"model_name": "fireworks.x", "recipe": "cloud", "status": "loading"}
    )
    with patch.object(lc.requests, "get", return_value=response):
        assert client._server_load_in_progress() is None


def test_server_load_probe_unreachable_is_unknown():
    client = lc.LemonadeClient(base_url="http://127.0.0.1:1/api/v1", verbose=False)
    with patch.object(
        lc.requests, "get", side_effect=requests.exceptions.ConnectionError("refused")
    ):
        assert client._server_load_in_progress() is None


def test_server_load_probe_unanswered_is_busy():
    client = lc.LemonadeClient(base_url="http://127.0.0.1:1/api/v1", verbose=False)
    with patch.object(
        lc.requests, "get", side_effect=requests.exceptions.ReadTimeout("slow")
    ):
        assert "busy" in client._server_load_in_progress()


def test_slow_probes_count_against_the_load_wait():
    """A probe that itself takes seconds is part of the wait, not free."""
    clock = FakeClock()

    def probe():
        clock.now += 8
        return f"Lemonade is loading {BIG}"

    retrier = retry.ModelCallRetrier(
        retry.RetryPolicy(load_wait=60),
        sleep=clock.sleep,
        clock=clock,
        server_loading=probe,
    )

    def call():
        raise requests.exceptions.ConnectionError("reset")

    with pytest.raises(requests.exceptions.ConnectionError):
        retrier.call(call)
    assert clock.now <= 60 + 8 + 5


def test_cloud_requests_do_not_wait_for_a_local_load():
    client = lc.LemonadeClient(base_url="http://127.0.0.1:1/api/v1", verbose=False)
    assert client._model_retrier("fireworks.some-model")._server_loading is None
    assert client._model_retrier(MODEL)._server_loading is not None
    assert client._model_retrier()._server_loading is None


def test_retries_off_means_off_during_a_load():
    """GAIA_LLM_RETRY_ATTEMPTS=1 is not overridden by a reported load."""
    clock = FakeClock()
    retrier = retry.ModelCallRetrier(
        retry.RetryPolicy(max_attempts=1),
        sleep=clock.sleep,
        clock=clock,
        server_loading=lambda: f"Lemonade is loading {BIG}",
    )
    calls = []

    def call():
        calls.append(1)
        raise requests.exceptions.ConnectionError("reset")

    with pytest.raises(requests.exceptions.ConnectionError):
        retrier.call(call)
    assert len(calls) == 1 and clock.now == 0


# ── Review follow-ups ─────────────────────────────────────────────────────


def test_repeated_loads_never_exceed_the_attempt_cap():
    """Load reported, load ends, request fails again, load reported... The
    attempt cap still holds, and so does one notice per wait."""
    clock = FakeClock()
    flip = {"loading": False}

    def probe():
        flip["loading"] = not flip["loading"]
        return f"Lemonade is loading {BIG}" if flip["loading"] else None

    notices = []
    retrier = retry.ModelCallRetrier(
        retry.RetryPolicy(max_attempts=5),
        sleep=clock.sleep,
        clock=clock,
        server_loading=probe,
    )
    calls = []

    def call():
        calls.append(1)
        raise requests.exceptions.ConnectionError("reset")

    with retry.model_retry_notices(notices.append):
        with pytest.raises(requests.exceptions.ConnectionError):
            retrier.call(call)
    assert len(calls) <= 5
    assert len(notices) <= 5


@pytest.mark.parametrize(
    "entry",
    [
        # Another app's speech or embedding model: not our slot pool.
        {"model_name": "whisper-v3", "type": "transcription", "status": "loading"},
        {"model_name": "embed", "type": "embedding", "status": "loading"},
        # A download is not a load: it holds no slot and can take hours.
        {"model_name": BIG, "type": "llm", "status": "downloading"},
    ],
)
def test_unrelated_activity_is_not_a_load(entry):
    client = lc.LemonadeClient(base_url="http://127.0.0.1:1/api/v1", verbose=False)
    response = _health(None)
    response.json.return_value["all_models_loaded"].append(
        dict(entry, recipe="llamacpp")
    )
    with patch.object(lc.requests, "get", return_value=response):
        assert client._server_load_in_progress() is None


def test_stop_ends_the_load_wait_within_half_a_second():
    clock = FakeClock()
    retrier = retry.ModelCallRetrier(
        retry.RetryPolicy(),
        sleep=clock.sleep,
        clock=clock,
        server_loading=lambda: f"Lemonade is loading {BIG}",
    )

    def call():
        raise requests.exceptions.ConnectionError("reset")

    with retry.model_retry_notices(
        lambda _n: None, is_cancelled=lambda: clock.now >= 0.2
    ):
        with pytest.raises(requests.exceptions.ConnectionError):
            retrier.call(call)
    assert clock.now <= 0.5 + 1e-9


def test_stop_ends_the_busy_server_wait():
    clock = FakeClock()
    client = lc.LemonadeClient(base_url="http://127.0.0.1:1/api/v1", verbose=False)
    client._retry_sleep = clock.sleep
    client._retry_clock = clock
    busy = lc.LemonadeStatus(running=False)
    busy.busy = True
    with (
        patch.object(
            client, "health_check", side_effect=lc.LemonadeUnresponsiveError("silent")
        ),
        patch.object(client, "get_status", return_value=busy),
    ):
        with retry.model_retry_notices(
            lambda _n: None, is_cancelled=lambda: clock.now >= 1
        ):
            with pytest.raises(retry.StreamCancelledError):
                client._wait_while_busy(MODEL)
    assert clock.now <= 1.5


def test_busy_wait_is_announced_not_printed(capsys):
    """A library reports through the retry notice, not stdout/stderr."""
    clock = FakeClock()
    client = lc.LemonadeClient(base_url="http://127.0.0.1:1/api/v1", verbose=False)
    client._retry_sleep = clock.sleep
    client._retry_clock = clock
    busy = lc.LemonadeStatus(running=False)
    busy.busy = True
    ready = lc.LemonadeStatus(running=True)
    notices = []
    silent = lc.LemonadeUnresponsiveError("no answer")
    with (
        patch.object(client, "health_check", side_effect=[silent, {"status": "ok"}]),
        patch.object(client, "get_status", return_value=ready),
    ):
        with retry.model_retry_notices(notices.append):
            assert client._wait_while_busy(MODEL).running
    assert notices and "busy" in notices[0].reason
    assert capsys.readouterr().err == ""


def test_a_slow_load_is_not_labelled_a_busy_server():
    """Only a status probe that goes unanswered means "busy"; a /load that
    times out is the load's own failure."""
    client = lc.LemonadeClient(base_url="http://127.0.0.1:1/api/v1", verbose=False)
    with patch.object(
        lc.requests, "post", side_effect=requests.exceptions.ReadTimeout("slow")
    ):
        with pytest.raises(lc.LemonadeClientError) as info:
            client._send_request("post", f"{client.base_url}/load", {}, timeout=5)
    assert not isinstance(info.value, lc.LemonadeUnresponsiveError)
    assert "busy" not in str(info.value)
