# Port audit — 6 legacy agents → Agent Hub / Agent UI v2

Read-only audit, 2026-07-19. Cohort: **chat, doc, file, browser, analyst, code**.
Reference baseline: **email** (`hub/agents/python/email/` + `hub/agents/npm/agent-email/`).

Every claim below was verified by reading the file. Nothing is inferred from naming.

---

## Executive summary

Five findings dominate the port cost, and none of them is "write some docs."

1. **The `interfaces:` contract is already lying.** All six cohort agents *already* declare
   `api_server: true` in `gaia-agent.yaml`. None of them has a sidecar, an `api_routes.py`,
   an `openapi.<id>.json`, or an npm client. D5 is not a missing manifest field — it is a
   capability the manifests currently claim and cannot deliver.
2. **`tools_count` is wrong in every cohort manifest**, in both directions, with no drift
   guard. browser declares 10 and has 3; analyst declares 10 and has 5; fileio declares 0
   and has ~19; code declares 0 and has 47. Email declares 55 and *fails CI* if that drifts.
3. **The eval numbers everyone is quoting measure the wrong thing.** The `agent_type:`
   values in `eval/scenarios/` (`chat`/`doc`/`file`/`data`) are **ChatAgent's internal
   `prompt_profile` branches**, not the standalone hub packages. No scenario exercises the
   `browser`, `analyst`, `fileio`, or `code` *packages*. The "39 doc scenarios" belong to
   ChatAgent's doc profile, not to `gaia-agent-docqa`.
4. **Two agents are functionally mis-scoped, not merely under-polished.** AnalystAgent
   cannot open a file (its manifest advertises CSV/Excel). CodeAgent's only live execution
   path is hardcoded to Next.js even when `language="python"` — which is its own default.
5. **Behavioral test coverage of the tool surface is ~0% for five of six agents.** The tests
   assert that tool *names* appear in a registry. They never call a tool.

The unit of work here is a **generalization + contract project**, not a packaging pass.
Docs and manifests are the cheap part.

---

## Per-agent findings (A–E)

### 1. chat — `hub/agents/python/chat/`

**A. Implementation shape.**
`ChatAgent` (`gaia_agent_chat/agent.py:160-175`) composes 11 tool mixins plus
`MCPClientMixin` and `MemoryMixin` — effectively every mixin in `KNOWN_TOOLS`. It defines
15 of its own `@tool` functions in `agent.py`: `load_tools` (:1228), `list_files` (:1272),
`execute_python_file` (:1310), `open_url` (:1403), `fetch_webpage` (:1429),
`get_system_info` (:1477), `read_clipboard` (:1505), `write_clipboard` (:1525),
`notify_desktop` (:1551), `list_windows` (:1606), `text_to_speech` (:1743),
`search_documentation` (:1987), `search_web` (:2011), `set_loop_state` (:1872),
`request_user_input` (:1915).

Default model: `config.model_id or DEFAULT_MODEL_NAME` (`agent.py:243`) →
`Gemma-4-E4B-it-GGUF` (`src/gaia/llm/lemonade_client.py:123`).

System prompt: `_get_system_prompt()` (`agent.py:630-961`) assembles ~10 named prose blocks
and selects a subset per `config.prompt_profile` (`agent.py:898-961`, branches for
`chat`/`doc`/`file`/`data`/`web`/`full`). The full profile is 8,000+ characters of English
prose embedded in a Python method.

`tool_bundles.py` is the doc-profile machinery: `DOC_CORE_TOOLS` (11 always-on tools,
`:30-48`) and `DOC_BUNDLES` (12 cohesion groups, `:54-158`) drive a dynamic tool loader
(`_maybe_build_tool_loader`, `agent.py:455-473`) that is active **only** when
`prompt_profile == "doc"` and the toggle is on (`_resolve_dynamic_tools_enabled`,
`agent.py:475-480`).

**The critical structural fact:** the chat/doc/file "split" today is **registration-only**.
`build_chat()` (`__init__.py:65-93`), `build_doc()` (`:96-120`), and `build_file()`
(`:123-147`) return three `AgentRegistration`s that all instantiate the **same**
`ChatAgent` class, differing only by `prompt_profile`. There are not three agents; there is
one 2,258-line class with a runtime switch.

**B. Generality gaps.**
- Base URL `http://localhost:13305/api/v1` hardcoded twice, not a shared constant:
  `agent.py:260` and `agent.py:1374`.
- `claude_model = "claude-sonnet-4-20250514"` duplicated in four places with no shared
  constant: `agent.py:63`, `lite_agent.py:15`, `app.py:36-37`, and (per the docqa section)
  `docqa/agent.py:20`.
- `~/.gaia/*` paths as dataclass defaults: `filesystem_index_path` (`agent.py:104`),
  `scratchpad_db_path` (`agent.py:105`), TTS dir (`agent.py:1759`), MCP config
  (`agent.py:1805`). Inconsistently, `SessionManager` defaults to the **relative**
  `.gaia/sessions` (`session.py:64`) — cwd-scoped, unlike the other three.
- `_dynamic_tools_max = 14` (`agent.py:146`) is comment-justified as "11 CORE + 3 dynamic
  slots" — silently coupled to `len(DOC_CORE_TOOLS)` with no assertion tying them together.
- `min_ctx=32768` hardcoded fallback (`agent.py:395-399`).
- `enable_filesystem` / `enable_scratchpad` / `enable_browser` all default `False` with the
  comment "(disabled until agent split)" (`agent.py:98-110`) — the class already anticipates
  this port and ships the capabilities gated off.
- Missing capability: SD tools are off unless `config.enable_sd_tools=True`
  (`agent.py:1385-1392`) and the SD prompt section is dropped unless `sd_default_model` is
  set (`agent.py:622-628`) — "generate an image" silently has no tool, with no user-facing hint.

*Fail-loudly violations:*
- `lite_agent.py:51-58` — `except Exception: pass` around `register_screenshot_tools()`,
  **zero logging**.
- `app.py:321-325` and `app.py:1006-1011` — identical `except Exception: pass` swallowing
  tool-loader `reset_session()` failures, no logging.
- `app.py:1069-1073` — `except Exception: pass` around `agent.stop_watching()`.
- `agent.py:1590-1593` — discards the real Windows PowerShell failure, then returns a
  misleading "plyer not installed" error.
- `agent.py:1627-1629` — per-window `except Exception: pass` in `list_windows`.
- Softer: the OS-probe chain at `agent.py:1635-1636`, `:1696-1697`, `:1732-1733` is typed
  (`ImportError`, `FileNotFoundError`) — defensible capability probing, but the same shape.

**C. Test reality.** `tests/test_chat_agent.py` has 7 tests (`:12-79`), all
registration-shape assertions; its own docstring says it asserts shape "without
constructing a full agent" (`:6-9`). No test constructs a `ChatAgent`, calls
`_get_system_prompt()`, or invokes a tool. `test_chat_dependency_floor.py` and
`test_dependency_floor.py` are near-duplicate packaging-metadata guards.
**Behavioral coverage of the 15 self-defined tools: 0/15.**

**D. Eval readiness.** 10 scenarios declare `agent_type: chat`. No committed scorecard
baseline (all three baselines under `tests/fixtures/eval_baselines/` were generated with
`--agent-type doc`). No agent-local `eval_baselines/`. The plan's own §6 calls chat
"hardest to score objectively" and says it needs a metric decision before work starts.

**E. v2 contract.** Entry points `chat`/`doc`/`file` → `build_chat`/`build_doc`/`build_file`
in `[project.entry-points."gaia.agent"]` — **all resolve (PASS)**. Manifest declares
`tui/cli/pipe/api_server/mcp_server` all true. `api_server: true` is unbacked. Declares no
`permissions:` block despite shell, filesystem, clipboard, and network tools.

