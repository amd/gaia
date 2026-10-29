# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""What GAIA learns about a gateway model it cannot read from the catalog.

The AMD gateway labels no model ``tool-calling``. Taken at face value, that
kept capable models on the in-text tool format.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.llm import lemonade_client as lc

CLOUD = {
    "data": [
        {
            "id": "amd.Opus",
            "recipe": lc.CLOUD_RECIPE,
            "labels": ["cloud", "chat"],
            "context_length": 4096,
        },
        {
            "id": "amd.Flash",
            "recipe": lc.CLOUD_RECIPE,
            "labels": ["cloud", "chat"],
            "context_length": 1000000,
            "max_context_window": 1000000,
        },
        {
            "id": "amd.Labelled",
            "recipe": lc.CLOUD_RECIPE,
            "labels": ["cloud", "tool-calling"],
        },
    ]
}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_GATEWAY_FILE", str(tmp_path / "gateway.json"))
    monkeypatch.setattr(
        "gaia.llm.gateway.GATEWAY_STATE_FILE", tmp_path / "gateway.json"
    )
    monkeypatch.setattr(lc, "_CLOUD_TOOL_CALLING", {})
    monkeypatch.setattr(lc, "_TOOL_CALLING_LOADED", False)
    previous = lc._CLOUD_MODELS
    lc.record_cloud_models(CLOUD)
    yield tmp_path / "gateway.json"
    lc._CLOUD_MODELS = previous


def _client(message, finish_reason="stop"):
    client = MagicMock()
    client.chat_completions.return_value = {
        "choices": [{"message": message, "finish_reason": finish_reason}]
    }
    return client


CALLED = {"tool_calls": [{"function": {"name": "report_ready", "arguments": "{}"}}]}


def test_an_unlabelled_cloud_model_is_not_assumed_native():
    assert lc.is_tool_calling_model("amd.Opus") is False


def test_a_label_is_authoritative_without_a_probe():
    assert lc.is_tool_calling_model("amd.Labelled") is True


def test_a_probe_that_gets_the_call_makes_the_model_native_and_persists(isolated):
    assert lc.probe_cloud_tool_calling(_client(CALLED), "amd.Opus") is True
    assert lc.is_tool_calling_model("amd.Opus") is True
    saved = json.loads(isolated.read_text())
    assert saved["tool_calling_models"] == ["amd.Opus"]


def test_a_probe_answered_in_text_records_no_tool_calling(isolated):
    text = {"content": '<invoke name="report_ready">...</invoke>'}
    assert lc.probe_cloud_tool_calling(_client(text), "amd.Opus") is False
    assert lc.is_tool_calling_model("amd.Opus") is False
    assert json.loads(isolated.read_text())["no_tool_calling_models"] == ["amd.Opus"]


def test_a_call_to_some_other_tool_is_not_support():
    other = {"tool_calls": [{"function": {"name": "rm_rf", "arguments": "{}"}}]}
    assert lc.probe_cloud_tool_calling(_client(other), "amd.Opus") is False


def test_a_network_failure_is_raised_never_recorded_as_no(isolated):
    client = MagicMock()
    client.chat_completions.side_effect = lc.LemonadeClientError("connection refused")
    with pytest.raises(lc.LemonadeClientError):
        lc.probe_cloud_tool_calling(client, "amd.Opus")
    assert lc.learned_tool_calling("amd.Opus") is None
    assert not isolated.exists()


def test_what_was_learned_survives_a_new_process(isolated):
    lc.probe_cloud_tool_calling(_client(CALLED), "amd.Opus")
    lc._CLOUD_TOOL_CALLING.clear()
    lc._TOOL_CALLING_LOADED = False
    assert lc.is_tool_calling_model("amd.Opus") is True


