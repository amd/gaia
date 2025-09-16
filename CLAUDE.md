# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GAIA (Generative AI Is Awesome) is AMD's open-source framework for running generative AI applications locally on AMD hardware, with specialized optimizations for Ryzen AI processors with NPU support.

## Documentation

Comprehensive documentation is available in the `docs/` directory:

### User Guides
- [`docs/cli.md`](docs/cli.md) - Command Line Interface guide and usage examples
- [`docs/features.md`](docs/features.md) - Complete feature overview and platform support matrix
- [`docs/chat.md`](docs/chat.md) - Interactive chat agent documentation
- [`docs/talk.md`](docs/talk.md) - Voice interaction and speech-to-speech features
- [`docs/blender.md`](docs/blender.md) - Blender 3D agent for content creation
- [`docs/jira.md`](docs/jira.md) - Jira agent for issue management with natural language interface
- [`docs/ui.md`](docs/ui.md) - Web UI documentation and deployment guide

### Developer Resources
- [`docs/dev.md`](docs/dev.md) - Development environment setup and contribution guidelines
- [`docs/eval.md`](docs/eval.md) - Evaluation framework for model testing and benchmarking
- [`docs/mcp-bridge.md`](docs/mcp-bridge.md) - MCP (Model Context Protocol) bridge for external integrations

### Installation & Setup
- [`docs/installer.md`](docs/installer.md) - NSIS installer documentation and customization
- [`docs/faq.md`](docs/faq.md) - Frequently asked questions and troubleshooting

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

## Version Control Guidelines

### IMPORTANT: Never Commit Changes
**NEVER commit changes to the repository unless explicitly requested by the user.** The user will decide when and what to commit. This prevents unwanted changes from being added to the repository history.

### Branch Management
- Main branch: `main`
- Feature branches: Use descriptive names (e.g., `kalin/mcp`, `feature/new-agent`)
- Always check current branch status before making changes
- Use pull requests for merging changes to main

## Testing Philosophy

### IMPORTANT: Always Test the CLI
When writing tests or CI/CD workflows, **ALWAYS test the actual CLI commands** that users will run. Never bypass the CLI by calling Python modules directly unless absolutely necessary for debugging.

**When testing GAIA features during development:**
- Always use the `gaia` CLI command, not Python module imports
- Test the exact commands that users will use in production
- This ensures the CLI interface, argument parsing, and module integration all work correctly

**Good:**
```bash
gaia mcp start --background
gaia mcp status
gaia mcp stop
```

**Bad (avoid unless debugging):**
```bash
python -m gaia.mcp.mcp_bridge
```

This ensures:
- CLI commands work as expected for end users
- Integration between CLI and modules is properly tested
- Command-line argument parsing is validated
- User experience matches test coverage

## Development Commands

### Setup and Installation
```bash
# Install in development mode with all extras
pip install -e .[audio,talk,dev,eval,youtube]

# Install specific components
pip install -e .[talk]     # Voice interaction
pip install -e .[eval]     # Evaluation framework
pip install -e .[youtube]  # YouTube transcript support
pip install -e .[blender]  # Blender 3D integration (requires Blender)

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

# Run Jira agent tests
python tests/test_jira.py
python tests/test_jira.py --interactive  # Interactive mode with test selection
python tests/test_jira.py --test test_basic_fetch_queries  # Run specific test
python tests/test_jira.py --show-prompts  # Display LLM prompts
python tests/test_jira.py --debug --show-prompts  # Full debug output
python run_jira_tests.py  # Comprehensive Jira test suite

# Run MCP tests
pytest tests/mcp/
python tests/mcp/test_mcp_simple.py
python tests/mcp/test_mcp_jira.py
python tests/mcp/test_mcp_http_validation.py
python validate_mcp.py  # Validate MCP protocol compliance

# Run other integration tests
pytest tests/test_chat_sdk.py
pytest tests/test_eval.py
pytest tests/test_summarizer.py
pytest tests/test_lemonade_client.py
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

# Jira agent for issue management (with automatic configuration discovery)
gaia jira
gaia jira "show my open issues"  # Direct query execution
gaia jira --interactive  # Interactive mode for multiple queries

# MCP bridge for external integrations
gaia mcp start
gaia mcp status
gaia mcp stop

# Summarization tool for documents and transcripts
gaia summarize -i document.txt --styles executive action_items
gaia summarize -i meeting_transcript.txt -o summary.json
gaia summarize --list-configs  # View available summarization configurations
```

## Project Structure

