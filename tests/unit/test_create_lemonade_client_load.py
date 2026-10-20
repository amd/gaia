# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``create_lemonade_client(auto_load=True)`` fails loudly and never blocks
for hours: a model that could not be downloaded is not then loaded anyway, the
download reports progress and stops on silence or Stop, and the load is bounded
by ``GAIA_LLM_LOAD_WAIT``.
"""

import threading
import time
from unittest.mock import patch

import pytest

from gaia.llm import lemonade_client as lc
from gaia.llm import retry

MODEL = "Gemma-4-E4B-it-GGUF"


def _patches(pull_events, available=()):
    return (
        patch.object(
            lc.LemonadeClient,
            "list_models",
            return_value={"data": [{"id": m} for m in available]},
        ),
        patch.object(
            lc.LemonadeClient, "pull_model_stream", return_value=iter(pull_events)
        ),
        patch.object(lc.LemonadeClient, "_is_cloud_model", return_value=False),
    )


def test_failed_pull_is_not_followed_by_a_load():
    events = [{"event": "error", "error": "HTTP 404: no such checkpoint"}]
    listing, pull, cloud = _patches(events)
    with listing, pull, cloud, patch.object(lc.LemonadeClient, "load_model") as load:
        with pytest.raises(lc.LemonadeClientError, match="could not download it"):
            lc.create_lemonade_client(model=MODEL, auto_load=True, verbose=False)
    load.assert_not_called()


def test_a_pull_that_never_completes_is_not_followed_by_a_load():
    events = [{"event": "progress", "percent": 40}]
    listing, pull, cloud = _patches(events)
    with listing, pull, cloud, patch.object(lc.LemonadeClient, "load_model") as load:
        with pytest.raises(lc.LemonadeClientError, match="did not finish"):
            lc.create_lemonade_client(model=MODEL, auto_load=True, verbose=False)
    load.assert_not_called()


def test_the_download_goes_through_the_progress_stream():
    """The streamed pull logs progress and ends on silence; the one-shot pull
    it replaces did neither."""
    events = [{"event": "progress", "percent": 50}, {"event": "complete"}]
    listing, pull, cloud = _patches(events)
    consume = lc.LemonadeClient._consume_pull_stream
    with (
        listing,
        pull,
        cloud,
        patch.object(lc.LemonadeClient, "load_model"),
        patch.object(lc.LemonadeClient, "pull_model") as one_shot,
        patch.object(
            lc.LemonadeClient,
            "_consume_pull_stream",
            autospec=True,
            side_effect=consume,
        ) as streamed,
    ):
        lc.create_lemonade_client(model=MODEL, auto_load=True, verbose=False)
    streamed.assert_called_once()
    one_shot.assert_not_called()


def test_stop_ends_the_download():
    events = iter(
        [{"event": "progress", "percent": p} for p in range(0, 100, 10)]
        + [{"event": "complete"}]
    )
    seen = []

    def tracked():
        for event in events:
            seen.append(event)
            yield event

    listing, _pull, cloud = _patches([])
    with (
        listing,
        cloud,
        patch.object(lc.LemonadeClient, "pull_model_stream", return_value=tracked()),
        patch.object(lc.LemonadeClient, "load_model") as load,
        retry.model_retry_notices(lambda _n: None, is_cancelled=lambda: len(seen) >= 2),
    ):
        with pytest.raises(lc.ModelDownloadCancelledError):
            lc.create_lemonade_client(model=MODEL, auto_load=True, verbose=False)
    assert len(seen) == 2
    load.assert_not_called()


def test_the_load_is_bounded_by_the_load_wait(monkeypatch):
    """Not 60 s (a load queued behind another app's needs more), and not the
    12000 s download-sized default either."""
    monkeypatch.setenv("GAIA_LLM_LOAD_WAIT", "900")
    listing, pull, cloud = _patches([], available=[MODEL])
    with listing, pull, cloud, patch.object(lc.LemonadeClient, "load_model") as load:
        lc.create_lemonade_client(model=MODEL, auto_load=True, verbose=False)
    load.assert_called_once_with(MODEL, timeout=900)


def test_fail_fast_load_wait_still_loads(monkeypatch):
    """GAIA_LLM_LOAD_WAIT=0 means "don't wait on a busy server", not "send a
    load with no time to run": a zero timeout is rejected outright."""
    monkeypatch.setenv("GAIA_LLM_LOAD_WAIT", "0")
    listing, pull, cloud = _patches([], available=[MODEL])
    with listing, pull, cloud, patch.object(lc.LemonadeClient, "load_model") as load:
        lc.create_lemonade_client(model=MODEL, auto_load=True, verbose=False)
    assert load.call_args.kwargs["timeout"] >= 60


def test_stop_is_not_logged_as_a_load_failure(caplog):
    """A deliberate Stop is a cancellation, not an error in the log."""
    events = iter([{"event": "progress", "percent": p} for p in range(0, 100, 10)])
    listing, _pull, cloud = _patches([])
    with (
        listing,
        cloud,
        patch.object(lc.LemonadeClient, "pull_model_stream", return_value=events),
        patch.object(lc.LemonadeClient, "load_model") as load,
        retry.model_retry_notices(lambda _n: None, is_cancelled=lambda: True),
        caplog.at_level("ERROR"),
    ):
        with pytest.raises(lc.ModelDownloadCancelledError):
            lc.create_lemonade_client(model=MODEL, auto_load=True, verbose=False)
    load.assert_not_called()
    assert not [r for r in caplog.records if "Failed to load" in r.getMessage()]


def test_a_real_load_failure_is_still_logged(caplog):
    listing, pull, cloud = _patches([], available=[MODEL])
    with (
        listing,
        pull,
        cloud,
        patch.object(
            lc.LemonadeClient,
            "load_model",
            side_effect=lc.InsufficientMemoryError("not enough memory"),
        ),
        caplog.at_level("ERROR"),
    ):
        with pytest.raises(lc.InsufficientMemoryError):
            lc.create_lemonade_client(model=MODEL, auto_load=True, verbose=False)
    assert [r for r in caplog.records if "Failed to load" in r.getMessage()]


class _DroppingResponse:
    """A /pull reply whose stream dies mid-read with ``error``."""

    status_code = 200

    def __init__(self, error):
        self.error = error
        self.closed = False

    def iter_lines(self):
        yield b"event: progress"
        yield b'data: {"percent": 5}'
        raise self.error

    def close(self):
        self.closed = True


@pytest.mark.parametrize(
    "error",
    [ConnectionResetError("reset by peer"), TimeoutError("timed out"), OSError(5)],
)
def test_a_socket_error_mid_download_names_the_fix(error):
    """A raw socket error that urllib3 did not wrap still reaches the caller
    as a LemonadeClientError carrying the download guidance."""
    reply = _DroppingResponse(error)
    with (
        patch.object(lc.LemonadeClient, "list_models", return_value={"data": []}),
        patch.object(lc.LemonadeClient, "_is_cloud_model", return_value=False),
        patch.object(lc.requests, "post", return_value=reply),
        patch.object(lc.LemonadeClient, "load_model") as load,
    ):
        with pytest.raises(lc.LemonadeClientError, match="could not download it") as e:
            lc.create_lemonade_client(model=MODEL, auto_load=True, verbose=False)
    assert not isinstance(e.value, lc.ModelDownloadCancelledError)
    load.assert_not_called()
    assert reply.closed


def test_a_socket_error_from_pull_model_stream_is_a_client_error():
    reply = _DroppingResponse(ConnectionResetError("reset by peer"))
    client = lc.LemonadeClient(verbose=False)
    with patch.object(lc.requests, "post", return_value=reply):
        with pytest.raises(lc.LemonadeClientError, match="connection lost") as e:
            list(client.pull_model_stream(MODEL))
    assert isinstance(e.value.__cause__, ConnectionResetError)


def test_a_programming_error_mid_download_is_not_disguised():
    """Only socket errors are wrapped; a bug still surfaces as itself."""
    reply = _DroppingResponse(ValueError("bad state"))
    client = lc.LemonadeClient(verbose=False)
    with patch.object(lc.requests, "post", return_value=reply):
        with pytest.raises(ValueError, match="bad state"):
            list(client.pull_model_stream(MODEL))


class _StuckResponse:
    """A /pull reply that sends one event, then blocks in a read that hanging
    up does not wake (as a socket read may not, depending on the OS)."""

    status_code = 200

    def __init__(self, then=None):
        self.release = threading.Event()
        self.then = then or []
        self.closed = threading.Event()

    def iter_lines(self):
        yield b"event: progress"
        yield b'data: {"percent": 5}'
        self.release.wait(30)
        yield from self.then

    def close(self):
        self.closed.set()


def test_stop_ends_a_pull_whose_read_never_wakes():
    reply = _StuckResponse()
    client = lc.LemonadeClient(verbose=False)
    seen = []
    stop_at = time.monotonic() + 1.0
    started = time.monotonic()
    try:
        with (
            patch.object(lc.requests, "post", return_value=reply),
            retry.model_retry_notices(
                lambda _n: None, is_cancelled=lambda: time.monotonic() >= stop_at
            ),
        ):
            with pytest.raises(lc.ModelDownloadCancelledError):
                for event in client.pull_model_stream(MODEL):
                    seen.append(event)
        assert time.monotonic() - started < 3
        assert [e["percent"] for e in seen] == [5]
        assert not reply.closed.is_set()  # the blocked reader still owns it
    finally:
        reply.release.set()
    assert reply.closed.wait(5)  # closed once its read ends


def test_a_pull_read_on_a_worker_still_completes():
    reply = _StuckResponse(then=[b"event: complete", b'data: {"percent": 100}'])
    reply.release.set()
    client = lc.LemonadeClient(verbose=False)
    with (
        patch.object(lc.requests, "post", return_value=reply),
        retry.model_retry_notices(lambda _n: None, is_cancelled=lambda: False),
    ):
        events = list(client.pull_model_stream(MODEL))
    assert [e["event"] for e in events] == ["progress", "complete"]
    assert reply.closed.wait(5)


@pytest.mark.parametrize(
    "error, expected",
    [
        (ConnectionResetError("reset"), "connection lost"),
        (lc.requests.exceptions.ConnectionError("read timed out"), "read timed out"),
    ],
)
def test_a_read_error_on_the_worker_reaches_the_caller(error, expected):
    reply = _DroppingResponse(error)
    client = lc.LemonadeClient(verbose=False)
    with (
        patch.object(lc.requests, "post", return_value=reply),
        retry.model_retry_notices(lambda _n: None, is_cancelled=lambda: False),
    ):
        with pytest.raises(lc.LemonadeClientError, match=expected) as e:
            list(client.pull_model_stream(MODEL))
    assert not isinstance(e.value, lc.ModelDownloadCancelledError)
