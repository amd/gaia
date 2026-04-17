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

### IMPORTANT: Commit Only When Bulletproof

You may create commits on your own **only when the change is bulletproof**. "Bulletproof" means every one of these has happened:

1. **Validated** — tests run and pass (`pytest` on the affected paths), lint runs and passes (`python util/lint.py --all` or the relevant subset), and — for UI/CLI-visible changes — the golden path is exercised end-to-end.
2. **Critiqued** — the changes have been read back, contradictions between files (examples in docs vs. real code, generated templates vs. existing patterns, new rule vs. established convention) have been actively hunted for and resolved. Empirical evidence from the actual codebase beats textbook advice every time.
3. **Scope-clean** — only the files required for the stated task are modified. No drive-by formatting, no unrelated refactors, no "while I'm here" additions.
4. **No half-finished work** — every function has a body, every import is used, no `TODO` left as a placeholder for missing logic, no tests referencing deleted code.

If *any* of those is uncertain, **do not commit** — surface the uncertainty to the user and wait. "I think this probably works" is not bulletproof. A second opinion from a relevant subagent (e.g. `code-reviewer`, `architecture-reviewer`) is a good proxy for critique when the user isn't immediately available.

**Still prohibited without explicit user instruction:** pushing to remote, force-pushing anywhere, amending existing commits, touching release/publishing branches, committing anything that looks like a secret. When in doubt, ask — the cost of a 10-second confirmation is trivial; the cost of an unwanted commit can be hours of cleanup.

### IMPORTANT: PR Descriptions — Tight and Value-Focused

**Keep PR descriptions short. Lead with *why* and *impact*, not *what*.** Reviewers skim; long walls of text get ignored. A PR description is a sales pitch for the change, not a changelog.

**Target shape:**

1. **One-paragraph Summary** — what this PR does, in plain English, and the problem it solves. If a reader stops after this paragraph, they should understand the change's purpose.
2. **Bullet list of threads** (if the PR has more than one logical thread) — one line each, with a *why this matters* clause for every bullet. Not every file changed — only changes a reviewer needs to evaluate.
3. **Test plan** — checkbox list of how to verify. Specific commands beat vague prose.

**Hard rules:**

- **No section longer than ~5 lines of prose** before breaking into bullets or cutting.
- **Every non-trivial claim earns its place with a why.** "Added a linter" is noise; "Added a linter so new agents stop shipping with missing docs/tests" is signal.
- **Cut exhaustive file-by-file enumeration.** The diff is the source of truth for what files changed. The description is the source of truth for *why they changed*.
- **No "Generated with Claude Code" tagline** (see attribution rule below).
- **If the PR really does bundle many threads**, group them — don't list 16 commits. Reviewers scan 4 themes faster than 16 bullets.

**Anti-patterns:**

- ❌ Copy-pasting the commit message log into the PR body
- ❌ "This PR adds X, Y, Z, A, B, C, D, E, F, G" with no stated value
- ❌ Mirroring every bullet in the summary inside the test plan (pick one)
- ❌ Explaining implementation details a reviewer will read from the diff anyway

**Title convention:** conventional commits style (`feat(scope):`, `fix(scope):`, `docs(scope):`, `ci(scope):`), under ~70 chars, descriptive of the *change*, not the *why* (the body carries the why).

### IMPORTANT: No Claude Attribution of Any Kind

**Never include any mention of Claude authoring or assisting in anything you produce.** Applies to:

- PR descriptions and titles
- PR review comments, issue comments, discussion replies
- Commit message bodies **including `Co-Authored-By: Claude ...` trailers**
- Code comments, docstrings, or doc files
- Any other artifact that ships to users or stakeholders

**Specifically prohibited:**
- `🤖 Generated with [Claude Code](https://claude.com/claude-code)` footers
- `Co-Authored-By: Claude Opus ...`, `Co-Authored-By: Claude Sonnet ...`, `Co-Authored-By: <any Claude variant>` trailers
- "Authored by AI", "AI-generated", "Written by Claude" attributions
- Inline code comments crediting Claude

Rationale: output is the project's work product. The human contributor is the author of record. AI assistance is a tool like an IDE or linter — tools don't co-author commits.

