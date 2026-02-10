# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GAIA (Generative AI Is Awesome) is AMD's open-source framework for running generative AI applications locally on AMD hardware, with specialized optimizations for Ryzen AI processors with NPU support.

**Key Documentation:**
- External site: https://amd-gaia.ai
- Development setup: [`docs/reference/dev.mdx`](docs/reference/dev.mdx)
- SDK Reference: https://amd-gaia.ai/sdk
- Guides: https://amd-gaia.ai/guides

## Version Control Guidelines

### Repository Structure

This is the GAIA repository (`amd/gaia`) on GitHub: https://github.com/amd/gaia

**Development Workflow:**
- All development work happens in this repository
- Use pull requests for all changes to main branch

### IMPORTANT: Never Commit Changes
**NEVER commit changes to the repository unless explicitly requested by the user.** The user will decide when and what to commit. This prevents unwanted changes from being added to the repository history.

### IMPORTANT: Always Review Your Changes
**After making any changes to files, you MUST review your work:**
1. Read back files you wrote or edited to verify correctness
2. Check for syntax errors, typos, and formatting issues
3. Verify code examples compile/run correctly
4. Ensure documentation links are valid
5. Confirm changes align with the original request
6. **For documentation:** Check both technical accuracy AND internal consistency:
   - Does the code match the SDK implementation? (technical accuracy)
   - Do code examples match their explanations? (internal consistency)
   - If example shows `return "text"`, explanation should describe returning text, not `return ""`

This self-review step is mandatory - never skip verification of your output.

### Branch Management
- Main branch: `main`
- Feature branches: Use descriptive names (e.g., `kalin/mcp`, `feature/new-agent`)
- Always check current branch status before making changes
- Use pull requests for merging changes to main

## Development Standards

### Documentation Requirements

**Every new feature must be documented.** Before completing any feature work:

1. **Update [`docs/docs.json`](docs/docs.json)** - Add new pages to the appropriate navigation section
2. **Create documentation in `.mdx` format** - All docs use MDX (Markdown + JSX for Mintlify)
3. **Follow the docs structure:**
   - User-facing features → `docs/guides/`
   - SDK/API features → `docs/sdk/`
   - Technical specs → `docs/spec/`
   - CLI commands → update `docs/reference/cli.mdx`

```bash
# Verify docs build locally before committing
# Check that new .mdx files are referenced in docs/docs.json
```

### Code Reuse and Base Classes

**Always extend existing base classes and reuse core functionality.** The `src/gaia/agents/base/` directory provides foundational components:

| File | Purpose | When to Use |
|------|---------|-------------|
| `agent.py` | Base `Agent` class | Inherit for all new agents |
| `mcp_agent.py` | `MCPAgent` mixin | Add MCP protocol support |
| `api_agent.py` | `ApiAgent` mixin | Add OpenAI-compatible API exposure |
| `tools.py` | `@tool` decorator, registry | Register all agent tools |
| `console.py` | `AgentConsole` | Standardized CLI output |
| `errors.py` | Error formatting | Consistent error handling |

**Before creating new functionality:**
1. Check if similar functionality exists in `src/gaia/agents/base/`
2. Check existing mixins in agent subdirectories (e.g., `chat/tools/`, `code/tools/`)
3. Extract shared logic into base classes or mixins when patterns repeat

### Testing Requirements

**Every new feature requires tests.** The testing structure:

```
tests/
├── unit/           # Isolated component tests (mocked dependencies)
├── mcp/            # MCP protocol integration tests
├── integration/    # Cross-system tests (real services)
└── [root]          # Feature tests (test_*.py)
```

**Required for new features:**

| Feature Type | Required Tests |
|--------------|----------------|
| SDK core (agents/base/) | Unit tests + integration tests |
| New tools (@tool decorated) | Unit tests with mocked LLM |
| CLI commands | CLI integration tests |
| API endpoints | API tests (see `test_api.py`) |
| Agent implementations | Agent tests with mocked/real LLM |

