# GAIA Command Line Interface

GAIA (Generative AI Acceleration Infrastructure & Applications) provides a command-line interface (CLI) for easy interaction with AI models and agents. The CLI allows you to start servers, manage chat sessions, and customize model configurations without writing code.

## Getting Started

1. Make sure you follow the instructions outlined in [Installation](#installation) section first.

1. Open a command terminal and activate the GAIA environment: `conda activate C:\Users\<username>\AppData\Local\GAIA\gaia_env`. Change `<username>` to your actual username.

1. Run `gaia-cli -h` to see the available commands.

## Basic Usage

The CLI supports several core commands:
- `start`: Launch the GAIA servers
- `chat`: Start an interactive chat session
- `prompt`: Send a single message and get a response
- `stop`: Shutdown all servers
- `stats`: View model performance statistics

### Server State Management

The GAIA CLI uses a `.gaia_servers.json` file to manage server state and connection information. When you run `gaia-cli start`, it creates or updates this file in the current working directory. The file contains server configurations, for example:

```json
{
    "agent_name": "Chaty",
    "host": "127.0.0.1",
    "port": 8001,
    "model": "llama3.2:1b",
    "max_new_tokens": 512,
    "backend": "ollama",
    "device": "cpu",
    "dtype": "int4",
    "server_pids": {
        "agent": 27324,
        "ollama_model": 25176,
        "ollama_client": 13988
    },
    "logging_level": "DEBUG"
}
```

This configuration file tracks:
- Connection details (host, port)
- Model configuration (model name, backend, device, precision)
- Server process IDs for management
- Agent settings and logging preferences

When running client commands like `gaia-cli chat`, the CLI looks for the `.gaia_servers.json` file in the current directory to establish connections. This means:

- All commands should be run from the same directory where `gaia-cli start` was executed

### Quick Start Demo

A simple chat demo using `gaia-cli` to verify functionality:

1. Activate the GAIA environment:
    ```
    conda activate C:\Users\<username>\AppData\Local\GAIA\gaia_env
    ```
    Change `<username>` to your actual username.

1. Start the gaia-cli backend:
   ```
   gaia-cli start
   ```
   This command initializes the necessary servers with the default parameters and model.

   ```
   (gaiaenv) C:\Users\kalin\Work\gaia>gaia-cli start
   [2024-10-14 18:34:09,556] | INFO | gaia.cli.start | cli.py:55 | Starting servers...
   ...
   [2024-10-14 18:34:23,769] | INFO | gaia.cli.wait_for_servers | cli.py:75 | All servers are ready.
   Servers started successfully.
   ```

1. Open a new terminal window and activate the same GAIA environment as above. Make sure you are in the same directory as the previous command was run from.

1. Begin a chat session:
   ```
   gaia-cli chat
   ```
   This opens an interactive chat interface where you can converse with the AI.
   ```
   Starting chat with Chaty. Type 'exit' to quit, 'restart' to clear chat history.
   ```

1. During the chat:
   - Type your messages and press Enter to send.
   - Type `exit` to exit the chat session.
   ```
   You: who are you in one sentence?
   {"status": "Success", "response": "Yer lookin' fer me, matey? I be the swashbucklin' AI pirate bot, here to help ye with yer queries and share tales o' the seven seas!"}
   You: exit
   Chat session ended.
   ```

1. Terminate the servers when finished:
   ```
   gaia-cli stop
   ```
   This ensures all server processes are properly shut down.
   ```
   (gaiaenv) C:\Users\kalin\Work\gaia>gaia-cli stop
   [2024-10-14 18:36:55,218] | INFO | gaia.cli.stop | cli.py:204 | Stopping servers...
   ...
   [2024-10-14 18:36:55,341] | INFO | gaia.cli.stop | cli.py:233 | All servers stopped.
   Servers stopped successfully.
   ```

## Advanced Configuration

The CLI supports various configuration options when starting the servers:

```bash
gaia-cli start [OPTIONS]
```

Available options:
- `--agent_name`: Choose the AI agent (default: "Chaty")
- `--model`: Specify the model to use (default: "llama3.2:1b")
- `--backend`: Select inference backend ["oga", "hf", "ollama"] (default: "ollama")
- `--device`: Choose compute device ["cpu", "npu", "gpu"] (default: "cpu")
- `--dtype`: Set model precision ["float32", "float16", "bfloat16", "int8", "int4"] (default: "int4")
- `--max-new-tokens`: Maximum response length (default: 512)

Common usage examples:
```bash
# Use Mistral 7B model
gaia-cli start --model mistral:7b

# Run on NPU with OGA backend
gaia-cli start --backend oga --device npu

# Use higher precision for better quality
gaia-cli start --dtype float16
```

For more options and detailed usage, refer to `gaia-cli --help`.

## Utility Functions

### Download YouTube Transcripts
You can download transcripts from YouTube videos directly using the CLI:
```bash
gaia-cli --download-transcript "https://www.youtube.com/watch?v=VIDEO_ID"
```

By default, this saves the transcript to `transcript.txt`. You can specify a custom output file:
```bash
gaia-cli --download-transcript "https://www.youtube.com/watch?v=VIDEO_ID" --output "my_transcript.txt"
```

## Running GAIA CLI Talk Demo on Hybrid
GAIA CLI's talk mode enables voice-based interaction with LLMs using Whisper for speech recognition. This feature allows for natural conversation with the AI through your microphone.

1. Activate the GAIA environment:
    ```
    conda activate C:\Users\<username>\AppData\Local\GAIA\gaia_env
    ```
    Change `<username>` to your actual username.

1. To run in hybrid mode, use the following command:
   ```bash
   gaia-cli start --model "amd/Llama-3.2-1B-Instruct-awq-g128-int4-asym-fp16-onnx-hybrid" --backend "oga" --device "hybrid" --dtype "int4"
   ```

1. Open a new terminal window and activate the same GAIA environment as above. Make sure you are in the same directory as the previous command was run from.

1. Launch `talk` mode:
   ```bash
   gaia-cli talk
   ```

1. You should see a similar output:
   ```bash
   [2025-01-17 15:01:37] | INFO | gaia.cli.__init__ | cli.py:95 | Gaia CLI client initialized with the following settings:
   [2025-01-17 15:01:37] | INFO | gaia.cli.__init__ | cli.py:96 | agent_name: Chaty
    host: 127.0.0.1
   port: 8001
    llm_port: 8000
   ollama_port: 11434
    model: amd/Llama-3.2-1B-Instruct-awq-g128-int4-asym-fp16-onnx-hybrid
   max_new_tokens: 512
    backend: oga
   device: hybrid
    dtype: int4
   Starting voice chat with Chaty. Say 'exit' to quit, or 'restart' to clear chat history.
   [2025-01-17 15:01:37] | INFO | gaia.cli.start_voice_chat | cli.py:397 | Initializing voice chat...
   Starting voice chat with Chaty. Say 'exit' to quit, or 'restart' to clear chat history.
   [2025-01-17 15:01:37] | INFO | gaia.audio.whisper_asr.__init__ | whisper_asr.py:25 | Loading Whisper model: base
   [2025-01-17 15:01:39] | INFO | gaia.cli.start_voice_chat | cli.py:410 | Using audio device: Remote Audio
   Using device: Remote Audio
   [2025-01-17 15:01:39] | INFO | gaia.cli.start_voice_chat | cli.py:414 | Starting audio recording...
   [2025-01-17 15:01:39] | INFO | gaia.audio.whisper_asr.start_recording | audio_recorder.py:134 | Initializing recording...
   [2025-01-17 15:01:39] | INFO | gaia.audio.whisper_asr.start_recording | audio_recorder.py:145 | Starting record thread...
   [2025-01-17 15:01:39] | INFO | gaia.audio.whisper_asr._record_audio | audio_recorder.py:43 | Using audio device: Remote Audio
   [2025-01-17 15:01:39] | INFO | gaia.audio.whisper_asr._record_audio | audio_recorder.py:54 | Recording started...
   [2025-01-17 15:01:39] | INFO | gaia.audio.whisper_asr.start_recording | audio_recorder.py:153 | Starting process thread...
   [2025-01-17 15:01:39] | INFO | gaia.audio.whisper_asr._process_audio | whisper_asr.py:43 | Starting audio processing...
   [2025-01-17 15:01:39] | INFO | gaia.cli.start_voice_chat | cli.py:418 | Starting audio processing thread...
   [2025-01-17 15:01:39] | INFO | gaia.cli.start_voice_chat | cli.py:424 | Listening for voice input...
   ```

1. Speak to the microphone and the LLM will respond. You can interrupt the LLM while it's generating a response by speaking again. For example:
   ```bash
   You: Hi, can you hear me?
   Chaty: I can hear you. How can I help you today?
   ```

1. When you are done, say `quit` to exit the application.

### Troubleshooting
- If you don't see your speech printed on screen, it's possible your microphone is on a different device index. Try the following:
1. List devices available with `gaia-cli talk --list-devices`).
1. Use talk with the correct device by running `gaia-cli talk --audio-device-index <device_index>`
- For better recognition accuracy, try using a larger Whisper model (e.g., "medium" or "large") `gaia-cli talk --whisper-model-size medium`
- Ensure you're in a quiet environment for optimal speech recognition
- Speaking clearly and at a moderate pace will improve transcription quality

### Configuration Options
Run `gaia-cli talk --help` to see the available options. You can customize the voice interaction experience with these parameters:

- `--whisper-model-size`: Choose the Whisper model size for speech recognition
  ```bash
  gaia-cli talk --whisper-model-size medium  # Options: tiny, base, small, medium, large
  ```

- `--audio-device-index`: Specify which microphone to use
  ```bash
  gaia-cli talk --audio-device-index 2  # Default: 1
  ```

### Voice Commands
During a talk session:
- Say "exit" or "quit" to end the session
- Say "restart" to clear the chat history
- Natural pauses (>2 seconds) trigger the AI's response

### Troubleshooting
- If you don't hear any response, check your microphone settings and the `--audio-device-index`
- For better recognition accuracy, try using a larger Whisper model (e.g., "medium" or "large")
- Ensure you're in a quiet environment for optimal speech recognition
- Speaking clearly and at a moderate pace will improve transcription quality

## Development Setup

For manual setup including creation of the virtual environment and installation of dependencies, refer to the instructions outlined [here](./docs/ort_genai_npu.md). This approach is not recommended for most users and is only needed for development purposes.

