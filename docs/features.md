# Features

Currently, the following capabilities are available, more will be added in the near future:

## Platform Support Overview

- **Windows 11**: Full GUI and CLI support with all features
- **Linux (Ubuntu/Debian)**: Full GUI and CLI support with all features
- **macOS**: Not supported

| Use-Case Example   | Function                                 | Description                                                     | Platform Support |
| ------------------ | ---------------------------------------- | --------------------------------------------------------------- | ---------------- |
| LLM Direct         | Direct LLM queries via CLI               | Direct model interaction using the new `gaia llm` command      | Windows, Linux   |
| Chat Agent         | Interactive conversations with history   | Interactive chat sessions with conversation context and commands | Windows, Linux   |
| Blender Agent      | 3D content creation and manipulation     | Specialized agent for Blender automation and workflow          | Windows, Linux   |
| Summarization      | Document and transcript summarization    | AI-powered summarization with multiple output formats          | Windows, Linux   |
| Evaluation Suite   | Model evaluation and benchmarking       | Comprehensive evaluation framework with groundtruth generation  | Windows, Linux   |
| Voice Interaction  | Speech-to-speech conversation           | Voice-based AI interaction with TTS and ASR                    | Windows, Linux   |
| Web Dashboards     | Browser-based interfaces for agents      | JavaScript apps with simple configuration                       | Windows, Linux   |

## LLM Direct Usage

The `gaia llm` command provides direct access to language models without requiring server setup. This is the simplest way to interact with AI models:

```bash
# Basic query
gaia llm "What is 1+1?"

# Specify model and token limit
gaia llm "Explain quantum computing" --model Llama-3.2-3B-Instruct-Hybrid --max-tokens 200

# Disable streaming for batch processing
gaia llm "Write a short poem" --no-stream
```

