# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Live-microphone speech-to-text for ``gaia talk``, transcribed by Lemonade.

``AudioRecorder`` cuts the microphone stream into utterances on silence; each
one is posted to Lemonade's Whisper (``/audio/transcriptions``) and the text
lands on ``transcription_queue``.
"""

import os
import queue
import tempfile
import time

import numpy as np

from gaia.audio.audio_recorder import AudioRecorder
from gaia.audio.lemonade_asr import LemonadeASRClient
from gaia.audio.lemonade_tts import write_wav
from gaia.logger import get_logger

#: ``--whisper-model-size`` values and the Lemonade model each one selects.
WHISPER_MODELS = {
    "tiny": "Whisper-Tiny",
    "base": "Whisper-Base",
    "small": "Whisper-Small",
    "medium": "Whisper-Medium",
    "large": "Whisper-Large-v3",
    "turbo": "Whisper-Large-v3-Turbo",
}
DEFAULT_WHISPER_SIZE = "base"

# One utterance is seconds of audio; anything near this is a stuck server.
UTTERANCE_TIMEOUT = 60


def whisper_model_for(size: str) -> str:
    """The Lemonade model id for a ``--whisper-model-size`` value."""
    try:
        return WHISPER_MODELS[size]
    except KeyError:
        raise ValueError(
            f"Unknown Whisper model size '{size}'. Choose one of: "
            f"{', '.join(WHISPER_MODELS)}."
        ) from None


class WhisperAsr(AudioRecorder):
    log = get_logger(__name__)

    def __init__(
        self,
        model_size=DEFAULT_WHISPER_SIZE,
        device_index=None,  # Use default input device
        transcription_queue=None,
        silence_threshold=None,  # Custom silence threshold
        min_audio_length=None,  # Custom minimum audio length
        base_url=None,
        language="en",
        client=None,
        say=None,
    ):
        """
        Args:
            model_size: Whisper size; see ``WHISPER_MODELS``.
            base_url: Lemonade API root; defaults to ``LEMONADE_BASE_URL``.
            language: ISO 639-1 code sent with every utterance.
            client: A ready :class:`LemonadeASRClient` (tests inject one).
            say: Called with a line before the Whisper model is downloaded.

        Raises:
            ImportError: sounddevice is missing.
            ConnectionError: Lemonade Server is not reachable.
            LemonadeASRError: Lemonade cannot serve the Whisper model.
        """
        super().__init__(device_index)

        if silence_threshold is not None:
            self.SILENCE_THRESHOLD = silence_threshold
        if min_audio_length is not None:
            self.MIN_AUDIO_LENGTH = min_audio_length
        self.log = self.__class__.log

        self.language = language
        self.client = client or LemonadeASRClient(
            base_url=base_url,
            model=whisper_model_for(model_size),
            timeout=UTTERANCE_TIMEOUT,
        )
        self.client.ensure_model(say=say)
        self.transcription_queue = transcription_queue

        #: Why transcription stopped, set by the processing thread. Like
        #: ``mic_error`` it is the only way that thread can report anything.
        self.asr_error = None

    def transcribe_utterance(self, audio: np.ndarray) -> str:
        """Send one utterance of 16 kHz float audio to Lemonade; return its text."""
        fd, name = tempfile.mkstemp(suffix=".wav", prefix="gaia-talk-")
        os.close(fd)
        try:
            write_wav(name, audio, self.RATE)
            transcript = self.client.transcribe(
                name, language=self.language, require_timestamps=False
            )
        finally:
            os.unlink(name)
        return transcript.text.strip()

    def _process_audio(self):
        """Transcribe utterances as the recorder queues them.

        A failure stops recording and is kept on ``asr_error``: a server that
        went away would otherwise fail every utterance while the screen still
        says "Listening".
        """
        self.log.debug("Starting Lemonade transcription loop...")
        while self.is_recording:
            try:
                audio = self.audio_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if len(audio) == 0:
                continue
            started = time.time()
            try:
                text = self.transcribe_utterance(audio)
            except Exception as e:  # noqa: BLE001 - reported via asr_error
                self.asr_error = (
                    f"Speech recognition failed: {e}. Check that Lemonade Server "
                    "is running, then restart `gaia talk`."
                )
                self.log.error(self.asr_error)
                self.is_recording = False
                break
            self.log.debug(
                "Transcribed %.1fs of audio in %.2fs: %r",
                len(audio) / self.RATE,
                time.time() - started,
                text,
            )
            if text and self.transcription_queue is not None:
                self.transcription_queue.put(text)
        self.log.debug("Lemonade transcription loop stopped")