---

### 2. doc — today `hub/agents/python/docqa/` + chat's `DOC_BUNDLES`

**A. Implementation shape.** `DocumentQAAgent` (`gaia_agent_docqa/agent.py:27-34`) composes
4 mixins (`RAGToolsMixin`, `FileToolsMixin`, `FileIOToolsMixin`, `FileSearchToolsMixin`) plus
`MCPClientMixin`. It defines **0** `@tool` functions — `_register_tools()` (`:67-78`) only
calls mixin registrars. `build_registration()` (`__init__.py:37-55`) sets `hidden=True`
(`:49`) and `tools_count=0` (`:54`).

Default model: `config.model_id` defaults to `None` (`agent.py:22`) → base `Agent` fallback
`"Qwen3.5-35B-A3B-GGUF"` (`src/gaia/agents/base/agent.py:516`). The same literal is
hardcoded *again* at `agent.py:46` for `RAGConfig`.

System prompt is one line (`agent.py:80-81`):
`"You are DocumentQAAgent. Use indexed documents to answer user queries accurately and cite sources."`

**B. Generality gaps — this agent is a stub, and the plan already knows it.**
The entire RAG answer-quality apparatus lives in *chat's* doc profile
(`chat/agent.py:903-929`): single-vs-multi-doc resolution, a mandatory RAG-first rule, 13
numbered anti-hallucination rules, tool-loop prevention — all tuned across issues #1030,
#495, #1449. **None of it exists in `docqa`.** Retiring chat's doc profile into this package
as-is would be a large quality regression on the one eval category that has committed
baselines.

- `DocumentQAAgentConfig.rag_documents` (`agent.py:24`) is **dead config** — declared,
  never referenced anywhere in the file. Passing documents silently does nothing.
- No session persistence (chat has `SessionManager`; this has none — indexed docs are lost
  across restarts). No memory subsystem.
- `claude_model = "claude-sonnet-4-20250514"` hardcoded (`agent.py:20`).

*Fail-loudly:* `agent.py:43-50` — `except ImportError: self.rag = None` with **no logging**,
where chat's equivalent (`chat/agent.py:284-289`) logs a warning plus a debug traceback.
`agent.py:67-78` catches `(ImportError, AttributeError)` and logs at debug only.

**C. Test reality.** `tests/test_docqa_agent.py`, 5 tests (`:15-61`). Three of them do
construct a real agent and check `_get_system_prompt()` content and registry membership
(e.g. `test_registers_rag_tools`, `:52-57`) — a notch above pure smoke. But no tool is ever
called, and the dead `rag_documents` field is untested. Self-owned tool surface is 0, so
there is nothing to cover; mixin tools are presence-checked only.

**D. Eval readiness.** The literal string `docqa` appears **nowhere** in `src/gaia/eval/`
or `eval/scenarios/` — zero matches repo-wide. The 39 `agent_type: doc` scenarios and all
three committed `scorecard_rag_quality.json` baselines
(`tests/fixtures/eval_baselines/{gemma-4-e4b-95e4b372, gemma-4-e4b-d71cd914,
qwen-3.5-35b-3b51ca92}/`) target **ChatAgent's doc profile**, per the `to_reproduce`
commands in each `meta.json`. This is the best-positioned agent in the cohort for evals —
but only if the doc package inherits chat's profile behavior, not docqa's stub.

**E. v2 contract.** `docqa = "gaia_agent_docqa:build_registration"` — **resolves (PASS)**.
Declares `pipe`, `api_server`, `mcp_server`; no `cli`, no `tui`. Declares
`permissions: [filesystem:read]` — one of only two cohort agents honest about permissions.
Per D2 this package is **retired** into `gaia-agent-doc`; §8 of the plan flags the
`registry.py:665-672` id-collision hazard requiring an atomic same-release cutover.

---

### 3. file — today `hub/agents/python/fileio/`

**A. Implementation shape.** `FileIOAgent` (`gaia_agent_fileio/agent.py:26-33`) composes
`FileIOToolsMixin`, `FileSearchToolsMixin`, `ShellToolsMixin`, `ScreenshotToolsMixin`,
`MCPClientMixin`. Defines **0** own tools. Real inherited surface:

| Mixin | Tools |
|---|---|
| `FileIOToolsMixin` (`src/gaia/agents/tools/file_io_tools.py`) | `read_file`:38, `write_python_file`:188, `edit_python_file`:276, `search_code`:429, `generate_diff`:509, `write_markdown_file`:572, `write_file`:641, `edit_file`:734, `update_gaia_md`:876, `replace_function`:968 |
| `FileSearchToolsMixin` (`src/gaia/agents/tools/file_tools.py`) | `search_file`:94, `search_directory`:460, `read_file`:546, `search_file_content`:757, `write_file`:959, `edit_file`:1287, `browse_directory`:1447, `get_file_info`:1608, `analyze_data_file`:1798, `list_recent_files`:2519 |
| `ShellToolsMixin` | `run_shell_command`:392 |
| `ScreenshotToolsMixin` | `take_screenshot`:29 |

Default model: `model_id = None` (`agent.py:22`) → base default `"Qwen3.5-35B-A3B-GGUF"`.
System prompt, in full (`agent.py:67-68`):
`"You are FileIOAgent. Perform file operations safely and ask for confirmation before destructive actions."`

**B. Generality gaps.**
- **Silent tool-name collision — three tools are lost.** Registration order
  (`agent.py:58-59`) runs `FileIOToolsMixin` then `FileSearchToolsMixin`. Both define
  `read_file`, `write_file`, `edit_file`. `_TOOL_REGISTRY[tool_name] = {...}`
  (`src/gaia/agents/base/tools.py:79`) is a plain dict assignment with **no duplicate
  check**, so the simpler `file_tools` versions (no `project_dir`, no Python-syntax
  validation) silently overwrite the `file_io_tools` versions. The LLM never sees the richer
  implementations even though their registrar ran.
- **No registry isolation.** Unlike browser and analyst, `_register_tools()` never calls
  `_TOOL_REGISTRY.clear()` or `self._snapshot_tools()`. Per
  `src/gaia/agents/base/agent.py:717-727`, `_tools_registry` then falls back to the
  **process-global** registry — so tools registered earlier by a different agent in the same
  process leak into FileIOAgent's surface and prompt.
- **The system prompt promises a capability that does not exist.** It says "ask for
  confirmation before destructive actions" (`agent.py:68`), but grepping both mixins for
  `os.remove` / `os.unlink` / `shutil.move` / `shutil.rmtree` / `def delete` / `def move`
  returns **zero matches**. There is no delete, move, or rename tool. The only destructive
  operation is content overwrite.
