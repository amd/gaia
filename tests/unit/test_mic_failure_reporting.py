# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A microphone that does not work has to say so, not print "Listening…".

`gaia talk` spun on "Listening…" indefinitely when the mic failed: capture runs
on its own thread, so the `raise` reached nobody, `is_recording` stayed set, and
the supervisor loop had no reason to stop waiting. The device error was logged
at debug level, so nothing reached the user (#3554).

Two more claims on the same screen were false: every launch advertised
Enter-to-interrupt, whose only implementation nothing calls, and the guide told
users to say "exit" or "quit" when the loop matches only "stop".

No audio hardware required — `sounddevice` is patched, which is exactly the
failure being reproduced.
"""

from __future__ import annotations

import queue
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("numpy")

# ``sounddevice`` needs real audio hardware to install on a CI runner, and the
# modules under test already treat it as optional (``sd = None`` on
# ImportError). A stand-in keeps these tests running everywhere — and a mic
# failure is exactly what they reproduce, so the fake IS the scenario.
if "sounddevice" not in sys.modules:
    _fake = types.ModuleType("sounddevice")
    _fake.query_devices = lambda *a, **k: {"index": 0, "name": "Fake Mic"}
    _fake.InputStream = MagicMock()
    _fake.OutputStream = MagicMock()
    sys.modules["sounddevice"] = _fake

import sounddevice as sd  # noqa: E402

from gaia.audio.audio_recorder import AudioRecorder  # noqa: E402


@pytest.fixture
def recorder():
    """A recorder with the device layer stubbed out."""
    with patch.object(
        sd, "query_devices", return_value={"index": 0, "name": "Fake Mic"}
    ):
        rec = AudioRecorder(device_index=0)
    rec.log = MagicMock()
    return rec


class TestACaptureFailureStopsRecording:
    """The flag is the only way out of the supervisor's wait loop."""

    def test_a_device_that_cannot_open_clears_is_recording(self, recorder):
        recorder.is_recording = True
        with patch.object(sd, "InputStream", side_effect=OSError("device busy")):
            recorder._record_audio()

        assert recorder.is_recording is False

    def test_a_device_that_cannot_open_records_why(self, recorder):
        recorder.is_recording = True
        with patch.object(sd, "InputStream", side_effect=OSError("device busy")):
            recorder._record_audio()

        assert recorder.mic_error
        assert "device busy" in recorder.mic_error

    def test_a_read_failure_mid_session_also_clears_it(self, recorder):
        stream = MagicMock()
        stream.read.side_effect = OSError("input overflowed")
        recorder.is_recording = True
        with patch.object(sd, "InputStream", return_value=stream):
            recorder._record_audio()

        assert recorder.is_recording is False
        assert "input overflowed" in recorder.mic_error

    def test_it_does_not_raise_out_of_the_thread(self, recorder):
        """Raising here reached nobody; that is why the flag matters."""
        recorder.is_recording = True
        with patch.object(sd, "InputStream", side_effect=OSError("device busy")):
            recorder._record_audio()  # must not raise


class TestTheMessageIsActionable:
    def test_it_names_the_device(self, recorder):
        message = recorder.device_error_message("open", OSError("nope"))
        assert "Fake Mic" in message
        assert "[0]" in message

    def test_it_says_what_to_try_next(self, recorder):
        message = recorder.device_error_message("open", OSError("nope"))
        assert "--audio-device-index" in message
        assert "asr-list-audio-devices" in message

    def test_it_copes_when_even_the_device_query_fails(self, recorder):
        with patch.object(sd, "query_devices", side_effect=OSError("no such device")):
            message = recorder.device_error_message("open", OSError("nope"))
        assert "index 0" in message

    def test_it_names_the_default_device_when_none_was_chosen(self, recorder):
        recorder.device_index = None
        with patch.object(sd, "query_devices", side_effect=OSError("boom")):
            message = recorder.device_error_message("open", OSError("nope"))
        assert "default input device" in message


class TestStartingRecordingClearsAStaleError:
    def test_a_previous_failure_does_not_persist(self, recorder):
        recorder.mic_error = "something from last time"
        recorder.is_recording = False
        with (
            patch.object(sd, "InputStream", return_value=MagicMock()),
            patch("threading.Thread"),
        ):
            recorder.start_recording()

        assert recorder.mic_error is None


class TestTheLaunchBannerOnlyClaimsWhatWorks:
    """Enter-to-interrupt was advertised and never implemented."""

    @staticmethod
    def _banner_source() -> str:
        import inspect

        from gaia.audio.audio_client import AudioClient

        return inspect.getsource(AudioClient.start_voice_chat)

    def test_it_no_longer_advertises_enter_to_interrupt(self):
        assert "Press Enter key to stop" not in self._banner_source()

    def test_it_still_names_the_words_the_loop_matches(self):
        source = self._banner_source()
        assert "'stop'" in source
        assert "'restart'" in source


class TestTheGuideMatchesTheCode:
    """The doc said "exit"/"quit"; only "stop" is recognised."""

    @staticmethod
    def _guide() -> str:
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        return (root / "docs" / "guides" / "talk.mdx").read_text(encoding="utf-8")

    def test_it_does_not_tell_users_to_say_exit_or_quit(self):
        guide = self._guide()
        assert 'Say "exit" or "quit"' not in guide
        assert 'Say **"exit"** or **"quit"**' not in guide

    def test_it_tells_users_to_say_stop(self):
        assert 'Say **"stop"**' in self._guide()

    def test_it_does_not_advertise_enter_to_interrupt(self):
        assert "Press **Enter** during audio" not in self._guide()

    def test_the_stop_word_in_the_guide_is_the_one_the_code_matches(self):
        import inspect

        from gaia.audio.audio_client import AudioClient

        source = inspect.getsource(AudioClient._process_audio_wrapper)
        assert 'cleaned_text in ["stop"]' in source


class TestAFailedSpeakerDoesNotWedgeTheAnswer:
    """The producer puts onto a bounded queue; a dead consumer blocked it."""

    def test_the_drain_helper_releases_the_producer(self):
        from gaia.audio.kokoro_tts import KokoroTTS

        text_queue = queue.Queue(maxsize=3)
        text_queue.put("one")
        text_queue.put("__END__")

        KokoroTTS._drain_text_queue(text_queue)

        assert text_queue.empty()

    @pytest.mark.parametrize("terminator", ["__END__", "__HALT__", None])
    def test_it_stops_at_every_terminator(self, terminator):
        from gaia.audio.kokoro_tts import KokoroTTS

        text_queue = queue.Queue()
        text_queue.put(terminator)

        KokoroTTS._drain_text_queue(text_queue)

        assert text_queue.empty()

    def test_the_output_stream_is_opened_inside_a_try(self):
        """Opened outside it, a device failure killed the consumer thread."""
        import inspect

        from gaia.audio.kokoro_tts import KokoroTTS

        source = inspect.getsource(KokoroTTS.generate_speech_streaming)
        before_open = source.split("sd.OutputStream")[0]
        assert "try:" in before_open
        assert "_drain_text_queue" in source


# ============================================================================
# The success path: quitting normally must not look like a failure
# ============================================================================


class TestARequestedStopIsNotReportedAsAFailure:
    """The gap the review found: nothing exercised a normal launch-and-quit.

    `stop_recording()` clears `is_recording` and *then* joins threads that may
    be mid-transcribe. Through that whole window the supervisor sees "not
    recording, thread still alive" — the same state a dead microphone leaves —
    and its 0.1s poll makes that essentially every quit, not a rare race.
    """

    @staticmethod
    def _client():
        from gaia.audio.audio_client import AudioClient

        client = AudioClient.__new__(AudioClient)
        client.log = MagicMock()
        client._stop_requested = False
        client.whisper_asr = MagicMock()
        client.whisper_asr.mic_error = None
        client.whisper_asr.is_recording = False
        return client

    @staticmethod
    def _supervisor_message(client):
        """The branch the supervisor loop takes, as the user would see it."""
        reason = getattr(client.whisper_asr, "mic_error", None)
        if reason:
            return reason
        if not client._stop_requested:
            return "Recording stopped. Voice input is not active."
        return None

    def test_a_requested_stop_prints_nothing(self):
        client = self._client()
        client._stop_requested = True

        assert self._supervisor_message(client) is None

    def test_an_unrequested_stop_still_reports(self):
        client = self._client()

        assert "not active" in self._supervisor_message(client)

    def test_a_mic_error_wins_over_everything(self):
        client = self._client()
        client._stop_requested = True
        client.whisper_asr.mic_error = "Microphone unavailable: could not open …"

        assert "Microphone unavailable" in self._supervisor_message(client)

    def test_the_stop_word_sets_the_flag_before_stopping(self):
        """Order matters: set after, and the supervisor already misreported."""
        import inspect

        from gaia.audio.audio_client import AudioClient

        source = inspect.getsource(AudioClient._process_audio_wrapper)
        flag = source.index("self._stop_requested = True")
        stop = source.index("self.whisper_asr.stop_recording()", flag - 400)
        assert flag < stop

    def test_ctrl_c_sets_it_too(self):
        import inspect

        from gaia.audio.audio_client import AudioClient

        source = inspect.getsource(AudioClient.start_voice_chat)
        block = source.split("except KeyboardInterrupt:")[1][:300]
        assert "self._stop_requested = True" in block

    def test_a_new_session_clears_a_previous_stop(self):
        import inspect

        from gaia.audio.audio_client import AudioClient

        source = inspect.getsource(AudioClient.start_voice_chat)
        assert "self._stop_requested = False" in source


class TestDroppedSpeechIsDecidedOnce:
    """ "Dropped for the rest of this reply" has to mean it.

    Without a latch every remaining chunk waits the full timeout again, so a
    long answer stalls in five-second steps — the hang, slower.
    """

    @staticmethod
    def _speak_with_a_full_queue():
        """Rebuild the guard over a queue nothing consumes."""
        import queue as _queue

        text_queue = _queue.Queue(maxsize=1)
        text_queue.put("already full")
        log = MagicMock()
        state = {"dropped": False}

        def speak(item):
            if state["dropped"]:
                return False
            try:
                text_queue.put(item, timeout=0.05)
                return True
            except _queue.Full:
                state["dropped"] = True
                log.error("dropped")
                return False

        return speak, log, state

    def test_the_first_failure_latches(self):
        speak, _, state = self._speak_with_a_full_queue()

        assert speak("one") is False
        assert state["dropped"] is True

    def test_later_chunks_return_immediately(self):
        import time

        speak, _, _ = self._speak_with_a_full_queue()
        speak("one")  # latches

        started = time.time()
        for i in range(50):
            assert speak(f"chunk {i}") is False
        assert time.time() - started < 0.05

    def test_the_error_is_logged_once_not_per_chunk(self):
        speak, log, _ = self._speak_with_a_full_queue()

        for i in range(10):
            speak(f"chunk {i}")

        assert log.error.call_count == 1

    def test_the_real_guard_is_latched(self):
        """Pins the production code, not just this reconstruction.

        The guard lives in ``process_voice_input``, which nothing in the tree
        calls today — see ``TestTheLivePlaybackPathIsBounded`` for the path
        ``gaia talk`` actually takes.
        """
        import inspect

        from gaia.audio.audio_client import AudioClient

        source = inspect.getsource(AudioClient.process_voice_input)
        assert "speech_dropped = False" in source
        assert "nonlocal speech_dropped" in source
        assert "if speech_dropped:" in source


class TestTheLivePlaybackPathIsBounded:
    """``gaia talk`` speaks through ``speak_text``, not ``process_voice_input``.

    TalkSDK → ``start_voice_chat(voice_processor)`` → ``_process_audio_wrapper``
    → the callback → ``speak_text``. The queue hang #3554 describes has the same
    shape on both, but only this one is reachable.
    """

    def test_speak_text_never_puts_without_a_timeout(self):
        import inspect

        from gaia.audio.audio_client import AudioClient

        source = inspect.getsource(AudioClient.speak_text)
        for line in source.splitlines():
            if "text_queue.put(" in line:
                assert "timeout=" in line or "put_nowait" in line, line

    def test_speak_text_survives_a_consumer_that_never_reads(self, monkeypatch):
        """A dead TTS thread must not block the caller forever."""
        import asyncio

        from gaia.audio.audio_client import AudioClient

        client = AudioClient.__new__(AudioClient)
        client.log = MagicMock()
        client.enable_tts = True
        client.tts = MagicMock()
        # The thread starts and immediately does nothing — the broken-speaker
        # case, where generate_speech_streaming used to die on stream open.
        client.tts.generate_speech_streaming = lambda *a, **k: None

        real_queue_cls = queue.Queue
        monkeypatch.setattr(
            "gaia.audio.audio_client.queue.Queue",
            lambda maxsize=0: real_queue_cls(maxsize=1),
        )

        async def run():
            # Completes rather than hanging; the timeout is the proof. Bounded
            # puts make this ~10s worst case; unbounded, it never returns.
            await asyncio.wait_for(client.speak_text("one two three"), timeout=30)

        asyncio.run(run())


class TestTheTalkReadmeMatchesTheCode:
    @staticmethod
    def _readme() -> str:
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        return (root / "src" / "gaia" / "talk" / "README.md").read_text(
            encoding="utf-8"
        )

    def test_it_does_not_advertise_enter_to_interrupt(self):
        assert "Press Enter" not in self._readme()

    def test_it_still_documents_the_words_that_work(self):
        readme = self._readme()
        assert '"stop"' in readme
        assert '"restart"' in readme
