# Port triage — thin + infrastructure agents (milestone #58)

Read-only audit, 2026-07-18. Cohort: **docqa, doc-search, hello-world, word-count,
summarize, connectors-demo, routing, builder**.
Audited against `origin/main` (post-#2060 layout: `hub/agents/<id>/<lang>/`).
Reference baseline: **email** (`hub/agents/email/python/` + `hub/agents/email/npm/`).
Sibling audit: [`port-audit-6-agents.md`](port-audit-6-agents.md) (chat/doc/file/browser/analyst/code).

Every claim was verified by reading the file at `origin/main`. Nothing is inferred from naming.

---

## Executive summary

**Only 3 of these 8 should be ported as user-facing catalog agents.** The cohort is
dominated by things that are not products: two example scaffolds, one piece of
infrastructure, one demo, and a duplicate RAG agent.

1. **The RAG triangle collapses to one.** `docqa` and `doc-search` are the *same agent*
   built two different ways, and both are weaker than ChatAgent's `doc` profile, which is
   where the real doc-Q&A logic lives. `docqa` survives; `doc-search` becomes a docs
   snippet; chat's doc profile is what gets ported *into* docqa.
2. **The 5-interface contract is undeliverable across the whole cohort — but the harness
   already exists.** `src/gaia/agents/base/server.py:76` (`AgentServer` / `run_agent_cli`)
   implements all five modes generically. It has **zero production callers**
   (`git grep run_agent_cli` → only `base/server.py` and `tests/unit/test_agent_server.py`),
   and **no cohort package declares `[project.scripts]`**. Every manifest here claims
   `api_server: true`; none can serve. This is a ~30-line-per-package wiring job, not the
   sidecar rebuild the sibling audit's finding #1 implies — a materially cheaper fix than
   previously scoped.
3. **`summarize` loads two models per instance.** `DEFAULT_MODEL = "Qwen3-4B-Instruct-2507-GGUF"`
   (`agent.py:117`) feeds `chat_sdk`, while `self.rag_sdk = RAGSDK()` (`agent.py:147`) takes
   `RAGConfig.model`'s default — `DEFAULT_MODEL_NAME` = Gemma-4-E4B (`src/gaia/rag/sdk.py:90`).
   The Qwen pin is not just drift against #2284; it makes this agent the one that evicts and
   cold-reloads the resident model on every switch, which is precisely what the consolidation
   existed to stop (`CLAUDE.md:571-573`).
4. **`routing`'s Python path is dead code that terminates the process.**
   `_create_agent_with_defaults` defaults unknown language to `"python"` (`agent.py:563-566`),
   then immediately hands it to `_enforce_typescript_only`, which calls `SystemExit(1)`
   (`agent.py:436-449`). Every "default to Python" branch is a guaranteed hard exit. Its LLM
   prompt is also wrapped in Qwen's `<|im_start|>` chat template (`agent.py:201-204`) while
   its default model is Gemma (`agent.py:69`).
5. **`builder` is the best-engineered code in this cohort and the only one that shouldn't
   move.** 1,757 lines of tests across four files, fail-loudly model selection
   (`agent.py:121-159`), path-traversal guard (`agent.py:545-548`), cleanup-on-write-failure
   (`agent.py:622-635`). It is scaffolding infrastructure, not a catalog product.

**Net: 3 PORT (docqa, summarize, connectors-demo — the last conditionally), 1 MERGE
(doc-search → docqa), 2 DISCARD-to-templates (hello-world, word-count), 2 stay
infrastructure (routing, builder).**

---

## Per-agent findings

### 1. docqa — `hub/agents/docqa/python/`

> **VERDICT: PORT — and it is the single surviving RAG agent.**
> It is the only one of the three with a published wheel slot
> (`setup.py:42`, `gaia-agent-docqa`), a resolving entry point, and a
> `productivity` (not `examples`) category. But "port" here means *rebuild*: the
> package today is an 81-line shell with a one-sentence system prompt.

**Generalization scope.**

- **The system prompt is one line.** `agent.py:80-82` returns a single sentence. Chat's doc
  profile — the thing users actually experience as "doc Q&A" — assembles ~10 prose blocks
  with 13 anti-hallucination rules plus `tool_bundles.py`'s 12 cohesion groups and a dynamic
  tool loader (see sibling audit §2). None of that exists here. **This is the port's real
  payload**, not packaging.
- **`rag_documents` is a dead config field.** Declared at `agent.py:24`, never read anywhere
  in the package (`git grep rag_documents hub/agents/docqa` → the dataclass line and one
  test asserting it's `None`). A user setting it gets silence.
- **`claude_model = "claude-sonnet-4-20250514"` hardcoded** at `agent.py:20` — the fourth
  copy of that literal in the repo (sibling audit §1B).
- **`_mcp_manager = None` is set manually** at `agent.py:52-55` with a comment explaining
  that `MCPClientMixin.__init__` never runs because `Agent.__init__` doesn't chain `super()`.
  The agent inherits `MCPClientMixin` (`agent.py:33`) and then disables it. Either drop the
  mixin or fix the MRO — shipping a mixin you neutralize in the constructor is a trap for
  the next reader.

**Capability-truth check (manifest vs code).**

| Manifest claim | Reality |
|---|---|
| `tools_count: 0` (`gaia-agent.yaml:11`) | **False.** Registers four mixins' tools (`agent.py:70-73`): RAG (~10) + file + file_search + file_io (~19 in fileio alone, per sibling audit). Off by ~30. |
| `permissions: [filesystem:read]` (`:27-28`) | **False and security-relevant.** `FileIOToolsMixin` (`agent.py:31`) provides write/edit. The manifest under-declares write access. |
| `interfaces.api_server: true` (`:34`) | No server, no entry point (see §Contract gaps). |
| `description: "RAG document Q&A and indexing"` | Directionally true but oversells: no indexing lifecycle, no watchdog, no citation enforcement beyond the one-line prompt. |

**Fail-loudly violations.**

- `agent.py:43-50` — `except ImportError: self.rag = None`. The RAG SDK failing to import
  silently produces an agent whose entire purpose is dead. Every RAG tool then fails deep in
  the call stack against `None` instead of at construction with "install `amd-gaia[rag]`".
- `agent.py:69-78` — `except (ImportError, AttributeError)` around all four
  `register_*_tools()` calls, logged at **debug**. A partially-registered agent boots looking
  healthy. Note `doc-search` does the same registration with no try/except at all
  (`doc-search/agent.py:75-79`) — the two packages disagree about whether this can fail.

**Test/eval reality.** `tests/test_docqa_agent.py` — 61 lines, 5 tests. Three construct the
agent; the strongest (`:52-57`) asserts three tool *names* exist in the registry. **Zero
tools are ever called.** Behavioral coverage of the tool surface: 0%.
Eval: the string `docqa` appears nowhere in `src/gaia/eval/`. The 7 `rag_quality` scenarios
and 3 committed baselines (`tests/fixtures/eval_baselines/*/scorecard_rag_quality.json`)
run `agent_type: doc` — **ChatAgent's `prompt_profile`, not this package**. Best-positioned
of the cohort, but currently measuring a different agent.

**v2 contract gaps.** Entry point `docqa = "gaia_agent_docqa:build_registration"`
(`pyproject.toml:16`) — **resolves**. `interfaces` declares `pipe`/`api_server`/`mcp_server`
true (`gaia-agent.yaml:33-35`); no `[project.scripts]`, no `run_agent_cli` call, no
`server.py`, no OpenAPI. README is 22 lines. No CHANGELOG / CONTRACT / CAPABILITY_MATRIX /
SCORECARD (email has all four).

---

### 2. doc-search — `hub/agents/doc-search/python/`

> **VERDICT: MERGE INTO docqa — then delete the package, keep the file as a docs example.**
> It duplicates docqa's RAG half with *less* capability, is categorized
> `examples` / `security_tier: experimental` (`gaia-agent.yaml:8-9`), and is **not in
> `setup.py:30-46`'s `AGENT_WHEEL_PACKAGES`** — it was never slated to publish.

Two packages, one job. `doc-search` composes `RAGToolsMixin` only (`agent.py:45`); `docqa`
composes that plus three file mixins (`agent.py:27-34`). Same default model
(`doc-search/agent.py:59`, `docqa/agent.py:46`). Same purpose in the manifest description.
A catalog listing both teaches users nothing except that GAIA ships two doc agents and
can't say which to use.

**What to salvage before deleting.** `doc-search` is the *better-written* of the two and
its assets should move into docqa, not be discarded:

- Its 12-line system prompt (`agent.py:30-42`) already encodes index-first, answer-only-from-
  retrieval, always-cite, and say-so-on-empty. docqa's one-liner (`agent.py:80-82`) has none
  of that. **Move it.**
- `self._snapshot_tools()` at `agent.py:79` isolates the agent's tools from the
  process-global registry. docqa omits this — a real cross-agent leakage bug in docqa.
- Its module docstring (`agent.py:3-23`) is the clearest "how to compose a mixin" explainer
  in the repo. That is documentation value, and it belongs in
  `docs/sdk/core/agent-system.mdx` or the `gaia agent init` template, not in the catalog.

**Generalization scope (if it were kept — it shouldn't be).** No file access, so "index the
documents in ./docs" (its own conversation starter, `agent.py:54`) depends entirely on RAG
mixin tools; it cannot read a file the index missed.

**Capability-truth check.** `tools_count: 10` (`gaia-agent.yaml:12`) is asserted by a test
(`tests/test_doc_search.py:22`) but nothing verifies it against the actual registry — it is
a hardcoded number checked against another hardcoded number. `interfaces` claims all five
true (`:28-33`), the most aggressive claim in the cohort, from a 80-line package.

**Fail-loudly violations.** None. It constructs `RAGSDK` unguarded (`agent.py:67`) and
registers tools unguarded (`agent.py:77`) — correct behavior, and a direct contradiction of
docqa's defensive swallowing of the same two operations.

**Test/eval reality.** `tests/test_doc_search.py` — 43 lines, 3 tests. One is
`issubclass(DocSearchAgent, RAGToolsMixin)`. No tool is called. No eval coverage.

**v2 contract gaps.** Entry point resolves (`pyproject.toml:19`). No scripts, no server, no
release docs.

---

### 3. hello-world — `hub/agents/hello-world/python/`

> **VERDICT: DISCARD from the catalog — promote to the `gaia agent init` scaffold template.**
> `category: examples`, `security_tier: experimental` (`gaia-agent.yaml:8-9`), not in
> `AGENT_WHEEL_PACKAGES` (`setup.py:30-46`). Publishing "an agent that greets you" to a
> user-facing hub costs catalog credibility and returns nothing a user wants.

The code is *good* — it is simply not a product. `agent.py:3-22` is a 20-line annotated
"anatomy of a GAIA agent"; `agent.py:62-65` explains why `response_mode` must be set before
`super().__init__()`. That is exactly what a scaffold template should say, and #2295 just
landed `gaia agent init --layout hub` to consume one.

**Recommended disposition:** move to the `gaia agent init` template set (`src/gaia/cli_agent.py`
already carries manifest fixtures at `:1259`/`:1296`), delete the hub package, and keep the
docstring as the canonical "your first agent" doc.

**Generalization scope.** N/A by design — it has no capability to generalize. The one thing
a user would expect from a "hello world" that's missing is a *runnable command*: there is no
`[project.scripts]`, so `hello-world` cannot actually be run standalone. As the flagship
first-run example, that's the single most important gap.

**Capability-truth check.** `tools_count: 0` (`gaia-agent.yaml:12`) — **correct**, one of
two accurate `tools_count` values in the cohort. `interfaces` claims all five true
(`:28-33`) — false for all five.

**Fail-loudly violations.** None.

**Test/eval reality.** 62 lines, 4 tests. Notably the *best-honest* test in the cohort:
`:47-49` clears the process-global `_TOOL_REGISTRY` before asserting emptiness, acknowledging
the shared-registry hazard that docqa's tests paper over with subset checks. No eval — and
none needed.

**v2 contract gaps.** Entry point resolves (`pyproject.toml:19`). No scripts/server.

---

### 4. word-count — `hub/agents/word-count/python/`

> **VERDICT: DISCARD from the catalog — promote to the `gaia agent init --with-tool` template.**
> Same reasoning as hello-world: `examples` / `experimental` (`gaia-agent.yaml:8-9`), not in
> `AGENT_WHEEL_PACKAGES`. `wc` is not an agent.

**Generalization scope.** The obvious missing capability for something named "word count" is
counting words *in a file* — it only accepts a string argument (`agent.py:96`), so a user
must paste the document into chat. Fixing that means adding file I/O, at which point it
overlaps `fileio` and stops being a one-tool example. **That tension is the argument for
discarding rather than generalizing**: its value is pedagogical precision, and every
generalization destroys it.

**Capability-truth check.** `tools_count: 1` (`gaia-agent.yaml:12`) — **correct**, the other
accurate one. `interfaces` all-true (`:28-33`) — false.

**Fail-loudly violations.** None. `count_text_stats` handles empty input explicitly
(`agent.py:59-64`) rather than swallowing.

**Test/eval reality.** 58 lines, 6 tests — and this is the **only package in the cohort that
actually exercises its tool's behavior**. `:18-40` calls `count_text_stats` with three
inputs and asserts real outputs. That the trivial example has the cohort's only behavioral
test is the clearest evidence that test quality here tracks *effort available*, not agent
importance. No eval; none needed.

**v2 contract gaps.** Entry point resolves (`pyproject.toml:19`). No scripts/server.

---

### 5. summarize — `hub/agents/summarize/python/`

> **VERDICT: PORT — highest-value port in the cohort, and the one with the most real work.**
> It is a genuine product capability (PDF/transcript/email summarization), already in
> `AGENT_WHEEL_PACKAGES` (`setup.py:31`), with 903 LOC of real logic and 902 lines of real
> tests. It also carries the cohort's worst fail-loudly record and a model configuration that
> actively fights the #2284 consolidation.

**Generalization scope.**

- **The Qwen pin is drift, not justification.** `DEFAULT_MODEL = "Qwen3-4B-Instruct-2507-GGUF"`
  (`agent.py:117`, mirrored `gaia-agent.yaml:15` and `__init__.py:50`) carries **no comment
  explaining why** — compare `agent.py:145-146`, where the `max_ctx_size` default *is*
  justified inline. `CLAUDE.md:571` states every agent shares one model id so switching never
  evicts; `:573` lists Summarizer as the lone exception. There is no evidence in the package
  that Qwen was measured against Gemma for this task.
- **Worse: it pulls two models.** `self.chat_sdk = AgentSDK(AgentConfig(model=self.model))`
  → Qwen (`agent.py:142-146`), while `self.rag_sdk = RAGSDK()` (`agent.py:147`) uses
  `RAGConfig.model`'s default = `DEFAULT_MODEL_NAME` = Gemma (`src/gaia/rag/sdk.py:90`).
  Then `super().__init__()` is called with **no arguments** (`agent.py:173`), so the base
  `Agent` also resolves Gemma (`src/gaia/agents/base/agent.py:516`). One instance, two model
  ids. **Recommendation: drop the pin to Gemma-4-E4B and re-baseline; if Qwen genuinely wins
  on summarization quality, that must be shown in a scorecard, not asserted by a constant.**
- **`max_ctx_size = 8192` is hardcoded** (`agent.py:122`), driving `chunk_tokens = 5734`
  (`agent.py:160`). The repo pins context per *device profile* — `GPU_CTX_SIZE` 65536 /
  `NPU_CTX_SIZE` 32768 (`CLAUDE.md:572`). On a GPU box this chunks an 8x-larger window into
  needless pieces, costing both latency and cross-chunk coherence. Should read the device
  profile.
- **Cache dir is relative:** `Path(".gaia") / "text_cache"` (`agent.py:176`) — cwd-scoped, so
  the cache silently misses whenever the user runs from a different directory. Every other
  GAIA path is `~/.gaia/*`.
- **Directory scan is extension-allowlisted** to 6 suffixes (`agent.py:876`) — `.docx`,
  `.pptx`, `.html`, `.rtf` silently excluded despite `amd-gaia[rag]` shipping pptx/pdf
  parsers (`doc-search/pyproject.toml:14-15`).

**Capability-truth check.** `tools_count: 0` (`gaia-agent.yaml:11`) — **correct**;
`_register_tools` is intentionally empty (`agent.py:200-201`). This agent bypasses the tool
framework entirely and drives `AgentSDK`/`RAGSDK` directly, which is why it has no tools and
also why the generic `AgentServer` harness will expose nothing useful over MCP for it. The
description ("PDFs, transcripts, and email") is honest — all three have prompt paths
(`prompts.py`, `agent.py:214-218`).

**Fail-loudly violations** — the worst in the cohort, 9 distinct sites:

| Line | Violation |
|---|---|
| `agent.py:572-573` | `except (json.JSONDecodeError, ValueError, KeyError): pass` — email participant parsing fails, result silently omits `participants`. **(known)** |
| `agent.py:828-829` | `except Exception: return None` in `_resolve_text_cache_paths` — any hashing/IO error disables caching invisibly. **(known)** |
| `agent.py:460-470` | Retry loop that swallows the final failure: after `max_retries`, logs error and sets `detected_type = "transcript"`. A PDF silently gets transcript prompts. Textbook prohibited retry. |
| `agent.py:784-792` | Encoding cascade ending in `else: text = ""` — an undecodable file yields an empty summary instead of an error. |
| `agent.py:776-777`, `800-803` | `except Exception:` → non-atomic `write_text` fallback. Masks the exact partial-write hazard the atomic `replace` was for. |
| `agent.py:205-208`, `671-674`, `505-506` | Three copies of `except Exception: log.warning` on `clear_history()`. If history doesn't clear, the *next* summary is contaminated by prior context — a silent correctness bug, not a cosmetic one. |
| `agent.py:373-375` | `except Exception: perf_stats = {}` → the reported token counts and TTFT become zeros indistinguishable from real zeros. |
| `agent.py:108-109` | `try: logger.info(...) except Exception: logger.warning(...)` — a try/except around a log call. Delete. |
| `agent.py:680-681` | `if content_type == "email" and "participants" in applicable_styles: pass` — dead conditional, presumably a removed branch. |

Only `agent.py:177-180` (`raise RuntimeError(...) from e`) and `:858-860` (log-then-`raise`)
follow the rule.

**Test/eval reality.** `tests/test_summarizer.py` — **902 lines** plus a 39-line conftest.
By far the strongest suite in the cohort and genuinely behavioral (it exercises chunking,
style validation, content-type detection). The gap is not volume but **the untested paths are
exactly the swallowing ones** — none of the 9 sites above has a test asserting the failure is
surfaced.
Eval: no scenario, dataset, or baseline mentions `summarize` anywhere in `src/gaia/eval/` or
`eval/scenarios/`. Summarization quality is currently unmeasured — which is why the Qwen pin
can't be defended or refuted today.

**v2 contract gaps.** Entry point `summarize = "gaia_agent_summarize:build_registration"`
(`pyproject.toml:16`) — **resolves**, with a well-implemented lazy `__getattr__`
(`__init__.py:20-27`) so discovery stays cheap. `interfaces` claims `cli`/`pipe`/`api_server`/
`mcp_server` (`gaia-agent.yaml:29-32`); none wired. README 37 lines; no CHANGELOG/CONTRACT/
CAPABILITY_MATRIX/SCORECARD.

---

### 6. connectors-demo — `hub/agents/connectors-demo/python/`

> **VERDICT: DEFER — keep as an in-repo integration fixture; re-decide once the real
> Google-backed agents land.** It is in `AGENT_WHEEL_PACKAGES` (`setup.py:39`) and is
> `category: productivity` (`gaia-agent.yaml:8`), which currently makes it look like a
> product. It is not — it is the connector framework's live test.

**The argument.** Its own module docstring says so: "#926 adds three things that needed a
real consumer to validate" (`agent.py:9-18`). It works, it's well-tested, and it validates
the grant flow end-to-end. But its four tools (Gmail subjects, today's calendar, recent
Drive files, GitHub repos) are each a thin read that the shipping `email` agent already does
properly, and that milestone #48's `knowledge`/`files`/`crm` agents will do better. Shipping
it to a catalog invites users to install "Connectors Demo" and find a toy.

**Two clean options, both better than publishing as-is:**
1. Keep as an in-repo integration fixture (recommended) — recategorize `examples` /
   `experimental`, drop from `AGENT_WHEEL_PACKAGES`, keep the 520-line test suite as the
   connector framework's regression harness.
2. If a "connected services at a glance" product is genuinely wanted, that is milestone #48's
   `morning`/briefing agent, not this.

**Generalization scope.**

- **Gmail is N+1 HTTP calls** — one list plus one detail fetch per message
  (`agent.py:184-195`) with no batching. At the max of 25 (`agent.py:380`) that's 26
  round-trips.
- **Calendar is hardcoded to `primary`** (`agent.py:217`); secondary/shared calendars are
  invisible.
- **Google-only + a single GitHub PAT.** No Microsoft path, though the email sidecar spec
  already declares `forward_providers=("google", "microsoft")`
  (`src/gaia/daemon/sidecars/spec.py:111`).
- **`_today_window_iso` uses `time.max`** (`agent.py:170`), i.e. 23:59:59.999999 — an event
  starting in that last microsecond is dropped. Minor, but it's the kind of thing a
  "reference implementation" gets copied for.

**Capability-truth check.** `tools_count: 4` (`gaia-agent.yaml:11`) — **correct** (four
`@tool`s, `agent.py:368/384/395/408`). `models: []` (`:15`) — **false**: the agent resolves
`DEFAULT_MODEL_NAME` (`agent.py:340`). An empty models list tells the hub UI this agent needs
no model. `interfaces.api_server: true` with everything else false (`:27-32`) is the cohort's
most honest interfaces block, and it's still unwired.

**Fail-loudly violations.** The four `except BaseException` handlers (`agent.py:208`, `238`,
`256`, `283`) return `{"ok": false, "error": ...}`. Translating an exception into a structured
tool error at a boundary is **explicitly allowed** by CLAUDE.md — so these are *not* silent
fallbacks. But `BaseException` catches `KeyboardInterrupt`, `SystemExit`, and
`GeneratorExit`: a user pressing Ctrl-C mid-Gmail-fetch gets a JSON error envelope and the
agent loop continues. **Narrow all four to `Exception`.** The `noqa: BLE001` comments show
this was a deliberate choice; it's the wrong one.
Also `agent.py:344` hardcodes `http://localhost:13305/api/v1` as an env fallback — a fifth
copy of that literal (sibling audit §1B).

**Test/eval reality.** 520 lines. Strong for what it covers: it patches `get_credential_sync`
and asserts each tool impl's success and error translation. It deliberately never constructs
the agent (`tests/test_connectors_demo.py:5-11`), so tool *registration* and the system
prompt are untested. No eval coverage — and evaluating it would mean evaluating the connector
framework, which is the right framing for why this is a fixture.

**v2 contract gaps.** Entry point resolves (`pyproject.toml`). No scripts/server. README 23
lines. No release docs.

---

### 7. routing — `hub/agents/routing/python/`

> **VERDICT: DISCARD from the catalog — it is infrastructure and must not appear in a
> user-facing hub. Keep the wheel (`setup.py:43`) as a private dependency of the API server;
> recategorize and mark it hidden.**
> The package *already knows this*: `pyproject.toml:15-16` — "no `gaia.agent` entry point.
> RoutingAgent is infrastructure loaded by class path" — and `gaia-agent.yaml:27-29` repeats
> it. It is loaded from `src/gaia/api/agent_registry.py:35` by class path.

**The argument.** A catalog entry a user cannot install, select, or converse with is not a
catalog entry. Worse, `category: infrastructure` (`gaia-agent.yaml:8`) is a taxonomy value
that milestone #48's AH-X4 (#1510, "Hub category taxonomy + filters for the 22 agents") has
to either render or hide — better to settle it now as *not listed*. And it does not inherit
`Agent` at all (`agent.py:19`, a bare `class RoutingAgent:`), so the generic `AgentServer`
harness cannot wrap it, the registry cannot instantiate it, and the Agent UI cannot show it.
It is a library, correctly packaged as a library.

**Generalization scope — and a live bug.**

- **`_enforce_typescript_only` calls `SystemExit(1)` from library code** (`agent.py:436-449`).
  A routing helper imported by the API server terminating the process is a fail-*fatally*,
  not a fail-loudly; it should raise a typed error the server converts to a 4xx.
- **The Python default path is unreachable-by-design and always fatal.**
  `_create_agent_with_defaults` sets `language = "python"` for unknowns
  (`agent.py:563-566`, logging "Defaulting to Python for unknown language"), then calls
  `_enforce_typescript_only` at `:581-583`, which `SystemExit(1)`s because python ≠ typescript.
  Same for the `project_type = "script"` branch (`:576-578`). **Every log line claiming a
  Python default is a lie the next line kills the process over.** A test even pins this
  behavior (`tests/test_routing_agent.py:351`, `test_python_raises_system_exit`).
- **Chat-template/model mismatch.** The prompt is wrapped in Qwen's ChatML
  (`agent.py:201-204`, `<|im_start|>` / `<|im_end|>`) with matching stop tokens at `:211`,
  while `routing_model` defaults to `Gemma-4-E4B-it-GGUF` (`agent.py:69`). Post-#2284 this is
  stale — Gemma does not use ChatML.
- **`_fallback_keyword_detection` (`agent.py:246-353`, 107 lines) is dead code.** No caller
  exists in the package; the only references are five tests
  (`tests/test_routing_agent.py:266-291`) that call it directly. 100 lines of maintained,
  tested, never-executed logic.

**Capability-truth check.** The manifest description — "meta-agent that routes requests to
the right concrete agent" (`gaia-agent.yaml:4`) — is **false**. `_create_agent` handles
exactly one agent type and raises on anything else (`agent.py:528-547`). The class docstring
is honest ("Currently handles Code agent routing. Future: Jira, Docker, etc.",
`agent.py:23`); the manifest is not. `entry_class: RoutingAgent` (`:19`) implies a
constructible registry agent that does not exist. `tools_count: 0` (`:11`) — correct.
`models: [Gemma-4-E4B-it-GGUF]` (`:15`) matches `agent.py:69`.

**Fail-loudly violations.**

- `agent.py:232-241` — on `JSONDecodeError`, fabricates an analysis dict with
  `confidence: 0.0` and `"unknown"` parameters. The LLM producing garbage is indistinguishable
  downstream from the LLM genuinely being unsure. The very next handler (`:242-244`) does it
  right: `raise RuntimeError(...) from e`. Two adjacent handlers, opposite philosophies.
- `agent.py:139-142` — empty user input at the clarification prompt logs a warning and
  proceeds with defaults, which (per above) then `SystemExit`s.

**Test/eval reality.** 510 lines, and the highest *fidelity-to-implementation* in the cohort —
which is the problem: it faithfully pins the SystemExit behavior (`:351`) and the dead
fallback (`:252-291`) rather than flagging them. Tests documenting a bug as intended. No eval
coverage.

**v2 contract gaps.** **No entry point at all** (`pyproject.toml:15-16`) — deliberate and
correct for infrastructure, but it means `interfaces.api_server: true` (`gaia-agent.yaml:34`)
cannot be satisfied by any mechanism. A manifest for a package that isn't a registry agent is
itself the contract gap: **recommend dropping `gaia-agent.yaml` entirely** rather than
shipping one that claims interfaces it structurally cannot have.

---

### 8. builder — `src/gaia/agents/builder/` (not a hub package)

> **VERDICT: STAY IN-CORE now; absorb into Agent Factory (milestone #42) tooling later.
> Do not make it a hub package.**
> It is the last reserved builtin (`src/gaia/agents/registry.py:70-72`) and registered
> unconditionally at `:670-703`.

**The argument, three ways:**

1. **Bootstrapping.** BuilderAgent's job is to *create* agents. If it ships as a hub package,
   a user with a fresh `amd-gaia` install has no way to scaffold their first agent without
   first knowing to install a package they've never heard of. The tool that produces hub
   packages must not itself be one.
2. **It is already coupled to core internals** that a wheel would have to depend back on:
   the registry's template constant (`registry.py:37`, "Consumed by BuilderAgent's template"),
   and the UI hot-reload path `gaia.ui._chat_helpers.get_agent_registry`
   (`agent.py:638-643`). Extracting it creates a circular dependency between core and a hub
   wheel.
3. **Agent Factory (#42) is the right eventual home**, because BuilderAgent and
   `gaia agent init` (`src/gaia/cli_agent.py`, which just gained `--layout hub` in #2295) are
   two front-ends over one scaffolding capability and are drifting apart. Converging them
   under the Factory is the real fix; porting Builder to the hub first would be motion in the
   wrong direction.

**Generalization scope.**

- **It scaffolds exactly one shape:** a conversational, no-tool agent, optionally with an
  empty `mcpServers` block (`agent.py:616-627`). A user asking the *agent builder* for "an
  agent that reads my CSVs" gets a prompt-only agent with no tools. Composing from
  `KNOWN_TOOLS` by name is the obvious missing capability, and it's the single highest-value
  generalization in this whole cohort — the framework side already exists.
- **Output target is fixed to `~/.gaia/agents/<id>`** (`agent.py:542`). Post-#2295 the hub
  layout is `hub/agents/<id>/python/`; the builder cannot emit a publishable package. It
  produces a local single-file agent, full stop.
- Hardcoded model preference list `BUILDER_PREFERRED_MODELS` (`agent.py:140-147`) — justified
  here, since `_select_builder_model` is a deliberate #2243 safety net.

**Capability-truth check.** No `gaia-agent.yaml` exists (it's not a package), so there is
nothing to be untrue. `tools_count` would be 1 (`create_agent`, `agent.py:237-238`).

**Fail-loudly violations — best record in the cohort, one real finding.**

- `agent.py:645-646` — `except Exception: logger.warning("Hot-reload skipped")`. Then
  `:653` tells the user *"It's already loaded — you'll see it in the agent selector."*
  **The success message is emitted on the failure path.** The file did write, so this isn't
  data loss, but the user is told something false and will go looking for an agent that
  isn't there. Fix by branching the message on whether the reload succeeded.
- Everything else is exemplary and worth citing as the pattern the rest of the cohort should
  copy: `_select_builder_model` distinguishes "Lemonade unreachable" from "nothing installed"
  and raises typed errors with install commands (`agent.py:121-159`); path traversal is
  rejected (`:545-548`); generated source is `ast.parse`-validated before writing (`:606-610`);
  write failures `rmtree` the partial directory and return an actionable message
  (`:622-635`); the broad LLM handler at `:347-360` extracts the typed Lemonade message
  rather than substituting a generic placeholder (explicitly the #2243 lesson).

**Test/eval reality.** **1,757 lines across four files** — `test_builder_agent.py` (1,141),
`test_builder_model_selection.py` (255), `test_builder_fail_loudly.py` (242),
`test_builder_fenced_integration.py` (119). The only agent in the cohort with a dedicated
fail-loudly test file. This is the cohort's quality ceiling and the reason "port it for
quality reasons" does not apply. No eval scenario — scaffolding output is deterministic and
better served by the existing integration tests than by an LLM judge.

**v2 contract gaps.** N/A — not a package, correctly. If #42 later needs it addressable, that
should be a Factory-internal interface, not a hub manifest.

---

## Summary table

| Agent | Verdict | Generalization | Tests | Eval | Packaging | Why |
|---|---|---|---|---|---|---|
| **docqa** | **PORT** (sole RAG survivor) | **L** | **L** | **M** | **M** | Only RAG package with a wheel slot + `productivity` category; but it's an 81-line shell that must absorb chat's doc profile to be real |
| **doc-search** | **MERGE INTO docqa** | — | — | — | **S** (delete) | Same agent as docqa with fewer tools; `examples`/`experimental` and never slated to publish (`setup.py:30-46`) |
| **hello-world** | **DISCARD** → `gaia agent init` template | **S** | **S** | — | **S** (delete) | A greeter is not a product; its real value is the annotated docstring the scaffold should carry |
| **word-count** | **DISCARD** → `gaia agent init --with-tool` template | **S** | **S** | — | **S** (delete) | Every generalization that would make it useful destroys its pedagogical precision |
| **summarize** | **PORT** (highest value) | **L** | **M** | **L** | **M** | Real capability, real tests — but 9 fail-loudly sites, a 2-model config, and an unjustified Qwen pin |
| **connectors-demo** | **DEFER** → in-repo fixture | **M** | **M** | — | **S** (recategorize) | Its own docstring says it exists to validate #926; #48's agents supersede its four toy reads |
| **routing** | **DISCARD from catalog** (keep private wheel) | **M** | **M** | — | **S** (drop manifest) | Not an `Agent` subclass, no entry point, loaded by class path — a library, not a catalog item |
| **builder** | **STAY IN-CORE** → absorb into Factory (#42) | **M** | **S** | — | — | The tool that makes hub packages must not be one; already coupled to registry + UI internals |

Effort keys: **S** ≤1 day · **M** ~1 week · **L** multi-week.
"—" = not applicable under the recommended verdict.

---

## Recommendation: which single RAG agent survives

**`docqa` survives. `doc-search` is deleted. ChatAgent's `doc` profile is retired *into*
docqa.**

**Why docqa and not doc-search:** docqa is in `AGENT_WHEEL_PACKAGES` (`setup.py:42`), is
categorized `productivity` rather than `examples`/`experimental`
(`docqa/gaia-agent.yaml:8` vs `doc-search/gaia-agent.yaml:8-9`), and already composes the
file mixins a document agent needs to reach files RAG missed
(`docqa/agent.py:27-34`). doc-search has none of that. The decision is about *slot*, not
code quality — doc-search's 80 lines are the better-written 80 lines, which is why they get
salvaged rather than discarded.

**Why not chat's `doc` profile, given it has the actual logic:** because it isn't an agent.
Per the sibling audit, `build_chat()` / `build_doc()` / `build_file()` return three
registrations that instantiate **one** 2,258-line `ChatAgent` differing only by
`prompt_profile`. Keeping "doc" there means the doc agent can never be installed, versioned,
or evaluated independently — the entire point of the hub. The logic moves; the class doesn't
stay.

**The three-step sequence:**

1. **Salvage doc-search into docqa, then delete the package.** Move its system prompt
   (`doc-search/agent.py:30-42` — index-first, answer-only-from-retrieval, always-cite,
   say-so-on-empty) over docqa's one-liner (`docqa/agent.py:80-82`). Add
   `self._snapshot_tools()` (`doc-search/agent.py:79`), which docqa is missing and which is a
   live cross-agent tool-leakage bug. Relocate its module docstring (`agent.py:3-23`) to
   `docs/sdk/core/agent-system.mdx` or the `gaia agent init` template. Delete
   `hub/agents/doc-search/`. Cheap — it was never in the wheel list, so no deprecation path
   is owed.

2. **Port chat's doc profile into docqa.** The ~13 anti-hallucination rules, multi-doc
   resolution, `tool_bundles.py` (11 core tools + 12 cohesion groups), and the dynamic tool
   loader. This is the L-sized work and the actual content of milestone #58 for this agent.
   Then retire chat's `doc` registration per the sibling audit's §11 retirement checklist.

3. **Fix docqa's own defects in the same pass** — they're small next to step 2: delete the
   dead `rag_documents` field (`agent.py:24`); remove or repair the neutered `MCPClientMixin`
   (`agent.py:33`, `:52-55`); replace `except ImportError: self.rag = None` (`:43-50`) and the
   debug-swallowed tool registration (`:69-78`) with loud errors naming
   `amd-gaia[rag]`; correct `tools_count: 0` → real count and add `filesystem:write` to
   `permissions` (`gaia-agent.yaml:11`, `:27-28`), since `FileIOToolsMixin` writes.

**The eval consequence, which is the real prize:** the 7 `rag_quality` scenarios and 3
committed baselines (`tests/fixtures/eval_baselines/*/scorecard_rag_quality.json`) currently
measure `agent_type: doc` — ChatAgent's prompt profile. After step 2 they measure the
`gaia-agent-docqa` package, and GAIA's flagship RAG capability finally has a scorecard
attached to a shippable artifact. That is the strongest argument for doing docqa first in
milestone #58.

---

## Cross-cutting notes

**The `interfaces` fix is cheaper than the sibling audit implies.** All 8 declare
`api_server: true`; none serves. But `AgentServer` / `run_agent_cli`
(`src/gaia/agents/base/server.py:76`, `:15-19`) already implements all five modes generically
against any `Agent`. It has zero production callers. Each package needs a `main()` calling
`run_agent_cli(...)` plus a `[project.scripts]` entry — ~30 lines. Two caveats: `summarize`
registers no tools (`agent.py:200-201`) so its MCP surface would be empty, and `routing`
doesn't subclass `Agent` (`agent.py:19`) so the harness cannot wrap it at all. Both should
have their `interfaces` blocks corrected rather than wired.

**`tools_count` needs a CI drift guard.** Wrong in docqa (0, actually ~30). Unverifiable in
doc-search (asserted against a literal, `tests/test_doc_search.py:22`). Correct in
hello-world, word-count, summarize, connectors-demo, routing. Email fails CI on drift; nothing
else does.

**No cohort package has release docs.** Email ships `CHANGELOG.md`, `CONTRACT.md`,
`CAPABILITY_MATRIX.md`, `SCORECARD.md`, `SPEC.md`, `SKILL.md`. Zero cohort packages ship any
of them; READMEs run 22–60 lines. Under the recommended verdicts only 3 packages ever need
them, which is the point — the doc burden is real, so it should be spent on agents that ship.

**Milestone #48 overlap is real and argues for discards, not ports.** #1485 `knowledge`
(`index_folder`, `query_kb`, `monitor_topic`) is the proactive successor to docqa/doc-search
and is explicitly the Wave 1 hub platform acceptance test. #1486 `files` (`scan_disk`,
`semantic_find`) covers what word-count would need to become useful. #1493 `writing` and
#1505 `presentation` (`build_narrative`, `talking_points`) overlap summarize's output side.
Porting `doc-search`, `hello-world`, `word-count`, or `connectors-demo` spends milestone #58
budget on capabilities milestone #48 rebuilds properly.
