# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GAIA (Generative AI Is Awesome) is AMD's open-source framework for running generative AI applications locally on AMD hardware, with specialized optimizations for Ryzen AI processors with NPU support.

**Key Documentation:**
- External site: https://amd-gaia.ai
- Development setup: [`docs/dev.md`](docs/dev.md) (local MDX format)
- SDK Reference: https://amd-gaia.ai/sdk
- Guides: https://amd-gaia.ai/guides

## File Headers

**IMPORTANT: All new files created in this project MUST start with the following copyright header (using appropriate comment syntax for the file type):**

```
Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
```

## Version Control Guidelines

### Repository Structure

This is the **gaia-pirate** repository (`amd/gaia`), the private repository where all GAIA development occurs. There are two related repositories:

1. **gaia-pirate** (`amd/gaia`) - **This repository** - Private development repository and single source of truth
2. **gaia-public** (`github.com/amd/gaia`) - Public open-source repository

**Development Workflow:**
- All development work happens in this private repository (gaia-pirate)
- **Claude will NEVER commit to the public repository** - releases are synced manually using `release.py`
- The `release.py` script filters out internal/NDA content based on an exclude list
- Files in the `./nda` directory are automatically excluded from public releases
- External contributions (issues/PRs) come through the public repository
- External contributions are manually reviewed and merged back into this private repository
- Legal review happens during the manual release process before PRs are completed in the public repo

See [`nda/docs/release.md`](nda/docs/release.md) for detailed release process documentation.

### IMPORTANT: Never Commit Changes
**NEVER commit changes to the repository unless explicitly requested by the user.** The user will decide when and what to commit. This prevents unwanted changes from being added to the repository history.

### Branch Management
- Main branch: `main`
- Feature branches: Use descriptive names (e.g., `kalin/mcp`, `feature/new-agent`)
- Always check current branch status before making changes
- Use pull requests for merging changes to main

## Testing Philosophy

**IMPORTANT:** Always test the actual CLI commands that users will run. Never bypass the CLI by calling Python modules directly unless debugging.

```bash
# Good - test CLI commands
gaia mcp start --background
gaia mcp status

# Bad - avoid unless debugging
python -m gaia.mcp.mcp_bridge
```

## Development Workflow

**See [`docs/dev.md`](docs/dev.md)** for complete setup (using uv for fast installs), testing, and linting instructions.

**Feature documentation:** All documentation is in MDX format in `docs/` directory. See external site https://amd-gaia.ai for rendered version.

## Common Development Commands

### Setup
```bash
uv venv && uv pip install -e ".[dev]"
```

### Linting (run before commits)
```bash
python util/lint.py --all --fix    # Auto-fix formatting
python util/lint.py --black        # Just black
python util/lint.py --isort        # Just imports
```

### Testing
```bash
python -m pytest tests/unit/       # Unit tests only
python -m pytest tests/ -xvs       # All tests, verbose
python -m pytest tests/ --hybrid   # Cloud + local testing
```

### Running GAIA
```bash
lemonade-server serve              # Start LLM backend
gaia llm "Hello"                   # Test LLM
gaia chat                          # Interactive chat
gaia-code                          # Code agent
```

## Project Structure

```
gaia/
├── src/gaia/           # Main source code
│   ├── agents/         # Agent implementations (base, blender, code, jira)
│   ├── apps/           # Standalone applications (jira, llm, summarize)
│   ├── audio/          # Audio processing (ASR/TTS)
│   ├── chat/           # Chat interface and SDK
│   ├── eval/           # Evaluation framework
│   ├── llm/            # LLM backend clients
│   ├── mcp/            # Model Context Protocol
│   ├── rag/            # Document retrieval (RAG)
│   ├── talk/           # Voice interaction
│   └── cli.py          # Main CLI entry point
├── tests/              # Test suite (unit/, mcp/, integration tests)
├── docs/               # Documentation (see docs/README.md)
├── installer/          # NSIS installer scripts
├── workshop/           # Tutorial materials
└── .github/workflows/  # CI/CD pipelines
```

## Architecture

**See [`docs/dev.md`](docs/dev.md)** for detailed architecture documentation.

### Key Components
- **Agent System** (`src/gaia/agents/`): Base Agent class with tool registry, state management, error recovery
- **LLM Backend** (`src/gaia/llm/`): Lemonade Server integration for AMD-optimized inference
- **MCP Integration** (`src/gaia/mcp/`): Model Context Protocol for external integrations
- **RAG System** (`src/gaia/rag/`): Document Q&A with PDF support - see [`docs/chat.md`](docs/chat.md)
- **Evaluation** (`src/gaia/eval/`): Batch experiments and ground truth - see [`docs/eval.md`](docs/eval.md)

### Default Models
- General tasks: `Qwen2.5-0.5B-Instruct-CPU`
- Code/Jira: `Qwen3-Coder-30B-A3B-Instruct-GGUF`

## Documentation

**External Site:** https://amd-gaia.ai
- [Quickstart](https://amd-gaia.ai/quickstart) - Build your first agent in 10 minutes
- [SDK Reference](https://amd-gaia.ai/sdk) - Complete API documentation
- [Guides](https://amd-gaia.ai/guides) - Chat, Code, Talk, Blender, Jira, and more
- [FAQ](https://amd-gaia.ai/reference/faq) - Frequently asked questions

**Local Documentation** (Mintlify MDX format in `docs/`):
- **SDK**: `docs/sdk/` - Agent system, tools, core SDKs (chat, llm, rag, vlm, audio)
- **Guides**: `docs/guides/` - Feature guides for chat, code, talk, blender, jira, docker, etc.
- **Specs**: `docs/spec/` - 47 technical specifications for all components
- **Playbooks**: `docs/playbooks/` - Step-by-step tutorials (chat-agent, code-agent, emr-agent, etc.)
- **Reference**: `docs/reference/` - CLI reference, API reference, dev guide, troubleshooting
- **Integrations**: `docs/integrations/` - MCP, n8n, VSCode
- **Deployment**: `docs/deployment/` - Installer, UI, Electron testing

## File Path Rules
- When reading or editing files, **ALWAYS use relative paths** starting with `./`
- Example: `./src/components/Component.tsx` ✅
- **DO NOT use absolute paths**
- Example: `C:/Users/user/project/src/components/Component.tsx` ❌

## Claude Agents
Specialized agents are available in `.claude/agents/` for specific tasks:
- **Agent Development**: gaia-agent-builder (opus) - Creating new GAIA agents, tool registration, state management
- **SDK Architecture**: sdk-architect (opus) - API design, pattern consistency, breaking changes
- **Hardware Optimization**: hardware-optimizer (opus) - NPU/iGPU tuning, Ryzen AI performance
- **Python**: python-developer (sonnet) - Python code, refactoring, design patterns
- **TypeScript**: typescript-developer (sonnet) - TypeScript, Electron apps, type definitions
- **Testing**: test-engineer (sonnet) - pytest, CLI testing, AMD hardware validation
- **Frontend**: frontend-developer (sonnet) - Electron apps, web UIs
- **Architecture Review**: architecture-reviewer (opus) - SOLID principles, dependency analysis
- **Documentation**: api-documenter (sonnet) - OpenAPI specs, MCP schemas
- **MCP Development**: mcp-developer (sonnet) - MCP server creation
- And many more - see `.claude/agents/` directory

When invoking a proactive agent from `.claude/agents/`, indicate which agent you are using in your response.