def test_the_manager_probes_once_and_tolerates_a_failed_probe():
    from gaia.llm.lemonade_manager import LemonadeManager

    with patch("gaia.llm.lemonade_client.probe_cloud_tool_calling") as probe:
        LemonadeManager.learn_tool_calling(MagicMock(), "amd.Labelled")
        probe.assert_not_called()  # the label already says
        lc.record_tool_calling("amd.Flash", True)
        LemonadeManager.learn_tool_calling(MagicMock(), "amd.Flash")
        probe.assert_not_called()  # already learned
        probe.side_effect = lc.LemonadeClientError("down")
        LemonadeManager.learn_tool_calling(MagicMock(), "amd.Opus")  # logs, no raise
        probe.assert_called_once()


def test_the_probe_request_is_a_valid_tool_request():
    client = _client(CALLED)
    lc.probe_cloud_tool_calling(client, "amd.Opus")
    kwargs = client.chat_completions.call_args.kwargs
    tool = kwargs["tools"][0]
    assert tool["type"] == "function" and tool["function"]["name"] == "report_ready"
    assert tool["function"]["parameters"]["required"] == ["ready"]
    assert kwargs["stream"] is False and kwargs["auto_download"] is False


@pytest.mark.parametrize(
    "message, finish",
    [
        ({"content": "Let me think about which tool"}, "length"),  # thinking ran out
        ({"content": ""}, "stop"),
        ({}, None),
    ],
)
def test_an_inconclusive_probe_records_nothing(isolated, message, finish):
    assert lc.probe_cloud_tool_calling(_client(message, finish), "amd.Opus") is None
    assert lc.learned_tool_calling("amd.Opus") is None
    assert not isolated.exists()


def test_the_probe_cannot_hang_a_waiting_caller():
    client = _client(CALLED)
    lc.probe_cloud_tool_calling(client, "amd.Opus")
    assert client.chat_completions.call_args.kwargs["timeout"] == lc.PROBE_TIMEOUT_S


@pytest.mark.parametrize(
    "message, finish",
    [
        ({"content": "I can't help with that."}, "content_filter"),
        ({"content": "", "tool_calls": []}, "tool_calls"),  # claims a call, has none
        ({"content": "Sure."}, None),  # no finish reason at all
        ({"content": None}, "stop"),
    ],
)
def test_a_reply_that_is_neither_a_call_nor_a_clean_stop_records_nothing(
    isolated, message, finish
):
    assert lc.probe_cloud_tool_calling(_client(message, finish), "amd.Opus") is None
    assert lc.learned_tool_calling("amd.Opus") is None
    assert lc.is_tool_calling_model("amd.Opus") is False
    assert not isolated.exists()


def test_a_gateway_file_from_an_older_gaia_loads_and_keeps_its_fields(isolated):
    from gaia.llm.gateway import GatewayState

    isolated.write_text(
        json.dumps(
            {
                "base_url": "https://gw.example/v1",
                "enabled_models": ["amd.Opus"],
                "active_model": "amd.Opus",
                "non_streaming_models": ["amd.Flash"],
            }
        ),
        encoding="utf-8",
    )
    state = GatewayState.load()
    assert state.tool_calling_models == [] and state.no_tool_calling_models == []
    assert lc.learned_tool_calling("amd.Opus") is None

    lc.probe_cloud_tool_calling(_client(CALLED), "amd.Opus")
    saved = json.loads(isolated.read_text())
    assert saved["tool_calling_models"] == ["amd.Opus"]
    assert saved["non_streaming_models"] == ["amd.Flash"]
    assert saved["active_model"] == "amd.Opus"


# ---------------------------------------------------------------------------
# A hung or failing gateway: the probe is bounded and its failure remembered
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _failing_gateway_client(monkeypatch, failure, seconds_per_attempt):
    """A real client whose every HTTP attempt takes *seconds_per_attempt* and fails."""
    clock = _Clock()
    attempts = []

    def post(*_args, **kwargs):
        attempts.append(kwargs.get("timeout"))
        clock.advance(seconds_per_attempt)
        raise failure

    client = lc.LemonadeClient(base_url="http://127.0.0.1:9/api/v1", verbose=False)
    client._retry_sleep = clock.advance
    client._retry_clock = clock
    monkeypatch.setattr(lc, "_probe_clock", clock)
    monkeypatch.setattr(lc.requests, "post", post)
    monkeypatch.setattr(client, "_recover_stopped_gaia_lemonade", lambda *a, **k: False)
    return client, clock, attempts


