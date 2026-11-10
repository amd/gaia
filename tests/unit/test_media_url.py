# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Fetching a recording from a link, including links that must be refused."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gaia.audio import media_url
from gaia.audio.media_url import MediaUrlError, fetch_media, is_url

PUBLIC = "https://www.youtube.com/watch?v=abc123"


def _say(_message):
    pass


@pytest.fixture
def public_host():
    with patch("gaia.web.client.WebClient.validate_url", side_effect=lambda u: u):
        yield


class _Process:
    """Stands in for yt-dlp: writes a file and prints what it would print."""

    def __init__(self, directory: Path, output, code=0, make_file=True):
        self.stdout = iter_stdout(output)
        self.pid = 1
        self.code = code
        if make_file:
            (directory / "Talk [abc123].m4a").write_bytes(b"audio")

    def wait(self, timeout=None):
        return self.code

    def kill(self):
        pass


def iter_stdout(lines):
    stream = MagicMock()
    stream.readline.side_effect = [*lines, ""]
    return stream


def _popen(output=(), code=0, make_file=True):
    calls = []

    def fake(command, **kwargs):
        calls.append((command, kwargs))
        directory = Path(command[command.index("-o") + 1]).parent
        return _Process(directory, output, code, make_file)

    return fake, calls


class TestIsUrl:
    @pytest.mark.parametrize("text", [PUBLIC, "http://x.io/a.mp4", "  https://x.io/a "])
    def test_links(self, text):
        assert is_url(text)

    @pytest.mark.parametrize(
        "text", ["talk.mp4", "C:\\videos\\talk.mp4", "/home/me/talk.mp4", "~/talk.m4a"]
    )
    def test_paths(self, text):
        assert not is_url(text)


class TestRefusedLinks:
    @pytest.mark.parametrize(
        "url",
        [
            "file:///C:/Windows/win.ini",
            "ftp://example.com/video.mp4",
            "http://127.0.0.1:8000/secret.mp4",
            "http://localhost/admin.mp4",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/video.mp4",
        ],
    )
    def test_unsafe_links_never_reach_the_downloader(self, url, tmp_path):
        with patch("subprocess.Popen") as popen:
            with pytest.raises(MediaUrlError, match="Cannot fetch"):
                fetch_media(url, tmp_path, _say)
        popen.assert_not_called()


