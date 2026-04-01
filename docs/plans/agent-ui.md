# Agent UI — Comprehensive Plan

> **Date:** 2026-03-06 (updated 2026-04-01)
>
> **Milestones:** v0.18.2 → v0.20.0 → v0.23.0 → v0.24.0
>
> **Strategic context:** Track B (RyzenClaw) from the OpenClaw strategy doc.
> GAIA is a secure, hardware-optimized desktop AI assistant for mainstream users.

---

## 1. Positioning & Differentiation

GAIA should NOT clone OpenClaw. OpenClaw is a messaging-native daemon for power users
willing to accept security risk. GAIA is a **secure, hardware-optimized desktop AI
assistant** for mainstream users who want privacy and safety.

### GAIA vs OpenClaw Scorecard

| Dimension | GAIA Advantage | OpenClaw Advantage |
|-----------|---------------|-------------------|
| Security | Guardrails, confirmation, whitelisted shell, no public ports | Unrestricted = more powerful |
| Hardware efficiency | 8GB minimum (AMD NPU), MoE models at 52 tok/s | Requires 32GB+ RAM |
| Document intelligence | Built-in RAG, PDF support, semantic search | No RAG |
| Desktop UI | Rich visual feedback, code blocks, file previews | N/A (messaging only) |
| Privacy | Local-only default, data never leaves device | Cloud API default |
| Messaging adapters | 0 (Desktop UI primary) | 24+ platforms |
| Persistent memory | Planned (v0.20.0) | Shipped (MEMORY.md) |
| Proactive scheduling | Planned (v0.23.0) | Shipped (Heartbeat) |
| Ecosystem | MCP servers | 13,700+ skills on ClawHub |

### One-Line Pitch

> OpenClaw for people who want their AI agent to be powerful AND safe — running locally
> on AMD hardware with built-in document intelligence, proactive scheduling, and
> human-in-the-loop safety controls.

### User Personas

**Primary: "Privacy Paul"** — Developer/power user concerned about data privacy.
Has technical PDFs to search. Already has Lemonade installed.

**Secondary: "Curious Carla"** — Non-technical, heard about local AI, wants "just works"
experience. May not have Lemonade installed.

**Tertiary: "Enterprise Eric"** — Evaluating for company use. Needs data locality
guarantees, audit trails, compliance features.

### Competitive Positioning

