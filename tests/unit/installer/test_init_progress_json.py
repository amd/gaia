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

from gaia.installer.init_command import (
    InitCommand,
    InitEvents,
    _JsonDownloadProgress,
    bytes_already_on_disk,
)
from gaia.llm.lemonade_client import LemonadeClientError, LemonadeUnresponsiveError
from gaia.llm.model_fit import check_download_fits_disk, model_store_free_gb

GB = 1_000_000_000

CATALOG = {
    "data": [
        {
            "id": "Qwen3.6-35B-A3B-GGUF",
            "size": 21.7,
            "downloaded": False,
            "checkpoint": "unsloth/Qwen3.6-35B-A3B-GGUF:Q4_K_M",
        },
        {
            "id": "Gemma-4-E4B-it-GGUF",
            "size": 5.56,
            "downloaded": False,
            "checkpoint": "ggml-org/gemma-4-E4B-it-GGUF:Q4_K_M",
        },
        {"id": "embeddinggemma-300m-GGUF", "size": 0.311, "downloaded": True},
    ]
}
MODELS = [
    "Qwen3.6-35B-A3B-GGUF",
    "Gemma-4-E4B-it-GGUF",
    "user.embeddinggemma-300m-GGUF",
]


def _system_info(free_gb, store="/models/hub"):
    return {
        "model_storage": {
            "free_bytes": int(free_gb * GB),
            "path": str(store),
        }
    }


def _client(free_gb=500.0, store="/models/hub"):
    client = MagicMock()
    client.list_models.return_value = CATALOG
    client.get_system_info.return_value = _system_info(free_gb, store)

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


def test_refuses_a_download_the_model_store_cannot_hold(capsys, tmp_path):
    # Each model fits alone; together they need 27.3 GB and 25 GB is free.
    client = _client(free_gb=25.0, store=tmp_path)
    cmd = InitCommand(profile="minimal", yes=True, progress_json=True)
    assert not _download(cmd, client)
    out = capsys.readouterr().out
    text = " ".join(out.split())
    assert "Not enough disk space" in text
    assert "27.3 GB" in text and "25.0 GB free" in text
    client.ensure_model_downloaded.assert_not_called()
    events = _json_lines(out)
    assert [e["event"] for e in events] == ["error"]
    assert "27.3 GB" in events[0]["message"]
    assert str(tmp_path) in events[0]["message"]


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


