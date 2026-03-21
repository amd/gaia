# Agent Memory Architecture

**Status:** Draft
**Date:** 2026-03-18
**Scope:** Core memory system for GAIA agents — "second brain" + tool learning
**Files:** 3 new (`memory_store.py`, `memory.py`, `discovery.py`), 1 edit (`agent.py`)
**Dependencies:** stdlib only (sqlite3, threading, json, re, uuid)

## Design References

This architecture draws on analysis of several open-source agent memory systems:

| Project | What we took / compared |
|---------|-------------------------|
| **[OpenJarvis](https://github.com/open-jarvis/OpenJarvis)** | Frozen prefix + dynamic suffix pattern for LLM KV-cache reuse (Hook 1). Their comparative analysis is at [`docs/spec/openjarvis-memory-analysis.md`](openjarvis-memory-analysis.md). |
| **gaia6** (internal) | FTS5 with AND→OR fallback, Szymkiewicz-Simpson deduplication, BM25 ranking, confidence decay. We simplified: dropped 2 databases → 1, 8 tools → 5, 7 categories → 6. See "What We Keep/Change from gaia6" section. |
| **General agent memory literature** | Confidence scoring (+0.02 on recall, ×0.9 decay), temporal awareness (`due_at`/`reminded_at`), sensitivity classification. |

**Key design decision from OpenJarvis analysis:** Their frozen prefix approach keeps stable content (facts, preferences) in a cached system prompt so the LLM inference engine can reuse its KV-cache, while time-sensitive content (current time, upcoming items) is injected per-turn. GAIA implements this with `get_memory_system_prompt()` (stable) + `get_memory_dynamic_context()` (per-turn, prepended to user message).

---

## Design Philosophy

The agent is a **trusted second brain** — it remembers everything, surfaces the right thing at the right time, and holds the user accountable to their own commitments. Memory should feel invisible in storage but proactive in recall.

Four principles:
1. **Store automatically** — conversations, tool calls, errors, preferences
2. **Recall naturally** — the LLM decides when to search memory, using its own tools (no forced pre-query step)
3. **Learn silently** — errors and successes update confidence scores without user awareness
4. **Be temporally aware** — know what time it is, what's coming up, what's overdue, and proactively surface time-sensitive items

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                     Agent                            │
│                                                     │
│  ┌──────────────┐    ┌───────────────────────────┐  │
│  │ MemoryMixin  │    │   Agent.process_query()   │  │
│  │              │    │                           │  │
│  │ Hooks into:  │───▶│ 1. _compose_system_prompt │  │
│  │  • prompt    │    │    → inject preferences   │  │
│  │  • tool exec │    │    → inject error patterns │  │
│  │  • post-query│    │                           │  │
│  │              │    │ 2. _execute_tool           │  │
│  │ Exposes:     │    │    → auto-log call+result │  │
│  │  • remember  │    │    → auto-log errors      │  │
│  │  • recall    │    │                           │  │
│  │  • forget    │    │ 3. after process_query     │  │
│  │              │    │    → store conversation   │  │
│  └──────┬───────┘    └───────────────────────────┘  │
│         │                                           │
│  ┌──────▼───────┐                                   │
│  │ MemoryStore  │  ← Pure data layer (no Agent deps)│
│  │              │                                   │
│  │ Single file: │                                   │
│  │ ~/.gaia/     │                                   │
│  │  memory.db   │                                   │
│  └──────────────┘                                   │
└─────────────────────────────────────────────────────┘
```

---

## File 1: `memory_store.py` — Data Layer

Agent-agnostic. Pure SQLite + FTS5. Zero imports from `gaia.agents`.

### Single Database: `~/.gaia/memory.db`

One file, three tables. WAL mode for concurrent reads.

### Timestamps

All timestamps use ISO 8601 format with timezone: `YYYY-MM-DDTHH:MM:SS±HH:MM` (e.g., `2026-03-18T14:30:00-07:00`).

This format is:
- **Human-readable** — the LLM can reason about "last Tuesday" or "yesterday at 3pm"
- **Sortable** — lexicographic ordering works correctly
- **Timezone-aware** — critical for users who travel or work across timezones
- **SQL-friendly** — SQLite's comparison operators work natively on this format

Stored via Python: `datetime.now().astimezone().isoformat()` (local time with UTC offset).

**Important caveat:** SQLite's built-in `datetime()` function does NOT understand timezone offsets. The temporal queries (`get_upcoming`, etc.) must compare against a Python-generated "now" string passed as a parameter, not `datetime('now')` in SQL. This is handled in the implementation — all time comparisons use parameterized queries with Python-computed boundaries.

### Schema

```sql
-- Schema version tracking (for future migrations)
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    migrated_at TEXT NOT NULL         -- ISO 8601
);
-- Initialize: INSERT INTO schema_version VALUES (1, <now>);


-- Table 1: conversations
-- Every conversation turn, persistent across sessions
CREATE TABLE conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,        -- 'user' | 'assistant'
    content     TEXT NOT NULL,
    context     TEXT DEFAULT 'global', -- Active context when this turn occurred
    timestamp   TEXT NOT NULL         -- ISO 8601 with timezone (set by Python, not SQL default)
);
CREATE INDEX idx_conv_session ON conversations(session_id);
CREATE INDEX idx_conv_ts ON conversations(timestamp DESC);
CREATE INDEX idx_conv_context ON conversations(context);

-- FTS5 for conversation search
CREATE VIRTUAL TABLE conversations_fts USING fts5(
    content,
    content=conversations,
    content_rowid=id
);
-- Sync triggers (INSERT/DELETE)


-- Table 2: knowledge
-- Persistent facts, preferences, learnings — the "second brain"
CREATE TABLE knowledge (
    id          TEXT PRIMARY KEY,     -- UUID
    category    TEXT NOT NULL,        -- 'fact' | 'preference' | 'error' | 'skill' | 'note' | 'reminder'
    content     TEXT NOT NULL,        -- Human-readable description
    domain      TEXT,                 -- Optional grouping (e.g., 'python', 'deployment')
    source      TEXT NOT NULL DEFAULT 'tool',  -- 'tool' | 'heuristic' | 'error_auto' | 'user'
    confidence  REAL DEFAULT 0.5,    -- 0.0 to 1.0, decays over time
    metadata    TEXT,                 -- JSON blob for structured data
    use_count   INTEGER DEFAULT 0,
    -- Context scoping
    context     TEXT DEFAULT 'global', -- Workspace/context scope (e.g., 'work', 'personal', 'project-x')
    -- Sensitivity
    sensitive   INTEGER DEFAULT 0,   -- 1 = sensitive (excluded from system prompt, redacted in logs)
    -- Entity linking
    entity      TEXT,                -- Optional entity reference (e.g., 'person:sarah_chen', 'app:vscode')
    -- Timestamps
    created_at  TEXT NOT NULL,       -- ISO 8601 with timezone (when first stored)
    updated_at  TEXT NOT NULL,       -- ISO 8601 (set on every modification)
    last_used   TEXT,                -- ISO 8601 (set on every recall)
    -- Temporal awareness
    due_at      TEXT,                -- ISO 8601 (when this becomes actionable/relevant)
    reminded_at TEXT                  -- ISO 8601 (when agent last surfaced this to user)
);

CREATE INDEX idx_knowledge_due ON knowledge(due_at)
    WHERE due_at IS NOT NULL;
CREATE INDEX idx_knowledge_context ON knowledge(context);
CREATE INDEX idx_knowledge_entity ON knowledge(entity)
    WHERE entity IS NOT NULL;
CREATE INDEX idx_knowledge_sensitive ON knowledge(sensitive)
    WHERE sensitive = 1;

-- FTS5 for knowledge search (standalone, manually synced)
CREATE VIRTUAL TABLE knowledge_fts USING fts5(content, domain, category);


