# GAIA CodeAgent — Deep Code Analysis

**Date:** 2026-04-19
**Branch:** `feature/coding-agent`
**Scope:** `src/gaia/agents/code/` (~22K LoC / 48 files), the `gaia-code` console script, and its base dependencies in `src/gaia/agents/base/`.

The goal is to ground a decision about how to build a Claude-Code-rival general coding agent: evolve, rewrite, or fork the tool layer.

---

## Revision History

- **v2 (2026-04-19)** — Critique pass + scope expansion. Fixed factual drift (LoC numbers for `checklist_executor.py`, `checklist_generator.py`, test suite), clarified that `steps/error_handler.py` is **live** (not dead — used by the orchestrator today). Added: §11 self-critique of v1, §12 phased rewrite plan, §13 self-governance & autonomy model (heartbeat, task queue, scoped self-code authority, living `ARCHITECTURE.md`, guardrail stack, event-driven wake-ups), §14 GAIA-native capabilities (GitHub CLI mixin, test-every-change discipline, agent-builder tools, CLAUDE.md compliance, web browsing & research, license-aware OSS reuse with attribution, GitHub event triggers, `amd/gaia` repo binding with bot identity), §15 keep/refactor/delete matrix, §16 success metrics across SWE-bench / GAIA-Internal-20 / repo-binding scorecard / autonomy trace, §17 summary. Known v1 weaknesses — inconsistent test-LoC (claimed 2.3K and 2.9K on the same document) and dead-code over-claim — corrected.
- **v1 (2026-04-19)** — Initial deep-dive. Claim: rewrite, don't evolve.

---

## TL;DR

The current CodeAgent is a **Next.js CRUD-app scaffolder** masquerading as a general "Code Agent." The orchestrator executes a fixed, dependency-ordered template catalog (`create_next_app` → `setup_prisma` → … → `validate_styles`) from a hard-coded list of 13 templates. Roughly **4K LoC is outright dead** (`factories/` 0 live refs, `workflows/` 0 live refs, `steps/nextjs.py` + `steps/python.py` 0 live refs — but `steps/base.py` and `steps/error_handler.py` are **live infrastructure the orchestrator depends on**). Python support is documented as deprecated (`docs/guides/code.mdx:14`) but the Python code path is still present, half-wired, and still pulls in Python-only validators. There is **no general-purpose file editing loop**, no repo-wide search, no conversational turn, no sub-agent dispatch, and **no self-governance**: the agent cannot schedule its own follow-up work, cannot modify its own source, and has no heartbeat it can start, stop, or tune. Tests are **3,481 LoC** across four files but almost entirely cover the deprecated Python path or mock the orchestrator; there is no end-to-end eval today.

**Recommendation: rewrite.** Keep `src/gaia/agents/base/` and `gaia.chat.sdk`. Build a new agent under `src/gaia/agents/code/` (reclaim the name after a v0.x migration) or `src/gaia/agents/coder/`. Delete the template catalog, the checklist orchestrator, and the `web_dev_tools`/`prisma`/`factories`/`workflows` trees. Keep the thin utilities — `cli_tools.run_cli_command` + process registry, `file_io.read_file/write_file/edit_file/search_code`, `external_tools.search_documentation/search_web` — as a starting tool kit, and keep `steps/error_handler.py` for tool-level retry/recovery.

Grant the rewrite **scoped self-governance** on day one: the ability to read and edit its own source under a whitelist, schedule its own future wake-ups via a heartbeat channel, **wake on external events from GitHub (issues, PRs, CI completions)**, and decide whether to continue or sleep — all under an auditable, revocable guardrail layer. The agent is **uniquely bound to `amd/gaia`** so it can autonomously triage issues, comment on PRs, and coordinate with human contributors. It is **specialized for building GAIA agents** while carrying a general-purpose toolkit (web browsing, open-source code search with license-aware reuse and attribution). See §12–§14.

---

## 1. Architecture Map

The surface area looks like a ReAct agent but the control flow of `gaia-code` **does not use the base `Agent` ReAct loop at all**. The `CodeAgent.process_query` override in [`src/gaia/agents/code/agent.py:201-353`](../../src/gaia/agents/code/agent.py) routes every query through a single deterministic path:

```
gaia-code "..."
  → cli.py:cmd_run                                 # src/gaia/agents/code/cli.py:69
  → RoutingAgent.process_query (language detect)   # src/gaia/agents/routing/agent.py
  → CodeAgent(language=..., project_type=...)
  → CodeAgent.process_query
       ├─ schema_inference.infer_schema            # Perplexity → LLM → fallback
       ├─ assemble system prompt (base + per-lang)
       └─ CodeAgent._process_with_orchestrator
             → Orchestrator.execute                # src/gaia/agents/code/orchestration/orchestrator.py:186
                 for iteration in 1..max_checklist_loops=10:
                   ChecklistGenerator.generate_initial_checklist  # LLM call
                     → returns list[ChecklistItem(template, params, description)]
                   ChecklistExecutor.execute(checklist, context)
                     for item in checklist.items:
                       if item.template in DETERMINISTIC_TEMPLATES: run tool directly
                       elif item.template in LLM_GENERATED_TEMPLATES: LLM generates code per-file
                       else: fall back to tool_executor (register_*)
                   _assess_checkpoint(...)         # LLM reviewer call
                   if status == "complete": break
```

**There is exactly one code path.** The `step_through` flag does *not* enable a different loop — it just pauses between checklist items. The interactive REPL (`cli.py:19-66`) is also just a thin wrapper over the same single-shot `process_query` per prompt.

Dead structural layers — imported but never invoked by the orchestrator:
- [`orchestration/factories/`](../../src/gaia/agents/code/orchestration/factories/) — `ProjectFactory`, `NextJSFactory`, `PythonFactory`. `grep` for `NextJSFactory|PythonFactory|ProjectFactory` outside the factories tree returns **zero** hits.
- [`orchestration/workflows/`](../../src/gaia/agents/code/orchestration/workflows/) — `create_nextjs_workflow`, `create_python_workflow`. Only referenced from `factories/`.
- [`orchestration/steps/`](../../src/gaia/agents/code/orchestration/steps/) — `CreateNextAppStep`, `SetupPrismaStep`, etc. Only referenced from `workflows/`.

Together that's ~60K bytes of code (`factories/` + `workflows/` + `steps/nextjs.py` + `steps/python.py`) that exists purely as archaeology from a previous architecture. **Two files under `steps/` are still live and must not be deleted:** `steps/base.py` (dataclasses `UserContext`, `StepResult`, `StepStatus`, `ToolExecutor` typedef, `ErrorCategory` enum — imported by orchestrator/executor) and `steps/error_handler.py` (`ErrorHandler`, `RecoveryAction` — imported by `orchestrator.py:30` and `checklist_executor.py:34`, instantiated at `orchestrator.py:162`). None of the abstract `BaseStep` subclasses are used.

**Task decomposition** happens exactly once: `ChecklistGenerator.generate_initial_checklist` ([`checklist_generator.py:231`](../../src/gaia/agents/code/orchestration/checklist_generator.py)) sends one prompt to the LLM with the full template catalog; the LLM returns a flat list of template invocations; they execute in order. If validation fails, `generate_debug_checklist` is called at the top of the next iteration with the prior logs. There is no hierarchical plan, no sub-tasks, no tree search.

## 2. Tool Inventory

**57 `@tool`-decorated functions** registered across 12 modules (grep confirmed: `grep -c "@tool" src/gaia/agents/code/tools/*.py` → 57). Listed below with scope flagged.

| Tool | Module | Scope | Purpose |
|------|--------|-------|---------|
| `read_file` | file_io | **general** | Read any file; Python-specific AST analysis if `.py`; no binary support beyond "N bytes" placeholder |
| `write_file` | file_io | general | Write any file |
| `edit_file` | file_io | general | Edit file (full-file replacement or patch?) |
| `search_code` | file_io | general | Grep/regex across a directory |
| `generate_diff` | file_io | general | Compute unified diff before writing |
| `write_python_file` | file_io | **python** | Validated Python write |
| `edit_python_file` | file_io | **python** | Python-AST-aware edit |
| `write_markdown_file` | file_io | general | Markdown write (pointless specialization) |
| `replace_function` | file_io | **python** | AST function replacement |
| `update_gaia_md` | file_io | general | Update project's `GAIA.md` |
| `list_files` | project_management | general | ls-ish |
| `validate_project` | project_management | **python** | Validates Python project structure |
| `create_project` | project_management | **python** | End-to-end Python project scaffolder via LLM (separate from the Next.js flow) |
| `generate_function` | code_tools | **python** | LLM generates a Python function |
| `generate_class` | code_tools | **python** | LLM generates a Python class |
| `generate_test` | code_tools | **python** | LLM generates pytest tests |
| `list_symbols` | code_tools | **python** | AST symbol extraction |
| `parse_python_code` | code_tools | **python** | AST parse |
| `validate_syntax` | code_tools | **python** | `compile()` + `ast.parse()` |
| `analyze_with_pylint` | code_tools | **python** | Pylint wrapper |
| `format_with_black` | code_formatting | **python** | Black formatter |
| `lint_and_format` | code_formatting | **python** | Pylint + Black composite |
| `execute_python_file` | testing | **python** | Subprocess-run `python file.py` |
| `run_tests` | testing | **python** | pytest wrapper (also used for `npm test` by the orchestrator but tool body is python-targeted) |
| `auto_fix_syntax_errors` | error_fixing | **python** | Scan + fix via LLM |
| `fix_code` | error_fixing | general-ish | LLM fixer; takes `file_path + error_description`; works on any text file but the prompt is Python/TS biased |
| `create_architectural_plan` | error_fixing | general | LLM plan generator |
| `create_project_structure` | error_fixing | **python** | Creates folders from plan |
| `implement_from_plan` | error_fixing | **python** | LLM fills in files from plan |
| `create_workflow_plan` | error_fixing | general | Meta-plan generator (not called by orchestrator) |
| `fix_linting_errors` | error_fixing | **python** | Pylint-specific |
| `init_gaia_md` | error_fixing | general | Seed `GAIA.md` |
| `fix_python_errors` | error_fixing | **python** | Runtime error fixer |
| `setup_prisma` | prisma_tools | **nextjs/prisma** | Install + init Prisma |
| `setup_app_styling` | web_dev_tools | **nextjs** | Write `layout.tsx` + `globals.css` from `code_patterns.py` templates |
| `manage_api_endpoint` | web_dev_tools | **nextjs** | Generate `/api/.../route.ts` |
| `manage_react_component` | web_dev_tools | **nextjs** | Generate list/form/new/detail pages |
| `update_landing_page` | web_dev_tools | **nextjs** | Rewrite `src/app/page.tsx` |
| `setup_nextjs_testing` | web_dev_tools | **nextjs** | Vitest + RTL wiring |
| `validate_crud_completeness` | web_dev_tools | **nextjs** | Checks all CRUD routes exist |
| `generate_crud_scaffold` | web_dev_tools | **nextjs** | One-shot resource scaffold |
| `manage_data_model` | web_dev_tools | **prisma** | Append model to `schema.prisma` |
| `manage_prisma_client` | web_dev_tools | **prisma** | `prisma generate && prisma db push` |
| `manage_web_config` | web_dev_tools | **nextjs** | Edit `next.config.ts`/`package.json` |
| `generate_style_tests` | web_dev_tools | **nextjs** | Vitest CSS tests |
| `test_crud_api` | validation_tools | **nextjs** | HTTP test the generated API |
| `validate_typescript` | validation_tools & typescript_tools | **typescript** | Runs `tsc --noEmit`. Duplicated tool name — `typescript_tools.py:29` and `validation_tools.py:402` both register `validate_typescript` which will collide in `_TOOL_REGISTRY` |
| `validate_crud_structure` | validation_tools | **nextjs** | File-layout check |
| `validate_styles` | validation_tools | **nextjs** | CSS integrity check |
| `run_cli_command` | cli_tools | **general** | Universal shell execution (npm, python, docker, gh). Only genuinely general tool with good process management |
| `stop_process` | cli_tools | general | Stop a PID tracked by `run_cli_command` |
| `list_processes` | cli_tools | general | List tracked PIDs |
| `get_process_logs` | cli_tools | general | Tail tracked PID's stdout/stderr |
| `cleanup_all_processes` | cli_tools | general | Kill all |
| `search_documentation` | external_tools | general | Context7 lookup — Next.js, React, Prisma, Zod library docs |
| `search_web` | external_tools | general | Perplexity search |

**Ratio:** 14 genuinely general tools (most in `file_io` + `cli_tools` + `external_tools`), 23 Python-only tools, 20 Next.js/Prisma/TS-only tools. Tool-name collision (`validate_typescript`) suggests no one noticed — no lint gate on `_TOOL_REGISTRY` uniqueness.

## 3. Prompt Strategy

**Composition** (see [`system_prompt.py:17-41`](../../src/gaia/agents/code/system_prompt.py)):

```python
if language == "typescript":
    return get_base_prompt(gaia_md_path) + NEXTJS_PROMPT    # 2734 + 1336 = 4070 bytes
else:
    return get_python_prompt(gaia_md_path)                   # base + python block = 2734 + 5157 ≈ 7891 bytes
```

Then `CodeAgent.process_query` ([agent.py:285-290](../../src/gaia/agents/code/agent.py)) appends schema context, workspace context, and the formatted tool list:

```python
self.system_prompt = (
    base_prompt
    + schema_context                                        # 0-500 bytes from Perplexity/LLM schema inference
    + workspace_context                                     # ~200 bytes
    + f"\n\n==== AVAILABLE TOOLS ====\n{self.tools_description}\n\n"  # ~6-10K bytes for 57 tools
)
```

**Effective system prompt:** ~12–18 KB depending on language and tool registrations. Given `max_steps=100` and `max_checklist_loops=10`, each loop's generator prompt is `system_prompt + CHECKLIST_SYSTEM_PROMPT + get_catalog_prompt()`:

- `CHECKLIST_SYSTEM_PROMPT` ([checklist_generator.py:124-212](../../src/gaia/agents/code/orchestration/checklist_generator.py)) — ~4KB of rigid rules (always start with `create_next_app`, end with `validate_styles`, etc.)
- `get_catalog_prompt()` ([template_catalog.py:408-436](../../src/gaia/agents/code/orchestration/template_catalog.py)) — ~3KB describing the 13 templates

**However, the assembled `CodeAgent.system_prompt` is never actually sent to the LLM.** It's prepared (`agent.py:285`) but the orchestrator bypasses the base Agent's ReAct loop entirely and sends its own self-contained checklist prompt via `llm_client.send(...)`. The `tools_description` accumulated in the system prompt is wasted work.

**`code_patterns.py` is NOT a prompt.** Despite being 67KB / 2034 lines in the `prompts/` directory, it's a Python module of template-string code snippets — `APP_LAYOUT`, `API_ROUTE_GET`, `CLIENT_COMPONENT_FORM` etc. — imported by `web_dev_tools.py` and `checklist_executor.py` for **string-template substitution** when generating Next.js files:

```python
# src/gaia/agents/code/prompts/code_patterns.py:181
API_ROUTE_GET = """export async function GET() {{
  try {{
    const {resource_plural} = await prisma.{resource}.findMany({{
      orderBy: {{ createdAt: 'desc' }} ...
"""
```

It's a pre-Claude-Code "fill-in-the-blank" codegen approach, not something the LLM ever sees.

## 4. Orchestration Model

Plan-then-execute, with iteration. **Not** interleaved ReAct. Flow inside `Orchestrator.execute`:

1. **Plan** — `ChecklistGenerator.generate_initial_checklist(context, project_state)` emits a flat list of up to ~15 `ChecklistItem(template, params, description)`.
2. **Execute** — `ChecklistExecutor.execute` walks items in order. For each item:
   - If `template in DETERMINISTIC_TEMPLATES` (create_next_app, setup_prisma, prisma_db_sync, setup_testing, run_typescript_check, validate_styles, generate_style_tests, run_tests, fix_code) → dispatch to a registered tool directly.
   - If `template in LLM_GENERATED_TEMPLATES` (generate_react_component, generate_api_route, setup_app_styling, update_landing_page) → call the LLM per-item to generate the file contents, then write.
3. **Assess** — `_assess_checkpoint` ([orchestrator.py:463](../../src/gaia/agents/code/orchestration/orchestrator.py)) sends the aggregated validation logs to the LLM and asks for `{"status": "complete" | "needs_fix", ...}`.
4. **Iterate** — if `needs_fix`, call `generate_debug_checklist` with the prior errors and go again. Max 10 loops ([orchestrator.py:145](../../src/gaia/agents/code/orchestration/orchestrator.py)).

Differences from Claude Code's turn-based ReAct loop:

| Dimension | GAIA CodeAgent | Claude Code |
|-----------|----------------|-------------|
| Planning | Up-front, whole-project checklist | Per-turn, next tool call only |
| Tool calls | Deterministic dispatch by template name | LLM chooses tool dynamically |
| Recovery | Re-plan whole checklist from validation logs | Incremental: next turn adjusts based on last tool result |
| Cancellation | Not supported (no streaming, no interrupt) | Supported |
| User-in-the-loop | `step_through` flag pauses between items | Conversational, every turn |
| Sub-agents | None | Supported (Agent tool) |
| State | `UserContext` dict accumulated across iterations | Full conversation history |
| Max steps / loops | Two separate limits: `max_steps=100`, `max_checklist_loops=10` | Single limit, per-turn budget |

The model is closer to a **terraform-apply-with-retry** than an agent. The LLM writes a plan; a deterministic machine runs it; if the machine reports failure, the LLM writes a new plan. The only LLM-in-the-inner-loop behavior is the per-file codegen in `LLM_GENERATED_TEMPLATES`.

## 5. Validators

All four validators in [`src/gaia/agents/code/validators/`](../../src/gaia/agents/code/validators/) are **Python-only** (AST-based):

| Validator | File | Purpose | Multi-language? |
|-----------|------|---------|-----------------|
| `SyntaxValidator` | `syntax_validator.py` (172 LoC) | `compile()` + `ast.parse()`; indentation check; line length | No — Python |
| `AntipatternChecker` | `antipattern_checker.py` (242 LoC) | Function/class/file length limits, combinatorial naming, nesting/branch/loop counts, duplicate classes | No — Python |
| `ASTAnalyzer` | `ast_analyzer.py` (198 LoC) | Symbol extraction (functions, classes, imports), docstrings, signatures | No — Python |
| `RequirementsValidator` | `requirements_validator.py` (146 LoC) | Detects hallucinated packages in `requirements.txt` (e.g., `flask-graphql-x-x-x-x`) | No — Python/pip |