@pytest.mark.parametrize(
    "error",
    [
        LemonadeUnresponsiveError("system-info timed out after 15s"),
        LemonadeClientError("Request failed: connection reset"),
    ],
)
def test_a_failing_system_info_warns_and_still_downloads(
    capsys, tmp_path, monkeypatch, error
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    client = _client()
    client.get_system_info.side_effect = error
    cmd = InitCommand(profile="minimal", yes=True, progress_json=True)
    assert _download(cmd, client)
    out = capsys.readouterr().out
    text = " ".join(out.split())
    assert "could not check that 27.3 GB fits" in text
    assert str(error) in text
    assert client.ensure_model_downloaded.call_count == len(MODELS)
    events = _json_lines(out)
    assert events[0]["event"] == "plan" and events[0]["disk_free_bytes"] is None
    assert events[-1] == {"event": "done", "ok": True}


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


# The interrupted-pull tests shrink every size a millionfold (1 "GB" is 1 KB)
# so they write kilobytes, not the gigabytes a real Qwen pull leaves.
MICRO = 1e-6


def _interrupted_pull(store, done_gb, partial_gb, manifest=True):
    """What Lemonade leaves after a Qwen pull stopped partway: finished files,
    one ``.partial``, and the manifest naming them all."""
    snapshot = store / "models--unsloth--Qwen3.6-35B-A3B-GGUF" / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    if manifest:
        (snapshot / ".download_manifest.json").write_text(
            json.dumps({"files": [{"name": "model.gguf"}, {"name": "mmproj.gguf"}]})
        )
    (snapshot / "mmproj.gguf").write_bytes(b"x" * round(done_gb * MICRO * GB))
    (snapshot / "model.gguf.partial").write_bytes(b"x" * round(partial_gb * MICRO * GB))
    return snapshot


def _qwen_only(client):
    qwen = dict(CATALOG["data"][0], size=21.7 * MICRO)
    client.list_models.return_value = {"data": [qwen]}
    return client


def _download_qwen(cmd, client):
    with (
        patch("gaia.llm.lemonade_client.LemonadeClient", return_value=client),
        patch(
            "gaia.installer.init_command.INIT_PROFILES",
            {"minimal": {"models": ["Qwen3.6-35B-A3B-GGUF"], "agent": "minimal"}},
        ),
        patch("gaia.installer.init_command.with_chat_model", lambda _p, ids, _r: ids),
    ):
        return cmd._download_models()


def test_an_interrupted_pull_resumes_when_the_rest_fits(capsys, tmp_path):
    # 12 of 21.7 GB is already on disk; 10 GB free holds the other 9.7 GB.
    _interrupted_pull(tmp_path, done_gb=1.0, partial_gb=11.0)
    client = _qwen_only(_client(free_gb=10.0 * MICRO, store=tmp_path))
    assert _download_qwen(InitCommand(profile="minimal", yes=True), client)
    client.ensure_model_downloaded.assert_called_once()
    assert "disk" not in capsys.readouterr().out.lower()


def test_an_interrupted_pull_is_refused_when_the_rest_does_not_fit(capsys, tmp_path):
    _interrupted_pull(tmp_path, done_gb=1.0, partial_gb=11.0)
    client = _qwen_only(_client(free_gb=5.0 * MICRO, store=tmp_path))
    assert not _download_qwen(InitCommand(profile="minimal", yes=True), client)
    assert "Not enough disk space" in capsys.readouterr().out
    client.ensure_model_downloaded.assert_not_called()


def test_partial_files_count_without_a_manifest(tmp_path):
    _interrupted_pull(tmp_path, done_gb=1.0, partial_gb=11.0, manifest=False)
    client = _qwen_only(_client(store=tmp_path))
    found = bytes_already_on_disk(client, ["Qwen3.6-35B-A3B-GGUF"], str(tmp_path))
    assert found == {"Qwen3.6-35B-A3B-GGUF": round(11 * MICRO * GB)}


@pytest.mark.parametrize("store", ["/not/on/this/pc", "corrupt-manifest"])
def test_unreadable_partials_warn_instead_of_refusing(capsys, tmp_path, store):
    if store == "corrupt-manifest":
        snapshot = _interrupted_pull(tmp_path, done_gb=1.0, partial_gb=11.0)
        (snapshot / ".download_manifest.json").write_text("{not json")
        store = tmp_path
    client = _qwen_only(_client(free_gb=5.0 * MICRO, store=store))
    assert _download_qwen(InitCommand(profile="minimal", yes=True), client)
    text = " ".join(capsys.readouterr().out.split())
    assert "The disk may be too small" in text and "GB free" in text
    client.ensure_model_downloaded.assert_called_once()


def test_registry_size_stands_in_when_the_catalog_has_none(capsys, tmp_path):
    client = _client(store=tmp_path)
    client.list_models.return_value = {
        "data": [{"id": "Qwen3.6-35B-A3B-GGUF", "downloaded": False}]
    }
    cmd = InitCommand(profile="minimal", yes=True, progress_json=True)
    assert _download_qwen(cmd, client)
    plan = _json_lines(capsys.readouterr().out)[0]
    assert plan["models"][0]["bytes_total"] == int(23.3 * GB)


def test_a_bytes_total_of_zero_mid_file_does_not_lose_bytes():
    # Lemonade sends bytes_total 0 between the first and last event of a file.
    progress, stream, clock, _ = _tracker(planned=None)
    for i, (name, done, total) in enumerate(
        [("a", 0, 4 * GB), ("a", 3 * GB, 0), ("a", 4 * GB, 0), ("b", GB, 0)]
    ):
        clock.now = i
        progress(
            {
                "event": "progress",
                "file": name,
                "bytes_downloaded": done,
                "bytes_total": total,
            }
        )
    assert _emitted(stream)[-1]["bytes_done"] == 5 * GB


def test_a_json_line_never_shares_a_line_with_an_open_progress_bar():
    stream = io.StringIO()
    events = InitEvents(True, stream)
    stream.write("\r   [====------] 40% (40.0 MB/100.0 MB)")
    events.line_open = True
    events.emit("step", step=1, total=3, message="x")
    events.emit("done", ok=True)
    lines = stream.getvalue().split("\n")
    assert json.loads(lines[1]) == {
        "event": "step",
        "step": 1,
        "total": 3,
        "message": "x",
    }
    assert json.loads(lines[2]) == {"event": "done", "ok": True}


def test_the_lemonade_download_bar_marks_its_line_open(capsys):
    cmd = InitCommand(profile="minimal", yes=True, progress_json=True)
    cmd._download_progress(40, 100)
    cmd.events.emit("done", ok=True)
    out = capsys.readouterr().out
    assert '\n{"event": "done", "ok": true}\n' in out