**Requirements**: Requires lemonade-server to be running. Download and install from [lemonade-server.ai](https://lemonade-server.ai/). The command will provide helpful error messages if the server is not accessible.

**Platform Availability**: Windows and Linux

## Chat Agent

The Chat agent provides an interactive conversational interface with conversation history and various utility commands:

```bash
# Start interactive chat session
gaia chat

# Send a single message
gaia chat "What is machine learning?"

# Use a specific model
gaia chat --model Llama-3.2-1B-Instruct-Hybrid

# Set custom system prompt
gaia chat --system-prompt "You are a helpful coding assistant"

# Use custom assistant name
gaia chat --assistant-name "Gaia"
```

**Key features:**
- **Conversation History**: Maintains context across messages with configurable history length
- **Assistant Naming**: Customize the assistant's name for personalized interactions
- **Interactive Commands**: Built-in commands for session management and debugging
- **Streaming Responses**: Real-time response streaming for better user experience
- **Model Flexibility**: Support for different LLM models with automatic prompt formatting
- **Single Message Mode**: Non-interactive mode for scripting and automation

**Interactive Commands:**
- `/clear` - Clear conversation history
- `/history` - Show conversation history
- `/system` - Show current system prompt configuration
- `/model` - Show current model information
- `/prompt` - Show complete formatted prompt sent to LLM
- `/stats` - Show performance statistics (tokens/sec, latency, etc.)
- `/help` - Show available commands
- `quit`, `exit`, or `bye` - End the chat session

**Requirements:** Requires lemonade-server to be running. The chat agent defaults to Llama-3.2-3B-Instruct-Hybrid model for optimal performance.

**Platform Availability**: Windows and Linux

## Blender Agent

The Blender agent provides specialized functionality for 3D content creation and workflow automation. It is now fully integrated into the CLI for easy access:

```bash
# Run all Blender examples
gaia blender

# Interactive 3D scene creation
gaia blender --interactive

# Create specific objects with custom queries
gaia blender --query "Create a red cube and blue sphere"

# Run a specific example
gaia blender --example 2
```

**Key capabilities:**
- **Scene Management**: Clear scenes and get scene information
- **Object Creation**: Create cubes, spheres, cylinders, cones, and torus objects
- **Material Assignment**: Set RGBA colors and materials for objects
- **Object Modification**: Modify position, rotation, and scale of existing objects
- **Interactive Planning**: Multi-step scene creation with automatic planning

**Requirements:** In addition to the Lemonade server, the Blender agent requires a Blender MCP server to be running. See the [CLI documentation](./cli.md#blender-command) for complete setup instructions.

**Platform Availability**: Windows and Linux

## Summarization

The Summarization feature provides AI-powered document and transcript processing with multiple output formats:

```bash
# Summarize a meeting transcript
gaia summarize -i meeting_transcript.txt -o summary.json

# Summarize emails with PDF output
gaia summarize -i emails_directory/ -f pdf -o summaries/

# Generate specific summary styles
gaia summarize -i document.txt --styles executive action_items --max-tokens 1024

# List available configuration templates
gaia summarize --list-configs
```

**Key features:**
- **Multiple Input Types**: Process transcripts, emails, and documents
- **Flexible Output Formats**: JSON, PDF, email, or both
- **Configurable Styles**: Executive summaries, detailed summaries, action items, key decisions, participants, and topics
- **Batch Processing**: Process entire directories of documents
- **Template Support**: Use predefined configuration templates
- **Model Flexibility**: Support for both local (Lemonade) and cloud (OpenAI/Claude) models

**Output formats:**
- `json` - Structured JSON with all summary components
- `pdf` - Professional PDF reports
- `email` - Email-ready format with recipients
- `both` - Generate both JSON and PDF

**Summary styles:**
- `executive` - High-level executive summary
- `detailed` - Comprehensive detailed summary
- `action_items` - Extracted action items
- `key_decisions` - Important decisions made
- `participants` - Meeting participants and roles
- `topics` - Main topics discussed

**Platform Availability**: Windows and Linux

## Evaluation Framework

GAIA includes a comprehensive evaluation framework for testing and comparing AI model performance:

```bash
# Generate synthetic test data
gaia generate --meeting-transcript -o ./test_data --meeting-types standup --count-per-type 2

# Create evaluation standards (ground truth)
gaia groundtruth -d ./test_data --use-case summarization -o ./groundtruth

# Run batch experiments
gaia batch-experiment -c config.json -i ./groundtruth -o ./experiments

# Evaluate results
gaia eval -d ./experiments -o ./evaluation

# Generate reports
gaia report -d ./evaluation -o ./reports/report.md

# Launch interactive visualizer
gaia visualize --experiments-dir ./experiments --evaluations-dir ./evaluation
```

**Key capabilities:**
- **Synthetic Data Generation**: Create realistic test scenarios
- **Ground Truth Creation**: Generate evaluation standards
- **Batch Experimentation**: Test multiple models systematically
- **Automated Evaluation**: Score and compare model outputs
- **Interactive Visualization**: Web-based results explorer
- **Cost Tracking**: Monitor API costs and token usage
- **Performance Metrics**: Detailed timing and quality analysis

**Platform Availability**: Windows and Linux

## Voice Interaction (Talk)

The Talk feature enables voice-based conversations with AI models:

```bash
# Start voice conversation
gaia talk

# Use specific model with voice
gaia talk --model Llama-3.2-3B-Instruct-Hybrid

# Disable text-to-speech (ASR only)
gaia talk --no-tts

# Configure audio settings
gaia talk --audio-device-index 1 --whisper-model-size medium
```

**Key features:**
- **Speech Recognition**: Whisper ASR for voice input
- **Text-to-Speech**: Kokoro TTS for natural voice output
- **Real-time Processing**: Streaming audio pipeline
- **Device Selection**: Configure audio input devices
- **Model Flexibility**: Choose ASR model sizes

**Platform Availability**: Windows and Linux

## Desktop Applications

GAIA provides desktop applications with graphical interfaces for interacting with AI capabilities. These applications are built with Electron and provide native desktop experiences.

For available applications and setup instructions, see the [Apps Documentation](./apps.md).

**Platform Availability**: Windows and Linux

## Supported LLMs

The following is a list of the currently supported LLMs with GAIA using Ryzen AI Hybrid (NPU+iGPU) mode using `gaia-windows-setup.exe`. To request support for a new LLM, please contact the [AMD GAIA team](mailto:gaia@amd.com).

| LLM                    | Checkpoint                                                            | Backend            | Data Type |
| -----------------------|-----------------------------------------------------------------------|--------------------|-----------|
| Phi-3.5 Mini Instruct  | amd/Phi-3.5-mini-instruct-awq-g128-int4-asym-fp16-onnx-hybrid         | oga                | int4      |
| Phi-3 Mini Instruct    | amd/Phi-3-mini-4k-instruct-awq-g128-int4-asym-fp16-onnx-hybrid        | oga                | int4      |
| Llama-2 7B Chat        | amd/Llama-2-7b-chat-hf-awq-g128-int4-asym-fp16-onnx-hybrid            | oga                | int4      |
| Llama-3.2 1B Instruct  | amd/Llama-3.2-1B-Instruct-awq-g128-int4-asym-fp16-onnx-hybrid         | oga                | int4      |
| Llama-3.2 3B Instruct  | amd/Llama-3.2-3B-Instruct-awq-g128-int4-asym-fp16-onnx-hybrid         | oga                | int4      |
| Qwen 1.5 7B Chat       | amd/Qwen1.5-7B-Chat-awq-g128-int4-asym-fp16-onnx-hybrid               | oga                | int4      |
| Mistral 7B Instruct    | amd/Mistral-7B-Instruct-v0.3-awq-g128-int4-asym-fp16-onnx-hybrid      | oga                | int4      |

The following is a list of the currently supported LLMs in the generic version of GAIA (GAIA_Installer.exe). To request support for a new LLM, please contact the [AMD GAIA team](mailto:gaia@amd.com).
| LLM                    | Checkpoint                                                            | Device   | Backend            | Data Type |
| -----------------------|-----------------------------------------------------------------------|----------|--------------------|-----------|

* oga - [Onnx Runtime GenAI](https://github.com/microsoft/onnxruntime-genai)

# License

[MIT License](../LICENSE.md)

Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT