# C++ Framework Parity: Retrieval, Skills, and MCP

**Status:** Proposed
**Date:** 2026-08-04
**Milestone:** C++ Framework: Domain Agent Parity [OEM]
**Supersedes nothing.** Extends `docs/plans/cpp-webui-integration.md`, which explicitly scoped
the C++ side to "MCP tools only (no RAG, no file tools)". That boundary is what this plan removes.

---

## Why this matters

The C++ framework today can run a *general-purpose* agent — `gaia-bash` proves it, with a real
agent loop, tool registry, security policies, streaming, MCP stdio client, TUI, and 473 unit
tests. What it cannot run is a *domain-specific* agent. A coding agent needs to search a
codebase semantically; a document agent needs retrieval; any agent that ships behavior rather
than code needs `SKILL.md`. None of those exist in C++, so every OEM shipping a native binary
is limited to whatever tools they hand-write in C++.

The gap is not the agent loop. It is the four subsystems underneath it: **retrieval**
(embeddings + a vector index), **structured persistence** (SQLite), **portable behavior**
(`SKILL.md`), and a **complete MCP client**. Plus one correctness debt — the C++ loop drives
tools by asking the model to emit raw JSON in its prose, which modern tool-calling models
handle worse than the native `tools` / `tool_calls` protocol they were trained on.

After this milestone a C++ binary can index a repository, answer questions over it, load a
`SKILL.md` written for the Python runtime without modification, and talk to HTTP MCP servers.
The reference deliverable is `gaia-agent`: one native binary whose capability is compiled in and
whose behavior comes from `SKILL.md`, so an OEM ships one signed artifact and retargets it by
shipping skills rather than rebuilding.

The email triage agent is deliberately **not** in scope — its dependency chain (OAuth PKCE,
OS keychain, grants ledger, Gmail + Microsoft Graph backends, FTS5 hybrid memory) is ~13k LOC
of Python and belongs in a follow-on milestone. This plan lands the shared foundations it will
need (HTTP client, SQLite, vector index) so that milestone starts from a much shorter runway.

---

## Current state — what exists, what does not

Verified against `cpp/` at `9bf0042a`.

### Exists and is production-shaped

| Subsystem | Detail |
|---|---|
| Agent loop | `cpp/src/agent.cpp` — multi-step, error recovery, loop detection, cancellation, single-flight re-entrancy guard |
| Tool registry | `ToolRegistry` with fuzzy name resolution, JSON-schema arg validation, `ALLOW`/`CONFIRM`/`DENY` policies, persistent `AllowedToolsStore` |
| LLM transport | `LemonadeClient` — OpenAI `/chat/completions`, real SSE streaming via `SseParser`, model load/ensure |
| MCP | stdio client (`MCPClient` + `StdioTransport`, both Win32 and POSIX) with auto-reconnect; separate stdio *server* in `cpp/agents/bash/mcp_server.cpp` |
| Tools | `FileIOTools` (read/write/edit/search), `GitTools` (status/diff/log/show), `ProcessRunner` |
| Output | `OutputHandler` ABC + `TerminalConsole`, `CleanConsole`, `TuiConsole` (FTXUI), `JsonEventOutputHandler` |
| Persistence | `SessionStore` — conversation history as JSON under `~/.gaia/sessions/` |
| Security | `validatePath`, `isSafeShellArg`, confirmation callbacks |
| Build | CMake 3.14+, C++17, `find_package`→`FetchContent` for nlohmann/json, cpp-httplib, FTXUI, GoogleTest; install/export as `gaia::gaia_core`; Windows/Linux/macOS-arm64 |

### Does not exist at all

