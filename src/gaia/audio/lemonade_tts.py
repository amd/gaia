# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Lemonade-backed text-to-speech for GAIA.

Wraps ``POST /api/v1/audio/speech`` on the local Lemonade Server, which serves
the Kokoro voices as model ``kokoro-v1``. Audio comes back as raw 16-bit PCM
(``audio/l16;rate=24000``) so no decoder is needed on this side.
"""

from __future__ import annotations

import re
import wave
from pathlib import Path
from typing import Callable, Optional, Tuple

import numpy as np
import requests

from gaia.audio.lemonade_service import LemonadeAudioService
from gaia.logger import get_logger

log = get_logger(__name__)

DEFAULT_TTS_MODEL = "kokoro-v1"
DEFAULT_VOICE = "af_bella"
# Kokoro's native rate; the player opens its output stream at this rate.
TTS_SAMPLE_RATE = 24000
DEFAULT_SYNTH_TIMEOUT = 120

#: Kokoro voices Lemonade's ``kokoro-v1`` accepts; an unknown one is a 500.
KOKORO_VOICES = {
    # American English Voices
    "af_alloy": {
        "name": "American Female - Alloy",
        "quality": "C",
        "duration": "MM",
    },
    "af_aoede": {
        "name": "American Female - Aoede",
        "quality": "C+",
        "duration": "H",
    },
    "af_bella": {
        "name": "American Female - Bella",
        "quality": "A-",
        "duration": "HH",
    },
    "af_jessica": {
        "name": "American Female - Jessica",
        "quality": "D",
        "duration": "MM",
    },
    "af_kore": {
        "name": "American Female - Kore",
        "quality": "C+",
        "duration": "H",
    },
    "af_nicole": {
        "name": "American Female - Nicole",
        "quality": "B-",
        "duration": "HH",
    },
    "af_nova": {
        "name": "American Female - Nova",
        "quality": "C",
        "duration": "MM",
    },
    "af_river": {
        "name": "American Female - River",
        "quality": "D",
        "duration": "MM",
    },
    "af_sarah": {
        "name": "American Female - Sarah",
        "quality": "C+",
        "duration": "H",
    },
    "af_sky": {
        "name": "American Female - Sky",
        "quality": "C-",
        "duration": "M",
    },
    "am_adam": {
        "name": "American Male - Adam",
        "quality": "F+",
        "duration": "H",
    },
    "am_echo": {
        "name": "American Male - Echo",
        "quality": "D",
        "duration": "MM",
    },
    "am_eric": {
        "name": "American Male - Eric",
        "quality": "D",
        "duration": "MM",
    },
    "am_fenrir": {
        "name": "American Male - Fenrir",
        "quality": "C+",
        "duration": "H",
    },
    "am_liam": {
        "name": "American Male - Liam",
        "quality": "D",
        "duration": "MM",
    },
    "am_michael": {
        "name": "American Male - Michael",
        "quality": "C+",
        "duration": "H",
    },
    "am_onyx": {
        "name": "American Male - Onyx",
        "quality": "D",
        "duration": "MM",
    },
    "am_puck": {
        "name": "American Male - Puck",
        "quality": "C+",
        "duration": "H",
    },
    # British English Voices
    "bf_alice": {
        "name": "British Female - Alice",
        "quality": "D",
        "duration": "MM",
    },
    "bf_emma": {
        "name": "British Female - Emma",
        "quality": "B-",
        "duration": "HH",
    },
    "bf_isabella": {
        "name": "British Female - Isabella",
        "quality": "C",
        "duration": "MM",
    },
    "bf_lily": {
        "name": "British Female - Lily",
        "quality": "D",
        "duration": "MM",
    },
    "bm_daniel": {
        "name": "British Male - Daniel",
        "quality": "D",
        "duration": "MM",
    },
    "bm_fable": {
        "name": "British Male - Fable",
        "quality": "C",
        "duration": "MM",
    },
    "bm_george": {
        "name": "British Male - George",
        "quality": "C",
        "duration": "MM",
    },
    "bm_lewis": {
        "name": "British Male - Lewis",
        "quality": "D+",
        "duration": "H",
    },
}

_L16_RE = re.compile(r"^audio/l16\b", re.IGNORECASE)
_RATE_RE = re.compile(r"\brate=(\d+)", re.IGNORECASE)


class LemonadeTTSError(RuntimeError):
    """Raised when Lemonade cannot produce speech."""


class LemonadeTTSClient(LemonadeAudioService):
    """Text-to-speech against a running Lemonade Server."""

    error_cls = LemonadeTTSError

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: str = DEFAULT_TTS_MODEL,
        api_key: Optional[str] = None,
        timeout: int = DEFAULT_SYNTH_TIMEOUT,
    ):
        """
        Args:
            base_url: Lemonade API root. Defaults to ``LEMONADE_BASE_URL`` or
                the packaged localhost default, like ``LemonadeClient``.
            model: Speech model id, e.g. ``kokoro-v1``.
            api_key: Lemonade API key. Defaults to ``LEMONADE_API_KEY``.
            timeout: Per-request timeout in seconds.
        """
        if not model:
            raise ValueError(
                "model must be a Lemonade speech model id, e.g. "
                f"'{DEFAULT_TTS_MODEL}'."
            )
        super().__init__(base_url, model, api_key, timeout)

    @property
    def speech_url(self) -> str:
        return f"{self.base_url}/audio/speech"

    def ensure_model(self, say: Optional[Callable[[str], None]] = None) -> None:
        """Pull the speech model now rather than inside the first reply.

        Raises:
            ConnectionError: Lemonade Server is not reachable.
            LemonadeTTSError: The model is unknown to this server or the pull
                failed.
        """
        self.ensure_model_pulled("tts", say=say)

    def synthesize(
        self, text: str, voice: str = DEFAULT_VOICE, speed: float = 1.0
    ) -> Tuple[np.ndarray, int]:
        """Speak *text* and return ``(float32 samples in -1..1, sample_rate)``.

        Raises:
            ValueError: *text* is empty.
            ConnectionError: Lemonade Server is not reachable.
            LemonadeTTSError: The server refused the request or returned audio
                in a format other than 16-bit PCM.
        """
        if not text or not text.strip():
            raise ValueError("text to speak must not be empty.")
        body = {
            "model": self.model,
            "input": text,
            "voice": voice,
            "speed": speed,
            "response_format": "pcm",
        }
        try:
            response = self._session.post(
                self.speech_url,
                json=body,
                headers=self._headers(),
                timeout=self.timeout,
            )
        except requests.ConnectionError as e:
            raise self._unreachable(e) from e
        except requests.Timeout as e:
            raise LemonadeTTSError(
                f"Speech synthesis timed out after {self.timeout}s for "
                f"{len(text)} characters. Split the text, or raise the timeout "
                "with LemonadeTTSClient(timeout=...)."
            ) from e
        self._check_status(response, self.speech_url, "speech")

        content_type = response.headers.get("Content-Type", "")
        rate_match = _RATE_RE.search(content_type)
        if not _L16_RE.match(content_type) or not rate_match:
            raise LemonadeTTSError(
                f"Lemonade returned '{content_type or 'no content type'}' for a "
                "pcm speech request; expected 'audio/l16;rate=<hz>'. Check the "
                "Lemonade Server version."
            )
        if len(response.content) % 2:
            raise LemonadeTTSError(
                f"Lemonade returned {len(response.content)} bytes of 16-bit PCM, "
                "an odd count, so the audio is truncated."
            )
        samples = (
            np.frombuffer(response.content, dtype="<i2").astype(np.float32) / 32768.0
        )
        return samples, int(rate_match.group(1))

    def synthesize_to_wav(
        self,
        text: str,
        output_path: str | Path,
        voice: str = DEFAULT_VOICE,
        speed: float = 1.0,
    ) -> float:
        """Speak *text* into a 16-bit mono WAV file and return its duration in seconds."""
        samples, rate = self.synthesize(text, voice=voice, speed=speed)
        write_wav(output_path, samples, rate)
        return len(samples) / rate


def write_wav(path: str | Path, samples: np.ndarray, rate: int) -> None:
    """Write float samples in -1..1 as a 16-bit mono WAV."""
    pcm = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    with wave.Wave_write(str(path)) as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes((pcm * 32767.0).astype("<i2").tobytes())
