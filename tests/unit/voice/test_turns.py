# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Turn-taking: finished thoughts end the turn at once, trailing ones wait,
and only real words interrupt GAIA."""

import pytest

from gaia.voice.turns import (
    DUCK_MS,
    SHORT_WAIT_MS,
    UNFINISHED_WAIT_MS,
    TurnTaker,
    is_backchannel,
    sounds_unfinished,
    wait_after,
)


@pytest.mark.parametrize(
    "text",
    [
        "Find last week's invoices and",
        "I was thinking that we could, um",
        "send it to",
        "So the thing is...",
        "Add milk, eggs,",
        "because",
    ],
)
def test_trailing_speech_sounds_unfinished(text):
    assert sounds_unfinished(text)
    assert wait_after(text) == UNFINISHED_WAIT_MS


@pytest.mark.parametrize(
    "text",
    [
        "How much space is left on my drive?",
        "Find last week's invoices and total them.",
        "What is this for?",
        "Turn it off.",
    ],
)
def test_finished_speech_ends_the_turn_at_once(text):
    assert not sounds_unfinished(text)
    assert wait_after(text) == 0


def test_one_or_two_bare_words_wait_briefly():
    assert wait_after("Hey GAIA") == SHORT_WAIT_MS
    assert wait_after("Stop.") == 0


@pytest.mark.parametrize("text", ["yeah", "Mm-hmm.", "ok", "Right, right.", "I see"])
def test_backchannels(text):
    assert is_backchannel(text)


@pytest.mark.parametrize("text", ["stop", "no wait", "yeah but what about Tuesday", "", "okay stop"])
def test_not_backchannels(text):
    assert not is_backchannel(text)


def test_a_complete_sentence_is_a_turn_immediately():
    t = TurnTaker()
    d = t.final("What's on my calendar today?", speaking=False)
    assert d.turn == "What's on my calendar today?" and d.hold_ms == 0


def test_a_trailing_sentence_waits_and_joins_what_follows():
    t = TurnTaker()
    d = t.final("Find last week's invoices and", speaking=False)
    assert d.turn is None and d.hold_ms == UNFINISHED_WAIT_MS
    assert d.holding == "Find last week's invoices and"
    d = t.final("total them.", speaking=False)
    assert d.turn == "Find last week's invoices and total them."


def test_a_hold_that_runs_out_sends_what_was_said():
    t = TurnTaker()
    t.final("Send it to", speaking=False)
    assert t.timeout().turn == "Send it to"
    assert t.timeout().turn is None  # nothing left


def test_a_sound_ducks_and_words_interrupt():
    t = TurnTaker()
    d = t.speech_started(speaking=True)
    assert d.duck is True and d.duck_ms == DUCK_MS and not d.barge_in
    d = t.partial("wait what about", speaking=True)
    assert d.barge_in and d.duck is False
    # Once interrupted, the rest of the utterance is the user's turn.
    d = t.final("Wait, what about Tuesday?", speaking=False)
    assert d.turn == "Wait, what about Tuesday?" and not d.barge_in


def test_a_cough_restores_the_volume():
    t = TurnTaker()
    t.speech_started(speaking=True)
    d = t.duck_timeout()
    assert d.duck is False and not d.barge_in
    assert t.duck_timeout().duck is None  # only once


def test_mm_hm_over_gaia_is_not_a_turn():
    t = TurnTaker()
    t.speech_started(speaking=True)
    assert not t.partial("mm-hmm", speaking=True).barge_in
    d = t.final("Mm-hmm.", speaking=True)
    assert d.turn is None and not d.barge_in and d.duck is False
    # Nothing was kept: the next real turn stands alone.
    t.speaking_done()
    assert t.final("What time is it?", speaking=False).turn == "What time is it?"


def test_stop_always_interrupts():
    t = TurnTaker()
    t.speech_started(speaking=True)
    assert t.partial("stop", speaking=True).barge_in


def test_a_sound_while_gaia_is_quiet_does_not_duck():
    assert TurnTaker().speech_started(speaking=False).duck is None