-- Table 3: tool_history
-- Every tool call the agent makes, auto-logged
CREATE TABLE tool_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    tool_name   TEXT NOT NULL,
    args        TEXT,                 -- JSON
    result_summary TEXT,             -- Truncated result (first 500 chars)
    success     INTEGER NOT NULL,    -- 1 = success, 0 = failure
    error       TEXT,                -- Error message if failed
    duration_ms INTEGER,             -- Execution time
    timestamp   TEXT NOT NULL        -- ISO 8601 with timezone
);
CREATE INDEX idx_tool_name ON tool_history(tool_name);
CREATE INDEX idx_tool_session ON tool_history(session_id);
CREATE INDEX idx_tool_success ON tool_history(success);
CREATE INDEX idx_tool_ts ON tool_history(timestamp DESC);
```

### Knowledge Categories (4, not 7)

| Category | What it stores | Example |
|---|---|---|
| `fact` | Things about the user, project, world | "User's project uses React 19 with app router" |
| `preference` | How the user wants the agent to behave | "User prefers concise answers", "Always use dark mode" |
| `error` | Tool error patterns to avoid | "pip install torch fails without --index-url on this machine" |
| `skill` | Learned workflows and patterns | "To deploy: run tests → build → push to staging → verify → promote" |

### Knowledge Sources

| Source | How it's created | Default confidence |
|---|---|---|
| `tool` | LLM called `remember()` tool | 0.5 |
| `heuristic` | Auto-extracted via regex patterns | 0.3 (lower — heuristics are noisy) |
| `error_auto` | Auto-stored from tool failure | 0.5 |
| `user` | Manually created/edited via dashboard | 0.8 (user explicitly set this) |
| `discovery` | System scan during bootstrap | 0.4 (inferred, not stated — promoted via use) |

Source is visible in the dashboard and helps users understand why the agent "knows" something.

### Context Scoping

Different areas of your life produce different knowledge. Without scoping, the system
prompt mixes "deploy with `kubectl apply`" (work) with "dentist appointment Thursday"
(personal) with "use 4-space indent" (side project). Context keeps them separate.

| Context | When it's active | What it contains |
|---|---|---|
| `global` | Always included | Universal preferences, name, timezone |
| `work` | When agent is used for work tasks | Colleagues, project details, work tools |
| `personal` | Personal assistant mode | Appointments, health goals, personal contacts |
| `project-x` | User-defined per project | Project-specific facts, skills, errors |

**How it works:**
- `init_memory(context="work")` sets the active context at startup
- `set_memory_context("personal")` switches mid-session
- System prompt includes `global` + active context items
- `remember()` defaults to the active context (overridable per call)
- `recall()` searches across all contexts by default, filterable with `context=`
- Dedup is scoped to context — "deploy process" in `work` doesn't collide with `personal`

### Sensitivity Classification

Some knowledge is private — email addresses, API tokens, health information, financial
data. The `sensitive` flag controls visibility:

| Where | sensitive=0 (default) | sensitive=1 |
|---|---|---|
| System prompt | ✅ Included | ❌ Never included |
| `recall()` results | ✅ Returned | ✅ Returned (explicit query) |
| Tool history `args` | Full args logged | Args redacted to keys only |
| Dashboard | Normal display | 🔒 Badge, content blurred until clicked |

The LLM can still access sensitive data via `recall()` — it just won't be broadcast
in the system prompt where it could leak into logs or debugging output.

### Entity Linking

For use cases like email triage, contact management, and app integrations, the agent
needs to associate knowledge with specific people, apps, or services.

The `entity` field uses a lightweight `type:name` convention:

| Entity pattern | Example knowledge |
|---|---|
| `person:sarah_chen` | "Sarah Chen, VP Engineering, sarah@company.com" |
| `person:sarah_chen` | "Sarah prefers morning meetings" |
| `person:sarah_chen` | "Follow up with Sarah about Q2 roadmap" |
| `app:vscode` | "User prefers dark mode, 4-space tabs" |
| `service:gmail` | "User's work email is alex@company.com" |
| `project:gaia` | "Project uses Python 3.12, uv for package management" |

**How it works:**
- `recall(entity="person:sarah_chen")` returns everything about Sarah
- `get_by_entity("person:sarah_chen")` in MemoryStore does a direct indexed lookup
- The LLM links entities naturally: when user says "email Sarah about the roadmap,"
  the LLM calls `recall(entity="person:sarah_chen")` to get her email and preferences
- No separate entity table needed — it's just a denormalized tag on knowledge rows
- Multiple entries can share an entity, building a profile over time

### Class Interface: `MemoryStore`

```python
class MemoryStore:
    """Pure SQLite storage for agent memory. No agent dependencies."""

    def __init__(self, db_path: Path = None):
        """Open/create DB at db_path. Default: ~/.gaia/memory.db
        Uses WAL mode. Thread-safe via threading.Lock."""

    # --- Conversations ---
    def store_turn(self, session_id: str, role: str, content: str,
                   context: str = "global") -> None
    def get_history(self, session_id: str = None, context: str = None,
                    limit: int = 20) -> List[Dict]
    def search_conversations(self, query: str, context: str = None,
                             limit: int = 10) -> List[Dict]
        """FTS5 keyword search across conversation content.
        Filterable by context. For time-filtered queries, use
        get_recent_conversations(days=N) instead."""
    def get_recent_conversations(self, days: int = 7, context: str = None,
                                 limit: int = 50) -> List[Dict]
        """Get conversations from the last N days (timestamp-based, not FTS5).
        Filterable by context. Returns turns ordered oldest-first."""

    # --- Knowledge ---
    def store(self, category: str, content: str,
              domain: str = None, metadata: dict = None,
              confidence: float = 0.5,
              due_at: str = None,
              source: str = "tool",
              context: str = "global",
              sensitive: bool = False,
              entity: str = None) -> str
        """Store with dedup: >80% word overlap in same category+context → update existing.
        Dedup is scoped to context — 'work' facts don't collide with 'personal' facts.
        Validates due_at is a valid ISO 8601 string if provided."""

    def search(self, query: str, category: str = None,
               context: str = None, entity: str = None,
               include_sensitive: bool = False,
               top_k: int = 5) -> List[Dict]
        """FTS5 search. AND semantics, OR fallback. BM25 ranking.
        Bumps confidence +0.02 on each recalled item.
        Filters by context/entity if provided. Excludes sensitive by default.
        Returns dicts with all fields."""

    def get_by_category(self, category: str, context: str = None,
                        limit: int = 10) -> List[Dict]
    def get_by_entity(self, entity: str, limit: int = 20) -> List[Dict]
        """Get all knowledge about a specific entity.
        Example: get_by_entity('person:sarah_chen') → all facts about Sarah."""
    def get_upcoming(self, within_days: int = 7, include_overdue: bool = True,
                     context: str = None) -> List[Dict]
        """Get time-sensitive items due within N days (or overdue).
        Returns items where either: (a) never reminded, or (b) reminded before
        the due date but due date has now passed (needs follow-up).
        Filterable by context."""
    def update(self, knowledge_id: str, content: str = None,
               category: str = None, domain: str = None,
               metadata: dict = None, context: str = None,
               sensitive: bool = None, entity: str = None,
               due_at: str = None, reminded_at: str = None) -> bool
        """Update an existing knowledge entry. Only provided fields are changed.
        Sets updated_at to now. Returns False if ID not found."""
    def update_confidence(self, knowledge_id: str, delta: float) -> None
    def delete(self, knowledge_id: str) -> bool

    # --- Tool History ---
    def log_tool_call(self, session_id: str, tool_name: str,
                      args: dict, result_summary: str,
                      success: bool, error: str = None,
                      duration_ms: int = None) -> None
    def get_tool_errors(self, tool_name: str = None,
                        limit: int = 10) -> List[Dict]
    def get_tool_stats(self, tool_name: str) -> Dict
        """Returns: {total_calls, success_rate, avg_duration_ms, last_error}"""

    # --- Housekeeping ---
    def apply_confidence_decay(self, days_threshold: int = 30,
                               decay_factor: float = 0.9) -> int
        """Decay confidence for items not used in N days. Called once per session start."""
    def close(self) -> None
```

### Key Behaviors

- **Deduplication:** `store()` checks for >80% word overlap (Szymkiewicz-Simpson coefficient) in same category + context. If found, updates existing entry — replaces content with the newer version (facts change), takes max confidence, updates `updated_at`. Context scoping means "deploy process" in `work` won't collide with `personal`.
- **FTS5 search:** AND semantics by default. If zero results, automatic OR fallback. Query sanitized to strip FTS5 special characters.
- **Confidence:** 0.0–1.0 scale. +0.02 on recall, decays ×0.9 for items unused >30 days.
- **Thread safety:** All DB operations protected by `threading.Lock`.
- **Timestamps:** All timestamps use `datetime.now().astimezone().isoformat()` — local time with UTC offset. Stored as TEXT, sortable, timezone-aware.

### Fact Conflict & Consolidation

Facts change. "User's project uses React 18" becomes "User's project uses React 19." The system handles this at two levels:

**Level 1: Automatic dedup (storage layer)**

When `store()` finds >80% word overlap in the same category, it **replaces the content with the newer version** (not the longer one — gaia6 got this wrong). The newer fact is assumed to be more current. `updated_at` timestamp is set, `created_at` is preserved (shows when we first learned this topic).

```
store(category="fact", content="Project uses React 18")  → creates entry
store(category="fact", content="Project uses React 19")  → 80% overlap → replaces content
# Result: one entry, content="Project uses React 19", updated_at=now
```

**Level 2: LLM-driven correction (tool layer)**

When the LLM detects a contradiction (e.g., user says "actually we switched to Vue"), it can:
1. Call `recall(query="frontend framework")` → finds the old fact with its ID
2. Call `update_memory(knowledge_id="abc-123", content="Project uses Vue 3")` → updates in place
3. Or if it's a complete replacement: `forget` + `remember`

The LLM is the intelligence layer for conflict resolution. The storage layer handles the easy case (similar text = auto-update); the LLM handles the hard case (different text = explicit update/replace).

**Level 3: Confidence decay (time layer)**

Stale facts naturally lose confidence. If "Project uses React 18" hasn't been referenced in 30+ days, its confidence decays. New facts start at 0.5 and grow with use. This means outdated facts gradually disappear from the system prompt in favor of actively-used knowledge.

---

## File 2: `memory.py` — Agent Integration

The mixin that hooks memory into the Agent lifecycle at exactly **3 points**.

### Hook 1: System Prompt Injection + Per-Turn Dynamic Context

#### Frozen Prefix Design (KV-cache optimization)

The system prompt is split into two parts to allow LLM inference engines to reuse
their KV-cache across conversation turns:

**Stable prefix** (`get_memory_system_prompt`) — injected once via `Agent._get_mixin_prompts()`:
- Preferences, facts, known errors
- Contains nothing time-sensitive (no timestamps, no due dates)
- Stays frozen for the entire session → KV-cache can be reused

**Dynamic per-turn context** (`get_memory_dynamic_context`) — prepended to the user message each turn:
- Current time (changes every turn)
- Upcoming/overdue items (may change between turns)
- Injected by the `process_query()` override in MemoryMixin

```python
def get_memory_system_prompt(self) -> str:
    """Called by Agent._get_mixin_prompts() — injects STABLE memory only.

    Injects into the system prompt:
    1. All user preferences in active context
    2. Top 5 high-confidence facts in active context
    3. Recent error patterns (limit 5)

    Deliberately excludes current time and upcoming items — those go in
    get_memory_dynamic_context() so this prompt stays frozen for KV-cache reuse.

    Filters:
    - global context items always included regardless of active context
    - Sensitive items (sensitive=1) are NEVER included
    """

def get_memory_dynamic_context(self) -> str:
    """Per-turn context injected by process_query() override.

    Contains:
    1. Current date/time
    2. Upcoming/overdue items (due within 7 days)

    Returns empty string if nothing time-sensitive is active.
    """

def process_query(self, user_input, **kwargs):
    """Prepend per-turn dynamic context to the user message.

    The system prompt is NOT invalidated — it stays frozen for KV-cache reuse.
    The original user_input is saved so _after_process_query can store the
    clean version (without the dynamic context prefix) to conversation history.
    """
    self._original_user_input = user_input
    dynamic = self.get_memory_dynamic_context()
    augmented = f"{dynamic}\n\n{user_input}" if dynamic else user_input
    return super().process_query(augmented, **kwargs)
```

**Example stable system prompt fragment:**

```
=== MEMORY ===
Preferences:
  - tone: professional but friendly
  - code_style: black formatter, 88 char lines

Known facts:
  - Project uses React 19 with app router (confidence: 0.82)
  - User's name is Alex, role is tech lead (confidence: 0.95)

Known errors to avoid:
  - execute_code: "import torch" fails — torch not installed on this machine
```

**Example dynamic context prepended to each user message:**

```
[GAIA Memory Context]
Current time: 2026-03-25T10:30:00-07:00 (Tuesday)

Upcoming/overdue:
  - [DUE Mar 27] Online course starts next week
  - [OVERDUE Mar 24] Follow up on deployment review
After mentioning a time-sensitive item, call update_memory to set reminded_at so you don't repeat yourself.

