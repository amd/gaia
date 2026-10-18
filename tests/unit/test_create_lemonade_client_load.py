# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``create_lemonade_client(auto_load=True)`` fails loudly and never blocks
for hours: a model that could not be downloaded is not then loaded anyway, the
download reports progress and stops on silence or Stop, and the load is bounded
by ``GAIA_LLM_LOAD_WAIT``.
"""

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
