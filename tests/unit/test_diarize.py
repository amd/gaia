# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for acoustic speaker diarization."""

import subprocess
import sys
import tarfile
import wave
from pathlib import Path
from unittest.mock import patch

import pytest

from gaia.audio import diarize
from gaia.audio.diarize import DiarizationError


def _write_wav(path: Path, seconds=1.0, rate=16000, channels=1, width=2):
    import struct

    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(
            struct.pack(f"<{frames * channels}h", *([0] * frames * channels))
        )
    return path


class TestAudioValidation:
    """Bad audio must be named precisely, not fail deep inside the model."""

    def test_missing_file(self, tmp_path):
        with pytest.raises(DiarizationError, match="No such audio file"):
            diarize.diarize(tmp_path / "nope.wav")

    def test_wrong_sample_rate_names_the_fix(self, tmp_path):
        wav = _write_wav(tmp_path / "a.wav", rate=44100)
        with pytest.raises(DiarizationError) as e:
            diarize.diarize(wav)
        assert "44100" in str(e.value)
        assert "to_wav16k_mono" in str(e.value)

    def test_stereo_is_rejected(self, tmp_path):
        wav = _write_wav(tmp_path / "s.wav", channels=2)
        with pytest.raises(DiarizationError, match="mono"):
            diarize.diarize(wav)


class TestAvailability:
    def test_unavailable_without_models(self, tmp_path):
        with patch.object(diarize, "_SEGMENTATION_MODEL", tmp_path / "nope.onnx"):
            assert diarize.is_available() is False

    def test_setup_is_lazy(self):
        """Nothing may install or download at import time."""
        source = Path(diarize.__file__).read_text(encoding="utf-8")
        body = source.split("def ensure_ready")[0]
        assert "pip install" not in body.replace('"pip", "install"', "")
        assert "requests.get" not in body


class TestInstallFailures:
    """A failed setup must say what to run by hand."""

    def test_pip_failure_gives_the_manual_command(self):
        error = subprocess.CalledProcessError(1, "pip", stderr="no network")
        with patch.dict(sys.modules, {"sherpa_onnx": None}):
            with patch("builtins.__import__", side_effect=ImportError):
                with patch("subprocess.run", side_effect=error):
                    with pytest.raises(DiarizationError) as e:
                        diarize._ensure_package(lambda _m: None)
        assert "pip install" in str(e.value)
        assert "sherpa-onnx" in str(e.value)

    def test_download_failure_names_the_host(self, tmp_path):
        import requests

        with patch("requests.get", side_effect=requests.ConnectionError("dns")):
            with pytest.raises(DiarizationError) as e:
                diarize._download("https://example.invalid/m.onnx", tmp_path / "m.onnx")
        assert "github.com" in str(e.value)
        assert not (tmp_path / "m.onnx").exists()

    def test_partial_download_is_not_left_behind(self, tmp_path):
        """An interrupted download must not look like a finished one."""
        import requests

        target = tmp_path / "m.onnx"
        with patch("requests.get", side_effect=requests.Timeout("slow")):
            with pytest.raises(DiarizationError):
                diarize._download("https://example.invalid/m.onnx", target)
        assert not target.exists()
        assert not target.with_suffix(".onnx.part").exists()


class TestArchiveSafety:
    def test_refuses_to_extract_outside_the_target(self, tmp_path):
        """A crafted archive must not write outside the model directory."""
        archive = tmp_path / "evil.tar"
        payload = tmp_path / "payload"
        payload.write_text("x", encoding="utf-8")
        with tarfile.open(archive, "w") as tar:
            tar.add(payload, arcname="../escaped.txt")

        with tarfile.open(archive) as tar:
            with pytest.raises(DiarizationError, match="outside"):
                diarize._safe_extract(tar, tmp_path / "models")