@pytest.mark.parametrize(
    "failure",
    [
        lc.requests.exceptions.ConnectionError("gateway hung up"),
        lc.requests.exceptions.ReadTimeout("no reply"),
    ],
)
def test_a_hung_gateway_holds_the_probe_for_a_bounded_total(monkeypatch, failure):
    client, clock, attempts = _failing_gateway_client(
        monkeypatch, failure, lc.PROBE_TIMEOUT_S
    )
    with pytest.raises(lc.LemonadeClientError):
        lc.probe_cloud_tool_calling(client, "amd.Opus")
    # Not the client's five attempts: one, and at most one retry begun in time.
    assert 1 <= len(attempts) <= 2
    assert clock.now - 1000.0 <= 2 * lc.PROBE_TIMEOUT_S + 1
    assert lc.learned_tool_calling("amd.Opus") is None


def test_quick_failures_stop_retrying_once_the_budget_is_spent(monkeypatch):
    client, clock, _attempts = _failing_gateway_client(
        monkeypatch, lc.requests.exceptions.ConnectionError("refused"), 0.1
    )
    with pytest.raises(lc.LemonadeClientError):
        lc.probe_cloud_tool_calling(client, "amd.Opus")
    assert clock.now - 1000.0 <= 2 * lc.PROBE_TIMEOUT_S + 1


def test_stop_pressed_by_the_caller_still_ends_the_probe(monkeypatch):
    from gaia.llm.retry import model_retry_notices

    client, _clock, attempts = _failing_gateway_client(
        monkeypatch, lc.requests.exceptions.ConnectionError("refused"), 0.1
    )
    with model_retry_notices(lambda n: None, is_cancelled=lambda: True):
        with pytest.raises(lc.LemonadeClientError):
            lc.probe_cloud_tool_calling(client, "amd.Opus")
    assert len(attempts) == 1


def _manager_client(replay=True):
    client = MagicMock()
    client._replay_cloud_key.return_value = replay
    return client


def _forget_in_memory():
    lc._CLOUD_TOOL_CALLING.clear()
    lc._CLOUD_PROBE_FAILURES.clear()
    lc._TOOL_CALLING_LOADED = False


@pytest.fixture
def manager(monkeypatch):
    from gaia.llm.lemonade_manager import LemonadeManager

    monkeypatch.setattr(lc, "_CLOUD_PROBE_FAILURES", {})
    monkeypatch.setattr(LemonadeManager, "_probe_skip_reported", set())
    return LemonadeManager


def _down(**kwargs):
    return patch(
        "gaia.llm.lemonade_client.probe_cloud_tool_calling",
        side_effect=lc.LemonadeClientError("gateway down"),
        **kwargs,
    )


def test_a_failed_probe_is_not_repeated_in_this_process_or_the_next(manager, isolated):
    with _down() as probe:
        manager.learn_tool_calling(_manager_client(), "amd.Opus")
        manager.learn_tool_calling(_manager_client(), "amd.Opus")
        assert probe.call_count == 1
        _forget_in_memory()  # a new process reads gateway.json
        manager.learn_tool_calling(_manager_client(), "amd.Opus")
        assert probe.call_count == 1
    saved = json.loads(isolated.read_text())
    assert "amd.Opus" in saved["tool_calling_probe_failures"]
    assert lc.learned_tool_calling("amd.Opus") is None  # never recorded as "no"


