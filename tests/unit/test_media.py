# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for gaia.audio.media — the lazy ffmpeg bootstrap and media decode.

Two things these tests deliberately do NOT mock:

- the decode itself, which runs real ffmpeg and is verified by reading the
  resulting WAV header back with the stdlib ``wave`` module. A mocked
  ``subprocess.run`` would prove only that we built *an* argv, never that
  ffmpeg accepts it.
- the import, which is executed in a clean subprocess to prove the bootstrap
  cannot fire at import time.
"""

import os
import subprocess
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from gaia.audio import media


def _write_wav(path: Path, seconds: float = 1.0, rate: int = 44100, channels: int = 2):
    """Write a real (silent) WAV so ffmpeg has genuine input to convert."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(rate * seconds)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * frames * channels)
    return path


ffmpeg_required = pytest.mark.skipif(
    media.find_ffmpeg() is None,
    reason="ffmpeg not installed - skipping real decode test",
)


class TestBootstrapIsLazy:
    def test_import_runs_no_subprocess(self):
        """Importing the module must not probe or install anything (plan: lazy only)."""
        # gaia's package __init__ pulls in heavy third-party deps, so warm those
        # first; the trap is armed only around the module under test.
        code = (
            "import gaia, subprocess\n"
            "def boom(*a, **k):\n"
            "    raise AssertionError('media.py ran a subprocess at import time')\n"
            "subprocess.run = boom\n"
            "subprocess.Popen = boom\n"
            "import gaia.audio.media\n"
            "print('ok')\n"
        )
        import gaia

        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(gaia.__file__).parent.parent)
        env["PATH"] = ""
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "ok" in result.stdout

    def test_find_ffmpeg_never_installs(self, monkeypatch):
        monkeypatch.setattr(media, "_which", lambda tool: None)
        monkeypatch.setattr(
            media.subprocess,
            "run",
            lambda *a, **k: pytest.fail("find_ffmpeg must not install"),
        )
        assert media.find_ffmpeg() is None


class TestEnsureFfmpeg:
    def test_returns_existing_path_without_installing(self, monkeypatch):
        monkeypatch.setattr(media, "_which", lambda tool: f"/usr/bin/{tool}")
        monkeypatch.setattr(
            media.subprocess,
            "run",
            lambda *a, **k: pytest.fail("must not install when already present"),
        )
        assert media.ensure_ffmpeg() == "/usr/bin/ffmpeg"

    def test_installs_via_winget_then_reprobes(self, monkeypatch):
        state = {"installed": False}
        calls = []

        def which(tool):
            if tool == "winget":
                return "C:/winget.exe"
            if tool == "ffmpeg":
                return "C:/ffmpeg.exe" if state["installed"] else None
            return None

        def run(cmd, **_kwargs):
            calls.append(cmd)
            state["installed"] = True
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(media.platform, "system", lambda: "Windows")
        monkeypatch.setattr(media, "_which", which)
        monkeypatch.setattr(media.subprocess, "run", run)

        assert media.ensure_ffmpeg() == "C:/ffmpeg.exe"
        assert calls == [
            [
                "winget",
                "install",
                "--id",
                "Gyan.FFmpeg",
                "--exact",
                "--source",
                "winget",
                "--accept-package-agreements",
                "--accept-source-agreements",
            ]
        ]

    def test_installs_via_brew_on_macos(self, monkeypatch):
        state = {"installed": False}
        calls = []

        def which(tool):
            if tool == "brew":
                return "/opt/homebrew/bin/brew"
            if tool == "ffmpeg":
                return "/opt/homebrew/bin/ffmpeg" if state["installed"] else None
            return None

        def run(cmd, **_kwargs):
            calls.append(cmd)
            state["installed"] = True
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(media.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(media, "_which", which)
        monkeypatch.setattr(media.subprocess, "run", run)

        assert media.ensure_ffmpeg().endswith("ffmpeg")
        assert calls == [["brew", "install", "ffmpeg"]]

    def test_no_package_manager_fails_loudly_with_commands_and_docs(
        self, monkeypatch
    ):
        """Plan validation item: 'missing ffmpeg with no package manager fails loudly'."""
        monkeypatch.setattr(media.platform, "system", lambda: "Linux")
        monkeypatch.setattr(media, "_which", lambda tool: None)
        monkeypatch.setattr(
            media.subprocess,
            "run",
            lambda *a, **k: pytest.fail("no manager present - nothing to run"),
        )

        with pytest.raises(media.MediaError) as excinfo:
            media.ensure_ffmpeg()

        message = str(excinfo.value)
        assert "apt-get" in message
        assert "dnf" in message
        assert "pacman" in message
        assert media.DOCS_URL in message

    def test_failed_install_surfaces_exit_code_and_stderr(self, monkeypatch):
        monkeypatch.setattr(media.platform, "system", lambda: "Windows")
        monkeypatch.setattr(
            media, "_which", lambda tool: "C:/winget.exe" if tool == "winget" else None
        )
        monkeypatch.setattr(
            media.subprocess,
            "run",
            lambda *a, **k: SimpleNamespace(
                returncode=3, stdout="", stderr="no applicable installer"
            ),
        )

        with pytest.raises(media.MediaError) as excinfo:
            media.ensure_ffmpeg()

        message = str(excinfo.value)
        assert "exit 3" in message
        assert "no applicable installer" in message
        assert media.DOCS_URL in message

    def test_install_success_but_still_missing_says_reopen_terminal(self, monkeypatch):
        monkeypatch.setattr(media.platform, "system", lambda: "Windows")
        monkeypatch.setattr(
            media, "_which", lambda tool: "C:/winget.exe" if tool == "winget" else None
        )
        monkeypatch.setattr(
            media.subprocess,
            "run",
            lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""),
        )

        with pytest.raises(media.MediaError, match="still not on"):
            media.ensure_ffmpeg()

    def test_install_timeout_is_not_swallowed(self, monkeypatch):
        def run(cmd, **_kwargs):
            raise subprocess.TimeoutExpired(cmd, media._INSTALL_TIMEOUT_SECONDS)

        monkeypatch.setattr(media.platform, "system", lambda: "Windows")
        monkeypatch.setattr(
            media, "_which", lambda tool: "C:/winget.exe" if tool == "winget" else None
        )
        monkeypatch.setattr(media.subprocess, "run", run)

        with pytest.raises(media.MediaError, match="timed out"):
            media.ensure_ffmpeg()


class TestSourceValidation:
    def test_missing_file_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Media file not found"):
            media.to_wav16k_mono(tmp_path / "nope.mp4")

    def test_directory_raises_media_error(self, tmp_path):
        with pytest.raises(media.MediaError, match="directory"):
            media.to_wav16k_mono(tmp_path)


class TestProgressParsing:
    @pytest.mark.parametrize(
        "line,expected",
        [
            ("out_time=00:00:01.500000\n", 1.5),
            ("out_time=01:02:03.000000", 3723.0),
            ("out_time=00:00:00.00", 0.0),
        ],
    )
    def test_parses_ffmpeg_out_time(self, line, expected):
        assert media._parse_progress_seconds(line) == pytest.approx(expected)

    @pytest.mark.parametrize(
        "line", ["frame=12", "out_time_us=1500000", "progress=continue", ""]
    )
    def test_ignores_other_progress_lines(self, line):
        assert media._parse_progress_seconds(line) is None


@ffmpeg_required
class TestRealDecode:
    def test_converts_to_16k_mono_pcm_s16le(self, tmp_path):
        src = _write_wav(tmp_path / "in.wav", seconds=1.0, rate=44100, channels=2)
        out = media.to_wav16k_mono(src, dest=tmp_path / "out.wav")

        assert out.exists()
        with wave.open(str(out), "rb") as handle:
            assert handle.getnchannels() == media.TARGET_CHANNELS
            assert handle.getframerate() == media.TARGET_SAMPLE_RATE
            assert handle.getsampwidth() == 2

    def test_handles_windows_paths_with_spaces(self, tmp_path):
        """Regression guard: argv lists, never a shell string."""
        spaced_dir = tmp_path / "My Recordings" / "Staff Meeting"
        src = _write_wav(spaced_dir / "team sync.wav", seconds=0.5, rate=22050)
        out = media.to_wav16k_mono(src, dest=spaced_dir / "team sync 16k.wav")

        assert out.exists()
        assert " " in str(out)
        with wave.open(str(out), "rb") as handle:
            assert handle.getframerate() == media.TARGET_SAMPLE_RATE

    def test_default_dest_is_a_fresh_temp_file(self, tmp_path):
        src = _write_wav(tmp_path / "clip.wav", seconds=0.5)
        out = media.to_wav16k_mono(src)
        try:
            assert out.exists()
            assert out.name == "clip.16k.wav"
            assert out.parent != src.parent
        finally:
            out.unlink(missing_ok=True)
            out.parent.rmdir()

    def test_probe_duration_matches_real_length(self, tmp_path):
        src = _write_wav(tmp_path / "two-seconds.wav", seconds=2.0)
        assert media.probe_duration(src) == pytest.approx(2.0, abs=0.1)

    def test_progress_callback_is_monotonic_and_completes(self, tmp_path):
        src = _write_wav(tmp_path / "long.wav", seconds=3.0)
        seen = []
        media.to_wav16k_mono(
            src, dest=tmp_path / "out.wav", progress_callback=seen.append
        )

        assert seen, "progress_callback was never called"
        assert seen == sorted(seen)
        assert seen[-1] == pytest.approx(1.0)
        assert all(0.0 <= value <= 1.0 for value in seen)

    def test_undecodable_input_raises_with_exit_code(self, tmp_path):
        bogus = tmp_path / "not really.mp4"
        bogus.write_text("this is not a media file")

        with pytest.raises(media.MediaError) as excinfo:
            media.to_wav16k_mono(bogus, dest=tmp_path / "out.wav")

        assert "ffmpeg failed to decode" in str(excinfo.value)
        assert media.DOCS_URL in str(excinfo.value)

    def test_probe_duration_on_undecodable_input_raises(self, tmp_path):
        bogus = tmp_path / "broken.m4a"
        bogus.write_text("nope")

        with pytest.raises(media.MediaError, match="ffprobe"):
            media.probe_duration(bogus)
