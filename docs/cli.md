# GAIA Command Line Interface (CLI)

GAIA (Generative AI Acceleration Infrastructure & Applications) provides a command-line interface (CLI) for easy interaction with AI models and agents. The CLI allows you to query models directly, manage chat sessions, and access various utilities without writing code.

## Platform Support

- **Windows 11**: Full GUI and CLI support with installer and desktop shortcuts
- **Linux (Ubuntu/Debian)**: Full GUI and CLI support via source installation
- **macOS**: Not supported

## GAIA-CLI Getting Started Guide

### Windows Installation
1. Make sure to follow the [Getting Started Guide](../README.md#getting-started-guide) to install the necessary `gaia` CLI and `lemonade` LLM serving tools.

2. GAIA uses Lemonade Server to provide optimal performance on Ryzen AI hardware.

3. Once installed, double click on the desktop icon **GAIA-CLI** to launch the command-line shell with the GAIA environment activated.

4. The GAIA CLI connects to the Lemonade server for AI processing. Make sure the server is running by:
   - Double-clicking the desktop shortcut, or
   - Running: `lemonade-server serve`

### Linux Installation
1. **Install from Source**: Follow the [Linux Installation](../README.md#linux-installation) instructions in the main README.

2. **Install Lemonade Server**: Download and install the Lemonade server from [lemonade-server.ai](https://www.lemonade-server.ai) or build from source.

3. **Start the Server**: Run the Lemonade server:
   ```bash
   lemonade-server serve
   ```

4. **Verify Installation**: Test the CLI:
   ```bash
   gaia -v
   gaia llm "Hello, world!"
   ```

5. Now try the direct LLM demo in the [GAIA CLI LLM Demo](#gaia-cli-llm-demo) section, chat demo in the [GAIA CLI Chat Demo](#gaia-cli-chat-demo) section, or talk demo in the [GAIA CLI Talk Demo](#gaia-cli-talk-demo) section.

## GAIA CLI LLM Demo

The fastest way to interact with AI models is through the direct LLM command:

1. Try a simple query:
   ```bash
   gaia llm "What is 1+1?"
   ```

   This will stream the response directly to your terminal. The system will automatically check for the lemonade server and provide helpful error messages if it's not running.

2. Use advanced options:
   ```bash
   # Specify model and token limit
   gaia llm "Explain quantum computing in simple terms" --model Llama-3.2-3B-Instruct-Hybrid --max-tokens 200

   # Disable streaming for batch processing
   gaia llm "Write a short poem about AI" --no-stream
   ```

3. If you get a connection error, make sure the lemonade server is running:
   ```bash
   lemonade-server serve
   ```

## GAIA CLI Chat Demo

1. Make sure the Lemonade server is running (see [Getting Started Guide](#gaia-cli-getting-started-guide)).

2. Begin an interactive chat session:
   ```bash
   gaia chat
   ```
   This opens an interactive chat interface with conversation history and streaming responses.

3. During the chat session, you can:
   - Type your messages and press Enter to send
   - Use special commands to manage your session:
     - `/clear` - Clear conversation history
     - `/history` - Show conversation history
     - `/system` - Show current system prompt
     - `/model` - Show model information
     - `/prompt` - Show complete formatted prompt
     - `/stats` - Show performance statistics
     - `/help` - Show available commands
   - Type `quit`, `exit`, or `bye` to end the session

   Example interaction:
   ```bash
   You: who are you in one sentence?
   Assistant: I'm an AI assistant designed to help you with various tasks and answer your questions.
   You: /history
   ==============================
   Conversation History:
   ==============================
   User: who are you in one sentence?
   Assistant: I'm an AI assistant designed to help you with various tasks and answer your questions.
   ==============================
   You: quit
   Goodbye!
   ```

4. You can also send single messages without starting an interactive session:
   ```bash
   # Send a single message
   gaia chat "What is machine learning?"

   # Use a specific model
   gaia chat "Explain quantum computing" --model Llama-3.2-1B-Instruct-Hybrid
   ```

## GAIA CLI Talk Demo

For voice-based interaction with AI models, see the [Voice Interaction Guide](./talk.md).

**Note:** Voice features are fully supported on both Windows and Linux platforms.

## Basic Usage

The CLI supports the following core commands:

```bash
gaia --help
```

### Available Commands

- **`llm`**: Send direct queries to language models (fastest option, no server management required)
- **`prompt`**: Send a single message to an agent and get a response
- **`chat`**: Start an interactive text chat session with message history
- **`talk`**: Start a voice-based conversation session
- **`code`**: Python code assistant with analysis, generation, and linting (see [Code Guide](./code.md))
- **`blender`**: Create and modify 3D scenes using the Blender agent (see [Blender Guide](./blender.md))
- **`mcp`**: Start and manage MCP (Model Context Protocol) bridge servers for integration with external clients and services (see [MCP Bridge Guide](./mcp.md))
- **`stats`**: View model performance statistics from the most recent run
- **`test`**: Run various audio/speech tests for development and troubleshooting
- **`youtube`**: YouTube utilities for transcript downloading
- **`kill`**: Kill a process running on a specific port
- **Evaluation commands**: See the [Evaluation Guide](./eval.md) for comprehensive documentation of:
  - `groundtruth`: Generate ground truth data for various evaluation use cases
  - `create-template`: Create evaluation template files from ground truth data
  - `eval`: Evaluate RAG system performance using results data
  - `report`: Generate summary reports from evaluation results
  - `generate`: Generate synthetic test data (meeting transcripts or business emails)
  - `batch-experiment`: Run systematic experiments with different LLM configurations
  - `visualize`: Launch web-based evaluation results visualizer for interactive comparison

### Global Options

All commands support these global options:
- `--logging-level`: Set logging verbosity [DEBUG, INFO, WARNING, ERROR, CRITICAL] (default: INFO)
- `-v, --version`: Show program's version number and exit

## LLM Command

The `llm` command provides direct access to language models:

```bash
gaia llm QUERY [OPTIONS]
```

**Available options:**
- `--model`: Specify the model to use (optional, uses client default)
- `--max-tokens`: Maximum tokens to generate (default: 512)
- `--no-stream`: Disable streaming response (streaming enabled by default)

**Examples:**
```bash
# Basic query with streaming
gaia llm "What is machine learning?"

# Use specific model with token limit
gaia llm "Explain neural networks" --model Llama-3.2-3B-Instruct-Hybrid --max-tokens 300

# Disable streaming for batch processing
gaia llm "Generate a Python function to sort a list" --no-stream
```

**Requirements**: The lemonade server must be running. If not available, the command will provide clear instructions on how to start it.

## Prompt Command

Send a single prompt to a GAIA agent:

```bash
gaia prompt "MESSAGE" [OPTIONS]
```

**Available options:**
- `--model`: Model to use for the agent (default: "Llama-3.2-3B-Instruct-Hybrid")
- `--max-new-tokens`: Maximum number of new tokens to generate (default: 512)
- `--stats`: Show performance statistics after generation

**Examples:**
```bash
# Basic prompt
gaia prompt "What is the weather like today?"

# Use a different model with stats
gaia prompt "Create a poem about AI" --model Llama-3.2-3B-Instruct-Hybrid --stats

# Use different model and token limit
gaia prompt "Write a story" --model Llama-3.2-3B-Instruct-Hybrid --max-new-tokens 1000
```

## Chat Command

Start an interactive conversation or send a single message with conversation history:

```bash
gaia chat [MESSAGE] [OPTIONS]
```

**Behavior:**
- **No message provided**: Starts interactive chat session
- **Message provided**: Sends single message and exits

**Available options:**
- `--model`: Model name to use (default: Llama-3.2-3B-Instruct-Hybrid)
- `--max-tokens`: Maximum tokens to generate (default: 512)
- `--system-prompt`: Custom system prompt for the conversation
- `--assistant-name`: Name to use for the assistant (default: "gaia")
- `--stats`: Show performance statistics (single message mode only)

**Examples:**
```bash
# Start interactive chat session (default behavior when no message provided)
gaia chat

# Send a single message
gaia chat "What is machine learning?"

# Use specific model with custom system prompt for single message
gaia chat "Help me code" --system-prompt "You are a helpful coding assistant"

# Use custom assistant name
gaia chat "Hello" --assistant-name "Gaia"

# Interactive mode with custom settings and assistant name
gaia chat --max-tokens 1000 --model Llama-3.2-3B-Instruct-Hybrid --assistant-name "Gaia"
```

**Interactive Commands:**
During an interactive chat session, you can use these special commands:
- `/clear` - Clear conversation history
- `/history` - Show conversation history
- `/system` - Show current system prompt configuration
- `/model` - Show current model information
- `/prompt` - Show complete formatted prompt sent to LLM
- `/stats` - Show performance statistics (tokens/sec, latency, token counts)
- `/help` - Show available commands
- `quit`, `exit`, or `bye` - End the chat session

**Requirements**: The lemonade server must be running. The chat maintains conversation context automatically and supports both streaming and non-streaming modes.

## Talk Command

Start a voice-based conversation:

```bash
gaia talk [OPTIONS]
```

**Available options:**
- `--model`: Model to use for the agent (default: "Llama-3.2-3B-Instruct-Hybrid")
- `--max-new-tokens`: Maximum number of new tokens to generate (default: 512)
- `--no-tts`: Disable text-to-speech in voice chat mode
- `--audio-device-index`: Index of the audio input device to use (default: 1)
- `--whisper-model-size`: Size of the Whisper model [tiny, base, small, medium, large] (default: base)

For detailed voice interaction instructions, see the [Voice Interaction Guide](./talk.md).

## Code Command

For comprehensive documentation of GAIA's Code agent including Python code analysis, generation, and linting, see the **[Code Guide](./code.md)**.

**Prerequisites**: The Code Agent requires a larger context size (32,768 tokens). When using the local Lemonade server, start it with:
```bash
lemonade-server serve --ctx-size 32768
```

The Code agent provides:
- **Code Generation**: Create functions, classes, and unit tests from descriptions
- **Autonomous Workflow**: Complete development lifecycle with planning, implementation, testing, and verification
- **Automatic Test Generation**: Creates comprehensive unit tests after implementation
- **Iterative Error Correction**: Automatically fixes linting, syntax, and runtime errors
- **Code Analysis**: Parse Python files with AST, extract symbols, validate syntax
- **Linting & Formatting**: Analyze with pylint, format with black, auto-fix issues
- **File Editing**: Edit files with diffs, replace functions by name
- **Interactive Mode**: Multiple queries in a single session
- **Search**: Find patterns across Python files

Quick examples:
```bash
# Complete workflow: generate, validate, lint, test
gaia code "Create a calculator that adds two numbers with error handling"

# Generate a function
gaia code "Generate a Python function to calculate factorial"

# Interactive mode
gaia code --interactive

# Analyze, lint, and fix a file
gaia code "Analyze and fix linting issues in my_script.py"

# Generate tests for existing code
gaia code "Generate unit tests for the functions in utils.py"

# List available tools
gaia code --list-tools

# Silent JSON output
gaia code "Validate syntax of: def hello() print('hi')" --silent

# Use with cloud LLM (no server setup required)
gaia code "Create a REST API" --use-claude
```

## Blender Command

For comprehensive documentation of GAIA's Blender agent including 3D scene creation, interactive modeling, and natural language 3D workflows, see the **[Blender Guide](./blender.md)**.

The Blender agent provides:
- **Natural Language 3D Modeling**: Create and modify 3D scenes through conversational commands
- **Interactive Planning**: Multi-step scene creation with automatic task breakdown
- **Object Management**: Create, position, scale, and apply materials to 3D objects
- **Scene Organization**: Clear scenes, manage hierarchies, and organize complex layouts
- **MCP Integration**: Direct communication with Blender through Model Context Protocol

Quick examples:
```bash
# Run interactive Blender mode
gaia blender --interactive

# Create specific 3D objects
gaia blender --query "Create a red cube and blue sphere arranged in a line"

# Run built-in examples
gaia blender --example 2
```

## Stats Command

View performance statistics from the most recent model run:

```bash
gaia stats [OPTIONS]
```

## MCP Command

The MCP (Model Context Protocol) command provides integration with MCP-compatible clients and external services like Atlassian's Jira, Confluence, and Compass. MCP enables GAIA to connect with Claude Desktop, VS Code, Cursor, and custom applications through a standardized WebSocket-based protocol.

**For complete documentation including setup, SDK usage, and integration examples, see the [MCP Bridge Guide](./mcp.md).**

### Quick Start

```bash
# Install MCP support
pip install -e .[mcp]

# Start the MCP bridge
gaia mcp start

# Test basic functionality  
gaia mcp test --query "Hello from GAIA MCP!"

# Test Hugging Face integration
export HF_TOKEN=hf_your_token_here
gaia mcp test --query "What are the most popular LLMs on Hugging Face?"
```

### Available Subcommands

- **`start`** - Start the main MCP bridge server
- **`status`** - Check if the MCP server is running
- **`test`** - Test MCP functionality with a sample query
- **`atlassian`** - Start Atlassian integration for Jira/Confluence/Compass

### Common Options

Most MCP commands support:
- `--host` - Server host (default: localhost)
- `--port` - Server port (default: 8765)
- `--query` - Custom test query for testing commands
- `--verbose` - Enable verbose logging for all HTTP requests (for `start` command)
- `--background` - Run server in background mode (for `start` command)

For detailed usage, configuration options, SDK integration, and examples, see the [MCP Bridge Guide](./mcp.md).

## Evaluation Commands

For comprehensive documentation of GAIA's evaluation system including systematic testing, benchmarking, and model comparison capabilities, see the **[Evaluation Guide](./eval.md)**.

The evaluation system provides tools for:
- **Ground Truth Generation**: Create standardized test datasets using Claude AI
- **Automated Evaluation**: Perform semantic evaluation with detailed reporting
- **Batch Experimentation**: Run systematic experiments across multiple models
- **Performance Analysis**: Generate comprehensive comparison reports
- **Transcript Testing**: Create realistic test data for transcript processing

Quick examples:
```bash
# Generate evaluation data from documents
gaia groundtruth -f ./data/document.html

# Run systematic experiments across models
gaia batch-experiment --create-sample-config experiments.json
gaia batch-experiment -c experiments.json -i ./data -o ./results

# Evaluate and report results
gaia eval -f ./results/experiment.json
gaia report -d ./eval_results

# Default behavior: automatically skip existing evaluations 
gaia eval -d ./output/experiments -o ./output/evaluations

# Force regeneration of ALL evaluations (overrides skip behavior)
gaia eval -d ./output/experiments -o ./output/evaluations --force

# Update consolidated report incrementally
gaia eval -d ./output/experiments -o ./output/evaluations --incremental-update

# Launch interactive visualizer for results comparison
gaia visualize
```

## Visualize Command

Launch an interactive web-based visualizer for comparing evaluation results:

```bash
gaia visualize [OPTIONS]
```

**Available options:**
- `--port`: Port to run the visualizer server on (default: 3000)
- `--experiments-dir`: Directory containing experiment JSON files (default: ./output/experiments)
- `--evaluations-dir`: Directory containing evaluation JSON files (default: ./output/evaluations)
- `--workspace`: Base workspace directory (default: current directory)
- `--no-browser`: Don't automatically open browser after starting server
- `--host`: Host address for the visualizer server (default: localhost)

**Examples:**
```bash
# Launch visualizer with default settings
gaia visualize

# Launch with custom data directories
gaia visualize --experiments-dir ./my_experiments --evaluations-dir ./my_evaluations

# Launch on custom port without opening browser
gaia visualize --port 8080 --no-browser

# Launch with specific workspace directory
gaia visualize --workspace ./output/evaluations_workspace
```

**Features:**
- **Interactive Comparison**: Side-by-side comparison of multiple experiment results
- **Key Metrics Dashboard**: View costs, token usage, quality scores, and performance metrics
- **Quality Analysis**: Detailed breakdown of evaluation criteria and ratings
- **Real-time Updates**: Automatic discovery of new files in data directories
- **Responsive Design**: Works on desktop and mobile devices

**Requirements**: Node.js must be installed on your system. The command will automatically install webapp dependencies on first run.

**Workflow Integration:**
```bash
# Complete evaluation workflow with visualization
gaia batch-experiment -c config.json -i ./data -o ./output/experiments
gaia eval -d ./output/experiments -o ./output/evaluations
gaia visualize --experiments-dir ./output/experiments --evaluations-dir ./output/evaluations
```

## Test Commands

Run various tests for development and troubleshooting:

```bash
gaia test --test-type TYPE [OPTIONS]
```

### Text-to-Speech (TTS) Tests

**Test types:**
- `tts-preprocessing`: Test TTS text preprocessing
- `tts-streaming`: Test TTS streaming playback
- `tts-audio-file`: Test TTS audio file generation

**TTS options:**
- `--test-text`: Text to use for TTS tests
- `--output-audio-file`: Output file path for TTS audio file test (default: output.wav)

**Examples:**
```bash
# Test TTS preprocessing with custom text
gaia test --test-type tts-preprocessing --test-text "Hello, world!"

# Test TTS streaming
gaia test --test-type tts-streaming --test-text "Testing streaming playback"

# Generate audio file
gaia test --test-type tts-audio-file --test-text "Save this as audio" --output-audio-file speech.wav
```

### Automatic Speech Recognition (ASR) Tests

**Test types:**
- `asr-file-transcription`: Test ASR file transcription
- `asr-microphone`: Test ASR microphone input
- `asr-list-audio-devices`: List available audio input devices

**ASR options:**
- `--input-audio-file`: Input audio file path for file transcription test
- `--recording-duration`: Recording duration in seconds for microphone test (default: 10)
- `--audio-device-index`: Index of audio input device (default: 1)
- `--whisper-model-size`: Whisper model size [tiny, base, small, medium, large] (default: base)

**Examples:**
```bash
# Test file transcription
gaia test --test-type asr-file-transcription --input-audio-file ./data/audio/test.m4a

# Test microphone for 30 seconds
gaia test --test-type asr-microphone --recording-duration 30

# List audio devices
gaia test --test-type asr-list-audio-devices
```

## YouTube Utilities

Download transcripts from YouTube videos:

```bash
gaia youtube --download-transcript URL [--output-path PATH]
```

**Available options:**
- `--download-transcript`: YouTube URL to download transcript from
- `--output-path`: Output file path for transcript (optional, defaults to transcript_<video_id>.txt)

**Example:**
```bash
# Download YouTube transcript
gaia youtube --download-transcript "https://youtube.com/watch?v=..." --output-path transcript.txt
```

## Kill Command

Terminate processes running on specific ports:

```bash
gaia kill --port PORT_NUMBER
```

**Required options:**
- `--port`: Port number to kill process on

**Examples:**
```bash
# Kill process running on port 8000
gaia kill --port 8000

# Kill process running on port 8001
gaia kill --port 8001
```

This is useful for cleaning up lingering server processes. The command will:
- Find the process ID (PID) of any process bound to the specified port
- Forcefully terminate that process
- Provide feedback about the operation's success or failure

## Development Setup

For manual setup including creation of the virtual environment and installation of dependencies, refer to the instructions outlined [here](./dev.md). This approach is not recommended for most users and is only needed for development purposes.

## Troubleshooting

### Common Issues

**Connection Errors:**
If you get connection errors with any command, ensure the Lemonade server is running:
```bash
lemonade-server serve
```

**Model Issues:**
- Make sure you have sufficient RAM (16GB+ recommended)
- Check that your model files are properly downloaded
- Verify your Hugging Face token if prompted
- To install additional models, see [Installing Additional Models](./features.md#installing-additional-models)

**Audio Issues:**
- Use `gaia test --test-type asr-list-audio-devices` to check available devices
- Verify your microphone permissions in Windows settings
- Try different audio device indices if the default doesn't work

**Performance:**
- For optimal NPU performance, disable discrete GPUs in Device Manager
- Ensure NPU drivers are up to date
- Monitor system resources during model execution

For general troubleshooting, refer to the [Development Guide](./dev.md#troubleshooting) and [FAQ](./faq.md).

## License

Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT