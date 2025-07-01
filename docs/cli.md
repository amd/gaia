# GAIA Command Line Interface (CLI)

GAIA (Generative AI Acceleration Infrastructure & Applications) provides a command-line interface (CLI) for easy interaction with AI models and agents. The CLI allows you to start servers, manage chat sessions, and customize model configurations without writing code.

## GAIA-CLI Getting Started Guide

1. Make sure to follow the [Getting Started Guide](../README.md#getting-started-guide) to install the `gaia-cli` tool.

1. GAIA automatically configures optimal settings for Ryzen AI hardware.

1. Once installed, double click on the desktop icon **GAIA-CLI** to launch the command-line shell with the GAIA environment activated.

1. Start the servers with default settings:
   ```bash
   gaia-cli start
   ```
   The optimal configuration for Ryzen AI hardware is automatically applied.

   You can also use the hybrid shortcut or specify a different model:
   ```bash
   # Using hybrid shortcut
   gaia-cli start --hybrid

   # Or specify a different model
   gaia-cli start --model "amd/Llama-3.2-3B-Instruct-awq-g128-int4-asym-fp16-onnx-hybrid"
   ```

1. Wait for servers to start up. You should see the following in the console:
   ```bash
   ...
   INFO:     ::1:56598 - "GET /health HTTP/1.1" 200 OK
   [2025-02-06 11:12:49] | INFO | gaia.cli.check_servers_ready | cli.py:204 | All servers are ready.
   [2025-02-06 11:12:49] | INFO | gaia.cli.wait_for_servers | cli.py:145 | All servers are ready.
   Servers started successfully.
   ```

1. Now try the chat demo in [GAIA-CLI Chat Demo](./cli.md#gaia-cli-chat-demo) section or talk demo in [GAIA-CLI Talk Demo](./talk.md#gaia-cli-talk-demo) section.

## GAIA-CLI Chat Demo

1. Follow the directions in [GAIA-CLI Getting Started Guide](#gaia-cli-getting-started-guide) to start the servers.

1. Begin a chat session by running:
   ```
   gaia-cli chat
   ```
   This opens an interactive chat interface where you can converse with the AI.
   ```
   Starting chat with Chaty. Type 'stop' to quit, 'restart' to clear chat history.
   ```

1. During the chat:
   - Type your messages and press Enter to send.
   - Type `stop` to quit the chat session.
   ```bash
   You: who are you in one sentence?
   {"status": "Success", "response": "Yer lookin' fer me, matey? I be the swashbucklin' AI pirate bot, here to help ye with yer queries and share tales o' the seven seas!"}
   You: stop
   Chat session ended.
   ```

1. Terminate the servers when finished:
   ```bash
   gaia-cli stop
   ```
   This ensures all server processes are properly shut down.
   ```
   ...
   [2024-10-14 18:36:55,341] | INFO | gaia.cli.stop | cli.py:233 | All servers stopped.
   Servers stopped successfully.
   ```

## Basic Usage

The CLI supports several core commands:
- `start`: Launch the GAIA servers
- `chat`: Start an interactive chat session that tracks message history
- `prompt`: Send a single message and get a response
- `stop`: Shutdown all servers
- `stats`: View model performance statistics
- `kill`: Kill a process running on a specific port

## Advanced Configuration

The CLI supports various configuration options when starting the servers:

```bash
gaia-cli start [OPTIONS]
```

Available options:
- `--agent-name`: Choose the AI agent (default: "Chaty")
- `--model`: Specify the model to use (default: "llama3.2:1b")
- `--max-new-tokens`: Maximum response length (default: 512)
- `--background`: Launch mode ["terminal", "silent", "none"] (default: "silent")
- `--hybrid`: Shortcut for optimal configuration (sets model to hybrid variant)
- `--stats`: Show performance statistics after generation
- `--host`: Host address for the Agent server (default: "127.0.0.1")
- `--port`: Port for the Agent server (default: 8001)
- `--logging-level`: Set logging verbosity ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] (default: "INFO")

Common usage examples:
```bash
# Start with optimal settings
gaia-cli start

# Use hybrid shortcut (same as default if available)
gaia-cli start --hybrid

# Use Llama 3.2 3B model (overrides default model)
gaia-cli start --model llama3.2:3b

# Launch servers in a separate terminal window
gaia-cli start --background terminal

# Show performance stats after each response
gaia-cli start --stats

# Enable debug logging for troubleshooting
gaia-cli start --logging-level DEBUG
```

### Background Launch Modes

The `--background` option controls how the servers are launched:

- `terminal`: Opens a new terminal window for server processes (default)
- `silent`: Runs servers in background with output redirected to gaia.cli.log
- `none`: Runs servers in the current terminal

When using background modes:
```bash
# Launch in new terminal window
gaia-cli start --background terminal

# Launch silently with logging
gaia-cli start --background silent
```

You can then use `gaia-cli chat` or `gaia-cli talk` in a separate terminal while the servers run in the background. Use `gaia-cli stop` to shut down the servers when finished.

## Running GAIA CLI Talk Mode
GAIA CLI supports voice-based interaction with AI models through talk mode. For detailed instructions on using talk mode, including setup, configuration options, testing and troubleshooting, please refer to the [Voice Interaction Guide](./talk.md).

## Development Setup

For manual setup including creation of the virtual environment and installation of dependencies, refer to the instructions outlined [here](./dev.md). This approach is not recommended for most users and is only needed for development purposes.

## Testing and Utilities

The CLI includes several testing and utility commands for development and troubleshooting.

### Test Commands

```bash
gaia-cli test --test-type TYPE [OPTIONS]
```

Text-to-Speech (TTS) tests:
- `tts-preprocessing`: Test TTS text preprocessing
- `tts-streaming`: Test TTS streaming playback
- `tts-audio-file`: Test TTS audio file generation

Text-to-Speech (TTS) options:
- `--test-text`: Text to use for TTS tests: preprocessing, streaming, audio file generation
- `--output-audio-file`: Output file path for TTS audio file test (default: output.wav)

Automatic Speech Recognition (ASR) tests:
- `asr-file-transcription`: Test ASR file transcription
- `asr-microphone`: Test ASR microphone input

Automatic Speech Recognition (ASR) options:
- `--input-audio-file`: Input audio file path for ASR file transcription test
- `--recording-duration`: Recording duration in seconds for ASR microphone test (default: 10)
- `--audio-device-index`: Index of audio input device for ASR microphone test (optional)
- `--whisper-model-size`: Size of the Whisper model to use for ASR tests (default: base)

Example usage:
```bash
# Test TTS preprocessing with custom text
gaia-cli test --test-type tts-preprocessing --test-text "Hello, world!"

# Test TTS streaming with custom text
gaia-cli test --test-type tts-streaming --test-text "Hello, world!"

# Test TTS audio file generation with custom text and output file path
gaia-cli test --test-type tts-audio-file --test-text "Hello, world!" --output-audio-file my_speech.wav

# Test ASR file transcription
gaia-cli test --test-type asr-file-transcription --input-audio-file ./data/audio/test.m4a --whisper-model-size base

# Test microphone input for 30 seconds
gaia-cli test --test-type asr-microphone --recording-duration 30 --audio-device-index 1
```

### YouTube Utilities

The CLI provides utilities for working with YouTube content:

```bash
gaia-cli youtube [OPTIONS]
```

Available options:
- `--download-transcript URL`: Download transcript from a YouTube URL
- `--output-path PATH`: Output file path for transcript (optional)

Example usage:
```bash
# Download YouTube transcript
gaia-cli youtube --download-transcript "https://youtube.com/watch?v=..." --output-path transcript.txt
```

### Audio Device Management

When using voice features, you can list available audio input devices:

```bash
gaia-cli talk --list-devices
```

This will display all available audio input devices with their indices and properties, helping you choose the correct device for voice interaction.

### Process Management

The `kill` command allows you to terminate processes running on specific ports:

```bash
gaia-cli kill --port PORT_NUMBER
```

For example:
```bash
# Kill process running on port 8000
gaia-cli kill --port 8000

# Kill process running on port 8001
gaia-cli kill --port 8001
```

This is useful for cleaning up lingering server processes that weren't properly shut down. The command will:
- Find the process ID (PID) of any process bound to the specified port
- Forcefully terminate that process
- Provide feedback about the operation's success or failure

### Server State Management

The GAIA CLI uses a `.gaia_servers.json` file to manage server state and connection information. When you run `gaia-cli start`, it creates or updates this file in the current working directory. The file contains server configurations, for example:

```json
{
    "agent_name": "Chaty",
    "host": "127.0.0.1",
    "port": 8001,
    "model": "llama3.2:1b",
    "max_new_tokens": 512,
    "server_pids": {
        "agent": 27324,
        "llm": 25176
    },
    "logging_level": "DEBUG"
}
```

This configuration file tracks:
- Connection details (host, port)
- Model configuration (model name, parameters)
- Server process IDs for management
- Agent settings and logging preferences

When running client commands like `gaia-cli chat`, the CLI looks for the `.gaia_servers.json` file in the current directory to establish connections. This means:

- All commands should be run from the same directory where `gaia-cli start` was executed

## Troubleshooting

### Common Issues

For general troubleshooting, refer to the [Development Guide](./dev.md#troubleshooting) which covers:
- Installation issues
- Model loading problems
- Server startup failures
- Performance optimization

# License

Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT