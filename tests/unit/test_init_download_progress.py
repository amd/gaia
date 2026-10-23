"""`gaia init` shows a model pull as it happens, and says why it failed."""

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.installer.init_command import _DownloadProgress
from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError


@pytest.fixture
def client():
    c = LemonadeClient(base_url="http://127.0.0.1:1/api/v1", verbose=False)
    c.list_models = MagicMock(return_value={"data": []})
    c.pull_model = MagicMock()
    return c


def test_streams_events_to_the_caller_instead_of_blocking(client):
    events = [
        {"event": "progress", "percent": 40, "bytes_downloaded": 4, "bytes_total": 10},
        {"event": "complete", "percent": 100},
    ]
    client.pull_model_stream = MagicMock(return_value=iter(events))
    seen = []
    assert client.ensure_model_downloaded(
        "Gemma-4-E4B-it-GGUF", on_progress=seen.append
    )
    assert seen == events
    client.pull_model.assert_not_called()


def test_a_failed_stream_is_reported_once_with_its_reason(client):
    def stream(*_a, **_k):
        yield {"event": "progress", "percent": 0}
        raise LemonadeClientError("Read timed out")

    client.pull_model_stream = stream
    seen = []
    assert not client.ensure_model_downloaded("m", on_progress=seen.append)
    assert seen[-1] == {"event": "error", "error": "Read timed out"}


def test_a_server_error_event_is_not_reported_twice(client):
    def stream(*_a, **_k):
        yield {"event": "error", "error": "Failed to open file for writing"}
        raise LemonadeClientError("Failed to open file for writing")

    client.pull_model_stream = stream
    seen = []
    assert not client.ensure_model_downloaded("m", on_progress=seen.append)
    assert [e["event"] for e in seen] == ["error"]


def test_already_downloaded_streams_nothing(client):
    client.list_models.return_value = {"data": [{"id": "m", "downloaded": True}]}
    client.pull_model_stream = MagicMock()
    seen = []
    assert client.ensure_model_downloaded("m", on_progress=seen.append)
    assert seen == []
    client.pull_model_stream.assert_not_called()


@pytest.mark.parametrize(
    "kwargs, forbidden, required",
    [
        ({}, {"recipe", "checkpoint", "embedding"}, set()),
        (
            {
                "checkpoint": "ggml-org/embeddinggemma-300M-GGUF:Q8_0",
                "recipe": "llamacpp",
                "embedding": True,
            },
            set(),
            {"recipe", "checkpoint", "embedding"},
        ),
    ],
)
def test_the_streamed_pull_request_is_valid(client, kwargs, forbidden, required):
    """A built-in pulls by name only (#1655); a user. model registers itself."""
    response = MagicMock(status_code=200)
    response.iter_lines.return_value = [b"event: complete", b'data: {"percent": 100}']
    with patch("gaia.llm.lemonade_client.requests.post", return_value=response) as post:
        assert client.ensure_model_downloaded("m", on_progress=lambda e: None, **kwargs)
    body = post.call_args.kwargs["json"]
    assert body["stream"] is True
    assert not forbidden & body.keys()
    assert required <= body.keys()


def test_progress_redraws_are_throttled_and_skip_empty_files():
    console = MagicMock()
    progress = _DownloadProgress(console, "m")
    for n in range(500):
        progress({"event": "progress", "percent": 0, "bytes_total": 0})
        progress({"event": "progress", "bytes_downloaded": n, "bytes_total": 999})
    assert console.print_download_progress.call_count == 1


def test_progress_draws_a_bar_and_prints_the_error_reason():
    console = MagicMock()
    progress = _DownloadProgress(console, "user.embeddinggemma-300m-GGUF")
    progress(
        {"event": "progress", "percent": 12, "bytes_downloaded": 1, "bytes_total": 9}
    )
    console.print_download_progress.assert_called_once()
    progress({"event": "error", "error": "disk full"})
    console.print_download_error.assert_called_once_with(
        "disk full", "user.embeddinggemma-300m-GGUF"
    )
    assert progress.reported_error and progress.events == 2


def test_a_pull_outlasts_the_servers_silent_hash_check(client):
    """Lemonade sends nothing while it hashes a finished file; hanging up then
    cancels the rest of the pull (seen on the 22 GB Qwen3.6 at the 2-minute
    limit this replaced)."""
    response = MagicMock(status_code=200)
    response.iter_lines.return_value = [b"event: complete", b'data: {"percent": 100}']
    with patch("gaia.llm.lemonade_client.requests.post", return_value=response) as post:
        list(client.pull_model_stream("Qwen3.6-35B-A3B-GGUF"))
    _connect, idle = post.call_args.kwargs["timeout"]
    assert idle >= 15 * 60


# ── --progress-json: what the Agent UI's setup screen reads ────────────────


def _events(out):
    return [json.loads(line) for line in out.splitlines() if line.startswith("{")]


def test_json_progress_reports_bytes_and_speed_instead_of_a_bar():
    console, seen = MagicMock(), []
    progress = _DownloadProgress(
        console, "m", emit=lambda kind, **fields: seen.append({"event": kind, **fields})
    )
    progress(
        {
            "event": "progress",
            "percent": 10,
            "bytes_downloaded": 100,
            "bytes_total": 1000,
        }
    )
    progress({"event": "progress", "percent": 0, "bytes_total": 0})
    console.print_download_progress.assert_not_called()
    assert seen == [
        {
            "event": "download",
            "model": "m",
            "bytes_done": 100,
            "bytes_total": 1000,
            "speed_bps": 0.0,
        }
    ]


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _json_progress(monkeypatch):
    from gaia.installer import init_command

    clock, seen = _Clock(), []
    monkeypatch.setattr(init_command.time, "monotonic", clock)
    progress = _DownloadProgress(
        MagicMock(), "m", emit=lambda kind, **f: seen.append({"event": kind, **f})
    )
    return clock, seen, progress


