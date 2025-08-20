# Talk Agent

A voice chat application that uses AudioClient with direct LLM communication (no WebSocket agent server needed).

## Usage

### Basic Usage
```bash
# Run with default settings (local LLM)
python src/gaia/talk/app.py

# Run with OpenAI API
python src/gaia/talk/app.py --use-openai

# Run without TTS (speech output)
python src/gaia/talk/app.py --no-tts
```

### Advanced Options
```bash
# Custom Whisper model
python src/gaia/talk/app.py --whisper-model-size large

# Show performance stats
python src/gaia/talk/app.py --show-stats

# Verbose logging
python src/gaia/talk/app.py --verbose
```

### System Prompt
```bash
# Set a custom system prompt
python src/gaia/talk/app.py --system-prompt "You are a helpful cooking assistant."
```

## Features

- **Voice Input**: Uses Whisper ASR for speech recognition
- **Voice Output**: Uses Kokoro TTS for speech synthesis  
- **Direct LLM**: Communicates directly with LLM via LLMClient (no agent server needed)
- **Interrupts**: Press Enter to interrupt generation or TTS
- **Commands**: Say "stop" to quit the application
- **Stats**: Optional performance statistics display
- **Flexible**: Supports both local LLM and OpenAI API

## Requirements

- Local LLM server (Lemonade) running OR OpenAI API key
- Audio input device (microphone)
- Audio output device (speakers/headphones)
- Python packages: `pip install .[talk]`

## Controls

- **Speak**: Just talk normally - the app will detect when you stop speaking
- **Interrupt**: Press Enter during generation or speech playback
- **Quit**: Say "stop" or press Ctrl+C