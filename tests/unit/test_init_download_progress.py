"""`gaia init` shows a model pull as it happens, and says why it failed."""

from unittest.mock import MagicMock, patch

import pytest

from gaia.installer.init_command import _DownloadProgress
from gaia.llm.lemonade_client import (
    LemonadeClient,
    LemonadeClientError,
    ModelDownloadCancelledError,
)


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
    with pytest.raises(
        LemonadeClientError, match="Failed to download model 'm': Read timed out"
    ):
        client.ensure_model_downloaded("m", on_progress=seen.append)
    assert seen[-1] == {"event": "error", "error": "Read timed out"}


def test_a_server_error_event_is_not_reported_twice(client):
    def stream(*_a, **_k):
        yield {"event": "error", "error": "Failed to open file for writing"}
        raise LemonadeClientError("Failed to open file for writing")

    client.pull_model_stream = stream
    seen = []
    with pytest.raises(
        LemonadeClientError,
        match="Failed to download model 'm': Failed to open file for writing",
    ):
        client.ensure_model_downloaded("m", on_progress=seen.append)
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


def test_polling_timeout_raises_with_the_model_name(client):
    client.list_models.return_value = {"data": []}
    with patch("gaia.llm.lemonade_client.time.sleep"):
        with pytest.raises(
            LemonadeClientError,
            match="Download of 'm' did not finish within 1 seconds",
        ):
            client._wait_for_model_download("m", timeout=1, show_progress=False)


def test_a_stream_that_ends_without_complete_raises(client):
    client.pull_model_stream = MagicMock(
        return_value=iter([{"event": "progress", "percent": 99}])
    )
    with pytest.raises(
        LemonadeClientError, match="Download did not complete for model 'm'"
    ):
        client.ensure_model_downloaded("m", on_progress=lambda _e: None)


def test_an_unexpected_error_raises_with_its_cause(client):
    client.list_models.side_effect = KeyError("data")
    with pytest.raises(LemonadeClientError, match="Failed to ensure model 'm'") as info:
        client.ensure_model_downloaded("m", show_progress=False)
    assert isinstance(info.value.__cause__, KeyError)


def test_a_failed_non_streamed_pull_raises_instead_of_returning_false(client):
    client.pull_model.side_effect = ConnectionError("Connection refused")
    with pytest.raises(LemonadeClientError, match="Connection refused"):
        client.ensure_model_downloaded("m", show_progress=False)


def test_a_status_check_blip_keeps_polling_until_downloaded(client):
    client.list_models.side_effect = [
        ConnectionError("blip"),
        {"data": [{"id": "m", "downloaded": True}]},
    ]
    with patch("gaia.llm.lemonade_client.time.sleep"):
        assert client._wait_for_model_download("m", timeout=120, show_progress=False)


def test_a_cancelled_stream_is_not_converted_to_a_failed_download(client):
    def stream(*_args, **_kwargs):
        yield {"event": "progress", "percent": 10}
        raise ModelDownloadCancelledError("Download stopped at your request.")

    client.pull_model_stream = stream
    seen = []
    with pytest.raises(ModelDownloadCancelledError):
        client.ensure_model_downloaded("m", on_progress=seen.append)
    assert seen == [{"event": "progress", "percent": 10}]


def test_a_progress_redraw_keeps_its_carriage_return_under_rich():
    """Setup splits gaia init's output on \r; without it the redraws pile up."""
    import io

    from rich.console import Console

    from gaia.installer.init_command import InitCommand

    out = io.StringIO()
    cmd = InitCommand(profile="gaia", yes=True)
    cmd.console = Console(file=out, width=1000, force_terminal=False)
    cmd._download_progress(1, 4)
    cmd._download_progress(4, 4)

    assert out.getvalue().count("\r") == 2
    assert out.getvalue().endswith("100% (0.0 MB/0.0 MB)")


def test_the_header_box_fits_its_text_at_setups_width():
    import io

    from rich.console import Console

    from gaia.installer.init_command import InitCommand

    out = io.StringIO()
    cmd = InitCommand(profile="gaia", yes=True)
    cmd.console = Console(file=out, width=1000, force_terminal=False)
    cmd._print_header()

    assert max(len(line) for line in out.getvalue().splitlines()) < 40