**Testing patterns** (see `tests/conftest.py` for shared fixtures):
```python
# Unit test with mocked LLM
@pytest.fixture
def mock_lemonade_client(mocker):
    return mocker.patch("gaia.llm.lemonade_client.LemonadeClient")

# Integration test (uses require_lemonade fixture from conftest.py)
def test_real_inference(require_lemonade, api_client):
    # Test skips automatically if Lemonade server not running
    response = api_client.post("/v1/chat/completions", json={...})
    ...
```

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

**See [`docs/reference/dev.mdx`](docs/reference/dev.mdx)** for complete setup (using uv for fast installs), testing, and linting instructions.

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
│   ├── agents/         # Agent implementations
│   │   ├── base/       # Base Agent class, MCPAgent, ApiAgent, tools
│   │   ├── chat/       # ChatAgent with RAG capabilities
│   │   ├── code/       # CodeAgent with orchestration, validators
│   │   ├── blender/    # BlenderAgent for 3D automation
│   │   ├── jira/       # JiraAgent for issue management
│   │   ├── docker/     # DockerAgent for containerization
│   │   ├── emr/        # MedicalIntakeAgent for healthcare
│   │   └── routing/    # RoutingAgent for intelligent agent selection
│   ├── api/            # OpenAI-compatible REST API server
│   ├── apps/           # Standalone applications (jira, llm, summarize, docker)
│   ├── audio/          # Audio processing (Whisper ASR, Kokoro TTS)
│   ├── chat/           # Chat SDK
│   ├── database/       # DatabaseMixin and DatabaseAgent
│   ├── electron/       # Electron app integration
│   ├── eval/           # Evaluation framework
│   ├── llm/            # LLM backend clients (Lemonade, Claude, OpenAI)
│   ├── mcp/            # Model Context Protocol servers/clients
│   ├── rag/            # Document retrieval (RAG)
│   ├── shell/          # Shell integration
│   ├── talk/           # Voice interaction SDK
│   ├── testing/        # Test utilities and fixtures
│   ├── utils/          # Utility modules (FileWatcher, parsing)
│   └── cli.py          # Main CLI entry point
├── tests/              # Test suite
│   ├── unit/           # Unit tests
│   ├── mcp/            # MCP integration tests
│   ├── integration/    # Cross-system integration tests
│   └── electron/       # Electron app tests (Jest)
├── docs/               # Documentation (MDX format)
├── workshop/           # Tutorial materials
└── .github/workflows/  # CI/CD pipelines
```

## Architecture

**See [`docs/reference/dev.mdx`](docs/reference/dev.mdx)** for detailed architecture documentation.

### Key Components
- **Agent System** (`src/gaia/agents/`): Base Agent class with tool registry, state management, error recovery
  - `base/agent.py` - Core Agent class
  - `base/mcp_agent.py` - MCP support mixin
  - `base/api_agent.py` - OpenAI API compatibility mixin
  - `base/tools.py` - Tool decorator and registry
- **LLM Backend** (`src/gaia/llm/`): Multi-provider support with AMD optimization
  - `lemonade_client.py` - Lemonade Server (AMD NPU/GPU)
  - `providers/claude.py` - Claude API
  - `providers/openai_provider.py` - OpenAI API
  - `factory.py` - Client factory for provider selection
- **API Server** (`src/gaia/api/`): OpenAI-compatible REST API for agent access
- **MCP Integration** (`src/gaia/mcp/`): Model Context Protocol for external integrations
- **RAG System** (`src/gaia/rag/`): Document Q&A with PDF support - see [`docs/guides/chat.mdx`](docs/guides/chat.mdx)
- **Evaluation** (`src/gaia/eval/`): Batch experiments and ground truth - see [`docs/reference/eval.mdx`](docs/reference/eval.mdx)

### Agent Implementations

| Agent | Location | Description | Default Model |
|-------|----------|-------------|---------------|
| **ChatAgent** | `agents/chat/agent.py` | Document Q&A with RAG | Qwen3-Coder-30B |
| **CodeAgent** | `agents/code/agent.py` | Code generation with orchestration | Qwen3-Coder-30B |
| **JiraAgent** | `agents/jira/agent.py` | Jira issue management | Qwen3-Coder-30B |
| **BlenderAgent** | `agents/blender/agent.py` | 3D scene automation | Qwen3-Coder-30B |
| **DockerAgent** | `agents/docker/agent.py` | Container management | Qwen3-Coder-30B |
| **MedicalIntakeAgent** | `agents/emr/agent.py` | Medical form processing | Qwen3-VL-4B (VLM) |
| **RoutingAgent** | `agents/routing/agent.py` | Intelligent agent selection | Qwen3-Coder-30B |

### Default Models
- General tasks: `Qwen3-0.6B-GGUF`
- Code/Agents: `Qwen3-Coder-30B-A3B-Instruct-GGUF`
- Vision tasks: `Qwen2.5-VL-7B-Instruct-GGU`

## CLI Commands

Primary commands available via `gaia`:
- `gaia chat` - Interactive chat with RAG
- `gaia talk` - Voice interaction
- `gaia prompt` - Single prompt to LLM
- `gaia llm` - Simple LLM queries
- `gaia blender` - Blender 3D agent
- `gaia jira` - Jira integration
- `gaia docker` - Docker management
- `gaia api` - OpenAI-compatible API server
- `gaia mcp` - MCP bridge server (start, stop, status, test)
- `gaia eval` - Evaluation framework
- `gaia summarize` - Document summarization
- `gaia cache` - Cache management (status, clear)

## Documentation Index

All documentation uses `.mdx` format (Markdown + JSX for Mintlify).

**User Guides:**
- [`docs/guides/chat.mdx`](docs/guides/chat.mdx) - Chat with RAG
- [`docs/guides/talk.mdx`](docs/guides/talk.mdx) - Voice interaction
- [`docs/guides/code.mdx`](docs/guides/code.mdx) - Code generation
- [`docs/guides/blender.mdx`](docs/guides/blender.mdx) - 3D automation
- [`docs/guides/jira.mdx`](docs/guides/jira.mdx) - Jira integration
- [`docs/guides/docker.mdx`](docs/guides/docker.mdx) - Docker management
- [`docs/guides/routing.mdx`](docs/guides/routing.mdx) - Agent routing
- [`docs/guides/emr.mdx`](docs/guides/emr.mdx) - Medical intake

**SDK Reference:**
- [`docs/sdk/core/agent-system.mdx`](docs/sdk/core/agent-system.mdx) - Agent framework
- [`docs/sdk/core/tools.mdx`](docs/sdk/core/tools.mdx) - Tool decorator
- [`docs/sdk/core/console.mdx`](docs/sdk/core/console.mdx) - Console output
- [`docs/sdk/sdks/chat.mdx`](docs/sdk/sdks/chat.mdx) - Chat SDK
- [`docs/sdk/sdks/rag.mdx`](docs/sdk/sdks/rag.mdx) - RAG SDK
- [`docs/sdk/sdks/llm.mdx`](docs/sdk/sdks/llm.mdx) - LLM clients
- [`docs/sdk/sdks/vlm.mdx`](docs/sdk/sdks/vlm.mdx) - Vision LLM clients
- [`docs/sdk/sdks/audio.mdx`](docs/sdk/sdks/audio.mdx) - Audio (ASR/TTS)
- [`docs/sdk/infrastructure/mcp.mdx`](docs/sdk/infrastructure/mcp.mdx) - MCP protocol
- [`docs/sdk/infrastructure/api-server.mdx`](docs/sdk/infrastructure/api-server.mdx) - API server

**Reference:**
- [`docs/reference/cli.mdx`](docs/reference/cli.mdx) - CLI reference
- [`docs/reference/dev.mdx`](docs/reference/dev.mdx) - Development guide
- [`docs/reference/faq.mdx`](docs/reference/faq.mdx) - FAQ
- [`docs/reference/troubleshooting.mdx`](docs/reference/troubleshooting.mdx) - Troubleshooting

**Deployment:**
- [`docs/deployment/ui.mdx`](docs/deployment/ui.mdx) - Electron UI

**Specifications:** See `docs/spec/` for 40+ technical specifications.

## Issue Response Guidelines

When responding to GitHub issues and pull requests, follow these guidelines:

### Documentation Structure

**External Site:** https://amd-gaia.ai
- [Quickstart](https://amd-gaia.ai/quickstart) - Build your first agent in 10 minutes
- [SDK Reference](https://amd-gaia.ai/sdk) - Complete API documentation
- [Guides](https://amd-gaia.ai/guides) - Chat, Code, Talk, Blender, Jira, and more
- [FAQ](https://amd-gaia.ai/reference/faq) - Frequently asked questions

The documentation is organized in [`docs/docs.json`](docs/docs.json) with the following structure:
- **SDK**: `docs/sdk/` - Agent system, tools, core SDKs (chat, llm, rag, vlm, audio)
- **User Guides** (`docs/guides/`): Feature-specific guides (chat, talk, code, blender, jira, docker, routing, emr)
- **Playbooks** (`docs/playbooks/`): Step-by-step tutorials for building agents
- **SDK Reference** (`docs/sdk/`): Core concepts, SDKs, infrastructure, mixins, agents
- **Specifications** (`docs/spec/`): Technical specs for all components
- **Reference** (`docs/reference/`): CLI, API, features, FAQ, development
- **Integrations**: `docs/integrations/` - MCP, n8n, VSCode
- **Deployment** (`docs/deployment/`): Packaging, UI

### Response Protocol

1. **Check documentation first:** Always search `docs/` folder before suggesting solutions
   - See [`docs/docs.json`](docs/docs.json) for the complete documentation structure

2. **Check for duplicates:** Search existing issues/PRs to avoid redundant responses

3. **Reference specific files:** Use precise file references with line numbers when possible
   - Agent implementations: `src/gaia/agents/` (base/, chat/, code/, blender/, jira/, docker/, emr/, routing/)
   - CLI commands: `src/gaia/cli.py`
   - MCP integration: `src/gaia/mcp/`
   - LLM backend: `src/gaia/llm/`
   - Audio processing: `src/gaia/audio/` (whisper_asr.py, kokoro_tts.py)
   - RAG system: `src/gaia/rag/` (sdk.py, pdf_utils.py)
   - Evaluation: `src/gaia/eval/` (eval.py, batch_experiment.py)
   - Applications: `src/gaia/apps/` (jira/, llm/, summarize/, docker/)
   - Chat SDK: `src/gaia/chat/`
   - API Server: `src/gaia/api/`

4. **Link to relevant documentation:**
   - **Getting Started:** [`docs/setup.mdx`](docs/setup.mdx), [`docs/quickstart.mdx`](docs/quickstart.mdx)
   - **User Guides:** [`docs/guides/chat.mdx`](docs/guides/chat.mdx), [`docs/guides/talk.mdx`](docs/guides/talk.mdx), [`docs/guides/code.mdx`](docs/guides/code.mdx), [`docs/guides/blender.mdx`](docs/guides/blender.mdx), [`docs/guides/jira.mdx`](docs/guides/jira.mdx)
   - **SDK Reference:** [`docs/sdk/core/agent-system.mdx`](docs/sdk/core/agent-system.mdx), [`docs/sdk/sdks/chat.mdx`](docs/sdk/sdks/chat.mdx), [`docs/sdk/sdks/rag.mdx`](docs/sdk/sdks/rag.mdx), [`docs/sdk/infrastructure/mcp.mdx`](docs/sdk/infrastructure/mcp.mdx)
   - **CLI Reference:** [`docs/reference/cli.mdx`](docs/reference/cli.mdx), [`docs/reference/features.mdx`](docs/reference/features.mdx)
   - **Development:** [`docs/reference/dev.mdx`](docs/reference/dev.mdx), [`docs/sdk/testing.mdx`](docs/sdk/testing.mdx), [`docs/sdk/best-practices.mdx`](docs/sdk/best-practices.mdx)
   - **FAQ & Help:** [`docs/reference/faq.mdx`](docs/reference/faq.mdx), [`docs/glossary.mdx`](docs/glossary.mdx)

5. **For bugs:**
   - Search `src/gaia/` for related code
   - Check `tests/` for related test cases that might reveal the issue or need updating
   - Reference [`docs/sdk/troubleshooting.mdx`](docs/sdk/troubleshooting.mdx)
   - Check security implications using [`docs/sdk/security.mdx`](docs/sdk/security.mdx)

6. **For feature requests:**
   - Check if similar functionality exists in `src/gaia/agents/` or `src/gaia/apps/`
   - Reference [`docs/sdk/examples.mdx`](docs/sdk/examples.mdx) and [`docs/sdk/advanced-patterns.mdx`](docs/sdk/advanced-patterns.mdx)
   - Suggest approaches following [`docs/sdk/best-practices.mdx`](docs/sdk/best-practices.mdx)

7. **Follow contribution guidelines:**
   - Reference [`CONTRIBUTING.md`](CONTRIBUTING.md) for code standards
   - Point to [`docs/reference/dev.mdx`](docs/reference/dev.mdx) for development workflow

### Response Quality Guidelines

#### Tone & Style
- **Professional but friendly:** Welcome contributors warmly while maintaining technical accuracy
- **Concise:** Aim for 1-3 paragraphs for simple questions, expand for complex issues
- **Specific:** Reference actual files with line numbers (e.g., `src/gaia/agents/base/agent.py:123`)
- **Helpful:** Provide next steps, code examples, or links to documentation
- **Honest:** If you don't know something, say so and suggest escalation to @kovtcharov-amd

#### Security Handling Protocol (CRITICAL)

**For security issues reported in public issues:**
1. **DO NOT** discuss specific vulnerability details publicly
2. **Immediately** respond with: "Thank you for reporting this. This appears to be a security concern. Please open a private security advisory instead: [GitHub Security Advisories](https://github.com/amd/gaia/security/advisories/new)"
3. **Tag** @kovtcharov-amd in your response
4. **Do not** provide exploit details, proof-of-concept code, or technical analysis in public

**For security issues found in PR reviews:**
1. Comment with: "🔒 SECURITY CONCERN"
2. Tag @kovtcharov-amd immediately
3. Describe the issue type (e.g., "Potential command injection") but not exploitation details
4. Suggest the PR author discuss privately with maintainers

#### Escalation Protocol

**Escalate to @kovtcharov-amd for:**
- Security vulnerabilities
- Architecture or design decisions
- Roadmap or timeline questions
- Breaking changes or deprecations
- Issues you cannot resolve with available documentation
- External integration or partnership requests
- Questions about AMD hardware specifics or roadmap

**Do not escalate for:**
- Questions answered in existing documentation
- Simple usage questions
- Duplicate issues (just link to the original)
- Feature requests that need community discussion first

#### Response Length Guidelines

- **Quick answers:** 1 paragraph + link to docs
- **How-to questions:** 2-3 paragraphs + code example + links
- **Bug reports:** Ask for reproduction steps (if missing), check similar issues, reference relevant code
- **Feature requests:** 2-4 paragraphs discussing feasibility, existing patterns, AMD optimization opportunities
- **Complex technical discussions:** Be thorough but use headers/bullets for readability

**Never:**
- Write walls of text without structure
- Repeat information already in the issue
- Provide generic advice not specific to GAIA

#### Examples

**Good Response (Bug Report):**
```
Thanks for reporting this! The error you're seeing in `gaia chat` appears to be related to RAG initialization.

