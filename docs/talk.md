# Voice Interaction with GAIA CLI

This document covers all voice-related features including Text-to-Speech (TTS), Automatic Speech Recognition (ASR), and talk mode functionality.

## Talk Mode Overview
GAIA CLI's talk mode enables voice-based interaction with LLMs using Whisper for automatic speech recognition (ASR) and Kokoro TTS for text-to-speech (TTS). This feature allows for natural conversation with the AI through your microphone and speakers.

### GAIA-CLI Talk Demo

1. Make sure to follow the [Getting Started Guide](../README.md#getting-started-guide) to install the `gaia` tool.

1. Once installed, double click on the desktop icon **GAIA-CLI** to launch the command-line shell with the GAIA environment activated.

1. Start the Lemonade server:
   ```bash
   lemonade-server serve
   ```
   Or double-click the desktop shortcut to start the server.

1. Launch talk mode:
   ```bash
   gaia talk
   ```
   Optionally, launch talk mode using ASR only:
   ```bash
   gaia talk --no-tts
   ```

1. Once you see the following in the console, you can start talking:
   ```bash
   ...
   [2025-02-06 11:56:30] | INFO | gaia.cli.start_voice_chat | cli.py:421 | Starting audio processing thread...
   [2025-02-06 11:56:30] | INFO | gaia.cli.start_voice_chat | cli.py:427 | Listening for voice input...
   ⠴ Listening...
   ```

1. Say "exit" or "quit" to end the session.

### Voice Commands
During a talk session:
- Say "exit" or "quit" to end the session
- Say "restart" to clear the chat history
- Natural pauses (>1 seconds) trigger the AI's response
- Press Enter key during audio playback to stop the current response

### Configuration Options
Customize the voice interaction experience:
```bash
# Choose Whisper model size for speech recognition
gaia talk --whisper-model-size medium  # Options: tiny, base, small, medium, large

# Specify which microphone to use
gaia talk --audio-device-index 2  # Default: 1
```

## Automatic Speech Recognition (ASR) Tests
You can test the ASR system using the `gaia test` command with various test types:

### Audio File Transcription Test
Test transcription of an existing audio file:
```bash
gaia test --test-type asr-file-transcription --input-audio-file path/to/audio.wav
```
Supported audio formats include WAV, MP3, M4A, and other common formats.

Options:
- `--input-audio-file`: Path to the audio file to transcribe (required)
- `--whisper-model-size`: Choose Whisper model size ["tiny", "base", "small", "medium", "large"] (default: "base")

### List Audio Devices Test
List available audio input devices:
```bash
gaia test --test-type asr-list-audio-devices
```

### Microphone Recording Test
Test real-time transcription from your microphone:
```bash
gaia test --test-type asr-microphone --recording-duration 15
```
Options:
- `--recording-duration`: Recording duration in seconds (default: 10)
- `--whisper-model-size`: Choose Whisper model size ["tiny", "base", "small", "medium", "large"] (default: "base")
- `--audio-device-index`: Select which microphone to use (default: 1)

## Testing TTS Components
You can test different aspects of the TTS system using the `gaia test` command with various test types:

### Text Preprocessing Test
Test how the TTS system processes and formats text before speech generation:

```bash
gaia test --test-type tts-preprocessing
```
Optionally provide custom test text:
```bash
gaia test --test-type tts-preprocessing --test-text "Your test text here"
```

### Streaming Playback Test
Test real-time audio generation and playback with progress visualization:
```bash
gaia test --test-type tts-streaming
```
Optionally provide custom test text:
```bash
gaia test --test-type tts-streaming --test-text "Your test text here"
```
This test shows:
- Processing progress
- Playback progress
- Currently spoken text
- Performance metrics

### Audio File Generation Test
Test audio generation and save to a WAV file:
```bash
gaia test --test-type tts-audio-file --test-text "Your test text here" --output-audio-file ./test_output.wav
```
Use the `--output-audio-file` option to specify the output file path.

## Troubleshooting Voice Features
- If you get audio device errors, try different `--audio-device-index` values
- For better ASR accuracy, try larger Whisper models (e.g., "medium" or "large")
- Ensure you're in a quiet environment for ASR tests
- For TTS tests, make sure espeak-ng is properly installed
- If your voice input isn't being recognized, check your microphone settings
- If you don't hear the AI's voice response:
  - Check your system's audio output/speaker settings
  - Verify TTS is enabled (not using `--no-tts` flag)
  - Ensure your system volume is not muted
- Check your system's audio input settings in Settings > Audio > Input and ensure the correct microphone is selected as the default input device
- Speaking clearly and at a moderate pace will improve transcription quality

# License

[MIT License](../LICENSE.md)

Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT