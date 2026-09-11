# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""`gaia talk` must not hear itself, and must exit non-zero when the loop dies.

No real audio device is involved: TTS and the recorder are stand-ins that keep
the same pause/resume state the real WhisperAsr exposes.
"""

import asyncio
import sys
import threading
import time
import types
from unittest.mock import MagicMock, patch

import pytest

from gaia.audio.audio_client import AudioClient

# asyncio uses a local socketpair for its Windows event-loop wakeup.
pytestmark = pytest.mark.allow_network


def _run(coro):
    loop = asyncio.SelectorEventLoop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _client(**kwargs):
    with patch("gaia.audio.audio_client.create_client"):
        return AudioClient(**kwargs)


class _Mic:
    """WhisperAsr's pause interface with real state instead of a mock."""

    def __init__(self):
        self.is_paused = False
        self.resumes = 0

    def pause_recording(self):
        self.is_paused = True

    def resume_recording(self):
        self.is_paused = False
        self.resumes += 1


class _PlayingTTS:
    """Consumes text like KokoroTTS, then plays until released, sampling the mic."""

    def __init__(self, mic):
        self.mic = mic
        self.release = threading.Event()
        self.mic_paused_samples = []
        self.text = ""

    def generate_speech_streaming(
        self, text_queue, status_callback=None, interrupt_event=None
    ):
        self.mic_paused_samples.append(self.mic.is_paused)
        while (chunk := text_queue.get(timeout=5)) != "__END__":
            self.text += chunk
        status_callback(True)
        while not self.release.wait(0.02):
            self.mic_paused_samples.append(self.mic.is_paused)
        self.mic_paused_samples.append(self.mic.is_paused)
        status_callback(False)


def _speak_in_background(client, text, method="speak_text"):
    client.llm_client.is_generating.return_value = False
    client.llm_client.generate.return_value = [text]
    client.llm_client.get_performance_stats.return_value = None
    runner = threading.Thread(target=lambda: _run(getattr(client, method)(text)))
    runner.start()
    return runner


@pytest.mark.parametrize("method", ["speak_text", "process_voice_input"])
def test_mic_is_paused_for_the_whole_utterance_and_resumes_after(method):
    client = _client(enable_tts=True)
    mic = _Mic()
    client.whisper_asr = mic
    tts = _PlayingTTS(mic)
    client.tts = tts

    runner = _speak_in_background(client, "hello there", method)
    try:
        time.sleep(0.3)
        assert runner.is_alive(), "speak_text returned while audio was still playing"
        assert mic.is_paused is True
        client.transcription_queue.put("hello there")  # the mic heard the speaker
    finally:
        tts.release.set()
        runner.join(timeout=5)

    assert not runner.is_alive()
    assert tts.text == "hello there"
    assert len(tts.mic_paused_samples) > 3
    assert all(tts.mic_paused_samples), "mic was live during playback"
    assert mic.is_paused is False and mic.resumes == 1
    assert client.transcription_queue.empty(), "self-transcription was not dropped"
    assert client.is_speaking is False


def test_voice_processor_does_not_resume_on_early_status_callback():
    client = _client(enable_tts=True)
    mic = _Mic()
    client.whisper_asr = mic
    callback_sent = threading.Event()

    class EarlyStatusTTS(_PlayingTTS):
        def generate_speech_streaming(
            self, text_queue, status_callback, interrupt_event
        ):
            status_callback(False)
            callback_sent.set()
            super().generate_speech_streaming(
                text_queue, status_callback, interrupt_event
            )

    tts = EarlyStatusTTS(mic)
    client.tts = tts
    runner = _speak_in_background(client, "hello", "process_voice_input")
    try:
        assert callback_sent.wait(2)
        assert mic.is_paused
        assert mic.resumes == 0
    finally:
        tts.release.set()
        runner.join(5)
    assert not runner.is_alive()
    assert mic.resumes == 1


@pytest.mark.parametrize("method", ["speak_text", "process_voice_input"])
@pytest.mark.parametrize("error_cls, paused", [(OSError, False), (TimeoutError, True)])
def test_playback_failure_propagates_and_timeout_keeps_mic_muted(
    method, error_cls, paused
):
    client = _client(enable_tts=True)
    client.whisper_asr = _Mic()
    client.llm_client.is_generating.return_value = False
    client.llm_client.generate.return_value = ["hello"]
    client.llm_client.get_performance_stats.return_value = None
    client.tts = MagicMock()
    client.tts.generate_speech_streaming.side_effect = error_cls("output stalled")
    with pytest.raises(RuntimeError, match="output stalled"):
        _run(getattr(client, method)("hello"))
    assert client.whisper_asr.is_paused is paused


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_tts_crash_mid_speech_resumes_mic_and_is_reported():
    client = _client(enable_tts=True)
    mic = _Mic()
    client.whisper_asr = mic

    class _CrashingTTS:
        def generate_speech_streaming(self, text_queue, **_kwargs):
            text_queue.get(timeout=5)
            raise OSError("audio output device lost")

    client.tts = _CrashingTTS()

    with pytest.raises(RuntimeError, match="audio output device lost"):
        _run(client.speak_text("hello"))
    assert mic.is_paused is False, "a TTS crash left the microphone dead"
    assert client.is_speaking is False