<actual user message here>
```

The temporal query is a simple indexed lookup — no FTS5 needed:

```sql
-- Python computes: now_iso = datetime.now().astimezone().isoformat()
--                  future_iso = (datetime.now().astimezone() + timedelta(days=7)).isoformat()
-- Passed as parameters (NOT using SQLite datetime() which doesn't handle timezones)

SELECT * FROM knowledge
WHERE due_at IS NOT NULL
  AND due_at <= ?                                  -- ? = future_iso (due within a week)
  AND (reminded_at IS NULL OR reminded_at < due_at) -- not reminded since it became due
ORDER BY due_at ASC
LIMIT ?   -- parameterized, default 10; callers can override via limit= parameter
```

### Hook 2: Tool Execution Wrapper

```python
# Memory tools that should NOT be logged to tool_history (avoids noise/recursion)
_MEMORY_TOOLS = {"remember", "recall", "update_memory", "forget", "search_past_conversations"}

def _execute_tool(self, tool_name: str, tool_args: dict) -> Any:
    """Override Agent._execute_tool() to auto-log every tool call.

    1. Record start time
    2. Call super()._execute_tool()
    3. If tool_name NOT in _MEMORY_TOOLS:
       a. Log to tool_history: name, args, result, success/failure, duration
       b. If failure and error is novel: auto-store as knowledge(category='error')
    4. Return original result unchanged

    Memory tools (remember, recall, etc.) are excluded from logging to avoid
    noise and potential recursion (error auto-store calls remember internally).
    """
```

### Hook 3: Post-Query Conversation Storage

```python
def _after_process_query(self, user_input: str, assistant_response: str) -> None:
    """Called after process_query() completes.

    1. Store both turns in conversations table (tagged with active context)
    2. Run lightweight heuristic extraction (regex, no LLM):
       - "I prefer X" → knowledge(category='preference', context=active_context)
       - "my name is X" → knowledge(category='fact', context='global')
       - "always/never X" → knowledge(category='preference', context=active_context)

    Heuristic-extracted knowledge inherits the active context.
    Name/identity facts go to 'global' since they apply everywhere.
    Auto-extraction is best-effort. The LLM has memory tools for
    anything the heuristics miss.
    """
```

### Memory Tools (5 tools, exposed to the LLM)

```python
@tool("remember")
def remember(fact: str, category: str = "fact", domain: str = "",
             due_at: str = "", context: str = "", sensitive: str = "false",
             entity: str = "") -> dict:
    """Store a fact, preference, or learning in persistent memory.
    Categories: fact, preference, error, skill, note, reminder
    If a similar fact already exists (>80% overlap in same context), it will be updated.
    Use due_at for time-sensitive items (ISO 8601 format).
    Use context to scope memories (e.g., "work", "personal", "project-x").
    Use sensitive="true" for private data (excluded from system prompt).
    Use entity to link to a person/app/service (e.g., "person:sarah_chen").
    Examples:
      remember(fact="User prefers concise answers", category="preference")
      remember(fact="Project uses Next.js 15", category="fact", domain="frontend",
               context="work")
      remember(fact="Online course starts", category="fact",
               due_at="2026-03-25T09:00:00-07:00")
      remember(fact="Sarah's email is sarah@company.com", category="fact",
               entity="person:sarah_chen", sensitive="true")
    """

@tool("recall")
def recall(query: str = "", category: str = "", context: str = "",
           entity: str = "", limit: int = 5) -> dict:
    """Search memory for relevant knowledge.
    With query: uses full-text search (FTS5).
    Without query: returns entries filtered by category/context/entity.
    At least one of query, category, context, or entity must be provided.
    Returns results with IDs, timestamps, and due dates.
    Use ID with update_memory or forget.
    Examples:
      recall(query="deployment process")
      recall(query="user preferences", category="preference")
      recall(entity="person:sarah_chen")             # everything about Sarah
      recall(query="API keys", context="work")
      recall(category="preference")                   # all preferences
    """

@tool("update_memory")
def update_memory(knowledge_id: str, content: str = "",
                  category: str = "", domain: str = "",
                  due_at: str = "", reminded_at: str = "",
                  context: str = "", sensitive: str = "",
                  entity: str = "") -> dict:
    """Update an existing memory entry. Use recall first to find the ID.
    Only non-empty fields are updated; empty strings are ignored.
    Set reminded_at="now" after mentioning a time-sensitive item to the user.
    Examples:
      update_memory(knowledge_id="abc-123", content="Project now uses React 19")
      update_memory(knowledge_id="abc-123", reminded_at="now")  # mark as mentioned
      update_memory(knowledge_id="abc-123", sensitive="true")   # mark as sensitive
    """

@tool("forget")
def forget(knowledge_id: str) -> dict:
    """Remove a specific memory entry by ID."""

@tool("search_past_conversations")
def search_past_conversations(query: str = "", days: int = 0,
                              limit: int = 10) -> dict:
    """Search past conversation history across all sessions.
    Use query for keyword search, days for time-based retrieval, or both.
    Examples:
      search_past_conversations(query="database migration")
      search_past_conversations(days=7)  # everything from last week
      search_past_conversations(query="deploy", days=14)  # deploy discussions in last 2 weeks
    """
```

**Why 5 tools, not 8?** The gaia6 version had `remember`, `recall_memory`, `forget_memory`, `store_insight`, `recall`, `store_preference`, `get_preference`, `search_conversations`. Too many overlapping tools — the LLM gets confused about which to use. Unified:

- `remember` + `store_insight` + `store_preference` → **`remember`** with `category` param
- `recall_memory` + `recall` + `get_preference` → **`recall`** with `category` filter
- NEW: **`update_memory`** — modify existing entries (recall → get ID → update)
- `forget_memory` → **`forget`**
- `search_conversations` → **`search_past_conversations`**

The CRUD operations map cleanly: `remember` = create, `recall` = read, `update_memory` = update, `forget` = delete. Plus `search_past_conversations` for conversation history.

### Class Interface: `MemoryMixin`

```python
class MemoryMixin:
    """Mixin that gives any Agent persistent memory.

    Usage:
        class MyAgent(MemoryMixin, Agent):   # MemoryMixin MUST come before Agent
            def __init__(self, **kwargs):
                self.init_memory()          # Before super().__init__()
                super().__init__(**kwargs)

            def _register_tools(self):
                super()._register_tools()
                self.register_memory_tools()
    """

    def init_memory(self, db_path: Path = None, context: str = "global") -> None
        """Initialize memory store with an active context scope."""
    @property
    def memory_store(self) -> MemoryStore
    @property
    def memory_session_id(self) -> str
    @property
    def memory_context(self) -> str
        """Current active context (e.g., 'work', 'personal', 'global')."""
    def set_memory_context(self, context: str) -> None
        """Switch active context. Affects system prompt filtering and default store context."""

    # Prompt integration
    def get_memory_system_prompt(self) -> str

    # Tool registration
    def register_memory_tools(self) -> None

    # Lifecycle hooks (overrides Agent methods)
    def process_query(self, user_input, **kwargs) -> Dict
        """Saves original input, prepends per-turn dynamic context, calls super()."""
    def get_memory_dynamic_context(self) -> str
        """Returns current time + upcoming items for per-turn injection into user message."""
    def _execute_tool(self, tool_name, tool_args) -> Any
    def _after_process_query(self, user_input, response) -> None

    # Session management
    def reset_memory_session(self) -> None
```

---

## Integration with Agent Base Class

**Two small changes to `agent.py`** (the `process_query` override lives in the mixin, not in agent.py):

### Change 1: Mixin prompt check (4 lines)

In `_get_mixin_prompts()`, add after the VLM check:

```python
# Check for Memory mixin prompts
if hasattr(self, "get_memory_system_prompt"):
    fragment = self.get_memory_system_prompt()
    if fragment:
        prompts.append(fragment)
```

### Change 2: Post-query hook (2 lines)

At the end of `process_query()`, just before `return result` (after line 2498 `self.last_result = result`):

```python
if hasattr(self, '_after_process_query'):
    self._after_process_query(user_input, result.get("result", ""))
```

Note: `final_answer` is a local string variable. The return dict has `result["result"]`
which is the final answer text. We pass that, not the full dict.

Note: Agent already has `_post_process_tool_result()` (line 2502) which fires after
each individual tool call. Our `_execute_tool` override serves a different purpose
(logging to tool_history), so there's no conflict.

---

## Use Case Mapping

### "Remember that my meeting is at 3pm"
```
User → LLM has memory tools → calls remember(fact="Meeting at 3pm today")
→ MemoryStore.store(category="fact", content="Meeting at 3pm today")
→ Next query: system prompt includes "Meeting at 3pm today" (high-confidence fact)
```

### "I have a course starting next week"
```
Day 1 (March 18):
  User → LLM calls remember(fact="Online course starts next week",
                             category="fact", due_at="2026-03-25T09:00:00-07:00")
  → stored with due_at

Day 5 (March 22):
  User starts a conversation about something unrelated
  → get_memory_dynamic_context() runs get_upcoming(within_days=7)
  → Dynamic context prepended to user message: "[DUE Mar 25] Online course starts next week"
  → LLM proactively mentions: "By the way, your online course starts in 3 days"
  → LLM calls update_memory(knowledge_id="...", reminded_at="now")
  → Item won't appear in upcoming again (reminded_at is set)

Day 8 (March 25):
  User starts a new session
  → reminded_at was set, but due_at has now passed
  → get_upcoming() includes overdue items where reminded_at < due_at
     (was reminded before it was due, but not after)
  → Dynamic context: "[TODAY] Online course starts today"
  → LLM: "Your course starts today! How did it go?"
  → LLM updates reminded_at again
```

### "What did we talk about last week?"
```
User → LLM knows current date from system prompt
→ LLM must search by topic keywords, NOT by "last week" (FTS5 is keyword-based)
→ If user asks about a specific topic: search_past_conversations(query="deployment")
→ If user asks about a time range: LLM should ask "What topic?" or recall recent facts
→ Returns matching turns with timestamps → LLM summarizes

Note: FTS5 cannot filter by date range. For pure time-based queries, the LLM
should use recall() to find knowledge stored during that period (knowledge has
timestamps), or ask the user to narrow by topic. This is an acceptable limitation
— keyword search covers 90% of "what did we discuss" queries.
```

### Accountability: "I committed to exercising 3x this week"
```
Day 1:
  User → LLM calls remember(fact="User committed to exercising 3x this week",
                             category="fact", due_at="2026-03-23T20:00:00-07:00")

Day 5 (due date):
  User starts any conversation
  → Dynamic context: "[DUE TODAY] User committed to exercising 3x this week"
  → LLM: "Hey, end of the week — how did the exercise commitment go? Did you hit 3x?"
  → Based on user's answer, LLM might:
    - update_memory with outcome in metadata
    - remember a new follow-up for next week
    - forget if no longer relevant
```

### Tool fails → agent learns
```
Agent calls execute_code(code="import torch") → fails with ImportError
→ _execute_tool wrapper logs: tool_history(success=0, error="ImportError: torch")
→ Auto-stores: knowledge(category='error', content="import torch fails: not installed")
→ Next time: system prompt includes error pattern → LLM avoids/handles it
```

### User says "I prefer concise answers"
```
User input → heuristic extraction catches "I prefer X" pattern
→ MemoryStore.store(category="preference", content="User prefers concise answers")
→ Next session: system prompt includes preference → LLM adjusts behavior
```

### Agent learns a workflow
```
User walks agent through multi-step deployment 3 times
→ Agent notices pattern → calls remember(fact="Deploy workflow: test → build → push → verify",
                                         category="skill", domain="deployment")
→ Next time user says "deploy": LLM recalls skill → follows learned workflow
```

---

## What We Keep from gaia6

| Component | Status | Notes |
|---|---|---|
| FTS5 with AND/OR fallback | ✅ Keep | Core search mechanism |
| Dedup (80% word overlap) | ✅ Keep | Prevents knowledge bloat |
| Confidence scoring + decay | ✅ Keep | Keeps knowledge fresh |
| BM25 ranking | ✅ Keep | Better search relevance |
| Thread-safe locking | ✅ Keep | Required for concurrent access |
| `_sanitize_fts5_query()` | ✅ Keep | Essential for safe FTS5 |
| `_word_overlap()` | ✅ Keep | Dedup mechanism |
| `_extract_keywords()` | ❌ Drop | gaia6 used this for trigger-based recall; our design uses FTS5 on content directly — no separate triggers column needed |
| Heuristic extraction (regex) | ✅ Keep, simplified | Fewer patterns, best-effort |

## What We Change from gaia6

| gaia6 | New Design | Why |
|---|---|---|
| 2 databases (memory.db + knowledge.db) | 1 database (memory.db) | Simpler |
| Singleton SharedAgentState | No singleton. Direct MemoryStore refs | Simpler, testable |
| 8 tools | 5 tools (full CRUD + search) | Less confusion, complete operations |
| 7 knowledge categories | 6 categories (fact, preference, error, skill, note, reminder) | Less decision fatigue |
| Separate preferences table | `knowledge(category='preference')` | One fewer table |
| Credentials table | Dropped | Belongs in OS keyring |
| File cache table | Dropped | Separate concern |
| Session-only working memory table | Dropped | Tool history replaces this |

## What We Add (vs gaia6)

| Feature | How |
|---|---|
| Auto tool call logging | `_execute_tool()` override — transparent |
| Auto error learning | Failed tool → `knowledge(category='error')` |
| Error patterns in system prompt | `get_memory_system_prompt()` includes them |
| Tool stats (success rate, duration) | `get_tool_stats()` on MemoryStore |
| Post-query hook pattern | `_after_process_query()` in agent.py |
| Temporal awareness | `due_at` + `reminded_at` on knowledge, `get_upcoming()` query |
| Current time per-turn (dynamic context) | LLM always knows what day/time it is |
| Accountability | Overdue items surface proactively, agent follows up |

---

## What We Explicitly Don't Do

- **No LLM calls for extraction** — heuristic only. The LLM has memory tools for anything important.
- **No pre-query forced search** — the LLM calls `recall` when it wants. Faster, simpler.
- **No vector embeddings** — FTS5 is sufficient. Embeddings add deps and complexity.
- **No push notifications** — time-sensitive items surface when the user interacts (or via scheduled agent runs), not via OS notifications.
- **No autonomous scheduling** — memory stores `due_at` but doesn't trigger itself. The existing scheduler can read `get_upcoming()` to trigger the agent — that's a one-liner integration for a follow-up PR, not an architecture concern.
- **No multi-user support** — single user per machine.
- **No credential encryption** — credentials belong in OS keyring, not memory.db.

---

## Observability & Dashboard

Memory is only trustworthy if you can see it. The agent UI should have a **Memory Dashboard** — a window into everything the agent knows, how it's performing, and what's coming up. Think GitHub's contribution dashboard, but for your agent's brain.

### MemoryStore Query API

The `MemoryStore` class exposes read-only aggregate methods specifically for the dashboard. No new tables — these are queries over existing data.

```python
class MemoryStore:
    # ... (existing methods) ...

    # --- Dashboard / Observability ---
    def get_stats(self) -> Dict:
        """Aggregate statistics across all tables.
        Returns:
            {
                "knowledge": {
                    "total": 142,
                    "by_category": {"fact": 68, "preference": 12, "error": 35, "skill": 27, "note": 5, "reminder": 3},
                    "by_context": {"global": 15, "work": 95, "personal": 32},
                    "sensitive_count": 8,
                    "entity_count": 12,        -- unique entities
                    "avg_confidence": 0.64,
                    "oldest": "2026-01-15T...",
                    "newest": "2026-03-18T...",
                },
                "conversations": {
                    "total_turns": 1847,
                    "total_sessions": 93,
                    "first_session": "2026-01-15T...",
                    "last_session": "2026-03-18T...",
                },
                "tools": {
                    "total_calls": 523,
                    "unique_tools": 18,
                    "overall_success_rate": 0.91,
                    "total_errors": 47,
                },
                "temporal": {
                    "upcoming_count": 3,      -- due within 7 days, not reminded
                    "overdue_count": 1,        -- past due, not resolved
                },
                "db_size_bytes": 2457600,
            }
        """

    def get_all_knowledge(self, category: str = None,
                          context: str = None,
                          entity: str = None,
                          sensitive: bool = None,
                          search: str = None,
                          sort_by: str = "updated_at",
                          order: str = "desc",
                          offset: int = 0,
                          limit: int = 50) -> Dict:
        """Paginated knowledge browser with full filtering.
        search: optional FTS5 query to filter by content.
        Returns: {"items": [...], "total": 142, "offset": 0, "limit": 50}"""

    def get_tool_summary(self) -> List[Dict]:
        """Per-tool stats for the tool activity table.
        Returns list of:
            {
                "tool_name": "execute_code",
                "total_calls": 87,
                "success_count": 79,
                "failure_count": 8,
                "success_rate": 0.91,
                "avg_duration_ms": 1230,
                "last_used": "2026-03-18T14:30:00-07:00",
                "last_error": "SyntaxError: unexpected indent",
            }
        """

    def get_activity_timeline(self, days: int = 30) -> List[Dict]:
        """Daily activity counts for the activity chart.
        Returns list of:
            {
                "date": "2026-03-18",
                "conversations": 12,   -- turns that day
                "tool_calls": 8,
                "knowledge_added": 3,
                "errors": 1,
            }
        """

    def get_recent_errors(self, limit: int = 20) -> List[Dict]:
        """Recent tool errors for the error log view.
        Returns tool_history rows where success=0, newest first."""
```

### UI Backend: Memory Router

New FastAPI router following the existing pattern in `src/gaia/ui/routers/`:

**File:** `src/gaia/ui/routers/memory.py`

```python
router = APIRouter(tags=["memory"])

# --- Dashboard ---
@router.get("/api/memory/stats")
async def memory_stats() -> Dict:
    """Aggregate stats for dashboard header cards."""

@router.get("/api/memory/activity")
async def memory_activity(days: int = 30) -> List[Dict]:
    """Daily activity timeline for the activity chart."""

# --- Knowledge Browser ---
@router.get("/api/memory/knowledge")
async def list_knowledge(category: str = None,
                         context: str = None,
                         entity: str = None,
                         sensitive: bool = None,
                         search: str = None,
                         sort_by: str = "updated_at",
                         order: str = "desc",
                         offset: int = 0,
                         limit: int = 50) -> Dict:
    """Paginated, filterable, searchable knowledge entries."""

@router.post("/api/memory/knowledge")
async def create_knowledge(body: KnowledgeCreate) -> Dict:
    """Create a knowledge entry from the dashboard (source='user', confidence=0.8)."""

@router.put("/api/memory/knowledge/{knowledge_id}")
async def edit_knowledge(knowledge_id: str, body: KnowledgeUpdate) -> Dict:
    """Edit a knowledge entry from the dashboard (all fields editable)."""

@router.delete("/api/memory/knowledge/{knowledge_id}")
async def delete_knowledge(knowledge_id: str) -> Dict:
    """Delete a knowledge entry from the dashboard."""

# --- Entities ---
@router.get("/api/memory/entities")
async def list_entities() -> List[Dict]:
    """List all unique entities with their knowledge counts.
    Returns: [{"entity": "person:sarah_chen", "count": 5, "last_updated": "..."}]"""

@router.get("/api/memory/entities/{entity}")
async def get_entity(entity: str) -> List[Dict]:
    """Get all knowledge linked to a specific entity."""

# --- Contexts ---
@router.get("/api/memory/contexts")
async def list_contexts() -> List[Dict]:
    """List all contexts with their knowledge counts.
    Returns: [{"context": "work", "count": 68}, {"context": "personal", "count": 23}]"""

# --- Tool Performance ---
@router.get("/api/memory/tools")
async def tool_summary() -> List[Dict]:
    """Per-tool performance stats."""

@router.get("/api/memory/tools/{tool_name}/history")
async def tool_history(tool_name: str, limit: int = 50) -> List[Dict]:
    """Recent call history for a specific tool."""

# --- Errors ---
@router.get("/api/memory/errors")
async def recent_errors(limit: int = 20) -> List[Dict]:
    """Recent tool errors across all tools."""

# --- Conversations ---
@router.get("/api/memory/conversations")
async def list_sessions(limit: int = 20) -> List[Dict]:
    """List conversation sessions with timestamps and turn counts."""

@router.get("/api/memory/conversations/{session_id}")
async def get_session(session_id: str) -> List[Dict]:
    """Get all turns for a specific conversation session."""

@router.get("/api/memory/conversations/search")
async def search_conversations(query: str, limit: int = 20) -> List[Dict]:
    """Full-text search across all conversations."""

# --- Temporal ---
@router.get("/api/memory/upcoming")
async def upcoming_items(days: int = 7) -> List[Dict]:
    """Time-sensitive items due within N days + overdue."""
```

### UI Frontend: Dashboard Views

**File:** `src/gaia/apps/webui/src/pages/MemoryDashboard.tsx` (new page)

The dashboard has 6 sections:

#### 1. Header Cards (at-a-glance stats)
```
┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│  142          │ │  93          │ │  523         │ │  91%         │
│  Memories     │ │  Sessions    │ │  Tool Calls  │ │  Success Rate│
│  +3 today     │ │  since Jan   │ │  18 tools    │ │  47 errors   │
└──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘
```

#### 2. Activity Timeline (contribution graph)
A heatmap or bar chart showing daily activity over the last 30 days:
- Conversations (blue)
- Tool calls (green)
- Knowledge added (purple)
- Errors (red)

Similar to GitHub's contribution graph — at a glance you see when the agent was most active.

#### 3. Knowledge Browser (main table)
Filterable, sortable table of all knowledge entries:

```
  [Context: All ▾]  [Category: All ▾]  [Entity: All ▾]  [Search: ________]

┌─────────┬────────────────────────────────────────┬─────────┬────────┬──────────┬─────────┬──────────┐
│Category │ Content                                │ Context │ Entity │Confidence│ Due     │ Updated  │
├─────────┼────────────────────────────────────────┼─────────┼────────┼──────────┼─────────┼──────────┤
│ fact    │ Project uses React 19                  │ work    │ —      │ 0.82     │ —       │ Mar 18   │
│ pref    │ User prefers concise answers           │ global  │ —      │ 0.65     │ —       │ Mar 15   │
│ error   │ import torch fails: not installed      │ work    │ —      │ 0.70     │ —       │ Mar 17   │
│ fact    │ 🔒 Sarah's email is sarah@company.com  │ work    │ 👤sarah│ 0.50     │ —       │ Mar 18   │
│ fact    │ Online course starts next week         │personal │ —      │ 0.50     │ Mar 25  │ Mar 18   │
│ skill   │ Deploy: test → build → push → verify   │ work    │ —      │ 0.88     │ —       │ Mar 16   │
└─────────┴────────────────────────────────────────┴─────────┴────────┴──────────┴─────────┴──────────┘
  + Add Memory                                                    Page 1 of 3  [< >]
```

- 🔒 = sensitive entry (content blurred until clicked)
- 👤 = entity link (click to see all knowledge about this entity)
- Each row clickable for full detail view (metadata, timestamps, source, use_count)
- Inline actions: **Edit** (all fields), **Delete**, **Copy ID**, **Toggle Sensitive**
- **+ Add Memory** button creates entries with `source='user'`, `confidence=0.8`

All memory is user-editable through the dashboard. The user is the ultimate authority
over what the agent knows — they can create, correct, update, or delete any entry.

#### 4. Tool Performance (stats table)
```
┌────────────────┬───────┬─────────┬───────────┬──────────────────────────────┐
│ Tool           │ Calls │ Success │ Avg Time  │ Last Error                   │
├────────────────┼───────┼─────────┼───────────┼──────────────────────────────┤
│ execute_code   │  87   │  91%    │  1.2s     │ SyntaxError: unexpected...   │
│ read_file      │  156  │  98%    │  45ms     │ FileNotFoundError: /tmp/...  │
│ web_search     │  43   │  86%    │  2.1s     │ ConnectionTimeout: ...       │
│ write_file     │  28   │  100%   │  18ms     │ —                            │
└────────────────┴───────┴─────────┴───────────┴──────────────────────────────┘
```

Click a tool row to see its full call history.

#### 5. Conversation History Browser
List of past sessions with timestamps, turn counts, and preview of first message.
Click a session to see full conversation. Search bar for FTS5 across all conversations.
Read-only — conversations are an immutable log (no edit/delete).

#### 6. Upcoming & Overdue (temporal sidebar)
```
┌─ Upcoming ──────────────────────────────────┐
│ ⏰ Mar 25  Online course starts             │
│ ⏰ Mar 27  Team standup presentation         │
│                                             │
│ ⚠️ OVERDUE                                  │
│ 🔴 Mar 15  Follow up on deployment review   │
└─────────────────────────────────────────────┘
```

### Navigation

Add a "Memory" tab to the agent UI sidebar/nav, alongside existing Chat/Documents tabs.

### Design Principles for the Dashboard

1. **Read-heavy, write-light** — Dashboard is mostly reading. Writes only for manual edits/deletes.
2. **No real-time streaming needed** — Data refreshes on page load or manual refresh. No WebSocket needed.
3. **Same DB file** — The dashboard reads directly from `~/.gaia/memory.db`. No separate data store.
4. **API-first** — All data flows through the REST API. The frontend never touches SQLite directly.
5. **Pagination everywhere** — Knowledge and tool history can grow large. Always paginated.

---

## Risks & Mitigations

### System Prompt Bloat
As knowledge accumulates, `get_memory_system_prompt()` could grow unbounded and eat into context window budget. **Mitigation:** Hard limits on each section — max 10 preferences, max 5 facts, max 5 errors, max 10 upcoming items. Total memory prompt section capped at ~2000 tokens. If over budget, prioritize by confidence score.

### Database Growth
Conversations and tool_history grow indefinitely. After months of use, the DB could become large. **Mitigation:**
- `tool_history`: Implement a retention policy — keep last 90 days, archive/delete older entries. Add `MemoryStore.prune(days=90)` method.
- `conversations`: Same retention policy. Older conversations can be summarized into knowledge entries before pruning (future enhancement).
- `knowledge`: Self-regulating via confidence decay — stale items drop below 0.1 and can be pruned.
- WAL mode checkpoint: Periodic `PRAGMA wal_checkpoint(TRUNCATE)` to prevent WAL file growth.

### Schema Migration
When we add columns or tables in future versions, existing `memory.db` files need migration. **Mitigation:** Add a `schema_version` table (single row) and a migration function that runs on `MemoryStore.__init__()`. Check version, apply ALTER TABLE/CREATE TABLE as needed. Start at version 1.

### FTS5 Index Corruption
FTS5 standalone tables can get out of sync if the process crashes mid-write. **Mitigation:** Use `INSERT OR REPLACE` patterns carefully, and add a `rebuild_fts()` method that can be called from the dashboard if search results seem wrong.

### MRO and Multiple Mixins
`MemoryMixin` overrides `process_query` and `_execute_tool` — both of which are also defined in `Agent`. For MemoryMixin's versions to run first (and call `super()` to reach Agent), **MemoryMixin must appear before Agent in the class declaration**: `class MyAgent(MemoryMixin, Agent, OtherMixin)`. If Agent is listed first, both overrides are silently shadowed and tool logging + dynamic context injection will not work.

### LLM Not Using Memory Tools
Local LLMs (especially smaller ones) may not reliably call memory tools — they might ignore the tools or use wrong arguments. **Mitigation:** The automatic hooks (tool logging, conversation storage, system prompt injection) work regardless of whether the LLM calls memory tools. The tools are a bonus for smarter models, not a requirement. The heuristic extraction provides a baseline.

### Invalid Dates from LLM
The LLM might pass "next Tuesday" or "2026-13-45" as `due_at`. **Mitigation:** The `remember` tool validates `due_at` with `datetime.fromisoformat()` before calling `store()`. If invalid, return an error message telling the LLM to use ISO 8601 format. The current time is in the system prompt so the LLM can compute dates.

### Dashboard DB Access
The UI backend (FastAPI) needs its own MemoryStore instance to access `~/.gaia/memory.db`. This is safe because WAL mode supports concurrent readers. **Implementation:** The dashboard uses a single shared read-write `MemoryStore` singleton (thread-safe initialization via `threading.Lock`), which handles both read and write endpoints through the public `MemoryStore` API. Raw SQL access from the router layer is prohibited — all queries must go through named `MemoryStore` methods. The singleton is lazy-initialized on first request and persists for the lifetime of the server process.

**Sync handlers in threadpool:** All FastAPI route handlers are defined as `def` (not `async def`) so FastAPI automatically runs them in a worker thread pool. This prevents the SQLite calls from blocking the asyncio event loop. In particular, `get_stats()` issues ~12 sequential queries and `rebuild_fts()` can be slow — running these synchronously in `async def` handlers would stall all other requests.

**Content validation rules (`store()` / `update()`):**
- Empty or whitespace-only content raises `ValueError` — rejected before any SQL.
- Content is silently truncated at **2 000 chars** (`store()` / `update()`) or **4 000 chars** (`store_turn()`). This prevents arbitrarily large rows from degrading FTS5 performance.
- `due_at` values are normalized to timezone-aware ISO 8601 (local timezone applied when caller passes a naive datetime). Ensures correct SQL string comparisons in `get_upcoming()` and `get_stats()`.

**FTS5 query input cap (`_sanitize_fts5_query()`):** Input is capped at **500 chars** before regex processing. All FTS-backed callers (knowledge search, conversation search, deduplication) benefit automatically.

**N+1 query elimination (`get_tool_summary()`):** Previously fetched the most-recent failure per tool inside a Python loop — one extra query per tool. Rewritten to a single SQL statement using a `LEFT JOIN` subquery with `ROW_NUMBER() OVER (PARTITION BY tool_name ORDER BY timestamp DESC)`. Requires SQLite ≥ 3.25 (window functions); GAIA's bundled SQLite 3.35+ satisfies this.

**Bounded list queries:** `get_entities()` and `get_contexts()` accept `limit: int = 100`; `get_upcoming()` accepts `limit: int = 10`. All limits are parameterized in SQL to prevent unbounded scans when many entries accumulate.

**JSON corruption resilience (`_safe_json_loads()`):** `_row_to_knowledge_dict()` and `_row_to_tool_dict()` previously used bare `json.loads()`, which would crash an entire list query if a single row held corrupt JSON in `metadata` or `args`. Replaced with `_safe_json_loads()` that logs a warning and returns `None` on parse failure, leaving the surrounding query intact.

**WAL checkpoint outside lock:** `wal_checkpoint()` is called *outside* `with self._lock:` to avoid a potential deadlock where SQLite's internal checkpoint lock and the Python threading lock are acquired in conflicting order. The checkpoint is wrapped in `try/except` so a failed checkpoint never aborts the caller.

**Category alignment:** The `remember` LLM tool and the REST API `_VALID_CATEGORIES` set both accept exactly `{"fact", "preference", "error", "skill", "note", "reminder"}`. Previously, `note` and `reminder` were accepted by the REST API validator but rejected by the LLM tool's own validation — causing silent inconsistency where the LLM could not store entries that the API allowed.

**`update_memory` tool category validation:** The `update_memory` tool previously passed the `category` field to `update()` without validation — the LLM could store categories like `"todo"` or `"task"` that never existed in the schema, silently breaking category-filter queries in the dashboard. Fixed: `update_memory` now checks against the same 6-category set as `remember`.

**`update_memory` tool `reminded_at` validation:** `reminded_at` previously accepted any string. If the LLM passed natural language ("next Friday", "tomorrow") it was stored verbatim and broke the SQL string comparisons in `get_upcoming()`. Fixed: `reminded_at` must be valid ISO 8601 or the special keyword `"now"` (which is converted to a tz-aware timestamp). Any other string returns an error dict to the LLM.

**`_build_dynamic_memory_context()` naive `due_at` label:** `due_dt < now` raises `TypeError` when `due_dt` is naive and `now` is tz-aware. The `except` clause caught `TypeError` silently and labelled all such items `"DUE"` even when they were overdue. Fixed: `due_dt` is normalized to tz-aware (via `.astimezone()`) before the comparison, so overdue items receive the correct `"OVERDUE"` label. (New DB entries are always tz-aware via `store()` normalization; old or directly-inserted entries may still be naive.)

**`get_all_knowledge(search=...)` all-special-char fallback:** A search string consisting entirely of FTS5 special characters (e.g. `"@@@"`, `"---"`) sanitizes to `None` inside `_sanitize_fts5_query()`. Previously, when this happened inside `get_all_knowledge()`, the FTS JOIN was skipped and the query returned *all* items — a confusing result for a non-empty search. Fixed: when a non-empty `search` sanitizes to `None`, return `{"items": [], "total": 0}` immediately.

**`log_tool_call()` unbounded `error` column:** Stack traces passed as `error` can be thousands of characters. The `result_summary` field was already capped at 500 chars but `error` was not. Fixed: `error` is also truncated to 500 chars before storage.

**`store_turn()` empty content validation:** Empty strings and whitespace-only turns were silently stored, polluting conversation history rows and adding empty entries to the `conversations_fts` FTS5 index. Fixed: `store_turn()` now returns early without writing anything when content is empty or whitespace-only.

**`store()` confidence clamping:** Programmatic callers (migration scripts, eval harnesses) could accidentally pass `confidence=999.0` or `confidence=-1.0`. These values were stored as-is, corrupting `avg_confidence` stats and making `apply_confidence_decay()` produce nonsensical values. Fixed: `confidence` is clamped to `[0.0, 1.0]` via `max(0.0, min(1.0, float(confidence)))` before storage.

**`set_memory_context()` empty/whitespace validation:** Passing `""` or `"   "` as context would set `self._memory_context` to an empty string, causing all subsequent `get_by_category()` and `search()` calls to filter on `context = ""` — matching nothing. Fixed: empty or whitespace-only strings are normalized to `"global"`.

**HTTP 422 vs 500 for invalid dashboard inputs:** Python `ValueError` raised inside a synchronous FastAPI route handler propagates as an unhandled exception and becomes HTTP 500. FastAPI's automatic 422 conversion only applies to `pydantic.ValidationError` raised by *model construction* — not to `ValueError` raised manually inside handler code. Fixed in `ui/routers/memory.py`: moved all input validation into Pydantic `@field_validator` methods on `KnowledgeCreate` and `KnowledgeUpdate`, so invalid inputs are rejected by Pydantic's model construction phase (→ HTTP 422) before any handler code runs. Specifically:
- `content` must not be empty or whitespace-only (both `KnowledgeCreate` and `KnowledgeUpdate`)
- `due_at` must be valid ISO 8601 (both models)
- `reminded_at` must be valid ISO 8601 (`KnowledgeUpdate` only)
The helper `_validate_iso8601()` is shared by all three datetime validators.

**`update()` `reminded_at` normalization (defense-in-depth):** The Pydantic validator at the API layer ensures `reminded_at` is valid ISO 8601, but direct Python callers of `MemoryStore.update()` bypass the API. Previously, a naive `reminded_at` (no tzinfo) stored via `update()` would be written as-is, producing an inconsistent mix of naive and tz-aware timestamps in the `knowledge.reminded_at` column. This could cause incorrect SQL string comparisons in `get_upcoming()`. Fixed: `update()` now normalizes `reminded_at` to tz-aware ISO 8601 (via `.astimezone()`) when the parsed datetime is naive — the same pattern already used for `due_at`. Invalid non-ISO strings raise `ValueError` at the store layer.

**`remember` tool empty `fact` raises unhandled exception:** Memory tools (`remember`, `update_memory`, etc.) are in `_MEMORY_TOOLS` and bypass the `_execute_tool` exception handler in `MemoryMixin` — they go directly to `super()._execute_tool()`. If the LLM called `remember(fact="")`, `store()` would raise `ValueError` which would propagate up to the LLM's tool loop rather than returning an error dict. Fixed: `remember` now explicitly validates that `fact` is non-empty and non-whitespace, returning `{"status": "error", ...}` before calling `store()`.

**`update_memory` tool whitespace `content` raises unhandled exception:** Same pattern as `remember`: if the LLM passed `content="   "`, the `if content:` guard evaluates `True` (non-empty string), then `update()` raises `ValueError("content must be non-empty")` which propagates instead of returning an error dict. Fixed: `update_memory` now checks `content.strip()` before adding to kwargs, returning an error dict for whitespace-only content.

**`rebuild_fts()` / `prune()` — partial DELETE committed by next unrelated operation:** Both methods follow the pattern: DELETE from FTS → INSERT into FTS → `commit()`. Python's `sqlite3` module does not auto-rollback uncommitted DML when an exception propagates — the pending DELETE transaction stays open. The *next* unrelated `commit()` call (e.g., from `store()` or `delete()`) then commits the stale DELETE, leaving the FTS table empty and all searches broken. Fixed: both `rebuild_fts()` and `prune()` now wrap their multi-step SQL in `try/except` with `self._conn.rollback()` in the `except` branch, so a mid-rebuild failure rolls back the entire transaction cleanly.

**`set_memory_context()` stale system prompt:** When the active memory context is switched (e.g., `work` → `personal`), the Agent's `_system_prompt_cache` still holds preferences and facts from the old context. The LLM would see the wrong context until the cache was next rebuilt (e.g., after adding tools). Fixed: `set_memory_context()` calls `self.rebuild_system_prompt()` if the Agent base class provides that method, so the new context's preferences and facts are immediately visible.

**`apply_confidence_decay()` never called on long-running servers:** Confidence decay was only triggered in `reset_memory_session()`, which users call explicitly. Long-running server processes (e.g., the Agent UI backend) would never call this, so knowledge confidence never decayed — eventually filling the system prompt with stale low-value facts. Fixed: `init_memory()` now calls `apply_confidence_decay()` on startup alongside `prune()`. Both are idempotent; the actual decay only fires if items have been unused for >30 days.

**`remember` / `update_memory` tools — silent content truncation:** When `fact` or `content` exceeds 2000 chars, `store()` and `update()` silently truncate. The LLM received `"Remembered: ..."` with no indication that the stored content was shorter than what it provided — this could cause the LLM to believe the full content was saved. Fixed: the `remember` tool now appends `(note: content was truncated to 2000 chars)` to the message when truncation occurs; `update_memory` adds a `"note"` key to the response dict with the same message.

**`_auto_store_error()` — empty error message stored as useless entry:** When a tool raised an exception with an empty or whitespace-only message, `_auto_store_error()` built `f"{tool_name}: "` and called `store()`. The string `"tool_name:"` passes the non-empty content validation, so a semantically empty error fact got stored — polluting the `error` category and the system prompt's "Known errors to avoid" section with no actionable signal. Fixed: `_auto_store_error()` now returns early when `error_msg` is empty or whitespace-only.

**`store()` / `update()` — empty string `entity`/`domain` breaks dedup and NULL-based indexing:** SQLite treats `entity = ""` differently from `entity IS NULL`. The `_find_similar_locked()` dedup query routes to `k.entity IS NULL` when `entity=None` and `k.entity = ?` when `entity=""`, so calling `store(entity="")` and `store(entity=None)` with the same content would create two separate knowledge entries instead of deduping. The `WHERE entity IS NOT NULL` partial index would also incorrectly count empty-string entities. Fixed: `store()` normalizes `entity=""` and `domain=""` to `None` before storage. `update()` also normalizes these to `None` — which, under `update()`'s "None = don't change" semantics, means `update(entity="")` is a no-op (the field is left unchanged rather than set to an empty string).

**`search()` confidence bump — Python-side arithmetic vulnerable to multi-process lost-update:** `search()` previously fetched `confidence` from the result row and computed the bump in Python: `new_conf = min(r["confidence"] + 0.02, 1.0)`, then wrote `new_conf` back. In a multi-process deployment (e.g., the Agent UI backend + a background agent both running against the same SQLite file), two processes could read the same confidence value simultaneously, each compute the same bump, and both write it — resulting in only one increment instead of two. The Python-side snapshot also violated the principle that store-layer invariants (clamping to 1.0) should be enforced atomically. Fixed: `search()` now uses SQL-side arithmetic `MIN(confidence + 0.02, 1.0)` in the UPDATE statement, which SQLite evaluates against the current row value at lock-acquisition time, making the increment atomic across processes. The Python dict is updated from the pre-bump snapshot for the return value (accurate enough for display; actual DB value may be slightly higher if concurrent bumps happened).

**`apply_confidence_decay()` non-idempotent — runaway decay on repeated restarts:** The method's WHERE clause was `last_used < cutoff`, but `last_used` was never updated by decay. Every call re-matched the same items and multiplied their confidence by 0.9 again. An agent restarted N times would apply 0.9^N decay to all stale knowledge — after ~22 rapid restarts, a confidence-0.5 item would drop below the 0.1 pruning threshold and be permanently destroyed. `init_memory()` calls `apply_confidence_decay()`, so this was triggered on every cold start. Fixed: the WHERE clause now also requires `updated_at < cutoff`. Because decay sets `updated_at = now`, a decayed item's `updated_at` is recent and the condition fails on the next call. After 30 days of no edits, both `last_used` and `updated_at` are old again and decay fires once more — the intended once-per-period cadence.

**`get_sessions()` first-message preview uses lexicographic MIN instead of chronological first:** The query used `MIN(CASE WHEN role = 'user' THEN content END)` to pick the session preview. SQLite's `MIN()` on TEXT returns the lexicographically smallest value, not the earliest row. A session starting with "Zebra question" followed by "Apple question" would show "Apple question" as the preview. Fixed: replaced with a correlated subquery `(SELECT content FROM conversations c2 WHERE c2.session_id = ... AND c2.role = 'user' ORDER BY c2.id ASC LIMIT 1)`, which selects the chronologically first user message by insertion order.

**`store()` FTS insert failure leaves orphaned uncommitted knowledge row:** In the new-entry branch, the knowledge INSERT was executed first, then `_insert_knowledge_fts_locked` was called. If the FTS insert raised (e.g., rowid conflict from a prior incomplete operation), the knowledge INSERT remained in an open implicit transaction. The next unrelated `commit()` call (e.g., from `store_turn` or `log_tool_call`) would then commit the orphaned knowledge row without its FTS entry — making it invisible to all full-text search queries and deduplication. Fixed: the new-entry branch is now wrapped in `try/except Exception: self._conn.rollback(); raise`, ensuring the knowledge INSERT is rolled back atomically with any FTS failure.

**WAL checkpoint in `prune()` runs outside the lock — concurrent connection race:** After `prune()` released `self._lock`, it called `self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")` without holding the lock. Python's `sqlite3.Connection` is not internally thread-safe for concurrent `execute()` calls — `check_same_thread=False` only disables the ownership check, it does not add locking. A concurrent `store_turn()` or `log_tool_call()` holding the lock and executing on the same connection could race with the checkpoint call, corrupting cursor state. Fixed: the WAL checkpoint is now executed inside the `try` block while the lock is held, before the `except` rollback clause. The checkpoint is still best-effort (`except Exception: pass`) so an `SQLITE_BUSY` error during snapshot-reader activity does not fail the prune operation.

**`rebuild_fts()` and `prune_memory()` routes expose raw exceptions as HTTP 500:** FastAPI's default unhandled-exception handler returns a generic `{"detail": "Internal Server Error"}` body for runtime errors, but in debug mode the traceback may include internal file paths and DB state. More importantly, callers had no structured way to distinguish a partial failure from a complete one. Fixed: both route handlers now wrap the store call in `try/except Exception` and raise `HTTPException(500, ...)` with a structured `f"... failed: {type(exc).__name__}"` detail string, while logging the full exception at ERROR level for server-side diagnostics.

**`store()` dedup branch — no rollback if FTS update fails:** The `if existing_id:` path in `store()` executed `UPDATE knowledge SET ...`, then called `_update_knowledge_fts_locked()`, then `commit()`. If `_update_knowledge_fts_locked()` raised (e.g., rowid mismatch, index corrupt), the UPDATE stayed in an uncommitted implicit Python sqlite3 transaction. The next unrelated `commit()` call (e.g., from `store_turn()`) would then commit the knowledge UPDATE *without* the corresponding FTS sync — the updated content would be invisible to dedup and search, and the stale FTS entry would return outdated content. Fixed: the UPDATE + FTS sync are now wrapped in `try/except Exception: self._conn.rollback(); raise`, atomically rolling back both on any failure.

**`update()` — no rollback if FTS sync fails:** `update()` set the `updated_at` and other columns on the knowledge row, then conditionally called `_update_knowledge_fts_locked()`, then committed. The same implicit-transaction pattern as the `store()` dedup branch: if FTS failed, the knowledge UPDATE could be committed stale by a later unrelated operation. This could cause `search()` to return outdated content for the entry (FTS matched the old text but the DB row had new text), silently corrupting search results. Fixed: the entire lock block is now wrapped in `try/except Exception: self._conn.rollback(); raise`.

**`delete()` — FTS and knowledge DELETEs not protected by rollback:** `delete()` first deleted from `knowledge_fts` (using a subquery to resolve the rowid), then deleted from `knowledge`, then committed. If the `DELETE FROM knowledge` somehow failed after the FTS DELETE (due to a disk error or interrupted process), and the connection stayed open long enough for another operation's `commit()` to fire, the FTS entry would be deleted without the knowledge row being deleted — the knowledge row would remain in the DB but be invisible to all FTS queries and deduplication, effectively becoming a ghost row. Fixed: both DELETEs are now wrapped in `try/except Exception: self._conn.rollback(); raise`.

**`_extract_heuristics()` — `pattern.search()` captured only the first match per pattern:** The heuristic extraction loop used `pattern.search(text)` which returns the first match object and stops. A message like "I prefer Python over Ruby. I prefer dark mode over light mode." would extract only the first preference ("I prefer Python over Ruby") and silently discard the second. Changed to `pattern.finditer(text)` which iterates all non-overlapping matches. Since each match is independently validated (10–300 char length), stored with low confidence (0.3), and subject to the existing dedup logic, there is no risk of runaway storage.

**`search()` confidence bump UPDATEs — no rollback on failure:** After finding search results, `search()` issued one `UPDATE` per result to bump `confidence` and `use_count`, then committed them all in a single `commit()`. If any UPDATE raised an exception (e.g., a disk error mid-loop), the successfully executed UPDATEs stayed in an uncommitted implicit transaction. The next unrelated `commit()` (e.g., from `store_turn()`) would commit a partial confidence-bump batch — some results bumped, others not — violating the expectation that a single `search()` call either updates all found items or none. Fixed: the entire bump loop + `commit()` is now wrapped in `try/except Exception: self._conn.rollback(); raise`, making confidence updates all-or-nothing.

**`log_tool_call()` `args_json` — unbounded storage from large tool arguments:** Tool arguments were serialized to JSON and stored verbatim. A tool such as `write_file` called with a 100 KB file body would produce a 100 KB+ JSON string stored in `tool_history.args`. Over many calls this bloats the database significantly, slows down `get_tool_summary()` aggregation queries, and could cause `_execute()` to hit SQLite's max page-size limit in extreme cases. `result_summary` and `error` were already truncated to 500 chars for the same reason. Fixed: `args_json` is now also truncated to 500 chars before storage, consistent with the other text columns.

**`store_turn()` and `log_tool_call()` — no rollback if `commit()` fails:** Both methods followed the pattern `execute(INSERT); commit()` without a rollback guard. If `commit()` raised (e.g., disk full), the uncommitted INSERT stayed in an open implicit Python sqlite3 transaction. The next operation's `commit()` would commit the row — potentially interleaved with later inserts, making timestamps and insertion order inconsistent. Fixed: both methods now wrap the INSERT + commit in `try/except Exception: self._conn.rollback(); raise`, matching the rollback discipline applied to all other write paths.

---

## Future Extensions (built on this foundation)

The memory architecture is designed to support the full AI PC assistant vision.
These features build on the schema and APIs defined above — no redesign needed.

| Extension | How memory supports it | Fields used |
|---|---|---|
| **Scheduler / Heartbeat** | Scheduler calls `get_upcoming()`, triggers `process_query()` for overdue items. Zero memory changes needed. | `due_at`, `reminded_at` |
| **Email Triage** | Email contacts as entities (`person:sarah_chen`). Email prefs as knowledge. Sensitive emails flagged. Tool history tracks email API calls. | `entity`, `sensitive`, `context="email"` |
| **Browser Automation** | Learned workflows stored as `category='skill'`. Browser errors auto-learned. Per-site preferences tracked. | `skill`, `error`, `entity="site:github.com"` |
| **Multi-Agent Workspaces** | Each agent uses a different `context`. System prompt filters by context. No cross-contamination. | `context` |
| **App Integrations** | Each app is an entity (`app:vscode`, `service:slack`). App-specific prefs and errors tracked. | `entity`, `preference`, `error` |
| **Knowledge Capture (second brain)** | Voice notes → transcribed → stored as facts. Links → summarized → stored with entity/domain tags. | `fact`, `domain`, `entity` |
| **Contact Management** | People as entities with accumulated facts. `recall(entity="person:X")` returns full profile. | `entity` pattern |
| **Calendar / Task Management** | Tasks as knowledge entries with `due_at`. Recurring tasks via LLM creating follow-ups. | `due_at`, `reminded_at`, `metadata` |

---

## Bootstrap: Day-Zero Onboarding

An empty memory is a useless memory. The agent needs to be valuable from the
first interaction — not after weeks of accumulation. Bootstrap solves the
cold-start problem through two phases: **conversation** (the agent asks you)
and **discovery** (the agent looks around your PC, with your permission).

### Design Principles

1. **Consent-first** — Every discovery source is opt-in. The agent asks before it looks.
2. **Show before store** — Discovered facts are presented to the user for review before being committed to memory. The user can edit, reject, or reclassify any item.
3. **Progressive** — Bootstrap doesn't need to happen all at once. The user can do the conversational phase now and system discovery later (or never).
4. **Repeatable** — Bootstrap can be re-run anytime to refresh the agent's understanding. New discoveries don't overwrite user-edited memories (source='user' is preserved).
5. **Private** — All discovery happens locally. Nothing leaves the machine. Sensitive discoveries are auto-flagged.

### Phase 1: Conversational Onboarding

A guided conversation that runs on first launch (or via `gaia memory bootstrap`).
The agent asks questions and stores answers as knowledge entries.

```
Agent: "Hi! I'm your GAIA assistant. Let me learn a bit about you so I can
        be helpful from the start. You can skip any question."

Agent: "What's your name?"
→ remember(fact="User's name is Alex", category="fact", context="global")

Agent: "What do you do? (e.g., software engineer, student, designer)"
→ remember(fact="Alex is a senior software engineer", category="fact", context="global")

Agent: "What are you mainly going to use me for?"
  User: "Work coding, personal task management, and learning"
→ remember(fact="Primary use cases: work coding, personal tasks, learning",
           category="fact", context="global")
→ Suggests creating contexts: "work", "personal", "learning"

Agent: "Any preferences for how I communicate? (concise vs detailed, formal vs casual)"
→ remember(fact="Prefers concise, casual communication", category="preference",
           context="global")

Agent: "What's your timezone?"
→ remember(fact="Timezone: America/Los_Angeles (PST/PDT)", category="fact",
           context="global")

Agent: "What tools/languages do you use most for work?"
  User: "Python, TypeScript, VS Code, git"
→ remember(fact="Primary stack: Python, TypeScript", category="fact",
           context="work", entity="app:vscode")
→ remember(fact="Uses VS Code as primary IDE", category="fact",
           context="work", entity="app:vscode")
```

**Implementation:** A predefined question flow in `memory.py` — a method like
`run_bootstrap_conversation()` that the agent or CLI can invoke. Not a separate
agent — just a structured conversation using the existing memory tools.

The questions are adaptive — if the user says "I'm a student," follow-up questions
shift to coursework and study habits, not deployment pipelines.

### Phase 2: System Discovery

After conversational onboarding, the agent offers to scan the local system.
Each source is a separate opt-in with a clear description of what it will access.

```
Agent: "I can learn more about your setup by looking at your system.
        Each scan is optional — I'll show you what I find before saving anything."

  [ ] File system — Scan common project folders to understand your projects
  [ ] Browser — Read bookmarks and recent history to learn your interests
  [ ] Installed apps — Check what applications you use
  [ ] Git repos — Find your projects and understand your tech stack
  [ ] Email accounts — Discover your email addresses (not email content)

  [Start Selected Scans]  [Skip All]
```

#### Discovery Sources

| Source | What it reads | What it stores | Sensitive? |
|---|---|---|---|
| **File system** | `~/`, `~/Documents`, `~/Work`, `~/Projects` — folder names + file extensions only, not file contents | Project names, languages used (by extension), directory structure | No |
| **Browser bookmarks** | Chrome/Edge/Firefox bookmark files (JSON/SQLite) | Bookmarked sites → interests, tools, frequently visited services | Partial — flag social media, banking |
| **Browser history** | Last 30 days of visited URLs (not page content) | Top domains → interests, workflow patterns, services used | Yes — auto-flag all |
| **Installed apps** | Windows Apps & Features registry, Start Menu shortcuts | App inventory → tools, IDEs, communication apps, creative tools | No |
| **Git repos** | Walk project folders for `.git/config` — read remotes, branch names | Project names, languages (by file extensions), remote URLs (GitHub/GitLab) | Partial — flag private repos |
| **Email accounts** | Windows credential store / Thunderbird profiles — addresses only | Email addresses → entity creation (`service:gmail`, `service:outlook`) | Yes — addresses only, not content |

#### Discovery Flow

```
1. User selects sources → Agent scans
2. Agent presents findings as a review list:

   "Here's what I found. Review and edit before I save:"

   PROJECTS (from git + file system):
   ✅ gaia — Python/TypeScript, remote: github.com/amd/gaia [work]
   ✅ personal-site — Next.js, remote: github.com/alex/site [personal]
   ❌ old-project — Java (remove? looks inactive)

   TOOLS (from installed apps):
   ✅ VS Code, Docker Desktop, Slack, Chrome, Spotify, OBS

   INTERESTS (from bookmarks):
   ✅ AI/ML (arxiv.org, huggingface.co)
   ✅ Music production (splice.com, ableton.com)
   🔒 Banking (chase.com) [auto-flagged sensitive]

   [Save Selected]  [Edit]  [Cancel All]

3. User reviews → Agent stores approved items as knowledge entries
```

#### Discovery Implementation

**File:** `src/gaia/agents/base/discovery.py` — System discovery module

```python
class SystemDiscovery:
    """Local system scanner for bootstrap. No agent dependencies.
    Each method returns a list of DiscoveredFact dicts, NOT stored directly.
    The caller (MemoryMixin.run_bootstrap) presents them for user review."""

    def scan_file_system(self, paths: List[Path] = None) -> List[Dict]
        """Walk project directories. Returns project names + languages."""

    def scan_git_repos(self, paths: List[Path] = None) -> List[Dict]
        """Find .git directories. Returns repo names, remotes, languages."""

    def scan_installed_apps(self) -> List[Dict]
        """Read Windows registry/shortcuts. Returns app inventory."""

    def scan_browser_bookmarks(self) -> List[Dict]
        """Read Chrome/Edge/Firefox bookmark files. Returns categorized sites."""

    def scan_browser_history(self, days: int = 30) -> List[Dict]
        """Read browser history DBs. Returns top domains. All flagged sensitive."""

    def scan_email_accounts(self) -> List[Dict]
        """Read credential store for email addresses. All flagged sensitive."""
```

Each method returns dicts like:
```python
{
    "content": "Project 'gaia' — Python/TypeScript, github.com/amd/gaia",
    "category": "fact",
    "context": "work",          # auto-inferred or "unclassified"
    "entity": "project:gaia",
    "sensitive": False,
    "confidence": 0.4,          # discovery confidence (lower than user-stated)
    "source": "discovery",      # new source type for bootstrap
    "approved": None,           # set by user review: True/False
}
```

#### Auto-Classification

Discovery results are auto-classified into contexts using simple heuristics:

| Signal | Inferred context |
|---|---|
| Found in `~/Work/` or has corporate git remote | `work` |
| Found in `~/Documents/Personal/` or personal domain | `personal` |
| Browser bookmark in "Work" folder | `work` |
| Social media / entertainment sites | `personal` |
| Can't determine | `unclassified` → user assigns during review |

#### Source: `discovery`

A new knowledge source for bootstrap-discovered items:

| Source | How it's created | Default confidence |
|---|---|---|
| `discovery` | System scan during bootstrap | 0.4 (lower — inferred, not stated) |

Lower confidence than `tool` (0.5) because these are machine-inferred, not
user-stated or LLM-decided. They'll naturally get promoted via confidence bumps
when recalled, or decay if irrelevant.

### CLI Integration

```bash
gaia memory bootstrap              # Run full bootstrap (conversation + discovery)
gaia memory bootstrap --chat-only  # Conversational onboarding only
gaia memory bootstrap --discover   # System discovery only (re-scannable)
gaia memory bootstrap --reset      # Clear source='discovery' items (with confirmation prompt)
gaia memory status                 # Show memory stats (count by source, category, context)
```

**Reset safety:** `--reset` only deletes items where `source='discovery'`. Items the
user has manually edited via dashboard (source changes to `'user'`) are preserved.
Always prompts for confirmation with a count: "Delete 34 discovered items? (y/n)"

### Agent UI Integration

A "Setup" or "Get Started" page shown on first launch:
1. Welcome screen explaining what bootstrap does
2. Conversational onboarding (chat interface)
3. Discovery source selection (checkboxes)
4. Review screen (approve/reject/edit findings)
5. Summary ("I learned 47 things about you. Ready to help!")

Accessible anytime from dashboard via "Re-run Bootstrap" button.

### Privacy Safeguards

- **No file contents** — File system scan reads names and extensions only
- **No email content** — Only discovers email addresses exist
- **No browser page content** — Only URLs/domains from history
- **No network** — Everything runs locally, nothing transmitted
- **Auto-flag sensitive** — Browser history, email, banking sites → `sensitive=1`
- **User review required** — Nothing stored without explicit approval
- **Deletable** — User can delete any bootstrap-discovered item from dashboard
- **Source tracking** — All bootstrap items tagged `source='discovery'` so user can filter/bulk-delete

---

## Implementation Order

1. `memory_store.py` — Pure data layer, fully testable in isolation
2. `memory.py` — Mixin with hooks and 5 tools
3. `agent.py` edits — Mixin prompt check + post-query hook
4. Unit tests — MemoryStore (store, search, dedup, decay, tool history, temporal, stats)
5. Integration tests — MemoryMixin lifecycle (prompt injection, tool logging, conversation storage)
6. Wire into ChatAgent — First consumer: `class ChatAgent(MemoryMixin, Agent, ...)`
7. `discovery.py` — System discovery module (file system, git, apps, browser, email)
8. Bootstrap flow — Conversational onboarding + discovery review
9. `ui/routers/memory.py` — Dashboard REST API endpoints
10. `apps/webui/src/pages/MemoryDashboard.tsx` — Frontend dashboard

---

## Known Corner Cases and Fixes

This section documents reliability issues identified and fixed during iterative hardening. Each entry explains the root cause and the applied fix.

### 1. Python sqlite3 Implicit Transaction Model

**Root cause:** Python's `sqlite3` module opens implicit transactions on any DML (`INSERT`/`UPDATE`/`DELETE`). If an exception occurs between the first DML and the `commit()`, the pending changes are not discarded — they stay in a half-open transaction and will be committed by the *next* unrelated `commit()` on the same connection.

**Pattern:** Every write path wraps DML + `commit()` in `try/except Exception: self._conn.rollback(); raise`.

**Affected methods (all fixed):** `store()` (both insert and dedup-update branches), `update()`, `delete()`, `store_turn()`, `log_tool_call()`, `search()` (confidence bump loop), `apply_confidence_decay()`, `update_confidence()`, `rebuild_fts()`, `prune()`.

### 2. Confidence Decay Idempotency

**Root cause:** `apply_confidence_decay()` sets `updated_at = now` after decay. Without guarding, a second call within the same decay window (e.g., on rapid restart) would find items with `last_used < cutoff` but `updated_at = now` (just set) and decay them again.

**Fix:** Added `AND updated_at < cutoff` to the WHERE clause so items decayed in the current period (whose `updated_at` was just bumped to now) are not re-decayed until the next period.

### 3. `get_sessions()` First Message Ordering

**Root cause:** `MIN(CASE WHEN role='user' THEN content END)` returns the lexicographically *smallest* user message, not the *chronologically first* one. For sessions starting with a long message, this could return a later, shorter message.

**Fix:** Replaced with a correlated subquery: `(SELECT content FROM conversations c2 WHERE c2.session_id = conversations.session_id AND c2.role = 'user' ORDER BY c2.id ASC LIMIT 1)`.

### 4. `_extract_heuristics()` Only Found First Match

**Root cause:** Used `pattern.search()` which returns only the first match per pattern, silently dropping subsequent matches in the same message (e.g., "I prefer Python. I prefer dark mode." stored only one preference).

**Fix:** Changed to `pattern.finditer()` to capture all non-overlapping matches per pattern.

### 5. `log_tool_call()` Unbounded args_json

**Root cause:** `result_summary` and `error` columns were truncated to 500 chars, but `args_json` (JSON-serialized tool arguments) was stored at full length. A single `write_file` call with large content could store megabytes in `tool_history`.

**Fix:** Added truncation of `args_json` to 500 chars, matching `result_summary` and `error`.

### 6. WAL Checkpoint Race Condition

**Root cause:** `PRAGMA wal_checkpoint(TRUNCATE)` was called outside `self._lock`, making it possible for a concurrent `self._conn.execute()` from another thread to race with the checkpoint.

**Fix:** Moved the checkpoint inside the `with self._lock:` block in `prune()`, wrapped in its own `try/except` (best-effort — `SQLITE_BUSY` from a reader holding a snapshot is non-fatal).

### 7. `remember()` Tool Content Not Actually Truncated

**Root cause:** The `remember()` tool set `was_truncated = len(fact) > 2000` and appended a truncation note to the return message, but passed the full `fact` string (not `fact[:2000]`) to `store()`. The database stored the complete oversized content despite the user-facing note claiming otherwise.

**Fix:** Changed `content=fact` to `content=fact[:2000]` in the `store()` call. The note now matches the actual stored content.

### 8. `update_memory()` Tool Content Not Actually Truncated

**Root cause:** Same issue as `remember()`. `kwargs["content"] = content` stored the full string; the `content_truncated` flag and its note were based on the original `content` length but nothing was actually truncated before storage.

**Fix:** Changed `kwargs["content"] = content` to `kwargs["content"] = content[:2000]`. The `content_truncated` check (based on the original `content` variable length) remains accurate.
