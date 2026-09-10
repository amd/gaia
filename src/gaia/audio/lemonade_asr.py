# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Lemonade-backed speech-to-text for GAIA.

Wraps ``POST /api/v1/audio/transcriptions`` on the local Lemonade Server. The
``whispercpp`` backend returns word-level timestamps *and* per-word confidence
under ``verbose_json`` — richer than the endpoint's documentation claims — which
is what :meth:`Transcript.low_confidence_spans` turns into a bounded edit
surface for a targeted error-correction pass.

The endpoint accepts WAV only; decode other containers with
:func:`gaia.audio.media.to_wav16k_mono` first.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import requests

from gaia.llm.lemonade_client import (
    LEMONADE_API_VERSION,
    _get_lemonade_config,
    lemonade_auth_headers,
    resolve_lemonade_api_key,
)
from gaia.logger import get_logger

log = get_logger(__name__)

DOCS_URL = "https://amd-gaia.ai/docs/guides/install"

# Near-large-v3 quality at half the download, on the whispercpp backend that
# returns the segment and word timestamps diarization aligns against.
DEFAULT_ASR_MODEL = "Whisper-Large-v3-Turbo"

# Formats the endpoint accepts. transcribe() always asks for verbose_json —
# it is the only one carrying segments, words and confidence.
RESPONSE_FORMATS = ("json", "verbose_json", "text", "srt", "vtt")
TRANSCRIPTION_RESPONSE_FORMAT = "verbose_json"

# A 46-minute meeting is ~5 minutes of ASR at the measured ~9x realtime, and a
# first call may also pull model weights.
DEFAULT_TRANSCRIBE_TIMEOUT = 1800

DEFAULT_CONFIDENCE_THRESHOLD = 0.6
DEFAULT_CONTEXT_WORDS = 6

_FLM_RE = re.compile(r"(?:^|[-_.])flm(?:$|[-_.])", re.IGNORECASE)


class LemonadeASRError(RuntimeError):
    """Raised when Lemonade cannot produce a transcript."""


def is_flm_model(model: str) -> bool:
    """True for Lemonade's NPU ``flm`` Whisper builds, which return no timestamps."""
    return bool(_FLM_RE.search(model or ""))


@dataclass(frozen=True)
class Word:
    """One token with its timing and the model's confidence in it."""

    word: str
    start: float
    end: float
    probability: float


@dataclass
class Segment:
    """A contiguous utterance. ``speaker`` is filled in later by diarization."""

    start: float
    end: float
    text: str
    avg_logprob: float
    words: List[Word] = field(default_factory=list)
    speaker: Optional[str] = None


@dataclass(frozen=True)
class LowConfidenceSpan:
    """A run of adjacent low-confidence words, plus the words around it.

    ``context`` is what an error-correction pass should see; ``text`` is the
    only part it is allowed to rewrite.
    """

    text: str
    context: str
    start: float
    end: float
    min_probability: float
    words: List[Word]
    segment_index: int


@dataclass
class Transcript:
    """A full transcription result."""

    segments: List[Segment]
    language: str
    duration: float
    model: str
    # Server-provided full text, used when the model returns no segments.
    raw_text: str = ""

    @property
    def text(self) -> str:
        """The whole transcript as one string."""
        if self.segments:
            return "".join(segment.text for segment in self.segments).strip()
        return self.raw_text.strip()

    @property
    def words(self) -> List[Word]:
        """Every word across every segment, in order."""
        return [word for segment in self.segments for word in segment.words]

    def low_confidence_spans(
        self,
        threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
        context_words: int = DEFAULT_CONTEXT_WORDS,
    ) -> List[LowConfidenceSpan]:
        """Contiguous runs of words the model was unsure about, with context.

        Whisper's mis-hearings concentrate in these runs — in validation it
        rendered "diarization" as "diurization" and the two lowest-probability
        tokens in the whole file were exactly the two composing that error. So a
        correction pass gets only these spans and their surroundings, rather
        than the full transcript, which bounds the edit surface and anchors
        every change to a measured signal.

        Args:
            threshold: Words scoring below this are low-confidence.
            context_words: Words of surrounding context on each side.

        Raises:
            ValueError: ``threshold`` is outside ``0..1``, ``context_words`` is
                negative, or the transcript carries no word-level confidence
                (the FLM builds, for instance) — a silent empty list would read
                as "no likely errors".
        """
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(
                f"threshold must be between 0.0 and 1.0, got {threshold}. "
                "Word probabilities from Lemonade are in that range."
            )
        if context_words < 0:
            raise ValueError(f"context_words must be >= 0, got {context_words}")

        if not self.segments:
            return []
        if not any(segment.words for segment in self.segments):
            raise ValueError(
                f"Transcript from model '{self.model}' has no word-level "
                "confidence, so low-confidence spans cannot be computed. "
                "Re-transcribe with a whispercpp model such as "
                f"'{DEFAULT_ASR_MODEL}'."
            )

        spans: List[LowConfidenceSpan] = []
        for segment_index, segment in enumerate(self.segments):
            for run_start, run_end in _low_confidence_runs(segment.words, threshold):
                run = segment.words[run_start:run_end]
                context_start = max(0, run_start - context_words)
                context_end = min(len(segment.words), run_end + context_words)
                context = segment.words[context_start:context_end]
                spans.append(
                    LowConfidenceSpan(
                        text="".join(word.word for word in run).strip(),
                        context="".join(word.word for word in context).strip(),
                        start=run[0].start,
                        end=run[-1].end,
                        min_probability=min(word.probability for word in run),
                        words=run,
                        segment_index=segment_index,
                    )
                )
        return spans