```
gaia/
├── src/gaia/           # Main source code
│   ├── agents/         # Agent implementations
│   │   ├── base/       # Base agent framework
│   │   │   ├── agent.py        # Base Agent class with state management
│   │   │   └── console.py      # Console-based agent interface
│   │   ├── blender/    # Blender 3D agent
│   │   │   ├── agent.py        # Blender agent implementation
│   │   │   └── app.py          # Blender agent entry point
│   │   └── jira/       # Jira integration agent
│   │       ├── agent.py        # Jira agent with NLP capabilities
│   │       └── app.py          # Jira agent entry point
│   ├── apps/           # Standalone applications
│   │   ├── llm/        # Direct LLM interface
│   │   │   └── app.py          # LLM query application
│   │   ├── jira/       # Jira app components
│   │   └── summarize/  # Document summarization
│   │       └── app.py          # Summarization application
│   ├── audio/          # Audio processing (ASR/TTS)
│   │   ├── asr.py              # Speech recognition (Whisper)
│   │   └── tts.py              # Text-to-speech (Kokoro)
│   ├── chat/           # Chat interface and SDK
│   │   ├── app.py              # Interactive chat application
│   │   ├── sdk.py              # Chat SDK with session management
│   │   └── prompts.py          # System prompts and templates
│   ├── eval/           # Evaluation framework
│   │   ├── groundtruth.py      # Ground truth generation
│   │   └── experiment.py       # Batch experiment runner
│   ├── llm/            # LLM backend clients
│   │   ├── llm_client.py       # Base LLM client interface
│   │   └── lemonade_client.py  # Lemonade server integration
│   ├── mcp/            # Model Context Protocol
│   │   ├── mcp_bridge.py       # HTTP-native MCP server
│   │   ├── mcp.json            # MCP configuration
│   │   ├── atlassian_mcp.py   # Atlassian services wrapper
│   │   └── blender_mcp_server.py # Blender MCP integration
│   ├── talk/           # Voice interaction
│   │   ├── app.py              # Voice interaction application
│   │   └── sdk.py              # Talk SDK for voice processing
│   ├── cli.py          # Main CLI entry point
│   ├── logger.py       # Logging configuration
│   ├── util.py         # Utility functions
│   └── version.py      # Version information
├── tests/              # Test suite
│   ├── unit/           # Unit tests
│   │   ├── test_asr.py         # ASR tests
│   │   ├── test_tts.py         # TTS tests
│   │   └── test_llm.py         # LLM client tests
│   ├── mcp/            # MCP integration tests
│   │   ├── test_mcp_simple.py  # Basic MCP tests
│   │   ├── test_mcp_http_validation.py # HTTP validation
│   │   ├── test_mcp_jira.py    # Jira MCP tests
│   │   └── test_mcp_integration.py # Integration tests
│   ├── test_chat_sdk.py        # Chat SDK tests
│   ├── test_eval.py            # Evaluation framework tests
│   ├── test_jira.py            # Comprehensive Jira tests
│   ├── test_lemonade_client.py # Lemonade client tests
│   ├── test_summarizer.py      # Summarizer tests
│   └── conftest.py             # Pytest configuration
├── docs/               # Documentation
│   ├── cli.md                  # CLI reference guide
│   ├── mcp.md                  # MCP server documentation
│   ├── jira.md                 # Jira agent guide
│   ├── blender.md              # Blender agent guide
│   ├── chat.md                 # Chat interface guide
│   ├── talk.md                 # Voice interaction guide
│   ├── eval.md                 # Evaluation framework guide
│   ├── dev.md                  # Development guide
│   ├── features.md             # Feature overview
│   └── faq.md                  # Troubleshooting guide
├── installer/          # NSIS installer scripts
│   └── installer.nsi           # Windows installer script
├── workshop/           # Tutorial materials
│   └── blender.ipynb           # Blender workshop notebook
├── util/               # Utility scripts
│   └── lint.ps1                # PowerShell linting script
├── .github/workflows/  # CI/CD pipelines
│   ├── lint.yml                # Code quality checks
│   ├── test_mcp.yml            # MCP test workflow
│   └── build_installer.yml     # Installer build workflow
├── setup.py            # Package setup configuration
├── pyproject.toml      # Project metadata
├── CLAUDE.md           # Claude Code guidance (this file)
├── README.md           # Project readme
├── CONTRIBUTING.md     # Contribution guidelines
├── LICENSE.md          # MIT license
├── .env.example        # Environment variables template
├── run_jira_tests.py   # Jira test runner
└── validate_mcp.py     # MCP protocol validator
```

## Architecture

### Core Components

1. **Agent System** (`src/gaia/agents/`): WebSocket-based agents with specialized capabilities
   - Base `Agent` class (`src/gaia/agents/base/agent.py`) handles communication protocol, tool execution, and conversation management
   - State management: PLANNING → EXECUTING_PLAN → COMPLETION with error recovery
   - Tool registry system for domain-specific functionality
   - Current agents: Llm (direct LLM queries), Blender (3D content creation), Jira (issue management with automatic configuration discovery)
   - Console-based agent interface (`src/gaia/agents/base/console.py`) for interactive sessions

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
   - Generic MCP bridge server for external client integration
   - Atlassian wrapper for Jira, Confluence, and Compass integration
   - Blender MCP server for 3D modeling integration
   - WebSocket-based communication following MCP specification
   - Support for tools, resources, prompts, and streaming responses
   - Background process management with status tracking

