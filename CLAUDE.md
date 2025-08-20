# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GAIA (Generative AI Is Awesome) is AMD's open-source framework for running generative AI applications locally on AMD hardware, with specialized optimizations for Ryzen AI processors with NPU support.

### GAIA + RAUX Integration

GAIA works in conjunction with RAUX, an Electron-based desktop application that provides:
- Enhanced user interface and experience layer
- Installation management and progress tracking
- Inter-process communication (IPC) for status updates
- Unified "GAIA BETA" branding across the platform

RAUX serves as the frontend application layer while GAIA provides the core AI framework and backend services. The integration uses:
- NSIS installer coordination between both systems
- IPC channels for real-time installation and runtime status
- Shared environment configuration and Python execution management

## Development Commands

### Setup and Installation
```bash
# Install in development mode with all extras
pip install -e .[talk,dev]

# Create conda environment (recommended)
conda create -n gaiaenv python=3.10 -y
conda activate gaiaenv
```

### Testing
```bash
# Run all tests
pytest tests/

# Run specific test file
pytest tests/test_gaia.py

# Run unit tests only
pytest tests/unit/

# Run with hybrid configuration
pytest --hybrid
```

### Linting and Formatting
```bash
# Format code (Black is configured in pyproject.toml)
black src/ tests/

# Run linting via PowerShell script
powershell util/lint.ps1

# Run specific linting tools
powershell util/lint.ps1 -RunBlack
powershell util/lint.ps1 -RunPylint
```

### Running the Application
```bash
# CLI interface
gaia

# Direct LLM queries (fastest, no server setup required)
gaia llm "What is artificial intelligence?"

# Interactive chat
gaia chat

# Voice interaction
gaia talk

# Blender agent for 3D tasks
gaia blender
```

## Architecture

### Core Components

1. **Agent System** (`src/gaia/agents/`): WebSocket-based agents with specialized capabilities
   - Base `Agent` class (`src/gaia/agents/base/agent.py`) handles communication protocol, tool execution, and conversation management
   - State management: PLANNING → EXECUTING_PLAN → COMPLETION with error recovery
   - Tool registry system for domain-specific functionality
   - Current agents: Llm (direct LLM queries), Blender (3D content creation)

2. **LLM Backend Layer** (`src/gaia/llm/`): Multiple backend support
   - `lemonade_client.py`: AMD-optimized ONNX Runtime GenAI backend via Lemonade Server
   - Uses Lemonade Server for running LLM models with hardware optimization
   - OpenAI-compatible API with streaming support
   - Automatic server management and health checking

3. **Evaluation Framework** (`src/gaia/eval/`): Comprehensive testing and evaluation
   - Ground truth generation with Claude AI integration
   - Batch experiment execution with multiple models
   - Transcript analysis and summarization evaluation
   - Performance metrics and statistical analysis

4. **Audio Pipeline** (`src/gaia/audio/`): Complete audio processing
   - Whisper ASR for speech recognition
   - Kokoro TTS for text-to-speech
   - Audio recording and playback capabilities

5. **MCP Integration** (`src/gaia/mcp/`): Model Context Protocol support
   - Blender MCP server for 3D modeling integration
   - Client-server communication for external tool integration

### Key Architecture Patterns

- **Agent Pattern**: All domain-specific functionality implemented as agents inheriting from base `Agent` class
- **Tool Registry**: Dynamic tool registration system allowing agents to expose domain-specific capabilities
- **Streaming Support**: Real-time response streaming throughout the system
- **Server Management**: Automatic startup, health checking, and cleanup of backend servers
- **Error Recovery**: Built-in error handling and recovery mechanisms in agent conversations

### Backend Architecture

GAIA uses Lemonade Server as the LLM backend, which provides hardware-optimized model execution on available AMD hardware including NPU and iGPU on supported Ryzen AI systems.

### Testing Architecture

Tests are organized by component:
- `tests/unit/`: Unit tests for individual modules (ASR, TTS, LLM)
- `tests/test_*.py`: Integration tests
- `conftest.py`: Shared test fixtures with `--hybrid` configuration support
- Agent-specific tests in `src/gaia/agents/*/tests/`

When adding new agents, follow the pattern in existing agents with separate `app.py` and agent implementation files.