def _low_confidence_runs(
    words: List[Word], threshold: float
) -> Iterator[tuple[int, int]]:
    """Yield ``(start, end)`` index pairs for maximal runs below ``threshold``."""
    run_start: Optional[int] = None
    for index, word in enumerate(words):
        if word.probability < threshold:
            if run_start is None:
                run_start = index
        elif run_start is not None:
            yield run_start, index
            run_start = None
    if run_start is not None:
        yield run_start, len(words)


def _resolve_base_url(base_url: Optional[str]) -> str:
    """Resolve the Lemonade API root, matching ``lemonade_client``'s rules.

    Falls through to ``LEMONADE_BASE_URL`` / the packaged default when the
    caller passes nothing, and appends the ``/api/v1`` suffix users omit.
    """
    if not base_url:
        return _get_lemonade_config()[2]
    normalized = base_url.rstrip("/")
    if not normalized.endswith(f"/api/{LEMONADE_API_VERSION}"):
        normalized = f"{normalized}/api/{LEMONADE_API_VERSION}"
    return normalized


class LemonadeASRClient:
    """Speech-to-text against a running Lemonade Server."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: str = DEFAULT_ASR_MODEL,
        api_key: Optional[str] = None,
        timeout: int = DEFAULT_TRANSCRIBE_TIMEOUT,
    ):
        """
        Args:
            base_url: Lemonade API root. Defaults to the same resolution
                ``LemonadeClient`` uses (``LEMONADE_BASE_URL`` or the packaged
                localhost default).
            model: Transcription model id, e.g. ``Whisper-Large-v3-Turbo``.
            api_key: Lemonade API key. Defaults to ``LEMONADE_API_KEY``.
            timeout: Per-request timeout in seconds.
        """
        if not model:
            raise ValueError(
                "model must be a Lemonade transcription model id, e.g. "
                f"'{DEFAULT_ASR_MODEL}'."
            )
        self.base_url = _resolve_base_url(base_url)
        self.model = model
        self.api_key = resolve_lemonade_api_key(api_key)
        self.timeout = timeout
        self._session = requests.Session()

    @property
    def transcriptions_url(self) -> str:
        return f"{self.base_url}/audio/transcriptions"

    def available_models(self) -> List[str]:
        """Transcription models downloaded on this server, ready to use now.

        Models in the catalog but not yet downloaded are excluded — they would
        trigger a multi-gigabyte pull mid-request.
        """
        url = f"{self.base_url}/models"
        payload = self._get_json(url, what="model list")
        models = payload.get("data")
        if not isinstance(models, list):
            raise LemonadeASRError(
                f"Unexpected response from {url}: expected a 'data' list, got "
                f"{type(models).__name__}. Check the Lemonade Server version."
            )
        return sorted(
            str(entry.get("id"))
            for entry in models
            if isinstance(entry, dict)
            and "transcription" in (entry.get("labels") or [])
            and entry.get("id")
        )

    def transcribe(
        self,
        wav_path: os.PathLike | str,
        language: Optional[str] = None,
        require_timestamps: bool = True,
    ) -> Transcript:
        """Transcribe a 16 kHz mono WAV file.

        Args:
            wav_path: Path to a WAV file. Decode other containers first with
                :func:`gaia.audio.media.to_wav16k_mono`.
            language: ISO 639-1 code (``"en"``). ``None`` auto-detects.
            require_timestamps: Set ``False`` only for quick, unattributed text
                where segment and word timing is not needed. Leaving it ``True``
                makes an FLM model fail up front instead of silently returning a
                transcript with no timing.

        Returns:
            Transcript: Segments, words, confidence, detected language, duration.

        Raises:
            FileNotFoundError: ``wav_path`` does not exist.
            ValueError: The file is not a WAV, or the model cannot satisfy
                ``require_timestamps``.
            ConnectionError: Lemonade Server is not reachable.
            LemonadeASRError: The server rejected the request or returned an
                unusable body.
        """
        if require_timestamps and is_flm_model(self.model):
            raise ValueError(
                f"Model '{self.model}' runs on the FLM (NPU) backend, which "
                "returns no segment or word timestamps, so it cannot produce "
                "the timing this call requires. Use a whispercpp model such as "
                f"'{DEFAULT_ASR_MODEL}', or pass require_timestamps=False if "
                "plain text with no timing is enough."
            )

        path = Path(wav_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(
                f"Audio file not found: {path}. Pass an absolute path to a "
                "16 kHz mono WAV file."
            )
        if path.suffix.lower() != ".wav":
            raise ValueError(
                f"Lemonade's transcription endpoint accepts WAV only, got "
                f"'{path.suffix or path.name}'. Convert it first with "
                "gaia.audio.media.to_wav16k_mono()."
            )

        data: Dict[str, str] = {
            "model": self.model,
            "response_format": TRANSCRIPTION_RESPONSE_FORMAT,
        }
        if language:
            data["language"] = language

        log.debug(
            "transcribing %s with %s (language=%s)", path, self.model, language or "auto"
        )
        with path.open("rb") as handle:
            payload = self._post_multipart(
                self.transcriptions_url,
                files={"file": (path.name, handle, "audio/wav")},
                data=data,
            )
        return self._parse_transcript(payload)

    def _parse_transcript(self, payload: Dict[str, Any]) -> Transcript:
        raw_segments = payload.get("segments") or []
        if not isinstance(raw_segments, list):
            raise LemonadeASRError(
                "Lemonade returned a non-list 'segments' field "
                f"({type(raw_segments).__name__}). Check the Lemonade Server version."
            )
        segments = [
            Segment(
                start=float(raw.get("start", 0.0)),
                end=float(raw.get("end", 0.0)),
                text=str(raw.get("text", "")),
                avg_logprob=float(raw.get("avg_logprob", 0.0)),
                words=[
                    Word(
                        word=str(raw_word.get("word", "")),
                        start=float(raw_word.get("start", 0.0)),
                        end=float(raw_word.get("end", 0.0)),
                        probability=float(raw_word.get("probability", 0.0)),
                    )
                    for raw_word in (raw.get("words") or [])
                ],
            )
            for raw in raw_segments
        ]
        return Transcript(
            segments=segments,
            language=str(payload.get("language") or payload.get("detected_language") or ""),
            duration=float(payload.get("duration") or 0.0),
            model=self.model,
            raw_text=str(payload.get("text") or ""),
        )

    def _headers(self) -> Dict[str, str]:
        return lemonade_auth_headers(self.api_key)

    def _get_json(self, url: str, what: str) -> Dict[str, Any]:
        try:
            response = self._session.get(
                url, headers=self._headers(), timeout=self.timeout
            )
        except requests.ConnectionError as e:
            raise self._unreachable(e) from e
        return self._decode(response, url, what)

    def _post_multipart(self, url: str, files: Dict, data: Dict) -> Dict[str, Any]:
        try:
            response = self._session.post(
                url,
                files=files,
                data=data,
                headers=self._headers(),
                timeout=self.timeout,
            )
        except requests.ConnectionError as e:
            raise self._unreachable(e) from e
        except requests.Timeout as e:
            raise LemonadeASRError(
                f"Transcription timed out after {self.timeout}s. Longer audio "
                "needs a bigger timeout (LemonadeASRClient(timeout=...)); a "
                "first call with an undownloaded model also pays for the pull."
            ) from e
        return self._decode(response, url, "transcription")

    def _unreachable(self, error: Exception) -> ConnectionError:
        return ConnectionError(
            f"Lemonade Server is not reachable at {self.base_url} ({error}). "
            "Start it with `lemonade-server serve`, run `gaia init` to install "
            f"it, or set LEMONADE_BASE_URL to a running server. See {DOCS_URL}"
        )

    def _decode(self, response, url: str, what: str) -> Dict[str, Any]:
        # 401 is handled before the generic branch so the body — which some
        # proxies echo the Authorization header into — never reaches the user.
        if response.status_code == 401:
            raise LemonadeASRError(
                f"Lemonade rejected the API key (401 Unauthorized) on {what}. "
                "Verify LEMONADE_API_KEY is correct."
            )
        if response.status_code >= 400:
            raise LemonadeASRError(
                f"Lemonade returned {response.status_code} for {what} at {url}: "
                f"{_server_message(response)}"
            )
        try:
            payload = response.json()
        except ValueError as e:
            raise LemonadeASRError(
                f"Lemonade returned a non-JSON body for {what} at {url}: "
                f"{response.text[:300]!r}"
            ) from e
        if not isinstance(payload, dict):
            raise LemonadeASRError(
                f"Lemonade returned {type(payload).__name__} for {what} at "
                f"{url}, expected a JSON object."
            )
        return payload


def _server_message(response) -> str:
    """Pull Lemonade's ``{"error": {"message": ...}}`` text out of a failure body."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:500]
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])[:500]
    return str(body)[:500]
