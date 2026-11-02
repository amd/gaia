# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for whole-recording analysis of a timed transcript."""

import json
import re
import threading
import time

import pytest

from gaia.audio.transcript_analysis import (
    Item,
    MalformedReply,
    ReplyOverflow,
    _parse_merge_reply,
    analyze_segments,
    build_windows,
    format_timestamp,
    merge_boundary_items,
    parse_timestamp,
    render_analysis,
    to_srt,
    to_vtt,
    transcript_lines,
)


def _segments(
    count: int, seconds: float = 5.0, text: str = "We do the next move."
) -> list:
    return [
        {
            "start": i * seconds,
            "end": i * seconds + seconds - 0.5,
            "text": f"{text} {i}",
        }
        for i in range(count)
    ]


def _item(title, start, end, **fields) -> dict:
    return {"title": title, "start": start, "end": end, "fields": fields}


def _reply(*items, summary="Part of the recording.") -> str:
    return json.dumps({"items": list(items), "summary": summary})


class FakeModel:
    """Answers window, boundary and overview prompts, and records them."""

    def __init__(self, window_reply, boundary_reply="NONE"):
        self.window_reply = window_reply
        self.boundary_reply = boundary_reply
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if "<transcript>" in prompt:
            return self.window_reply(prompt)
        if "numbered pair" in prompt:
            return self.boundary_reply
        return "An overview of the recording."

    @property
    def window_calls(self):
        return sum("<transcript>" in prompt for prompt in self.prompts)


def _first_stamp(prompt: str) -> str:
    return re.search(r"<transcript>\n\[([\d:]+)\]", prompt).group(1)


class TestTimestamps:
    @pytest.mark.parametrize(
        "seconds,expected",
        [(0, "00:00"), (333.9, "05:33"), (3599, "59:59"), (3770, "01:02:50")],
    )
    def test_format(self, seconds, expected):
        assert format_timestamp(seconds) == expected

    @pytest.mark.parametrize(
        "text,expected",
        [("05:33", 333), ("[05:33]", 333), ("01:02:50", 3770), ("1:02:50", 3770)],
    )
    def test_parse(self, text, expected):
        assert parse_timestamp(text) == expected

    @pytest.mark.parametrize("text", ["", "soon", "12", None, "05:33 → 06:17"])
    def test_parse_rejects_what_is_not_a_time(self, text):
        assert parse_timestamp(text) is None


class TestWindows:
    def test_every_segment_is_read_and_neighbours_overlap(self):
        segments = _segments(4000)
        windows = build_windows(segments)

        assert len(windows) > 1
        seen = {line for window in windows for line in window.lines}
        assert seen == set(transcript_lines(segments))
        for earlier, later in zip(windows, windows[1:]):
            assert later.start < earlier.end, "an item at the seam needs both sides"

    def test_windows_respect_the_size_limit(self):
        for window in build_windows(_segments(4000), max_chars=2000):
            assert len(window.text) <= 2000

    def test_a_short_recording_is_one_window(self):
        assert len(build_windows(_segments(5))) == 1

    def test_one_line_longer_than_a_window_still_ends(self):
        windows = build_windows([{"start": 0, "end": 9, "text": "x" * 500}] * 3, 100)
        assert len(windows) >= 3

    def test_silence_produces_no_windows(self):
        assert build_windows([{"start": 0, "end": 1, "text": "  "}]) == []

    def test_speakers_are_named_only_when_there_is_more_than_one(self):
        solo = [{"start": 0, "end": 1, "text": "Hello", "speaker": "Speaker 1"}]
        duo = solo + [{"start": 2, "end": 3, "text": "Hi", "speaker": "Speaker 2"}]

        assert transcript_lines(solo) == ["[00:00] Hello"]
        assert transcript_lines(duo)[1] == "[00:02] Speaker 2: Hi"


class TestAnalyze:
    def test_items_keep_verified_times_and_requested_fields(self):
        model = FakeModel(
            lambda _p: _reply(_item("Lunge", "00:10", "00:20", cues="Slow"))
        )

        result = analyze_segments(_segments(10), "every exercise", ["cues"], model)

        item = result.items[0]
        assert item.title == "Lunge"
        assert (item.start, item.end) == (10.0, 24.5), "end is where that line ends"
        assert item.fields == {"cues": "Slow"}
        assert item.timed
        assert result.overview == "An overview of the recording."

    def test_a_timestamp_matching_no_line_is_marked_not_trusted(self):
        model = FakeModel(lambda _p: _reply(_item("Lunge", "09:59", "10:30")))

        item = analyze_segments(_segments(10), "x", [], model).items[0]

        assert item.timed is False
        assert (item.start, item.end) == (0.0, 49.5), "falls back to the section"

    def test_a_field_the_model_skipped_is_said_to_be_missing(self):
        model = FakeModel(lambda _p: _reply(_item("Lunge", "00:10", "00:15", cues="")))

        item = analyze_segments(_segments(10), "x", ["cues", "reps"], model).items[0]

        assert item.fields == {"cues": "not stated", "reps": "not stated"}

    def test_a_long_recording_reads_every_window_and_merges_nothing_it_did_not_ask(
        self,
    ):
        def reply(prompt):
            stamp = _first_stamp(prompt)
            return _reply(_item(f"Move at {stamp}", stamp, stamp))

        model = FakeModel(reply)
        result = analyze_segments(_segments(4000), "every move", [], model)

        assert result.windows > 3
        assert model.window_calls == result.windows
        assert len(result.items) == result.windows

    def test_an_item_split_by_a_window_edge_is_merged_once(self):
        segments = _segments(4000)
        windows = build_windows(segments)
        seam = windows[1].starts[0]
        stamp = format_timestamp(seam)

        def reply(prompt):
            if _first_stamp(prompt) == format_timestamp(windows[0].starts[0]):
                return _reply(_item("Plank", stamp, stamp, cues="Brace"))
            if _first_stamp(prompt) == stamp:
                return _reply(_item("Plank hold", stamp, stamp, reps="Twice"))
            return _reply()

        model = FakeModel(reply, boundary_reply="1")
        result = analyze_segments(segments, "x", ["cues", "reps"], model)

        assert len(result.items) == 1
        assert result.items[0].fields == {"cues": "Brace", "reps": "Twice"}

    def test_a_repeat_later_on_is_never_offered_for_merging(self):
        """Left then right, or a repeat, must stay two entries."""
        segments = _segments(4000)
        windows = build_windows(segments)

        def reply(prompt):
            stamp = _first_stamp(prompt)
            return _reply(_item("Same move", stamp, stamp))

        model = FakeModel(reply, boundary_reply="1, 2, 3")
        result = analyze_segments(segments, "x", [], model)

        assert len(result.items) == len(windows)
        assert not any("numbered pair" in prompt for prompt in model.prompts)

    def test_a_cut_off_item_runs_on_to_the_next(self):
        reply = _reply(
            _item("First", "00:10", "00:10"),
            _item("Second", "00:30", "00:45"),
            _item("Last", "00:50", "00:50"),
        )
        items = analyze_segments(
            _segments(11), "x", [], FakeModel(lambda _p: reply)
        ).items

        assert items[0].end == 30.0
        assert items[1].end == 49.5, "a real range is left alone"
        assert items[2].end == 54.5, "nothing follows the last item"

    def test_a_cut_off_item_never_runs_on_past_a_long_gap(self):
        segments = _segments(4) + [
            {"start": 600.0, "end": 604.0, "text": "Much later."}
        ]
        reply = _reply(
            _item("First", "00:00", "00:00"), _item("Second", "10:00", "10:00")
        )

        items = analyze_segments(segments, "x", [], FakeModel(lambda _p: reply)).items

        assert items[0].end == 4.5

    def test_an_overflowing_window_is_halved_and_loses_nothing(self):
        calls = []

        def reply(prompt):
            calls.append(prompt)
            if len(calls) == 1:
                raise ReplyOverflow("cut off")
            stamp = _first_stamp(prompt)
            return _reply(_item(f"Move {stamp}", stamp, stamp))

        result = analyze_segments(_segments(100), "x", [], FakeModel(reply))

        assert len(calls) == 3
        assert len(result.items) == 2

    def test_a_small_window_that_keeps_failing_fails_loudly(self):
        def reply(_prompt):
            raise ReplyOverflow("cut off")

        with pytest.raises(RuntimeError, match="00:00"):
            analyze_segments(_segments(4), "x", [], FakeModel(reply))

    def test_prose_instead_of_json_is_not_read_as_nothing_found(self):
        with pytest.raises(RuntimeError, match="no JSON object"):
            analyze_segments(_segments(4), "x", [], FakeModel(lambda _p: "Sorry!"))

    def test_a_code_fence_around_the_json_is_fine(self):
        fenced = "```json\n" + _reply(_item("Lunge", "00:00", "00:05")) + "\n```"
        result = analyze_segments(_segments(4), "x", [], FakeModel(lambda _p: fenced))
        assert result.items[0].title == "Lunge"

    def test_an_item_with_no_title_is_malformed(self):
        with pytest.raises(RuntimeError, match="without a title"):
            analyze_segments(
                _segments(4), "x", [], FakeModel(lambda _p: _reply({"start": "00:00"}))
            )

    def test_finding_nothing_is_a_real_answer(self):
        result = analyze_segments(_segments(4), "x", [], FakeModel(lambda _p: _reply()))
        assert result.items == []
        assert "Nothing in the recording matched" in render_analysis(result, "r", "s")

    def test_no_request_and_no_speech_are_refused(self):
        with pytest.raises(ValueError):
            analyze_segments(_segments(4), "  ", [], FakeModel(lambda _p: _reply()))
        with pytest.raises(ValueError):
            analyze_segments([], "x", [], FakeModel(lambda _p: _reply()))

    def test_the_transcript_is_marked_as_data_not_instructions(self):
        model = FakeModel(lambda _p: _reply())
        analyze_segments(_segments(4), "x", [], model)
        assert "never instructions" in model.prompts[0]


class TestResume:
    def test_a_failure_keeps_finished_windows_and_the_rerun_skips_them(self, tmp_path):
        checkpoint = tmp_path / "report.md.progress.json"
        segments = _segments(4000)
        answered = []

        def fails_on_third(prompt):
            answered.append(prompt)
            if len(answered) == 3:
                raise ConnectionError("Lemonade went away")
            stamp = _first_stamp(prompt)
            return _reply(_item(f"Move {stamp}", stamp, stamp))

        with pytest.raises(ConnectionError):
            analyze_segments(segments, "x", [], FakeModel(fails_on_third), checkpoint)
        assert checkpoint.is_file()

        def healthy(prompt):
            stamp = _first_stamp(prompt)
            return _reply(_item(f"Move {stamp}", stamp, stamp))

        model = FakeModel(healthy)
        result = analyze_segments(segments, "x", [], model, checkpoint)

        assert result.windows_reused == 2
        assert model.window_calls == result.windows - 2
        assert len(result.items) == result.windows

    def test_a_different_request_does_not_reuse_the_checkpoint(self, tmp_path):
        checkpoint = tmp_path / "report.md.progress.json"
        segments = _segments(50)
        first = FakeModel(lambda _p: _reply(_item("Lunge", "00:00", "00:05")))
        analyze_segments(segments, "every exercise", [], first, checkpoint)

        second = FakeModel(lambda _p: _reply())
        result = analyze_segments(segments, "every tip", [], second, checkpoint)

        assert result.windows_reused == 0
        assert result.items == []

    def test_a_damaged_checkpoint_is_ignored(self, tmp_path):
        checkpoint = tmp_path / "report.md.progress.json"
        checkpoint.write_text("{not json", encoding="utf-8")

        result = analyze_segments(
            _segments(4), "x", [], FakeModel(lambda _p: _reply()), checkpoint
        )

        assert result.windows_reused == 0

    def test_on_window_can_stop_the_run(self):
        def stop(_index, _total, _window):
            raise KeyboardInterrupt

        model = FakeModel(lambda _p: _reply())
        with pytest.raises(KeyboardInterrupt):
            analyze_segments(_segments(4), "x", [], model, on_window=stop)
        assert model.prompts == []


class TestParallel:
    @staticmethod
    def _reply(prompt):
        stamp = _first_stamp(prompt)
        return _reply(_item(f"Move {stamp}", stamp, stamp), summary=f"Part {stamp}")

    def test_the_result_does_not_depend_on_which_window_finishes_first(self):
        serial = analyze_segments(_segments(4000), "x", [], FakeModel(self._reply))
        model = FakeModel(self._reply)
        both = analyze_segments(_segments(4000), "x", [], model, parallel=4)

        assert [i.title for i in both.items] == [i.title for i in serial.items]

    def test_windows_really_overlap_in_time(self):
        lock = threading.Lock()
        running = [0, 0]

        def reply(prompt):
            if "<transcript>" in prompt:
                with lock:
                    running[0] += 1
                    running[1] = max(running[1], running[0])
                time.sleep(0.05)
                with lock:
                    running[0] -= 1
            return self._reply(prompt) if "<transcript>" in prompt else "Overview."

        analyze_segments(_segments(4000), "x", [], reply, parallel=4)

        assert running[1] > 1

    def test_a_failure_keeps_what_finished_and_a_rerun_completes(self, tmp_path):
        checkpoint = tmp_path / "report.md.progress.json"
        segments = _segments(4000)
        last = format_timestamp(build_windows(segments)[-1].start)

        def fails_on_the_last(prompt):
            if _first_stamp(prompt) == last:
                raise ConnectionError("provider went away")
            return self._reply(prompt)

        with pytest.raises(ConnectionError):
            analyze_segments(
                segments, "x", [], FakeModel(fails_on_the_last), checkpoint, parallel=4
            )

        model = FakeModel(self._reply)
        result = analyze_segments(segments, "x", [], model, checkpoint, parallel=4)

        assert result.windows_reused >= 1
        assert len(result.items) == result.windows

    def test_on_window_can_stop_a_parallel_run(self):
        def stop(_index, _total, _window):
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            analyze_segments(
                _segments(4000),
                "x",
                [],
                FakeModel(self._reply),
                on_window=stop,
                parallel=2,
            )

    def test_window_prompts_use_the_json_channel_and_prose_does_not(self):
        json_calls, prose_calls = [], []

        def as_json(prompt):
            json_calls.append(prompt)
            return _reply()

        def as_prose(prompt):
            prose_calls.append(prompt)
            return "Overview."

        analyze_segments(_segments(4), "x", [], as_prose, complete_json=as_json)

        assert len(json_calls) == 1 and "<transcript>" in json_calls[0]
        assert len(prose_calls) == 1 and "<transcript>" not in prose_calls[0]


class TestRender:
    def test_report_shows_coverage_and_marks_approximate_times(self):
        def reply(_prompt):
            return _reply(
                _item("Exact", "00:00", "00:05", cues="Slow"),
                _item("Guessed", "59:00", "59:30", cues="Fast"),
            )

        result = analyze_segments(_segments(10), "x", ["cues"], FakeModel(reply))
        report = render_analysis(result, "Workshop", "C:\\v\\Workshop.mp4")

        assert "# Analysis — Workshop" in report
        assert "read in 1 overlapping sections" in report
        assert "1. **Exact** (00:00 → 00:09)" in report
        assert "**Guessed** (~00:00 → 00:49)" in report
        assert "   - **cues:** Slow" in report
        assert "1 timestamp(s) marked ~" in report

    def test_malformed_reply_is_a_value_error(self):
        assert issubclass(MalformedReply, ValueError)


class TestSubtitles:
    SEGMENTS = [
        {"start": 1.5, "end": 3.25, "text": " Hello there.", "speaker": "Ana"},
        {"start": 3661.0, "end": 3662.0, "text": "Goodbye.", "speaker": "Ben"},
        {"start": 4000.0, "end": 4001.0, "text": "   ", "speaker": "Ben"},
    ]

    def test_srt(self):
        assert to_srt(self.SEGMENTS) == (
            "1\n00:00:01,500 --> 00:00:03,250\nAna: Hello there.\n\n"
            "2\n01:01:01,000 --> 01:01:02,000\nBen: Goodbye.\n"
        )

    def test_vtt(self):
        assert to_vtt(self.SEGMENTS) == (
            "WEBVTT\n\n00:00:01.500 --> 00:00:03.250\nAna: Hello there.\n\n"
            "01:01:01.000 --> 01:01:02.000\nBen: Goodbye.\n"
        )

    def test_one_speaker_is_not_repeated_on_every_cue(self):
        solo = [dict(segment, speaker="Ana") for segment in self.SEGMENTS[:2]]
        assert "Ana:" not in to_srt(solo)


class TestReviewFindings:
    def test_an_item_cut_by_a_split_window_is_offered_for_merging(self):
        """Halves of one window share nothing, so each can report the same item."""
        calls = []

        def reply(prompt):
            calls.append(prompt)
            if len(calls) == 1:
                raise ReplyOverflow("cut off")
            stamp = _first_stamp(prompt)
            return _reply(_item("Same recipe", stamp, stamp))

        model = FakeModel(reply, boundary_reply="1")
        result = analyze_segments(_segments(20), "x", [], model)

        assert any("numbered pair" in prompt for prompt in model.prompts)
        assert len(result.items) == 1

    @pytest.mark.parametrize(
        "reply",
        [
            "None of the 1 pairs match.",
            "Pair 1 is different.",
            "I think 1 might be the same but I am not sure.",
            "NONE",
            "none.",
        ],
    )
    def test_only_a_clear_answer_merges_anything(self, reply):
        segments = _segments(4000)
        seam = format_timestamp(build_windows(segments)[1].starts[0])

        def read(prompt):
            return _reply(_item(f"Move {_first_stamp(prompt)}", seam, seam))

        result = analyze_segments(
            segments, "x", [], FakeModel(read, boundary_reply=reply)
        )

        assert len(result.items) == result.windows

    @pytest.mark.parametrize(
        "reply,expected", [("1", [1]), ("2, 1.", [1, 2]), ("9", [])]
    )
    def test_a_clear_answer_is_read_in_range(self, reply, expected):
        assert sorted(_parse_merge_reply(reply, 3)) == expected

    def test_a_long_list_of_pairs_is_checked_in_batches(self):
        items = [
            Item(
                title=f"Tip {i}",
                start=i * 8.0,
                end=i * 8.0 + 4,
                fields={},
                window=str(i),
            )
            for i in range(400)
        ]
        prompts = []

        def reply(prompt):
            prompts.append(prompt)
            return "NONE"

        merge_boundary_items(items, reply)

        assert len(prompts) > 1
        assert max(len(prompt) for prompt in prompts) < 40_000

    @pytest.mark.parametrize(
        "fields",
        [["a list"], "text", {"other name": "value"}],
        ids=["list", "string", "wrong keys"],
    )
    def test_a_wrong_shape_is_refused_not_read_as_not_stated(self, fields):
        def reply(_prompt):
            return json.dumps(
                {
                    "items": [
                        {
                            "title": "X",
                            "start": "00:00",
                            "end": "00:05",
                            "fields": fields,
                        }
                    ],
                    "summary": "s",
                }
            )

        with pytest.raises(RuntimeError, match="fields"):
            analyze_segments(_segments(4), "x", ["cues"], FakeModel(reply))

    def test_stop_during_a_parallel_read_does_not_wait_for_calls_in_flight(self):
        release = threading.Event()
        stopped = threading.Event()

        def blocked(prompt):
            if "<transcript>" in prompt:
                release.wait(30)
            return _reply()

        def check():
            if stopped.is_set():
                raise KeyboardInterrupt

        threading.Timer(0.3, stopped.set).start()
        started = time.monotonic()
        try:
            with pytest.raises(KeyboardInterrupt):
                analyze_segments(
                    _segments(4000),
                    "x",
                    [],
                    FakeModel(blocked),
                    parallel=4,
                    check=check,
                )
        finally:
            release.set()

        assert time.monotonic() - started < 5

    def test_a_finished_window_is_saved_before_the_run_is_stopped(self, tmp_path):
        checkpoint = tmp_path / "report.md.progress.json"

        def stop(_index, _total, _window):
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            analyze_segments(
                _segments(4000),
                "x",
                [],
                FakeModel(lambda _p: _reply()),
                checkpoint,
                on_window=stop,
                parallel=2,
            )

        assert checkpoint.is_file()
