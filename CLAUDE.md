# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GAIA (Generative AI Is Awesome) is AMD's open-source framework for running generative AI applications locally on AMD hardware, with specialized optimizations for Ryzen AI processors with NPU support.

**Key Documentation:**
- Development setup: [`docs/dev.md`](docs/dev.md)
- Code Agent: [`docs/code.md`](docs/code.md)
- All features: [`docs/features.md`](docs/features.md)

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

**Feature documentation:** [`docs/cli.md`](docs/cli.md), [`docs/chat.md`](docs/chat.md), [`docs/code.md`](docs/code.md), [`docs/talk.md`](docs/talk.md), [`docs/blender.md`](docs/blender.md), [`docs/jira.md`](docs/jira.md), [`docs/mcp.md`](docs/mcp.md)

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

## Documentation Index

**User:** [`docs/cli.md`](docs/cli.md), [`docs/chat.md`](docs/chat.md), [`docs/talk.md`](docs/talk.md), [`docs/code.md`](docs/code.md), [`docs/blender.md`](docs/blender.md), [`docs/jira.md`](docs/jira.md), [`docs/features.md`](docs/features.md), [`docs/faq.md`](docs/faq.md)

**Developer:** [`docs/dev.md`](docs/dev.md), [`docs/apps/dev.md`](docs/apps/dev.md), [`docs/mcp.md`](docs/mcp.md), [`docs/eval.md`](docs/eval.md), [`CONTRIBUTING.md`](CONTRIBUTING.md)

**Platform:** [`docs/installer.md`](docs/installer.md), [`docs/ui.md`](docs/ui.md)

## Issue Response Guidelines

When responding to GitHub issues and pull requests, follow these guidelines:

### Documentation Structure

The documentation is organized in [`docs/docs.json`](docs/docs.json) with the following structure:
- **User Guides** (`docs/guides/`): Feature-specific guides (chat, talk, code, blender, jira, docker, routing)
- **Playbooks** (`docs/playbooks/`): Step-by-step tutorials for building agents
- **SDK Reference** (`docs/sdk/`): Core concepts, SDKs, infrastructure, mixins, agents
- **Specifications** (`docs/spec/`): Technical specs for all components
- **Reference** (`docs/reference/`): CLI, API, features, FAQ, development
- **Deployment** (`docs/deployment/`): Packaging, installers, UI

### Response Protocol

1. **Check documentation first:** Always search `docs/` folder before suggesting solutions
   - See [`docs/docs.json`](docs/docs.json) for the complete documentation structure

2. **Check for duplicates:** Search existing issues/PRs to avoid redundant responses

3. **Reference specific files:** Use precise file references with line numbers when possible
   - Agent implementations: `src/gaia/agents/` (base.py, chat_agent.py, code_agent.py, jira_agent.py, blender_agent.py)
   - CLI commands: `src/gaia/cli.py`
   - MCP integration: `src/gaia/mcp/`
   - LLM backend: `src/gaia/llm/`
   - Audio processing: `src/gaia/audio/` (ASR, TTS)
   - RAG system: `src/gaia/rag/` (document Q&A, embeddings)
   - Evaluation: `src/gaia/eval/` (batch experiments, ground truth)
   - Applications: `src/gaia/apps/` (jira, llm, summarize)
   - Chat SDK: `src/gaia/chat/`

4. **Link to relevant documentation:**
   - **Getting Started:** [`docs/setup.md`](docs/setup.md), [`docs/quickstart.md`](docs/quickstart.md)
   - **User Guides:** [`docs/guides/chat.md`](docs/guides/chat.md), [`docs/guides/talk.md`](docs/guides/talk.md), [`docs/guides/code.md`](docs/guides/code.md), [`docs/guides/blender.md`](docs/guides/blender.md), [`docs/guides/jira.md`](docs/guides/jira.md)
   - **SDK Reference:** [`docs/sdk/core/agent-system.md`](docs/sdk/core/agent-system.md), [`docs/sdk/sdks/chat.md`](docs/sdk/sdks/chat.md), [`docs/sdk/sdks/rag.md`](docs/sdk/sdks/rag.md), [`docs/sdk/infrastructure/mcp.md`](docs/sdk/infrastructure/mcp.md)
   - **CLI Reference:** [`docs/reference/cli.md`](docs/reference/cli.md), [`docs/reference/features.md`](docs/reference/features.md)
   - **Development:** [`docs/reference/dev.md`](docs/reference/dev.md), [`docs/sdk/testing.md`](docs/sdk/testing.md), [`docs/sdk/best-practices.md`](docs/sdk/best-practices.md)
   - **FAQ & Help:** [`docs/reference/faq.md`](docs/reference/faq.md), [`docs/glossary.md`](docs/glossary.md)

5. **For bugs:**
   - Search `src/gaia/` for related code
   - Check `tests/` for related test cases that might reveal the issue or need updating
   - Reference [`docs/sdk/troubleshooting.md`](docs/sdk/troubleshooting.md)
   - Check security implications using [`docs/sdk/security.md`](docs/sdk/security.md)

6. **For feature requests:**
   - Check if similar functionality exists in `src/gaia/agents/` or `src/gaia/apps/`
   - Reference [`docs/sdk/examples.md`](docs/sdk/examples.md) and [`docs/sdk/advanced-patterns.md`](docs/sdk/advanced-patterns.md)
   - Suggest approaches following [`docs/sdk/best-practices.md`](docs/sdk/best-practices.md)

7. **Follow contribution guidelines:**
   - Reference [`CONTRIBUTING.md`](CONTRIBUTING.md) for code standards
   - Ensure AMD copyright headers on new files
   - Point to [`docs/reference/dev.md`](docs/reference/dev.md) for development workflow

## File Path Rules (Workaround for Claude Code v1.0.111 Bug)
- When reading or editing a file, **ALWAYS use relative paths.**
- Example: `./src/components/Component.tsx` ✅
- **DO NOT use absolute paths.**
- Example: `C:/Users/user/project/src/components/Component.tsx` ❌
- Reason: This is a workaround for a known bug in Claude Code v1.0.111 (GitHub Issue
- when you invoke a particular proactive agent from @.claude\agents\, make sure to indicate what agent you are invoking in your response back to the user