def _sample(progress, done, total=20_000_000_000):
    progress({"event": "progress", "bytes_downloaded": done, "bytes_total": total})


def test_a_resumed_download_measures_speed_from_its_first_sample(monkeypatch):
    """10 GB already on disk is not 10 GB downloaded in the first second."""
    clock, seen, progress = _json_progress(monkeypatch)
    clock.now = 1.0
    _sample(progress, 10_000_000_000)
    clock.now = 2.0
    _sample(progress, 10_050_000_000)
    assert seen[0]["speed_bps"] == 0.0
    assert seen[1]["speed_bps"] == pytest.approx(50_000_000, rel=0.01)


def test_a_stalled_download_reports_no_speed(monkeypatch):
    clock, seen, progress = _json_progress(monkeypatch)
    clock.now = 1.0
    _sample(progress, 1_000_000_000)
    clock.now = 2.0
    _sample(progress, 1_050_000_000)
    clock.now = 30.0
    _sample(progress, 1_050_000_000)
    assert seen[-1]["speed_bps"] == 0.0


def test_a_pull_with_no_total_still_reports_its_bytes(monkeypatch):
    clock, seen, progress = _json_progress(monkeypatch)
    clock.now = 1.0
    progress({"event": "progress", "bytes_downloaded": 500_000_000, "bytes_total": 0})
    assert seen == [
        {
            "event": "download",
            "model": "m",
            "bytes_done": 500_000_000,
            "bytes_total": None,
            "speed_bps": 0.0,
        }
    ]


def test_json_progress_names_the_chat_model_and_why_others_were_passed_over(capsys):
    from gaia.installer.init_command import ChatModelChoice, InitCommand
    from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME, LARGE_DEFAULT_MODEL_NAME

    reason = "not enough free memory right now: it needs about 26.1 GB"
    choice = ChatModelChoice(
        model_id=DEFAULT_MODEL_NAME,
        user_set=False,
        skipped=[(LARGE_DEFAULT_MODEL_NAME, reason)],
    )
    cmd = InitCommand(profile="gaia", yes=True, progress_json=True)
    with patch(
        "gaia.installer.init_command.resolve_init_chat_model", return_value=choice
    ):
        cmd._chat_model(MagicMock())
    out = capsys.readouterr().out
    assert _events(out) == [
        {
            "event": "chat_model",
            "model": DEFAULT_MODEL_NAME,
            "user_set": False,
            "skipped": [{"id": LARGE_DEFAULT_MODEL_NAME, "reason": reason}],
        }
    ]
    # The terminal still gets the human lines.
    assert f"Not using {LARGE_DEFAULT_MODEL_NAME}" in out


def test_json_progress_states_the_downloads_and_their_total_first(capsys):
    from gaia.installer.init_command import InitCommand

    cmd = InitCommand(profile="minimal", yes=True, progress_json=True)

    def pull(model_id, on_progress, **_kwargs):
        on_progress(
            {
                "event": "progress",
                "percent": 50,
                "bytes_downloaded": 3,
                "bytes_total": 6,
            }
        )
        return True

    with patch("gaia.llm.lemonade_client.LemonadeClient") as client_class:
        client = MagicMock()
        client.list_models.return_value = {
            "data": [{"id": "Gemma-4-E4B-it-GGUF", "size": 5.6, "downloaded": False}]
        }
        client.ensure_model_downloaded.side_effect = pull
        client_class.return_value = client
        assert cmd._download_models()

    events = _events(capsys.readouterr().out)
    assert events[0] == {
        "event": "downloads",
        "models": [{"id": "Gemma-4-E4B-it-GGUF", "size_gb": 5.6}],
        "total_gb": 5.6,
    }
    assert events[1]["event"] == "download"
    assert events[1]["model"] == "Gemma-4-E4B-it-GGUF"
    assert (events[1]["bytes_done"], events[1]["bytes_total"]) == (3, 6)


def test_without_the_flag_init_prints_no_json(capsys):
    from gaia.installer.init_command import InitCommand

    cmd = InitCommand(profile="minimal", yes=True)
    with patch("gaia.llm.lemonade_client.LemonadeClient") as client_class:
        client = MagicMock()
        client.list_models.return_value = {"data": []}
        client.ensure_model_downloaded.return_value = True
        client_class.return_value = client
        cmd._download_models()
    assert _events(capsys.readouterr().out) == []


def test_the_cli_passes_the_planned_chat_model_to_init(monkeypatch):
    """Setup downloads the model the screen showed (--chat-model), not a re-pick."""
    import sys

    from gaia import cli
    from gaia.installer import init_command

    seen = {}

    def run_init(**kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(init_command, "run_init", run_init)
    argv = ["gaia", "init", "--yes", "--chat-model", "Gemma-4-E4B-it-GGUF"]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as done:
        cli.main()
    assert done.value.code == 0
    assert seen["chat_model"] == "Gemma-4-E4B-it-GGUF"


def test_the_cli_flag_reaches_init():
    from gaia.cli import build_parser
    from gaia.installer import init_command

    assert build_parser().parse_args(["init", "--progress-json"]).progress_json
    with patch.object(init_command, "InitCommand") as command:
        command.return_value.run.return_value = 0
        assert init_command.run_init(profile="gaia", progress_json=True) == 0
    assert command.call_args.kwargs["progress_json"] is True
