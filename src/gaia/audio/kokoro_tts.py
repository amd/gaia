# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import queue
import threading
import time

import numpy as np
import psutil

try:
    import sounddevice as sd
except ImportError:
    sd = None

try:
    import soundfile as sf
except ImportError:
    sf = None

try:
    from kokoro import KPipeline
except ImportError:
    KPipeline = None

from gaia.logger import get_logger


class KokoroTTS:
    log = get_logger(__name__)

    def __init__(self):
        # Check for required dependencies
        missing = []
        if sd is None:
            missing.append("sounddevice")
        if sf is None:
            missing.append("soundfile")
        if KPipeline is None:
            missing.append("kokoro>=0.3.1")

        if missing:
            error_msg = (
                f"\n❌ Error: Missing required talk dependencies: {', '.join(missing)}\n\n"
                f"Please install the talk dependencies:\n"
                f"  pip install -e .[talk]\n\n"
                f"Or install packages directly:\n"
                f"  pip install {' '.join(missing)}\n"
            )
            raise ImportError(error_msg)

        self.log = self.__class__.log

        # Initialize Kokoro pipeline with American English
        self.pipeline = KPipeline(lang_code="a")  # 'a' for American English

        # Available voice configurations with metadata
        self.available_voices = {
            # American English Voices 🇸
            "af_alloy": {
                "name": "American Female - Alloy",
                "quality": "C",
                "duration": "MM",
            },
            "af_aoede": {
                "name": "American Female - Aoede",
                "quality": "C+",
                "duration": "H",
            },
            "af_bella": {
                "name": "American Female - Bella",
                "quality": "A-",
                "duration": "HH",
            },
            "af_jessica": {
                "name": "American Female - Jessica",
                "quality": "D",
                "duration": "MM",
            },
            "af_kore": {
                "name": "American Female - Kore",
                "quality": "C+",
                "duration": "H",
            },
            "af_nicole": {
                "name": "American Female - Nicole",
                "quality": "B-",
                "duration": "HH",
            },
            "af_nova": {
                "name": "American Female - Nova",
                "quality": "C",
                "duration": "MM",
            },
            "af_river": {
                "name": "American Female - River",
                "quality": "D",
                "duration": "MM",
            },
            "af_sarah": {
                "name": "American Female - Sarah",
                "quality": "C+",
                "duration": "H",
            },
            "af_sky": {
                "name": "American Female - Sky",
                "quality": "C-",
                "duration": "M",
            },
            "am_adam": {
                "name": "American Male - Adam",
                "quality": "F+",
                "duration": "H",
            },
            "am_echo": {
                "name": "American Male - Echo",
                "quality": "D",
                "duration": "MM",
            },
            "am_eric": {
                "name": "American Male - Eric",
                "quality": "D",
                "duration": "MM",
            },
            "am_fenrir": {
                "name": "American Male - Fenrir",
                "quality": "C+",
                "duration": "H",
            },
            "am_liam": {
                "name": "American Male - Liam",
                "quality": "D",
                "duration": "MM",
            },
            "am_michael": {
                "name": "American Male - Michael",
                "quality": "C+",
                "duration": "H",
            },
            "am_onyx": {
                "name": "American Male - Onyx",
                "quality": "D",
                "duration": "MM",
            },
            "am_puck": {
                "name": "American Male - Puck",
                "quality": "C+",
                "duration": "H",
            },
            # British English Voices 🇧
            "bf_alice": {
                "name": "British Female - Alice",
                "quality": "D",
                "duration": "MM",
            },
            "bf_emma": {
                "name": "British Female - Emma",
                "quality": "B-",
                "duration": "HH",
            },
            "bf_isabella": {
                "name": "British Female - Isabella",
                "quality": "C",
                "duration": "MM",
            },
            "bf_lily": {
                "name": "British Female - Lily",
                "quality": "D",
                "duration": "MM",
            },
            "bm_daniel": {
                "name": "British Male - Daniel",
                "quality": "D",
                "duration": "MM",
            },
            "bm_fable": {
                "name": "British Male - Fable",
                "quality": "C",
                "duration": "MM",
            },
            "bm_george": {
                "name": "British Male - George",
                "quality": "C",
                "duration": "MM",
            },
            "bm_lewis": {
                "name": "British Male - Lewis",
                "quality": "D+",
                "duration": "H",
            },
        }

        # Default to highest quality voice (Bella)
        self.voice_name = "af_bella"
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
                line = line.replace(")", "...")  # Add pause after list items
                processed_lines.append(f"{line}...")
            else:
                # Add a period at the end of non-empty lines if they don't already have ending punctuation
                if not line[-1] in ".!?:":
                    processed_lines.append(line + ".")
                else:
                    processed_lines.append(line)

        return " ".join(processed_lines)  # Join with spaces instead of newlines

    def generate_speech(
        self, text: str, stream_callback=None
    ) -> tuple[list[float], str, dict]:
        """Generate speech from text using Kokoro TTS with quality optimizations."""
        self.log.debug(f"Generating speech for text of length {len(text)}")

        process = psutil.Process()
        start_memory = process.memory_info().rss / 1024 / 1024
        start_time = time.time()

        # Generate audio using the pipeline with chunking for optimal quality
        audio_chunks = []
        phonemes = []
        total_duration = 0

        # Split text into chunks of optimal size (100-200 tokens)
        sentences = text.split(".")
        current_chunk = []
        current_length = 0

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            sentence_length = len(sentence.split())

            if current_length + sentence_length > self.chunk_size:
                # Process current chunk
                chunk_text = ". ".join(current_chunk) + "."
                generator = self.pipeline(chunk_text, voice=self.voice_name, speed=1)
                for _, phoneme_seq, audio in generator:
                    audio_chunks.append(audio)
                    phonemes.append(phoneme_seq)
                    chunk_duration = len(audio) / 24000
                    total_duration += chunk_duration

                    if stream_callback and callable(stream_callback):
                        stream_callback(audio)

                current_chunk = [sentence]
                current_length = sentence_length
            else:
                current_chunk.append(sentence)
                current_length += sentence_length

        # Process remaining chunk if any
        if current_chunk:
            chunk_text = ". ".join(current_chunk) + "."
            generator = self.pipeline(chunk_text, voice=self.voice_name, speed=1)
            for _, phoneme_seq, audio in generator:
                audio_chunks.append(audio)
                phonemes.append(phoneme_seq)
                chunk_duration = len(audio) / 24000
                total_duration += chunk_duration

                if stream_callback and callable(stream_callback):
                    stream_callback(audio)

        # Combine all audio chunks
        audio = np.concatenate(audio_chunks)
        combined_phonemes = " ".join(phonemes)

        end_time = time.time()
        end_memory = process.memory_info().rss / 1024 / 1024
        processing_time = end_time - start_time
        peak_memory = end_memory - start_memory

        stats = {
            "processing_time": round(processing_time, 3),
            "audio_duration": round(total_duration, 3),
            "realtime_ratio": round(processing_time / total_duration, 2),
            "peak_memory": round(peak_memory, 2),
        }

        return audio, combined_phonemes, stats

    def generate_speech_streaming(
        self, text_queue: queue.Queue, status_callback=None, interrupt_event=None
    ) -> None:
        """Optimized streaming TTS with separate processing and playback threads."""
        self.log.debug("Starting speech streaming")
        buffer = ""
        audio_buffer = queue.Queue(maxsize=100)  # Buffer for processed audio chunks

        # Initialize audio stream
        stream = sd.OutputStream(
            samplerate=24000,
            channels=1,
            dtype=np.float32,
            blocksize=2400,  # 100ms buffer
            latency="low",
        )
        stream.start()
        self.log.debug("Audio stream initialized")

        # Playback thread function
        def audio_playback_thread():
            try:
                while True:
                    try:
                        audio_chunk = audio_buffer.get(timeout=0.1)
                        if audio_chunk is None:  # Exit signal
                            if status_callback:
                                status_callback(False)
                            break
                        if interrupt_event and interrupt_event.is_set():
                            break
                        if status_callback:
                            status_callback(True)
                        stream.write(np.array(audio_chunk, dtype=np.float32))
                    except queue.Empty:
                        continue
            except Exception as e:
                self.log.error(f"Error in playback thread: {e}")
                if status_callback:
                    status_callback(False)
            finally:
                stream.stop()
                stream.close()
                if status_callback:
                    status_callback(False)

        # Start playback thread
        playback_thread = threading.Thread(target=audio_playback_thread)
        playback_thread.daemon = True
        playback_thread.start()

        try:
            while True:
                try:
                    chunk = text_queue.get(timeout=0.1)

                    if chunk == "__END__" or (
                        interrupt_event and interrupt_event.is_set()
                    ):
                        if buffer.strip():
                            # Process final buffer
                            processed_text = self.preprocess_text(buffer.strip())
                            if processed_text:  # Only process if there's actual text
                                self.generate_speech(
                                    processed_text, stream_callback=audio_buffer.put
                                )
                        audio_buffer.put(None)  # Signal playback thread to exit
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
                                    processed_text, stream_callback=audio_buffer.put
                                )
                        buffer = sentences[-1]

                except queue.Empty:
                    continue

        except Exception as e:
            self.log.error(f"Error in streaming: {e}")
            audio_buffer.put(None)  # Ensure playback thread exits
        finally:
            audio_buffer.put(None)  # Ensure playback thread exits
            playback_thread.join(timeout=2.0)

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
            audio, _, stats = self.generate_speech(test_text)

            # Save audio to file
            sf.write(output_file, np.array(audio), 24000)
            print(f"Saved audio to: {output_file}")

            print("\nPerformance stats:")
            print(f"- Processing time: {stats['processing_time']:.3f}s")
            print(f"- Audio duration: {stats['audio_duration']:.3f}s")
            print(f"- Realtime ratio: {stats['realtime_ratio']:.2f}x (lower is better)")
            print(f"- Peak memory usage: {stats['peak_memory']:.2f} MB")
        except Exception as e:
            self.log.error(f"Error during audio generation test: {e}")

    def test_streaming_playback(self, test_text: str) -> None:
        """Test streaming audio generation with progress display."""
        try:
            # Setup audio stream
            stream = sd.OutputStream(samplerate=24000, channels=1, dtype=np.float32)
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
            _, _, stats = self.generate_speech(
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
