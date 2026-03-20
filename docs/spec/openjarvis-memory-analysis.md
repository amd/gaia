# OpenJarvis Memory System: Comparative Analysis

**Date:** 2026-03-19
**Branch:** feature/agent-memory
**Subject:** Architectural comparison of OpenJarvis memory vs GAIA agent memory

---

## Executive Summary

[OpenJarvis](https://github.com/open-jarvis/OpenJarvis) is a local-first personal AI agent framework (Apache 2.0, active as of March 2026) with a sophisticated multi-layer memory system. This report analyzes their architecture in depth and identifies components that could strengthen GAIA's memory system. No changes are proposed here — this is a pure analysis document.

---

## OpenJarvis Memory Architecture Overview

OpenJarvis implements memory across six distinct layers:

| Layer | Storage | Purpose |
|-------|---------|---------|
| Document/RAG store | SQLite+FTS5 / FAISS / ColBERT / BM25 | Indexed file knowledge |
| Episodic agent memory | `MEMORY.md` (Markdown) | Agent-managed learned facts |
| User profile | `USER.md` (Markdown) | Facts about the user |
| Agent persona | `SOUL.md` (Markdown) | Character/persona |
| Knowledge graph | SQLite (entities + relations) | Structured entity relationships |
| Session store | SQLite | Conversation history with auto-consolidation |

The key design philosophy is **separation of concerns**: each memory type has a dedicated store, a dedicated tool, and configurable size limits. All six layers are assembled into a single frozen system prompt prefix optimized for LLM KV-cache reuse.

---

## Detailed Component Analysis

### 1. Pluggable Backend Architecture

OpenJarvis defines a `MemoryBackend` ABC with five implementations, all registered via a central `RegistryBase`:

| Backend | Ranking | Notes |
|---------|---------|-------|
| `sqlite` | BM25 (via FTS5) | Default; Rust-backed for performance |
| `faiss` | Cosine similarity | In-memory; requires `faiss-cpu` extra |
| `colbert` | MaxSim (token-level) | In-memory; requires `colbert` extra |
| `bm25` | Okapi BM25 | In-memory; `rank_bm25` library |
| `hybrid` | RRF fusion | Delegates to two sub-backends |

The `hybrid` backend uses **Reciprocal Rank Fusion (RRF)**:
```
RRF_score(d) = sum_i weight_i / (k + rank_i(d))
```
Default `k=60`, equal weights, over-fetches `top_k × 3` per sub-backend, deduplicates by content string.

**GAIA comparison:** GAIA uses a single SQLite+FTS5 backend with BM25 ranking and an AND→OR fallback. We don't have pluggable backends or hybrid ranking. GAIA's BM25 is implemented in Python (not Rust).

### 2. ColBERTv2 MaxSim Retrieval

The most sophisticated retrieval mechanism in OpenJarvis:

```
score(q, d) = Σᵢ max_j cosine(qᵢ, dⱼ)
```

For each query token embedding `qᵢ`, take the max cosine similarity across all document token embeddings `dⱼ`, then sum across query tokens. This is **late interaction** retrieval — both query and document are represented as token-level embedding matrices rather than a single vector.

ColBERT is known to significantly outperform single-vector retrieval (DPR, SBERT) on retrieval benchmarks, especially for complex multi-concept queries. The checkpoint is loaded lazily on first use.

**GAIA comparison:** GAIA has no vector retrieval at all. Our search is purely lexical (FTS5 BM25). ColBERT would be the highest-quality upgrade we could make to memory search.

### 3. Knowledge Graph Memory

OpenJarvis maintains a separate SQLite database (`knowledge_graph.db`) with:

```sql
entities(entity_id PK, entity_type, name, properties JSON, created_at REAL)
relations(id PK, source_id FK, target_id FK, relation_type, weight REAL, properties JSON, created_at REAL)
```

Entity types: `agent`, `tool`, `model`, `user`, `concept`, `document`
Relation types: `used`, `produced`, `depends_on`, `similar_to`

The agent can manipulate the graph via four dedicated tools: `KGAddEntityTool`, `KGAddRelationTool`, `KGQueryTool`, `KGNeighborsTool`. Crucially, the KG backend **also implements `MemoryBackend`** — so it can be used anywhere a document store can, with `store()` mapping content to entities and `retrieve()` doing name/type/property LIKE searches.

**GAIA comparison:** GAIA uses `entity` as a free-text tagging column (e.g., `person:Alice`, `app:Chrome`) with no relational structure. We have no graph traversal, no neighbor queries, no typed relations. A KG would let GAIA answer questions like "which tools does project X depend on?" or "what has Alice mentioned lately?"

### 4. MEMORY.md / USER.md / SOUL.md Separation

OpenJarvis separates three concerns into three plain-text Markdown files:

- **SOUL.md** — Agent persona/character (max 4,000 chars, manually authored)
- **MEMORY.md** — Agent-learned episodic facts (max 2,500 chars, LLM-writable via tool)
- **USER.md** — User profile facts (max 1,500 chars, LLM-writable via tool)

Each file uses `head_tail` truncation (70% from head, 20% from tail) to preserve both start and end when the limit is exceeded. All three are loaded into the **frozen prefix** of the system prompt, designed for KV-cache reuse across turns.

**GAIA comparison:** GAIA stores everything in a single `knowledge` table partitioned by `category` (preferences, facts, goals, errors, todo) and `context` (global, work, personal). We don't distinguish between "what the agent knows" vs "what the user profile is." The three-file separation in OpenJarvis is simpler for humans to inspect and edit directly.

### 5. System Prompt Frozen Prefix (KV-Cache Optimization)

`SystemPromptBuilder` assembles a `_frozen_prefix` by loading SOUL.md + MEMORY.md + USER.md + skill index once at startup. The frozen prefix is cached and only the session context and previous state are appended per-turn.

This is a deliberate optimization: if the prefix is identical across turns, the LLM's KV cache can reuse those attention computations, significantly reducing inference cost and latency.

**GAIA comparison:** GAIA regenerates the full system prompt on every call (our `MemoryMixin.process_query()` explicitly deletes `_system_prompt_cache` before each query to inject fresh memory context). This is correct for freshness but sacrifices the KV-cache benefit. OpenJarvis solves this by separating stable content (frozen prefix) from dynamic content (session context appended after).

### 6. Session Auto-Consolidation

When a session exceeds `consolidation_threshold=100` messages, OpenJarvis automatically consolidates:

1. Takes the oldest 50% of messages
2. Summarizes the first 10 into plain text
3. Deletes old messages from DB
4. Inserts the summary as a `system` role message

**Tiered summary strategy** (`src/openjarvis/sessions/compression.py`):
- Oldest messages → one-liner summaries
- Middle tier → paragraph summaries
- Recent messages → kept verbatim

**GAIA comparison:** GAIA stores full conversation turns with no consolidation. We rely on the LLM context window. For long-running sessions or users with months of history, our approach will eventually hit context limits. We have no compression or tiered summary strategy.

### 7. Rust-Backed Hot Paths

OpenJarvis compiles a Rust extension (`openjarvis-python` crate) for the SQLite and BM25 backends. Python classes are thin wrappers calling `self._rust_impl.store()` / `self._rust_impl.retrieve()`, with results deserialized from JSON via `retrieval_results_from_json`.

**GAIA comparison:** GAIA's memory store is pure Python with `sqlite3`. The performance difference is likely minimal at current scale but would matter at hundreds of thousands of records.

### 8. Event Bus Integration

Every `store()` and `retrieve()` call publishes to a central `EventBus` (`MEMORY_STORE`, `MEMORY_RETRIEVE` event types). This decouples telemetry, tracing, and UI from the storage implementation — the MemoryBrowser UI component subscribes to these events rather than polling.

**GAIA comparison:** GAIA has no event bus. Our dashboard polls REST endpoints. An event-driven approach would enable real-time updates to the Agent UI memory dashboard without polling.

### 9. Learning Loop Integration

OpenJarvis's learning subsystem reads from the trace store to run GRPO reinforcement learning, SFT fine-tuning, and agent prompt optimization. Memory traces (query, result, agent, model) feed directly into the model improvement pipeline — so the agent's stored experiences become training data.

**GAIA comparison:** GAIA has an evaluation framework but no learning loop that reads from memory. This is a significant architectural difference — OpenJarvis treats memory as a training data source, not just a runtime context source.

### 10. Cross-Channel Session Identity

`SessionIdentity` tracks a single user across multiple channels:

```python
channel_ids: Dict[str, str]  # {"discord": "12345", "email": "alice@example.com", ...}
```

Supported channels: Discord, Email, iMessage (BlueBubbles), Feishu, WhatsApp, Mattermost, Signal, Telegram, Teams.

**GAIA comparison:** GAIA sessions are scoped to a single `session_id` UUID with no cross-channel identity linking. For a multi-interface future (voice + UI + API), this identity model would be valuable.

### 11. Security-Aware File Ingestion

The ingestion pipeline calls `openjarvis.security.file_policy.is_sensitive_file()` before indexing any file, skipping `.env`, credentials, private keys, etc.

**GAIA comparison:** GAIA's `discovery.py` has `sensitive=True` classification and user approval gates, but our RAG indexing (`gaia chat index`) has no equivalent security policy layer.

---

## Side-by-Side Comparison

| Capability | GAIA | OpenJarvis |
|-----------|------|-----------|
| Storage backend | SQLite+FTS5 (single) | SQLite+FTS5 / FAISS / ColBERT / BM25 / Hybrid (pluggable) |
| Search ranking | BM25 with AND→OR fallback | BM25, Cosine, MaxSim, or RRF hybrid |
| Semantic/vector search | None | FAISS (cosine), ColBERT (MaxSim) |
| Knowledge structure | Free-text entity tags | Full graph (entities + typed relations) |
| Memory types | Single `knowledge` table (5 categories) | 6 distinct layers (doc, MEMORY.md, USER.md, SOUL.md, KG, sessions) |
| Confidence scoring | Yes (0.5 default, +0.02 on recall, ×0.9 decay) | No explicit scoring |
| Temporal / reminders | Yes (`due_at`, `reminded_at`) | Session expiry only (no future reminders) |
| Deduplication | Szymkiewicz-Simpson coefficient (>80%) | None explicit |
| Context scoping | Yes (global/work/personal/project-x) | Per-file source metadata only |
| System prompt injection | Dynamic (fresh each query) | Frozen prefix + dynamic suffix (KV-cache optimized) |
| Session consolidation | None (full history) | Auto-consolidation at 100 turns, tiered compression |
| Tool logging | Yes (all non-memory tools) | Event bus (MEMORY_STORE, MEMORY_RETRIEVE events) |
| Bootstrap / onboarding | Yes (chat Q&A + 7-source discovery) | None documented |
| Dashboard / UI | React MemoryDashboard (15 endpoints) | Tauri/React MemoryBrowser |
| Learning loop | None | GRPO + SFT from trace data |
| Cross-channel identity | No (single session_id) | Yes (multi-channel identity map) |
| Sensitivity classification | Yes (per-record `sensitive` flag) | File ingestion security policy |
| Performance backend | Pure Python + sqlite3 | Rust extension for hot paths |
| Event-driven updates | No (polling) | Yes (EventBus publish/subscribe) |

---

## Components Worth Leveraging in GAIA

The following OpenJarvis components are interesting enough to evaluate for adoption. **These are observations only — no implementation is proposed here.**

### High Value

**1. Frozen Prefix + Dynamic Suffix for KV-Cache**
Split the system prompt into a stable frozen prefix (persona, long-term memory, user profile) and a dynamic suffix (recent context, current session). This would reduce inference latency without sacrificing freshness. Compatible with our existing `_system_prompt_cache` design.

**2. Tiered Session Consolidation**
Automatically compress old conversation turns into progressive summaries (one-liners → paragraphs → verbatim) rather than keeping full history forever. This makes GAIA's memory sustainable over months of use without hitting context window limits.

**3. MEMORY.md / USER.md Separation**
Splitting "what the agent has learned" from "facts about this user" is semantically cleaner than our single `knowledge` table. A direct user profile category already exists in GAIA (`category='preferences'`), but an explicit `USER.md`-style export would make it human-inspectable and editable outside the agent.

### Medium Value

**4. Hybrid RRF Retrieval**
Adding a hybrid search option (BM25 + vector) with RRF fusion would improve recall for complex queries. Our current AND→OR fallback is a simpler version of the same idea — the upgrade path would be adding a vector index alongside FTS5.

**5. Knowledge Graph Layer**
Our `entity` tags (`person:Alice`, `app:Chrome`) are a primitive form of entity tracking. A proper KG with typed relations would enable traversal queries and richer context injection ("show me everything related to project X"). The implementation cost is moderate; the value scales with how long the agent is used.

**6. EventBus for Memory Operations**
Publishing `MEMORY_STORE` / `MEMORY_RETRIEVE` events would let our Agent UI memory dashboard receive real-time updates via SSE rather than polling. This aligns with the existing SSE infrastructure in `src/gaia/ui/`.

### Lower Priority (for current scope)

**7. ColBERT MaxSim Retrieval**
Highest retrieval quality of all the OpenJarvis backends. Requires additional ML dependencies and GPU/NPU inference. Best deferred until the AMD hardware acceleration story for retrieval models is clearer.

**8. Cross-Channel Identity**
Relevant when GAIA expands to voice + email + messaging integrations. The `SessionIdentity` pattern (dict of `{channel: user_id}`) is worth adopting then.

**9. Security-Aware RAG Ingestion**
GAIA's `gaia chat index` command has no sensitivity filter. Adopting a `is_sensitive_file()` check before indexing would prevent accidentally surfacing credentials or private keys in RAG results.

---

## What GAIA Has That OpenJarvis Does Not

It's worth noting areas where GAIA's memory system is ahead:

| GAIA Advantage | Details |
|---------------|---------|
| Confidence decay | Memories decay in relevance over time (×0.9 after 30 unused days) |
| Temporal reminders | `due_at` + `reminded_at` for future-scheduled memory recall |
| Deduplication | Szymkiewicz-Simpson similarity prevents knowledge fragmentation |
| System bootstrap | 7-source Windows discovery (files, Git, apps, bookmarks, history, email) + conversational Q&A |
| Tool call logging | Full history of tool invocations with success/error classification |
| Context scoping | Fine-grained context isolation (global, work, personal, project-x) |
| REST API dashboard | 15 FastAPI endpoints enabling a full CRUD memory management UI |

OpenJarvis is stronger in retrieval quality, session longevity, and system architecture. GAIA is stronger in temporal awareness, deduplication, and observability.

---

## Conclusion

OpenJarvis and GAIA have arrived at complementary designs. OpenJarvis prioritizes retrieval fidelity (pluggable backends, ColBERT, KV-cache optimization) and long-session sustainability (consolidation, compression). GAIA prioritizes memory quality (deduplication, confidence decay, temporal scheduling) and observability (tool logging, REST dashboard, discovery bootstrap).

The single highest-leverage improvement available to GAIA from this analysis is **tiered session consolidation** — it's the one gap that will become a practical problem first (as users accumulate months of conversation history) and doesn't require external dependencies to implement.

The second highest-leverage improvement is the **frozen prefix / dynamic suffix** system prompt pattern, which would reduce inference latency at zero quality cost.

The rest of the OpenJarvis components (KG, ColBERT, Rust backends, EventBus) are excellent long-term directions, but not urgent for the current feature scope.