@pytest.mark.usefixtures("public_host")
class TestDownload:
    def test_downloads_once_then_reuses(self, tmp_path):
        fake, calls = _popen()
        with (
            patch.object(media_url, "ensure_downloader"),
            patch("subprocess.Popen", fake),
        ):
            first = fetch_media(PUBLIC, tmp_path, _say)
            second = fetch_media(PUBLIC, tmp_path, _say)

        assert first.path.name == "Talk [abc123].m4a"
        assert not first.reused
        assert second.reused
        assert second.path == first.path
        assert len(calls) == 1

    def test_the_command_is_safe_against_config_and_option_injection(self, tmp_path):
        fake, calls = _popen()
        with (
            patch.object(media_url, "ensure_downloader"),
            patch("subprocess.Popen", fake),
        ):
            fetch_media(PUBLIC, tmp_path, _say)

        command, kwargs = calls[0]
        assert command[:3] == [sys.executable, "-m", "yt_dlp"]
        assert "--ignore-config" in command
        assert "--no-playlist" in command
        assert command[-2:] == ["--", PUBLIC], "a URL must never parse as an option"
        assert kwargs["stdin"] == subprocess.DEVNULL

    def test_a_failure_quotes_the_reason_and_the_way_forward(self, tmp_path):
        fake, _ = _popen(
            ["[youtube] abc123: Downloading\n", "ERROR: Private video\n"],
            code=1,
            make_file=False,
        )
        with (
            patch.object(media_url, "ensure_downloader"),
            patch("subprocess.Popen", fake),
        ):
            with pytest.raises(MediaUrlError, match="Private video") as e:
                fetch_media(PUBLIC, tmp_path, _say)
        assert "give GAIA the file" in str(e.value)

    def test_a_playlist_link_stops_before_downloading_every_video(self, tmp_path):
        fake, calls = _popen(code=101, make_file=False)
        with (
            patch.object(media_url, "ensure_downloader"),
            patch("subprocess.Popen", fake),
        ):
            with pytest.raises(MediaUrlError, match="not one video"):
                fetch_media(PUBLIC, tmp_path, _say)
        command = calls[0][0]
        assert command[command.index("--break-match-filters") + 1] == "!playlist_id"

    def test_a_video_link_pasted_without_a_scheme_is_a_link(self):
        for text in (
            "youtube.com/watch?v=abc",
            "www.youtube.com/watch?v=abc",
            "youtu.be/abc",
        ):
            assert is_url(text)
        assert not is_url("youtube.com.mp4")

    def test_success_with_no_file_is_not_success(self, tmp_path):
        fake, _ = _popen(make_file=False)
        with (
            patch.object(media_url, "ensure_downloader"),
            patch("subprocess.Popen", fake),
        ):
            with pytest.raises(MediaUrlError, match="produced no file"):
                fetch_media(PUBLIC, tmp_path, _say)

    def test_a_partial_file_is_not_a_finished_download(self, tmp_path):
        directory = media_url.download_dir_for(PUBLIC, tmp_path)
        directory.mkdir()
        (directory / "Talk.m4a.part").write_bytes(b"half")
        fake, calls = _popen()
        with (
            patch.object(media_url, "ensure_downloader"),
            patch("subprocess.Popen", fake),
        ):
            result = fetch_media(PUBLIC, tmp_path, _say)

        assert len(calls) == 1
        assert result.path.suffix == ".m4a"

    def test_progress_is_reported_without_flooding(self, tmp_path):
        lines = [f"[download]  {i}.0% of 10MiB\n" for i in range(100)]
        fake, _ = _popen(lines)
        said = []
        with (
            patch.object(media_url, "ensure_downloader"),
            patch("subprocess.Popen", fake),
        ):
            fetch_media(PUBLIC, tmp_path, said.append)

        assert 5 <= len(said) <= 13

    def test_a_temporary_file_vanishing_mid_count_is_skipped(self, tmp_path):
        (tmp_path / "kept.m4a").write_bytes(b"12345")
        (tmp_path / "gone.part").write_bytes(b"123")
        real_stat = Path.stat

        def stat(path, *args, **kwargs):
            if path.name == "gone.part":
                raise FileNotFoundError(path)
            return real_stat(path, *args, **kwargs)

        with patch.object(Path, "stat", stat):
            assert media_url._folder_size(tmp_path) == 5

    def test_an_error_while_watching_still_stops_the_downloader(self, tmp_path):
        made = []

        def fake(command, **kwargs):
            directory = Path(command[command.index("-o") + 1]).parent
            made.append(_Process(directory, ["[download] 1.0% of 10MiB\n"] * 3))
            made[-1].kill = MagicMock()
            return made[-1]

        with (
            patch.object(media_url, "ensure_downloader"),
            patch("subprocess.Popen", fake),
            patch.object(media_url, "_SIZE_CHECK_SECONDS", 0),
            patch.object(media_url, "_folder_size", side_effect=PermissionError("x")),
        ):
            with pytest.raises(PermissionError):
                fetch_media(PUBLIC, tmp_path, _say)

        made[0].kill.assert_called()


@pytest.mark.usefixtures("public_host")
class TestFinishedDownloads:
    def test_an_interrupted_download_is_never_reused(self, tmp_path):
        """Regression: a 40% file was reused and reported as the whole recording."""
        directory = media_url.download_dir_for(PUBLIC, tmp_path)
        directory.mkdir()
        (directory / "Talk [abc123].m4a").write_bytes(b"first forty percent")
        fake, calls = _popen()
        with (
            patch.object(media_url, "ensure_downloader"),
            patch("subprocess.Popen", fake),
        ):
            result = fetch_media(PUBLIC, tmp_path, _say)

        assert len(calls) == 1
        assert not result.reused

    def test_a_download_over_the_limit_is_stopped_and_removed(self, tmp_path):
        fake, _ = _popen(["[download]   1.0% of ~ unknown\n"] * 3)
        with (
            patch.object(media_url, "ensure_downloader"),
            patch.object(media_url, "_SIZE_CHECK_SECONDS", -1.0),
            patch.object(media_url, "MAX_DOWNLOAD_BYTES", 1),
            patch("subprocess.Popen", fake),
        ):
            with pytest.raises(MediaUrlError, match="limit"):
                fetch_media(PUBLIC, tmp_path, _say)

        assert not media_url.download_dir_for(PUBLIC, tmp_path).exists()

    def test_the_catch_all_extractor_stays_off(self, tmp_path):
        fake, calls = _popen()
        with (
            patch.object(media_url, "ensure_downloader"),
            patch("subprocess.Popen", fake),
        ):
            fetch_media(PUBLIC, tmp_path, _say)

        command = calls[0][0]
        assert command[command.index("--ies") + 1] == "default,-generic"
        assert "--no-part" not in command

    def test_a_direct_file_uses_the_client_that_checks_redirects(self, tmp_path):
        url = "https://cdn.example.com/talk.MP4?token=1"

        def download(_self, _url, save_dir, max_size=None):
            saved = Path(save_dir) / "talk.mp4"
            saved.write_bytes(b"audio")
            return {"path": str(saved), "size": 5}

        with (
            patch("gaia.web.client.WebClient.download", download),
            patch("subprocess.Popen") as popen,
        ):
            first = fetch_media(url, tmp_path, _say)
            second = fetch_media(url, tmp_path, _say)

        popen.assert_not_called()
        assert first.path.name == "talk.mp4"
        assert second.reused

    def test_a_redirect_into_a_private_network_is_refused(self, tmp_path):
        with patch(
            "gaia.web.client.WebClient.download",
            side_effect=ValueError("Blocked: resolves to private/reserved IP"),
        ):
            with pytest.raises(MediaUrlError, match="private"):
                fetch_media("https://cdn.example.com/talk.mp4", tmp_path, _say)