6. **Applications** (`src/gaia/apps/`): Standalone application modules
   - Document summarization with multiple output styles and formats
   - PDF formatting and HTML viewing capabilities
   - Configurable summarization templates and prompts

### Key Architecture Patterns

- **Agent Pattern**: All domain-specific functionality implemented as agents inheriting from base `Agent` class
- **Tool Registry**: Dynamic tool registration system allowing agents to expose domain-specific capabilities
- **Streaming Support**: Real-time response streaming throughout the system
- **Server Management**: Automatic startup, health checking, and cleanup of backend servers
- **Error Recovery**: Built-in error handling and recovery mechanisms in agent conversations
- **SDK Integration**: Chat and Talk SDKs (`sdk.py` files) provide programmatic interfaces
- **Configuration Management**: Environment-based configuration with `.env` file support
- **Cross-platform Support**: Windows and Linux compatibility with platform-specific optimizations

### Backend Architecture

GAIA uses Lemonade Server as the LLM backend, which provides hardware-optimized model execution on available AMD hardware including NPU and iGPU on supported Ryzen AI systems.

The system uses different models for different agents:
- **Default model**: Qwen2.5-0.5B-Instruct-CPU for general agent tasks
- **Coding tasks**: Qwen3-Coder-30B-A3B-Instruct-GGUF for code-related operations
- **Jira Agent**: Uses Qwen3-Coder-30B-A3B-Instruct-GGUF by default for reliable JSON parsing and complex operations

### Testing Architecture

Tests are organized by component:
- `tests/unit/`: Unit tests for individual modules (ASR, TTS, LLM)
- `tests/test_*.py`: Integration tests
- `tests/mcp/`: MCP-specific integration and validation tests
- `conftest.py`: Shared test fixtures with `--hybrid` configuration support
- Agent-specific tests in `src/gaia/agents/*/tests/`
- Test utilities: `run_jira_tests.py` for comprehensive Jira testing
- Validation scripts: `validate_mcp.py` for MCP protocol compliance

When adding new agents, follow the pattern in existing agents with separate `app.py` and agent implementation files.

### CI/CD Pipeline

GitHub Actions workflows are configured for:
- **Linting**: `lint.yml` - Code quality checks
- **Testing**: Multiple test workflows for different components
  - `test_gaia_cli.yml`, `test_gaia_cli_linux.yml`, `test_gaia_cli_windows.yml`
  - `test_mcp.yml` - MCP integration tests
  - `test_chat_sdk.yml` - Chat SDK tests
  - `test_eval.yml` - Evaluation framework tests
- **Building**: `build_installer.yml` - NSIS installer creation
- **Publishing**: `publish_installer.yml` - Release distribution
- **Hybrid Testing**: `local_hybrid_tests.yml` - Local and cloud model testing

## Quick Reference to Documentation

### Feature-Specific Documentation
- **LLM Direct Usage**: See [`docs/features.md`](docs/features.md#llm-direct-usage) and [`docs/cli.md`](docs/cli.md#gaia-cli-llm-demo)
- **Chat Interface**: See [`docs/chat.md`](docs/chat.md) for interactive conversation features
- **Voice Interaction**: See [`docs/talk.md`](docs/talk.md) for speech-to-speech capabilities
- **3D Content Creation**: See [`docs/blender.md`](docs/blender.md) for Blender automation
- **Jira Integration**: Natural language interface with automatic configuration discovery, LLM-driven JQL generation, and JSON API for webapp integration. See [`docs/jira.md`](docs/jira.md)
- **Document Summarization**: Multiple styles and output formats for document processing. See [`docs/features.md`](docs/features.md) for usage examples
- **Model Evaluation**: See [`docs/eval.md`](docs/eval.md) for testing and benchmarking
- **Web UI**: See [`docs/ui.md`](docs/ui.md) for browser-based interface
- **MCP Integration**: See [`docs/mcp-bridge.md`](docs/mcp-bridge.md) and [`docs/cli.md`](docs/cli.md#mcp-command) for external client integration

### Platform-Specific Guides
- **Windows Installation**: See [`docs/cli.md`](docs/cli.md#windows-installation) and [`docs/installer.md`](docs/installer.md)
- **Linux Installation**: See [`docs/cli.md`](docs/cli.md#linux-installation)
- **Platform Support Matrix**: See [`docs/features.md`](docs/features.md#platform-support-overview)

### Development Resources
- **Contributing Guidelines**: See [`docs/dev.md`](docs/dev.md) and [`CONTRIBUTING.md`](CONTRIBUTING.md)
- **Troubleshooting**: See [`docs/faq.md`](docs/faq.md)
- **Evaluation Framework**: See [`docs/eval.md`](docs/eval.md) for creating tests and benchmarks
- **Test Documentation**: See [`tests/TEST_JIRA_GUIDE.md`](tests/TEST_JIRA_GUIDE.md) for Jira testing guide
- **Workshop Materials**: Available in `workshop/` directory for tutorials and examples