| Feature | ChatGPT Desktop | Ollama + Open WebUI | GAIA |
|---------|----------------|---------------------|------|
| Privacy | Cloud | Local (manual setup) | Local (one-click) |
| Cost | $20/month | Free | Free |
| Offline | No | Yes | Yes |
| Document Q&A | Limited | Requires config | Native RAG |
| AMD optimization | None | Generic | NPU/iGPU accelerated |
| Windows support | Yes | Limited | Native |
| Enterprise ready | Yes (cloud) | DIY | Designed for |

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        GAIA Agent UI                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   ┌──────────────────┐      ┌─────────────────────────────────┐ │
│   │  Electron Shell  │      │      Frontend (Vanilla JS)      │ │
│   │  (main.js)       │─────▶│  ┌─────────────────────────────┐│ │
│   │                  │      │  │ Onboarding Wizard            ││ │
│   │  • Window mgmt   │      │  │ Chat View + Message Input    ││ │
│   │  • Tray icon     │      │  │ Document Library + Picker    ││ │
│   │  • Auto-update   │      │  │ File Browser + Search        ││ │
│   │  • IPC bridge    │      │  │ Settings Panel               ││ │
│   └──────────────────┘      │  │ Agent Activity Feed          ││ │
│                             │  └─────────────────────────────┘│ │
│                             └─────────────────────────────────┘ │
│                                          │                       │
│                                          ▼                       │
│   ┌─────────────────────────────────────────────────────────────┐│
│   │               OpenAI-Compatible API Server                  ││
│   │  ┌──────────────┐ ┌──────────────┐ ┌────────────────────┐  ││
│   │  │ /v1/chat/*   │ │ /api/docs/*  │ │ /api/sessions/*    │  ││
│   │  └──────────────┘ └──────────────┘ └────────────────────┘  ││
│   │  ┌──────────────┐ ┌──────────────┐ ┌────────────────────┐  ││
│   │  │ /api/system  │ │ /api/mcp/*   │ │ /api/memory/*      │  ││
│   │  └──────────────┘ └──────────────┘ └────────────────────┘  ││
│   └─────────────────────────────────────────────────────────────┘│
│                                          │                       │
│                                          ▼                       │
│   ┌─────────────────────────────────────────────────────────────┐│
│   │                    GAIA Core Layer                          ││
│   │  ┌─────────────┐ ┌─────────────┐ ┌─────────────────────┐  ││
│   │  │ ChatSDK     │ │ RAG SDK     │ │ LemonadeClient      │  ││
│   │  └─────────────┘ └─────────────┘ └─────────────────────┘  ││
│   │  ┌─────────────┐ ┌─────────────┐ ┌─────────────────────┐  ││
│   │  │ Agent System │ │ SQLite DB  │ │ MCP Client Manager  │  ││
│   │  └─────────────┘ └─────────────┘ └─────────────────────┘  ││
│   └─────────────────────────────────────────────────────────────┘│
│                                          │                       │
│   ┌─────────────────────────────────────────────────────────────┐│
│   │                  Lemonade Server (External)                 ││
│   │  • Model serving (Qwen3-Coder-30B, Nomic-Embed)             ││
│   │  • NPU/iGPU acceleration                                    ││
│   └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
```

### Communication Pattern

- **Electron mode:** Frontend → IPC (preload bridge) → Main Process → MCP Bridge HTTP
- **Browser mode:** Frontend → Direct HTTP to API Server
- **ApiClient** auto-detects environment and routes accordingly

### Database Schema

```sql
CREATE TABLE documents (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    filepath TEXT NOT NULL,
    file_hash TEXT UNIQUE NOT NULL,
    file_size INTEGER,
    chunk_count INTEGER,
    indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_accessed_at TIMESTAMP
);

CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    title TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    model TEXT NOT NULL,
    system_prompt TEXT
);

CREATE TABLE session_documents (
    session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
    document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
    attached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (session_id, document_id)
);

CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT CHECK(role IN ('user', 'assistant', 'system', 'tool')) NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    rag_sources TEXT,    -- JSON: [{doc_id, chunk_idx, score, page}]
    tokens_prompt INTEGER,
    tokens_completion INTEGER
);

CREATE INDEX idx_messages_session ON messages(session_id, created_at);
CREATE INDEX idx_documents_hash ON documents(file_hash);
```

**Document/Session model:** Hybrid — global document library + per-session attachment.
Documents indexed once, sessions attach from library.

---

## 3. Near-Term: Eval, Webapp Integration & API Polish

### 3.1 v0.18.0 — Agent Eval Benchmark

**Goal:** Establish quality baselines and fix reliability issues in the RAG pipeline.

| Deliverable | Description | Issue |
|-------------|-------------|-------|
| Document processing test suite | Comprehensive tests for RAG indexing, chunking, retrieval | #456 |
| Thread-safe RAG index operations | Fix concurrent access bugs | #455 |
| Unbounded query context fix | Prevent context growth from consuming entire window | #453 |
| File System Agent | Navigation, browsing, and scratchpad tools | #502 |
| Web search tool | DuckDuckGo + Perplexity for research and daily briefs | #669 |
| Perplexity as premium search provider | Optional paid web search | #547 |

### 3.2 v0.18.1 — Webapp Integration Support

**Goal:** Make GAIA embeddable in third-party web applications.

| Deliverable | Description | Issue |
|-------------|-------------|-------|
| System prompt passthrough | Honor custom system prompts from API clients | #650 |
| Document indexing API | `POST /v1/documents/index` on OpenAI-compatible server | #651 |
| Enhanced health check | LLM and RAG status in `/health` response | #655 |
| Web search tool (lightweight) | DuckDuckGo + Perplexity for research | #669 |
| API key authentication | Optional auth for the API server | #630 |

These endpoints enable external apps (n8n workflows, custom dashboards, SaaS integrations)
to use GAIA as a backend without the Electron UI.

### 3.3 v0.18.2 — Agent Registry, API Readiness & UI Polish

**Goal:** Make ChatAgent useful by wiring existing SDK mixins and adding MCP support.

| Deliverable | Description | Issue |
|-------------|-------------|-------|
| Agent registry and selection UI | Switch between agents in the UI | #612 |
| Agent orchestrator and routing | Route queries to appropriate agent | #622 |
| JS chat client library | `@amd-gaia/chat-client` npm package | #652 |
| Drop-in chat widget | Single `<script>` tag embeddable widget | #653 |
| Multi-session concurrency | Support concurrent API clients | #654 |
| Tool execution guardrails | Confirm-first popup for write operations | #438 |
| MCP security hardening | Address MCP protocol vulnerabilities | #94 |
| Lemonade state feedback | Show model download/busy/connection state in UI | #588 |

## 4. Phase A — Wire SDK Capabilities into ChatAgent

**GitHub:** Spans v0.18.0 through v0.18.2

**Goal:** Make ChatAgent as capable as CodeAgent for consumer use.

### ChatAgent Target Class Hierarchy

```python
class ChatAgent(
    Agent,
    MCPClientMixin,        # MCP server connectivity
    RAGToolsMixin,         # Document Q&A (10 tools)
    FileToolsMixin,        # File watching (1 tool)
    ShellToolsMixin,       # Shell commands (1 tool)
    FileSearchToolsMixin,  # File/directory search (3 tools after dedup)
    FileIOToolsMixin,      # Read/write/edit files (4 tools, refactored)
    ProjectManagementMixin,# list_files (1 tool)
    ExternalToolsMixin,    # search_web (0-1 tool, conditional)
):
```

### Tool Set (~21 native + MCP tools)

| Category | Tools | Source | Count |
|----------|-------|--------|-------|
| RAG (core) | query_documents, query_specific_file, search_indexed_chunks, index_document, index_directory, list_indexed_documents, summarize_document | RAGToolsMixin | 7 |
| RAG (diagnostic) | dump_document, evaluate_retrieval, rag_status | RAGToolsMixin | 3 |
| File Ops | read_file, write_file, edit_file, search_code | FileIOToolsMixin | 4 |
| File Search | search_file, search_directory, search_file_content | FileSearchToolsMixin | 3 |
| Navigation | list_files | ProjectManagementMixin | 1 |
| Shell | run_shell_command | ShellToolsMixin | 1 |
| File Watch | add_watch_directory | FileToolsMixin | 1 |
| Web Search | search_web | ExternalToolsMixin (conditional) | 0-1 |
| **Total native** | | | **20-21** |
| + MCP tools | Playwright, Brave, etc. | Connected MCP servers | +5-10 |

**Context budget:** 30 tools x ~400 tokens = ~12K tokens. On 32K context, that's 37%.
**Cap MCP tools at 10.** Consider lazy-loading RAG diagnostic tools.

### Explicitly Excluded Tools (zero consumer value)

- `edit_python_file` — Python AST editing, developer-only
- `create_project` — project scaffolding, developer-only
- `search_documentation` (Context7) — npm docs, developer-only
- `run_cli_command` — unrestricted shell, no guardrails
- All TestingMixin — arbitrary code execution, not sandboxed
- All ErrorFixing/CodeTools/TypeScript/Web/Prisma — developer-only

### Work Items

| Item | Effort | Status | GitHub Issue |
|------|--------|--------|-------------|
| FileIOToolsMixin: `hasattr()` guards for ValidationAndParsingMixin | 2 hours | Open | — |
| Tool name collision: MRO guard for FileSearchToolsMixin vs FileIOToolsMixin | 1 hour | Open | — |
| Wire FileIOToolsMixin + ProjectManagementMixin + ExternalToolsMixin | 0.5 day | Open | — |
| Wire MCPClientMixin into ChatAgent | 2 hours | Open | — |
| MCP API endpoints (GET/POST/DELETE /api/mcp/servers) | 0.5 day | Open | — |
| MCP Settings UI (server list, add/remove, on/off toggles) | 2 days | Open | — |
| Pre-configure 3 MCP servers (Playwright, Brave Search, Fetch) | config | Open | — |
| Write confirmation popup (SSE → modal → yes/no) | 2 days | Open | #438 |
| Code block component (syntax highlighting, copy, line numbers) | 0.5 day | Open | — |
| Tool execution card (collapsible, tool/args/result/duration) | 0.5 day | Open | — |
| Agent capabilities discovery API | — | Open | #440 |
| Agent registry and selection UI | — | Open | #612 |

**Already shipped:** #439 (cooperative cancellation), #441 (tool arg streaming),
#442 (cross-platform shell). **Already exists:** SQLite session persistence
(DatabaseMixin + SessionManager with JSON storage and 30-day TTL).

### SDK Refactors Required

**3.1 FileIOToolsMixin graceful degradation:**

```python
# hasattr() guards on _validate_python_syntax
# Call sites in file_io.py: read_file (2), write_file (1), edit_file (1), edit_python_file (1)
if hasattr(self, '_validate_python_syntax'):
    validation = self._validate_python_syntax(content)
    result["is_valid"] = validation["is_valid"]
else:
    result["file_type"] = "python"  # tag it, skip validation
```

**3.2 ExternalToolsMixin conditional registration:**

```python
def register_external_tools(self):
    if os.environ.get("PERPLEXITY_API_KEY"):
        # register search_web only
    # skip search_documentation entirely (developer-only)
```

**3.3 Tool name collision:** Guard in `FileSearchToolsMixin` — skip `read_file`/`write_file`
if `FileIOToolsMixin` is in the MRO:

```python
if not any(c.__name__ == 'FileIOToolsMixin' for c in type(self).__mro__):
    # register read_file, write_file
```

### MCP Integration

Ship 3 pre-configured servers in `~/.gaia/mcp_servers.json`:

| Server | Package | Notes |
|--------|---------|-------|
| Playwright | `@playwright/mcp` (v0.0.68) | Browser control, uses user's browser |
| Brave Search | `@brave/brave-search-mcp-server` (v2.0.75) | Free 2K queries/mo, requires API key |
| Fetch | `@modelcontextprotocol/server-fetch` | URL→markdown, no API key |

**MCP API endpoints (3 only):**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/mcp/servers` | GET | List servers with status |
| `/api/mcp/servers` | POST | Add/update server config |
| `/api/mcp/servers/{name}` | DELETE | Remove a server |

**Additional verified MCP servers (user-installable):**

| Server | Package | Verified? |
|--------|---------|-----------|
| Gmail | `gmail-mcp-server` (v1.0.30) | Yes |
| Google Calendar | `@cocal/google-calendar-mcp` (v2.6.1) | Yes |
| Spotify | `@iflow-mcp/iceener-spotify` | Yes |
| Outlook | `outlook-mcp-server` | Unverified |
| GitHub | `@modelcontextprotocol/server-github` | Yes |

### Security: Tool Confirmation

Two tiers:
- **Auto-approve:** All read-only tools (read_file, search_*, list_files, query_documents,
  RAG read tools, run_shell_command, Playwright read-only browser ops, Brave Search, Fetch)
- **Confirm first:** All write tools (write_file, edit_file) and all unknown MCP tools

Full MCP tool classification (auto-approve vs confirm whitelists) and web search priority
logic are defined in [security-model.md](security-model.md) — the canonical reference
for all guardrail decisions.

### Native vs MCP Decisions

| Capability | Decision | Rationale |
|-----------|----------|-----------|
| File read/write | **NATIVE** | Must be instant and offline |
| Shell commands | **NATIVE** | Must work without Node.js |
| Document Q&A | **NATIVE** | Already built |
| Web search | **MCP primary** + native fallback | Brave free tier primary, Perplexity fallback |
| Browser | **MCP** (Playwright) | Avoids ~400MB Chromium |
| Email/Calendar | **MCP** | External service, requires OAuth |
| Screenshots | **NATIVE** (later) | PIL/mss, no deps |
| Guardrails | **NATIVE** | Safety-critical |

**Rules:**
1. Never MCP for file/shell ops — must work without npx
2. MCP is fine for browser/web — these need external services anyway
3. Never duplicate: if native exists, don't also enable MCP equivalent

### Frontend Components (Phase A)

**Code Block** — used by read_file, search_code, run_shell_command:
- Syntax-highlighted with language detection, copy button, line numbers

**Tool Execution Card** — used by all tools:
- Collapsible (collapsed by default), shows tool name, args, result, duration
- Falls back to formatted JSON for any structured result

Everything else renders as JSON inside a Tool Execution Card until we know what users need.

---

## 5. Phase B — Memory & Onboarding

**GitHub:** v0.20.0 — Agent Memory & Bootstrap

**Goal:** Agent remembers context across sessions and acts proactively.

**Key rename:** ChatAgent → **GaiaAgent**. The main consumer agent is the core product,
not just a chatbot. All new code and plans should use GaiaAgent.

### 5.1 Persistent Memory System

**GitHub issues:** #542 (MemoryStore), #543 (MemoryMixin), #556 (Bootstrap),
#574 (Dashboard API), #575 (Dashboard UI), #576 (temporal), #577 (context scoping),
#578 (wire GaiaAgent), #579 (tests)

**Architecture:**
```
~/.gaia/memory/
  ├── memory.md          # Long-term facts (agent-maintained)
  ├── personality.md     # System prompt customization (user-editable)
  └── daily/
      └── 2026-04-01.md  # Conversation summaries + key facts
```

**Design decisions:**
- Markdown on disk — human-readable, git-trackable
- RAG integration — existing RAG indexes memory files automatically (GAIA advantage)
- **No context compaction** — compaction loses critical information (OpenClaw's biggest
  failure was losing safety instructions during compaction). Instead, the memory system
  handles long conversations: important context is offloaded to persistent storage and
  retrieved via RAG when needed. The memory system IS the solution to long conversations,
  not summarization/pruning.
- User-editable personality — `personality.md` loaded at session start
- **Dynamic tool loading** — tools loaded based on conversation context using memory,
  not statically compressed. Memory system informs which tools to activate per session.

### 5.1.1 Personality Recipes

The personality system goes beyond a text editor. The Agent UI configuration dashboard
offers pre-built personality recipes that provide fun, interesting variety:

- **Professional** — concise, structured, business-appropriate
- **Creative** — expressive, uses analogies, explores ideas
- **Technical** — precise, includes code examples, references specs
- **Friendly** — warm, conversational, encouraging
- **Minimalist** — terse, bullet points, no filler
- Plus user-contributed recipes via the skill marketplace

Users can browse, preview, and apply recipes from the configuration dashboard,
or edit `personality.md` directly for full control.

### 5.1.2 Agent UI Dashboards

The Agent UI has two distinct dashboard panels:

**Configuration Dashboard** — what the agent CAN do:
- Personality editor with recipe browser
- MCP server management (add/remove/toggle)
- Skill management (install/remove/configure via SKILL.md)
- Tool enablement per context
- Messaging adapter settings
- Model selection and preferences

**Observability Dashboard** — what the agent DID:
- Audit trail (all tool executions with who/what/when/result)
- Agent activity timeline
- Memory browser (view/edit stored knowledge)
- Token usage and cost savings telemetry
- Session history and conversation analytics
- Tool execution history with success/failure rates

### 5.2 Email & Calendar via MCP

**GitHub issues:** #660 (email/calendar), #663 (daily briefs)

Gmail and Calendar are Tier 1 use cases — too important for browser automation.
The primary integration path uses dedicated MCP servers, the same approach Claude
Code uses for its Gmail and Google Calendar integrations:

| Service | MCP Server | Auth | Status |
|---------|-----------|------|--------|
| Gmail | `gmail-mcp-server` (v1.0.30) | OAuth2 | Verified on npm |
| Google Calendar | `@cocal/google-calendar-mcp` (v2.6.1) | OAuth2 | Verified on npm |
| Outlook | `outlook-mcp-server` | OAuth2 | Unverified |
| Outlook (Phase 2) | MS Graph API native | OAuth2 via MSAL | Planned |

These ship as pre-configured options in the MCP Settings UI. User enables them,
completes OAuth once, and the agent has structured API access to email and calendar.
Browser automation (Playwright) remains available as a fallback for providers without
MCP servers, but is not the primary path for Gmail/Outlook.

See [email-calendar-integration.md](email-calendar-integration.md) for the full plan
including meeting notes capture with speaker diarization.

### 5.3 Proactive Agent / Heartbeat Scheduler

**Note:** The heartbeat scheduler and autonomy engine are v0.23.0 deliverables (Phase C),
not Phase B. They are described here for context since memory is a prerequisite for
personalized proactive behavior. See [autonomy-engine.md](autonomy-engine.md) for the
full plan and Phase C (§6) for the milestone mapping.

### 5.4 Onboarding & First-Run Experience

**GitHub issues:** #466 (system scanner), #467 (onboarding agent), #468 (setup executor),
#469 (first-run detection), #470 (onboarding wizard), #471 (guided first task),
#597 (first-run setup wizard)

**State machine:**
```
Launch → Check State → [All Ready?] → Direct to Chat
                     → [Not Ready?] → Onboarding Wizard → Chat
                     → [Power User?] → Skip → Chat
```

**State checks on launch:**
- `lemonade_installed` / `lemonade_running`
- `model_available` / `embedding_model_available`
- `first_run` (check `~/.gaia/chat/initialized`)

**Onboarding flow:**
1. Welcome screen with value prop
2. Check/start Lemonade Server
3. Download chat model (progress bar)
4. Download embedding model (progress bar)
5. Ready to chat

**Error states:**

| State | UI Treatment |
|-------|-------------|
| Lemonade not installed | Link to download + install instructions |
| Lemonade not running | "Start Lemonade" button + tray hint |
| Model not loaded | Progress bar during load |
| Model download failed | Retry + disk space check |
| Out of memory | "Try smaller model" suggestion |

### 5.5 What to Skip/Defer

| Feature | Decision | Reasoning |
|---------|----------|-----------|
| Self-modifying agent | **Skip** | Security nightmare |
| 22+ messaging adapters | **Defer to v0.23.0** | 3 max; desktop UI primary |
| ClawHub skill marketplace | **Skip** | MCP ecosystem is the marketplace |
| Model failover chain | **Skip** | Privacy risk; user restarts locally |
| Multi-agent teams | **Defer** | RoutingAgent exists |

---

## 6. Phase C — Autonomous Agent Infrastructure

**GitHub:** v0.23.0 — Autonomous Agent Infrastructure

**Goal:** GAIA operates as an always-on background service with messaging adapters.

### 6.1 System Tray App + Background Service

**GitHub issues:** #643 (system tray + background service), #415 (tray core),
#416 (agent manager), #417 (marketplace), #418 (permissions), #419 (dashboard),
#420 (agent terminal), #421 (notifications), #422 (interactive interface),
#423 (auto-start), #424 (testing/CI)

### 6.2 Messaging Adapters

**GitHub issues:** #635 (Telegram, Discord, Slack), #693 (Signal — Phase 1 priority)

**Detailed plan:** See [messaging-integrations-plan.md](messaging-integrations-plan.md)

Phase 1: **Signal** (privacy-first priority), Telegram, Discord, Slack — all work without
a public URL (local-first compatible). WhatsApp deferred (requires public webhook,
business verification, per-message cost).

### 6.3 Unified Communication Hub

**GitHub issue:** #703

All messaging channels (SMS, email, Signal, Telegram, Discord, Slack) converge into a
single prioritized feed in the Agent UI. The agent reads everything, ranks by urgency,
and surfaces what matters. Features: intelligent snooze (deadline-aware), pre-drafted
responses, cross-channel awareness ("they texted about the question they emailed"),
auto-created to-do lists from messages.

This is the consumer-facing integration point — users interact with one feed, not 5 apps.

### 6.4 Use Case Agents (from strategy Tier 1-2)

| Agent | GitHub Issue | Priority |
|-------|-------------|----------|
| Email triage | #645 | Tier 1 |
| Home automation (P0) | #705, #646 | Tier 1 |
| Calendar management | #662 | Tier 2 |
| Infrastructure monitoring | #665 | Tier 2 |
| Financial tracking | #664 (v0.21.0) | Tier 2 |
| Daily briefs | #663 | Tier 2 |

### 6.4 Autonomy Primitives

| Component | GitHub Issue |
|-----------|-------------|
| Dangerous mode (opt-in guardrail bypass) | #559 |
| Agent Activity feed | #558 |
| One-shot delayed execution | #560 |

---

## 7. Phase D — Agent Hub & Service Integration

**GitHub:** v0.24.0 — Agent Hub, Service Integration & Onboarding

### Key Issues

| Component | GitHub Issue |
|-----------|-------------|
| Agent Manifest (declarative metadata) | #462 |
| Dynamic Agent Registry (plugin discovery) | #463 |
| Capability-based routing + orchestration | #464 |
| Agent Lifecycle Manager | #465 |
| Setup executor (progressive install) | #468 |
| Auto-install MCP servers during onboarding | #474 |
| Health dashboard (`gaia status`) | #472 |
| Model Manager UI | #644 |
| Skill marketplace (format, tiers, AMD Verified) | #647 |
| OEM bundling framework | #648 |
| Cost savings telemetry | #649 |
| ServiceIntegrationMixin | #545 |
| HTTPToolsMixin (reusable REST API tool) | #480 |

---

## 8. Resource Budget & Context Management

### Token Budget (32K context model)

| Component | Tokens | % of 32K |
|-----------|--------|----------|
| System prompt | ~2,000 | 6% |
| Tool descriptions (20 native) | ~8,000 | 25% |
| MCP tool descriptions (10 max) | ~4,000 | 12% |
| Conversation history | ~12,000 | 38% |
| Current query + RAG context | ~6,000 | 19% |
| **Total** | **~32,000** | **100%** |

**Cap MCP tools at 10.** Tool description compression (P1 backlog) could reduce
tool prompt from ~12K to ~3K tokens, freeing 28% more context for conversation.

### Memory Budget

| Component | Memory |
|-----------|--------|
| Base GAIA (ChatSDK + LLM client) | ~200MB |
| RAG index (per 100 documents) | ~50MB |
| Electron app | ~150MB |
| Per MCP server connection | ~20-30MB |
| **Typical total** | **~500MB** |

**Recommendation:** 8GB RAM minimum, 16GB recommended for RAG + heartbeat + MCP servers.

### LLM Model Requirements by Capability

| Capability | Minimum Model | Recommended Model |
|-----------|---------------|-------------------|
| Basic chat | Qwen3-0.6B | Qwen3-Coder-30B-A3B |
| Tool use (file I/O, shell) | Qwen3-Coder-30B-A3B | Same |
| RAG + multi-step reasoning | Qwen3-Coder-30B-A3B | Same |
| Vision/VLM | Qwen3-VL-4B | Same |
| Heartbeat (cheap checks) | Qwen3-0.6B | Qwen3-0.6B (save tokens) |

---

## 9. Privacy-First UX

### Visual Privacy Indicators

- **Status bar:** "Local" indicator always visible
- **Network monitor:** Confirm no outbound connections
- **Data location:** Settings shows where data is stored
- **Export/Delete:** Easy data export and secure deletion
- **No telemetry by default:** Opt-in only analytics

### Settings > Privacy Panel

```
Privacy & Data
├─ Chat history: ~/.gaia/chat/sessions.db
├─ Document index: ~/.gaia/chat/documents/
├─ Memory: ~/.gaia/memory/
└─ Size: 234 MB

[Export All Data]  [Clear All Data]

Analytics (helps improve GAIA)
☐ Send anonymous usage statistics
  • No conversation content, ever
  • Only: app version, crash reports, feature usage counts
```

---

## 10. Error Handling & Degradation

| Error | Detection | UI Response |
|-------|-----------|-------------|
| Lemonade not running | Health check | Full-screen "Start Lemonade" prompt |
| Model not loaded | Health check | "Loading model..." progress or "Download" |
| Model crashed | SSE drops, 500 | "Model error. Restart?" button |
| Out of memory | OOM error | "Try smaller model or close other apps" |
| Document indexing failed | Upload error | Toast with error + retry |
| MCP server connection failed | Status dot red | Agent proceeds without MCP tools |
| No Node.js | npx not found | "Node.js required for MCP servers" hint |
| No API keys | Config check | Brave shows "API key required" in Settings |
| Brave quota exhausted | Rate limit | Falls back to Fetch MCP or Playwright |
| Database locked | SQLite busy | Auto-retry with exponential backoff |

**Offline mode:** All native tools (file I/O, shell, RAG, file search) work fully offline.
Only MCP tools that connect to external services require network.

---

## 11. SDK Agent & Mixin Audit

**Note:** ChatAgent is renamed to **GaiaAgent** in v0.20.0 (#696). References below
use the current name for pre-v0.20.0 context.

### Agents Worth Using in Agent UI

| Agent | Verdict | Reasoning |
|-------|---------|-----------|
| **ChatAgent → GaiaAgent** | **Core** | This IS the product |
| **CodeAgent** | Cherry-pick 3 mixins | FileIOToolsMixin, ProjectManagementMixin, ExternalToolsMixin |
| **SDAgent** | VLM later | Image analysis high value; image gen niche |
| All others | **Skip for consumer UI** | B2B demos, zero consumer value |

### Mixins for ChatAgent

| Mixin | Phase | Notes |
|-------|-------|-------|
| **FileIOToolsMixin** | A | 4 of 5 tools (skip edit_python_file) |
| **ProjectManagementMixin** | A | 1 of 2 tools (skip create_project) |
| **ExternalToolsMixin** | A | 1 of 2 tools (skip search_documentation) |
| **MCPClientMixin** | A | All MCP connectivity |
| **MemoryMixin** | B | 5 tools + 3 lifecycle hooks |
| **VLMToolsMixin** | Backlog | Needs VRAM co-loading solution |
| CLIToolsMixin | Defer | Needs guardrails |
| Everything else | **Skip** | Developer-only |

### Current ChatAgent Tools (before Phase A)

| Tool | Mixin | Count |
|------|-------|-------|
| `run_shell_command` | ShellToolsMixin | 1 |
| `add_watch_directory` | FileToolsMixin | 1 |
| RAG tools (query, index, search, summarize, etc.) | RAGToolsMixin | 10 |
| `search_file`, `search_directory`, `read_file`, `search_file_content`, `write_file` | FileSearchToolsMixin | 5 |
| **Total** | | **17** |

---

## 12. Security as Competitive Moat

OpenClaw: 42,000 exposed instances, 1,184 malicious skills, 63+ CVEs.

| OpenClaw Vulnerability | GAIA's Answer |
|----------------------|---------------|
| Unrestricted shell access | Read-only command whitelist (ShellToolsMixin) |
| Malicious skills on ClawHub | Confirm-first for unknown MCP tools |
| No confirmation for destructive actions | Confirm-first popup for all writes |
| Prompt injection via messaging | Desktop UI primary — no untrusted input channels |
| 42,000 exposed instances | Local-only by default, no network daemon |
| Self-modifying agent | Not supported — by design |
| Context compaction loses safety | No compaction — memory + RAG replaces it entirely |

---

## 13. Strategy Alignment — Items Not Yet Covered

The following Track B items from the OpenClaw strategy doc need plan coverage:

### Windows-Native Installer

**GitHub milestone:** v0.17.2. **Issue:** #530 (desktop installer).
See [installer.mdx](installer.mdx) for detailed plan. MSI/MSIX, no WSL2/Docker/CLI.

### Hybrid Model Routing

**GitHub issue:** #632 (hybrid model routing: local + cloud task routing).
Route routine agent tasks (heartbeats, classification) to local cheap models,
complex reasoning to cloud frontier models. Saves 50-90% vs cloud-only.
This is the strategy doc's #1 cost proof point.

### Security Model (Comprehensive)

Covered in [security-model.md](security-model.md): sandboxed skill execution, audit
trail (#697), code signing, approval gates, credential vault (#698). Spans v0.18.2
through v0.24.0.

### OpenClaw Skill Compatibility Layer

Migration tool for OpenClaw SKILL.md files to run on GAIA. GitHub issue: #692 (v0.24.0).

### Skill Format Specification

Covered in [skill-format.md](skill-format.md): YAML frontmatter, domain-scoped
permissions, security tiers. GitHub issue: #691 (v0.24.0). Prerequisite for the
skill marketplace (#647).

---

## 14. Backlog

| Priority | Feature | GitHub Issue | Milestone |
|----------|---------|-------------|-----------|
| P0 | Full guardrails framework (risk tiers, allow-lists) | #438 | v0.18.2 |
| P0 | Rename ChatAgent → GaiaAgent across codebase | #696 | v0.20.0 |
| P0 | Configuration dashboard (personality, skills, MCP, tools) | #701 | v0.20.0 |
| P0 | Observability dashboard (audit trail, activity, memory browser) | #697 | v0.20.0 |
| P1 | Dynamic tool loading based on conversation context via memory | #688 | v0.20.0 |
| P1 | Personality recipes (pre-built, browseable, fun/interesting) | #687 | v0.20.0 |
| P1 | Screenshot + VLM | #460, #461 | v0.21.0 |
| P1 | Email/Calendar MCP (Tier 2 servers) | #660 | v0.20.0 |
| P1 | BrowserToolsMixin (Playwright native) | #458 | v0.20.0 |
| P1 | Merge CodeAgent file diff/tree to GaiaAgent | #695 | v0.20.0 |
| P1 | Audit trail (all tool executions to SQLite) | #697 | v0.23.0 |
| P1 | Encrypted credential vault | #698 | v0.23.0 |
| P2 | Feature flags per capability | — | — |
| P2 | Multi-modal rendering (images, audio inline) | #540 | v0.21.0 |
| P2 | Multi-agent task management | #677 | v0.20.0 |
| P3 | MCP auto-discovery | #474 | v0.24.0 |
| P4 | Computer Use (CUA) | #460, #544 | v0.21.0 |
| **P0** | **Voice-first interaction (enabling technology)** | #702 | v0.21.0 |
| P4 | Meeting notes capture with speaker diarization | #700 | v0.21.0 |
| P4 | Image generation | SDToolsMixin | v0.21.0 |

**Removed from backlog:** Context compaction — replaced by memory system + RAG approach.
The memory system handles long conversations by offloading to persistent storage, not
by summarizing/pruning (which loses critical information).

---

## 15. Validation Workflows

### Phase A (5 tests)

| # | User Says | Tools | Prerequisites |
|---|-----------|-------|---------------|
| 1 | "Read main.py and explain it" | read_file | None |
| 2 | "Create notes.txt with meeting notes" | write_file + confirm | None |
| 3 | "What files are in my project?" | list_files | None |
| 4 | "Search the web for AMD Ryzen AI specs" | Brave MCP or search_web | API key |
| 5 | "Go to github.com/amd/gaia, latest release?" | Playwright MCP | Node.js |

### Phase B (5 tests)

| # | Scenario | Features |
|---|----------|----------|
| 1 | "What did we discuss yesterday about the API redesign?" | Memory + RAG |
| 2 | Configure heartbeat for ~/projects/ every 30 min; add a file | Heartbeat + notifications |
| 3 | "Remember that I prefer TypeScript over JavaScript" | Memory write + retrieval |
| 4 | Edit personality.md to "Always respond in bullet points" | Personality loading |
| 5 | "Summarize this PDF and save key points to notes" | RAG + file write + memory |

---

## 16. Cross-Platform Requirements

| Capability | Windows | Linux | macOS |
|-----------|---------|-------|-------|
| Shell commands | cmd.exe (shell=True), Unix→Win mapping | /bin/sh | /bin/zsh |
| File operations | pathlib (handles separators) | Same | Same |
| MCP servers | npx (Node.js v18+) | Same | Same |
| File paths in tool args | Backslash `\` or forward `/` | Forward `/` | Forward `/` |

MCP servers require Node.js v18+. Without Node.js, native tools still work.

---

## 17. CLI Integration

```bash
# Launch
gaia chat                     # Interactive CLI
gaia chat ui                  # Desktop app (Electron)
gaia chat ui --browser        # Browser mode

# Documents
gaia chat docs list           # List indexed documents
gaia chat docs add file.pdf   # Add to global library
gaia chat docs remove <id>    # Remove from library

# Sessions
gaia chat sessions list       # List sessions
gaia chat sessions export <id>  # Export to markdown
gaia chat sessions delete <id>  # Delete session
```

CLI and UI share the same SQLite database (`~/.gaia/chat/gaia_chat.db`).

---

## 18. Success Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Time to first chat (new user) | < 5 minutes | Onboarding funnel |
| Time to first chat (returning) | < 10 seconds | App launch → first response |
| Document indexing | < 5 seconds per MB | Performance test |
| Streaming latency | < 200ms first token | Time from send to first chunk |
| Session retention | > 50% | Users who return within 7 days |
| Document usage | > 30% | Sessions that use RAG |

---

## 19. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Memory enables prompt injection | Medium | High | Agent-written only; user can review |
| Heartbeat runs unintended actions | Medium | High | Off by default; confirm-first for writes |
| Memory complexity underestimated | Medium | Medium | Memory + RAG approach avoids compaction complexity entirely |
| Feature creep (matching OpenClaw) | High | Medium | Strict scope; desktop UI primary |
| Local model quality insufficient | Medium | High | Hybrid routing; MoE models; AMD Silo AI |
| OpenAI accelerates OpenClaw | High | Low | Differentiate on security + AMD hardware |
| Tool count exceeds context budget | Medium | High | Cap MCP at 10; tool compression P1 |

---

## 20. Document Support

### RAG SDK Supported Formats

**Documents:** PDF, TXT, LOG, MD, Markdown, RST, CSV, JSON

**Code:** Python, Java, C/C++, C#, Go, Rust, Ruby, PHP, Swift, Kotlin, Scala,
JavaScript, TypeScript, JSX, TSX, Vue, Svelte, Astro, CSS, SCSS, SASS, LESS,
HTML, SVG, Shell, PowerShell, R, SQL

**Config:** YAML, XML, TOML, INI, ENV, Properties, Gradle, CMake

**Not yet supported:** DOCX (needs python-docx), XLSX (needs openpyxl), PPTX (needs python-pptx),
Images as standalone files (currently VLM only in PDFs)
