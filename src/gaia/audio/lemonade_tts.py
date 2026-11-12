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
from typing import BinaryIO, Callable, Dict, List, Optional, Sequence, Tuple

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
#: Longest text sent in one speech request; longer text is split at sentences.
SPEECH_CHUNK_CHARS = 1800

#: Formats a speech file is written in, and the suffix each takes. MP3 is
#: Lemonade's own encoding of the same voice, about 2.4x smaller than WAV.
SPEECH_FILE_FORMATS = {"wav": ".wav", "mp3": ".mp3"}

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

_ACCENTS = {"a": "american", "b": "british"}
_GENDERS = {"f": "female", "m": "male"}


def voice_catalog() -> List[Dict[str, str]]:
    """Every Kokoro voice as ``id``, ``name``, ``accent``, ``gender`` and ``quality``.

    ``accent`` and ``gender`` come from the voice id (``af_bella`` is an
    American female voice), so a picker can group them without parsing names.
    """
    return [
        {
            "id": voice,
            "name": meta["name"].rsplit(" - ", 1)[-1],
            "accent": _ACCENTS[voice[0]],
            "gender": _GENDERS[voice[1]],
            "quality": meta["quality"],
        }
        for voice, meta in KOKORO_VOICES.items()
    ]


_L16_RE = re.compile(r"^audio/l16\b", re.IGNORECASE)
_RATE_RE = re.compile(r"\brate=(\d+)", re.IGNORECASE)


def _split_text(text: str, limit: int) -> List[str]:
    """Split speech at sentence boundaries without dropping any text."""
    if limit < 1:
        raise ValueError("speech chunk limit must be positive")
    if len(text) <= limit:
        return [text]
    pieces = re.split(r"(?<=[.!?。！？])\s+", text.strip())
    chunks: List[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 1 > limit:
            chunks.append(current)
            current = piece
        else:
            current = f"{current} {piece}".strip()
    if current:
        chunks.append(current)
    # A URL/code-like run may contain no sentence boundary. Split it at the
    # last whitespace inside each window when possible so speech does not cut
    # ordinary words in half; fall back to the hard bound for one long token.
    result: List[str] = []
    for chunk in chunks:
        while len(chunk) > limit:
            split_at = chunk.rfind(" ", 0, limit + 1)
            if split_at <= 0:
                split_at = limit
            result.append(chunk[:split_at])
            chunk = chunk[split_at:].lstrip()
        if chunk:
            result.append(chunk)
    return result


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

    def check_model_available(self) -> None:
        """Verify the configured TTS model is downloaded without pulling it.

        This is the preflight used by the agent tool. A missing model is a
        user choice in Settings, not something a speech request may download
        invisibly in the middle of a turn.
        """
        catalog = self.catalog()
        entry = next((item for item in catalog if item.get("id") == self.model), None)
        if entry is None:
            raise LemonadeTTSError(
                f"Lemonade has no TTS model '{self.model}' in its catalog. "
                "Run `gaia download --agent talk`, then retry."
            )
        if "tts" not in (entry.get("labels") or []):
            raise LemonadeTTSError(
                f"Lemonade model '{self.model}' is not a TTS model (labels: "
                f"{', '.join(entry.get('labels') or []) or 'none'})."
            )
        if entry.get("downloaded") is not True:
            size = entry.get("size")
            size_hint = (
                f" (~{float(size):.2f} GB)" if isinstance(size, (int, float)) else ""
            )
            raise LemonadeTTSError(
                f"TTS model '{self.model}' is not downloaded{size_hint}, and GAIA "
                "will not download speech models during a chat. Using Read aloud "
                "on any reply in the app downloads it; so does "
                "`gaia download --agent talk`. Then retry."
            )

    def synthesize_long(
        self,
        text: str,
        voice: str = DEFAULT_VOICE,
        speed: float = 1.0,
        limit: int = 1800,
    ) -> Tuple[np.ndarray, int]:
        """Synthesize long text in sentence-sized chunks and join the PCM."""
        if not text or not text.strip():
            raise ValueError("text to speak must not be empty.")
        pieces = _split_text(text, limit)
        chunks: List[np.ndarray] = []
        rate: Optional[int] = None
        for piece in pieces:
            samples, piece_rate = self.synthesize(piece, voice=voice, speed=speed)
            if rate is None:
                rate = piece_rate
            elif piece_rate != rate:
                raise LemonadeTTSError(
                    f"Lemonade changed speech sample rate from {rate} to {piece_rate}."
                )
            chunks.append(samples)
        return np.concatenate(chunks), rate or TTS_SAMPLE_RATE

    def synthesize_to_wav_long(
        self,
        text: str,
        output_path: str | Path,
        voice: str = DEFAULT_VOICE,
        speed: float = 1.0,
        limit: int = 1800,
    ) -> Tuple[float, int]:
        """Synthesize long text into one 16-bit mono WAV and report its chunks."""
        pieces = _split_text(text, limit)
        samples, rate = self.synthesize_long(
            text, voice=voice, speed=speed, limit=limit
        )
        write_wav(output_path, samples, rate)
        return len(samples) / rate, len(pieces)

    def _speech_response(
        self, text: str, voice: str, speed: float, response_format: str
    ) -> requests.Response:
        """One ``/audio/speech`` reply for *text*, checked for an HTTP error."""
        if not text or not text.strip():
            raise ValueError("text to speak must not be empty.")
        self._refuse_unless_local()
        body = {
            "model": self.model,
            "input": text,
            "voice": voice,
            "speed": speed,
            "response_format": response_format,
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
        return response

    def synthesize_mp3(
        self, text: str, voice: str = DEFAULT_VOICE, speed: float = 1.0
    ) -> Tuple[bytes, float]:
        """Speak *text* as MP3 and return ``(bare MP3 frames, seconds)``.

        The frames carry no ID3 tag, so several replies join into one file.

        Raises:
            ValueError: *text* is empty.
            ConnectionError: Lemonade Server is not reachable.
            LemonadeTTSError: The server refused the request or did not return MP3.
        """
        response = self._speech_response(text, voice, speed, "mp3")
        content_type = response.headers.get("Content-Type", "")
        if not content_type.lower().startswith("audio/mpeg"):
            raise LemonadeTTSError(
                f"Lemonade returned '{content_type or 'no content type'}' for an "
                "mp3 speech request; expected 'audio/mpeg'. Check the Lemonade "
                "Server version, or save the speech as WAV."
            )
        try:
            return mp3_frames(response.content)
        except ValueError as e:
            raise LemonadeTTSError(
                f"Lemonade returned MP3 speech that cannot be read: {e}. Save "
                "the speech as WAV instead."
            ) from e

    def synthesize_file(
        self,
        pieces: Sequence[str],
        output_path: str | Path,
        voice: str = DEFAULT_VOICE,
        speed: float = 1.0,
        audio_format: str = "wav",
        on_piece: Optional[Callable[[int, int], None]] = None,
    ) -> float:
        """Speak *pieces* in order into one WAV or MP3 file; return its seconds.

        Audio goes to disk piece by piece, so an hour of speech never sits in
        memory. *on_piece* is called as ``(index, total)`` before each piece and
        may raise to stop the run. A failed run leaves no file behind.
        """
        if audio_format not in SPEECH_FILE_FORMATS:
            raise ValueError(
                f"audio_format must be one of {', '.join(SPEECH_FILE_FORMATS)}; "
                f"got {audio_format!r}."
            )
        pieces = [piece for piece in pieces if piece and piece.strip()]
        if not pieces:
            raise ValueError("text to speak must not be empty.")
        target = Path(output_path)
        seconds = 0.0
        # Opened once the first piece has arrived, so a failure or a stop
        # before any audio exists raises its own error, not the writer's.
        handle: Optional[BinaryIO | wave.Wave_write] = None
        shape: Optional[Tuple[int, ...]] = None
        try:
            for index, piece in enumerate(pieces, 1):
                if on_piece:
                    on_piece(index, len(pieces))
                if audio_format == "mp3":
                    frames, length = self.synthesize_mp3(piece, voice, speed)
                    piece_shape = _mp3_shape(frames)
                    data = frames
                else:
                    samples, rate = self.synthesize(piece, voice, speed)
                    piece_shape, length = (rate,), len(samples) / rate
                    data = _pcm16(samples)
                if shape is None:
                    shape = piece_shape
                elif piece_shape != shape:
                    raise LemonadeTTSError(
                        f"Lemonade changed the speech format part-way through "
                        f"(piece {index} of {len(pieces)}), so the pieces cannot "
                        "be joined into one file. Try again."
                    )
                if handle is None:
                    if audio_format == "mp3":
                        handle = target.open("wb")
                    else:
                        handle = wave.open(str(target), "wb")
                        handle.setnchannels(1)
                        handle.setsampwidth(2)
                        handle.setframerate(shape[0])
                if audio_format == "mp3":
                    handle.write(data)
                else:
                    handle.writeframes(data)
                seconds += length
            handle.close()
            return seconds
        except BaseException:
            if handle is not None:
                handle.close()
            target.unlink(missing_ok=True)
            raise

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
        response = self._speech_response(text, voice, speed, "pcm")
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


def _pcm16(samples: np.ndarray) -> bytes:
    pcm = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    return (pcm * 32767.0).astype("<i2").tobytes()


def write_wav(path: str | Path | BinaryIO, samples: np.ndarray, rate: int) -> None:
    """Write float samples in -1..1 as a 16-bit mono WAV, to a path or open file."""
    target = path if hasattr(path, "write") else str(path)
    with wave.Wave_write(target) as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(_pcm16(samples))


# Layer III bitrates in kbps, by "is MPEG-1" then the header's bitrate index.
_MP3_BITRATES = {
    True: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    False: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
# Sample rates by the header's version bits: MPEG-1, MPEG-2, MPEG-2.5.
_MP3_RATES = {
    3: (44100, 48000, 32000),
    2: (22050, 24000, 16000),
    0: (11025, 12000, 8000),
}


def _mp3_shape(frames: bytes) -> Tuple[int, ...]:
    """What two runs of frames must share to play as one file: MPEG version,
    sample rate and channel mode, from the first frame header."""
    return ((frames[1] >> 3) & 0x03, (frames[2] >> 2) & 0x03, frames[3] >> 6)


def mp3_frames(data: bytes) -> Tuple[bytes, float]:
    """``(the MPEG Layer III frames in *data*, their length in seconds)``.

    Drops a leading ID3v2 tag and a trailing ID3v1 tag, so the frames of
    several files can be written one after another as a single MP3.

    Raises:
        ValueError: *data* holds anything other than whole Layer III frames.
    """
    start, end = 0, len(data)
    if data[:3] == b"ID3" and len(data) >= 10:
        size = (data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9]
        start = 10 + size + (10 if data[5] & 0x10 else 0)
    if end - start >= 128 and data[end - 128 : end - 125] == b"TAG":
        end -= 128
    position, seconds = start, 0.0
    while position < end:
        header = data[position : position + 4]
        if len(header) < 4 or header[0] != 0xFF or header[1] & 0xE0 != 0xE0:
            raise ValueError(f"no MPEG frame at byte {position}")
        version = (header[1] >> 3) & 0x03
        layer = (header[1] >> 1) & 0x03
        bitrate_index = header[2] >> 4
        rate_index = (header[2] >> 2) & 0x03
        if version == 1 or layer != 1 or bitrate_index in (0, 15) or rate_index == 3:
            raise ValueError(f"unsupported MPEG frame header at byte {position}")
        mpeg1 = version == 3
        rate = _MP3_RATES[version][rate_index]
        bitrate = _MP3_BITRATES[mpeg1][bitrate_index] * 1000
        samples = 1152 if mpeg1 else 576
        length = samples // 8 * bitrate // rate + ((header[2] >> 1) & 0x01)
        if position + length > end:
            raise ValueError("the last MPEG frame is cut short")
        position += length
        seconds += samples / rate
    if position == start:
        raise ValueError("no MPEG frames")
    return data[start:end], seconds