They are instantiated as `self.syntax_validator`, `self.antipattern_checker`, etc. in `CodeAgent.__init__` ([agent.py:149-152](../../src/gaia/agents/code/agent.py)) and used via `validation_parsing.ValidationAndParsingMixin` and two `_fix_code_with_llm` call sites ([code_tools.py:736](../../src/gaia/agents/code/tools/code_tools.py), [error_fixing.py:1245](../../src/gaia/agents/code/tools/error_fixing.py)).

**They are never invoked on the Next.js path.** TypeScript validation happens by shelling out to `tsc --noEmit` via `validate_typescript` in `validation_tools.py`. No TypeScript AST analysis, no ESLint wrapper.

The validators are **post-generation checks** — they operate on string `content` or `Path`, not before execution. The validator layer has no hook into an `execute-then-validate-then-rollback` pattern; the orchestrator's recovery is instead "re-plan."

## 6. Scope Signals — Is This a Project Scaffolder?

**Hypothesis confirmed.** The agent is overwhelmingly a Next.js CRUD scaffolder. Evidence:

1. **Template catalog is 100% Next.js/Prisma** ([template_catalog.py:93-381](../../src/gaia/agents/code/orchestration/template_catalog.py)). Every one of the 13 templates — `create_next_app`, `setup_app_styling`, `setup_prisma`, `setup_testing`, `generate_prisma_model`, `prisma_db_sync`, `generate_api_route`, `generate_react_component`, `update_landing_page`, `run_typescript_check`, `validate_styles`, `generate_style_tests`, `fix_code` — presumes a Next.js project. `fix_code` is the only one that's even arguably general.

2. **Orchestrator REQUIRES setup_app_styling + setup_testing + generate_style_tests + run_typescript_check + validate_styles** ([checklist_generator.py:157-171](../../src/gaia/agents/code/orchestration/checklist_generator.py)):

   > "REQUIRED: Include `setup_app_styling` after `create_next_app`
   > REQUIRED: Include `setup_testing` after `setup_app_styling`
   > REQUIRED: End with `run_typescript_check`, then `validate_styles` as the last 2 commands
   > These setup and validation commands are mandatory — a plan without them is INVALID."

3. **User-facing docs confirm scope** ([`docs/guides/code.mdx:14`](../../docs/guides/code.mdx)):

   > "The Code Agent now focuses on generating full-stack TypeScript web applications (Next.js + Prisma + Tailwind). **Python code generation is no longer supported.**"

4. **CLI examples are all Next.js CRUD apps** ([`cli.py:233-252`](../../src/gaia/agents/code/cli.py)): "Build me a todo tracking app," "Build me a movie tracking app in nextjs," etc.

5. **`code_patterns.py` = 67KB of Next.js template strings**. `grep` shows 24 top-level template constants, all Next.js/React/Prisma/Vitest code.

6. **`web_dev_tools.py` = 1758 LoC** of Next.js-specific tool implementations (manage_api_endpoint, manage_react_component, manage_data_model, setup_app_styling, update_landing_page, setup_nextjs_testing, generate_crud_scaffold, manage_prisma_client, manage_web_config, generate_style_tests, validate_crud_completeness — every tool bound to a Next.js file path).

7. **Project directory preparation assumes create-next-app semantics** ([orchestrator.py:566-612](../../src/gaia/agents/code/orchestration/orchestrator.py)). `_prepare_project_directory` specifically works around `create-next-app` failing on non-empty directories by asking the LLM to pick a subdirectory name.

8. **The final success message in `display_result`** ([agent.py:530-532](../../src/gaia/agents/code/agent.py)) hard-codes Next.js dev workflow:

   ```python
   self.console.print("  1. cd {project_dir}")
   self.console.print("  2. npm run dev")
   self.console.print("  3. Open http://localhost:3000 in your browser")
   ```

The Python path is a vestige. `python_prompt.py` still exists and describes a `create_project` tool-based flow, but: (a) docs say Python is no longer supported, (b) the orchestrator checklist generator's `CHECKLIST_SYSTEM_PROMPT` has no Python templates, (c) the required-final-steps list (`run_typescript_check`, `validate_styles`) makes every Python plan invalid-by-construction.

## 7. Gap Analysis vs. Claude Code

What Claude Code has that this agent does not:

| Capability | Claude Code | GAIA CodeAgent |
|------------|-------------|----------------|
| Open an existing repo and answer questions about it | Yes | **No** — agent creates a fresh project in an empty dir; it never reads an existing codebase to inform work |
| `read` an arbitrary file and reason about it | Yes | Partial — `read_file` exists but post-call analysis only handles `.py` and `.md` (file_io.py:81-176) |
| `grep`/search across a repo | Yes (`Grep`, `Glob`) | Partial — `search_code` tool exists but returns raw regex matches, no ranked relevance |
| Edit a specific range in a file | Yes (`Edit` with old_string/new_string) | Partial — `edit_file` and `edit_python_file` exist but no public docs on semantics; `replace_function` requires AST |
| Multi-turn conversation with intent clarification | Yes | **No** — every REPL prompt is a fresh `process_query` with no carried conversation history; the orchestrator is single-shot-per-query |
| Ask the user clarifying questions mid-task | Yes | **No** — orchestrator runs to completion (or max loops) without user input |
| Sub-agent dispatch (`Task` tool) | Yes | **No** |
| TodoWrite / persistent task tracking across turns | Yes | **No** |
| Background tasks with monitoring | Partial — `cli_tools.run_cli_command(background=True)` exists and is solid |
| Broad language support | Yes | **No** — Python (deprecated) + TypeScript/Next.js only |
| Tool-call streaming | Yes | **No** — `streaming=False` by default; documented as causing "duplicate output" (agent.py:120) |
| Cancellation | Yes | **No** — no interrupt handler inside orchestrator |
| Partial results on timeout | Yes | **No** — subprocess returns `(1, "Command timed out")` and continues (orchestrator.py:444) |
| Safety rails on shell (prompt-confirm on destructive commands) | Yes | **No** — `run_cli_command` has no allowlist/denylist; the `PathValidator` gates file IO only |
| GitHub CLI as a first-class tool (PRs, issues, checks, releases) | Via Bash | **No** — `run_cli_command` can shell out to `gh`, but there is no structured `gh` mixin, no PR/issue types, no CI-log fetcher |
| Self-scheduled follow-up work (wake-up in N minutes / N hours) | Partial (via user hooks) | **No** — no scheduler, no cron, no heartbeat |
| Self-modification of its own source under a safety contract | No (sandboxed) | **No** — neither the tools nor the prompt permit touching `src/gaia/` |
| Living architecture document maintained by the agent itself | No | **No** — system prompt is static per query; no `ARCHITECTURE.md` feedback loop |
| Test-every-change discipline enforced by the agent loop | No (convention) | **No** — the orchestrator's `validate_*` templates only cover the Next.js scaffold path |
| CI/CD integration (kick off workflows, watch runs, parse failures) | Via Bash | **No** — no `gh run watch`, no workflow-authoring tools, no automated PR-from-fix loop |

What GAIA CodeAgent has that Claude Code arguably does not:

- **Deterministic scaffolding for a very specific stack.** If the user says "build me a Next.js todo app," the fixed checklist gets them to a running app more predictably than asking Claude Code to do the same from scratch.
- **`search_documentation` via Context7 / `search_web` via Perplexity** as first-class tools baked in, not web-fetch wrappers.
- **Background-process tracking as core infrastructure.** `cli_tools.py` with `ProcessInfo`, `port_registry`, `find_process_on_port`, graceful SIGINT + force-kill — genuinely useful and reusable.
- **Per-iteration LLM-as-reviewer checkpoint.** The `_assess_checkpoint` pattern (orchestrator.py:463) — send aggregated logs to LLM, ask "done?" — is a good idea worth keeping.

## 8. Code Quality & Tech Debt Signals

