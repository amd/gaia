# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Kokoro voice output for ``gaia talk``, synthesized by Lemonade Server.

Speech comes from Lemonade's ``kokoro-v1`` model; this module owns sentence
chunking and live playback through the speakers.
"""

import queue
import threading
import time

import numpy as np

try:
    import sounddevice as sd
except ImportError:
    sd = None

from gaia.audio.lemonade_tts import (
    DEFAULT_VOICE,
    KOKORO_VOICES,
    TTS_SAMPLE_RATE,
    LemonadeTTSClient,
    LemonadeTTSError,
    write_wav,
)
from gaia.logger import get_logger


class KokoroTTS:
    log = get_logger(__name__)
    PLAYBACK_TIMEOUT_SLACK = 5.0

    def __init__(self, base_url=None, client=None, say=None):
        """
        Args:
            base_url: Lemonade API root; defaults to ``LEMONADE_BASE_URL``.
            client: A ready :class:`LemonadeTTSClient` (tests inject one).
            say: Called with a line before the voice model is downloaded.

        Raises:
            ConnectionError: Lemonade Server is not reachable.
            LemonadeTTSError: Lemonade cannot serve the Kokoro model.
        """
        self.log = self.__class__.log
        self.client = client or LemonadeTTSClient(base_url=base_url)
        self.client.ensure_model(say=say)

        # Available voice configurations with metadata
        self.available_voices = KOKORO_VOICES

        self.voice_name = DEFAULT_VOICE
        self.chunk_size = 150  # Optimal token chunk size for best quality
        self.log.debug(
            f"Loaded voice: {self.voice_name} - {self.available_voices[self.voice_name]['name']} (Quality: {self.available_voices[self.voice_name]['quality']})"
        )

    def preprocess_text(self, text: str) -> str:
        """
        Preprocess text to add appropriate pauses and improve speech flow.
        Removes asterisks and adds pause markers.
        """
        # First remove all asterisks from the text
        text = text.replace("*", "")

        # Add pauses after bullet points and numbered lists
        lines = text.split("\n")
        processed_lines = []

        for line in lines:
            line = line.strip()
            if not line:  # Skip empty lines
                continue

            # Check for various list formats and add pauses
            if (
                line.startswith(("•", "-", "*"))  # Bullet points
                or (
                    len(line) > 2 and line[0].isdigit() and line[1] == "."
                )  # Numbered lists
                or (len(line) > 2 and line[0].isalpha() and line[1] in [")", "."])
            ):  # Lettered lists
                # For list items, ensure we add pause regardless of existing punctuation
                if line[-1] in ".!?:":
                    line = line[:-1]  # Remove existing punctuation
                # Replace only the list marker paren, not all parens in the line
                if len(line) > 1 and line[1] == ")":
                    line = line[0] + "..." + line[2:]
                processed_lines.append(f"{line}...")
            else:
                # Add a period at the end of non-empty lines if they don't already have ending punctuation
                if not line[-1] in ".!?:":
                    processed_lines.append(line + ".")
                else:
                    processed_lines.append(line)

        return " ".join(processed_lines)  # Join with spaces instead of newlines

    def generate_speech(self, text: str, stream_callback=None) -> tuple:
        """Speak *text* through Lemonade, chunked by sentence for early playback.

        Returns:
            ``(audio, stats)`` — float32 samples at ``TTS_SAMPLE_RATE`` and
            timing stats.
        """
        self.log.debug(f"Generating speech for text of length {len(text)}")
        start_time = time.time()
        audio_chunks = []

        def speak(chunk_text):
            audio, rate = self.client.synthesize(chunk_text, voice=self.voice_name)
            if rate != TTS_SAMPLE_RATE:
                raise LemonadeTTSError(
                    f"Lemonade returned {rate} Hz speech; playback is opened at "
                    f"{TTS_SAMPLE_RATE} Hz. Check the Lemonade Server version."
                )
            audio_chunks.append(audio)
            if stream_callback and callable(stream_callback):
                stream_callback(audio)

        # Sentence-sized requests keep the first audio close to the first text.
        current_chunk = []
        current_length = 0
        for sentence in text.split("."):
            sentence = sentence.strip()
            if not sentence:
                continue
            sentence_length = len(sentence.split())
            if current_chunk and current_length + sentence_length > self.chunk_size:
                speak(". ".join(current_chunk) + ".")
                current_chunk = []
                current_length = 0
            current_chunk.append(sentence)
            current_length += sentence_length
        if current_chunk:
            speak(". ".join(current_chunk) + ".")

        if audio_chunks:
            audio = np.concatenate(audio_chunks)
        else:
            audio = np.zeros(TTS_SAMPLE_RATE // 10, dtype=np.float32)
        processing_time = time.time() - start_time
        total_duration = len(audio) / TTS_SAMPLE_RATE if audio_chunks else 0.0
        stats = {
            "processing_time": round(processing_time, 3),
            "audio_duration": round(total_duration, 3),
            "realtime_ratio": (
                round(processing_time / total_duration, 2)
                if total_duration > 0
                else 0.0
            ),
        }
        return audio, stats

    @staticmethod
    def _drain_text_queue(text_queue: queue.Queue) -> None:
        """Consume until the producer's terminator, so it never blocks.

        The producer puts onto a bounded queue; with no consumer it wedges at
        100 chunks and the LLM stream stops mid-answer.
        """
        while True:
            try:
                item = text_queue.get(timeout=30.0)
            except queue.Empty:
                return
            if item in ("__END__", "__HALT__", None):
                return

    @staticmethod
    def _require_sounddevice() -> None:
        if sd is None:
            raise ImportError(
                "sounddevice is required to play speech.\n"
                'Install it with: uv pip install "amd-gaia[talk]"'
            )

    def generate_speech_streaming(
        self, text_queue: queue.Queue, status_callback=None, interrupt_event=None
    ) -> None:
        """Optimized streaming TTS with separate processing and playback threads."""
        self._require_sounddevice()
        self.log.debug("Starting speech streaming")
        buffer = ""
        audio_buffer = queue.Queue(maxsize=100)  # Buffer for processed audio chunks
        audio_duration = 0.0
        playback_errors = []
        stop_playback = threading.Event()

        def enqueue_audio(audio):
            nonlocal audio_duration
            audio_duration += len(audio) / TTS_SAMPLE_RATE
            try:
                audio_buffer.put(
                    audio, timeout=audio_duration + self.PLAYBACK_TIMEOUT_SLACK
                )
            except queue.Full as error:
                raise TimeoutError(
                    "Audio output stalled while buffering playback"
                ) from error

        # Initialize audio stream. Inside a try: this used to raise straight out
        # of the TTS thread, and the producer then blocked forever on a full
        # text_queue — a broken speaker froze the whole answer (#3554).
        try:
            stream = sd.OutputStream(
                samplerate=TTS_SAMPLE_RATE,
                channels=1,
                dtype=np.float32,
                blocksize=TTS_SAMPLE_RATE // 10,  # 100ms buffer
                latency="low",
            )
            stream.start()
        except Exception as e:
            message = (
                f"Speaker unavailable: could not open audio output ({e}). "
                "The reply is shown as text; voice output is off for this "
                "session."
            )
            self.log.error(message)
            print(f"\n{message}")
            # Keep draining so the producer can finish its stream. Returning
            # here would leave it blocked on a queue nobody reads.
            self._drain_text_queue(text_queue)
            if status_callback:
                status_callback(False)
            return
        self.log.debug("Audio stream initialized")

        # Playback thread function
        def audio_playback_thread():
            playing = True
            try:
                while not stop_playback.is_set():
                    try:
                        audio_chunk = audio_buffer.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    if audio_chunk is None:  # Exit signal
                        break
                    # Keep draining after an interrupt or device error: the
                    # synthesis side blocks on a full buffer otherwise.
                    if not playing or (interrupt_event and interrupt_event.is_set()):
                        continue
                    if status_callback:
                        status_callback(True)
                    try:
                        stream.write(np.array(audio_chunk, dtype=np.float32))
                    except Exception as e:
                        playback_errors.append(e)
                        playing = False
            except Exception as e:
                playback_errors.append(e)
            finally:
                try:
                    stream.stop()
                except Exception as e:
                    playback_errors.append(e)
                finally:
                    try:
                        stream.close()
                        if status_callback:
                            status_callback(False)
                    except Exception as e:
                        playback_errors.append(e)

        # Start playback thread
        playback_thread = threading.Thread(target=audio_playback_thread)
        playback_thread.daemon = True
        playback_thread.start()

        try:
            while True:
                try:
                    if interrupt_event and interrupt_event.is_set():
                        break
                    chunk = text_queue.get(timeout=0.1)

                    if chunk == "__END__" or (
                        interrupt_event and interrupt_event.is_set()
                    ):
                        if buffer.strip():
                            # Process final buffer
                            processed_text = self.preprocess_text(buffer.strip())
                            if processed_text:  # Only process if there's actual text
                                self.generate_speech(
                                    processed_text, stream_callback=enqueue_audio
                                )
                        break

                    buffer += chunk

                    # Find complete sentences for immediate processing
                    sentences = buffer.split(".")
                    if len(sentences) > 1:
                        # Process complete sentences immediately
                        text_to_process = ".".join(sentences[:-1]) + "."
                        if (
                            text_to_process.strip()
                        ):  # Only process if there's actual text
                            processed_text = self.preprocess_text(text_to_process)
                            if processed_text:  # Double check after preprocessing
                                self.generate_speech(
                                    processed_text, stream_callback=enqueue_audio
                                )
                        buffer = sentences[-1]

                except queue.Empty:
                    continue

        finally:
            deadline = time.monotonic() + audio_duration + self.PLAYBACK_TIMEOUT_SLACK
            try:
                audio_buffer.put(None, timeout=max(0, deadline - time.monotonic()))
            except queue.Full as error:
                stop_playback.set()
                raise TimeoutError(
                    "Audio output stalled while finishing playback"
                ) from error
            playback_thread.join(timeout=max(0, deadline - time.monotonic()))
            if playback_thread.is_alive():
                stop_playback.set()
                raise TimeoutError(
                    "Audio playback exceeded its duration plus cleanup allowance; "
                    "microphone remains paused. Restart voice chat after checking "
                    "the output device."
                )
            if playback_errors:
                raise RuntimeError(
                    f"Audio playback failed: {playback_errors[0]}"
                ) from playback_errors[0]

    def set_voice(self, voice_name: str) -> None:
        """Change the current voice."""
        self.log.info(f"Changing voice to: {voice_name}")
        if voice_name not in self.available_voices:
            self.log.error(f"Unknown voice '{voice_name}'")
            raise ValueError(
                f"Unknown voice '{voice_name}'. Available voices: {list(self.available_voices.keys())}"
            )

        self.voice_name = voice_name
        self.log.info(
            f"Changed voice to: {voice_name} - {self.available_voices[voice_name]['name']} (Quality: {self.available_voices[voice_name]['quality']})"
        )

    def list_available_voices(self) -> dict[str, dict]:
        """Get all available voice names and their descriptions."""
        return self.available_voices

    # Test methods remain largely unchanged, just updated to use new generate_speech method
    def test_preprocessing(self, test_text: str) -> str:
        """Test the text preprocessing functionality."""
        try:
            processed_text = self.preprocess_text(test_text)
            print("\nOriginal text:")
            print(test_text)
            print("\nProcessed text:")
            print(processed_text)
            return processed_text
        except Exception as e:
            self.log.error(f"Error during preprocessing test: {e}")
            return None

    def test_generate_audio_file(
        self, test_text: str, output_file: str = "output.wav"
    ) -> None:
        """Test basic audio generation and file saving."""
        try:
            print("\nGenerating audio...")
            audio, stats = self.generate_speech(test_text)

            write_wav(output_file, audio, TTS_SAMPLE_RATE)
            print(f"Saved audio to: {output_file}")

            print("\nPerformance stats:")
            print(f"- Processing time: {stats['processing_time']:.3f}s")
            print(f"- Audio duration: {stats['audio_duration']:.3f}s")
            print(f"- Realtime ratio: {stats['realtime_ratio']:.2f}x (lower is better)")
        except Exception as e:
            self.log.error(f"Error during audio generation test: {e}")

    def test_streaming_playback(self, test_text: str) -> None:
        """Test streaming audio generation with progress display."""
        self._require_sounddevice()
        try:
            # Setup audio stream
            stream = sd.OutputStream(
                samplerate=TTS_SAMPLE_RATE, channels=1, dtype=np.float32
            )
            stream.start()

            # Create audio queue and initialize tracking variables
            audio_queue = queue.Queue(maxsize=100)
            words = test_text.split()
            total_words = len(words)
            total_chunks = 0
            current_processing_chunk = 0
            current_playback_chunk = 0
            spinner_chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
            spinner_idx = 0

            # Count total chunks
            def count_chunks(_):
                nonlocal total_chunks
                total_chunks += 1

            print("\nAnalyzing text length...")
            self.generate_speech(test_text, stream_callback=count_chunks)

            if total_chunks == 0:
                print("No audio chunks generated from input text.")
                stream.stop()
                stream.close()
                return

            # Define and start streaming thread
            def stream_audio():
                nonlocal current_playback_chunk, spinner_idx
                while True:
                    try:
                        chunk = audio_queue.get()
                        if chunk is None:
                            break

                        chunk_array = np.array(chunk, dtype=np.float32)
                        stream.write(chunk_array)
                        current_playback_chunk += 1

                        # Update progress display
                        word_position = int(
                            (current_playback_chunk / total_chunks) * total_words
                        )
                        current_text = " ".join(
                            words[
                                max(0, word_position - 5) : min(
                                    total_words, word_position + 5
                                )
                            ]
                        )
                        current_text = current_text[:60].ljust(60)

                        process_progress = int(
                            (current_processing_chunk / total_chunks) * 50
                        )
                        playback_progress = int(
                            (current_playback_chunk / total_chunks) * 50
                        )
                        spinner_idx = (spinner_idx + 1) % len(spinner_chars)

                        print("\033[K", end="")
                        print(
                            f"\r{spinner_chars[spinner_idx]} Processing: [{'=' * process_progress}{' ' * (50-process_progress)}] {(current_processing_chunk/total_chunks)*100:.1f}%"
                        )
                        print(
                            f"{spinner_chars[spinner_idx]} Playback:  [{'=' * playback_progress}{' ' * (50-playback_progress)}] {(current_playback_chunk/total_chunks)*100:.1f}%"
                        )
                        print(
                            f"{spinner_chars[spinner_idx]} Current: {current_text}",
                            end="\033[2A\r",
                        )

                        audio_queue.task_done()
                    except queue.Empty:
                        continue

            print("\nGenerating and streaming audio...")
            print("\n\n")
            stream_thread = threading.Thread(target=stream_audio)
            stream_thread.start()

            def process_chunk(chunk):
                nonlocal current_processing_chunk
                current_processing_chunk += 1
                audio_queue.put(chunk)

            processed_text = self.preprocess_text(test_text)
            _, stats = self.generate_speech(
                processed_text, stream_callback=process_chunk
            )

            audio_queue.put(None)
            stream_thread.join()

            print("\n\n\n")
            stream.stop()
            stream.close()

            print("\nStreaming test completed")
            print(f"Realtime ratio: {stats['realtime_ratio']:.2f}x (lower is better)")

        except Exception as e:
            self.log.error(f"Error during streaming test: {e}")


def main():
    """Run all TTS tests."""
    test_text = """
Let's play a game of trivia. I'll ask you a series of questions on a particular topic, and you try to answer them to the best of your ability. We can keep track of your score and see how well you do.

Here's your first question:

**Question 1:** Which American author wrote the classic novel "To Kill a Mockingbird"?

A) F. Scott Fitzgerald
B) Harper Lee
C) Jane Austen
D) J. K. Rowling
E) Edgar Allan Poe

Let me know your answer!
"""

    tts = KokoroTTS()

    print("Running preprocessing test...")
    processed_text = tts.test_preprocessing(test_text)

    print("\nRunning streaming test...")
    tts.test_streaming_playback(processed_text)

    print("\nRunning audio generation test...")
    tts.test_generate_audio_file(processed_text)


if __name__ == "__main__":
    main()
