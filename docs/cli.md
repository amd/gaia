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

1. Open a new terminal window and activate the same GAIA environment as above.

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

## Running GAIA CLI Talk Mode
GAIA CLI's talk mode enables voice-based interaction with LLMs using Whisper for speech recognition. This feature allows for natural conversation with the AI through your microphone.

1. Start the servers:
   ```bash
   gaia-cli start
   ```
   1. To run in hybrid mode, use the following command:
   ```bash
   gaia-cli start --model "amd/Llama-3.2-1B-Instruct-awq-g128-int4-asym-fp16-onnx-hybrid" --backend "oga" --device "hybrid" --dtype "int4"
   ```

2. Launch talk mode:
   ```bash
   gaia-cli talk
   ```

### Configuration Options
You can customize the voice interaction experience with these parameters:

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