def test_a_failed_probe_is_tried_again_after_the_window(manager, monkeypatch):
    now = [5_000_000.0]
    monkeypatch.setattr(lc.time, "time", lambda: now[0])
    with _down() as probe:
        manager.learn_tool_calling(_manager_client(), "amd.Opus")
        now[0] += lc.PROBE_RETRY_AFTER_S - 1
        manager.learn_tool_calling(_manager_client(), "amd.Opus")
        assert probe.call_count == 1
        now[0] += 2
        manager.learn_tool_calling(_manager_client(), "amd.Opus")
        assert probe.call_count == 2


def test_one_models_failure_does_not_hold_back_another(manager):
    with _down() as probe:
        manager.learn_tool_calling(_manager_client(), "amd.Opus")
        manager.learn_tool_calling(_manager_client(), "amd.Flash")
    assert probe.call_count == 2


def test_an_inconclusive_probe_is_remembered_like_a_failure(manager):
    with patch(
        "gaia.llm.lemonade_client.probe_cloud_tool_calling", return_value=None
    ) as probe:
        manager.learn_tool_calling(_manager_client(), "amd.Opus")
        manager.learn_tool_calling(_manager_client(), "amd.Opus")
    assert probe.call_count == 1


def test_a_success_clears_an_old_failure(manager, isolated, monkeypatch):
    now = [5_000_000.0]
    monkeypatch.setattr(lc.time, "time", lambda: now[0])
    with _down():
        manager.learn_tool_calling(_manager_client(), "amd.Opus")
    now[0] += lc.PROBE_RETRY_AFTER_S + 1
    client = _client(CALLED)
    client._replay_cloud_key.return_value = True
    manager.learn_tool_calling(client, "amd.Opus")
    saved = json.loads(isolated.read_text())
    assert saved["tool_calling_models"] == ["amd.Opus"]
    assert saved["tool_calling_probe_failures"] == {}


def test_a_failed_probe_is_logged_with_when_it_retries(manager, caplog):
    with _down():
        with caplog.at_level("WARNING"):
            manager.learn_tool_calling(_manager_client(), "amd.Opus")
            manager.learn_tool_calling(_manager_client(), "amd.Opus")
    text = caplog.text
    assert "gateway down" in text and "retried in 60 minutes" in text
    assert "failed within the last" in text  # the skip is said, not silent


def test_the_key_is_replayed_before_the_probe(manager):
    order = []
    client = MagicMock()
    client._replay_cloud_key.side_effect = lambda m: order.append("replay") or True
    with patch(
        "gaia.llm.lemonade_client.probe_cloud_tool_calling",
        side_effect=lambda c, m: order.append("probe") or True,
    ):
        manager.learn_tool_calling(client, "amd.Opus")
    assert order == ["replay", "probe"]


def test_no_usable_key_skips_the_probe_and_records_nothing(manager, isolated):
    with patch("gaia.llm.lemonade_client.probe_cloud_tool_calling") as probe:
        manager.learn_tool_calling(_manager_client(replay=False), "amd.Opus")
        probe.assert_not_called()
    assert not lc.probe_recently_failed("amd.Opus")
    assert not isolated.exists()


def test_ensure_ready_replays_the_key_before_it_probes():
    # The cloud ready path: the key a fresh server lacks comes first.
    import inspect

    from gaia.llm.lemonade_manager import LemonadeManager

    src = inspect.getsource(LemonadeManager.ensure_ready)
    assert src.index("_replay_cloud_key(chat_model)") < src.index(
        "learn_tool_calling(client, chat_model, key_replayed=True)"
    )


def test_an_unreadable_failure_time_is_ignored_not_fatal(isolated):
    isolated.write_text(
        json.dumps({"tool_calling_probe_failures": {"amd.Opus": "yesterday"}}),
        encoding="utf-8",
    )
    assert not lc.probe_recently_failed("amd.Opus")


# ---------------------------------------------------------------------------
# gateway.json is replaced whole, never left half-written
# ---------------------------------------------------------------------------