When crafting commit messages, write as the human author writing them. Skip the trailer section entirely unless you need to credit a real human collaborator.

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

### No Silent Fallbacks — Fail Loudly

**Do not add fallbacks, default-to-something-that-works-ish behavior, or silent degradation paths.** Either the operation succeeds as intended, or it raises an actionable error. Applies to every layer: agents, LLM clients, CLI, CI workflows, config loaders, RAG, API server, Electron apps.

**Prohibited:**
- `except Exception: pass`, `try: ... except: return None`, or any handler that discards the error and returns a placeholder/empty/cached value.
- Model-level `fallback_model` / `fallback_client` / "try the other provider" glue. If Opus is down, surface the error — don't silently switch to Sonnet.
- Config loaders that default missing required values to empty string, `None`, or a guess. Missing required config is a startup-time error.
- Retry loops that swallow the final failure and return success.

**Allowed (this is fail-loudly, not "no error handling"):**
- Catching a specific exception and **re-raising with context** (use `raise ... from e` so the original traceback is preserved): `raise ValueError(f"invalid agent manifest at {path}: {e}") from e`.
- Translating exceptions at a **system boundary** (REST endpoint → HTTP 500 with a correlation ID; agent tool → structured error object).
- Explicit **opt-in** retry/backoff when the caller passed a parameter asking for it (e.g., an explicit `max_retries=3` constructor arg, like `ClaudeClient(max_retries=3)` in [`src/gaia/eval/claude.py`](src/gaia/eval/claude.py)) — never a hidden retry loop inside a function body that the caller didn't request.
- **GHA `continue-on-error: true` on specific steps** where the step is known to emit non-fatal permission warnings (e.g., `claude-code-action@beta` on fork PRs). This tolerates the warning without substituting different behavior — the step still runs its intended logic. It's *step-level tolerance*, not silent degradation.

**Actionable errors name three things:**
1. *What failed* — `"Lemonade Server not reachable at http://localhost:8000"`
2. *What the caller should do* — `"Run `gaia init` to install it, or set LEMONADE_BASE_URL to a running server"`
3. *Where to look next* — file path, docs link, issue tracker

**Why the rule exists:** fallbacks hide regressions. A review bot silently downgraded from Opus to a smaller model looks fine but produces worse reviews for weeks. A config loader that defaults a missing API key to `""` produces confusing 401s deep in the request pipeline instead of a clear `"ANTHROPIC_API_KEY is not set"` at startup. Better a loud error the user can fix than a quiet wrong answer.

**On existing violations:** the codebase has pre-existing `except Exception: pass` blocks (mostly in `src/gaia/ui/`) that predate this rule. They are **tech debt, not precedent**. When you touch a file that has one, fix it in the same commit — add a specific exception type, log with context, or re-raise. Don't cite existing violations to justify adding new ones.

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
uv pip install -e ".[ui]"    # For Agent UI development
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
gaia chat --ui                     # Agent UI (browser-based)
gaia-code                          # Code agent
```

### Agent UI Development
```bash
# Build frontend (required before gaia chat --ui)
cd src/gaia/apps/webui && npm install && npm run build