| Missing | Consequence |
|---|---|
| Any embedding call | No semantic search of any kind |
| Any vector index | No RAG, no code index, no memory recall |
| Any database | Everything persists as loose JSON files |
| Public HTTP client | `LemonadeClient::httpGet/httpPost` are private and Lemonade-shaped; a tool author must vendor their own client |
| Native `tools` / `tool_calls` | Request body is only `{model, max_tokens, temperature, messages}` — tool use is prompt-coerced JSON |
| Token-aware context management | History is head-truncated at 40 messages; no token counting, no overflow recovery |
| `SKILL.md` | `git grep -i skill -- cpp` returns nothing |
| MCP HTTP transport | stdio only |
| MCP protocol correctness | Sends `protocolVersion: "1.0.0"` (non-spec — the repo's own server correctly sends `2024-11-05`); never sends `notifications/initialized`; no `resources/*` or `prompts/*`; no id-correlated notification handling; `callTool` returns the raw `result` without unwrapping MCP `content[]`/`isError` |

### Known dead code to resolve, not inherit

`Agent::resolvePlanParameters()` implements `$PREV.field` / `$STEP_N.field` substitution and is
unit-tested, but is **never called by the loop** — C++ plans are advisory-only while Python
auto-executes them. This plan does not add plan auto-execution; issue P1.6 deletes the dead
resolver or wires it, so the divergence stops being silent.

---

## Architecture decisions

These are settled here so that seventeen parallel implementation PRs do not each decide them
differently.

### 1. No FAISS. A hand-rolled flat index.

Every place the Python SDK uses FAISS — RAG (`src/gaia/rag/sdk.py`), agent memory
(`agents/base/memory.py`), procedural memory (`agents/base/procedural_memory.py`), the code index
(`code_index/sdk.py`), and the memory UI router (`ui/routers/memory.py`) — constructs
`IndexFlatL2` or `IndexFlatIP`, which are *brute-force exhaustive scans*, not approximate
indexes. `grep -rn "IndexIVF\|IndexHNSW\|IndexPQ\|index_factory" src/gaia/` returns nothing:
there is no ANN structure anywhere in the codebase. Reproducing that in C++ is a few hundred lines of
straightforward numeric code with identical results and zero new dependencies, versus a heavy
BLAS-linked dependency that complicates every platform build.

`gaia::VectorIndex` implements `L2` and `InnerProduct` metrics over `float32` vectors, with
optional L2-normalization on add (matching `IndexFlatIP` usage in procedural memory).

**Explicit limitation:** C++ writes its own `index.vec` format and **cannot read Python's
`index.faiss`**. Cross-runtime index file sharing is out of scope. The C++ cache directory is
namespaced (`~/.gaia/code_index/cpp/<repo_hash>/`) so the two never collide. The shared,
documented contract is `metadata.json` — same schema, same `_CACHE_VERSION` semantics, same
embedding-model-mismatch guard.

### 2. SQLite via the vendored amalgamation.

Single public-domain `sqlite3.c` + `sqlite3.h`, compiled directly into `gaia_core` with
`SQLITE_ENABLE_FTS5`. No package manager, no `find_package` fallback path to debug, identical
on all three platforms. This is how essentially every C++ project ships SQLite.

### 3. YAML via yaml-cpp, unknown keys preserved as JSON.

`SKILL.md` frontmatter is YAML, so a parser is unavoidable. yaml-cpp 0.8.0 follows the existing
`find_package` → `FetchContent` pattern. Unknown top-level keys, foreign `metadata.<vendor>`
namespaces, and unknown `metadata.gaia` keys are captured into `nlohmann::json` blobs so
round-trip is identity — the same guarantee `Skill.extra_fields` gives in Python.

### 4. The public HTTP client hides httplib.

cpp-httplib is currently a **private** dependency and must stay one — it is a 10k-line header
that would otherwise leak into every consumer's translation units. `gaia::HttpClient` is a
pimpl-backed public header exposing `get`/`post`/`postStreaming` with header maps, timeouts, and
TLS. `LemonadeClient` is refactored to sit on top of it rather than owning raw httplib calls.

### 5. Skills are read-only in C++ for v1.

The C++ runtime **discovers, validates, loads, and injects** skills, and refuses those it cannot
honor. It does not publish, sign, install from the hub, or audit — those verbs stay in the
Python `gaia skill` CLI, and `~/.gaia/skills/` is a shared directory both runtimes read.

A skill declaring `metadata.gaia.tools` (i.e. shipping `tools.py`) is **refused with a clear
message**, because a C++ process cannot import a Python module. This covers 9 of the 10 starter
skills and all 6 email skills. Refusing loudly beats silently loading a skill whose tools will
never exist — a skill body that says "call `fetch_rss`" when `fetch_rss` is absent produces a
confidently wrong agent.

Permission handling ports verbatim: `network` and `mcp` are connector-bridged;
`filesystem`, `shell`, `database`, `desktop`, `env` at any level other than `:none` are
**refused at load**, exactly as Python does, because the sandbox does not exist in either runtime.

### 6. Prompt bytes are a cross-runtime contract.

The skills block must be byte-identical to Python's `Agent.get_skills_system_prompt()`:

```
==== LOADED SKILLS ====
--- SKILL: <name> ---
<body>

--- SKILL: <name2> ---
<body2>
```

Emitted from `Agent::composeSystemPrompt()` **before** the `==== AVAILABLE TOOLS ====` block, to
mirror Python's mixin-prompts-first ordering. It returns `""` when nothing is loaded, so every
existing C++ agent's system prompt stays byte-identical and no eval baseline moves.

`tests/fixtures/skills/` (6 skills: `bare-standard`, `incident-review`, `local-capability`,
`tool-mismatch`, `triage-support-ticket`, `web-search`) is on `main` today and is the conformance
corpus the C++ parser must pass. Two richer resources are **not yet on `main`** — see
[Forward dependencies](#forward-dependencies-on-in-flight-python-prs) below.

### 7. Native tool calling is opt-in per model, prompt-JSON stays.

Python gates on `is_tool_calling_model(model_id)`: tool-calling models get OpenAI schemas and no
response-format template; others get the JSON envelope. C++ adopts the same switch and the same
model list. The existing prompt-JSON path is not deleted — it remains the fallback, and every
current C++ agent keeps working unchanged.

### 8. The reference agent is generic and skill-programmable, not a hardcoded coding agent.

The milestone's deliverable is **`gaia-agent`**: one native binary whose *capability* is compiled
in and whose *behavior* comes from `SKILL.md`. "A coding agent" becomes a bundled skill set on top
of it rather than a second binary. This proves the skills runtime far more convincingly than a
hardcoded agent does, and it is the thing an OEM actually wants to ship — one signed binary they
can retarget without a rebuild.

Programmability has exactly two channels, and the boundary between them is the design:

**Instructions — the skill body.** Injected into the system prompt via the byte-exact contract in
decision 6. This is what makes the agent good at a domain.

**Capability — MCP servers the skill declares.** A skill carrying `mcp:connect:<id>` causes the
agent to launch that server on load and register its tools for the skill's lifetime. This is real
new capability with no in-process code loading, no interpreter embedded in the binary, and no
sandbox required — the server is a separate process the OS already isolates. It reuses
`Agent::connectMcpServer()`, which exists and already calls `rebuildSystemPrompt()`.

What is **not** a channel: a skill shipping `tools.py` (refused — a C++ process cannot import a
Python module) and a skill shipping `scripts/` it wants executed as tools. The latter is the
tempting one, and it is refused because executing skill-supplied scripts *is* the
`shell:execute` capability that decision 5 refuses at every tier until the #1019/#2672 sandbox
exists. A generic agent that runs arbitrary code from any `SKILL.md` on disk is a malware
delivery mechanism, not a feature.

Two consequences worth stating plainly:

- **`mcp:connect:<id>` needs something to resolve `<id>` against, and C++ has nothing.**
  `MCPClient::fromConfig(name, json)` accepts a config object but there is no config-*file*
  loader anywhere in `cpp/`. This is a missing prerequisite, not a detail — it gets its own issue
  (P4.3). It reads the same `mcpServers` map shape Python already uses, so a server configured
  once is reachable from both runtimes.
- **`tools_required` stays advisory**, exactly as in Python — names checked against the registry
  and warned about when absent. It is tempting to make it *gate* the toolbelt down per skill (the
  registry already has `setEnabled`/`enabledTools`, and a narrower toolbelt sharpens the prompt).
  That is a genuine improvement, but overloading one field with different semantics in the two
  runtimes would break the cross-runtime contract P5.2 exists to protect. If we want scoping, it
  gets its own explicit field in both runtimes. Out of scope here.

### 9. Benchmark against Pi — and know which gaps skills cannot close.

[Pi](https://pi.dev/) (Mario Zechner, MIT) is the closest existing thing to what we are building, and
it is an **existence proof for this architecture**: four core tools (`read`, `write`, `edit`,
`bash`), a system prompt under 1,000 tokens, and everything else supplied by skills. Its stated
rationale is ours — if you need ripgrep, run `rg` via bash; frontier models already know what a
coding agent is, so specialized tools cost prompt tokens without adding capability. Pi deliberately
ships no MCP, no sub-agents, no built-in todos.

The capability bar is not Pi but **oh-my-pi (`omp`)**, its 31-tool maximalist fork. The gap between
them is exactly the gap a skills-driven agent has to reason about, and it splits cleanly:

**What skills genuinely cover** — workflow, judgment, procedure. "How to approach a refactor," "the
review checklist," "run the build this way." Pi's success is evidence this is the right substrate,
and it is most of what makes an agent feel competent.

**What skills cannot cover, because these are properties of the tool implementation and not of any
instruction the model reads:**

1. **Edit reliability.** No `SKILL.md` can make a string-replace succeed against a file that changed
   since it was read, or stop a silent no-op. Our `FileIOTools::fileEdit` is a naive
   `old_string`/`new_string` replace with **no staleness check at all** — an edit against a stale
   read silently corrupts. Independent benchmarking is genuinely split on which *edit format* is
   best (replace beat hashline on Python; hashline led on Rust; model choice mattered more than
   format), so we should not cargo-cult a format — but *rejection on divergence* is not a format
   question and every serious harness has it.
2. **Structural correctness.** "Update all call sites" is a graph operation. An LSP rename fires
   `workspace/willRenameFiles` and fixes re-exports and aliased imports before files move; regex
   cannot. For C++ specifically this is the highest-value integration available, because C++ has the
   worst signal-to-noise ratio of any mainstream language for grep-based navigation.
3. **Ground truth about the code.** Compiler diagnostics and debugger state beat model inference.
4. **Token economics.** A skill that must be *read* to be applied costs context every turn; a tool
   encodes the behavior for free. Pi's answer is progressive disclosure — skills load on demand
   precisely so they do not exhaust the prompt cache.

Three consequences, and they are the difference between "generic" and "generic but codes well":

- **Harden the compiled-in toolbelt before blaming the skill** (P6.1). Stale-write rejection,
  ignore-aware search, and a persistent shell are prerequisites, not polish. Today
  `FileIOTools::fileSearch`'s glob matcher supports only `*` and `?` with no `.gitignore`
  awareness, and `ProcessRunner::run` is one-shot — it loses cwd and env between calls, which is
  materially worse for a build/test loop.
- **Progressive disclosure is required, not optional** (P6.4). Level-1 metadata listing is what
  makes "paste a skill and it gets used when appropriate" work without every skill body sitting in
  the prompt forever.
- **LSP/clangd is deliberately deferred** to a follow-on milestone. It is the single biggest
  remaining gap for C++ work and it is too large to bundle here; the toolbelt in P6.1 is designed so
  a `lsp` tool slots in beside it rather than requiring a rewrite.

Two cheap patterns worth copying from `omp` outright: **schema-validated sub-agent returns** (a
typed JSON object the parent reads directly, never prose it has to parse) and **P0–P3 + confidence +
ship/no-ship** as the review output shape.

### 10. The TUI has to be built, not extended.

`TuiConsole` is not a TUI. It is a headless FTXUI *element builder* — a passive `OutputHandler`
that accumulates a `vector<ChatEntry>` and can convert it to `ftxui::Element`s on demand. Nothing
in `cpp/` ever constructs a `ftxui::ScreenInteractive`, calls `Loop()`, or reads a keystroke through
FTXUI: `grep -rn "ScreenInteractive" cpp/` returns **zero hits**, and `getChatElements()` /
`getStatusBar()` have **no callers**.

The practical consequence is worse than "incomplete." In `gaia-bash`'s default interactive mode
`ReplRunner` installs a `TuiConsole`, so all agent output goes into `entries_` and is **never
displayed** — the default mode is effectively a silent REPL, and the only things on screen are
`ReplRunner`'s own `std::cout` banner and prompt. Meanwhile `docs/cpp/bash-agent.mdx` documents
scrollable history with syntax highlighting, a token-count status bar, multi-line input with
history, and a tool-approval modal. **None of those exist.** P7.4 corrects that doc.

Three landmines any TUI work must clear, all of which exist today and all of which will fight a
fullscreen screen:

- `makeStdinConfirmCallback()` does raw `cerr`/`cin` I/O and is **auto-installed** in
  `Agent`'s constructor. It will corrupt an FTXUI screen and deadlock against its input thread.
  Every non-interactive mode currently sidesteps it by installing an auto-allow lambda — the TUI
  must install a *modal-backed* callback instead.
- `ReplRunner` installs a `SIGINT` handler; FTXUI installs its own terminal and signal handling.
- All five built-in slash commands print via raw `std::cout` and would scribble over the screen.

What *is* reusable is real: `src/tui_markdown.cpp` (`renderMarkdown()`) is a self-contained,
dependency-free markdown→FTXUI renderer and the best asset here; the slash-command framework in
`ReplRunner` is clean and tested with `/run` and `/env` as working precedents; and the confirmation
*contract* (`ToolConfirmCallback`, `ToolConfirmResult`, `AllowedToolsStore`, fail-closed
enforcement) needs only a modal-backed callback swapped in.

**Testability is a first-class requirement, not an afterthought.** The Go terminal hub's loopback
control API (`tui/internal/control/`) is the design to copy, and it is unusually good: HTTP on
`127.0.0.1` only, bearer token, `/screen` `/keys` `/text` `/wait` `/frames` `/resize`, structured
self-describing state so drivers never screen-scrape, and a `MarkMsg` settle protocol that
guarantees "every key before this mark has been handled **and drawn**." A `/wait` timeout returns
**HTTP 408 carrying the screen it actually saw**, which is what makes a failed wait debuggable.
Two refusals worth copying verbatim: injection endpoints return `503` when the loop is not running
(because silently discarding an injected key and answering `200` is a lie), and port **4001 is
reserved and rejected**.

---

## Forward dependencies on in-flight Python PRs

Three artifacts this plan builds on are **real but not yet merged**. They live in open PRs, so an
implementer grepping `main` for them will come up empty. Each is a sequencing constraint, not a
missing piece of research.

| Artifact | Lands in | Needed by | If it has not merged |
|---|---|---|---|
| `src/gaia/skills/sets.py` — `SkillRef`, `SkillSets`, `SkillSetResolution`, `SkillSetError` | **PR #2695** (`feat(email,skills): bundled skills + account-keyed skill-set selection`) | **P3.4** skill sets | P3.4 blocks. It is a port of that module, and porting a moving target produces two divergent implementations. Do not start P3.4 until #2695 merges. |
| `workers/agent-hub/src/skill-manifest.ts` — a TypeScript reimplementation of the frontmatter validator | **PR #2668** (`feat(hub,skills): publish and serve skills as a first-class hub catalog lane`) | **P3.1** parser (as a template, not a dependency) | P3.1 proceeds regardless — it ports from `src/gaia/skills/format.py`, which *is* on `main`. #2668 is a convenience: a second-language port of the same validator, including its error wording and BOM/CRLF handling, is the closest prior art for a third. Use it if available. |
| `tests/fixtures/openclaw_skills/` — 26 real ClawHub skills, commit-pinned with `PROVENANCE.md` | **PR #2693** (`feat(skills): gaia skill migrate`) | **P3.1** tests, **P5.2** CI gate | Both proceed against the 6-skill `tests/fixtures/skills/` corpus that is on `main`. When #2693 merges, extend the conformance gate to the full 32. P5.2 must not make a nonexistent path a required check. |

**Consequence for wave scheduling:** P3.4 is gated on an external PR, not just on P3.1–P3.3.
Everything else in Phase 3 is independent of these and can proceed immediately.

---

## Phases and issues

Seventeen issues in five phases. Phases are dependency-ordered; issues within a phase are
independent and land in parallel.

### Phase 1 — Foundations (5 issues)

Nothing else in this plan can start until these merge. They touch `CMakeLists.txt` and the core
headers, so they land as one wave and everything downstream rebases onto them.

**P1.1 — `gaia::HttpClient`: a general HTTP client abstraction**
New `cpp/include/gaia/http_client.h` + `src/http_client.cpp`. Pimpl over cpp-httplib so the
dependency stays private. `HttpResponse{status, body, headers}`, `HttpError` with actionable
messages naming the URL and the failure. `get`, `post`, `postStreaming(cb)`, configurable
headers/timeout/TLS. `LemonadeClient` refactored onto it — its private `httpGet`/`httpPost`/
`httpPostStreaming` become thin forwards, with all 28 existing `test_lemonade_client` cases
still green. *Unblocks: P1.2, P4.1.*

**P1.2 — Embeddings API on `LemonadeClient`**
`std::vector<std::vector<float>> embeddings(const std::vector<std::string>& texts, const std::string& model, int timeoutSec)`
against `POST /api/v1/embeddings`. Port the Python batching semantics: `MAX_EMBED_CHARS`
truncation, per-batch retry, one-by-one fallback on batch failure. Plus `DEFAULT_EMBEDDING_MODEL`
and the `user.`-prefix registration rule for embedder pulls. Integration test against live
Lemonade behind `GAIA_BUILD_INTEGRATION_TESTS`. *Unblocks: P2.2, P2.3.*

**P1.3 — `gaia::VectorIndex`: flat vector index with persistence**
New `cpp/include/gaia/vector_index.h`. `add`, `search(query, k) -> vector<pair<id,score>>`,
`remove`, `size`, `dimension`, `save(path)`, `load(path)`. `Metric::L2` and
`Metric::InnerProduct`; optional normalize-on-add. Documented little-endian `.vec` binary format
with a magic header, version, dim, count. Scores match Python's convention: `1/(1+L2)` for L2,
raw dot for IP. Guards: dimension mismatch and embedding-model mismatch both raise rather than
returning garbage. *Unblocks: P2.2, P2.3.*

**P1.4 — SQLite integration and `gaia::Database`**
Vendor the SQLite amalgamation under `cpp/third_party/sqlite/` with `SQLITE_ENABLE_FTS5`. New
`cpp/include/gaia/database.h` — RAII connection, prepared statements, transactions, WAL mode,
`busy_timeout`, a schema-migration helper mirroring `MemoryStore._migrate_schema_locked`. Ships
with no consumer; P5.x and the email milestone build on it. Includes an FTS5 smoke test proving
the compile flag took.

**P1.5 — Native OpenAI tool calling and conversational response mode**
The correctness item. Add `tools` and `tool_choice` to the request body when
`isToolCallingModel(modelId)`; parse `choices[0].message.tool_calls` including parallel calls;
build spec-correct assistant messages carrying `tool_calls` and tool messages carrying
`tool_call_id` (today C++ downgrades tool results to `USER` messages with a `[Result from X]:`
prefix). Add `ResponseMode::Planning | Conversational` to `AgentConfig`, matching Python's
`response_mode`. Suppress the `RESPONSE_FORMAT_TEMPLATE` for tool-calling models. Streaming
must accumulate `tool_calls` deltas across SSE chunks. Existing prompt-JSON path preserved as
the fallback; all current agents keep working.

### Phase 2 — Retrieval (3 issues)

**P2.1 — Text extraction and chunking**
New `cpp/include/gaia/chunking.h`. Sentence-aware splitter with configurable
`chunkSize`/`chunkOverlap` matching `RAGSDK._split_text_into_chunks`. Extractors for plain text,
Markdown, and source files. **PDF/DOCX/XLSX/PPTX are explicitly out of scope** — they need
PyMuPDF-class dependencies, and the coding-agent target does not need them; the header leaves a
documented extension point and unsupported types fail loudly with the reason.

**P2.2 — RAG SDK**
New `cpp/include/gaia/rag.h` — `RAGConfig` (chunk size/overlap/maxChunks/embeddingModel/cacheDir/
baseUrl, mirroring `RAGConfig` defaults), `RAGSDK` with `indexDocument`, `reindexDocument`,
`removeDocument`, `query`, `getStatus`, `clearCache`. Per-file caching with the same HMAC-SHA256
sidecar scheme as Python (`~/.gaia/cache/hmac.key`) so a tampered cache is detected, not trusted.
Plus a `RagTools` pack registering `query_documents`, `index_document`, `index_directory`,
`list_indexed_documents`, `rag_status` — the subset of Python's 10 RAG tools that the C++ SDK
can honestly back.

**P2.3 — Code index**
New `cpp/include/gaia/code_index.h`. `chunkCodeFile(relPath, content)` producing `CodeChunk
{content, filePath, language, startLine, endLine, symbolName, symbolType, docstring, imports}`.
Regex-based symbol extraction per language (Python, JS, TS, Go, Rust, Java, C/C++) — this is what
Python's `parse_generic_file` already does; only Python-the-language uses `ast`, and C++ uses the
regex path for it too, with the fidelity difference documented. `CodeIndexSDK` with
`indexRepository`, `search(query, scope, topK)`, `getStatus`, `clearIndex`. Incremental
re-index via per-file SHA-256 against `metadata.json:file_hashes`, atomic temp→rename writes,
`.gitignore` honored, sensitive files skipped. Plus a `CodeIndexTools` pack.

### Phase 3 — Skills (4 issues)

**P3.1 — `SKILL.md` format parser and validation**
New `cpp/include/gaia/skill.h` + yaml-cpp dependency. `Skill`, `GaiaMetadata`, `SkillTool`,
`SkillRequirements`; `parseSkill`, `parseSkillFile`, `parseSkillMetadata` (frontmatter-only, for
level-1 disclosure), `validateSkill`, `toMarkdown`. Constants ported **verbatim** from
`src/gaia/skills/format.py`: name pattern `^[a-z0-9]+(-[a-z0-9]+)*$`, name ≤64, description
≤1024, SemVer 2.0.0, `0.0.0` reserved, tier enum defaulting to `experimental`, name must equal
directory name, `compatibility`/`allowed-tools`/`disallowed-tools` parsed and deliberately
ignored. BOM and CRLF tolerant. Tested against the 6-skill `tests/fixtures/skills/` corpus on
`main`; extend to `tests/fixtures/openclaw_skills/` (26 more) once PR #2693 merges.

**P3.2 — Skill permissions**
New `cpp/include/gaia/skill_permissions.h`. `<domain>:<level>[:scope]` parser (split on `:`, max
3 parts), the `DOMAIN_LEVELS` table, and the refusal rule: `network`/`mcp` are connector-bridged;
`filesystem`/`shell`/`database`/`desktop`/`env` at any level other than `:none` are **refused**
at load with `SkillPermissionError`. A bare `mcp:connect` without a scope is a validation error;
a scoped one resolves against the C++ MCP server registry or is refused. Refuse, never warn.

**P3.3 — `SkillManager` discovery and `Agent` load/unload/inject**
New `cpp/include/gaia/skill_manager.h`. Three roots in precedence order — agent-bundled
(`Agent::SKILL_DIRS`), user (`${GAIA_CONFIG_DIR:-$HOME}/.gaia/skills`), claude-import
(`./.claude/skills` then `~/.claude/skills`); a later root never overrides an earlier one;
shadowed copies and per-directory parse errors both retained for auditability.
`resourcePath(name, relative)` with `weakly_canonical` traversal refusal. On `Agent`:
`loadSkill`, `unloadSkill`, `loadedSkills`, and `getSkillsSystemPrompt()` wired into
`composeSystemPrompt()` before the tools block, emitting the byte-exact block from decision 6.
Permission gate runs **before** any registration; load is idempotent, unload is reversible, and
a failed load rolls back completely via a tool-registry snapshot. `tools_required` names are
checked against `ToolRegistry::hasTool` and warned about; a skill declaring
`metadata.gaia.tools` is refused.

**P3.4 — Skill sets and `gaia-agent.yaml` wiring**
**Gated on PR #2695**, which introduces `src/gaia/skills/sets.py` — it is not on `main` yet, and
porting a module still in review produces two divergent implementations. Do not start until it
merges. Port `SkillRef`, `SkillSets`, `SkillSetResolution`, and the
resolution order explicit → selector hook → default, where an undeclared set name **always**
raises naming the valid sets rather than falling back. On `Agent`: `skillSets()`,
`selectSkillSet()` virtual hook, `resolveSkillSet()`, `loadSkillSet()`, `activeSkillSet()`, and a
`skillSet` field on `AgentConfig`. Read `skills:` / `skill_sets:` / `default_skill_set` from the
agent's `gaia-agent.yaml`. Switching sets unloads only what the previous set added.
*Depends on P3.1–P3.3.*

### Phase 4 — MCP completeness (3 issues)

**P4.1 — HTTP / streamable-HTTP MCP transport**
New `HttpTransport : MCPTransport` on top of `gaia::HttpClient`, mirroring
`src/gaia/mcp/client/transports/http.py`. `MCPClient::fromConfig` accepts `{"url", "headers"}`
alongside the existing `{"command", "args", "env"}`, discriminating on which is present.
*Depends on P1.1.*

**P4.2 — MCP protocol correctness and resources/prompts**
Fix `protocolVersion` from the non-spec `"1.0.0"` to `"2024-11-05"`, matching what the repo's
own C++ MCP server already advertises. Send `notifications/initialized` after `initialize`. Add
id-correlated response matching so an unsolicited server notification is no longer mis-consumed
as a response. Unwrap MCP `content[]` / `isError` into text rather than returning the raw
`result` object. Add `resources/list`, `resources/read`, `prompts/list`, `prompts/get`. Replace
the naive command-string concatenation in `StdioTransport` with proper argv handling so
arguments containing quotes or shell metacharacters are safe.

**P4.3 — MCP server registry: resolve server ids from config**
The prerequisite decision 8 names. New `cpp/include/gaia/mcp_registry.h` reading the standard
`mcpServers` map (`{command, args, env}` per id) from `~/.gaia/mcp.json`, honoring
`GAIA_CONFIG_DIR`, with the same shape Python already uses so one configuration serves both
runtimes. `resolve(id) -> optional<json>`, `listServers()`, plus `Agent::connectMcpServerById(id)`
layered on the existing `connectMcpServer(name, config)`. An unresolvable id is an **error naming
the id and the config path searched** — never a silent skip. *Unblocks P3.2's `mcp:connect:<id>`
scope resolution and P5.1.*

### Phase 5 — Reference agent and validation (2 issues)

**P5.1 — `gaia-agent`: generic skill-programmable native agent**
The milestone deliverable, per decision 8. New `cpp/agents/generic/` following the
`cpp/agents/bash/` structure.

- **Fixed toolbelt, compiled in**: `FileIOTools`, `GitTools`, `CodeIndexTools` (P2.3), `RagTools`
  (P2.2), and `shell_execute` under `CONFIRM` policy.
- **Behavior from skills**: discovers skills across the three roots (P3.3), loads the active
  skill set (P3.4), injects bodies via the byte-exact prompt contract.
- **Capability from MCP**: a loaded skill declaring `mcp:connect:<id>` resolves through the P4.3
  registry, connects, and registers that server's tools. On unload, disconnect **only when no
  other loaded skill still needs that server** — refcount by id, because two skills sharing a
  server is the normal case and tearing it down under a live skill is a silent capability loss.
- **CLI**: modes matching `gaia-bash` (TUI/REPL default, `--print`/`--query`, `--serve --port`,
  `--mcp`, `--resume`, `--list-sessions`), plus `--skill-set <name>`, `--skill <name>` for ad-hoc
  loading, and `--list-skills` showing what was discovered per root, what is loaded, what is
  shadowed, and what was **refused and why** — the refusal reasons are the ones users will hit.
- Ships bundled skill sets (`coding`, `research`) as the real consumer P3.4 needs, and packages
  via `cpp/packaging/package_agents.py`.

**Naming note:** `gaia-agent` parallels `gaia-bash`. It is a distinct binary from the
`gaia agent {export|import}` CLI subcommand; if that proves confusing in review,
`gaia-native` is the fallback. Settle it in the issue, not at package time.

**Supersedes** the previously planned hardcoded `gaia-code` binary. A coding agent is now the
`coding` skill set on this binary — one artifact to build, sign, package, and eval.

**P5.2 — Cross-runtime conformance suite and docs**
The gate that makes "parity" a measured claim rather than an assertion. A conformance test that
runs the same `SKILL.md` corpus through both the Python and C++ parsers and asserts identical
accept/refuse verdicts and identical prompt bytes. The corpus is **discovered from the fixtures
directory, not hardcoded** — a required check must never name a path that does not exist — so it
ships against the 6 skills on `main` and widens to 32 automatically when PR #2693 lands
`tests/fixtures/openclaw_skills/`. A `gaia eval agent` adapter for `gaia-agent` running its bundled `coding` skill set
(pattern: `cpp/agents/bash/eval/bash_eval_adapter.py`) with a committed baseline. Docs: update
`cpp/README.md` (whose feature matrix currently lists RAG as Python-only and whose env-var table
documents only the deprecated `GAIA_CPP_*` names), `docs/cpp/overview.mdx`,
`docs/cpp/api-reference.mdx`, `docs/cpp/custom-agent.mdx`, and add `docs/cpp/skills.mdx`. Also
correct `docs/plans/cpp-webui-integration.md`, whose capability matrix asserts `rag: ❌`.

### Phase 6 — Coding competence and skill ergonomics (2 issues)

**P6.1 — Harden the coding toolbelt**
The gaps decision 9 identifies that no `SKILL.md` can close.
- **Stale-write rejection on `file_edit` and `file_write`**: anchor to a content hash captured at
  read time; an edit against a file that changed since is **rejected with the divergence named**,
  never silently applied and never a silent no-op.
- **Ignore-aware search**: replace `FileIOTools`' `*`/`?`-only glob matcher with real pattern
  matching that honors `.gitignore`. Shelling out to `grep -r` in a large repo returns
  `node_modules`/`build` noise that poisons context.
- **Persistent shell**: a session-scoped shell preserving cwd and environment across calls, so a
  build/test loop is not restarted from scratch every invocation. Keep the existing `CONFIRM`
  policy and output cap.
Designed so an `lsp` tool slots in beside these later without a rewrite.

**P6.2 — Paste-to-install skills and progressive disclosure**
Two halves of "paste a skill and the agent uses it when appropriate," mirroring Python's #2670.
- **Paste to install**: pasting `SKILL.md` content into the prompt is detected (frontmatter fence +
  a `name`/`description` pair), validated through the P3.1 parser, and written to
  `~/.gaia/skills/<name>/SKILL.md` — the shared user root, so a skill pasted into the C++ agent is
  visible to the Python runtime and vice versa. Refusals (bad frontmatter, refused permissions,
  declared `tools`) are shown with the reason **before** anything is written. Overwriting an
  existing skill requires confirmation.
- **Progressive disclosure (level 1)**: today skill selection is explicit — a loaded skill's full
  body sits in the prompt for the whole session, and an *unloaded* skill is invisible to the model.
  "Use it when appropriate" requires the model to know the skill exists. Emit a compact
  `name: description` listing of every discovered-but-unloaded skill, plus a `load_skill` tool the
  model can call. This is the level-1 disclosure both specs describe and neither runtime has
  implemented; it is also what keeps a large skill library from exhausting the prompt cache.
  **Coordinate the prompt-block format with Python** — P5.2's byte-equality gate covers the loaded
  block, and this adds a second block that must not drift.

### Phase 7 — TUI (3 issues) and validation

**P7.1 — Interactive TUI: event loop, streaming render, modals**
Per decision 10, this is net-new. Owns `ftxui::ScreenInteractive`, a component tree, an input
component with history, a scrollable transcript, and redraw-on-token posted from the agent thread.
Must clear all three landmines: install a **modal-backed** `ToolConfirmCallback` replacing the
auto-installed stdin one, reconcile `SIGINT` with FTXUI's own handling, and route the five built-in
slash commands through the screen instead of `std::cout`. Reuses `renderMarkdown()`. `TuiConsole`
needs redesign before reuse — it has no change notification and rebuilds and re-parses all 2000
entries on every `getChatElements()` call.

**P7.2 — TUI skill and MCP management screens**
The management surface. Skills: list across all three roots showing loaded / available / shadowed /
**refused-with-reason**, load and unload interactively, inspect a body, switch skill set, and paste
to install (P6.2). MCP: list servers from the P4.3 registry, connect and disconnect, show which
tools each contributes and which loaded skill required it. Model the hub screen in
`tui/internal/ui/hub/model.go` — tabs + list + modal overlays. Honor the Go TUI's design rules:
80×24 target, no colour-only signals (`[ok] [!] [ ] [..]` text markers), `esc` leaves the innermost
thing, and a failure always names the remedy key.

**P7.3 — TUI loopback control API**
Copy `tui/internal/control/` in C++: HTTP on `127.0.0.1` only, bearer token from a `0600`
discovery file, `/status` `/screen` `/keys` `/text` `/wait` `/frames` `/resize`. Structured
self-describing state so drivers never screen-scrape; a settle protocol guaranteeing injected keys
are handled *and drawn* before the response returns; `503` when the loop is not running; a `/wait`
timeout returning **408 with the screen it saw**; port 4001 reserved and rejected. This is what
makes every TUI assertion in P7.4 real rather than a screenshot someone eyeballed.

**P7.4 — `gaia-bash` supersession gate**
The proof that a skill-programmed generic agent replaces a hardcoded one. A `coding` skill set that
reproduces `gaia-bash`'s behavior on top of `gaia-agent`'s compiled-in toolbelt (`gaia-bash`
registers exactly `FileIOTools` + `GitTools` + `bash_execute` + `env_inspect`), and a **head-to-head
gate**: the same scenario suite run against both binaries, with `gaia-agent` required to match or
beat `gaia-bash` before supersession is declared. Reuse `cpp/agents/bash/eval/bash_eval_adapter.py`
and its committed scenarios so the comparison is against a real baseline, not a new one written to
pass. Only once that gate is green: mark `gaia-bash` deprecated, point its docs at `gaia-agent`, and
correct `docs/cpp/bash-agent.mdx`, which currently documents four TUI features that do not exist.

---

## Testing

Every issue lands with tests in `cpp/tests/` against the `tests_mock` target (no server
required), following the existing GoogleTest layout. Beyond that:

- **P1.2, P2.2, P2.3** additionally need integration tests under `GAIA_BUILD_INTEGRATION_TESTS`
  against a live Lemonade — an embeddings call mocked at the HTTP layer proves only that we
  called it, never that the request shape is one Lemonade accepts. This is the #1655 lesson.
- **P2.3** must be tested from a **cold cache**. An incremental-index bug is invisible when
  `~/.gaia/code_index/` is already warm from a previous run, which is exactly the state a
  developer's machine is in and a new user's is not.
- **P3.1–P3.3** run against the shared fixture corpus, and P5.2 makes the Python/C++ verdict
  agreement a CI gate rather than a spot check.
- **P1.5** changes the request body for tool-calling models, which is an LLM-affecting change:
  it requires a `gaia eval agent` run compared against the committed baseline before merge, per
  the repo eval policy.

## Risks

| Risk | Mitigation |
|---|---|
| Sixteen PRs all editing `cpp/CMakeLists.txt` and `agent.h` | Dependency waves of 4–5, each rebased onto merged foundations. Phase 1 lands alone and first. |
| P1.5 regresses existing agents | Prompt-JSON path is preserved, not replaced; the new path is gated on `isToolCallingModel`. Eval-baseline comparison is a merge gate. |
| Skill prompt bytes drift between runtimes | P5.2 asserts byte equality in CI, not by inspection. |
| Regex code parsing is lower fidelity than Python's `ast` | Accepted and documented. Python already uses regex for 7 of its 8 languages; only Python-the-language regresses, and the search path is embedding-based so symbol boundaries matter less than chunk coherence. |
| Vendored SQLite bloats build time | Amalgamation compiles as one translation unit, once. Measured in CI; it is the standard approach. |
| Cross-runtime index incompatibility surprises users | Namespaced cache directories plus an explicit limitation note in `docs/cpp/overview.mdx`. |

## Explicitly out of scope

Email triage agent and its OAuth connector / keychain / Graph-backend chain (follow-on
milestone). Agent memory with FTS5 hybrid search and cross-encoder reranking (P1.4 lands the
database foundation only). PDF and Office document extraction. Skill publish / install / sign /
audit verbs — those stay in the Python CLI. Plan auto-execution with `$PREV` substitution.
Multi-provider LLM support (Claude / OpenAI direct). Approximate-nearest-neighbor indexing.
