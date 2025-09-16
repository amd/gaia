#!/usr/bin/env python3
"""
Audio Pipeline Diagnostic Tool
Tests each component of the audio recording and transcription pipeline
"""

import numpy as np
import pyaudio
import time
import sys
import queue
import threading


def test_microphone_basics():
    """Test 1: Basic microphone functionality"""
    print("\n=== TEST 1: Basic Microphone Test ===")

    p = pyaudio.PyAudio()

    # List all audio devices
    print("\nAvailable audio input devices:")
    input_devices = []
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info.get("maxInputChannels") > 0:
            print(
                f"  [{i}] {info.get('name')} - {info.get('maxInputChannels')} channels"
            )
            input_devices.append(i)

    # Get default device
    try:
        default_device = p.get_default_input_device_info()
        default_idx = default_device["index"]
        print(f"\nDefault input device: [{default_idx}] {default_device['name']}")
    except:
        print("\nNo default input device found!")
        default_idx = 0 if input_devices else None

    if not input_devices:
        print("\n❌ No input devices found! Check your microphone connection.")
        p.terminate()
        return None

    # Test recording from default device
    print(f"\nTesting recording from device [{default_idx}]...")
    print("Speak now for 3 seconds...")

    CHUNK = 2048
    FORMAT = pyaudio.paFloat32
    CHANNELS = 1
    RATE = 16000

    try:
        stream = p.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=RATE,
            input=True,
            input_device_index=default_idx,
            frames_per_buffer=CHUNK,
        )

        audio_data = []
        start_time = time.time()

        while time.time() - start_time < 3:
            data = stream.read(CHUNK, exception_on_overflow=False)
            audio_data.append(data)

        stream.stop_stream()
        stream.close()

        # Convert to numpy array and analyze
        audio_array = np.frombuffer(b"".join(audio_data), dtype=np.float32)

        print(f"\n✅ Recorded {len(audio_array)/RATE:.2f} seconds of audio")
        print(f"   Audio shape: {audio_array.shape}")
        print(f"   Min value: {audio_array.min():.4f}")
        print(f"   Max value: {audio_array.max():.4f}")
        print(f"   Mean absolute value: {np.abs(audio_array).mean():.6f}")

        # Check if we got any sound
        if np.abs(audio_array).mean() < 0.0001:
            print("\n⚠️  WARNING: Audio levels are very low! Check:")
            print("   - Microphone is not muted")
            print("   - Microphone permissions are granted")
            print("   - Correct microphone is selected")
        else:
            print("\n✅ Audio levels look good!")

    except Exception as e:
        print(f"\n❌ Error recording audio: {e}")
        default_idx = None
    finally:
        p.terminate()

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
            print(f"\n✅ Captured {len(audio_chunks)} audio chunks")
        else:
            print(
                "\n⚠️  No audio chunks captured! The voice activity detection might be too strict."
            )
            print("   Try speaking louder or continuously.")

    except Exception as e:
        print(f"\n❌ Error testing AudioRecorder: {e}")
        import traceback

        traceback.print_exc()


def test_whisper_asr():
    """Test 3: WhisperAsr class"""
    print("\n=== TEST 3: WhisperAsr Class Test ===")

    try:
        from gaia.audio.whisper_asr import WhisperAsr
        import queue

        print("Loading Whisper model (this may take a moment)...")
        transcription_queue = queue.Queue()

        asr = WhisperAsr(
            model_size="tiny",  # Use tiny for faster testing
            device_index=None,
            transcription_queue=transcription_queue,
            enable_cuda=False,
        )

        print(f"✅ Model loaded: {asr.model}")
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
                print(f"  📝 Transcribed: {text}")

            time.sleep(0.5)

        asr.stop_recording()

        # Get any remaining transcriptions
        time.sleep(1)
        while not transcription_queue.empty():
            text = transcription_queue.get()
            transcriptions.append(text)
            print(f"  📝 Transcribed: {text}")

        if transcriptions:
            print(f"\n✅ Got {len(transcriptions)} transcriptions")
            print(f"Full text: {' '.join(transcriptions)}")
        else:
            print("\n⚠️  No transcriptions received!")
            print("Possible issues:")
            print("  - Microphone not working")
            print("  - Speech not detected (try speaking louder)")
            print("  - Whisper model issues")

    except Exception as e:
        print(f"\n❌ Error testing WhisperAsr: {e}")
        import traceback

        traceback.print_exc()


def test_raw_recording():
    """Test 4: Raw continuous recording without VAD"""
    print("\n=== TEST 4: Raw Recording Test (No VAD) ===")

    p = pyaudio.PyAudio()

    try:
        # Use default device
        default_device = p.get_default_input_device_info()
        device_idx = default_device["index"]

        CHUNK = 2048
        FORMAT = pyaudio.paFloat32
        CHANNELS = 1
        RATE = 16000

        print(f"Recording from: {default_device['name']}")
        print("Recording for 5 seconds (no voice detection)...")
        print("Make some noise!\n")

        stream = p.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=RATE,
            input=True,
            input_device_index=device_idx,
            frames_per_buffer=CHUNK,
        )

        chunks_with_sound = 0
        total_chunks = 0
        max_level = 0

        start_time = time.time()
        while time.time() - start_time < 5:
            data = stream.read(CHUNK, exception_on_overflow=False)
            audio = np.frombuffer(data, dtype=np.float32)

            level = np.abs(audio).mean()
            max_level = max(max_level, level)
            total_chunks += 1

            if level > 0.001:  # Very low threshold
                chunks_with_sound += 1
                print(f"  Level: {'█' * int(level * 500):{20}} {level:.6f}")
            else:
                print(f"  Level: {'':{20}} {level:.6f} (silence)")

            time.sleep(0.05)

        stream.stop_stream()
        stream.close()

        print(f"\n📊 Recording Statistics:")
        print(f"   Total chunks: {total_chunks}")
        print(f"   Chunks with sound: {chunks_with_sound}")
        print(f"   Max level: {max_level:.6f}")

        if chunks_with_sound == 0:
            print("\n❌ No sound detected at all!")
            print("   - Check microphone is connected")
            print("   - Check microphone permissions")
            print("   - Check microphone is not muted")
        elif chunks_with_sound < total_chunks * 0.1:
            print("\n⚠️  Very little sound detected")
        else:
            print("\n✅ Sound detection working!")

    except Exception as e:
        print(f"\n❌ Error in raw recording: {e}")
    finally:
        p.terminate()


if __name__ == "__main__":
    print("=" * 60)
    print("AUDIO PIPELINE DIAGNOSTIC TOOL")
    print("=" * 60)

    # Test 1: Basic microphone
    device_idx = test_microphone_basics()

    if device_idx is None:
        print("\n❌ Microphone test failed. Fix microphone issues before continuing.")
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
