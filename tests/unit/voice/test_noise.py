# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Key clicks, taps and recognizer guesses never become a turn or cut GAIA off."""

import asyncio

import numpy as np
import pytest
from test_voice_core import _session, _until

from gaia.voice import audio as pcm
from gaia.voice.noise import (
    BARGE_VOICED_MS,
    MIN_VOICED_MS,
    SpeechMeter,
    is_filler,
    is_noise_text,
)
from gaia.voice.turns import TurnTaker

RATE = pcm.ASR_RATE


def vowel(ms: int, level: float = 0.2) -> np.ndarray:
    """A voiced sound: a 140 Hz pitch with a few harmonics."""
    t = np.arange(RATE * ms // 1000) / RATE
    wave = sum(np.sin(2 * np.pi * 140 * k * t) / k for k in (1, 2, 3, 4))
    return (level * wave / np.max(np.abs(wave))).astype(np.float32)


def silence(ms: int) -> np.ndarray:
    return np.zeros(RATE * ms // 1000, dtype=np.float32)


def typing(ms: int, seed: int = 1) -> np.ndarray:
    """A mechanical keyboard: a 25 ms burst of decaying noise every 110 ms."""
    rng = np.random.default_rng(seed)
    out = silence(ms)
    burst = RATE * 25 // 1000
    decay = np.exp(-np.arange(burst) / (burst / 4))
    for start in range(0, out.size - burst, RATE * 110 // 1000):
        out[start : start + burst] = 0.6 * rng.uniform(-1, 1, burst) * decay
    return out


def voiced_ms(samples: np.ndarray) -> int:
    meter = SpeechMeter()
    meter.feed(silence(300))
    meter.begin()
    for i in range(0, samples.size, 1600):
        meter.feed(samples[i : i + 1600])
    return meter.end()


def test_a_spoken_word_counts_as_voiced():
    assert voiced_ms(vowel(250)) >= 200


def test_typing_is_not_voiced():
    assert voiced_ms(typing(3000)) < MIN_VOICED_MS


def test_a_single_thump_is_not_voiced():
    assert voiced_ms(np.concatenate([vowel(40, 0.8), silence(400)])) < MIN_VOICED_MS


def test_hiss_is_not_voiced():
    rng = np.random.default_rng(3)
    assert voiced_ms(0.2 * rng.uniform(-1, 1, RATE).astype(np.float32)) < MIN_VOICED_MS


def test_speech_over_typing_still_counts():
    mixed = typing(1000) + np.concatenate([silence(200), vowel(500), silence(300)])
    assert voiced_ms(mixed) >= BARGE_VOICED_MS


def test_the_sound_just_before_speech_started_is_counted():
    meter = SpeechMeter()
    meter.feed(vowel(300))
    meter.begin()
    assert meter.end() >= 200


@pytest.mark.parametrize(
    "text",
    [
        "",
        "*music*",
        "[BLANK_AUDIO]",
        "(keyboard clicking)",
        "♪♪",
        "[typing] [typing]",
        "...",
        "Продолжение следует...",
        "E aí",
        "ご視聴ありがとうございました",
    ],
)
def test_labels_and_other_languages_are_noise(text):
    assert is_noise_text(text)


@pytest.mark.parametrize(
    "text",
    [
        "Do you have access to my email?",
        "Okay.",
        "Stop",
        "Open the café menu",
        "It costs 40 dollars (I think)",
    ],
)
def test_real_words_are_not_noise(text):
    assert not is_noise_text(text)


def test_another_language_is_words_when_not_expecting_english():
    assert not is_noise_text("Продолжение следует", english=False)


@pytest.mark.parametrize("text", ["So", "you know", "Thank you.", "Okay.", " you "])
def test_recognizer_guesses_are_filler(text):
    assert is_filler(text)


@pytest.mark.parametrize("text", ["Okay, go ahead", "Thank you for the summary"])
def test_sentences_are_not_filler(text):
    assert not is_filler(text)


def test_voiced_sound_over_gaia_interrupts_before_the_words_arrive():
    turns = TurnTaker()
    assert turns.speech_started(speaking=True).duck is True
    decision = turns.sustained_speech()
    assert decision.barge_in and decision.duck is False
    # Only once per interruption.
    assert not turns.sustained_speech().barge_in


def test_noise_over_gaia_restores_her_voice_and_sends_nothing():
    turns = TurnTaker()
    turns.speech_started(speaking=True)
    decision = turns.noise()
    assert decision.duck is False and not decision.barge_in and decision.turn is None


def frames(samples: np.ndarray):
    for i in range(0, samples.size, 1600):
        yield pcm.float_to_pcm16(samples[i : i + 1600])


async def heard(fake_lemonade, transcript, samples):
    _, target = await fake_lemonade(transcript=transcript, stop_after=10)
    s, events, sink = await _session(target)
    await s.start()
    for frame in frames(samples):
        await s.feed_mic(frame)
        await asyncio.sleep(0.01)  # the recognizer's events arrive as audio does
    await _until(lambda: any(e["type"] in ("final", "ignored") for e in events))
    await s.close()
    return events, sink


async def test_typing_transcribed_as_words_is_ignored(fake_lemonade):
    events, sink = await heard(fake_lemonade, "you know", typing(1000))
    assert {"type": "ignored", "reason": "not_voiced"} in events
    assert not any(e["type"] == "final" for e in events)
    assert pcm.earcon() not in sink.frames


async def test_a_sound_label_is_ignored_even_when_loud(fake_lemonade):
    events, _ = await heard(fake_lemonade, "*music*", vowel(1000))
    assert {"type": "ignored", "reason": "not_words"} in events


async def test_a_short_okay_from_a_noise_is_ignored(fake_lemonade):
    samples = np.concatenate([vowel(300), silence(700)])
    events, _ = await heard(fake_lemonade, "Okay.", samples)
    assert {"type": "ignored", "reason": "filler"} in events


async def test_real_speech_is_a_turn(fake_lemonade):
    events, sink = await heard(fake_lemonade, "Check my email.", vowel(1000))
    assert {"type": "final", "text": "Check my email."} in events
    assert pcm.earcon() in sink.frames


async def test_a_bare_yes_counts_when_a_question_is_open(fake_lemonade):
    _, target = await fake_lemonade(transcript="Okay.", stop_after=10)
    s, events, _ = await _session(target)
    await s.start()
    s.expecting_answer = True
    for frame in frames(np.concatenate([vowel(300), silence(700)])):
        await s.feed_mic(frame)
        await asyncio.sleep(0.01)
    await _until(lambda: any(e["type"] in ("final", "ignored") for e in events))
    await s.close()
    assert {"type": "final", "text": "Okay."} in events


def test_one_sentence_over_and_over_is_noise():
    from gaia.voice.noise import is_loop

    assert is_loop("I'm going to get some more.\n" * 5)
    assert is_loop("Okay. Okay. Okay.")
    assert not is_loop("Check my email. Then check my calendar. Then stop.")
    assert not is_loop("No. No.")


def test_a_doubled_sentence_is_said_once():
    from gaia.voice.noise import tidy

    assert tidy("but you can speak\nSpanish\nSpanish") == "but you can speak Spanish"
    assert tidy("Okay.\n Okay.") == "Okay."
    assert tidy("Is it 3.5 GB? Yes!") == "Is it 3.5 GB? Yes!"


async def test_a_looped_sentence_is_ignored_even_when_voiced(
    fake_lemonade,
):
    events, _ = await heard(
        fake_lemonade, "Get some more.\nGet some more.\nGet some more.", vowel(1000)
    )
    assert {"type": "ignored", "reason": "not_words"} in events