class TestOffline:
    def test_a_link_is_refused_when_the_internet_is_off(self):
        from gaia.agents.base.offline import offline_refusal

        refused = offline_refusal("transcribe_media", {"file_path": PUBLIC})

        assert refused["error_type"] == "internet_off"

    def test_a_file_on_this_pc_is_fine_when_the_internet_is_off(self):
        from gaia.agents.base.offline import offline_refusal

        assert (
            offline_refusal("transcribe_media", {"file_path": "C:/a/talk.mp4"}) is None
        )


class TestInstall:
    def test_uses_the_installer_that_fits_this_environment(self):
        with (
            patch.object(media_url, "_package_installed", side_effect=[False, True]),
            patch("gaia.agents.install_hints._pip_available", return_value=False),
            patch("gaia.agents.install_hints._find_uv", return_value=("uv", "uv")),
            patch("subprocess.run") as run,
        ):
            media_url.ensure_downloader(_say)

        assert run.call_args.args[0][:3] == ["uv", "pip", "install"]
        assert run.call_args.args[0][-1] == "yt-dlp"
        assert run.call_args.kwargs["stdin"] == subprocess.DEVNULL

    def test_a_frozen_build_says_to_use_a_file(self):
        with (
            patch.object(media_url, "_package_installed", return_value=False),
            patch.object(media_url.sys, "frozen", True, create=True),
            patch("subprocess.run") as run,
        ):
            with pytest.raises(MediaUrlError, match="give GAIA the file"):
                media_url.ensure_downloader(_say)
        run.assert_not_called()

    def test_a_failed_install_gives_the_manual_command(self):
        error = subprocess.CalledProcessError(1, "pip", stderr="no network")
        with (
            patch.object(media_url, "_package_installed", return_value=False),
            patch("subprocess.run", side_effect=error),
        ):
            with pytest.raises(MediaUrlError, match="Install it manually"):
                media_url.ensure_downloader(_say)


class TestNoAudioTrack:
    def test_a_video_without_sound_is_explained(self):
        from gaia.audio.media import _decode_failure

        error = _decode_failure(
            Path("silent.mp4"), 1, "Output file does not contain any stream"
        )

        assert "no audio track" in str(error)

    def test_other_failures_keep_ffmpegs_reason(self):
        from gaia.audio.media import _decode_failure

        assert "moov atom" in str(
            _decode_failure(Path("a.mp4"), 1, "moov atom not found")
        )


class TestToolAcceptsLinks:
    def test_a_refused_link_is_a_tool_error_not_a_crash(self):
        from gaia.agents.tools.audio_tools import AudioToolsMixin

        class Host(AudioToolsMixin):
            pass

        result = Host()._transcribe_media("http://127.0.0.1/a.mp4")

        assert result["status"] == "error"
        assert "Cannot fetch" in result["error"]

    def test_a_link_flows_into_the_transcription_pipeline(self, tmp_path):
        from gaia.agents.tools import audio_tools

        class Host(audio_tools.AudioToolsMixin):
            pass

        download = tmp_path / "Talk.m4a"
        download.write_bytes(b"audio")
        fetched = media_url.DownloadedMedia(path=download, url=PUBLIC, reused=False)
        with (
            patch("gaia.audio.media_url.fetch_media", return_value=fetched),
            patch.object(Host, "_existing_transcript", return_value={"reused": True}),
            patch.object(Host, "_transcript_access_error", return_value=None),
        ):
            result = Host()._transcribe_media(PUBLIC)

        assert result == {"reused": True}