def test_crash_before_playback_starts_resumes_mic():
    client = _client(enable_tts=True)
    mic = _Mic()
    client.whisper_asr = mic
    client.tts = MagicMock()

    with (
        patch.object(
            threading.Thread, "start", side_effect=RuntimeError("can't start thread")
        ),
        pytest.raises(RuntimeError, match="can't start thread"),
    ):
        _run(client.speak_text("hello"))
    assert mic.is_paused is False, "a failed start left the microphone dead"
    assert client.is_speaking is False


def test_tts_that_fails_before_speaking_resumes_mic_and_is_reported():
    """Resolving the TTS entry point used to happen outside the try/finally."""
    client = _client(enable_tts=True)
    mic = _Mic()
    client.whisper_asr = mic

    class _UnloadedTTS:
        @property
        def generate_speech_streaming(self):
            raise RuntimeError("kokoro pipeline not loaded")

    client.tts = _UnloadedTTS()

    with pytest.raises(RuntimeError, match="kokoro pipeline not loaded"):
        _run(client.speak_text("hello"))
    assert mic.is_paused is False, "a TTS that never started left the mic dead"
    assert client.is_speaking is False


def test_stale_enter_press_does_not_cut_off_the_next_reply():
    client = _client(enable_tts=True)
    client.whisper_asr = _Mic()
    seen = {}

    class _InterruptibleTTS:
        def generate_speech_streaming(
            self, text_queue, status_callback=None, interrupt_event=None
        ):
            seen["event"] = interrupt_event
            while text_queue.get(timeout=5) != "__END__":
                pass
            interrupt_event.wait(timeout=5)

    client.tts = _InterruptibleTTS()
    client._playback_interrupt.set()  # Enter pressed while idle

    runner = _speak_in_background(client, "hi")
    try:
        time.sleep(0.3)
        assert runner.is_alive(), "a press from before the reply interrupted it"
    finally:
        client._playback_interrupt.set()  # Enter pressed during playback
        runner.join(timeout=5)
    assert not runner.is_alive()
    assert seen["event"] is client._playback_interrupt


def test_one_stdin_listener_per_session():
    client = _client(enable_tts=True)
    client.llm_client.is_generating.return_value = False
    presses = iter(["", EOFError()])

    def fake_input():
        item = next(presses)
        if isinstance(item, BaseException):
            raise item
        return item

    fake_stdin = MagicMock()
    fake_stdin.isatty.return_value = True
    with (
        patch("gaia.audio.audio_client.sys.stdin", fake_stdin),
        patch("builtins.input", side_effect=fake_input),
    ):
        assert client._start_stdin_listener() is True
        first = client._stdin_listener
        assert client._start_stdin_listener() is True
        assert client._stdin_listener is first
        assert client._playback_interrupt.wait(timeout=2)
        first.join(timeout=2)


def test_no_listener_without_a_terminal():
    client = _client(enable_tts=True)
    fake_stdin = MagicMock()
    fake_stdin.isatty.return_value = False
    with patch("gaia.audio.audio_client.sys.stdin", fake_stdin):
        assert client._start_stdin_listener() is False
    assert client._stdin_listener is None


def _fake_asr(recording=True, capture_failed=False):
    asr = MagicMock()
    asr.is_recording = recording
    asr.capture_failed = capture_failed
    asr.SILENCE_THRESHOLD = 0.003
    asr.MIN_AUDIO_LENGTH = 8000.0
    asr.RATE = 16000
    asr.device_index = 0
    asr.get_device_name.return_value = "Test Mic"
    return asr


def _prepared_client(asr):
    """AudioClient wired to a fake WhisperAsr module (no real audio stack)."""
    client = _client(enable_tts=False, silence_threshold=0.05)
    client._check_mic_levels = MagicMock()
    module = types.ModuleType("gaia.audio.whisper_asr")
    module.WhisperAsr = MagicMock(return_value=asr)
    return client, patch.dict(sys.modules, {"gaia.audio.whisper_asr": module})


def test_voice_loop_crash_propagates_instead_of_exiting_zero():
    asr = _fake_asr()
    client, fake_module = _prepared_client(asr)
    client.transcription_queue.put("hello")

    async def failing_processor(_text):
        raise ConnectionError("404 from /api/v1/completions")

    with fake_module, pytest.raises(RuntimeError, match="voice loop crashed"):
        _run(client.start_voice_chat(failing_processor))


def test_dead_microphone_propagates_with_device_hint():
    asr = _fake_asr(recording=False, capture_failed=True)
    client, fake_module = _prepared_client(asr)

    async def processor(_text):
        return None

    with fake_module, pytest.raises(RuntimeError, match="audio-device-index"):
        _run(client.start_voice_chat(processor))


def test_clean_stop_does_not_raise():
    asr = _fake_asr(recording=False, capture_failed=False)
    client, fake_module = _prepared_client(asr)

    async def processor(_text):
        return None

    with fake_module:
        _run(client.start_voice_chat(processor))