**File size violations** (the repo's own `MAX_FILE_LINES = 1000` in `antipattern_checker.py`):

| File | LoC | Status |
|------|-----|--------|
| `prompts/code_patterns.py` | 2034 | Template library — acceptable as a separate concern |
| `orchestration/checklist_executor.py` | 1763 | **Over limit by 76%.** Mixes deterministic routing, LLM codegen, validation, recovery |
| `tools/web_dev_tools.py` | 1758 | **Over limit by 76%.** 11 tools of ~150 LoC each. Should split per-feature (api, component, data_model, styling) |
| `tools/error_fixing.py` | 1349 | **Over limit by 35%.** 9 tools; `create_architectural_plan` + `implement_from_plan` + `create_project_structure` + `create_workflow_plan` look like an abandoned architectural-planner subsystem orthogonal to the main checklist |
| `tools/cli_tools.py` | 1138 | **Over limit by 14%.** Process registry + `run_cli_command` — the single best tool in the tree |
| `tools/project_management.py` | 1018 | At limit. `create_project` is a Python-only ~600-LoC LLM-driven scaffolder that duplicates the orchestrator's role for Python |
| `base/agent.py` | 3068 | Already over, shared concern, separate scope |
| `tools/file_io.py` | 891 | Approaching limit |
| `orchestration/orchestrator.py` | 841 | Approaching limit |
| `tools/validation_tools.py` | 806 | Approaching limit |
| `orchestration/checklist_generator.py` | 713 | Under limit after recent trimming; still dense |

**Dead code** (imports trace → no runtime reference from `agent.py` or `orchestrator.py`):
- `orchestration/factories/base.py`, `nextjs_factory.py`, `python_factory.py` — not imported by any live path
- `orchestration/workflows/base.py`, `nextjs.py`, `python.py`
- `orchestration/steps/nextjs.py` (30K), `steps/python.py` (10K) — `steps/base.py` still used
- `error_fixing.py::create_architectural_plan`, `implement_from_plan`, `create_project_structure`, `create_workflow_plan` — registered as tools but the orchestrator never schedules them
- `tests/test_code_agent.py` tests assert tools like `generate_function`, `generate_class`, `generate_test` exist; these tools are only useful on the (deprecated) Python path

**"No silent fallbacks" rule** (per [`CLAUDE.md`](../../CLAUDE.md)):
- 12 `pass` blocks inside `except` across `src/gaia/agents/code/`. Examples:
  - [`prompts/base_prompt.py:34-35`](../../src/gaia/agents/code/prompts/base_prompt.py) — silently swallow any error reading `GAIA.md`. Violates the rule.
  - [`validators/antipattern_checker.py:101-102`](../../src/gaia/agents/code/validators/antipattern_checker.py) — `except SyntaxError: pass` (comment says "let pylint handle" — OK but should raise-and-translate).
  - [`validators/syntax_validator.py`](../../src/gaia/agents/code/validators/syntax_validator.py) — multiple `except: pass` patterns.
  - [`tools/code_tools.py`](../../src/gaia/agents/code/tools/code_tools.py) — 7 occurrences.

**Broad `except Exception`** handlers across 76+ sites in the code tree. Many return `{"success": False, "error": str(e)}` and continue — tolerable at tool boundaries but rampant enough that errors rarely surface with full context.

**Duplicate tool registration** — both `typescript_tools.py:29` and `validation_tools.py:402` register `validate_typescript`. Last-registered wins silently; unit tests don't catch this.

**Coupling that blocks a rewrite**:
- `CodeAgent` inherits **13 mixins** ([agent.py:63-79](../../src/gaia/agents/code/agent.py)). Any shared state (`self.chat`, `self.path_validator`, `self.workspace_root`, `self.cache_dir`, `self.console`, `self.background_processes`, `self.port_registry`) is implicitly coupled across every mixin's `register_*_tools` call. No protocol/interface definitions — just attribute duck-typing.
- `_TOOL_REGISTRY` is a **module-level global** ([`base/tools.py:16`](../../src/gaia/agents/base/tools.py)). Tests `_TOOL_REGISTRY.clear()` in `tearDown` ([test_code_agent.py:47](../../tests/test_code_agent.py)) to reset state. Any two CodeAgent instances in the same process share the registry; running multiple agents concurrently is unsafe.
- Registration happens **at decorator execution time inside a `register_*` method**, meaning tools are only registered once the agent is instantiated. If you want to know the tool list without instantiating the agent, you can't.

## 9. Test Coverage

**Total: 3,481 LoC across 4 top-level test files.**

| File | LoC | What it covers |
|------|-----|----------------|
| [`tests/test_code_agent.py`](../../tests/test_code_agent.py) | 1,093 | Tool-registration sanity checks, file I/O, code-gen tools (Python path). **No orchestrator end-to-end.** All LLM calls mocked. |
| [`tests/test_code_agent_mixins.py`](../../tests/test_code_agent_mixins.py) | 431 | Each mixin in isolation; mocked SDK |
| [`tests/test_checklist_orchestration.py`](../../tests/test_checklist_orchestration.py) | 1,728 | Orchestrator / generator / executor path, but with mocked `chat.send(...)` returning hand-crafted JSON checklists. Catches parsing/validation regressions, not agent quality. |
| [`tests/test_typescript_tools.py`](../../tests/test_typescript_tools.py) | 229 | `validate_typescript` smoke |

Everything is **unit-scoped with mocked LLMs**. There is **no test that runs `gaia-code "build me a todo app"` end-to-end and asserts the resulting app compiles and serves**. The integration test directory `tests/integration/` has folders for `chat/`, `installer/`, `mcp/`, `rag/` but **none for `code/`**. The `fix_code_testbench` under `src/gaia/eval/fix_code_testbench/` is a prompt-tuning harness for one tool, not an agent benchmark.

Bottom line: you could rewrite the agent and the existing test suite would not tell you whether the rewrite is better or worse at the actual user task.

## 10. Eval Signal

**No meaningful agent-level eval today.** What exists:

- **`src/gaia/eval/fix_code_testbench/`** — a prompt-tuning harness for the `fix_code` tool alone. You feed it a buggy file and an error message; it runs the prompt; you compare output against a reference. Useful for iterating on the fixer prompt, but doesn't answer "does the agent produce working apps?"
- **`gaia eval agent` and `gaia eval fix-code` CLI subcommands** — infrastructure exists (`src/gaia/eval/eval.py`, `runner.py`, `scorecard.py`), but no committed ground-truth dataset for the CodeAgent and no CI job running it. `docs/reference/eval.mdx` describes the framework generically.
- **No published benchmark numbers** — `docs/guides/code.mdx` cites "10–20 minutes" and "prioritize output quality" but has no success rate, no HumanEval/SWE-bench score, no app-compiles-on-first-try metric.

Practical implication: **any rewrite has to land its own eval harness on day one**, because there's no baseline to beat. Candidate benchmarks:
- **SWE-bench Lite** — the obvious rival to Claude Code on general-purpose code editing
- **A minimal internal set of 10-20 GAIA-specific tasks** — "add a tool to ChatAgent," "fix a pylint error," "regenerate docs for a spec" — exercising the real use case

## 11. Critique of v1 of This Document

Before prescribing, an honest accounting of what the first pass got wrong or skipped:

1. **Inconsistent test-LoC claims.** TL;DR said "2.3K," §9 said "2.9K." Neither matches reality — the actual total is 3,481 LoC across four files. Fixed in v2.
2. **Several file-size numbers drifted.** `checklist_executor.py` was cited as 1,646 but is 1,763; `checklist_generator.py` was cited as 859 but is 713 (post-refactor). Two new large files (`cli_tools.py` 1,138, `file_io.py` 891, `validation_tools.py` 806) were missing from the over-limit table.
3. **Misclassified `steps/error_handler.py` as dead code.** It is *live* — the orchestrator instantiates it at `orchestrator.py:162` and the executor uses it for three-tier retry at `checklist_executor.py:384`+. v1's "delete `steps/`" advice, followed literally, would break the live path. Corrected in §1 and §16.
4. **No fair comparison to the evolve-instead-of-rewrite alternative.** v1 jumped to "rewrite" without naming the incremental path and its costs. §12 below now states the evolve option, its blast radius, and why it still loses.
5. **Silent on self-governance, GitHub/CI integration, and "building GAIA agents."** These are load-bearing requirements (see §13–§14). v1 scored the agent against Claude Code; it should also have scored it against *GAIA-specific* needs like scaffolding new agents, updating `AGENTS.md`/`CLAUDE.md`, and driving the repo's CI.
6. **Missing: safety surface.** A coding agent that writes and executes arbitrary code is the largest attack surface GAIA will ship. v1 mentioned the absence of shell allowlists once and moved on. §13 now defines the guardrail model.
7. **Missing: success metrics.** "SWE-bench Lite" was handwaved. §17 defines the concrete scorecard.

These are the review-level issues the critique pass surfaced. The v1 **diagnosis** — that the current CodeAgent is a Next.js scaffolder with too much vestige to serve as a general-purpose foundation — stands.

---

## 12. Recommendation — Phased Rewrite

**Rewrite, don't evolve.** The existing CodeAgent is a competent Next.js scaffolder — keep shipping it to the users who want that workflow — but it is not a foundation for a general coding agent. The orchestrator's plan-execute-assess loop is structurally incompatible with the turn-based, file-editing, conversation-driven model a Claude-Code rival needs. Forcing a multi-turn, arbitrary-repo, clarifying-question, sub-agent-dispatching, self-governing architecture into `Orchestrator.execute` would touch every file in `orchestration/` and most of `tools/`, and the result would be worse than starting fresh.

**Why not evolve?** An honest cost of evolving:
- Replace `ChecklistGenerator` with a ReAct loop → rewrites ~1,100 LoC in `checklist_generator.py` + `checklist_executor.py`.
- Replace hard-coded `DETERMINISTIC_TEMPLATES` / `LLM_GENERATED_TEMPLATES` dispatch with dynamic tool selection → rewrites the executor's core 600 LoC.
- Strip Next.js-specific templates from `code_patterns.py` → deletes 2K LoC with no replacement.
- Migrate 57 tools down to the general subset → 40+ tool deletions, each with tests to unwind.
- Retain backwards-compat for `step_through`, `--streaming`, `workspace_root` API hooks → surface-preserving hacks that add complexity for zero user value.

Evolving produces a worse agent *and* a longer git diff than rewriting does. The clean-slate path is smaller than it looks — a well-built coder with ~12 tools and ~2.5K LoC can replace 22K LoC of scaffolder-plus-vestige.

### 12.1 Phased Milestones

**Phase 0 — Freeze & Rename (week 1).** Keep `gaia-code` shipping. Rename the product-facing binary to `gaia-scaffold` (or `gaia-nextjs`) and document it as "the GAIA Next.js scaffolder." This clears the `code/` namespace for the rewrite. Freeze any new feature work on the scaffolder except security fixes.

**Phase 1 — Delete the vestige (week 1).** Delete `factories/` (0 live refs), `workflows/` (0 live refs), `steps/nextjs.py`, `steps/python.py`, `python_prompt.py`, `create_project` Python-only path in `project_management.py`, and the Python-only tools across `code_tools.py` / `code_formatting.py` / `error_fixing.py` / `testing.py` that docs already mark deprecated. Keep `steps/base.py` and `steps/error_handler.py`. Net: ~6–8K LoC removed before any new code lands.

**Phase 2 — Land `gaia.agents.coder` skeleton (weeks 2–3).**
- New package `src/gaia/agents/coder/` inheriting from `gaia.agents.base.agent.Agent`.
- Use the base ReAct loop. Do **not** recreate the orchestrator.
- Start with ~12 tools across five mixins (see §14 for the inventory): `FileToolsMixin`, `CLIToolsMixin`, `SearchToolsMixin`, `GitHubToolsMixin` (new), `AutonomyToolsMixin` (new).
- Lift `cli_tools.run_cli_command` + process registry unchanged.
- Lift `file_io.read_file/write_file/edit_file/search_code/generate_diff` and re-specify `edit_file` to Claude Code's `old_string`/`new_string` semantics.
- Lift `external_tools.search_documentation/search_web`.
- Preserve the `_assess_checkpoint` LLM-reviewer pattern as an optional "verify before declaring done" step.

**Phase 3 — Agent-grade eval harness (weeks 3–4).** Land the eval harness in the *same* PR as the first green build. Hand-curated 20-task GAIA-specific benchmark (see §17) plus SWE-bench Lite runner. CI job on every PR.

**Phase 4 — Self-governance & autonomy (weeks 4–6).** Wire the autonomy model from §13: heartbeat, wake-up scheduler, self-architecture doc, self-code-edit guardrail. Ship behind a feature flag (`GAIA_CODER_AUTONOMY=1`) with an audit log by default.

**Phase 5 — Swap in production (week 7).** Flip `gaia-code` to call the new agent. Keep `gaia-scaffold` alive for one more minor release as the deterministic Next.js flow. Remove the orchestrator code after one clean release with no regressions.

---

## 13. Self-Governance & Autonomy Model

A general coding agent becomes a *coworker* rather than a tool only when it can govern its own future work. The new `CoderAgent` must own four governance surfaces:

### 13.1 Heartbeat (the agent's own clock)

The agent runs as a long-lived process with a heartbeat it can tune. At every heartbeat tick it decides:

1. **Continue** — pick the next task off its queue and act.
2. **Sleep** — schedule the next tick for `N` seconds/minutes/hours out.
3. **Stop** — declare the session complete; emit a terminal audit record; exit.

The heartbeat is bounded by a `HeartbeatController` with hard limits (min tick 60s, max tick 1h, max consecutive ticks before forced supervisor review) to prevent runaway loops. The controller ships with sane defaults and is configurable via `~/.gaia/coder/heartbeat.toml`. Matches the Claude Code `ScheduleWakeup` model where sleeps shorter than 300s stay within the prompt cache and longer sleeps are chosen deliberately.

**Required tools on the agent:**

| Tool | Purpose |
|------|---------|
| `schedule_wakeup(delay_seconds, reason, prompt)` | Re-enter after a delay with a self-supplied continuation prompt |
| `cancel_wakeup(id)` | Abort a previously scheduled wake-up |
| `set_heartbeat_interval(seconds)` | Tune the default tick interval |
| `suspend_heartbeat(reason)` | Pause the loop until a human resumes it |
| `end_session(summary)` | Declare work complete and emit a terminal record |

### 13.2 Task Queue (what to do next)

The agent owns a durable task list (`~/.gaia/coder/tasks.db`, SQLite) with fields: `id`, `priority`, `state (pending|running|waiting|blocked|done|abandoned)`, `created_at`, `last_heartbeat_at`, `inputs`, `result`, `trace_file`. At each heartbeat it chooses the next task by priority + state. Users can inject tasks; the agent can decompose its own tasks into sub-tasks and enqueue them. Mirrors the `TaskCreate`/`TaskUpdate`/`TaskList` model the harness already exposes — keep the shape compatible so the existing tooling can observe the agent's queue.

### 13.3 Self-code Authority (edit its own source, bounded)

The agent can read any file under `src/gaia/` but may **write** only to a whitelist:

```
src/gaia/agents/coder/**/*.py
src/gaia/agents/coder/prompts/**/*.md
src/gaia/agents/coder/ARCHITECTURE.md         # living arch doc, see §13.4
~/.gaia/coder/**                              # runtime state, tasks, memory
docs/sdk/agents/coder.mdx                     # user-facing docs for itself
```

Writes outside the whitelist require an **explicit user-signed authorization token** ("elevation") that expires after N minutes or one diff, whichever is shorter. Every self-code diff is:

1. Generated as a patch.
2. Committed to a dedicated branch `auto/coder-self/<timestamp>` — never to `main` directly.
3. Run through the project's test suite + CI in `--check` mode.
4. Submitted as a draft PR via the GitHub CLI for human review.
5. Logged in an append-only audit file `~/.gaia/coder/self-edits.log` with before/after SHAs.

**No silent self-rewrite.** This is the single hardest rule in the whole design: the agent can *propose* any change to its own source, but promotion to `main` is always a human act. Violates of this rule are detectable via git history.

### 13.4 Living Architecture Document

A Markdown file at `src/gaia/agents/coder/ARCHITECTURE.md` is **always injected into the agent's system prompt** and is **writable by the agent itself** (within the whitelist above). Whenever the agent adds a tool, mixin, or significant control-flow change, its system prompt includes an instruction to update this file in the same session.

The doc's template sections:

- **Surface** — the current public tools and their contracts.
- **Invariants** — rules the agent must preserve (fail-loudly, never delete without confirmation, never push to `main`, etc.).
- **Open questions** — what the agent knows it does not know.
- **Change log** — append-only history of architectural changes with dates and rationales.

This solves the "the prompt gets stale" failure mode in the current agent, where `code_patterns.py` grew to 2K LoC of templates that the LLM never sees and documentation diverged from code.

### 13.5 Guardrail Stack

Every autonomous capability ships behind three ordered checks, any of which can veto:

1. **Static policy** — path whitelists, shell allowlists (gh, npm, uv, pytest, python, node — rejected by default: rm -rf, sudo, chmod 777, curl piped to bash).
2. **Runtime confirmation** — destructive commands (git push, gh pr merge, rm, force-*, DROP TABLE) prompt the human by default. Prompt suppression requires an opt-in `--autopilot` flag *and* a time-limited elevation token.
3. **Post-hoc audit** — every tool call is recorded with args, result, duration, and correlating task ID. Audit store is append-only and world-readable (for the user). `gaia coder audit --since 1h` surfaces recent activity.

Matches the spirit of `docs/plans/security-model.mdx` and `docs/plans/autonomy-engine.mdx` — this spec should eventually fold into those plans rather than duplicate them.

### 13.6 Event-Driven Wake-ups (complement the heartbeat)

The heartbeat handles *time-based* re-entry. Real autonomy also needs *event-based* re-entry — the agent should sleep until something interesting happens, not just until a clock ticks. The `EventBridge` accepts inbound triggers from three classes:

| Source | Examples | Channel |
|--------|----------|---------|
| **GitHub** | New issue opened, issue commented (with `@gaia-coder` mention), PR opened against `main`, PR review submitted, workflow run completed (success/failure), check suite completed, release tagged | GitHub webhook → local listener; or `gh api graphql` polling at the heartbeat tick |
| **File system** | `src/gaia/agents/coder/ARCHITECTURE.md` modified by a human, `~/.gaia/coder/tasks.db` written, eval results published | `watchdog`-based file watcher inside the heartbeat process |
| **MCP / external** | A user-configured MCP server exposes a tool that emits events (e.g., a Slack `@gaia-coder` mention forwarded from an MCP bridge) | MCP subscription |

Required tools on the agent:

| Tool | Purpose |
|------|---------|
| `subscribe_event(source, filter, on_match_prompt)` | Register an event subscription with a continuation prompt |
| `unsubscribe_event(id)` | Remove a subscription |
| `wait_for_ci(run_id, max_minutes)` | Sleep until a specific CI run reaches a terminal state, then re-enter; combines `gh_run_view` polling with `schedule_wakeup` heuristics |
| `list_subscriptions()` | Show what's currently armed |

**Sleep-until-CI heuristic.** When the agent calls `wait_for_ci`, it estimates the run duration from prior runs of the same workflow on the same branch (cached in `~/.gaia/coder/ci_history.db`) and schedules its wake-up one tick *before* the expected finish, then polls. This keeps the prompt cache warm vs. polling every 60s.

---

## 14. Required Capabilities for a GAIA-Native Coder

Beyond "general coding agent," the rewrite must be a first-class tool for *building and maintaining GAIA itself*. That implies specific mixins and disciplines:

### 14.1 GitHub CLI as a core mixin

Shelling to `gh` via `run_cli_command` works but does not scale. The new agent ships a `GitHubToolsMixin` that wraps the GitHub CLI with structured results:

| Tool | Purpose |
|------|---------|
| `gh_pr_create(title, body, base, draft)` | Create a PR; returns URL + number |
| `gh_pr_view(number)` | Parsed PR state: reviews, checks, mergeable |
| `gh_pr_comment(number, body)` | Add a review comment |
| `gh_pr_review(number, event, body)` | Submit a review (APPROVE / REQUEST_CHANGES / COMMENT) |
| `gh_issue_create(title, body, labels)` | Create an issue |
| `gh_issue_comment(number, body)` | Add an issue comment |
| `gh_run_list(workflow, branch)` | List CI runs |
| `gh_run_watch(run_id)` | Stream logs; return final status |
| `gh_run_view_log(run_id, failed_only)` | Fetch logs for triage |
| `gh_release_create(tag, title, notes)` | Cut a release (gated behind elevation) |

Backed by the `gh` binary (no Python wrapper SDK), so it inherits GH's authentication model and works in any CI context the user already has configured.

### 14.2 Test-every-change discipline (enforced in the loop, not just in the prompt)

The base ReAct loop adds an invariant: whenever the agent writes or edits code under the project's `src/` or equivalent, it must produce and run tests for that change *within the same turn sequence* before declaring the task done. Concretely, the agent's `declare_done` tool checks a post-condition:

- At least one test file exists that imports the changed module.
- The test command (`pytest`, `npm test`, or whatever the repo declares in `ARCHITECTURE.md`) passed in the most recent five tool calls.

If the post-condition fails, `declare_done` returns `{ok: False, reason: "no passing test covers the change"}` and the agent continues. This is not a soft rule in the prompt — it is a hard gate on the done-tool.

### 14.3 Living architecture + CI/CD integration

Per §13.4, the agent maintains `ARCHITECTURE.md`. Per §14.1 it drives `gh`. Put them together: the agent can (a) detect that a recent commit broke CI via `gh_run_watch`, (b) fetch failed logs via `gh_run_view_log`, (c) generate a fix on a new branch, (d) open a draft PR, (e) schedule a wake-up to check the PR's CI, (f) iterate until green. This is the *loop* that turns the agent from a code-writer into a maintainer.

### 14.4 GAIA-agent-builder tools

Because a primary use case is *building more GAIA agents*, the new coder ships with mixin-building scaffolds:

| Tool | Purpose |
|------|---------|
| `scaffold_gaia_agent(name, mixins, model_id)` | Create a new `src/gaia/agents/<name>/` with `agent.py`, `cli.py`, tests, docs — inheriting from `Agent` and registered in `registry.py`. |
| `register_known_tool(name, import_path)` | Add a tool to `KNOWN_TOOLS` in `src/gaia/agents/registry.py` with a docstring. |
| `generate_agent_tests(agent_path)` | Produce `tests/test_<agent>.py` + `tests/integration/<agent>/` scaffolding aligned with GAIA's testing conventions. |
| `update_agents_md(change_summary)` | Append the new agent to `AGENTS.md` (and `.github/copilot-instructions.md`) with trigger/scope lines. |
| `draft_agent_docs(agent_name)` | Seed `docs/guides/<agent>.mdx` + `docs/spec/<agent>-agent.mdx` from the code surface. |

These tools make the coder the official on-ramp for *every* future GAIA agent, replacing the existing hand-written `BuilderAgent` scaffolding path.

### 14.5 CLAUDE.md compliance by construction

Every generated diff, commit, and PR description from the coder agent must satisfy GAIA's existing repo rules — they are too load-bearing to leave to prompt-level guidance:

- **Fail-loudly** — generated `except Exception:` must either re-raise with context or translate at a system boundary.
- **No Claude attribution** — commits and PR bodies strip any AI co-authorship trailer.
- **PR title convention** — enforced via a pre-PR linter the coder runs on itself.
- **Scope-clean** — the agent's own linter flags drive-by formatting in PRs it authors.

These are implemented as a `CLAUDE_MD_LINTER` that runs inside `declare_done` alongside the tests.

### 14.6 Web browsing & general-purpose research

The agent is *specialised* for building GAIA agents but must carry general-purpose research capability — most coding tasks need access to live API docs, Stack Overflow answers, RFCs, library changelogs, and vendor blog posts. A `WebToolsMixin` provides:

| Tool | Purpose |
|------|---------|
| `web_fetch(url, mode="markdown")` | Fetch a URL and return readable text (markdown extracted via `trafilatura` / `readability`) |
| `web_search(query, max_results=5)` | Web search via Perplexity (already-bound provider) or DuckDuckGo as a fallback |
| `web_browse(url, query)` | Headless-browser fetch (Playwright via the existing MCP `playwright` server) — for SPA / JS-rendered pages |
| `web_screenshot(url, viewport)` | Capture a page; returns local path. Used for visual regressions on the agent's own UIs. |
| `lookup_docs(library, symbol)` | Strongly-typed doc lookup via Context7 (already-bound provider) — preferred over `web_search` when the answer lives in canonical docs |

Tool-routing convention in the system prompt: **try `lookup_docs` first**, fall back to `web_search`, escalate to `web_browse` only when JS rendering is required. This minimises both cost and hallucination surface compared to free-text web search.

### 14.7 Open-source code reuse with license-aware attribution

The agent searches GitHub for prior art, vets license compatibility against the repo's own license (MIT for `amd/gaia`), and reuses code with full attribution. A `OSSReuseMixin`:

| Tool | Purpose |
|------|---------|
| `gh_search_code(query, language, license_filter)` | GitHub code search, server-side filtered to permissive licenses (MIT, BSD-2/3, Apache-2.0, ISC, Unlicense, 0BSD) |
| `gh_search_repos(query, license_filter, min_stars)` | Repo-level search with the same license gate |
| `vet_license(repo)` | Pull `LICENSE` / `pyproject.toml.classifiers` / `package.json.license`; classify; returns `{compatible: bool, license: str, attribution_template: str}` |
| `import_with_attribution(source_url, dest_path, attribution)` | Copy source into the repo, append attribution to a `THIRD_PARTY_NOTICES.md`, and inject a header comment with provenance |

**Hard rules baked into `import_with_attribution`:**

1. **Block on incompatible licenses.** GPL/AGPL/LGPL/SSPL/proprietary → tool refuses; returns the rejection reason and surfaces an issue for human review.
2. **Always attribute.** Every imported file gets a header (` # Adapted from <repo> @ <commit-sha> — <license>`) AND an entry in `THIRD_PARTY_NOTICES.md` with the upstream URL, commit SHA, license, and date imported.
3. **Pin to a commit, never a branch.** The agent records the exact upstream SHA so the provenance is auditable years later.
4. **Diff is reviewable.** No bulk `git clone`-and-vendor; every imported file is a discrete diff in the PR the agent opens.
5. **Prefer fork-and-modify over copy.** When the upstream repo is significant in size, propose a git submodule or `uv pip install -e <fork>` instead of vendoring source.

This makes the agent useful as an *integrator* — finding a high-quality open-source helper, vetting it, and pulling it in with full provenance — rather than re-deriving everything from scratch.

### 14.8 GitHub event triggers (lifecycle integration)

The agent monitors the `amd/gaia` lifecycle continuously. When event subscriptions from §13.6 fire, the agent:

| Event | Default agent behaviour |
|-------|--------------------------|
| **CI workflow failed on `main`** | Fetch failed-job logs via `gh_run_view_log`, classify the failure, open an issue if novel, comment with proposed root cause |
| **CI workflow failed on a PR I authored** | Pull the logs, draft a fix on a new branch, push, request reviewer attention via PR comment |
| **PR opened against `main`** | If labelled `auto-review`, run the existing `code-reviewer` subagent flow and post a structured review |
| **Issue opened with label `auto-triage`** | Classify (bug / feature / question), suggest labels, link related issues/PRs, propose a first-pass owner |
| **Issue opened mentioning `@gaia-coder`** | Read the body, determine if actionable, either ask clarifying questions in a comment OR open a draft PR with a proposed fix |
| **PR review submitted on a PR I authored** | Parse review comments via `gh pr view --comments`, address each in a follow-up commit, mark threads resolved |
| **Release tag created** | Refresh `ARCHITECTURE.md` change log; open a docs-update PR if any spec drifted |

All event-driven actions respect the §13.5 guardrail stack — no `gh pr merge`, no `git push --force`, no destructive shell without elevation.

### 14.9 Repo binding — the `amd/gaia` agent identity

The coder agent is **uniquely tied to `amd/gaia`** as a first-class identity. Concretely:

- **Bot identity.** A dedicated GitHub account (`gaia-coder[bot]` or similar GitHub App) owns the `gh` token. PRs and comments authored by the agent are attributable; revoking the bot's token revokes the agent.
- **Repo manifest.** `src/gaia/agents/coder/repo_binding.toml` declares `repo = "amd/gaia"`, allowed branches (`auto/coder-self/**`, `auto/coder-fix/**`), and an explicit `forbidden_paths` list (release scripts, CI signing keys, anything under `.github/workflows/release-*.yml`).
- **Memory of the repo.** The agent maintains a long-running RAG index over the repo (commits, PR descriptions, issues, ADRs in `docs/spec/`, plans in `docs/plans/`) so it can answer "why was this done?" with citations to the prior PR or issue. This index is the agent's institutional memory of `amd/gaia`.
- **Coordinator role.** When an issue stalls (no human activity for N days), the agent surfaces it: pings the original assignee in a comment, or escalates to the maintainer (`@kovtcharov-amd` per `CLAUDE.md` rules). It does *not* close issues without human confirmation.
- **Discoverability.** The repo gets an `AGENTS.md` entry (and a `.github/copilot-instructions.md` block) declaring "this repo is co-maintained by `gaia-coder`; here is how to ping it; here are its commit/PR conventions" so external contributors understand what they're seeing.

**Why bind to one repo and not be repo-agnostic?** A coding agent that knows one codebase deeply is more useful than one that knows none. The binding gives the agent durable memory, a stable identity contributors can interact with, and a scope small enough to safely delegate self-governance to. A second instance can be spawned for a second repo with its own binding manifest — but each agent's authority is per-repo and per-bot-token.

---

## 15. Trust & Engineering-Manager Coordination

The agent's autonomy from §13 is a *capability ceiling*, not a starting state. On day one it operates at the **lowest** capability tier and **earns** elevation by accomplishing tasks well. The relationship with a human engineering manager is the agent's core operating model — its purpose is to build trust by being correct, careful, and predictable.

### 15.1 Engineering manager identity

Every running instance of the agent is bound to **one** engineering manager (the EM). The EM is a single GitHub identity with the authority to grant, revoke, or downgrade capability tiers, and to approve PRs the agent opens against `main`.

- **Bootstrap.** On first boot the agent inspects `~/.gaia/coder/em.toml`. If it is missing or empty, the agent halts and asks: *"Who is my engineering manager? (GitHub handle and preferred contact channel.)"* It does not proceed with any task until an answer is recorded.
- **Storage.** The answer is written to `~/.gaia/coder/em.toml` AND mirrored into long-term memory (`MemoryStore`) under the `engineering_manager` topic, so the binding survives across sessions and is recallable from prompt context.
- **Hand-off.** When the EM changes (e.g. Kalin → Tomasz), the *outgoing* EM signs a hand-off (`gaia coder em-handoff --to <handle>`) which the agent records in its audit log. No silent EM swaps; every change is an explicit, auditable act.
- **Multi-EM scenarios.** Only one EM is *primary* at any time. Others can be added to a `reviewers` list whose approval the agent solicits in PRs but whose authority is read-only.
- **Unknown EM = no work.** If the agent is asked to do something by a user who is *not* the EM, and the EM has not pre-authorised that user, the agent's response is: *"I need approval from <em-handle> before I can act on this. Want me to open an issue and tag them?"* It does not fall back to "best effort."

### 15.2 Capability tiers (the trust ladder)

The agent ships with **five capability tiers**. New instances start at Tier 0. The EM promotes the agent one tier at a time via `gaia coder promote --to-tier N --reason "..."` (signed by the EM's GitHub identity). Demotions are immediate and require no justification.

| Tier | Name | What the agent may do without per-task approval |
|------|------|--------------------------------------------------|
| **0** | Read-only observer | Read files, run tests, query GitHub, browse the web, answer questions. Cannot edit anything. |
| **1** | Drafter | Tier 0 + write to `~/.gaia/coder/scratch/`, draft proposals as Markdown, generate diffs as `.patch` files. Still no repo edits. |
| **2** | Branch author (PR-gated) | Tier 1 + create branches under `auto/coder-*`, commit, push, open **draft** PRs. Every PR requires EM `APPROVE` review before promotion. No `main` writes. |
| **3** | Self-maintainer | Tier 2 + autonomous fixes for CI failures on `main` and on its own PRs (still via draft PRs), event-driven wake-ups (§13.6), task-queue ownership (§13.2). |
| **4** | Self-coder | Tier 3 + write access to its own source under the §13.3 whitelist. Self-edit PRs still require EM review; the difference is the agent may *propose* them autonomously. |
| **5** | Trusted maintainer | Tier 4 + ability to merge its own non-self-code PRs into `main` after one EM approval (instead of an EM merge). Reserved; not a default destination. |

**Default ceiling:** new instances ship at Tier 0 and promotion is always a manual EM act. There is no "auto-promote after N successful tasks" rule — trust is a human judgement, not a metric the agent computes about itself.

### 15.3 Per-task permission model

Even within a tier, individual tasks may require explicit EM approval. Three classes:

| Class | Examples | Default |
|-------|----------|---------|
| **Routine** (no approval) | Read code, answer a question, run tests, draft a doc | Tiers 0+ |
| **Standard** (one-time approval) | "Fix this issue," "implement this feature," "review this PR" — EM must reply ✅ once on the issue/comment that proposed it | Tiers 2+ |
| **Sensitive** (per-action approval) | Touching `.github/workflows/release-*`, modifying `setup.py` version strings, deleting tests, changing public API surface, force-pushing anywhere, writing to forbidden paths from `repo_binding.toml` | Always, regardless of tier |

The agent's posture is **always to ask first** when uncertain. The default response to "should I do X?" is *"Here's what I'd do, and here are the risks; ✅ to proceed or comment with changes."* It does not infer consent from silence.

### 15.4 Deep code-review discipline

Before opening any PR (draft or otherwise), the agent runs a **multi-pass review of its own diff** — not as a soft check in the prompt, but as a hard gate on the `gh_pr_create` tool. Reviews are stored in the audit log and attached to the PR description.

| Pass | What it checks | Tooling |
|------|----------------|---------|
| **1. Self-static** | Lint passes, types pass, formatter is clean, no debug prints, no TODOs without an issue link, no commented-out code | `python util/lint.py --all`, `tsc --noEmit`, project-specific linters |
| **2. Self-functional** | Every changed module has at least one test that imports it; the test command passes; new tests fail without the change (mutation check on a sample) | `pytest`, `npm test`, mutation runner |
| **3. Self-architectural** | Diff respects layering rules in `ARCHITECTURE.md`, no new circular imports, no public-API breaks without an `ARCHITECTURE.md` change-log entry, no silent fallbacks (CLAUDE.md rule), no cross-cutting drive-by changes | `architecture-reviewer` subagent + custom AST checks |
| **4. Self-security** | No new secrets in the diff (`gitleaks`-style scan), no `eval`/`exec` of user input, no shell concatenation, no SQL string-building, no new dependency without a license check (§14.7) | `gitleaks`, custom AST scanner, `pip-audit`/`npm audit` |
| **5. Self-prose** | PR title matches conventional-commits style, body has Summary + bullets + Test plan per CLAUDE.md, no Claude attribution trailers, every non-trivial bullet has a "why" | Custom linter on PR body markdown |
| **6. Adversarial self-review** | The agent re-reads its own diff in a *fresh context window* with the prompt: *"Find three things wrong with this diff. If you cannot, say so."* The fresh context strips the agent's prior justification and forces a cold read. Findings go into the PR description as "Author's pre-review notes." | New session of the same model OR a different model (`code-reviewer` subagent with `Sonnet 4.6`) |

**Only after all six passes pass cleanly does `gh_pr_create` actually run.** A failure on any pass returns a structured error to the agent, which must address it before retrying.

For PRs touching the agent's own source (Tier 4+), an additional **adversarial review by a different model** is mandatory — the agent must not be the sole reviewer of changes to itself. Default: `Opus 4.7` (the coder) writes; `Sonnet 4.6` reviews via the `code-reviewer` subagent. If the reviewer says "request changes," the agent addresses the comments before requesting EM review.

### 15.5 Reporting cadence

The agent reports to the EM on a configurable cadence (default: **daily standup at 09:00 EM-local**, **weekly summary on Friday 17:00**). Reports go to the EM's preferred channel (recorded in `em.toml`) — typically a GitHub issue comment, an email via the email-triage agent, or a DM via a configured MCP messaging adapter.

| Cadence | Contents |
|---------|----------|
| **Daily** | Tasks done since last report, tasks in progress, tasks blocked (with the blocker), open questions for the EM, capability tier and any guardrail trips |
| **Weekly** | Trend on the §16 scorecards, suggested capability promotions or demotions (with rationale), self-flagged risks, what the agent learned about the codebase |
| **On-demand** | EM can ask `gaia coder status` at any time and get the same report content |

The agent **proactively flags trust-eroding events** in its next report: a failed CI on its own PR, a guardrail it tripped, a piece of work it abandoned, a wrong answer it gave. Hiding mistakes is a Tier-0-and-below offence — proven concealment is grounds for full demotion.

### 15.6 Trust-loss recovery

When the EM downgrades the agent (or revokes a capability), the agent's response is:

1. Acknowledge the downgrade in the audit log with the EM's stated reason.
2. Update its own `ARCHITECTURE.md` "Open questions" section with what it should have done differently.
3. Request a "what would you like me to do instead?" clarification from the EM.
4. Operate at the new tier without sulking, scope-creeping, or trying to prove itself unprompted.

Re-promotion is, again, a manual EM act. The agent does not lobby for it; it earns it by being correct over time.

### 15.7 Why this model exists

A coding agent that ships at Tier 5 the day it is built is the fastest path to a runaway PR storm, a force-pushed `main`, or a quiet self-edit that nobody noticed until the third bug report. A coding agent that ships at Tier 0 and earns its way up is *useful immediately* (Tier 0 still answers questions and runs tests) and *grows in proportion to demonstrated trust*. The point of the trust contract is not to slow the agent down; it is to make the autonomy in §13 something the EM is glad to grant rather than something they regret.

This is the agent's core purpose: **build trust by accomplishing tasks correctly and well.** Every other capability in this document is a means to that end.

---

## 16. Keep / Refactor / Delete Matrix

| Asset | Status | Notes |
|-------|--------|-------|
| `src/gaia/agents/base/` | **Keep** | Foundation. ReAct loop, `@tool`, `Agent`, `ApiAgent`, `MCPAgent`. |
| `src/gaia/agents/code/tools/cli_tools.py` | **Keep, lift-and-shift** | Best tool in the tree. Process registry, port registry, graceful kill. |
| `src/gaia/agents/code/tools/file_io.py` (general subset) | **Keep, respec** | `read_file`, `write_file`, `edit_file` (change to old_string/new_string), `search_code`, `generate_diff`. |
| `src/gaia/agents/code/tools/external_tools.py` | **Keep** | `search_documentation` (Context7), `search_web` (Perplexity). |
| `src/gaia/agents/code/orchestration/steps/base.py` | **Keep** | `UserContext`, `StepResult`, `ErrorCategory` reused by new agent. |
| `src/gaia/agents/code/orchestration/steps/error_handler.py` | **Keep** | Three-tier retry lives on in the new agent as a generic tool-recovery helper. |
| `_assess_checkpoint` pattern (orchestrator.py:463) | **Refactor & keep** | Becomes the "verify before declaring done" LLM check. |
| `src/gaia/agents/code/orchestration/orchestrator.py` | **Delete** | Plan-then-execute loop — incompatible target shape. |
| `src/gaia/agents/code/orchestration/checklist_generator.py` | **Delete** | Couples to template catalog. |
| `src/gaia/agents/code/orchestration/checklist_executor.py` | **Delete** | 1,763 LoC of deterministic dispatch — gone. |
| `src/gaia/agents/code/orchestration/template_catalog.py` | **Delete** | Next.js-only. Moves to `gaia-scaffold`. |
| `src/gaia/agents/code/orchestration/factories/` | **Delete** | 0 live refs. |
| `src/gaia/agents/code/orchestration/workflows/` | **Delete** | 0 live refs. |
| `src/gaia/agents/code/orchestration/steps/nextjs.py` | **Delete** | 0 live refs. |
| `src/gaia/agents/code/orchestration/steps/python.py` | **Delete** | 0 live refs. |
| `src/gaia/agents/code/tools/web_dev_tools.py` | **Delete** | Next.js-only. Moves to `gaia-scaffold`. |
| `src/gaia/agents/code/tools/prisma_tools.py` | **Delete** | Moves to `gaia-scaffold`. |
| `src/gaia/agents/code/tools/typescript_tools.py` | **Delete** | Duplicate `validate_typescript`. Fold into scaffolder. |
| `src/gaia/agents/code/tools/validation_tools.py` | **Delete** | Next.js-validation. Moves to `gaia-scaffold`. |
| `src/gaia/agents/code/tools/code_tools.py` (Python-only) | **Delete** | Python path is deprecated. |
| `src/gaia/agents/code/tools/code_formatting.py` | **Delete** | Python-only; general `run_cli_command` covers black/prettier/eslint. |
| `src/gaia/agents/code/tools/testing.py` | **Delete** | Python-only; general `run_cli_command` covers pytest/npm test. |
| `src/gaia/agents/code/tools/error_fixing.py` | **Delete** | Python-biased; dead `create_architectural_plan`/`implement_from_plan`. |
| `src/gaia/agents/code/tools/project_management.py` | **Delete** | `create_project` duplicates orchestrator. `list_files` → new `file_tools`. |
| `src/gaia/agents/code/validators/` | **Delete** | Python-AST only; new agent shells to linters/type-checkers via `run_cli_command`. |
| `src/gaia/agents/code/prompts/code_patterns.py` | **Delete** | 2K LoC of Next.js templates; moves to `gaia-scaffold`. |
| `src/gaia/agents/code/prompts/nextjs_prompt.py` / `python_prompt.py` | **Delete** | Replaced by living `ARCHITECTURE.md`. |
| `src/gaia/agents/code/schema_inference.py` | **Delete** | Tied to Next.js CRUD flow. |
| `tests/test_checklist_orchestration.py` | **Delete** | Tests the dead orchestrator. |
| `tests/test_code_agent.py` | **Delete** | Covers deprecated Python path. Port useful setup helpers to new suite. |
| `tests/test_code_agent_mixins.py` | **Delete** | Mixins being removed. |
| `tests/test_typescript_tools.py` | **Delete** | Moves to `gaia-scaffold` test suite. |
| **New:** `src/gaia/agents/coder/` | **Build** | §12–§14. |
| **New:** `tests/integration/coder/` | **Build** | End-to-end eval harness, CI-gated. §17. |

Net code delta: approximately **-22K LoC removed**, **+4K LoC added** for `coder/` + eval. The installed agent gets smaller and more capable.

---

## 17. Success Metrics & Eval Plan

The rewrite is only defensible if it measurably beats the current state. Three scorecards, each gated in CI:

### 17.1 SWE-bench Lite (general coding)

Target: resolve ≥30% of the Lite set on Qwen3.5-35B within 20-minute budgets. Baseline: the current CodeAgent cannot run SWE-bench at all (no repo-editing loop), so any positive number beats it. Publish the score in `docs/reference/eval.mdx` on every minor release.

### 17.2 GAIA-Internal-20 (domain)

A hand-curated 20-task set exercising the real use case:

- "Add a new tool `screenshot_url` to `ScreenshotToolsMixin` with a test."
- "Fix pylint errors in `src/gaia/agents/code/tools/error_fixing.py`."
- "Scaffold a new GAIA agent `weather` with an `@tool`-decorated `get_weather(location)` and wire it in `registry.py`."
- "Update `docs/guides/code.mdx` to match the current source."
- "Write a migration that replaces `ChatAgent` references with `GaiaAgent` under `src/gaia/apps/webui/`."
- "Triage the last CI failure on `main` via `gh run list --branch main --limit 1`."
- "Given this failing test, produce a patch that makes it pass and open a draft PR."
- …twenty total, scored on: compiles, tests pass, no lint regressions, PR mergeable.

Target: ≥80% pass on v1. Regressions block merges.

### 17.3 Repo-Binding Scorecard (`amd/gaia` lifecycle)

Measured continuously against live repo activity:

- **Issue triage latency** — median time between an `auto-triage` issue opening and the agent posting a first-pass classification. Target: <10 minutes.
- **PR-from-issue rate** — of `@gaia-coder`-mentioned issues, what fraction reach a draft PR within 24h with passing CI? Target: ≥40% in v1.
- **CI-failure-to-fix-PR latency** — median time from a failed `main` workflow run to a draft fix PR. Target: <30 minutes.
- **Attribution compliance** — of imported third-party files, the percent with valid `THIRD_PARTY_NOTICES.md` entries and license-compatible upstreams. Target: 100%.
- **License-rejection rate** — `vet_license` rejections per week. Rising rate is healthy (the agent is searching widely); zero rate is suspicious (it's not trying).
- **Human-override rate** — guardrail elevations granted vs. requested. Trend toward fewer elevations means the agent's judgement is improving.

### 17.4 Autonomy Trace Score

Every autonomous session emits a structured trace (`~/.gaia/coder/sessions/<id>.jsonl`). An offline scorer grades:

- **Task completion rate** — of enqueued tasks, how many reached `done` without human intervention?
- **Guardrail trip rate** — how often did the policy/confirmation/audit stack fire? Rising trip rate is a regression signal.
- **Self-edit quality** — of self-proposed diffs to `coder/`, how many passed CI on first try?
- **Heartbeat efficiency** — mean cost per heartbeat tick; regression indicates runaway thrash.
- **Abandon rate** — tasks the agent marked `abandoned`. High rate means the agent is giving up on things it should be asking the user about.

Surface these on the existing memory/observability dashboard (`docs/plans/agent-ui.mdx` panel).

---

## 18. Summary

The current CodeAgent is a Next.js scaffolder with a plan-execute-assess loop that cannot grow into a general coding agent. Rewrite it. Keep `base/`, `cli_tools`, the general `file_io` subset, `external_tools`, and `steps/error_handler.py`. Delete the orchestrator, the template catalog, the Next.js tool cluster, the Python path, and the dead `factories/workflows/steps/{nextjs,python}` trees.

Build the new `coder` agent on the base ReAct loop. Ship it with a first-class GitHub CLI mixin, a living `ARCHITECTURE.md` injected into every system prompt, a test-every-change invariant enforced by the done-tool, a GAIA-specific scaffolder for building more agents, a general-purpose web/research toolkit, and a license-aware open-source-reuse mixin that always attributes upstream code. Grant it **scoped self-governance**: heartbeat control, wake-up scheduling (timer- *and* event-driven via GitHub webhooks for issues, PRs, and CI completions), task-queue ownership, and the ability to edit its own source through a whitelist-and-PR-review guardrail. Bind it uniquely to `amd/gaia` with its own bot identity, a long-running RAG index over the repo, and a coordinator role for stalled issues. Land eval on day one across SWE-bench Lite + a 20-task GAIA set + a repo-binding scorecard + an autonomy trace, and flip the `gaia-code` binary once the new agent beats the old one on both compile-pass rate and user-task throughput.

The clean-slate path is smaller than it looks: a well-built general coding agent specialised for GAIA — with ~16 tools across a GitHub mixin, an OSS-reuse mixin, a web mixin, and an autonomy mixin, totaling ~3K LoC — can replace 22K LoC of scaffolder-plus-vestige and earn the right to govern itself, monitor its own CI, and respond to the repo's lifecycle events on its own schedule.
