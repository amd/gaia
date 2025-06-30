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
pip install -e .[hybrid,joker,clip,talk,dev]

# Set environment mode (required before running)
# GAIA automatically configures optimal settings
```

### Testing
```bash
# Run all tests
pytest tests/

# Run specific test file
pytest tests/test_gaia.py

# Run unit tests only
pytest tests/unit/
```

### Linting and Formatting
```bash
# Format code (Black is configured in pyproject.toml)
black src/ tests/

# Run linting via PowerShell script
powershell util/lint.ps1
```

### Running the Application
```bash
# CLI interface
gaia-cli

# GUI interface
python -m gaia.interface.widget
```

## Architecture

### Core Components

1. **Agent System** (`src/gaia/agents/`): WebSocket-based agents with specialized capabilities
   - Base `Agent` class handles communication protocol
   - Specialized agents: Chaty (chat), Rag (retrieval), Clip (vision), Joker (humor), Llm (direct LLM)

2. **LLM Backend Layer** (`src/gaia/llm/`): Multiple backend support
   - `lemonade_server.py`: AMD-optimized ONNX Runtime GenAI backend
   - `llama_index_local.py`: RAG capabilities via LlamaIndex

3. **Interface Layer**: Dual interface support
   - CLI tool
   - OpenWebUI (forked) GUI

4. **Audio Pipeline** (`src/gaia/audio/`): Complete audio processing
   - Whisper ASR, Kokoro TTS, audio recording

### Key Environment Variables
- Configuration is automatically optimized for Ryzen AI hardware when available

### Testing Architecture

Tests are organized by component:
- `tests/unit/`: Unit tests for individual modules
- `tests/test_*.py`: Integration tests
- `conftest.py`: Shared test fixtures

When adding new agents, follow the pattern in existing agents with separate `app.py` and `prompts.py` files.