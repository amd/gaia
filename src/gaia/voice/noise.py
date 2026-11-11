# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Telling speech from the room: key clicks, taps, coughs, a door.

A recognizer handed a keyboard clatter still returns words ("so", "you know")
or a label ("*music*"), and each would become a turn. Two checks stop that:
:class:`SpeechMeter` measures how much of a sound was voiced, and
:func:`is_noise_text` spots a transcript that is not something a person said.
"""

from __future__ import annotations

import re
from collections import deque
from typing import Deque

import numpy as np

from gaia.voice.audio import ASR_RATE

SUBFRAME_MS = 20
#: A voiced stretch shorter than this is a click, not a syllable.
MIN_RUN_MS = 80
#: An utterance with less voiced sound than this is not speech.
MIN_VOICED_MS = 120
#: This much voiced sound over GAIA's own voice is someone talking to her.
BARGE_VOICED_MS = 300
#: Sound just before the recognizer says "speech started" belongs to the utterance.
LOOKBACK_MS = 400

_FLOOR_MIN = 0.012
_FLOOR_RATIO = 3.0
#: Zero crossings per sample. Vowels sit well under this; clicks and hiss above.
_MAX_ZCR = 0.22

#: A whole transcript that is only a sound label: *music*, [BLANK_AUDIO], (typing).
_LABEL = re.compile(r"^\s*(?:[\[(*♪][^\])*♪]*[\])*♪]?[\s.,!?]*)+$")
_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)
_ASCII_LETTER = re.compile(r"[A-Za-z]")
#: What recognizers write when handed silence or noise.
_FILLER = frozenset(
    {
        "thank you",
        "thanks for watching",
        "thank you for watching",
        "you",
        "bye",
        "so",
        "the",
        "i",
        "uh",
        "um",
        "hmm",
        "you know",
        "ok",
        "okay",
        "yeah",
        "all right",
        "alright",
        "there you go",
    }
)


def is_noise_text(text: str, *, english: bool = True) -> bool:
    """Whether *text* is a sound label or not words at all."""
    stripped = (text or "").strip()
    if not stripped:
        return True
    if _LABEL.match(stripped):
        return True
    letters = _LETTER.findall(stripped)
    if not letters:
        return True
    if english:
        ascii_letters = len(_ASCII_LETTER.findall(stripped))
        # Words in another script or full of accents: the recognizer guessed a language.
        if ascii_letters < 0.8 * len(letters):
            return True
    return False


def is_filler(text: str) -> bool:
    """Whether *text* is what a recognizer invents for a noise; alone it is no turn."""
    cleaned = re.sub(r"[^a-z' ]", "", (text or "").lower()).strip()
    return cleaned in _FILLER


class SpeechMeter:
    """Running measure of voiced sound in 16 kHz PCM16 mono."""

    def __init__(self, rate: int = ASR_RATE):
        self._step = rate * SUBFRAME_MS // 1000
        self._rest = np.zeros(0, dtype=np.float32)
        self._floor = _FLOOR_MIN
        self._run = 0
        self._recent: Deque[bool] = deque(maxlen=LOOKBACK_MS // SUBFRAME_MS)
        self._active = False
        self._total = 0
        self.voiced_ms = 0

    def feed(self, samples: np.ndarray) -> int:
        """Take audio in; returns the voiced milliseconds it added."""
        before = self._total
        data = np.concatenate([self._rest, samples.astype(np.float32, copy=False)])
        count = data.size // self._step
        for i in range(count):
            self._subframe(data[i * self._step : (i + 1) * self._step])
        self._rest = data[count * self._step :]
        return self._total - before

    def begin(self) -> None:
        """The recognizer heard speech start; count from a little before now."""
        self._active = True
        self.voiced_ms = sum(self._recent) * SUBFRAME_MS

    def end(self) -> int:
        """The utterance is over; returns its voiced milliseconds."""
        voiced = self.voiced_ms
        self._active = False
        self.voiced_ms = 0
        return voiced

    def _subframe(self, frame: np.ndarray) -> None:
        rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))
        # The floor follows the room: down at once, up slowly.
        self._floor = min(self._floor, rms) if rms < self._floor else self._floor * 1.01
        loud = rms > max(_FLOOR_MIN, self._floor * _FLOOR_RATIO)
        crossings = np.count_nonzero(np.signbit(frame[1:]) != np.signbit(frame[:-1]))
        voiced = loud and crossings / frame.size < _MAX_ZCR
        self._run = self._run + 1 if voiced else 0
        run_ms = self._run * SUBFRAME_MS
        counts = run_ms >= MIN_RUN_MS
        self._recent.append(counts)
        if counts:
            self._total += SUBFRAME_MS
        if self._active and counts:
            # The frames that built the run count once it qualifies.
            self.voiced_ms += MIN_RUN_MS if run_ms == MIN_RUN_MS else SUBFRAME_MS
