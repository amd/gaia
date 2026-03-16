# Autonomous Agent Infrastructure

**Date**: March 13, 2026
**Branch**: `kalin/autonomous-agent-infra`
**Foundation**: gaia-v2 `SharedAgentState` + `kalin/chat-ui` + `feature/chat-agent-file-navigation`

---

## Development Methodology: Test-Driven Development (TDD)

Each milestone follows a strict TDD cycle:

1. **Write tests first** — Define expected behavior before writing implementation code
2. **Run tests (expect failures)** — Confirm tests fail for the right reasons
3. **Implement the feature** — Write minimal code to make tests pass
4. **Run tests (expect passes)** — Validate implementation against tests
5. **Refactor** — Clean up while keeping tests green

This applies to both unit tests (mocked dependencies) and integration tests (real services). Tests serve as living documentation of expected behavior and catch regressions early.

---

## Milestone Overview

| Milestone | What | Effort | Depends On |
|-----------|------|--------|------------|
| [M1: Persistent Memory](#milestone-1-persistent-memory) | Any agent remembers across sessions | 3-4 days | — |
| [M2: Agent UI MCP Server](#milestone-2-agent-ui-mcp-server) | Agent controls the Agent UI programmatically via MCP | 4-5 days | M1 |
| [M3: Service Integration & Computer Use](#milestone-3-service-integration--computer-use) | Agent discovers APIs, integrates services, learns/replays browser workflows. API-first with computer use as fallback. | 6-8 days | M1 |
| [M4: Domain Tools](#milestone-4-domain-tools) | GitHub monitoring and domain-specific tool wrappers | 2-3 days | M1 |
| [M5: Scheduled Autonomy](#milestone-5-scheduled-autonomy) | Agent schedules its own recurring tasks via Agent UI MCP | 4-5 days | M1, M2, M3 |
| [M6: RAC Integration](#milestone-6-rac-integration) | Recursive agent spawning, specialist sub-agents, quality gates | 5-7 days | M1 |
| [M7: Self-Improving Agent](#milestone-7-self-improving-agent) | Agent builds its own tools, extracts skills from patterns, learns from outcomes | 6-8 days | M1, M6 |

**M1-M5 are core. M6-M7 are future milestones driven by real usage.**

---

## Design Philosophy: Extensible to Any Use Case

This architecture is **generic infrastructure, not a social media agent**. Social media marketing is the first use case, but the same building blocks support:

| Use Case | Skills Used | Key Capabilities |
|----------|------------|-----------------|
| Social media marketing | replay (LinkedIn post), api (Twitter API) | Preferences, scheduling, web search |
| Email triage | decision (categorize → act), api (Gmail API) | Preference learning, credential management |
| Document processing | api (Google Drive, Dropbox) | Memory recall, file tools |
| Code review | api (GitHub API), decision (review → approve/comment) | Pattern recall, scheduling |
| Calendar management | api (Google Calendar) | Preferences, scheduling |
| CRM updates | api (Salesforce), decision (lead scoring) | Memory, preferences |
| Research & reporting | web search, decision (filter → summarize) | Knowledge persistence, scheduling |

**Specialization happens at the prompt/strategy layer** (stored as `category="strategy"` insights in KnowledgeDB), **not in code**. The infrastructure never assumes a specific domain.

---

## What Already Exists

### gaia-v2 (`SharedAgentState` — partially working, needs fixes)

gaia-v2 built a large memory system (7+ databases, 2500+ lines) but it **barely stored anything in practice**. Conversation turns were auto-stored, but knowledge/insights/working memory depended entirely on the LLM choosing to call `remember()` / `store_insight()` tools — and it almost never did. The InsightEngine for auto-extraction existed but was never wired to run.

**What we'll reuse** (schemas are reasonable, implementation needs fixes):

| Component | What It Does | Status |
|-----------|-------------|--------|
| **MemoryDB** | Session-scoped working memory: key-value facts, file cache, conversation history with FTS5 | Partially working — recall uses `LIKE` (imprecise), FTS5 uses `OR` (too broad) |
| **KnowledgeDB** | Cross-session persistent: insights, preferences. FTS5 searchable. | Schema good, but never got populated because LLM didn't call tools |
| **`_sanitize_fts5_query()`** | FTS5 query sanitizer | Works but uses `OR` semantics — needs `AND` default |

**What we'll drop** (over-engineered, not needed until M6/M7):

| Component | Why Drop |
|-----------|---------|
| **SkillsDB** | Consolidate into KnowledgeDB with `category="skill"` + `metadata` JSON column |
| **ToolsDB** | Consolidate into KnowledgeDB with `category="tool"` — only needed in M7 |
| **AgentsDB** | Consolidate into KnowledgeDB with `category="agent"` — only needed in M6 |
| **LogsDB** | Captures every Python log to SQLite — too heavy, not useful for agent memory |
| **MasterPlan** | Hierarchical task tree — defer to M6 (RAC) |
| **AgentCallStack** | Recursion tracking — defer to M6 (RAC) |
| **InsightEngine** | Auto-extraction class — never wired up, replace with simpler auto-store |

### Current branch (`kalin/autonomous-agent-infra`)

| Component | What It Does |
|-----------|-------------|
| **Agent UI** | Desktop chat interface — all user interaction happens here |
| **Agent UI database** | SQLite at `~/.gaia/chat/gaia_chat.db` — stores sessions, messages (with role, content, timestamps, rag_sources, agent_steps, tokens), documents |
| **Agent UI REST API** | FastAPI at `localhost:4200` — sessions CRUD, chat streaming, documents, tunnel management, system status |
| **AgentMCPServer** | `src/gaia/mcp/agent_mcp_server.py` — generic MCP server that wraps any MCPAgent, dynamically registers tools via FastMCP |
| **Browser tools** | `src/gaia/agents/tools/browser_tools.py` — basic web interaction |
| **Web client** | `src/gaia/web/client.py` — HTTP content extraction |
| **Filesystem tools** | File discovery, browsing, tree view |
| **Playwright MCP** | Available as MCP server — navigate, click, fill, snapshot, screenshot |
| **Perplexity MCP** | Web search — already integrated as external service |

### Storage Architecture (Two Agent Databases, One UI Database)

```
~/.gaia/chat/gaia_chat.db          ← Agent UI owns this (accessed via MCP)
  sessions, messages, documents       Conversations, UI state
  scheduled_tasks (M5)                Scheduling

~/.gaia/workspace/                 ← Agent owns this (accessed directly)
  memory.db                           Working memory (session-scoped)
  knowledge.db                        Everything persistent (insights, skills, preferences, credentials)
```

**Consolidated: Two databases, not five.** gaia-v2 had separate SkillsDB, ToolsDB, AgentsDB files. We consolidate everything persistent into `knowledge.db` using the `category` field + a `metadata` JSON column for structured data (workflow steps, tool parameters, agent capabilities).

**Agent memory** (SharedAgentState) is accessed directly by the agent — it owns these databases.

**Agent UI** is accessed via its MCP server (M2). The agent never touches `gaia_chat.db` directly. Instead, the Agent UI exposes MCP tools for session management, conversation search, tunnel control, scheduling, and more. This gives the agent broad programmatic control over the UI while maintaining clean separation of concerns.

---

## Milestone 1: Persistent Memory

**Goal**: Any GAIA agent can remember across sessions by adding `MemoryMixin`.

### Known Issues from gaia-v2 (What to Fix)

The gaia-v2 `SharedAgentState` memory system was only partially working. The biggest problem: **almost nothing got stored**. Conversation turns were auto-saved, but knowledge and working memory were entirely LLM-driven — the agent had `remember()` and `store_insight()` tools but the LLM almost never called them.

| Issue | Root Cause in gaia-v2 | Fix in M1 |
|-------|----------------------|-----------|
| **Nothing stored** | Knowledge storage depended entirely on the LLM calling `store_insight()` / `remember()` tools. Local LLMs (Qwen) almost never called them. Auto-extraction (InsightEngine) existed but was never wired to run after conversations. | **Auto-store after each conversation**: After every `process_query()`, run a lightweight extraction pass that stores key facts, decisions, and preferences to KnowledgeDB. LLM tools are a supplement, not the only path. |
| **Poor recall relevance** | FTS5 uses `OR` semantics (`_sanitize_fts5_query` joins words with `OR`) — returns too many low-relevance results. Working memory uses `LIKE %query%` — even less precise. | Use FTS5 `AND` by default for tighter matching, fall back to `OR` only on zero results. Add `bm25()` ranking to FTS5 queries for relevance scoring. |
| **Context pollution** | Agent auto-injects 20 working memories + 5 knowledge insights into every system prompt (`agent.py:606-650`) — stale/irrelevant facts clutter context. | **Don't auto-inject everything**. On session start, inject only a curated summary (last few preferences, active strategies). The agent calls `recall()` explicitly for deeper context. |
| **Duplicate insights** | Every `store_insight()` creates a new row with a fresh UUID. No dedup check. Same insight stored repeatedly across sessions. | **Dedup on store**: Before inserting, FTS5-search for existing insights with similar content in the same category. If a >80% word overlap match exists, update its confidence/timestamp instead of creating a new row. |
| **Over-complex schema** | 7+ databases (SkillsDB, ToolsDB, AgentsDB, LogsDB, etc.), plans/task_events/conventions/learnings tables — most never exercised. | **Two databases total**: MemoryDB (working) + KnowledgeDB (persistent). Skills, tools, agents all stored as KnowledgeDB categories with a `metadata` JSON column. |
| **Confidence scores decorative** | Confidence updates on usage but FTS5 `rank` dominates retrieval order — confidence never influences what gets recalled. | Include confidence as a tiebreaker in recall queries: `ORDER BY rank, confidence DESC`. Decay confidence for insights not accessed in N days. |
| **Key-value working memory fragile** | Agent must pick good string keys (`"auth_approach"`) and recall uses `LIKE` search on keys+values. Bad keys = unfindable memories. | Keep key-value for explicit facts but add FTS5 to `active_state` table too. The `recall_memory()` tool searches both key match and FTS5 content match. |

### Usage

```python
class MyAgent(Agent, MemoryMixin):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.init_memory()

    def _register_tools(self):
        self.register_memory_tools()
```

### Memory Tools

```python
# Working memory (session-scoped)
remember(key, value, tags)
recall_memory(query, key, limit)
forget_memory(key)

# Knowledge (cross-session, persistent)
store_insight(category, content, domain, triggers, metadata)
recall(query, category, top_k)
store_preference(key, value)
get_preference(key)

# Conversation search (from MemoryDB)
search_conversations(query, limit)
```

### Consolidated KnowledgeDB Schema

One `insights` table handles everything that was previously split across SkillsDB, ToolsDB, and AgentsDB:

```sql
CREATE TABLE insights (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL,        -- "fact", "strategy", "event", "error_fix", "skill", "tool", "agent"
    domain TEXT,                    -- "social_media", "linkedin.com", "gmail", "product", etc.
    content TEXT NOT NULL,          -- Human-readable description
    confidence REAL DEFAULT 0.5,
    triggers TEXT,                  -- JSON array of trigger keywords
    metadata TEXT,                  -- JSON blob: workflow steps, tool params, agent capabilities, etc.
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    use_count INTEGER DEFAULT 0,
    created_at TIMESTAMP,
    last_used TIMESTAMP
);

CREATE TABLE credentials (
    id TEXT PRIMARY KEY,            -- "cred_gmail_oauth", "cred_twitter_api", etc.
    service TEXT NOT NULL,          -- "gmail", "twitter", "linkedin", "github", etc.
    credential_type TEXT NOT NULL,  -- "oauth2", "api_key", "bearer_token", "cookie"
    encrypted_data TEXT NOT NULL,   -- Encrypted JSON: {access_token, refresh_token, api_key, etc.}
    scopes TEXT,                    -- JSON array of permission scopes
    created_at TIMESTAMP,
    expires_at TIMESTAMP,           -- NULL if no expiry (e.g., API key)
    last_used TIMESTAMP,
    last_refreshed TIMESTAMP
);

CREATE TABLE preferences (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP
);
```

| What gaia-v2 stored separately | How it's stored now |
|---|---|
| Skill (SkillsDB) | `category="skill"`, `metadata={"type": "replay\|decision\|api", "steps": [...]}` |
| Tool (ToolsDB) | `category="tool"`, `metadata={"parameters": {...}, "code_path": "..."}` |
| Agent (AgentsDB) | `category="agent"`, `metadata={"capabilities": [...], "system_prompt": "..."}` |
| Event | `category="event"`, no metadata needed |
| Fact | `category="fact"`, no metadata needed |
| Strategy | `category="strategy"`, no metadata needed |
| Preference | Separate `preferences` table (key-value, not FTS) |
| Credential | Separate `credentials` table (encrypted, referenced by ID from skill metadata) |

### Auto-Store: Solving the "Nothing Gets Stored" Problem

After each `process_query()`, the MemoryMixin runs a lightweight extraction:

```python
def _auto_extract_after_query(self, user_input: str, assistant_response: str):
    """Extract and store key facts from the conversation automatically."""
    # 1. Always store the conversation turn (already existed in gaia-v2)
    self.memory.store_conversation_turn(session_id, "user", user_input)
    self.memory.store_conversation_turn(session_id, "assistant", assistant_response)

    # 2. Simple heuristic extraction (no LLM call needed):
    #    - If assistant mentioned a preference/decision, store it
    #    - If user stated a fact about their product/audience, store it
    #    - If a tool was called successfully, log it as an event
    #    This is pattern-matching on the conversation, not an LLM call.

    # 3. Optional: periodic LLM-based extraction (every N conversations)
    #    Ask the LLM: "What key facts, preferences, or decisions were made
    #    in this conversation that should be remembered for next time?"
    #    Store results as insights.
```

This ensures memory actually accumulates. LLM tool calls (`store_insight()`, `remember()`) supplement this, but the system doesn't depend on the LLM remembering to call them.

### Implementation

| Task | Details |
|------|---------|
| Port MemoryDB | From gaia-v2: `active_state`, `file_cache`, `tool_results`, `conversation_history` with FTS5. Drop `plans`, `plan_tasks`, `plan_task_events` tables. |
| Port KnowledgeDB | From gaia-v2: `insights` table + `preferences` table + new `credentials` table. Add `metadata TEXT` column. Drop separate `learnings` and `conventions` tables. |
| Fix FTS5 recall | Change `_sanitize_fts5_query` to use `AND` by default. Add `bm25()` ranking. Add fallback to `OR` on zero results. |
| Add insight deduplication | Before `INSERT`, search FTS5 for similar content in same category. If match exists with >80% word overlap, update existing row instead. |
| Add FTS5 to working memory | Add FTS5 virtual table + triggers on `active_state` so `recall_memory()` can do content search, not just `LIKE`. |
| Add confidence decay | On recall, decay confidence for insights not accessed in 30+ days (multiply by 0.9). On use, bump confidence. |
| Create `memory_mixin.py` | Thin mixin: `init_memory()`, `register_memory_tools()`, properties for `.memory`, `.knowledge`. |
| Add auto-extraction | `_auto_extract_after_query()` hook: auto-store conversation turns + heuristic fact extraction after each query. |
| Design tool descriptions | Write tool descriptions with examples embedded so the LLM knows when/how to use them. |
| SharedAgentState singleton | Thread-safe singleton holding MemoryDB + KnowledgeDB. No LogsDB, no MasterPlan, no AgentCallStack. |

### Tests

| Test | Type | What It Verifies |
|------|------|-----------------|
| `test_memory_db_store_recall` | Unit | `store_memory()` → `recall_memories()` returns it. Tags filter correctly. |
| `test_memory_db_fts5_search` | Unit | FTS5 search finds entries by content keyword match (not just `LIKE`). |
| `test_memory_db_fts5_and_semantics` | Unit | FTS5 with AND: searching "marketing strategy" finds entries containing both words, not entries with just "marketing" or just "strategy". |
| `test_memory_db_fts5_or_fallback` | Unit | When AND returns zero results, automatically falls back to OR and returns partial matches. |
| `test_memory_db_clear_working` | Unit | `clear_working_memory()` removes active_state, file_cache, tool_results. Conversation history optionally retained. |
| `test_knowledge_db_store_insight` | Unit | `store_insight()` persists. `recall()` finds it via FTS5. |
| `test_knowledge_db_categories` | Unit | Insights with different categories (event, fact, strategy, skill) are stored and filtered correctly. |
| `test_knowledge_db_metadata` | Unit | `store_insight(category="skill", metadata={"steps": [...]})` → `recall()` returns the metadata JSON intact. |
| `test_knowledge_db_category_filter` | Unit | `recall(query, category="skill")` returns only skills, not facts or strategies matching the same query. |
| `test_knowledge_db_dedup_similar` | Unit | Storing "GAIA supports NPU acceleration" then "GAIA supports NPU" → second call updates existing row instead of creating duplicate. |
| `test_knowledge_db_dedup_different` | Unit | Storing "GAIA supports NPU" then "LinkedIn posting schedule" → creates two separate entries (no false dedup). |
| `test_knowledge_db_dedup_cross_category` | Unit | Same content in different categories (e.g., "skill" vs "fact") are NOT deduped — they're separate entries. |
| `test_knowledge_db_preferences` | Unit | `store_preference()` / `get_preference()` round-trip. Update existing preference. |
| `test_knowledge_db_confidence_update` | Unit | Recalling an insight bumps its confidence. Storing updates `last_used`. |
| `test_knowledge_db_confidence_decay` | Unit | Insights not accessed for 30+ days have confidence decayed on next recall query. |
| `test_knowledge_db_bm25_ranking` | Unit | Recall returns more relevant results first (entry with query words in content ranks higher than entry with query words only in triggers). |
| `test_knowledge_db_usage_tracking` | Unit | `record_usage(insight_id, success=True)` increments success_count and updates confidence. |
| `test_knowledge_db_credentials_store` | Unit | `store_credential()` persists encrypted data. `get_credential()` retrieves it. |
| `test_knowledge_db_credentials_expiry` | Unit | Expired credentials are flagged. `get_credential()` returns `expired=True` for past-expiry creds. |
| `test_knowledge_db_credentials_update` | Unit | Refreshing a credential updates `encrypted_data`, `last_refreshed`, and optionally `expires_at`. |
| `test_shared_state_singleton` | Unit | Two calls to `get_shared_state()` return the same instance. |
| `test_shared_state_thread_safety` | Unit | Concurrent writes from multiple threads don't corrupt data. |
| `test_shared_state_two_dbs_only` | Unit | SharedAgentState creates exactly 2 DB files: `memory.db` and `knowledge.db`. No `skills.db`, `tools.db`, `agents.db`. |
| `test_shared_state_no_gaia_code_deps` | Unit | `shared_state.py` imports nothing from `gaia_code/` — it's agent-agnostic. |
| `test_memory_mixin_registers_tools` | Unit | Agent with MemoryMixin has `remember`, `recall_memory`, `store_insight`, `recall`, etc. in tool registry. |
| `test_auto_extract_stores_conversation` | Unit | After `process_query()`, conversation turns are automatically stored in MemoryDB. |
| `test_auto_extract_stores_facts` | Unit | After a conversation where user says "our audience is AI developers", a fact insight is auto-stored in KnowledgeDB. |
| `test_auto_extract_dedup` | Unit | Running auto-extract on similar conversations doesn't create duplicate insights. |
| `test_memory_persistence_across_sessions` | Integration | Create agent → store insight → destroy agent → create new agent → recall returns the insight. |
| `test_memory_session_isolation` | Integration | Working memory clears between sessions. Knowledge persists. |
| `test_memory_mixin_with_chat_agent` | Integration | ChatAgent + MemoryMixin can store/recall during a mocked `process_query()` loop. |

### Files

```
src/gaia/agents/base/
├── shared_state.py          # Ported from gaia-v2 (MemoryDB, KnowledgeDB, SharedAgentState only)
└── memory_mixin.py          # MemoryMixin + auto-extraction

tests/unit/
├── test_memory_db.py        # MemoryDB unit tests
├── test_knowledge_db.py     # KnowledgeDB unit tests (including skill/tool/agent categories + credentials)
├── test_shared_state.py     # Singleton + thread safety + two-DB-only
└── test_memory_mixin.py     # Mixin registration + auto-extraction

tests/integration/
└── test_memory_persistence.py  # Cross-session persistence
```

**Effort**: 3-4 days

---

## Milestone 2: Agent UI MCP Server

**Goal**: The Agent UI exposes an MCP server so the agent can programmatically control it — search past conversations, create sessions, manage tunnels, and more. This is the agent's interface to the UI, not direct database access.

### Why MCP (Not Direct DB Access)

The agent needs to do more than read conversations. It needs to:
- Create ngrok tunnels for remote access
- Create new sessions and send messages
- Search through past conversations
- Manage scheduled tasks (M5)
- Control UI settings

The Agent UI already has a full REST API for all of this. The MCP server wraps those endpoints as MCP tools, giving the agent a clean, stable interface. This also means:
- The Agent UI owns its database — the agent never touches `gaia_chat.db` directly
- The agent works the same whether the UI runs locally or remotely
- Future UI changes (schema migrations, new features) don't break agent tools

### Agent UI MCP Tools

```python
# ── Session Management ──────────────────────────────────────────────────
list_sessions(limit: int = 50, offset: int = 0) -> Dict
    # List all chat sessions with title, date, message count, model.

create_session(title: str, model: str = None, system_prompt: str = None) -> Dict
    # Create a new chat session.

get_session(session_id: str) -> Dict
    # Get session details including title, model, system prompt.

update_session(session_id: str, title: str = None, system_prompt: str = None) -> Dict
    # Update session title or system prompt.

delete_session(session_id: str) -> Dict
    # Delete a session and all its messages.

# ── Conversation Access ─────────────────────────────────────────────────
get_session_messages(session_id: str, limit: int = 100) -> Dict
    # Get messages for a session. Returns role, content, timestamp.

search_conversations(query: str, limit: int = 10) -> Dict
    # Full-text search across all sessions' messages and titles.
    # This is a NEW endpoint added to the Agent UI REST API.

export_session(session_id: str, format: str = "markdown") -> Dict
    # Export session as markdown or JSON.

# ── Tunnel Management ──────────────────────────────────────────────────
start_tunnel() -> Dict
    # Start ngrok tunnel for remote/mobile access. Returns URL and auth token.

stop_tunnel() -> Dict
    # Stop the active ngrok tunnel.

get_tunnel_status() -> Dict
    # Check if tunnel is active, get URL.

# ── System ─────────────────────────────────────────────────────────────
get_system_status() -> Dict
    # Server health, uptime, model info, connected clients.
```

### How It Works

The Agent UI MCP Server is a FastMCP server that runs alongside the Agent UI backend. It calls the Agent UI's REST API endpoints internally (localhost:4200):

```python
# src/gaia/ui/mcp_server.py
from mcp.server.fastmcp import FastMCP
import httpx

mcp = FastMCP("GAIA Agent UI")
UI_BASE = "http://localhost:4200"

@mcp.tool()
async def list_sessions(limit: int = 50, offset: int = 0) -> dict:
    """List all chat sessions."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{UI_BASE}/api/sessions", params={"limit": limit, "offset": offset})
        return resp.json()

@mcp.tool()
async def search_conversations(query: str, limit: int = 10) -> dict:
    """Search past conversations for relevant context."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{UI_BASE}/api/sessions/search", params={"q": query, "limit": limit})
        return resp.json()

@mcp.tool()
async def start_tunnel() -> dict:
    """Start ngrok tunnel for remote access."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{UI_BASE}/api/tunnel/start")
        return resp.json()
```

### Agent UI REST API Additions

One new endpoint is needed for conversation search (the rest already exist):

```python
# GET /api/sessions/search?q=<query>&limit=10
# Searches message content and session titles using LIKE or FTS5
# Returns matching sessions with relevant message snippets
```

### Implementation

| Task | Details |
|------|---------|
| Add search endpoint to Agent UI | `GET /api/sessions/search` — FTS across messages and session titles |
| Create `mcp_server.py` | FastMCP server wrapping Agent UI REST API endpoints as MCP tools |
| Register MCP server | Add to Agent UI startup (or separate process) |
| Add to agent | Agent connects to UI MCP server to access these tools |

### Tests

| Test | Type | What It Verifies |
|------|------|-----------------|
| `test_list_sessions_via_mcp` | Unit | Mock HTTP → `list_sessions()` returns sessions with title, date, message count. |
| `test_create_session_via_mcp` | Unit | Mock HTTP → `create_session("Test")` returns new session with ID. |
| `test_get_session_messages_via_mcp` | Unit | Mock HTTP → `get_session_messages(id)` returns ordered message list. |
| `test_search_conversations_via_mcp` | Unit | Mock HTTP → `search_conversations("NPU")` returns matching sessions with snippets. |
| `test_search_conversations_no_results` | Unit | Query with no matches returns empty list gracefully. |
| `test_export_session_markdown` | Unit | Mock HTTP → `export_session(id, "markdown")` returns formatted markdown. |
| `test_start_tunnel_via_mcp` | Unit | Mock HTTP → `start_tunnel()` returns URL and auth token. |
| `test_stop_tunnel_via_mcp` | Unit | Mock HTTP → `stop_tunnel()` returns success. |
| `test_get_tunnel_status_via_mcp` | Unit | Mock HTTP → `get_tunnel_status()` returns active/inactive status. |
| `test_get_system_status_via_mcp` | Unit | Mock HTTP → `get_system_status()` returns health info. |
| `test_update_session_via_mcp` | Unit | Mock HTTP → `update_session(id, title="New")` returns updated session. |
| `test_delete_session_via_mcp` | Unit | Mock HTTP → `delete_session(id)` returns confirmation. |
| `test_mcp_server_registers_all_tools` | Unit | FastMCP server has all expected tools registered. |
| `test_mcp_handles_ui_down` | Unit | When Agent UI isn't running, tools return clear "UI unavailable" error. |
| `test_mcp_handles_http_errors` | Unit | 404, 500 from UI API are translated to clear MCP tool errors. |
| `test_search_endpoint_finds_match` | Integration | Start Agent UI → create session with messages → `GET /api/sessions/search?q=NPU` returns it. |
| `test_search_endpoint_empty` | Integration | Start Agent UI → search for nonexistent content → returns empty list. |
| `test_mcp_e2e_session_lifecycle` | Integration | Via MCP: create session → get messages → export → delete. Full round-trip against running Agent UI. |
| `test_mcp_e2e_tunnel` | Integration | Via MCP: start tunnel → check status → stop tunnel. Against running Agent UI. |
| `test_agent_uses_mcp_to_recall` | Integration | Agent with UI MCP connection uses `search_conversations()` to find context from a past session during `process_query()`. |

### Files

```
src/gaia/ui/
├── mcp_server.py            # FastMCP server wrapping Agent UI REST API
└── routers/
    └── sessions.py          # Extended: add search endpoint

tests/unit/
└── test_agent_ui_mcp.py     # MCP tool unit tests (mocked HTTP)

tests/integration/
└── test_agent_ui_mcp_e2e.py # Integration tests against running Agent UI
```

**Effort**: 4-5 days

---

## Milestone 3: Service Integration & Computer Use

**Goal**: The agent can integrate with external services to automate workflows. **API-first**: when a service has an API, the agent discovers it, helps the user set it up, and uses it directly. When no API exists (or it's broken), computer use (browser automation) is the fallback. The agent learns user preferences through observation and correction.

### Capability Escalation Ladder

The agent follows a priority order for every service it needs to interact with:

```
1. API exists → Agent discovers it (web_search), guides user through setup, generates wrapper
2. No API    → Fallback to computer use (Playwright): learn workflow, replay it
3. API broke → Temporary computer use fallback while API issue is resolved
```

This means for Gmail, the agent first discovers the Gmail API, helps the user create OAuth credentials, and then uses the API directly. For a website with no API (like LinkedIn posting), it falls back to browser automation.

### Three Skill Types

Every learned skill has a `type` in its metadata that determines how it's executed:

#### 1. Replay Skills (Deterministic Browser Automation)

Linear sequence of browser actions with parameter substitution. Used when no API exists.

```python
# Stored in KnowledgeDB:
store_insight(
    category="skill",
    domain="linkedin.com",
    content="Post content on LinkedIn feed",
    metadata={
        "type": "replay",
        "steps": [
            {"step": 1, "action": "navigate", "target": "https://linkedin.com/feed/",
             "value": None, "screenshot": "skills/abc/step_1.png", "notes": "Go to feed"},
            {"step": 2, "action": "click", "target": "div.share-box-feed-entry__trigger",
             "value": None, "screenshot": "skills/abc/step_2.png", "notes": "Click compose"},
            {"step": 3, "action": "type", "target": "div.ql-editor",
             "value": "{content}", "screenshot": "skills/abc/step_3.png",
             "notes": "Type post content — {content} is substituted at replay time"},
            {"step": 4, "action": "click", "target": "button.share-actions__primary-action",
             "value": None, "screenshot": "skills/abc/step_4.png", "notes": "Click Post"}
        ],
        "parameters": ["content"],
        "tools_used": ["playwright"]
    }
)
```

#### 2. Decision Skills (Observation → Reasoning → Conditional Action)

Non-linear workflows where the agent must observe, reason, and choose among possible actions. Used for triage, review, classification tasks.

```python
# Stored in KnowledgeDB:
store_insight(
    category="skill",
    domain="gmail",
    content="Triage incoming emails based on user preferences",
    metadata={
        "type": "decision",
        "navigation": [
            {"step": 1, "action": "api_call", "target": "gmail.list_messages",
             "params": {"label": "INBOX", "max_results": 20}}
        ],
        "observe": {
            "extract": ["sender", "subject", "snippet", "labels", "date"],
            "context_recall": ["email preferences", "important contacts"]
        },
        "actions": {
            "archive": {"description": "Low-priority, no action needed",
                       "execute": {"action": "api_call", "target": "gmail.modify_message",
                                  "params": {"remove_labels": ["INBOX"]}}},
            "star": {"description": "Important, user should see this",
                    "execute": {"action": "api_call", "target": "gmail.modify_message",
                               "params": {"add_labels": ["STARRED"]}}},
            "reply_draft": {"description": "Needs response, draft a reply",
                           "execute": {"action": "api_call", "target": "gmail.create_draft",
                                      "params": {"body": "{generated_reply}"}}},
            "flag_urgent": {"description": "Time-sensitive, notify user immediately",
                           "execute": {"action": "api_call", "target": "gmail.modify_message",
                                      "params": {"add_labels": ["IMPORTANT"]}}}
        },
        "preference_rules": [
            {"rule": "Emails from {important_contacts} are always 'star'", "confidence": 0.9},
            {"rule": "Newsletter emails are always 'archive'", "confidence": 0.8},
            {"rule": "Emails mentioning 'deadline' or 'urgent' are 'flag_urgent'", "confidence": 0.7}
        ]
    }
)
```

#### 3. API Skills (Direct API Integration)

Preferred over browser automation when available. The agent discovers and sets up the integration.

```python
# Stored in KnowledgeDB:
store_insight(
    category="skill",
    domain="gmail",
    content="Gmail API integration for email management",
    metadata={
        "type": "api",
        "provider": "gmail",
        "credential_id": "cred_gmail_oauth",       # References credentials table
        "base_url": "https://gmail.googleapis.com",
        "capabilities": [
            "list_messages", "get_message", "modify_message",
            "create_draft", "send_message", "list_labels"
        ],
        "setup_guide": "OAuth2 with scopes: gmail.modify, gmail.compose",
        "wrapper_path": "~/.gaia/integrations/gmail_wrapper.py"
    }
)
```

### Agent-Driven API Discovery & Setup

When the agent needs to interact with a new service, it follows this process:

```
User: "I want you to manage my email"

Agent: → web_search("Gmail API setup OAuth2 Python")
       → Discovers: Gmail API exists, needs OAuth2
       → "I can integrate with Gmail directly via its API. You'll need to:
          1. Go to Google Cloud Console
          2. Create a project and enable Gmail API
          3. Download credentials.json
          Let me walk you through it, or I can do it via the browser."

User: "Walk me through it" (or "Do it for me")

Agent: → (If guiding): Step-by-step instructions with verification at each step
       → (If doing): learn_workflow to open Google Cloud Console, create project...
       → store_credential(service="gmail", type="oauth2", data={...})
       → store_insight(category="skill", metadata={"type": "api", ...})
       → "Gmail is set up! I can now read, triage, and draft replies."
```

### Preference Learning Loop

The agent learns user preferences through three signal types:

#### 1. Explicit Correction (Strongest Signal)

User overrides an agent decision → agent stores/updates a preference rule.

```
Agent: [archives email from boss]
User: "No, emails from my boss are always important"
Agent: → store_preference("email_rule_boss", "Emails from boss@company.com → star")
       → Updates decision skill's preference_rules: confidence = 0.95
       → "Got it. I'll always star emails from your boss."
```

#### 2. Implicit Confirmation (Moderate Signal)

Agent makes a decision, user doesn't correct it → bump confidence.

```python
# After each decision batch:
for decision in batch_decisions:
    if not user_corrected(decision):
        # Bump confidence on the rule that drove this decision
        rule.confidence = min(1.0, rule.confidence + 0.05)
```

#### 3. Behavior Observation (Learning Signal)

Watch user perform actions via computer use → extract patterns as preference rules.

```
Agent: [observing user in browser]
       → User archives 5 newsletter emails in a row
       → Agent detects pattern: "User archives emails with 'unsubscribe' in body"
       → store_insight(category="strategy", domain="email",
            content="Archive emails containing 'unsubscribe' link",
            confidence=0.6)  # Low initial confidence, grows with confirmation
```

### Tools

```python
# ── Web Search & Reading ───────────────────────────────────────────────
web_search(query: str) -> Dict
    # Search the web for current information. Wraps Perplexity MCP.
    # Used for: API discovery, trend research, troubleshooting.

read_webpage(url: str, extract: str = "text") -> Dict
    # Fetch a URL and extract clean content. Strips nav, ads, footers.
    # extract: "text" (main content), "links" (all links), "full" (everything).
    # Wraps existing gaia.web.client.WebClient.
    # Used for: deep research (read full articles after web_search).

# ── Service Integration ────────────────────────────────────────────────
discover_api(service: str) -> Dict
    # Search for API documentation and setup instructions for a service.
    # Uses web_search internally. Returns: has_api, auth_type, setup_steps.

setup_integration(service: str, credential_data: Dict) -> Dict
    # Store API credentials and create an API skill for a service.
    # Validates credentials work before storing.

# ── Credential Management ──────────────────────────────────────────────
store_credential(service: str, credential_type: str, data: Dict,
                 scopes: List[str] = None, expires_at: str = None) -> Dict
    # Encrypt and store credentials for a service.

get_credential(service: str) -> Dict
    # Retrieve credentials for a service. Warns if expired.

refresh_credential(service: str) -> Dict
    # Refresh OAuth2 tokens. Updates stored credential.

list_credentials() -> Dict
    # List all stored credentials (service + type only, no secrets).

# ── Workflow Learning (Computer Use) ───────────────────────────────────
learn_workflow(task_description: str, start_url: str) -> Dict
    # Open visible browser. User demonstrates. Agent records and stores as skill.

replay_workflow(skill_name: str, parameters: Dict[str, str]) -> Dict
    # Replay a learned workflow, substituting parameters.

list_workflows(domain: str = None, skill_type: str = None) -> Dict
    # List learned workflows. Filter by domain and/or type (replay, decision, api).

test_workflow(skill_name: str) -> Dict
    # Replay in visible mode to verify it still works.
```

### Observation (Computer Use)

1. Agent opens visible Playwright browser
2. User demonstrates the task (agent watches via snapshots, user narrates in chat)
3. At each step, agent captures: screenshot, DOM snapshot, LLM-interpreted intent
4. Steps stored as simple dicts (see Replay Skill format above)
5. Agent replays once to verify, stores in KnowledgeDB

### Replay

1. Look up skill from KnowledgeDB (`category="skill"`, get `metadata.steps`)
2. Determine type: `replay` → linear execution, `decision` → observe + reason, `api` → direct API call
3. For replay: open browser (headless for autonomous, visible for debugging), walk steps
4. For decision: fetch data (API or browser), recall preferences, LLM reasons over each item
5. For API: call API directly using stored credentials
6. If a step fails: take screenshot, ask LLM for alternative selector, try once
7. If that fails: tell user "this workflow needs re-teaching" (or "API credentials may be expired")
8. Record success/failure via `record_usage()`

### Action Detection During Observation

**Start with user narration** (simplest): user says "I clicked compose" in the Agent UI chat, agent snapshots the page and records the step.

Add snapshot diffing later if narration feels tedious.

### Storage

Uses KnowledgeDB's consolidated `insights` table + `credentials` table:

```python
# API-based skill
store_insight(
    category="skill",
    domain="gmail",
    content="Gmail email management via API",
    metadata={"type": "api", "credential_id": "cred_gmail_oauth", "capabilities": [...]},
)

# Browser-based replay skill
store_insight(
    category="skill",
    domain="linkedin.com",
    content="Post content on LinkedIn feed",
    metadata={"type": "replay", "steps": [...], "tools_used": ["playwright"]},
)

# Decision skill
store_insight(
    category="skill",
    domain="gmail",
    content="Triage incoming emails based on preferences",
    metadata={"type": "decision", "observe": {...}, "actions": {...}, "preference_rules": [...]},
)
```

Screenshots in `~/.gaia/skills/{insight_id}/`. API wrappers in `~/.gaia/integrations/`. No separate database needed.

### Implementation

| Task | Details |
|------|---------|
| Create `computer_use.py` | Observation loop, replay engine, Playwright MCP connection |
| Create `service_integration.py` | API discovery, credential management, integration setup |
| Create `web_search.py` | Wrap Perplexity MCP as `web_search` + wrap WebClient as `read_webpage` |
| ComputerUseMixin | Registers workflow tools (learn, replay, list, test) on any agent |
| ServiceIntegrationMixin | Registers service tools (discover_api, setup_integration, credentials) on any agent |
| Preference learning | Correction detection in auto-extract, confidence update loop |
| Decision workflow executor | Observation → recall preferences → LLM reasoning → conditional action |

### Tests

| Test | Type | What It Verifies |
|------|------|-----------------|
| **Web Search** | | |
| `test_web_search_returns_results` | Unit | Mock Perplexity → `web_search("AI trends")` returns structured results with sources. |
| `test_web_search_no_api_key` | Unit | Graceful error when `PERPLEXITY_API_KEY` not set. |
| `test_web_search_service_unavailable` | Unit | Graceful fallback when Perplexity MCP server isn't running. |
| **API Discovery & Integration** | | |
| `test_discover_api_finds_api` | Unit | Mock web_search → `discover_api("gmail")` returns `{has_api: True, auth_type: "oauth2", ...}`. |
| `test_discover_api_no_api` | Unit | Mock web_search → `discover_api("some-niche-site")` returns `{has_api: False, fallback: "computer_use"}`. |
| `test_setup_integration_stores_skill` | Unit | `setup_integration("gmail", creds)` creates both a credential and an API skill in KnowledgeDB. |
| `test_setup_integration_validates_creds` | Unit | Invalid credentials → error returned, nothing stored. |
| **Credential Management** | | |
| `test_store_credential_encrypts` | Unit | Stored credential data is encrypted at rest. Raw tokens not visible in DB. |
| `test_get_credential_decrypts` | Unit | Retrieved credential contains decrypted data ready for use. |
| `test_credential_expiry_warning` | Unit | `get_credential()` for an expired credential returns data + `expired=True` flag. |
| `test_refresh_credential_oauth2` | Unit | Mock OAuth2 refresh → new access token stored, `expires_at` updated. |
| `test_list_credentials_no_secrets` | Unit | `list_credentials()` returns service names and types but NOT actual tokens. |
| `test_credential_referenced_by_skill` | Unit | API skill's `metadata.credential_id` correctly references a stored credential. |
| **Replay Workflows (Computer Use)** | | |
| `test_learn_workflow_stores_skill` | Unit | Mock Playwright → `learn_workflow()` stores a skill with `type="replay"` in KnowledgeDB. |
| `test_learn_workflow_captures_screenshots` | Unit | Screenshots saved to `~/.gaia/skills/{id}/step_N.png`. |
| `test_learn_workflow_step_format` | Unit | Each step has required fields: step, action, target, value, screenshot, notes. |
| `test_replay_workflow_executes_steps` | Unit | Mock Playwright → `replay_workflow()` calls navigate, click, type in correct order. |
| `test_replay_workflow_substitutes_params` | Unit | `{content}` in step value is replaced with provided parameter. |
| `test_replay_workflow_records_success` | Unit | On successful replay, `record_usage(success=True)` is called on KnowledgeDB. |
| `test_replay_workflow_handles_failure` | Unit | When Playwright click fails, agent takes screenshot and attempts one LLM-suggested alternative. |
| `test_replay_workflow_gives_up` | Unit | When both primary and alternative selectors fail, returns clear error and records `success=False`. |
| **Decision Workflows** | | |
| `test_decision_workflow_observes` | Unit | Decision skill fetches data (API or browser), extracts specified fields. |
| `test_decision_workflow_recalls_preferences` | Unit | Decision execution calls `recall()` with the `context_recall` queries from metadata. |
| `test_decision_workflow_applies_rules` | Unit | Email matching "newsletter" rule → action is "archive". Email from boss → action is "star". |
| `test_decision_workflow_llm_fallback` | Unit | Email matching no rule → LLM reasons about it and chooses an action. |
| `test_decision_workflow_logs_decisions` | Unit | Each decision logged as an event insight with the chosen action and reasoning. |
| **Preference Learning** | | |
| `test_explicit_correction_stores_rule` | Unit | User corrects "archive" → "star" → new preference rule stored with high confidence. |
| `test_explicit_correction_updates_existing` | Unit | Correcting same category again updates existing rule, doesn't create duplicate. |
| `test_implicit_confirmation_bumps_confidence` | Unit | Uncorrected decisions bump the driving rule's confidence by 0.05 (capped at 1.0). |
| `test_behavior_observation_extracts_pattern` | Unit | After observing user archive 5 similar emails, agent stores a pattern rule with low initial confidence. |
| `test_preference_rules_influence_decisions` | Unit | Decision workflow with stored "boss = star" rule applies it without LLM call. |
| **Workflow Listing & Testing** | | |
| `test_list_workflows_filters_domain` | Unit | `list_workflows(domain="linkedin.com")` returns only LinkedIn workflows. |
| `test_list_workflows_filters_type` | Unit | `list_workflows(skill_type="api")` returns only API skills, not replay/decision. |
| `test_list_workflows_all` | Unit | `list_workflows()` with no filters returns all skill-category insights. |
| `test_test_workflow_uses_visible_browser` | Unit | `test_workflow()` replays in visible (non-headless) mode. |
| **Mixin Registration** | | |
| `test_computer_use_mixin_registers_tools` | Unit | Agent with ComputerUseMixin has `learn_workflow`, `replay_workflow`, `list_workflows`, `test_workflow`. |
| `test_service_integration_mixin_registers_tools` | Unit | Agent with ServiceIntegrationMixin has `web_search`, `discover_api`, `setup_integration`, credential tools. |
| **Integration Tests** | | |
| `test_playwright_connection` | Integration | ComputerUseMixin successfully connects to Playwright MCP server. |
| `test_learn_and_replay_html_form` | Integration | Serve a local HTML form → teach agent to fill it (mocked narration) → replay with different values → verify form submission. |
| `test_workflow_persists_across_sessions` | Integration | Learn a workflow → destroy agent → create new agent → workflow is in `list_workflows()`. |
| `test_screenshot_cleanup` | Unit | When a skill is deleted, its screenshot directory is also removed. |
| `test_api_first_fallback_to_browser` | Integration | `discover_api("no-api-site")` returns no API → agent falls back to `learn_workflow()`. |
| `test_credential_persistence` | Integration | Store credential → restart agent → credential still retrievable and usable. |
| `test_web_search_live` | Integration | Hit real Perplexity API (skip if no API key). Verify response has answer + sources. |

### Files

```
src/gaia/agents/base/
├── computer_use.py             # M3: ComputerUseMixin + learn/replay/list/test
└── service_integration.py      # M3: ServiceIntegrationMixin + API discovery + credentials

src/gaia/agents/tools/
└── web_search.py               # M3: web_search @tool (wraps Perplexity MCP)

tests/unit/
├── test_computer_use.py        # Replay & decision workflow unit tests (mocked Playwright)
├── test_service_integration.py # API discovery, credentials, preference learning
└── test_web_search.py          # Web search tool unit tests

tests/integration/
├── test_computer_use_e2e.py    # Integration tests (local HTML server + Playwright)
├── test_service_integration_e2e.py  # Credential persistence, API-first fallback
└── test_web_search_live.py     # Live Perplexity API tests

tests/fixtures/
└── test_form.html              # Simple HTML form for computer use integration tests
```

**Effort**: 6-8 days

---

## Milestone 4: Domain Tools

**Goal**: Lightweight domain-specific tools that don't require service integration. Just `@tool` functions wrapping public APIs.

### GitHub Monitoring

```python
@tool
def check_github(repo: str, since_days: int = 7) -> Dict[str, Any]:
    """Check a GitHub repo for recent releases, PRs, and activity."""
    # GitHub REST API, no auth needed for public repos
```

### End-to-End Scenario

```
DAY 1 — Setup (Agent UI conversation):

User: "I want to post about GAIA on LinkedIn and Twitter"
Agent: "Let me find the best way to integrate with each service."
       → discover_api("linkedin") → has API but OAuth is complex
       → discover_api("twitter") → Twitter/X API exists
       → "LinkedIn: I'll learn the browser workflow. Twitter: I can set up API access."
       → learn_workflow("post on LinkedIn", "https://linkedin.com")  (replay skill)
       → setup_integration("twitter", {api_key: ...})               (API skill)
User: "Our audience is AI developers. Technical but friendly tone."
Agent: → store_insight(category="strategy", ...)
       → store_preference("brand_voice", ...)
       (Also auto-extracted by MemoryMixin after the conversation)

DAY 2+ — Usage:

User: "What should we post today?"
Agent: → recall("marketing strategy")
       → web_search("trending AI developer tools March 2026")
       → check_github("amd/gaia")
       → Generates draft using brand voice from memory
       → "Here's what I'd post. Want me to publish?"
User: "Post it"
Agent: → replay_workflow("post_on_linkedin", {"content": draft})    (browser)
       → twitter_api.create_tweet(draft)                            (API)
       → store_insight(category="event", content="Posted on LinkedIn + Twitter: ...")
```

### Implementation

| Task | Details |
|------|---------|
| `check_github` tool | Simple GitHub REST API call (~30 lines) |
| Documentation | Short guide: "Teaching your agent new skills" |

### Tests

| Test | Type | What It Verifies |
|------|------|-----------------|
| `test_check_github_releases` | Unit | Mock GitHub API → `check_github("amd/gaia")` returns recent releases with version, date, highlights. |
| `test_check_github_no_releases` | Unit | Repo with no recent releases returns empty list, not error. |
| `test_check_github_invalid_repo` | Unit | Bad repo name returns clear error. |
| `test_check_github_rate_limit` | Unit | Handles GitHub API 403 rate-limit gracefully. |
| `test_check_github_live` | Integration | Hit real GitHub API for `amd/gaia` (skip if no network). Verify response structure. |
| `test_tools_register_on_agent` | Unit | Agent with domain tools has `check_github` in tool registry. |
| `test_e2e_strategy_recall` | Integration | Store strategy in KnowledgeDB → invoke agent with "what should we post?" → verify agent calls `recall`, `web_search`, `check_github` in its tool usage. |

### Files

```
src/gaia/agents/tools/
└── github_monitor.py        # check_github @tool

tests/unit/
└── test_github_monitor.py

tests/integration/
└── test_domain_tools.py     # Live API tests + e2e scenario
```

**Effort**: 2-3 days

---

## Milestone 5: Scheduled Autonomy

**Goal**: The agent can schedule its own recurring tasks. The Agent UI backend manages the timers — no OS cron, no external scheduler. The agent creates and manages schedules **through the Agent UI MCP Server** (M2), which calls the Agent UI's scheduling REST API.

### How It Works

```
User: "Check for trends every morning and suggest posts"
Agent: → schedule_task("morning_trends", "every 24h",
           "Search for trending AI topics, check GAIA releases, and suggest posts")
       → (This MCP tool call hits the Agent UI's scheduling API)
       → Agent UI backend starts a Python asyncio timer
       → When timer fires: Agent UI sends the stored prompt through normal
         agent processing (same as user sending a message)
       → Agent executes with full memory + tools
       → Results stored in KnowledgeDB as events
       → User sees results next time they open a session
         (or agent surfaces them proactively: "While you were away, I found...")
```

### MCP Tools (Added to Agent UI MCP Server)

These are additional tools registered on the Agent UI MCP Server from M2:

```python
schedule_task(name: str, interval: str, prompt: str) -> Dict
    # Create a recurring scheduled task. interval: "every 6h", "every 24h", "daily at 9am"

list_schedules() -> Dict
    # List all scheduled tasks with next run time and last result.

cancel_schedule(name: str) -> Dict
    # Cancel a scheduled task.

pause_schedule(name: str) -> Dict
    # Pause without deleting.

resume_schedule(name: str) -> Dict
    # Resume a paused task.

get_schedule_results(name: str, limit: int = 5) -> Dict
    # View results from past runs of a scheduled task.
```

### Agent UI Backend Changes

Add to `gaia_chat.db`:

```sql
CREATE TABLE scheduled_tasks (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    interval_seconds INTEGER NOT NULL,
    prompt TEXT NOT NULL,
    status TEXT DEFAULT 'active',    -- active | paused | cancelled
    created_at TEXT,
    last_run_at TEXT,
    next_run_at TEXT,
    last_result TEXT                  -- JSON: {success, summary, timestamp}
);
```

Add REST API endpoints (consumed by the MCP server):

```python
POST /api/schedules              # Create scheduled task
GET  /api/schedules              # List all scheduled tasks
PUT  /api/schedules/{name}       # Update (pause/resume/cancel)
GET  /api/schedules/{name}/results  # Get past run results
DELETE /api/schedules/{name}     # Delete a scheduled task
```

Add a lightweight scheduler to the Agent UI's FastAPI startup:

```python
# On startup: load active tasks, start asyncio timers
# On timer fire: create a new session, send prompt through agent, store result
# On shutdown: cancel timers gracefully
```

### Proactive Recall

When a user starts a new session, the agent checks for pending scheduled results:

```python
# In agent system prompt or on session start:
recent_results = get_schedule_results("morning_trends", limit=3)
if recent_results:
    # Include in context: "While you were away, I ran morning_trends and found..."
```

### Implementation

| Task | Details |
|------|---------|
| Add `scheduled_tasks` table | Migration in Agent UI database |
| Add scheduling REST endpoints | 5 REST endpoints in Agent UI |
| Add scheduling MCP tools | 6 MCP tools wrapping the REST endpoints (added to M2's MCP server) |
| Agent UI scheduler | asyncio-based timer manager in FastAPI startup/shutdown lifecycle |
| Execution path | Timer fires → create session → send prompt → store result |
| Proactive surfacing | On session start, check for unseen scheduled results |

### Tests

| Test | Type | What It Verifies |
|------|------|-----------------|
| `test_schedule_task_creates_entry` | Unit | `schedule_task("test", "every 1h", "do stuff")` creates row in `scheduled_tasks` with correct interval_seconds=3600. |
| `test_schedule_task_duplicate_name` | Unit | Creating a task with existing name returns clear error. |
| `test_schedule_task_parses_intervals` | Unit | Parses "every 6h", "every 24h", "every 30m", "daily at 9am". Rejects invalid formats. |
| `test_list_schedules_via_mcp` | Unit | Mock HTTP → returns all tasks with status, next_run_at, last result summary. |
| `test_cancel_schedule_via_mcp` | Unit | Sets status to 'cancelled'. Task no longer fires. |
| `test_pause_resume_schedule_via_mcp` | Unit | Pause sets status='paused', timer stops. Resume restarts. |
| `test_get_schedule_results_via_mcp` | Unit | Returns last N results in reverse chronological order. |
| `test_schedule_rest_api_create` | Integration | `POST /api/schedules` creates task, returns it. |
| `test_schedule_rest_api_list` | Integration | `GET /api/schedules` returns all tasks. |
| `test_scheduler_fires_on_interval` | Integration | Create task with 2-second interval → wait 3 seconds → verify task executed at least once. |
| `test_scheduler_executes_prompt` | Integration | Scheduled task runs, creates a session, sends prompt through agent processing, stores result. |
| `test_scheduler_survives_restart` | Integration | Create task → restart Agent UI backend → verify task is reloaded and continues scheduling. |
| `test_scheduler_handles_agent_error` | Integration | Task prompt causes agent error → error is stored as result, task continues on next interval. |
| `test_proactive_recall` | Integration | Task runs while no user session active → user starts new session → agent surfaces results. |
| `test_scheduler_shutdown` | Unit | On Agent UI shutdown, all timers cancel cleanly without hanging. |
| `test_concurrent_scheduled_tasks` | Integration | 3 tasks with different intervals all fire correctly without interfering. |
| `test_schedule_tools_register` | Unit | MCP server has all scheduling tools registered. |

### Files

```
src/gaia/ui/
├── scheduler.py             # Async timer manager
├── mcp_server.py            # Extended from M2: add scheduling MCP tools
└── routers/
    └── schedules.py         # REST API for scheduling

tests/unit/
├── test_scheduler_tools.py  # MCP tool unit tests (mocked HTTP)
└── test_scheduler.py        # Timer manager unit tests

tests/integration/
└── test_scheduler_e2e.py    # Full lifecycle: create → fire → result → recall
```

**Effort**: 4-5 days

---

## Milestone 6: RAC Integration

**Goal**: Port Recursive Agent Composition from gaia-v2 so agents can spawn focused sub-agents with fresh context windows, sharing state via SharedAgentState.

*Deferred. Port when a use case demands recursive decomposition.*

### What Gets Ported

| Component | From gaia-v2 | Purpose |
|-----------|-------------|---------|
| `agent_query()` tool | `gaia_code/tools.py` | Spawn sub-agent with fresh context, shared state |
| `AgentCallStack` | `shared_state.py` | Track recursion depth, prevent infinite loops |
| Quality gates | `quality_gates.py` | Validate sub-agent output before accepting |
| Escalation ladder | `quality_gates.py` | Retry → decompose → cloud → ask user |

Agent registry uses KnowledgeDB (`category="agent"`, `metadata={"capabilities": [...], "system_prompt": "..."}`).

### Tests

| Test | Type | What It Verifies |
|------|------|-----------------|
| `test_agent_query_spawns_subagent` | Unit | `agent_query("do X")` creates a new agent instance with fresh context. |
| `test_agent_query_shares_state` | Unit | Sub-agent reads insights stored by parent. Parent reads insights stored by sub-agent. |
| `test_agent_query_max_depth` | Unit | Recursion beyond `max_depth` returns error, does not hang. |
| `test_call_stack_push_pop` | Unit | Push/pop frames track depth correctly. `current()` returns top. |
| `test_call_stack_thread_safety` | Unit | Concurrent push/pop from different threads doesn't corrupt stack. |
| `test_agent_registry_via_knowledge` | Unit | `store_insight(category="agent")` → `recall(category="agent")` finds it. |
| `test_quality_gate_pass` | Unit | Valid output passes quality gate. |
| `test_quality_gate_fail_escalation` | Unit | Failed gate triggers escalation ladder: retry → decompose → cloud → ask user. |
| `test_agent_query_result_stored` | Integration | Sub-agent completes task → result stored as insight in KnowledgeDB. |
| `test_recursive_decomposition` | Integration | Parent spawns child → child spawns grandchild → results propagate back up. |

**Effort**: 5-7 days

---

## Milestone 7: Self-Improving Agent

**Goal**: Agent builds its own tools from recurring patterns, extracts skills automatically, and learns from outcomes over time.

*Deferred. Requires M1 + M6 as foundation.*

### Tool Building

```
Agent notices: "I've done web_search → parse → filter → format" 3 times.
Agent: → Writes a Python function combining all 4 steps
       → Tests it in sandbox
       → store_insight(category="tool", metadata={"parameters": {...}, "code": "..."})
       → Uses single tool next time
```

### Automatic Skill Extraction

```
Agent completed: web_search → check_github → generate_post → replay_workflow
Agent: → store_insight(category="skill", content="morning marketing check",
            metadata={"steps": [...]})
       → Next time: "do the morning check" recalls and replays the whole sequence
```

### Outcome Learning

```
1. Agent posts content
2. Agent checks engagement later
3. store_insight(category="learning", content="NPU posts outperform 3:1")
4. Next cycle, agent recalls this and adjusts strategy
```

### Tests

| Test | Type | What It Verifies |
|------|------|-----------------|
| `test_tool_builder_generates_code` | Unit | Given a pattern description, ToolBuilderAgent generates valid Python function. |
| `test_tool_builder_runs_tests` | Unit | Generated tool has unit tests. Tests pass in sandbox. |
| `test_tool_builder_registers_tool` | Unit | New tool stored in KnowledgeDB with `category="tool"` and correct metadata. |
| `test_tool_builder_security` | Unit | Generated code is checked against import allowlist. Unsafe imports rejected. |
| `test_skill_extraction_from_sequence` | Unit | After multi-step completion, agent stores composite skill in KnowledgeDB. |
| `test_skill_extraction_replay` | Unit | Stored composite skill replays all steps in correct order. |
| `test_outcome_learning_stores_insight` | Unit | Positive outcome → `store_insight(category="learning")` with appropriate content. |
| `test_confidence_decay` | Unit | Insights not reconfirmed over N sessions have reduced confidence. |
| `test_learning_influences_decisions` | Integration | Agent with stored learning "NPU posts do well" preferentially generates NPU-related content. |
| `test_tool_builder_e2e` | Integration | Agent detects recurring pattern → spawns ToolBuilderAgent → new tool registered → agent uses it. |

**Effort**: 6-8 days

---

## File Structure (All Milestones)

```
src/gaia/agents/base/
├── shared_state.py             # M1: MemoryDB + KnowledgeDB + SharedAgentState (2 DBs only)
├── memory_mixin.py             # M1: MemoryMixin + auto-extraction
├── computer_use.py             # M3: ComputerUseMixin (learn/replay/list/test workflows)
├── service_integration.py      # M3: ServiceIntegrationMixin (API discovery, credentials, preferences)
├── quality_gates.py            # M6: Ported from gaia-v2
├── agent.py                    # Existing (unchanged)
├── tools.py                    # Existing (unchanged)
└── ...

src/gaia/agents/tools/
├── web_search.py               # M3: web_search @tool (wraps Perplexity MCP)
└── github_monitor.py           # M4: check_github @tool

src/gaia/ui/
├── mcp_server.py               # M2+M5: Agent UI MCP server (sessions, tunnels, scheduling)
├── scheduler.py                # M5: Async timer manager
├── database.py                 # Existing + M5: scheduled_tasks table
├── routers/
│   ├── sessions.py             # Existing + M2: search endpoint
│   └── schedules.py            # M5: Scheduling REST API
└── ...

tests/unit/
├── test_memory_db.py               # M1
├── test_knowledge_db.py            # M1 (covers skills, tools, agents as categories + credentials)
├── test_shared_state.py            # M1
├── test_memory_mixin.py            # M1
├── test_agent_ui_mcp.py            # M2
├── test_computer_use.py            # M3 (replay + decision workflows)
├── test_service_integration.py     # M3 (API discovery, credentials, preference learning)
├── test_web_search.py              # M3
├── test_github_monitor.py          # M4
├── test_scheduler_tools.py         # M5
├── test_scheduler.py               # M5
├── test_call_stack.py              # M6
├── test_quality_gates.py           # M6
└── test_tool_builder.py            # M7

tests/integration/
├── test_memory_persistence.py      # M1
├── test_agent_ui_mcp_e2e.py        # M2
├── test_computer_use_e2e.py        # M3
├── test_service_integration_e2e.py # M3 (credential persistence, API-first fallback)
├── test_web_search_live.py         # M3 (live Perplexity API)
├── test_domain_tools.py            # M4 (live GitHub API + e2e scenario)
├── test_scheduler_e2e.py           # M5
├── test_rac_e2e.py                 # M6
└── test_self_improvement.py        # M7

tests/fixtures/
└── test_form.html                  # M3: HTML form for computer use tests

~/.gaia/
├── chat/gaia_chat.db               # Agent UI database (sessions, messages, scheduled_tasks)
├── workspace/
│   ├── memory.db                   # Working memory (session-scoped)
│   └── knowledge.db                # Everything persistent (insights, preferences, credentials)
├── skills/
│   └── {insight_id}/
│       ├── step_1.png
│       └── ...
└── integrations/
    └── {service}_wrapper.py        # Auto-generated API wrappers (M3)
```