# Development with hot reload (two terminals)
uv run python -m gaia.ui.server --debug   # Terminal 1: backend (port 4200)
cd src/gaia/apps/webui && npm run dev      # Terminal 2: frontend (port 5173)
```

## Project Structure

```
gaia/
├── src/gaia/           # Main source code
│   ├── agents/         # Agent implementations
│   │   ├── base/       # Base Agent class, MCPAgent, ApiAgent
│   │   ├── tools/      # Cross-agent tool mixins (file_tools, screenshot_tools)
│   │   ├── chat/       # ChatAgent with RAG (tools/rag_tools, tools/shell_tools)
│   │   ├── code/       # CodeAgent with orchestration, validators, file_io tools
│   │   ├── builder/    # BuilderAgent — scaffolds new agents from templates
│   │   ├── summarize/  # SummarizeAgent — document/text summarization
│   │   ├── blender/    # BlenderAgent for 3D automation
│   │   ├── jira/       # JiraAgent for issue management
│   │   ├── docker/     # DockerAgent for containerization
│   │   ├── emr/        # MedicalIntakeAgent for healthcare (VLM)
│   │   ├── routing/    # RoutingAgent for intelligent agent selection
│   │   ├── sd/         # SDAgent for Stable Diffusion image generation
│   │   └── registry.py # YAML-manifest agent registry + KNOWN_TOOLS map
│   ├── api/            # OpenAI-compatible REST API server
│   ├── apps/           # Standalone applications
│   │   ├── webui/      # Agent UI frontend (React/Vite/Electron)
│   │   ├── jira/       # Jira standalone app
│   │   ├── llm/        # LLM standalone app
│   │   ├── summarize/  # Document summarization app
│   │   ├── docker/     # Docker standalone app
│   │   ├── example/    # Reference/starter app
│   │   └── _shared/    # Shared assets for apps
│   ├── audio/          # Audio processing (Whisper ASR, Kokoro TTS)
│   ├── chat/           # Agent SDK (AgentSDK class, prompts, app entry)
│   ├── database/       # DatabaseMixin and DatabaseAgent
│   ├── electron/       # Electron app integration
│   ├── eval/           # Evaluation framework
│   ├── img/            # Shared image assets
│   ├── installer/      # Install/init commands (gaia init, lemonade installer)
│   ├── llm/            # LLM backend clients (Lemonade, Claude, OpenAI) + providers/
│   ├── mcp/            # Model Context Protocol servers/clients
│   ├── rag/            # Document retrieval (RAG)
│   ├── sd/             # Stable Diffusion tool mixin (SDToolsMixin)
│   ├── shell/          # Shell integration
│   ├── talk/           # Voice interaction SDK
│   ├── testing/        # Test utilities and fixtures
│   ├── ui/             # Agent UI backend (FastAPI server, routers, SSE, database)
│   ├── utils/          # Utility modules (FileWatcher, parsing)
│   ├── vlm/            # Vision LLM tool mixin (VLMToolsMixin, structured extraction)
│   └── cli.py          # Main CLI entry point (all `gaia <command>` subparsers)
├── tests/              # Test suite
│   ├── unit/           # Unit tests
│   ├── mcp/            # MCP integration tests
│   ├── integration/    # Cross-system integration tests
│   ├── stress/         # Stress/load tests
│   ├── electron/       # Electron app tests (Jest)
│   ├── fixtures/       # Shared test fixtures/data
│   └── test_*.py       # Top-level feature tests (sdk, api, chat, code, rag, eval…)
├── scripts/            # Build, install, and launch scripts
├── docs/               # Documentation (MDX format)
├── workshop/           # Tutorial materials
└── .github/workflows/  # CI/CD pipelines
```

### Console Script Entry Points

Defined in [`setup.py`](setup.py) under `console_scripts`:

| Script | Entry Point | Purpose |
|--------|-------------|---------|
| `gaia` / `gaia-cli` | `gaia.cli:main` | Main CLI — all `gaia <subcommand>` |
| `gaia-mcp` | `gaia.mcp.mcp_bridge:main` | Standalone MCP bridge binary |
| `gaia-code` | `gaia.agents.code.cli:main` | CodeAgent standalone entry (NOT `gaia code`) |
| `gaia-emr` | `gaia.agents.emr.cli:main` | EMR/MedicalIntake standalone entry |

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
- **Agent SDK** (`src/gaia/chat/`): AgentSDK class (formerly ChatSDK) for programmatic chat - see [`docs/sdk/sdks/chat.mdx`](docs/sdk/sdks/chat.mdx)
- **Agent UI Backend** (`src/gaia/ui/`): FastAPI server with modular routers (chat, documents, files, sessions, system, tunnel), SSE streaming, database - see [`docs/guides/agent-ui.mdx`](docs/guides/agent-ui.mdx)
- **Agent UI Frontend** (`src/gaia/apps/webui/`): React/TypeScript/Vite desktop app with Electron shell - see [`docs/sdk/sdks/agent-ui.mdx`](docs/sdk/sdks/agent-ui.mdx)
- **Evaluation** (`src/gaia/eval/`): Batch experiments and ground truth - see [`docs/reference/eval.mdx`](docs/reference/eval.mdx)

### Agent Implementations

| Agent | Location | Description | Default Model |
|-------|----------|-------------|---------------|
| **ChatAgent** | `agents/chat/agent.py` | Document Q&A with RAG | Qwen3.5-35B |
| **CodeAgent** | `agents/code/agent.py` | Code generation with orchestration | Qwen3.5-35B |
| **BuilderAgent** | `agents/builder/agent.py` | Scaffolds new agents from templates | Qwen3.5-35B |
| **SummarizeAgent** | `agents/summarize/agent.py` | Document/text summarization | Qwen3.5-35B |
| **JiraAgent** | `agents/jira/agent.py` | Jira issue management | Qwen3.5-35B |
| **BlenderAgent** | `agents/blender/agent.py` | 3D scene automation | Qwen3.5-35B |
| **DockerAgent** | `agents/docker/agent.py` | Container management | Qwen3.5-35B |
| **MedicalIntakeAgent** | `agents/emr/agent.py` | Medical form processing | Qwen3-VL-4B (VLM) |
| **RoutingAgent** | `agents/routing/agent.py` | Intelligent agent selection | Qwen3.5-35B |
| **SDAgent** | `agents/sd/agent.py` | Stable Diffusion image generation | SDXL-Turbo |

### Agent Registry & Tool Mixins

New agents are preferably registered via YAML manifests validated by Pydantic in [`src/gaia/agents/registry.py`](src/gaia/agents/registry.py). The registry exposes `KNOWN_TOOLS` — a curated map of reusable tool mixins that agents opt into by name:

| Tool name | Mixin | Purpose |
|-----------|-------|---------|
| `rag` | `gaia.agents.chat.tools.rag_tools.RAGToolsMixin` | Document retrieval |
| `file_search` | `gaia.agents.tools.file_tools.FileSearchToolsMixin` | Fuzzy/glob file search |
| `file_io` | `gaia.agents.code.tools.file_io.FileIOToolsMixin` | Read/write/edit files |
| `shell` | `gaia.agents.chat.tools.shell_tools.ShellToolsMixin` | Sandboxed shell commands |
| `screenshot` | `gaia.agents.tools.screenshot_tools.ScreenshotToolsMixin` | Screen capture |
| `sd` | `gaia.sd.mixin.SDToolsMixin` | Stable Diffusion image generation |
| `vlm` | `gaia.vlm.mixin.VLMToolsMixin` | Vision LLM / structured extraction |

When adding a new tool, register it in `KNOWN_TOOLS` so YAML-manifest agents can declare it.

### Default Models
- General tasks: `Qwen3-0.6B-GGUF`
- Code/Agents: `Qwen3.5-35B-A3B-GGUF`
- Vision tasks: `Qwen3-VL-4B-Instruct-GGUF`

## CLI Commands

All commands are registered in [`src/gaia/cli.py`](src/gaia/cli.py). Run `gaia -h` for the authoritative list.

**Agents & chat:**
- `gaia chat` - Interactive chat with RAG
- `gaia chat --ui` - Launch Agent UI (browser-based, requires `[ui]` extras)
- `gaia chat --ui --ui-port 8080` - Agent UI on custom port
- `gaia talk` - Voice interaction
- `gaia prompt "<text>"` - Single prompt to LLM (with system-prompt support)
- `gaia llm "<text>"` - Simple LLM queries
- `gaia summarize` - Document summarization
- `gaia blender` - Blender 3D agent
- `gaia sd` - Stable Diffusion image generation
- `gaia jira` - Jira integration
- `gaia docker` - Docker management

**Servers & infrastructure:**
- `gaia api` - OpenAI-compatible API server
- `gaia mcp {start|stop|status|test|agent|docker|add|list|remove|tools|test-client}` - MCP bridge
- `gaia cache {status|clear}` - Cache management

**Setup & utilities:**
- `gaia init` - Setup Lemonade Server and download models
- `gaia install` - Install helper (e.g. Lemonade on first run)
- `gaia download` - Download a model
- `gaia kill` - Kill stray GAIA / Lemonade processes
- `gaia test` - Smoke tests
- `gaia yt` - YouTube transcript ingest
- `gaia template` - Scaffold agent templates

**Evaluation & analysis** (see [`docs/reference/eval.mdx`](docs/reference/eval.mdx)):
- `gaia eval {fix-code|agent}` - Run evaluation harness
- `gaia gt` - Generate ground truth
- `gaia generate` - Dataset/response generation
- `gaia batch-exp` - Batch experiments
- `gaia report` - Render eval reports
- `gaia visualize` / `gaia perf-vis` - Visualize results

**Standalone binaries** (separate `console_scripts`, not subcommands):
- `gaia-code` - CodeAgent entry (`src/gaia/agents/code/cli.py`)
- `gaia-emr` - Medical intake entry (`src/gaia/agents/emr/cli.py`)
- `gaia-mcp` - Standalone MCP bridge binary

## Documentation Index

All documentation uses `.mdx` format (Markdown + JSX for Mintlify).

**User Guides:**
- [`docs/guides/chat.mdx`](docs/guides/chat.mdx) - Chat with RAG
- [`docs/guides/agent-ui.mdx`](docs/guides/agent-ui.mdx) - Agent UI (desktop chat)
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
- [`docs/sdk/sdks/chat.mdx`](docs/sdk/sdks/chat.mdx) - Agent SDK (formerly Chat SDK)
- [`docs/sdk/sdks/agent-ui.mdx`](docs/sdk/sdks/agent-ui.mdx) - Agent UI SDK
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

## Roadmap & Plans

The roadmap is at [`docs/roadmap.mdx`](docs/roadmap.mdx) ([live site](https://amd-gaia.ai/roadmap)). Plan documents are in `docs/plans/`:

**Agent UI:**
- [`docs/plans/agent-ui.mdx`](docs/plans/agent-ui.mdx) - GaiaAgent comprehensive plan (Phases A-D)
- [`docs/plans/setup-wizard.mdx`](docs/plans/setup-wizard.mdx) - First-run onboarding and system scanner
- [`docs/plans/security-model.mdx`](docs/plans/security-model.mdx) - Guardrails, audit trail, credential vault
- [`docs/plans/email-calendar-integration.mdx`](docs/plans/email-calendar-integration.mdx) - Email triage, calendar, meeting notes
- [`docs/plans/messaging-integrations-plan.mdx`](docs/plans/messaging-integrations-plan.mdx) - Signal, Discord, Slack, Telegram adapters
- [`docs/plans/autonomy-engine.mdx`](docs/plans/autonomy-engine.mdx) - Heartbeat, scheduler, background service

**Ecosystem:**
- [`docs/plans/agent-hub.mdx`](docs/plans/agent-hub.mdx) - Agent marketplace and community hub
- [`docs/plans/skill-format.mdx`](docs/plans/skill-format.mdx) - SKILL.md specification
- [`docs/plans/oem-bundling.mdx`](docs/plans/oem-bundling.mdx) - OEM hardware pre-configuration

**Infrastructure:**
- [`docs/plans/installer.mdx`](docs/plans/installer.mdx) - Desktop installer
- [`docs/plans/mcp-client.mdx`](docs/plans/mcp-client.mdx) - MCP client integration
- [`docs/plans/cua.mdx`](docs/plans/cua.mdx) - Computer Use Agent
- [`docs/plans/docker-containers.mdx`](docs/plans/docker-containers.mdx) - Docker deployment

**Key architectural decisions (April 2026):**
- ChatAgent renamed to **GaiaAgent** in v0.20.0 (#696)
- Voice-first is P0 enabling technology (#702)
- No context compaction — memory + RAG handles long conversations
- Configuration dashboard + Observability dashboard as separate Agent UI panels
- MCP servers primary for email/calendar (not browser automation)
- Signal is Phase 1 messaging priority (privacy-first)

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
   - Agent implementations: `src/gaia/agents/` (base/, tools/, chat/, code/, builder/, summarize/, blender/, jira/, docker/, emr/, routing/, sd/, registry.py)
   - CLI commands: `src/gaia/cli.py`
   - MCP integration: `src/gaia/mcp/`
   - LLM backend: `src/gaia/llm/` (+ `providers/` for Claude/OpenAI)
   - Audio processing: `src/gaia/audio/` (whisper_asr.py, kokoro_tts.py)
   - RAG system: `src/gaia/rag/` (sdk.py, pdf_utils.py)
   - Evaluation: `src/gaia/eval/` (eval.py, batch_experiment.py)
   - Applications: `src/gaia/apps/` (webui/, jira/, llm/, summarize/, docker/, example/, _shared/)
   - Agent SDK: `src/gaia/chat/` (AgentSDK class, formerly ChatSDK)
   - Agent UI backend: `src/gaia/ui/` (FastAPI server, routers, SSE handler)
   - Agent UI frontend: `src/gaia/apps/webui/` (React/TypeScript/Vite/Electron)
   - API Server: `src/gaia/api/`
   - SD/VLM tool mixins: `src/gaia/sd/mixin.py`, `src/gaia/vlm/mixin.py`

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

1. The Agent SDK (docs/sdk/sdks/chat.mdx) for message handling
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

Specialized agents live in `.claude/agents/` (23 total). Each agent file is the authoritative source for its scope, when-to-use / when-NOT-to-use triggers, and conventions — the summaries below are a pointer, not a replacement.

### Development
- **gaia-agent-builder** — Creating a new GAIA agent (Python class or YAML manifest). Not for tuning an existing agent's prompt or adding a single tool.
- **sdk-architect** — Public SDK surface design, cross-module consistency, breaking-change planning.
- **python-developer** — Idiomatic Python 3.10+ inside `src/gaia/` (not new agents — use gaia-agent-builder).
- **typescript-developer** — Type-safe TS for the Agent UI and Electron IPC.
- **cli-developer** — `gaia <subcommand>` work in `src/gaia/cli.py` and `docs/reference/cli.mdx`.
- **mcp-developer** — MCP servers, the MCP bridge, and tool/resource/prompt exposure.

### Quality & testing
- **test-engineer** — pytest, fixtures, CLI integration tests, hardware validation runs.
- **eval-engineer** — Evaluation framework (`src/gaia/eval/`), ground truth, batch experiments.
- **code-reviewer** — Per-file quality, AMD compliance, framework invariants; flags security privately.
- **architecture-reviewer** — Layering, dependency direction, mixin composition, breaking-change blast radius.

### Specialists
- **rag-specialist** — `src/gaia/rag/` and the `rag` tool mixin: chunking, embeddings, retrieval quality.
- **jira-specialist** — `JiraAgent`, JQL templates, Atlassian integration.
- **blender-specialist** — `BlenderAgent` and the Blender MCP server/client pair.
- **voice-engineer** — Whisper ASR, Kokoro TTS, Talk SDK, real-time audio.
- **lemonade-specialist** — Lemonade Server / provider adapter, NPU/GPU optimisation, model selection.
- **prompt-engineer** — System prompts, tool docstrings, eval-judge prompts inside GAIA.

### Infrastructure
- **frontend-developer** — React/Vite/Electron Agent UI and standalone apps.
- **docker-specialist** — Dockerfiles, compose, and the `DockerAgent`.
- **github-actions-specialist** — `.github/workflows/` authoring and debugging.
- **github-issues-specialist** — Agent-ready issues/PRs, `AGENTS.md`, repo setup for AI agents.
- **release-manager** — Version bumps, changelog, publish/PyPI/installer workflows.

### Documentation & design
- **api-documenter** — Mintlify MDX docs under `docs/` (SDK specs, guides, CLI reference).
- **ui-ux-designer** — GAIA user flows, wireframes, accessibility, voice UX.

When invoking a proactive agent, name it in your response. If a user task straddles two agents' scopes, pick the primary owner and hand off rather than duplicating.

## Claude Code Plugins

The repo declares two plugins in [`.claude/settings.json`](.claude/settings.json) from the official Anthropic marketplace:

- **`frontend-design@claude-plugins-official`** — higher-quality UI generation
- **`superpowers@claude-plugins-official`** — structured dev methodology (brainstorm → plan → TDD → review → verify)

These are **not auto-installed silently**. First time a contributor opens the repo in Claude Code (v2.1.0+), they'll be prompted to install them. Accept once — see [`docs/reference/dev.mdx`](docs/reference/dev.mdx) "Step 6: Claude Code Plugins (Optional)" for details and the opt-out.

When a task fits a Superpowers skill (e.g. `superpowers:brainstorming`, `superpowers:writing-plans`, `superpowers:test-driven-development`, `superpowers:systematic-debugging`, `superpowers:verification-before-completion`), **use it** — these skills enforce the dev practices this repo expects.
