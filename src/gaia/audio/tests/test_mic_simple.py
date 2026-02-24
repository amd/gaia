#!/usr/bin/env python3
#
# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Simple microphone test - just records and shows audio levels
"""

import time

import numpy as np
import sounddevice as sd


def test_mic():
    """Simple test to record and display audio levels"""
    # List devices
    devices = sd.query_devices()
    print("Available input devices:")
    for dev_info in devices:
        if dev_info.get("max_input_channels", 0) > 0:
            print(f"  [{dev_info['index']}] {dev_info.get('name')}")

    # Get default device
    try:
        default_device = sd.query_devices(kind="input")
        device_idx = default_device["index"]
        print(f"\nUsing default device [{device_idx}]: {default_device['name']}")
    except Exception:
        print("No default device, using device 0")
        device_idx = 0

    # Audio settings
    CHUNK = 2048
    CHANNELS = 1
    RATE = 16000

    print(f"\nOpening audio stream...")
    stream = sd.InputStream(
        samplerate=RATE,
        channels=CHANNELS,
        dtype="float32",
        device=device_idx,
        blocksize=CHUNK,
    )
    stream.start()

    print("Recording... Speak into the microphone (Press Ctrl+C to stop)\n")
    print("Energy Level:")
    print("-" * 50)

    try:
        max_energy = 0
        chunks_with_sound = 0
        total_chunks = 0

        while True:
            # Read audio
            frames, overflowed = stream.read(CHUNK)
            audio = frames[:, 0]

            # Calculate energy
            energy = np.abs(audio).mean()
            max_energy = max(max_energy, energy)
            total_chunks += 1

            # Visualize
            bar_length = int(energy * 1000)  # Scale for visualization
            bar = "█" * min(bar_length, 50)

            if energy > 0.001:
                chunks_with_sound += 1
                print(f"{bar:{50}} {energy:.6f}")
            else:
                print(f"{'':{50}} {energy:.6f} (silence)")

            time.sleep(0.05)

    except KeyboardInterrupt:
        print("\n" + "-" * 50)
        print(f"\nRecording stopped.")
        print(f"Statistics:")
        print(f"  Total chunks: {total_chunks}")
        print(f"  Chunks with sound: {chunks_with_sound}")
        print(f"  Max energy: {max_energy:.6f}")

        if chunks_with_sound == 0:
            print("\n⚠️  NO SOUND DETECTED!")
            print("Check:")
            print("  - Microphone is not muted")
            print("  - Correct microphone selected")
            print("  - Microphone permissions granted")
        elif max_energy < 0.01:
            print("\n⚠️  Very low audio levels")
            print("  - Try speaking louder")
            print("  - Move closer to microphone")
            print("  - Check microphone volume in system settings")
        else:
            print("\n✅ Microphone is working!")

    finally:
        stream.stop()
        stream.close()


if __name__ == "__main__":
    test_mic()
