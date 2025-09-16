#!/usr/bin/env python3
"""
Simple microphone test - just records and shows audio levels
"""

import pyaudio
import numpy as np
import time


def test_mic():
    """Simple test to record and display audio levels"""
    p = pyaudio.PyAudio()

    # List devices
    print("Available input devices:")
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info.get("maxInputChannels") > 0:
            print(f"  [{i}] {info.get('name')}")

    # Get default device
    try:
        default_device = p.get_default_input_device_info()
        device_idx = default_device["index"]
        print(f"\nUsing default device [{device_idx}]: {default_device['name']}")
    except:
        print("No default device, using device 0")
        device_idx = 0

    # Audio settings
    CHUNK = 2048
    FORMAT = pyaudio.paFloat32
    CHANNELS = 1
    RATE = 16000

    print(f"\nOpening audio stream...")
    stream = p.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=RATE,
        input=True,
        input_device_index=device_idx,
        frames_per_buffer=CHUNK,
    )

    print("Recording... Speak into the microphone (Press Ctrl+C to stop)\n")
    print("Energy Level:")
    print("-" * 50)

    try:
        max_energy = 0
        chunks_with_sound = 0
        total_chunks = 0

        while True:
            # Read audio
            data = stream.read(CHUNK, exception_on_overflow=False)
            audio = np.frombuffer(data, dtype=np.float32)

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
            print("  - Check microphone volume in Windows settings")
        else:
            print("\n✅ Microphone is working!")

    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()


if __name__ == "__main__":
    test_mic()
