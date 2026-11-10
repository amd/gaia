# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Whisper sometimes repeats one line to the end of the window; that span is re-decoded."""

import array
import wave
from unittest.mock import patch

import pytest

from gaia.audio.lemonade_asr import LemonadeASRClient


def _wav(path, seconds, quiet=()):
    """A loud square wave, silent over each ``(start, end)`` second in *quiet*."""
    loud = array.array("h", [8000, -8000] * 500).tobytes()
    silent = b"\0\0" * 1000
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(1000)
        for second in range(seconds):
            hushed = any(start <= second < end for start, end in quiet)
            handle.writeframes(silent if hushed else loud)
    return path


def _payload(*segments):
    return {
        "segments": [
            {"start": start, "end": end, "text": text, "avg_logprob": -0.1}
            for start, end, text in segments
        ],
        "language": "en",
        "duration": 30.0,
    }


def _looping(start, count, line=" There is no reason to do that."):
    return [(start + i, start + i + 1, line) for i in range(count)]


@pytest.fixture
def client():
    with (
        patch.object(LemonadeASRClient, "_refuse_unless_local"),
        patch.object(LemonadeASRClient, "_slot_lease"),
    ):
        yield LemonadeASRClient(
            base_url="http://localhost:1/api/v1", model="Whisper-Base"
        )


def _seconds_sent(call):
    handle = call.kwargs["files"]["file"][1]
    handle.seek(0)
    with wave.open(handle) as reader:
        return reader.getnframes() / reader.getframerate()


def test_a_loop_is_re_decoded_and_the_real_speech_replaces_it(client, tmp_path):
    audio = _wav(tmp_path / "talk.wav", 30)
    first = _payload((0.0, 10.0, " Opening words."), *_looping(10.0, 20))
    redo = _payload(
        (0.0, 8.0, " What was really said."), (8.0, 20.0, " And then more.")
    )
    sent = []

    def post(*_args, **kwargs):
        sent.append(_seconds_sent(type("Call", (), {"kwargs": kwargs})))
        return first if len(sent) == 1 else redo

    with patch.object(client, "_post_multipart", side_effect=post):
        result = client.transcribe(audio)

    assert sent == [30.0, 20.0], "only the looped span goes back"
    assert result.text == "Opening words. What was really said. And then more."
    assert result.segments[1].start == pytest.approx(10.0), "redo is put back in place"
    assert result.unclear_spans == []


def test_a_loop_that_survives_the_retries_is_collapsed_and_reported(client, tmp_path):
    audio = _wav(tmp_path / "talk.wav", 30)
    stuck = _payload((0.0, 10.0, " Opening words."), *_looping(10.0, 20))
    again = _payload(*_looping(0.0, 20))

    with patch.object(client, "_post_multipart", side_effect=[stuck, again]) as post:
        result = client.transcribe(audio)

    assert post.call_count == 2, "a loop in the same place is not retried forever"
    assert result.text == "Opening words. There is no reason to do that."
    assert result.unclear_spans == [(10.0, 30.0)]


def test_a_later_loop_in_the_redo_gets_its_own_retry(client, tmp_path):
    audio = _wav(tmp_path / "talk.wav", 30)
    first = _payload(*_looping(0.0, 30))
    partly = _payload((0.0, 10.0, " Real start."), *_looping(10.0, 20, " Again."))
    rest = _payload((0.0, 20.0, " Real ending."))

    with patch.object(
        client, "_post_multipart", side_effect=[first, partly, rest]
    ) as post:
        result = client.transcribe(audio)

    assert post.call_count == 3
    assert result.text == "Real start. Real ending."
    assert result.segments[1].start == pytest.approx(10.0)


def test_a_loop_over_silence_is_dropped_without_a_retry(client, tmp_path):
    audio = _wav(tmp_path / "talk.wav", 30, quiet=[(10, 30)])
    filled = _payload(
        (0.0, 10.0, " Opening words."), *_looping(10.0, 20, " Thank you.")
    )

    with patch.object(client, "_post_multipart", return_value=filled) as post:
        result = client.transcribe(audio)

    assert post.call_count == 1, "silence has nothing to recover"
    assert result.text == "Opening words."
    assert result.unclear_spans == [], "a pause is not unclear speech"


def test_only_the_speech_after_a_pause_is_re_decoded(client, tmp_path):
    """A loop that runs from a pause into speech loops again if re-decoded whole."""
    audio = _wav(tmp_path / "talk.wav", 30, quiet=[(10, 20)])
    first = _payload((0.0, 10.0, " Opening words."), *_looping(10.0, 20, " Thank you."))
    redo = _payload((0.0, 10.0, " The speech after the pause."))
    sent = []

    def post(*_args, **kwargs):
        sent.append(_seconds_sent(type("Call", (), {"kwargs": kwargs})))
        return first if len(sent) == 1 else redo

    with patch.object(client, "_post_multipart", side_effect=post):
        result = client.transcribe(audio)

    assert sent == [30.0, 10.0], "the quiet 10 s are not sent again"
    assert result.text == "Opening words. The speech after the pause."
    assert result.segments[1].start == pytest.approx(20.0)


def test_a_loop_that_drifts_between_copies_is_still_found(client, tmp_path):
    audio = _wav(tmp_path / "talk.wav", 30)
    lines = [" I had a few years of my career."] + [
        " I had a few years of my career in the career"
        + (" in the career." if i == 2 else ".")
        for i in range(6)
    ]
    first = _payload(
        (0.0, 10.0, " Opening words."),
        *[(10.0 + i, 11.0 + i, line) for i, line in enumerate(lines)],
    )
    redo = _payload((0.0, 7.0, " Reed College offered calligraphy."))

    with patch.object(client, "_post_multipart", side_effect=[first, redo]) as post:
        result = client.transcribe(audio)

    assert post.call_count == 2
    assert result.text == "Opening words. Reed College offered calligraphy."


def test_a_loop_split_around_its_full_stop_is_still_found(client, tmp_path):
    audio = _wav(tmp_path / "talk.wav", 30)
    line = " I was able to take a class in the country"
    split = [(10.0, 11.0, " And" + line), (11.0, 11.1, " country.")]
    for i in range(8):
        split += [(11.1 + i, 11.9 + i, line), (11.9 + i, 12.1 + i, ".")]
    redo = _payload((0.0, 9.0, " What was really said."))
    first = _payload((0.0, 10.0, " Opening words."), *split)

    with patch.object(client, "_post_multipart", side_effect=[first, redo]) as post:
        result = client.transcribe(audio)

    assert post.call_count == 2
    assert result.text.count("class in the country") == 1


def test_a_re_decode_never_runs_past_its_span(client, tmp_path):
    audio = _wav(tmp_path / "talk.wav", 30)
    first = _payload(*_looping(0.0, 10), (10.0, 30.0, " The rest."))
    overlong = _payload((0.0, 6.0, " Real start."), (6.0, 14.0, " Too long."))

    with patch.object(client, "_post_multipart", side_effect=[first, overlong]):
        result = client.transcribe(audio)

    assert [s.end for s in result.segments] == [6.0, 10.0, 30.0]


def test_a_short_real_repetition_is_left_alone(client, tmp_path):
    audio = _wav(tmp_path / "talk.wav", 30)
    applause = _payload((0.0, 5.0, " Stay hungry."), *_looping(5.0, 4, " Thank you."))

    with patch.object(client, "_post_multipart", return_value=applause) as post:
        result = client.transcribe(audio)

    assert post.call_count == 1
    assert result.text.count("Thank you.") == 4