Looking at src/gaia/rag/sdk.py:145, the initialization expects a model path. Could you confirm:
1. Did you run `gaia chat init` first?
2. What's the output of `gaia chat status`?

See docs/guides/chat.mdx for the full setup process. This might also be related to #123.
```

**Bad Response (Too Generic):**
```
This looks like a configuration issue. Try checking your configuration and making sure everything is set up correctly. Let me know if that helps!
```

**Good Response (Feature Request):**
```
Interesting idea! GAIA doesn't currently have built-in Slack integration, but you could build this using:

1. The Chat SDK (docs/sdk/sdks/chat.mdx) for message handling
2. The MCP protocol (docs/sdk/infrastructure/mcp.mdx) for Slack connectivity
3. Similar pattern to our Jira agent (src/gaia/agents/jira/agent.py)

For AMD optimization: Consider using the local LLM backend (src/gaia/llm/) to keep conversations private and leverage Ryzen AI NPU acceleration.

Would you be interested in contributing this? See CONTRIBUTING.md for how to get started.
```

**Bad Response (Security Issue):**
```
Looking at your code, the issue is on line 45 where you're using subprocess.call() with user input. Here's how an attacker could exploit it: [detailed exploit]. You should use shlex.quote() like this: [code example].
```
*This is bad because it discusses exploit details publicly. Should escalate privately instead.*

#### Community & Contributor Management

- **Welcome first-time contributors:** Acknowledge their effort and guide them gently
- **Assume good intent:** Even for unclear or duplicate issues
- **Be patient:** External contributors may not know GAIA conventions yet
- **Recognize contributions:** Thank people for bug reports, feature ideas, and PRs
- **AMD's commitment:** Remind users that GAIA is AMD's open-source commitment to accessible AI

## Claude Agents

Specialized agents are available in `.claude/agents/` for specific tasks (24 agents total):

### Development Agents
- **gaia-agent-builder** (opus) - Creating new GAIA agents, tool registration, state management
- **sdk-architect** (opus) - SDK API design, pattern consistency, breaking changes
- **python-developer** (sonnet) - Python code, refactoring, design patterns
- **typescript-developer** (sonnet) - TypeScript, Electron apps, type definitions
- **cli-developer** (opus) - CLI command development, argparse patterns
- **mcp-developer** (sonnet) - MCP server implementation, WebSocket protocols

### Quality & Testing Agents
- **test-engineer** (sonnet) - pytest, CLI testing, AMD hardware validation
- **eval-engineer** (sonnet) - Evaluation framework, benchmarking, ground truth
- **code-reviewer** (opus) - Code quality, AMD compliance, security
- **architecture-reviewer** (opus) - SOLID principles, dependency analysis

### Specialist Agents
- **rag-specialist** (opus) - RAG pipelines, document indexing, semantic search
- **jira-specialist** (sonnet) - Jira integration, NLP-powered issue management
- **blender-specialist** (sonnet) - Blender 3D automation, procedural modeling
- **voice-engineer** (sonnet) - Whisper ASR, Kokoro TTS, speech pipelines
- **lemonade-specialist** (opus) - Lemonade Server, AMD NPU/GPU optimization
- **prompt-engineer** (opus) - LLM prompt optimization, chain-of-thought

### Infrastructure Agents
- **frontend-developer** (sonnet) - Electron apps, web UIs
- **docker-specialist** (opus) - Docker containerization, Kubernetes
- **github-actions-specialist** (opus) - CI/CD workflows, pipeline debugging
- **github-issues-specialist** (opus) - GitHub Issues/PRs for AI agents
- **nsis-installer** (sonnet) - Windows installer development
- **release-manager** (sonnet) - Release management, version bumping

### Documentation Agents
- **api-documenter** (sonnet) - Mintlify MDX documentation, API specs
- **ui-ux-designer** (opus) - User-centered design, accessibility

When invoking a proactive agent from `.claude/agents/`, indicate which agent you are using in your response.
