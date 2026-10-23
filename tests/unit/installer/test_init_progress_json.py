# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""`gaia init --progress-json` reports the model download as JSON lines, and
refuses a download the model store cannot hold (#5581).

The desktop installer showed 70% for the whole ~30 GB download because init's
progress only reached its log, and its 5 GB disk check passed on PCs that then
ran out of space mid-download.
"""

import io
import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.installer.init_command import InitCommand, InitEvents, _JsonDownloadProgress
from gaia.llm.model_fit import check_download_fits_disk, model_store_free_gb

GB = 1_000_000_000

CATALOG = {
    "data": [
        {"id": "Qwen3.6-35B-A3B-GGUF", "size": 21.7, "downloaded": False},
        {"id": "Gemma-4-E4B-it-GGUF", "size": 5.56, "downloaded": False},
        {"id": "embeddinggemma-300m-GGUF", "size": 0.311, "downloaded": True},
    ]
}
MODELS = [
    "Qwen3.6-35B-A3B-GGUF",
    "Gemma-4-E4B-it-GGUF",
    "user.embeddinggemma-300m-GGUF",
]


def _system_info(free_gb):
    return {
        "model_storage": {
            "free_bytes": int(free_gb * GB),
            "path": "/models/hub",
        }
    }


def _client(free_gb=500.0):
    client = MagicMock()
    client.list_models.return_value = CATALOG
    client.get_system_info.return_value = _system_info(free_gb)

    def pull(model_id, on_progress=None, **_kwargs):
        on_progress(
            {
                "event": "progress",
                "file": f"{model_id}.gguf",
                "bytes_downloaded": 2 * GB,
                "bytes_total": 4 * GB,
                "total_download_size": 4 * GB,
            }
        )
        on_progress({"event": "complete"})
        return True

    client.ensure_model_downloaded.side_effect = pull
    return client


def _download(cmd, client):
    with (
        patch("gaia.llm.lemonade_client.LemonadeClient", return_value=client),
        patch(
            "gaia.installer.init_command.INIT_PROFILES",
            {"minimal": {"models": MODELS, "agent": "minimal"}},
        ),
    ):
        return cmd._download_models()


def _json_lines(out):
    events = []
    for line in out.splitlines():
        if line.startswith("{"):
            events.append(json.loads(line))
    return events


def test_without_the_flag_no_json_is_printed(capsys):
    assert _download(InitCommand(profile="minimal", yes=True), _client())
    assert _json_lines(capsys.readouterr().out) == []


def test_the_flag_reports_plan_progress_and_done(capsys):
    cmd = InitCommand(profile="minimal", yes=True, progress_json=True)
    assert _download(cmd, _client())
    out = capsys.readouterr().out
    events = _json_lines(out)

    plan = events[0]
    assert plan["event"] == "plan"
    assert plan["bytes_total"] == int((21.7 + 5.56) * GB)
    assert plan["disk_free_bytes"] == 500 * GB
    assert plan["models"][0] == {
        "id": "Qwen3.6-35B-A3B-GGUF",
        "bytes_total": int(21.7 * GB),
        "downloaded": False,
    }
    assert plan["models"][2]["downloaded"] is True

    progress = [e for e in events if e["event"] == "progress"]
    assert progress[0] == {
        "event": "progress",
        "model": "Qwen3.6-35B-A3B-GGUF",
        "index": 1,
        "count": 3,
        "bytes_done": 2 * GB,
        "bytes_total": 4 * GB,
        "speed_bps": 0,
    }
    assert [e["model"] for e in events if e["event"] == "model_done"] == MODELS
    assert events[-1] == {"event": "done", "ok": True}
    # Human output still prints around the JSON.
    assert "Total download: about 27.3 GB" in out


def test_a_failed_pull_reports_an_error_event(capsys):
    client = _client()
    client.ensure_model_downloaded.side_effect = None
    client.ensure_model_downloaded.return_value = False
    cmd = InitCommand(profile="minimal", yes=True, progress_json=True)
    assert not _download(cmd, client)
    events = _json_lines(capsys.readouterr().out)
    errors = [e for e in events if e["event"] == "error"]
    assert errors[0]["model"] == "Qwen3.6-35B-A3B-GGUF"
    assert events[-1] == {"event": "done", "ok": False}


def test_refuses_a_download_the_model_store_cannot_hold(capsys):
    # Each model fits alone; together they need 27.3 GB and 25 GB is free.
    client = _client(free_gb=25.0)
    cmd = InitCommand(profile="minimal", yes=True, progress_json=True)
    assert not _download(cmd, client)
    out = capsys.readouterr().out
    text = " ".join(out.split())
    assert "Not enough disk space" in text
    assert "27.3 GB" in text and "25.0 GB free" in text and "/models/hub" in text
    client.ensure_model_downloaded.assert_not_called()
    events = _json_lines(out)
    assert [e["event"] for e in events] == ["error"]
    assert "27.3 GB" in events[0]["message"]


def test_nothing_to_download_skips_the_disk_check(capsys):
    client = _client(free_gb=0.0)
    client.list_models.return_value = {
        "data": [dict(m, downloaded=True) for m in CATALOG["data"]]
    }
    assert _download(InitCommand(profile="minimal", yes=True), client)
    client.get_system_info.assert_not_called()


def test_an_unreported_free_space_is_said_out_loud(capsys):
    client = _client()
    client.get_system_info.return_value = {}
    assert _download(InitCommand(profile="minimal", yes=True), client)
    out = " ".join(capsys.readouterr().out.split())
    assert "could not check that 27.3 GB fits" in out


def test_check_download_fits_disk():
    assert check_download_fits_disk(10.0, 10.0).fits
    assert check_download_fits_disk(10.0, None).fits
    verdict = check_download_fits_disk(30.0, 12.5, "/hub")
    assert not verdict.fits
    assert "30.0 GB" in verdict.reason and "12.5 GB free" in verdict.reason
    assert "Free up 17.5 GB" in verdict.reason and "/hub" in verdict.reason


def test_model_store_free_gb_reads_lemonade_system_info():
    assert model_store_free_gb(_system_info(12.5)) == (12.5, "/models/hub")
    assert model_store_free_gb({}) == (None, None)
    assert model_store_free_gb({"model_storage": {"free_bytes": "x"}}) == (None, None)


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _tracker(planned=None):
    stream = io.StringIO()
    console = MagicMock()
    clock = _Clock()
    progress = _JsonDownloadProgress(
        console, InitEvents(True, stream), "m", 1, 2, planned, clock=clock
    )
    return progress, stream, clock, console


def _emitted(stream):
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_bytes_add_up_across_a_models_files():
    progress, stream, clock, _ = _tracker(planned=11 * GB)
    clock.now = 1.0
    progress(
        {
            "event": "progress",
            "file": "a",
            "bytes_downloaded": GB,
            "bytes_total": 8 * GB,
        }
    )
    clock.now = 3.0
    progress(
        {
            "event": "progress",
            "file": "b",
            "bytes_downloaded": GB,
            "bytes_total": 3 * GB,
        }
    )
    events = _emitted(stream)
    assert [e["bytes_done"] for e in events] == [GB, 9 * GB]
    assert events[-1]["bytes_total"] == 11 * GB
    # 8 GB over the 2 s since the first event.
    assert events[-1]["speed_bps"] == 4 * GB


def test_progress_lines_are_throttled():
    progress, stream, clock, _ = _tracker(planned=10 * GB)
    for i in range(5):
        clock.now = i * 0.1
        progress(
            {
                "event": "progress",
                "file": "a",
                "bytes_downloaded": i,
                "bytes_total": 10 * GB,
            }
        )
    assert len(_emitted(stream)) == 1
    clock.now = 1.0
    progress(
        {"event": "progress", "file": "a", "bytes_downloaded": 9, "bytes_total": 1}
    )
    assert _emitted(stream)[-1]["bytes_done"] == 9


def test_unknown_size_uses_what_lemonade_reports():
    progress, stream, _, _ = _tracker(planned=None)
    progress(
        {"event": "progress", "file": "a", "bytes_downloaded": 5, "bytes_total": 20}
    )
    assert _emitted(stream)[0]["bytes_total"] == 20


@pytest.mark.parametrize("error", ["Failed to open file for writing", None])
def test_a_stream_error_prints_and_emits(error):
    progress, stream, _, console = _tracker()
    progress({"event": "error", "error": error})
    message = error or "unknown error"
    console.print_download_error.assert_called_once_with(message, "m")
    assert _emitted(stream) == [{"event": "error", "model": "m", "message": message}]
    assert progress.reported_error
