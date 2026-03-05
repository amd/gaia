#!/usr/bin/env python3
#
# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Basic talk mode test - minimal complexity for debugging
"""

import time

import numpy as np
import sounddevice as sd
import whisper


def test_basic_talk():
    """Test basic audio -> whisper pipeline"""

    print("Loading Whisper model (tiny for speed)...")
    model = whisper.load_model("tiny")

    # Get default device
    try:
        default_device = sd.query_devices(kind="input")
        device_idx = default_device["index"]
        print(f"Using device [{device_idx}]: {default_device['name']}")
    except Exception:
        print("No default device, using device 0")
        device_idx = 0

    CHUNK = 2048
    CHANNELS = 1
    RATE = 16000

    stream = sd.InputStream(
        samplerate=RATE,
        channels=CHANNELS,
        dtype="float32",
        device=device_idx,
        blocksize=CHUNK,
    )
    stream.start()

    print("\n" + "=" * 60)
    print("RECORDING - Speak for 3 seconds then wait for transcription")
    print("Press Ctrl+C to stop")
    print("=" * 60 + "\n")

    try:
        while True:
            # Record 3 seconds of audio
            print("Recording for 3 seconds... SPEAK NOW!")
            audio_buffer = []
            start_time = time.time()

            while time.time() - start_time < 3:
                frames, overflowed = stream.read(CHUNK)
                audio_buffer.append(frames[:, 0].copy())

            # Convert to numpy array
            audio_data = np.concatenate(audio_buffer)

            # Check if we got audio
            energy = np.abs(audio_data).mean()
            print(f"Audio captured: {len(audio_data)} samples, energy: {energy:.6f}")

            if energy < 0.0001:
                print("⚠️  No audio detected! Check your microphone.")
                print("Waiting 2 seconds before next attempt...\n")
                time.sleep(2)
                continue

            # Transcribe
            print("Transcribing...")
            try:
                result = model.transcribe(
                    audio_data,
                    language="en",
                    temperature=0.0,
                    no_speech_threshold=0.6,
                )

                text = result["text"].strip()
                if text:
                    print(f"✅ TRANSCRIBED: {text}")
                else:
                    print("❌ No speech detected in audio")

            except Exception as e:
                print(f"❌ Transcription error: {e}")

            print("\nWaiting 2 seconds before next recording...\n")
            print("-" * 60 + "\n")
            time.sleep(2)

    except KeyboardInterrupt:
        print("\nStopping...")

    finally:
        stream.stop()
        stream.close()


if __name__ == "__main__":
    test_basic_talk()
