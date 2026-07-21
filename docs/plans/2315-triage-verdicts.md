# Legacy Agent Port — Triage Verdicts (#2315)

**Gate:** no port work starts before its agent has a verdict. This record closes the five
verdicts milestone 58 left open — **chat, fileio, browser, analyst, code** — and
consolidates them with the thirteen already decided into one canonical table.

**Method** (per the milestone's ordering principle — *capability-truth before
documenting*): each verdict is grounded in what the agent's code actually does versus what
its manifest advertises, drawn from [`port-audit-6-agents.md`](port-audit-6-agents.md),
[`port-triage-thin-infra-agents.md`](port-triage-thin-infra-agents.md), and
[`port-triage-external-dep-agents.md`](port-triage-external-dep-agents.md), cross-checked
against the agent source at HEAD.

## Verdict summary (all 18)

| Agent | Verdict | Issue | Basis |
|-------|---------|-------|-------|
| **chat** | **PORT** | #2323 | ProfileSpec refactor; `doc`→docqa, `file`→fileio (see collapse note) |
| **fileio** | **PORT** | #2321 | bounded generalize; also absorbs chat's `file` profile |
| **browser** | **PORT** | #2320 | generalize: JS-render path + pluggable search provider |
| **analyst** | **PORT** | #2318 | generalize: compose the missing file-read mixin; keeps SQL identity |
| **code** | **PORT** | #2319 | generalize-vs-narrow is #2319's scoping call (see below) |
| docqa | PORT | #2322 | sole RAG survivor; absorbs chat `doc` + doc-search |
| summarize | PORT | #2324 | drop the Qwen pin / two-model config, then port |
| docker | PORT | #2325 | re-scope the phantom `inspect` tool, then port |
| sd | PORT | #2326 | promote generation to a shared layer |
| doc-search | MERGE → docqa | #2322 | `category: examples`; duplicate RAG |
| emr | MERGE → A12 health | #2329 | into #1496 + layer AH-L4.2 |
| jira | DEFER → L8 TaskStore | #2328 | external-tracker connector, not a standalone agent |
| blender | DISCARD (keep in-repo example) | #2327 | MCP-passthrough demo |
| hello-world | DISCARD (→ init template) | #2331 | `category: examples`, `experimental` |
| word-count | DISCARD (→ init template) | #2331 | `category: examples`, `experimental` |
| routing | DISCARD from catalog | — | infrastructure; no `gaia.agent` entry point |
| connectors-demo | DEFER (infrastructure) | #2332 | connector-flow demo |
| builder | STAY IN-CORE | #2332 | the only remaining reserved builtin |

The bold **PORT** rows are decided in this record; the rest restate the milestone-58
triage result and their existing issue headers.

## The five open verdicts

### chat → PORT

The chat/doc/file "split" is registration-only: three registry ids (`chat`, `doc`, `file`)
all instantiate one **2,258-line** `ChatAgent` class differing only by `prompt_profile`.
The manifest declares `api_server: true` and `mcp_server: true` with no REST/MCP surface
behind them, `tools_count: 0` where 15 tools are registered, and no `permissions:` block
despite shell/filesystem/clipboard/network tools.