def test_a_failed_write_leaves_the_old_file_and_no_temp(isolated, monkeypatch):
    from gaia.llm.gateway import GatewayState

    GatewayState(active_model="amd.Opus").save()
    before = isolated.read_text(encoding="utf-8")

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr("gaia.daemon.paths.os.replace", boom)
    with pytest.raises(OSError):
        GatewayState(active_model="amd.Flash").save()
    assert isolated.read_text(encoding="utf-8") == before
    assert [p.name for p in isolated.parent.iterdir()] == [isolated.name]


def test_a_write_interrupted_mid_payload_leaves_the_old_file(isolated, monkeypatch):
    from gaia.llm.gateway import GatewayState

    GatewayState(active_model="amd.Opus").save()
    before = isolated.read_text(encoding="utf-8")

    def interrupted(*_a, **_k):
        raise KeyboardInterrupt()

    monkeypatch.setattr("gaia.llm.gateway.json.dumps", interrupted)
    with pytest.raises(KeyboardInterrupt):
        GatewayState(active_model="amd.Flash").save()
    assert isolated.read_text(encoding="utf-8") == before
    assert len(list(isolated.parent.iterdir())) == 1


def test_concurrent_writers_never_leave_a_torn_file(isolated):
    import threading

    from gaia.llm.gateway import GatewayState

    errors = []

    def write(i):
        for _ in range(20):
            try:
                GatewayState(enabled_models=[f"m{i}"] * 200).save()
            except PermissionError:
                pass  # Windows: two renames racing; neither tears the file
            except Exception as e:  # noqa: BLE001 - asserted below
                errors.append(e)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    json.loads(isolated.read_text(encoding="utf-8"))  # whole, parseable
    assert [p.name for p in isolated.parent.iterdir()] == [isolated.name]


def test_stop_during_the_probe_is_not_remembered_as_a_failure(manager, monkeypatch):
    from gaia.llm.retry import model_retry_notices

    client, _clock, _attempts = _failing_gateway_client(
        monkeypatch, lc.requests.exceptions.ConnectionError("refused"), 0.1
    )
    monkeypatch.setattr(client, "_replay_cloud_key", lambda model: True)
    with model_retry_notices(lambda n: None, is_cancelled=lambda: True):
        manager.learn_tool_calling(client, "amd.Opus")
    assert not lc.probe_recently_failed("amd.Opus")
    assert lc.learned_tool_calling("amd.Opus") is None


def test_a_stopped_probe_raises_its_own_error(monkeypatch):
    from gaia.llm.retry import model_retry_notices

    client, _clock, _attempts = _failing_gateway_client(
        monkeypatch, lc.requests.exceptions.ConnectionError("refused"), 0.1
    )
    with model_retry_notices(lambda n: None, is_cancelled=lambda: True):
        with pytest.raises(lc.ToolCallingProbeStopped):
            lc.probe_cloud_tool_calling(client, "amd.Opus")


def test_a_key_already_replayed_is_not_replayed_again(manager):
    client = _manager_client()
    with patch("gaia.llm.lemonade_client.probe_cloud_tool_calling", return_value=True):
        manager.learn_tool_calling(client, "amd.Opus", key_replayed=True)
    client._replay_cloud_key.assert_not_called()


def test_a_reader_holding_the_file_briefly_does_not_fail_the_save(
    isolated, monkeypatch
):
    from gaia.daemon import paths
    from gaia.llm.gateway import GatewayState

    GatewayState(active_model="amd.Opus").save()
    real_replace = paths.os.replace
    denials = [PermissionError("in use"), PermissionError("in use")]

    def busy_then_free(src, dst):
        if denials:
            raise denials.pop()
        real_replace(src, dst)

    monkeypatch.setattr(paths, "_is_windows", lambda: True)
    monkeypatch.setattr(paths.os, "replace", busy_then_free)
    monkeypatch.setattr(paths.time, "sleep", lambda s: None)
    GatewayState(active_model="amd.Flash").save()
    assert json.loads(isolated.read_text())["active_model"] == "amd.Flash"
    assert [p.name for p in isolated.parent.iterdir()] == [isolated.name]
