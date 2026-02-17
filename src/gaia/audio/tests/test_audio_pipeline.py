#!/usr/bin/env python3
#
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Audio Pipeline Diagnostic Tool
Tests each component of the audio recording and transcription pipeline
"""

import sys
import time

import numpy as np
import sounddevice as sd


def test_microphone_basics():
    """Test 1: Basic microphone functionality"""
    print("\n=== TEST 1: Basic Microphone Test ===")

    # List all audio devices
    print("\nAvailable audio input devices:")
    devices = sd.query_devices()
    input_devices = []
    for i, info in enumerate(devices):
        if info.get("max_input_channels", 0) > 0:
            print(
                f"  [{i}] {info.get('name')} - {info.get('max_input_channels')} channels"
            )
            input_devices.append(i)

    # Get default device
    try:
        default_idx = sd.default.device[0]
        default_info = sd.query_devices(default_idx)
        print(f"\nDefault input device: [{default_idx}] {default_info['name']}")
    except Exception:
        print("\nNo default input device found!")
        default_idx = input_devices[0] if input_devices else None

    if not input_devices:
        print("\n\u274c No input devices found! Check your microphone connection.")
        return None

    # Test recording from default device
    print(f"\nTesting recording from device [{default_idx}]...")
    print("Speak now for 3 seconds...")

    CHUNK = 2048
    CHANNELS = 1
    RATE = 16000

    try:
        stream = sd.InputStream(
            samplerate=RATE,
            channels=CHANNELS,
            dtype="float32",
            device=default_idx,
            blocksize=CHUNK,
        )
        stream.start()

        audio_data = []
        start_time = time.time()

        while time.time() - start_time < 3:
            frames, _ = stream.read(CHUNK)
            audio_data.append(frames[:, 0])

        stream.stop()
        stream.close()

        # Convert to numpy array and analyze
        audio_array = np.concatenate(audio_data)

        print(f"\n\u2705 Recorded {len(audio_array)/RATE:.2f} seconds of audio")
        print(f"   Audio shape: {audio_array.shape}")
        print(f"   Min value: {audio_array.min():.4f}")
        print(f"   Max value: {audio_array.max():.4f}")
        print(f"   Mean absolute value: {np.abs(audio_array).mean():.6f}")

        # Check if we got any sound
        if np.abs(audio_array).mean() < 0.0001:
            print("\n\u26a0\ufe0f  WARNING: Audio levels are very low! Check:")
            print("   - Microphone is not muted")
            print("   - Microphone permissions are granted")
            print("   - Correct microphone is selected")
        else:
            print("\n\u2705 Audio levels look good!")

    except Exception as e:
        print(f"\n\u274c Error recording audio: {e}")
        default_idx = None

    return default_idx


def test_audio_recorder():
    """Test 2: AudioRecorder class"""
    print("\n=== TEST 2: AudioRecorder Class Test ===")

    try:
        from gaia.audio.audio_recorder import AudioRecorder

        print("Creating AudioRecorder...")
        recorder = AudioRecorder(device_index=None)  # Use default

        print(f"Device index: {recorder.device_index}")
        print(f"Device name: {recorder.get_device_name()}")
        print(f"Sample rate: {recorder.RATE} Hz")
        print(f"Chunk size: {recorder.CHUNK}")
        print(f"Silence threshold: {recorder.SILENCE_THRESHOLD}")

        print("\nStarting recording for 5 seconds...")
        print("Speak something...")

        # Monitor the audio queue
        recorder.start_recording()

        audio_chunks = []
        start_time = time.time()

        while time.time() - start_time < 5:
            if not recorder.audio_queue.empty():
                chunk = recorder.audio_queue.get()
                audio_chunks.append(chunk)
                print(
                    f"  Got audio chunk: {len(chunk)} samples, mean: {np.abs(chunk).mean():.6f}"
                )
            time.sleep(0.1)

        recorder.stop_recording()

        if audio_chunks:
            print(f"\n\u2705 Captured {len(audio_chunks)} audio chunks")
        else:
            print(
                "\n\u26a0\ufe0f  No audio chunks captured! The voice activity detection might be too strict."
            )
            print("   Try speaking louder or continuously.")

    except Exception as e:
        print(f"\n\u274c Error testing AudioRecorder: {e}")
        import traceback

        traceback.print_exc()


def test_whisper_asr():
    """Test 3: WhisperAsr class"""
    print("\n=== TEST 3: WhisperAsr Class Test ===")

    try:
        import queue

        from gaia.audio.whisper_asr import WhisperAsr

        print("Loading Whisper model (this may take a moment)...")
        transcription_queue = queue.Queue()

        asr = WhisperAsr(
            model_size="tiny",  # Use tiny for faster testing
            device_index=None,
            transcription_queue=transcription_queue,
            enable_cuda=False,
        )

        print(f"\u2705 Model loaded: {asr.model}")
        print(f"Device: {asr.get_device_name()}")

        print("\nStarting transcription for 10 seconds...")
        print("Speak clearly in complete sentences...")

        asr.start_recording()

        transcriptions = []
        start_time = time.time()

        while time.time() - start_time < 10:
            # Check audio queue size
            audio_size = asr.audio_queue.qsize()
            if audio_size > 0:
                print(f"  Audio queue size: {audio_size}")

            # Check for transcriptions
            while not transcription_queue.empty():
                text = transcription_queue.get()
                transcriptions.append(text)
                print(f"  Transcribed: {text}")

            time.sleep(0.5)

        asr.stop_recording()

        # Get any remaining transcriptions
        time.sleep(1)
        while not transcription_queue.empty():
            text = transcription_queue.get()
            transcriptions.append(text)
            print(f"  Transcribed: {text}")

        if transcriptions:
            print(f"\n\u2705 Got {len(transcriptions)} transcriptions")
            print(f"Full text: {' '.join(transcriptions)}")
        else:
            print("\n\u26a0\ufe0f  No transcriptions received!")
            print("Possible issues:")
            print("  - Microphone not working")
            print("  - Speech not detected (try speaking louder)")
            print("  - Whisper model issues")

    except Exception as e:
        print(f"\n\u274c Error testing WhisperAsr: {e}")
        import traceback

        traceback.print_exc()


def test_raw_recording():
    """Test 4: Raw continuous recording without VAD"""
    print("\n=== TEST 4: Raw Recording Test (No VAD) ===")

    try:
        # Use default device
        device_idx = sd.default.device[0]
        device_info = sd.query_devices(device_idx)

        CHUNK = 2048
        CHANNELS = 1
        RATE = 16000

        print(f"Recording from: {device_info['name']}")
        print("Recording for 5 seconds (no voice detection)...")
        print("Make some noise!\n")

        stream = sd.InputStream(
            samplerate=RATE,
            channels=CHANNELS,
            dtype="float32",
            device=device_idx,
            blocksize=CHUNK,
        )
        stream.start()

        chunks_with_sound = 0
        total_chunks = 0
        max_level = 0

        start_time = time.time()
        while time.time() - start_time < 5:
            frames, _ = stream.read(CHUNK)
            audio = frames[:, 0]

            level = np.abs(audio).mean()
            max_level = max(max_level, level)
            total_chunks += 1

            if level > 0.001:  # Very low threshold
                chunks_with_sound += 1
                print(f"  Level: {'\u2588' * int(level * 500):{20}} {level:.6f}")
            else:
                print(f"  Level: {'':{20}} {level:.6f} (silence)")

            time.sleep(0.05)

        stream.stop()
        stream.close()

        print(f"\nRecording Statistics:")
        print(f"   Total chunks: {total_chunks}")
        print(f"   Chunks with sound: {chunks_with_sound}")
        print(f"   Max level: {max_level:.6f}")

        if chunks_with_sound == 0:
            print("\n\u274c No sound detected at all!")
            print("   - Check microphone is connected")
            print("   - Check microphone permissions")
            print("   - Check microphone is not muted")
        elif chunks_with_sound < total_chunks * 0.1:
            print("\n\u26a0\ufe0f  Very little sound detected")
        else:
            print("\n\u2705 Sound detection working!")

    except Exception as e:
        print(f"\n\u274c Error in raw recording: {e}")


if __name__ == "__main__":
    print("=" * 60)
    print("AUDIO PIPELINE DIAGNOSTIC TOOL")
    print("=" * 60)

    # Test 1: Basic microphone
    device_idx = test_microphone_basics()

    if device_idx is None:
        print("\n\u274c Microphone test failed. Fix microphone issues before continuing.")
        sys.exit(1)

    input("\nPress Enter to continue to AudioRecorder test...")

    # Test 2: AudioRecorder
    test_audio_recorder()

    input("\nPress Enter to continue to raw recording test...")

    # Test 4: Raw recording (do this before Whisper to avoid model loading)
    test_raw_recording()

    input("\nPress Enter to continue to WhisperAsr test...")

    # Test 3: WhisperAsr
    test_whisper_asr()

    print("\n" + "=" * 60)
    print("DIAGNOSTIC COMPLETE")
    print("=" * 60)