- No recursive glob tool (`search_file`'s matching, `file_tools.py:78`, and
  `browse_directory`'s single-dir listing, `:1447`, are the closest).
- Stale docstring: `search_file` tells the LLM to call `browse_files` (`file_tools.py:73`) —
  no such tool exists; the real one is `browse_directory`.
- Manifest `tools_count: 0` (`gaia-agent.yaml:11`) versus ~19 real names.

*Fail-loudly (in composed mixins):*
- `src/gaia/agents/tools/shell_tools.py:557-559` — `except Exception: pass` inside
  **per-argument path validation** for `run_shell_command`. This is a security-relevant
  forbidden-path check being silently skipped on error. The most serious instance found in
  this audit.
- `src/gaia/agents/tools/file_tools.py:869-870` — `except Exception: return True  # Continue
  searching`; files that fail to open are silently omitted with no indication in the result.
- Not violations (best-effort format sniffing that still returns correctly-labeled output):
  `file_tools.py:313-314`, `:1223-1224`, `:1730-1731`; `screenshot_tools.py:69-70`.

**C. Test reality.** `tests/test_fileio_agent.py`, 64 lines, 5 tests. Four are
import/defaults/construct/substring smoke; `test_registers_file_tools` (`:54-59`) checks 4
of ~19 names for presence. **0/19 behaviorally exercised (0%); 4/19 (~21%) presence-checked.**
The three-tool collision above is entirely invisible to this suite — it asserts the name
exists, never which implementation won.

**D. Eval readiness.** Exactly **1** scenario:
`eval/scenarios/captured/captured_eval_smart_discovery.yaml` (`agent_type: file`) — and that
`file` value is ChatAgent's prompt profile, not this package. No committed baseline, no
agent-local `eval_baselines/`. The plan (§6) calls this the "worst starting point" and
proposes the right metric: a synthetic filesystem fixture with exact-match on resulting FS
state — deterministic and cheap.

**E. v2 contract.** `fileio = "gaia_agent_fileio:build_registration"` — **resolves (PASS)**.
Declares `pipe`, `api_server`, `mcp_server`; no `cli`/`tui`. Declares
`permissions: [filesystem:read, filesystem:write]` — the most honest manifest in the cohort.
No dedicated CI workflow at all (the only cohort agent with none).

---

### 4. browser — `hub/agents/python/browser/` (id `web`)

**A. Implementation shape.** `BrowserAgent(Agent, BrowserToolsMixin, MCPClientMixin)`
(`agent.py:37`). `_register_tools()` (`:78-81`) does `_TOOL_REGISTRY.clear()` →
`register_browser_tools()` → `_snapshot_tools()` — correctly isolated. Defines 0 own tools.
Real surface is **3** tools from `src/gaia/agents/tools/browser_tools.py`: `fetch_page`:47,
`search_web`:188, `download_file`:240.

Default model: `None` (`agent.py:22`) → base default `"Qwen3.5-35B-A3B-GGUF"`.
System prompt (`agent.py:83-89`) is 4 sentences.

**B. Generality gaps.**
- **No JS-render path.** `fetch_page`'s own docstring says "Does NOT execute JavaScript"
  (`browser_tools.py:56`). For an agent named "browser," static fetch only is the headline
  missing capability — most modern research targets are client-rendered.
- **Single hardcoded search provider.** `search_web` → `search_duckduckgo`
  (`browser_tools.py:208`) → hardcoded `https://html.duckduckgo.com/html/`
  (`src/gaia/web/client.py:827-839`). HTML scraping, no configurable/alternate provider, no
  API-key path. Brittle by construction — a DDG markup change breaks the agent silently.
- **`download_file` instructs the LLM to call tools this agent does not have.** Both the
  docstring (`browser_tools.py:249`) and the success message (`:321`) say "use `read_file` or
  `index_document` to process it." Neither is registered — `BrowserAgent` composes only
  `BrowserToolsMixin` + `MCPClientMixin` (`agent.py:37`). Following the tool's own advice
  produces a tool-not-found failure on every download.
- Manifest `tools_count: 10` (`gaia-agent.yaml:11`) versus 3 real. Off by 3.3×.
- `download_file(save_to="~/Downloads")` (`browser_tools.py:242`) assumes that convention.

*Fail-loudly:* `browser_tools.py:298-301` — `except OSError: pass` on the cleanup-delete of a
policy-blocked download. If `unlink` fails the blocked file stays on disk while the user is
told it was handled.

**C. Test reality.** `tests/test_browser_agent.py`, 30 lines, 3 tests — `build_registration`
shape, import, and registry discovery. **None instantiates `BrowserAgent` or calls any of
the 3 tools. Coverage of the real tool surface: 0%.** Not even presence-checked.

**D. Eval readiness.** **Zero.** `grep -rn "agent_type: web\|agent_type: browser"` across
`eval/scenarios/` returns nothing. No category, no scenario, no ground truth, no baseline,
no agent-local `eval_baselines/`. Note the trap: `eval/scenarios/web_system/fetch_webpage.yaml`
declares `agent_type: chat` and exercises **ChatAgent's** `fetch_webpage`
(`chat/agent.py:1428-1429`) — a completely separate implementation from
`BrowserToolsMixin.fetch_page`. There is no eval signal for this package at all.

**E. v2 contract.** `web = "gaia_agent_browser:build_registration"` — **resolves (PASS)**.
Note the **id/dir mismatch**: directory `browser`, registry id `web`. Declares `cli`, `pipe`,
`api_server`; `mcp_server: false`. No `permissions:` block despite unrestricted network
access and disk writes via `download_file`.

---

### 5. analyst — `hub/agents/python/analyst/` (id `data`)

**A. Implementation shape.** `AnalystAgent(Agent, ScratchpadToolsMixin, MCPClientMixin)`
(`agent.py:35-39`). `_register_tools()` (`:76-79`) clears and snapshots — correctly isolated.
Defines 0 own tools. Real surface is **5** tools from
`src/gaia/agents/tools/scratchpad_tools.py`: `create_table`:56, `insert_data`:90,
`query_data`:170, `list_tables`:241, `drop_table`:275.

Default model: `None` (`agent.py:22`) → base default `"Qwen3.5-35B-A3B-GGUF"`.
System prompt (`agent.py:81-87`) is 3 sentences.

**B. Generality gaps — the most severe mis-scope in the cohort.**

**AnalystAgent cannot read a file.** Its manifest describes it as "structured data analysis
(CSV/Excel, scratchpad SQL)" with `tags: [data, csv, excel, analysis]`
(`gaia-agent.yaml:4,9`), and `README.md:3` repeats it. But its only mixin has 5 tools and
none of them touches a path. `insert_data`'s sole input is a JSON array string typed by the
caller (`scratchpad_tools.py:90-109`). There is no CSV parser, no Excel reader, and no path
parameter anywhere in `scratchpad_tools.py`.

The tool this agent needs **already exists and is wired to a different agent**:
`analyze_data_file` (`src/gaia/agents/tools/file_tools.py:1798`, decorator context
`:1762-1795`) explicitly handles "CSV, Excel, or tabular data files with full row-level
aggregation… Supports CSV, TSV, XLSX, and XLS" with `group_by` / `date_range` /
`analysis_type` — built for exactly the questions `rag_quality/csv_analysis.yaml` asks. It
lives in `FileSearchToolsMixin`, composed by **FileIOAgent** (`fileio/agent.py:29`), not here.

Net: AnalystAgent depends on the LLM having already parsed a spreadsheet into JSON by some
means it does not provide. The fix is a one-line mixin addition — but it is a *capability*
fix, and until it lands the agent cannot do the thing its catalog entry promises.

- `query_data` requires an undocumented `scratch_` table-name prefix
  (`scratchpad_tools.py:178-179`). An LLM writing `SELECT * FROM transactions` against a
  table it just created fails unless it remembers the prefix.
- Manifest `tools_count: 10` (`gaia-agent.yaml:11`) versus 5 real. Off by 2×.
- Not gaps: `_MAX_INSERT_JSON_BYTES = 10MB` and `_MAX_INSERT_ROWS_PER_CALL = 10_000`
  (`scratchpad_tools.py:24-25`) are enforced with explicit actionable errors, not silent
  truncation. This mixin is the cleanest fail-loudly citizen in the audit.

**C. Test reality.** `tests/test_analyst_agent.py`, 83 lines, 5 tests.
`test_isolated_tool_set_is_scratchpad_only` (`:63-77`) asserts the exact 5-name set — so
100% of names are presence-checked, the best in the cohort. But **no test creates a table,
inserts a row, or runs a query. Behavioral coverage: 0%.**

**D. Eval readiness.** 5 scenarios declare `agent_type: data` (e.g.
`eval/scenarios/real_world/us_labor_statistics_xlsx.yaml`,
`eval/scenarios/rag_quality/csv_analysis.yaml`) — but again, `data` is **ChatAgent's prompt
profile**, not this package (`chat/agent.py:941`). Those scenarios pass today because
ChatAgent composes `ScratchpadToolsMixin` *and* the file-reading mixins. Pointed at the
standalone `AnalystAgent`, `csv_analysis.yaml` would fail outright — there is no way to open
the CSV. No committed baseline, no agent-local `eval_baselines/`.

**E. v2 contract.** `data = "gaia_agent_analyst:build_registration"` — **resolves (PASS)**.
Same **id/dir mismatch** pattern: directory `analyst`, registry id `data`. Declares `cli`,
`pipe`, `api_server`; `mcp_server: false`. No `permissions:` block.

---

### 6. code — `hub/agents/python/code/`

**A. Implementation shape.** `CodeAgent` (`agent.py:64-81`) inherits `ApiAgent` + `Agent` and
composes 13 mixins — but only **one** is a shared framework mixin (`CodeIndexToolsMixin`,
`agent.py:24,80`); the other 12 are package-local under `gaia_agent_code/tools/`.

**47 registered `@tool` functions.** Distribution by module: `web_dev_tools.py` 11,
`error_fixing.py` 9, `code_tools.py` 7, `cli_tools.py` 5, `validation_tools.py` 4,
`project_management.py` 3, `code_formatting.py` 2, `external_tools.py` 2, `testing.py` 2,
`prisma_tools.py` 1, `typescript_tools.py` 1. (`tools/file_io.py` is a deprecated re-export
shim, `:14-21`; `tools/validation_parsing.py` registers none.) Note a duplicate tool name:
`validate_typescript` is defined at both `typescript_tools.py:29` and
`validation_tools.py:403` — the same silent-overwrite hazard as fileio.

Default model: `kwargs["model_id"] = "Qwen3.5-35B-A3B-GGUF"` (`agent.py:125`).

System prompt: `system_prompt.py:17-41` branches on `language` — `"typescript"` → base +
`NEXTJS_PROMPT` (`:35-38`); anything else → `get_python_prompt()` (`:39-41`).
`prompts/code_patterns.py` is **2,034 lines of 22 Next.js/React/Prisma/Vitest code
templates** (`:15-1925`) — not a prompt at all, a codegen template library consumed by
`web_dev_tools.py:21,878,1681` and `validation_tools.py:17`.

Orchestration: `Orchestrator.execute()` (`orchestration/orchestrator.py:186-419`) is the
live path — LLM generates a JSON checklist of template invocations, `ChecklistExecutor` runs
them, a checkpoint-review LLM call decides complete/needs-fix, looping up to
`max_checklist_loops=10` (`:145`).

**B. Generality gaps — two structural problems.**

**(i) The live path is 100% Next.js-hardcoded, regardless of `language`.**
`TEMPLATE_CATALOG` (`template_catalog.py:95-393`) has 13 templates: `create_next_app`,
`setup_app_styling`, `setup_prisma`, `setup_testing`, `generate_prisma_model`,
`prisma_db_sync`, `generate_api_route`, `generate_react_component`, `update_landing_page`,
`run_typescript_check`, `validate_styles`, `generate_style_tests` — **12 of 13 (92%) are
Next.js/Prisma/React-only**; the 13th (`fix_code`) is generic. **Zero are Python-specific.**

`get_catalog_prompt()` (`template_catalog.py:408`) takes **no arguments** — no language
filter. Both `generate_initial_checklist()` and `generate_debug_checklist()` inject the full
catalog unconditionally (`checklist_generator.py:240-242`, `:264-266`).
`CHECKLIST_SYSTEM_PROMPT` (`:124-215`) is hardcoded Next.js prose ("REQUIRED: Include
`setup_app_styling` after `create_next_app`", "REQUIRED: End with `run_typescript_check`,
then `validate_styles`", `:190-193`). `_validate_checklist()` (`:553-643`) **unconditionally
rejects** any checklist not ending in `run_typescript_check` → `validate_styles`
(`:608-624`), with **no language branch at all**.

`CodeAgent`'s own default is `language="python"` (`agent.py:99`). So the default
configuration routes every request through a validator that demands TypeScript steps against
a catalog with no Python entries. It either exhausts `max_attempts=3` and raises
`RuntimeError("Failed to generate a valid checklist…")` (`checklist_generator.py:321-324`),
or the LLM stuffs Next.js templates into a Python project. This directly contradicts the
in-code claim at `agent.py:336` that the orchestrator "handles correct step ordering for all
project types." No test exercises the Python path — `grep "language=.python.\|PythonFactory"
tests/*.py` returns zero matches.

**(ii) ~1,782 lines of dead parallel implementation.** `factories/` (287 lines),
`workflows/` (360 lines), `steps/nextjs.py` (828) and `steps/python.py` (307) implement a
complete `WorkflowPhase`-based system including a working `PythonFactory`
(`factories/python_factory.py:18`) and `NextJSFactory` (`nextjs_factory.py:18`). **Nothing
invokes them** — those names appear only in their own files and `factories/__init__.py:6-9`.
`orchestration/__init__.py:11-24` re-exports only `Orchestrator` and the `steps.base`
dataclasses. This is ~36% of the `orchestration/` package (1,782 of 4,952 lines) — and,
ironically, the dead code contains the Python-language support the live path lacks.

*Other hardcoding:* `Path.home()/".gaia"/"cache"` (`agent.py:135`);
`DEFAULT_PORT_RANGE_START = 3000` (`cli_tools.py:29`); `port: int = 3000`
(`validation_tools.py:76,92`); pinned `NEXTJS_VERSION="14.2.33"`, `PRISMA_VERSION="5.22.0"`,
`ZOD_VERSION="3.23.8"` (`nextjs_prompt.py:6-8`); `display_result()` unconditionally prints
`npm run dev` and `http://localhost:3000` for **any** successful project including Python
scripts (`agent.py:569-574`); `project_type` accepted then ignored with a pylint disable
(`system_prompt.py:19`). Manifest `tools_count: 0` versus 47 real.

*Author's-machine:* `grep -rn "shutil.which"` returns **no hits** anywhere in `tools/`. There
is no preflight check for `node`/`npm`/`npx`/`prisma` despite pervasive use in
`web_dev_tools.py`, `prisma_tools.py`, `typescript_tools.py` — only a *reactive* post-failure
regex (`cli_tools.py:71`). Without Node installed the user gets an opaque subprocess error.

*Fail-loudly violations.* 103 `except Exception` hits; the large majority are the allowed
tool-boundary translation pattern. The genuine violations:
1. **`prompts/base_prompt.py:34-35`** — `except Exception: pass`, no logging, silently
   discards GAIA.md read failures and proceeds with empty project context.
2. **`schema_inference.py:75-100`** — an explicit cascading provider fallback
   ("Perplexity API → Local LLM → Generic fallback", docstring `:58`), returning
   `{"entity": None, "fields": []}` at `:177-179`, `:202-204`, `:282-284`. This is precisely
   the prohibited "try the other provider" glue.
3. **`tools/code_tools.py:643-644, 667-668, 749-773`** — on LLM timeout,
   `_get_timeout_placeholder()` **writes fabricated placeholder code to disk** as if it were
   the generated file (`"""TODO: Implementation needed - LLM generation timed out."""`).
   A placeholder masquerading as real output — the textbook prohibited case.
4. **`tools/project_management.py:637-676`** — on test-generation failure, substitutes a
   `unittest.TestCase` whose only body is `self.skipTest(...)`, so a failed step reads
   downstream as a passing/skipped test.
5. `orchestration/orchestrator.py:562-564` and `checklist_executor.py:1127-1129` — log-and-
   return-empty on summarization and Prisma-model-read failures. Lower severity, same shape.

**C. Test reality.** By far the strongest suite in the cohort — 7 files, ~4,938 lines, 255
test functions. `test_checklist_orchestration.py` (1,728 lines, 77 tests) and
`test_code_validators.py` (477 lines, 63 tests) are genuinely behavioral.
`test_code_agent_mixins.py` (41 tests) is mostly registration smoke and contains a
copy-paste bug: `test_generate_function_tool_registered` (`:81-90`) actually invokes
`_execute_tool("validate_syntax", ...)` — `generate_function` is never exercised.

**Two tests are broken and have been since the orchestrator migration** (verified by running
the suite): `test_process_query_generate` and `test_process_query_analyze` both assert keys
(`"conversation"`, `"steps_taken"`, `test_code_agent.py:578,593`) that the current
`process_query()` return schema does not contain (`agent.py:381-390`). Result:
`2 failed, 29 passed`. The same run surfaced a **live unrelated bug**:
`LemonadeProvider.chat() got multiple values for argument 'messages'` at
`orchestrator.py:633`, which makes `_prepare_project_directory` fail with "Unable to find an
available project name…" (`orchestrator.py:211`).

Coverage: 32 of 47 tool names (~70%) appear somewhere, many registration-only. **14 tools
have zero mention**: `cleanup_all_processes`, `generate_crud_scaffold`, `generate_style_tests`,
`get_process_logs`, `list_processes`, `manage_prisma_client`, `manage_react_component`,
`manage_web_config`, `setup_nextjs_testing`, `stop_process`, `test_crud_api`,
`validate_crud_completeness`, `validate_crud_structure`, `validate_styles` — i.e. most of the
CRUD/validation surface the hard-required checklist steps depend on.

**D. Eval readiness.** **Zero.** No scenario declares `agent_type: code`. No category, no
dataset, no baseline, no agent-local `eval_baselines/`. Along with browser, completely dark.

**E. v2 contract.** `code = "gaia_agent_code:build_registration"` — **resolves (PASS)**.
Declares `cli`, `pipe`, `api_server`; `mcp_server: false`. Depends on
`gaia-agent-routing>=0.1.0` in addition to `amd-gaia>=0.20.0` — the only cohort agent with a
cross-agent dependency. No `permissions:` block despite shell execution, arbitrary file
writes, and network access.

---

## The v2 load contract, and what actually gates on it

`src/gaia/hub/manifest.py` parses `gaia-agent.yaml` into a dataclass (`:170-217`) via
`AgentManifest.from_dict()` (`:223-337`). **Required**: `id`, `name`, `version`,
`description`, `author`, `license` (`:248-255`), plus `language` ∈ `{python, cpp}`
(`:56,261-271`). `id` matches `_ID_RE` (`:40`) and must not collide with
`_RESERVED_BUILTIN_IDS` (`:410-415`); `version` must be full SemVer (`:44-49`).
`interfaces:` is validated against
`VALID_INTERFACES = {"tui","cli","pipe","api_server","mcp_server"}` (`:100`), with unknown
keys rejected in `_parse_interfaces` (`:560-580`).

The load path itself, `_default_loader` (`src/gaia/hub/lifecycle.py:185-226`): builtins are
registry-checked only (`:193-199`); otherwise it iterates `AGENT_ENTRY_POINT_GROUPS`
(`:204-208`) and calls `ep.load()` (`:212`). **It fails loudly** — a load exception is
re-raised as `LifecycleError` naming the agent and cause (`:214-221`), and a missing entry
point raises (`:222-226`). `health_check()` (`:285-354`) converts any load exception into
`HEALTH_ERROR` with detail (`:335-343`) — again no silent "healthy."

`src/gaia/hub/installer.py`: `install()` (`:744-947`) → `_resolve_version` (`:532-622`) →
`_download_and_verify` (SHA-256, `:360-371`) → `_install_python_artifact` (`:406-419`,
`uv pip install --target`, raising on nonzero exit at `:394-398`) → `_hot_register()`
(`:648-677`, best-effort by design, `:653-655`) → `_write_sentinel()` (`:949-972`).

Actual entry-point resolution happens in `src/gaia/agents/registry.py`:
`AGENT_ENTRY_POINT_GROUPS = ("gaia.agents", "gaia.agent")` (`:33`) — **both** group names are
scanned. Every cohort agent registers under the **singular** `"gaia.agent"`.

**Conformance results — all six PASS:**

| Agent | dir → id | entry point | resolves? | `api_server` | `mcp_server` | `permissions:` | `tools_count` declared vs real |
|---|---|---|---|---|---|---|---|
| chat | chat → `chat`/`doc`/`file` | `build_chat`/`build_doc`/`build_file` | **PASS** (`agent.py:160`) | ✅ | ✅ | none | — vs 15 own |
| doc | docqa → `docqa` | `gaia_agent_docqa:build_registration` | **PASS** (`agent.py:27`) | ✅ | ✅ | `filesystem:read` | 0 vs 0 ✔ |
| file | fileio → `fileio` | `gaia_agent_fileio:build_registration` | **PASS** (`agent.py:26`) | ✅ | ✅ | `filesystem:read/write` | **0 vs ~19** |
| browser | browser → **`web`** | `gaia_agent_browser:build_registration` | **PASS** (`agent.py:37`) | ✅ | ❌ | none | **10 vs 3** |
| analyst | analyst → **`data`** | `gaia_agent_analyst:build_registration` | **PASS** (`agent.py:35`) | ✅ | ❌ | none | **10 vs 5** |
| code | code → `code` | `gaia_agent_code:build_registration` | **PASS** (`agent.py:64`) | ✅ | ❌ | none | **0 vs 47** |
| *email* | email → `email` | **commented out** (`pyproject.toml:33-34`) | n/a — superseded by REST sidecar | ✅ | ✅ | none | 55, **CI-enforced** |

Two notes that matter for the port:

- The `python.entry_module`/`entry_class` fields in `gaia-agent.yaml` are **not** what the
  runtime resolves — `lifecycle.py` and `registry.py` read `pyproject.toml`'s
  `[project.entry-points."gaia.agent"]` (factory functions), while the manifest fields feed a
  separate dev-tool path, `_instantiate_agent()` in `src/gaia/cli_agent.py:1029-1063`. Both
  paths independently resolve for all six (each `__init__.py` lazily re-exports the class,
  e.g. `chat/gaia_agent_chat/__init__.py:22-30`). Two declarations of "the entry point" that
  can drift is itself a factory concern.
- **Nobody needs `api_server` *added*.** All six already declare it. What none of them has is
  the substance D5 demands.

**D5, verbatim** (`docs/plans/2260-chat-doc-file-agent-packages.md:15`):

> Each agent exposes a **sidecar REST API server** (email pattern) so Agent UI v2 integrates
> them over a contract, not an import.

§5 (`:79-93`) enumerates what that means: `api_routes.py` + `server.py` on a dedicated port
(email uses 8131); `openapi.<id>.json` generated by `export_openapi.py` plus a conformance
test; `spec_html.py` → `specification.html`; an npm client under `hub/agents/npm/agent-<id>/`
which becomes the canonical doc root; and lifecycle guarantees (auto-reap on parent exit,
readiness vs liveness, typed error envelopes). The plan flags the open question: N sidecars
means N processes, and Agent UI v2 needs a launcher story.

So the `api_server: true` already in all six manifests is currently a **false capability
claim** — they run in-process through the registry factory, which is exactly the "import,
not contract" integration D5 exists to eliminate.

---

## Eval surface — the long pole, and a measurement trap

`--category` is a free-text argparse argument (`src/gaia/cli.py:2334-2338`, no `choices=`);
categories are discovered from scenario YAML at runtime by `find_scenarios()`
(`src/gaia/eval/runner.py:351-412`) scanning `eval/scenarios/`. The 12 that exist:
`adversarial` (3), `captured` (2), `context_retention` (4), `error_recovery` (3),
`mcp_reliability` (10), `memory` (25), `personality` (3), `rag_quality` (7), `real_world` (19),
`tool_selection` (5), `vision` (3), `web_system` (6).

`agent_type:` values across all 91 scenarios: `doc` (39), `chat` (10), `data` (5), `file` (1).

**The trap:** those four values are **ChatAgent's internal `prompt_profile` branches**
(`chat/agent.py:898,903,931,941,945`), not hub package ids. Confirmed two ways:
`web_system/fetch_webpage.yaml` declares `agent_type: chat` and tests ChatAgent's own
`fetch_webpage` (`chat/agent.py:1428-1429`), a different implementation from
`BrowserToolsMixin.fetch_page`; and `rag_quality/csv_analysis.yaml` declares `agent_type: data`
but passes only because ChatAgent composes scratchpad **and** file-reading mixins — the
standalone `AnalystAgent` cannot open the CSV at all.

Committed baselines (`tests/fixtures/eval_baselines/`): `gemma-4-e4b-95e4b372/`
(`scorecard_rag_quality.json`), `gemma-4-e4b-d71cd914/` and `qwen-3.5-35b-3b51ca92/`
(`scorecard_context_retention`, `scorecard_rag_quality`, `scorecard_tool_selection`). All are
**model** baselines generated with `--agent-type doc`, per each `meta.json`'s `to_reproduce` —
not per-agent baselines. 3 of 12 categories have any baseline at all.

| Agent | Scenarios | Committed baseline | Local `eval_baselines/` | Verdict |
|---|---|---|---|---|
| chat | 10 (`agent_type: chat`) | none | none | profile-level signal only |
| doc | 39 (`agent_type: doc`) | 3 × `scorecard_rag_quality.json` | none | best positioned — but measures chat's doc profile, not docqa |
| file | **1** | none | none | plan calls it the worst starting point |
| analyst | 5 (`agent_type: data`) | none | none | scenarios would fail against the real package |
| browser | **0** | none | none | completely dark |
| code | **0** | none | none | completely dark |
| *email* | labeled corpus (300 emails + 3 threshold manifests) | `SCORECARD.md`, 333 lines, generated | `eval_baselines/query_sequences/` | deterministic + release-gated |

**CI enforcement.** `release_agent_email.yml` has a `scorecard-gate` job (`:277`) running
`python -m gaia.eval.scorecard_gate` (`:354-359`) as a hard `needs:` of publish (`:399`).
`weekly_eval.yml` (`:38-40`) reuses the email suite exclusively.
`test_eval_rag.yml` is the only wiring that touches `doc` — and it is **fully disabled**
(`on: workflow_dispatch` only; header comment: "FULLY DISABLED pending rebuild — see #1315…
This gate has never executed successfully"), with `publish.yml:329-331` noting it is
deliberately not a release gate. **No `release_agent_*.yml` exists for any cohort agent** —
only `release_agent_email.yml`.

Email's vehicle is deterministic (`gaia eval benchmark`, non-judge) with pinned event
sequences: `eval_baselines/query_sequences/triage_inbox.json` pins
`required_subsequence: ["status","tool_call","tool_result","final"]`, `terminal: "final"`,
`forbidden: ["error"]`; `send_needs_confirmation.json` pins that a destructive step emits
`needs_confirmation` and never silently acts. Ordering and vocabulary are pinned, exact event
counts are not — LLM-nondeterminism-aware by design. This is the pattern to copy.

Per the `adding-eval-scorecard` skill's Phase 1.4 (quoted in the plan, `:108-110`): **stop
rather than invent numbers** if there is no non-judge eval vehicle. Four of six agents have
no vehicle at all today.

---

## (b) Port-cost table

S = days, M = 1–2 weeks, L = multi-week. Ratings are per agent, per workstream.

| Agent | Generalization | Tests | Docs (SPEC/SKILL/CHANGELOG) | Eval dataset + scorecard | Packaging / manifest |
|---|---|---|---|---|---|
| **chat** | **L** — the ProfileSpec refactor (plan §4): extract the profile switch from `_get_system_prompt`/`_register_tools`, split `ChatAgentConfig` (40+ fields), make RAG/watchdog/session lazy, decide `app.py`'s owner. Behavior-preserving but touches prompt assembly → mandatory eval re-baseline. | **L** — 0/15 tools behaviorally covered; no test constructs the agent. Needs a real fixture-based suite from scratch. | **M** — largest surface (15 tools + 6 profiles) but content is derivable from existing code. | **L** — plan §6: "hardest to score objectively"; needs a metric *decision* (tool-selection accuracy vs instruction-following) before any work starts. | **M** — entry points already resolve; must drop `doc`/`file` from its entry points in the **same release** as the new wheels (registry collision, §8). |
| **doc** | **L** — not a repackaging: docqa is a stub with a one-line prompt. Must absorb chat's doc-profile prompt logic (13 anti-hallucination rules, multi-doc resolution), `tool_bundles.py`, the dynamic loader, and RAG indexing/watchdog ≈600 lines (plan `:35`). Plus fix dead `rag_documents` (`docqa/agent.py:24`). | **M** — 3 of 5 existing tests do construct the agent; extend to behavioral RAG tests over a fixed corpus. | **M** | **M** — best starting point: 7 `rag_quality` scenarios + 3 committed baselines exist. Needs conversion from LLM-judge to a deterministic groundedness/citation metric. | **M** — plus the docqa retirement checklist (plan §11: 8 files incl. workflows, lint, labeler). |
| **file** | **M** — fix the 3-tool registry collision (`tools.py:79`), add `_TOOL_REGISTRY.clear()`/`_snapshot_tools()` isolation, add the delete/move/rename tools the prompt already promises, add recursive glob, fix the `browse_files` stale docstring. Bounded and well-understood. | **M** — 0/19 behavioral. A synthetic-FS fixture makes this straightforward. | **M** | **S** — plan §6 names the metric: synthetic filesystem fixture, exact-match on resulting FS state. Deterministic and cheap; the easiest scorecard in the cohort. | **M** — needs a workflow from scratch (only cohort agent with **no** CI file at all). |
| **browser** | **M** — add a JS-render path (headless), make the search provider pluggable off hardcoded DDG scraping (`web/client.py:827-839`), fix `download_file`'s docstring+message pointing at unregistered tools, fix `except OSError: pass` (`browser_tools.py:298-301`). Small codebase, real capability additions. | **M** — 0/3 covered, nothing even presence-checked. Small surface, so bounded; needs network mocking. | **S** — 3 tools. | **L** — zero scenarios, zero ground truth, zero baseline. Needs a labeled corpus **and** a deterministic metric invented from nothing; web content is non-stationary, so the fixture must be a frozen page snapshot set. | **M** — plus resolving the dir(`browser`)/id(`web`) mismatch. |
| **analyst** | **M** — headline fix is one line (compose `FileSearchToolsMixin` for `analyze_data_file`, `file_tools.py:1798`), but the agent must then be re-scoped and re-prompted around a real file→table pipeline, plus the undocumented `scratch_` prefix convention. | **M** — 5/5 names checked, 0/5 behavioral. Scratchpad SQL is highly testable deterministically. | **S** — 5 tools. | **M** — 5 `data` scenarios exist to adapt, and SQL-over-fixture-CSV yields exact-match answers. Deterministic metric is natural here. | **M** — plus the dir(`analyst`)/id(`data`) mismatch. |
| **code** | **L** — the largest single item in the port. Either (a) make `TEMPLATE_CATALOG`/`CHECKLIST_SYSTEM_PROMPT`/`_validate_checklist` language-parameterized and add a Python template set, or (b) narrow the agent's advertised scope to Next.js honestly. Plus delete ~1,782 dead lines, fix 5 fail-loudly violations incl. **fabricated placeholder code written to disk** (`code_tools.py:749-773`), add `shutil.which` preflights, fix the duplicate `validate_typescript`. | **M** — best existing suite (255 tests, genuinely behavioral in 4 files), but **2 are broken**, 1 has a copy-paste bug, 14 tools untested, and a live `orchestrator.py:633` bug is unfixed. Repair + fill, not build-from-zero. | **M** — 47 tools is a large surface; `CAPABILITY_MATRIX.md`-style generation strongly indicated. | **L** — zero of everything. Codegen quality needs a compile/test-pass metric over a task corpus; buildable deterministically (does the generated project build and pass its own tests?) but the corpus is real work. | **M** |

**Cross-cutting, not per-agent:** the sidecar contract (D5) — `api_routes.py`, `server.py`,
`export_openapi.py`, conformance test, `spec_html.py`, and an npm client per agent — is
**L per agent if hand-written, S per agent if templated.** That single decision dominates the
total. Same for the Agent UI v2 launcher story for N sidecars, which the plan explicitly
leaves open (`:92-93`).

---

## (c) Top recurring gaps — what the Agent Factory should automate

Ranked by how many of the six they hit and how mechanical the fix is.

1. **Manifest ↔ reality drift, with no guard (6/6).** `tools_count` is wrong in 4 of 6 and
   unguarded in all 6. Email pins 55 and **fails CI on drift** via
   `tests/test_capability_matrix.py` + `tests/test_email_agent.py`, with
   `packaging/capability_matrix.py` introspecting the real surface from source.
   → *Factory: generate `tools_count`, the tool list, and `CAPABILITY_MATRIX.md` by AST
   introspection; emit the drift test automatically. No human should ever type this number.*

2. **`interfaces:` asserts capabilities nothing verifies (6/6).** All six claim
   `api_server: true` with no sidecar. Nothing in `manifest.py` cross-checks a declared
   interface against shipped code.
   → *Factory: for every declared interface, scaffold the implementation **and** a conformance
   test; make `manifest.py` (or a lint gate) reject a declared interface with no backing
   artifact. This is the single highest-value automation — it converts a silent lie into a
   build error.*

3. **Tests assert registration, not behavior (5/6 at ~0% behavioral).** Every cohort agent's
   test file follows the same shape: import, defaults, construct, `assertIn(name, registry)`.
   Code is the only exception, and even it has 2 broken and 14 untested tools.
   → *Factory: generate a per-tool behavioral test skeleton from each `@tool` signature +
   docstring, with a fixture harness (temp FS, mocked network, temp scratchpad DB) — so the
   default state is "every tool has a test that calls it," not "every tool has a name."*

4. **No eval vehicle at all for 4 of 6; the scenarios that exist measure the wrong subject.**
   `agent_type` values are ChatAgent prompt profiles, so no scenario tests any standalone
   package. Only email has a deterministic non-judge harness and a release-gating scorecard.
   → *Factory: scaffold the `eval_baselines/query_sequences/`-style deterministic sequence
   pins (email's pattern: pin ordering + vocabulary, not exact counts), a `gen_scorecard.py`
   adapter, and the `scorecard-gate` CI job. Also: make `agent_type` resolve to a registry id
   so scenarios provably target the package under test.*

5. **The whole publish surface is hand-built and exists exactly once (1/7).**
   `hub/agents/npm/` contains only `agent-email`. There is exactly one
   `release_agent_*.yml`. Every cohort agent has a bare `test_*.yml` with no `schedule:`
   trigger and no release lane.
   → *Factory: generate the npm sidecar client (`src/*.ts` is ~12 near-boilerplate modules),
   `packaging/` (freeze, stamp_version, smoke_test, gen_binaries_lock, publish_to_r2), and the
   release workflow from the manifest. This is ~20 files per agent that differ mainly by id
   and port.*

6. **Silent fallbacks recur in the same three shapes (5/6).** (a) `except Exception: pass`
   with no logging — `lite_agent.py:51-58`, `app.py:321-325`, `:1006-1011`, `:1069-1073`,
   `base_prompt.py:34-35`, `shell_tools.py:557-559`; (b) provider-cascade fallback —
   `schema_inference.py:75-100`; (c) **placeholder-as-real-output** —
   `code_tools.py:749-773`, `project_management.py:637-676`.
   → *Factory: a lint rule for shapes (a) and (b) at agent-package scope. Shape (c) needs
   human review, but it is rare and detectable (any write-to-disk on an exception path).*

7. **Duplicated constants with no shared source (4/6).** `"claude-sonnet-4-20250514"` in 4
   places; `"Qwen3.5-35B-A3B-GGUF"` in 3+; `localhost:13305` twice in one file; port 3000 in
   3 places.
   → *Factory: a generated `constants.py` per package sourced from the manifest's `models:`
   and requirements, plus a lint check for model-string literals in agent code.*

8. **Directory ≠ registry id, and two competing entry-point declarations (2/6 + 6/6).**
   `browser`→`web`, `analyst`→`data`; and `gaia-agent.yaml`'s `entry_module`/`entry_class`
   (dev path, `cli_agent.py:1029-1063`) can drift from `pyproject.toml`'s entry point
   (runtime path).
   → *Factory: derive both from one source; add a test asserting they agree and that the id
   matches the directory or that the divergence is explicit.*

---

## (d) The parity kit — exact files to go from today's state to publish-ready

Derived from what email actually ships. Note the non-obvious structural fact: **email's
canonical doc root is the npm package, not the Python package.** `hub/agents/python/email/`
has **no `README.md`, no `SPEC.md`, and no `SKILL.md`** (verified). Those live in
`hub/agents/npm/agent-email/` and ship via `package.json` `files`
(`hub/agents/npm/agent-email/package.json:56-66`). `release_agent_email.yml:95-108` states
this directly: "Single canonical README for the agent: the npm client README… The python-side
README is being retired." The Python side substitutes machine-generated `CONTRACT.md` +
`specification.html`.

### Evidence — email's full inventory

**Python package doc/contract artifacts** (`hub/agents/python/email/`):

| File | Role |
|---|---|
| `gaia-agent.yaml` | Manifest. Adds beyond the cohort's: `npm_package`, `playground_url`, `models`, `min_gaia_version`, `requirements.min_lemonade_version`, and `tools_count: 55` with an inline comment naming its two drift guards. |
| `pyproject.toml` | `[project.scripts] gaia-agent-email = "gaia_agent_email.server:main"`; entry point deliberately commented out (`:33-34`) because the sidecar supersedes in-process registration. |
| `CHANGELOG.md` | Keep-a-Changelog; references `contract.SCHEMA_VERSION` for the separately-versioned REST contract. |
| `CONTRACT.md` | Frozen pydantic request/response schema (`SCHEMA_VERSION "2.3"`) shared by REST + MCP, with version history. |
| `CAPABILITY_MATRIX.md` | **Auto-generated** inventory of 25 ops (21 REST + 4 MCP) cross-referenced against eval coverage; "do not edit by hand" (`:1-17`). |
| `specification.html` | 73 KB interactive spec, generated by `gaia_agent_email/spec_html.py`. |
| `openapi.email.json` | OpenAPI 3, 22 paths, pydantic-derived schemas with `additionalProperties: false`. |
| `eval_baselines/query_sequences/triage_inbox.json` | Golden-path event-sequence pin (`required_subsequence`, `terminal`, `forbidden`). |
| `eval_baselines/query_sequences/send_needs_confirmation.json` | Pins that destructive ops emit `needs_confirmation` and never silently act. |

**Sidecar runtime modules** (`gaia_agent_email/`): `server.py`, `api_routes.py`,
`agent_routes.py`, `query_routes.py`, `connector_routes.py`, `connection_intake_routes.py`,
`export_openapi.py`, `spec_html.py`, `playground_html.py`, `contract.py`, `caller_auth.py`,
`forwarded_credentials.py`, `sse_translation.py`, `version.py`, `mcp_server.py`,
`daemon_migration.py`, `supervision.py`.

**`packaging/` (18 files)** — `freeze.py` (PyInstaller → self-contained binary),
`stamp_version.py` (single-source version propagation), `smoke_test.py` (launches the frozen
binary, polls `/health`, checks `/openapi.json` + `/version`, real `/v1/email/triage`
round-trip), `gen_scorecard.py`, `gen_package_files.py`, `gen_binaries_lock.py`,
`capability_matrix.py`, `publish_to_r2.py`, `upload_to_r2.sh`, `server.py` (freeze entry
shim), `eval_gate_report.py`, `eval_action_item_report.py`, `eval_briefing_report.py`,
`eval_drafting_report.py`, `README.md`, `HUB-UPLOAD.md`, `.gitignore`.

**Tests (55 files)** — behavioral (`test_email_briefing.py`, `test_thread_fold.py`,
`test_triage_condense.py`, `test_email_memory.py`, …), contract/conformance
(`test_rest_contract.py`, `test_envelope_docs_presence.py`, `test_spec_html_artifact.py`),
drift guards (`test_capability_matrix.py`), and unit tests for the CI gate scripts themselves
(`test_eval_gate_report.py` et al., each locking the `should_fail`→exit-code contract and the
fail-loud missing-`ANTHROPIC_API_KEY` path).

**npm sidecar** (`hub/agents/npm/agent-email/`) — `package.json`, `package-lock.json`,
`tsconfig.json`, `vitest.config.ts`, `LICENSE`, `.gitignore`, `binaries.lock.json`,
`assets/`, `examples/demo.mjs`; docs `README.md` (146), `SPEC.md` (653), `SKILL.md` (365),
`SCORECARD.md` (333), `EVALUATION.md` (90), `CHANGELOG.md` (171); `src/` — `index.ts`,
`client.ts`, `client-entry.ts`, `cli.ts`, `lifecycle.ts`, `sse.ts`, `fetch.ts`, `url.ts`,
`errors.ts`, `logger.ts`, `platform.ts`, `types.ts`; `test/` — 10 vitest files including
`query-integration.test.ts` against `test/fixtures/query_test_server.py`.

**CI** — `release_agent_email.yml` (797 lines): per-platform freeze+smoke `build` → older-
macOS verify → **`scorecard-gate`** → `email-eval` (release gate, limit 20) → `email-tests` →
`publish` (Hub Worker `/publish` + npm OIDC + docs redeploy). Plus `test_email_agent.yml`,
`test_email_agent_unit.yml`, `test_email_agent_eval.yml`, `test_agent_email_npm.yml`,
`email_scorecard_refresh.yml`, `build_cpp_email.yml`.

### The kit — what each cohort agent must add

Nothing in this list exists for any of the six today (verified: no `SPEC.md`, `SKILL.md`,
`CHANGELOG.md`, `SCORECARD.md`, `packaging/`, `openapi.*.json`, `eval_baselines/`, or npm
package under any cohort directory).

**Tier 1 — docs & manifest (mostly generatable)**
1. `hub/agents/npm/agent-<id>/README.md` — canonical, hub- and npm-rendered.
2. `.../SPEC.md` — full technical reference (own hub doc tab).
3. `.../SKILL.md` — AI-assistant integration playbook (own hub doc tab).
4. `.../CHANGELOG.md` — Keep-a-Changelog, referencing the contract schema version.
5. `.../SCORECARD.md` — **generated**, never hand-written.
6. `.../EVALUATION.md` — methodology behind the scorecard.
7. `hub/agents/python/<id>/CONTRACT.md` — frozen request/response schema + version history.
8. `hub/agents/python/<id>/CAPABILITY_MATRIX.md` — **generated** from source introspection.
9. Corrected `gaia-agent.yaml`: real `tools_count`, `models`, `min_gaia_version`,
   `requirements`, honest `permissions:`, and `npm_package`/`playground_url`.

**Tier 2 — sidecar contract (D5)**
10. `gaia_agent_<id>/api_routes.py` + `server.py` (dedicated port).
11. `gaia_agent_<id>/contract.py` — pydantic models + `SCHEMA_VERSION`.
12. `gaia_agent_<id>/export_openapi.py` → `openapi.<id>.json` (committed).
13. `gaia_agent_<id>/spec_html.py` → `specification.html` (committed).
14. `gaia_agent_<id>/caller_auth.py` + `sse_translation.py` + `version.py`.
15. `tests/test_<id>_openapi_conformance.py` + `tests/test_rest_contract.py`.
16. Lifecycle: auto-reap on parent exit, readiness vs liveness, typed error envelopes.

**Tier 3 — npm client**
17. `hub/agents/npm/agent-<id>/` — `package.json` (with `files:` shipping the Tier-1 docs),
    `src/` (~12 modules mirroring email's), `test/` (vitest), `binaries.lock.json`,
    `tsconfig.json`, `vitest.config.ts`, `LICENSE`.

**Tier 4 — packaging**
18. `packaging/freeze.py`, `stamp_version.py`, `smoke_test.py`, `gen_binaries_lock.py`,
    `gen_package_files.py`, `publish_to_r2.py`, `capability_matrix.py`, `gen_scorecard.py`,
    `eval_gate_report.py`, `README.md`, `HUB-UPLOAD.md`.

**Tier 5 — eval**
19. `eval_baselines/` with deterministic sequence pins (email's `query_sequences/` shape).
20. A labeled corpus + a **non-judge** metric per agent (per `adding-eval-scorecard` Phase
    1.4: stop rather than invent numbers).
21. Generated `SCORECARD.md` + a `scorecard_gate` invocation.

**Tier 6 — CI**
22. `.github/workflows/release_agent_<id>.yml` — build → smoke → **scorecard-gate** →
    eval → tests → publish, with the gate a hard `needs:` of publish.
23. `test_<id>_agent_unit.yml`, `test_<id>_agent_eval.yml`, `test_agent_<id>_npm.yml`,
    `<id>_scorecard_refresh.yml` (scheduled).
24. Register a PyPI pending publisher (OIDC-only) before the first run.

**Count: ~24 file groups, ~40–60 files per agent, ×6 agents.** Roughly 80% is templatable
from the manifest plus source introspection. That ratio is the case for building the factory
before porting agent number two.

---

## Recommended sequencing

The plan's §12 phases are sound; this audit sharpens two of them.

- **Do Phase 0 (ProfileSpec refactor) and the factory in parallel.** They are independent and
  each unblocks five agents.
- **Fix the mis-scopes before writing any docs.** Analyst's missing file-reading mixin and
  code's Next.js-locked validator will invalidate any SPEC/SKILL/SCORECARD written against
  today's behavior. Docs are cheap; docs that must be rewritten twice are not.
- **Make `interfaces:` verifiable before publishing anything.** Six manifests currently claim
  `api_server: true` with no sidecar. Publishing that to the hub ships a false capability
  claim to users — a correctness problem, not a polish problem.
- **Start the eval work with `file`.** Deterministic FS-state exact-match is the cheapest real
  scorecard in the cohort and proves the gate mechanism end-to-end before the hard cases
  (chat's metric decision, browser's non-stationary corpus, code's build-and-test harness).