**Verdict:** the *conversation* profile is a real product → **PORT**. It ships only after
the **ProfileSpec refactor (#2323)** extracts the profile switch, splits the 40+-field
config, makes RAG/watchdog/session construction lazy, corrects `tools_count`, drops the
unbacked interface claims, and fixes the fail-loudly violations — because until then the
three profiles cannot ship as independent, honest packages. The `doc` and `file` profiles
do **not** ship as chat; they merge (see the collapse note). Behaviour-preserving, but it
touches prompt assembly, so it **requires an eval re-baseline before merge**, not after.

### fileio → PORT

The system prompt promises the agent will "ask for confirmation before destructive
actions," but grepping both composed mixins for remove/unlink/move/rename returns zero
matches — **there is no delete, move, or rename tool**; the only destructive operation is a
content overwrite. `FileIOToolsMixin` and `FileSearchToolsMixin` both define
`read_file`/`write_file`/`edit_file` with no duplicate check, so the simpler versions
silently shadow the richer ones, and the process-global `_TOOL_REGISTRY` has no
snapshot/restore isolation. `tools_count: 0` vs ~19 real.

**Verdict: PORT** — the cheapest real generalization in the cohort: add the delete/move/
rename tools the prompt already advertises, add recursive glob, fix the 3-tool registry
collision and add isolation. It is also the **MERGE target for chat's `file` profile** and
the **eval starting point** for the fleet (synthetic-FS fixture, exact-match on resulting
filesystem state — the easiest scorecard to build).

### browser → PORT

Named "browser," but `fetch_page`'s own docstring says it "Does NOT execute JavaScript" —
there is **no JS-render path**, so a large fraction of the modern web returns empty.
`download_file`'s docstring and success message tell the LLM to call `read_file` or
`index_document` next; neither is registered, so following the tool's own advice yields a
tool-not-found failure on every download. The search provider is hardcoded DuckDuckGo HTML
scraping (`src/gaia/web/client.py:827-839`) with no config/API-key path. `tools_count: 10`
vs 3 real; directory `browser` ≠ registry id `web`.

**Verdict: PORT** (generalize) — add a headless render path, make the search provider
pluggable, fix the docstrings/messages pointing at unregistered tools. **#48 overlap:**
`#1485 knowledge` was specced more generally over web search + extraction and may later
supersede this agent — recorded as a future-consolidation question, **not** a discard
reason (knowledge is unbuilt; browser is the existing capability). Eval is the hard part
here (L): it needs a labelled corpus and a deterministic metric invented from nothing over
non-stationary web content, so its fixture must be a frozen page-snapshot set — do not
start the eval here; prove the mechanism on fileio first.

### analyst → PORT

The manifest and README advertise "structured data analysis (CSV/Excel, scratchpad SQL),"
but **AnalystAgent cannot read a file** — it composes only `ScratchpadToolsMixin` and
`MCPClientMixin` (no file mixin), and the tool it needs, `analyze_data_file`, exists but is
wired to FileIOAgent instead. So the advertised entry path (point it at a CSV) does not
exist. `agent.py:77` calls `_TOOL_REGISTRY.clear()`, a process-global mutation that clobbers
other agents' tools in-process. `tools_count: 10` vs 5 real; directory `analyst` ≠ id `data`.

**Verdict: PORT** (generalize) — compose `FileSearchToolsMixin` so `analyze_data_file` is
reachable, re-prompt around a real file→table pipeline, and isolate the registry mutation.
The agent keeps its **scratchpad-SQL analysis identity**; the resulting file-read overlap
with fileio is acceptable (fileio = general file ops, analyst = data-analysis workflow).
The precise analyst/fileio capability boundary is a #2318 scoping detail, not a triage blocker.

### code → PORT (generalize-vs-narrow deferred to #2319)

`CodeAgent.__init__` defaults to `language="python"`, but the only live path forces every
request through a validator that hard-requires TypeScript steps
(`checklist_generator.py:608-624`) against a 12-of-13 Next.js template catalog — so a
default-configuration Python request **fails the agent's own validator**, contradicting the
in-code claim that the orchestrator "handles correct step ordering for all project types."
It carries ~1,782 dead lines (including a working, never-invoked `PythonFactory`). One
fail-loudly violation is dangerous, not cosmetic: `code_tools.py:749-773` writes fabricated
placeholder code to disk on LLM timeout and reports it as generated output (#2316).
`tools_count: 0` vs 47.

**Verdict: PORT** — not a merge/discard candidate: `#48`'s `A4 code-context` (#1488) is
repo-context-only (`repo_status`, `review_prs`, `dep_advisories`), functionally distinct
from CodeAgent's app-generation mission. The genuine sub-decision — **(a)** generalize
(parameterize `TEMPLATE_CATALOG`/`CHECKLIST_SYSTEM_PROMPT`/`_validate_checklist`, add a real
Python template set, revive `PythonFactory`; the largest single item in the port) vs **(b)**
narrow the advertised surface to Next.js honestly — is **#2319's implementation-scoping
call**. Recommendation: **(b) narrow-to-Next.js for the first honest ship, (a) generalize as
a fast-follow** — but the dangerous fabricated-code fail-loudly bug is fixed either way, and
the current state (advertises general code generation, delivers Next.js only) does not ship.

## Cross-cutting: the three-profile collapse

chat/doc/file are one class today. The verdicts route each profile to a distinct home:

- **conversation → chat package** (PORT, #2323)
- **doc → docqa** (MERGE, #2322 — ~600 lines of anti-hallucination rules, multi-document
  resolution, RAG indexing + watchdog absorbed by the sole RAG survivor)
- **file → fileio** (MERGE — the file-ops logic joins fileio's bounded generalization)

**Release hazard:** chat's entry points currently claim all three ids
(`chat = build_chat`, `doc = build_doc`, `file = build_file`). When `doc` and `file` ship as
their own wheels, chat must **drop those entry points in the same release** as the new wheels
land, or the registry collides. Coordinate in one release (tracked on #2323 / #2322 / #2321).
