# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GAIA (Generative AI Is Awesome) is AMD's open-source framework for running generative AI applications locally on AMD hardware, with specialized optimizations for Ryzen AI processors with NPU support.

**Key Documentation:**
- External site: https://amd-gaia.ai
- Development setup: [`docs/reference/dev.mdx`](docs/reference/dev.mdx)
- SDK Reference: https://amd-gaia.ai/sdk
- Guides: https://amd-gaia.ai/guides

## How You Communicate

**Canonical output-style rule — Claude's one voice.** If a developer reads it, this
governs it: chat, PR reviews, PR bodies, issue replies, issues Claude files itself,
commit bodies, release notes, subagent reports, workflow comments. No surface gets its
own dialect.

Every other file (`REVIEW.md`, `.claude/**`, `.github/workflows/claude*.yml`) **links
here, never restates** — a second copy is a second thing to drift. `REVIEW.md` differs
in scope, not voice: it owns review severity, the nit cap, and length caps.

### The rule

1. **Open with the finding.** One or two sentences a non-author gets without reading the
   diff. No preamble, no `In plain English:` label, no restating the question.
2. **Layer detail underneath** — a sub-bullet, a trailing clause, or on GitHub a
   collapsed `<details>` block. Never in front of the finding.
3. **Stop when the reader can act.** One line is a complete answer.

**Cite `file.py:line` and symbols only when the reader needs them to act** — never to
show your work.

**Every sentence must earn its place.** Cut what the reader already knows: restating
what you just did, narrating the implementation, listing every file when three matter,
summaries of summaries. Say each point exactly once. Prefer few important bullets over
many complete ones.

### On GitHub

Part 1 is visible: the finding in plain words, and the next step. No `file.py:line`,
symbol names, or ```suggestion blocks. Part 2 is everything mechanical, wrapped exactly
like this — the blank line after `</summary>` is required or GitHub renders the contents
as raw text:

```
<details>
<summary>🔍 Technical details</summary>

...refs, suggestions, reasoning...
</details>
```

Omit part 2 when there's nothing mechanical to say. Never restate part 1 inside it. Two
things are never collapsed: a 🔒 security finding with the @kovtcharov-amd tag, and a
PR's Test plan.

### The shape

❌ **Technical-first:**
> The `_ensure_model_loaded()` call in `chat_completion()` was absent from the
> non-streaming branch, so when `RAGSDK._load_embedder` evicted the resident model,
> Lemonade auto-loaded Gemma at its default 32K ctx.

✅ **Plain-language-first:**
> Document Q&A silently capped out at 32K context, so long PDFs got truncated answers.
>
> - The embedder warm-up evicted the chat model, and the non-streaming path skipped the
>   reload check (`_ensure_model_loaded` in `lemonade_client.py`).

Same information; the second says what happened before it says where. **When in doubt:
shorter, plainer, outcome first.**

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

A PR description is developer-facing output, so [How You Communicate](#how-you-communicate)
governs it like everything else: plain language first, technical detail underneath, each
point made once. This section adds only the PR-specific *shape*.

**Keep PR descriptions short. Lead with *why* and *impact*, not *what*.** Reviewers skim; long walls of text get ignored. A PR description is a sales pitch for the change, not a changelog.

**Target shape (default — most PRs need only this):**

1. **One-paragraph "Why this matters"** — the user-observable impact, in concise direct prose (~3 sentences max). Lead with the *before-state* (what was broken / missing) and the *after-state* (what now works). No labelled prefix (`In plain English:`, `Layman-first:`); just lead with the substance. If a reviewer stops after this paragraph, they should know whether to merge.
2. **Test plan** — checkbox list of how to verify. Specific commands beat vague prose. Only list items a reviewer can actually verify before merge.

That's it. No "What changed" / "Files modified" / "Implementation notes" sections by default — the diff shows what changed; the commit messages explain how. The PR description's job is to sell the merge.

**Add a short threads list ONLY if** the PR genuinely bundles multiple logical changes a reviewer needs to evaluate independently. Each bullet: one line, with a *why this matters* clause. Not every commit — only changes a reviewer can't infer from the title.

**The "user-observable impact" test:** can a non-author understand the value in <30 seconds without reading the diff? If your description is "supports X protocol" or "refactors Y handler", you've described the *change* but not the *value*. Rewrite to "before: feature Z silently failed for users running model M; after: it works." Concrete observable behaviour beats abstract capability claims.

**Same rule for commit messages:** the conventional-commits title is the technical handle; the first line of the body is the summary (concise direct prose, no labelled prefix). PR #1034's body opens with `"ChatAgent system prompt had grown to ~52K chars …"` — direct, no preamble. The same rule applies to bot reviews and issue comments — see [Issue Response Guidelines](#issue-response-guidelines).

**Hard rules:**

- **No section longer than ~5 lines of prose** before breaking into bullets or cutting.
- **Every non-trivial claim earns its place with a why.** "Added a linter" is noise; "Added a linter so new agents stop shipping with missing docs/tests" is signal.
- **Cut exhaustive file-by-file enumeration and implementation walkthroughs.** The diff is the source of truth for what files changed and how. The description is the source of truth for *why a reviewer should care*.
- **No "Generated with Claude Code" tagline** (see attribution rule below).
- **If the PR really does bundle many threads**, group them — don't list 16 commits. Reviewers scan 4 themes faster than 16 bullets.

**Anti-patterns:**

- ❌ Copy-pasting the commit message log into the PR body
- ❌ "This PR adds X, Y, Z, A, B, C, D, E, F, G" with no stated value
- ❌ Mirroring every bullet in the summary inside the test plan (pick one)
- ❌ Explaining implementation details a reviewer will read from the diff anyway
- ❌ A "What changed" bullet list when the title + commit message body already cover it
- ❌ Naming files in the description ("modified `agent.py`") — the diff already shows that
- ❌ Burying the user impact under a section labelled "Summary"; lead with the impact
- ❌ Opening with "Refactors X handler" / "Migrates to Y protocol" / "Adopts Z abstraction" — implementation-language leads tell the reviewer *what changed* but not *why they should care*; lead with the user / reviewer impact instead

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

### IMPORTANT: No "Generated with Claude Code" Branding
**NEVER add "Generated with Claude Code" or similar branding text** to any output including documentation, PR descriptions, PR comments, commit messages, code comments, or any other content. This applies to all generated artifacts without exception.

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

**`amd-gaia.ai` links in `src/gaia/` MUST keep the `/docs/` path prefix.** Use
`https://amd-gaia.ai/docs/guides/...`, never `https://amd-gaia.ai/guides/...` — the
Mintlify docs tab serves under `/docs/` and bare paths 404. This is enforced by
`tests/unit/test_amd_gaia_urls.py` (issue #1058), which scans every `amd-gaia.ai`
URL literal in `src/gaia/`; dropping `/docs/` from a runtime string is a CI failure,
not a cleanup. Only the site root and install scripts (`/install.ps1`, `/install.sh`)
are allowlisted without the prefix.

#### IMPORTANT: A functional change must update EVERY doc that describes it — not just one

When a change alters an agent's behavior, public API, request/response contract,
defaults, lifecycle, or error codes, the same claim is almost always repeated
across several **bundled** docs. Update them **together** in the same change, or the
package ships documentation that contradicts itself — and the contradiction goes
live the moment that version publishes.

For a hub agent package (`hub/agents/<id>/{npm,python}/`), the doc surfaces that
must stay in sync are:

- **`README.md`** — the canonical, integrator-facing doc (rendered on the hub + npm)
- **`SPEC.md`** — the full technical reference
- **`SKILL.md`** — the AI-assistant integration playbook (Claude Code, etc.)
- **`CHANGELOG.md`** — the version entry describing the change
- any runtime/contract spec it ships — `spec_html.py`, `specification.html`,
  `openapi.*.json`

**Before calling the change done, grep the old claim/symbol/status-code across all
of these.** A behavior described in three docs must be corrected in three docs; the
CHANGELOG must name it. The same rule applies to the doc *site* (`docs/`) when the
change touches a documented surface.

Canonical miss (#1841): an agent gained auto-reap of its sidecar on parent exit and
the PR updated `README.md` to "cleanup is automatic" — but left `SPEC.md` and
`SKILL.md` still saying "always call `shutdown` or the child is orphaned." Both were
slated to publish in the same release, so the package would have shipped
self-contradicting lifecycle docs.

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
2. Check existing mixins in agent packages (e.g., `hub/agents/chat/python/gaia_agent_chat/tools/`)
3. Extract shared logic into base classes or mixins when patterns repeat

### Code Comments — Short or Skip

**Default to no comments.** Write one only when the *why* is non-obvious — a hidden constraint, a subtle invariant, a workaround for a specific bug. Never explain *what* the code does (identifiers should already do that).

**Keep WHY comments to one short line.** Multi-paragraph "history of how we got here" blocks are noise — the diff, commit message, and linked issue carry the history. Inline comments are read at the speed of code, not the speed of a postmortem.

**Don't reference the current task, fix, or callers inline.** Patterns like `"Pre-#1030 follow-up the non-streaming path skipped the check…"` or `"Added for the Y flow"` belong in the PR description and commit body. Inline they rot as soon as the code moves.

**Bad** (verbose, history-tagged, will rot):

```python
# Pre-flight: ensure the model is loaded at the GAIA-expected ctx.
# The streaming path already does this via
# ``_stream_chat_completions_with_openai`` -> ``_ensure_model_loaded``.
# Pre-#1030 follow-up the non-streaming path skipped the check, so
# when something (e.g. the RAG SDK's embedder warm-up) unloaded the
# LLM, the next non-streaming chat_completion let Lemonade auto-load
# Gemma at its own default ctx (32K) — bypassing
# MODELS[…].min_ctx_size and silently capping doc-Q&A at 32K.
self._ensure_model_loaded()
```

**Good** (one line, names the invariant the call enforces):

```python
# Re-check ctx — embedder warm-up can quietly unload the chat model.
self._ensure_model_loaded()
```

The PR / commit message is where multi-paragraph context lives. The code carries the one line a future reader needs to not break it.

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

### IMPORTANT: Test from the user's real initial state, and verify call *validity* at boundaries — not just invocation

**Two failure modes let bugs pass every "green" test and still break users. They are general — not specific to installers — so guard against both on any change, not just setup/download work.**

1. **Hidden-state masking — test from the state the *user* is in, not your primed one.** Many bugs only fire from a specific starting state: an empty cache, an empty DB/list, a first run, a cleared session, no config/connector yet, an expired token, zero search results, a missing optional dependency. Your dev box and your mocks carry leftover state that returns success and hides the failure ("works on my machine") — and it breaks for exactly the new users a feature targets. Reproduce from the cold/empty state before claiming a fix: for setup/download use `gaia init --profile <p> --force-models`, delete the artifact, or use a clean machine; for runtime features use an empty index/DB/session. And **a passing runtime ≠ a passing setup** — evals or inference prove a model *runs*; they say nothing about whether a new user can *download/register/configure* it. These are different code paths; verify the one the bug actually lives in.

2. **Mocks prove "we called it," not "the call is valid."** At any boundary with a contract — HTTP API, subprocess, SQL, file format, IPC — a stub returning a hardcoded success only proves the method was invoked, never that the request would be accepted. Assert the *shape* of the outgoing call (required prefixes, mutually-required fields, allowed value combinations), and where the contract lives in an external service add one real integration test (e.g. `require_lemonade`) that exercises it.

**#1655 is the canonical case for both:** the model-pull sent `recipe=` for a *built-in* Lemonade model, which Lemonade 400s — but only on a *fresh* pull. Every unit test mocked the client, every manual check ran on a box that already had `gemma4-it-e2b-FLM` cached, and the PR's `gaia init --profile npu` test-plan item was checked off against that warm cache. `tests/test_lemonade_client.py::test_pull_model` even documented the correct `user.`-prefix-with-`recipe` pattern, but stubbed the HTTP layer, so it couldn't catch the profile that violated it.

### IMPORTANT: Run agent evals when changing LLM-affecting code paths — do NOT skip

**Unit tests catch code paths; they don't catch LLM behavior.** When a change touches an LLM-affecting surface, you MUST run `gaia eval agent` against the relevant category and compare to the committed baseline before claiming the change is done. Skipping the eval is how regressions that pass every unit test still ship to users.

**Changes that REQUIRE an eval run before merge:**

- ChatAgent / DocumentQAAgent / FileIOAgent / ChatAgentLite system prompts (`_get_system_prompt()`) or any mixin prompt fragment
- The base agent's `_compose_system_prompt`, prompt-assembly order, or `_format_tools_for_prompt`
- Tool registration, tool docstrings, or the JSON tool schema sent to Lemonade
- Error classification (`LemonadeError` subclasses, `_classify_chat_exception`, `_extract_lemonade_user_message`) or the agent-loop catchall
- The default LLM model, tokenizer config, or the `is_tool_calling_model` mapping
- Tool-call response parsing / native-tool-call sentinel handling

**Claude access is almost always already available** when you are running from a Claude Code session — it comes from the user's Claude Code subscription, **not** from an exported `ANTHROPIC_API_KEY`. An empty `ANTHROPIC_API_KEY` therefore does **not** mean you lack Claude access, and is never a reason to skip the eval. Check access in this order:

1. **Confirm subscription auth first.** If you are running inside a Claude Code session, the subscription is active — that *is* your Claude access. The env var being unset is expected and fine. `ANTHROPIC_API_KEY` is rarely needed.
2. **Only then consider the key.** `gaia eval`'s judge client ([`src/gaia/eval/claude.py`](src/gaia/eval/claude.py)) reads `ANTHROPIC_API_KEY` from the environment specifically, so it is the *fallback* path used when an eval subprocess can't ride the subscription. Check it only when you actually need that path:

```bash
echo "${ANTHROPIC_API_KEY:0:8}"   # prints the first 8 chars if set; empty is normal
```

Only if the eval genuinely requires the key (the subprocess errors with `ANTHROPIC_API_KEY not found`) and it is absent, ask the user to export it. "I didn't run the eval because the env var looked empty" is not acceptable — verify auth access first.

**How to run:**

```bash
# Terminal 1 — backend (needed by gaia eval agent)
python -m gaia.ui.server --port 4200 --host 127.0.0.1

# Terminal 2 — run the eval, then compare its scorecard to the committed baseline.
# NOTE: `--compare` only DIFFS scorecards (BASELINE CURRENT) — it does NOT run an eval.
#       Run the eval first; it prints the run dir and writes <run-dir>/scorecard.json.
gaia eval agent --category rag_quality --agent-type doc
# → prints an ABSOLUTE path, e.g.  Output: /…/gaia/eval/results/<run-id>/   ← use it as printed, + /scorecard.json
# Pick the BASELINE matching your model; don't `ls -t` to find it — a fresh clone stamps
# every baseline with the checkout time, so an mtime sort picks arbitrarily.
gaia eval agent --compare \
  tests/fixtures/eval_baselines/gemma-4-e4b-d71cd914/scorecard_rag_quality.json \
  <printed-output-path>/scorecard.json
```

**Interpreting regressions:** if a category drops, fix the prompt in the same session and re-run before you commit. If the regression is intentional (e.g. you deliberately removed a capability), regenerate the baseline with `--save-baseline` and call it out explicitly in the PR description — the reviewer needs to see the diff between baselines, not just the new score.

**#1030 (the Gemma-4 RAG-PDF timeout) is the canonical example of what happens when this rule is skipped:** a prompt change passed every unit test, then broke document Q&A in production. #1033 tracks the systemic CI gaps that let it through.

### IMPORTANT: Run agent evals SERIALLY, never in parallel

**Never run two `gaia eval agent` invocations concurrently against the same Lemonade Server.** Each eval scenario forces Lemonade to load a specific model at a specific `ctx_size`; two concurrent runs will race-evict each other's models and you'll see chaotic failures like:
- `request (NNNN tokens) exceeds the available context size (4096 tokens)` — one run reloaded the model at a smaller ctx
- Spurious `BLOCKED_BY_ARCHITECTURE` / `INFRA_ERROR` results — process management collisions
- `model_load_error: llama-server failed to start` — port conflicts on llama-server children

**Rule of thumb:** at most ONE `gaia eval agent ...` process running at any time, period. If a fix-loop or batch-experiment script needs to chain runs, it must do so sequentially (`run-1 && run-2 && run-3`), never via background `&`. Before kicking off a new eval, verify nothing else is running:

```bash
ps aux | grep "gaia eval" | grep -v grep | wc -l    # must print "0"
```

This applies to every `gaia eval agent` run — including `--fix` auto-fix runs and any batch fix-loop that chains them. The judge LLM (Claude) can run concurrently across scenarios — the bottleneck is the local Lemonade backend, which is single-tenant per model slot.

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
```

### Agent UI Development
```bash
# Build frontend (required before gaia chat --ui)
cd src/gaia/apps/webui && npm install && npm run build

# Development with hot reload (two terminals)
uv run python -m gaia.ui.server --debug   # Terminal 1: backend (port 4200)
cd src/gaia/apps/webui && npm run dev      # Terminal 2: frontend (port 5174)
```

## Project Structure

```
gaia/
├── src/gaia/           # Main source code
│   ├── agents/         # Agent framework + in-core agents
│   │   ├── base/       # Base Agent class, MCPAgent, ApiAgent mixins
│   │   ├── tools/      # Cross-agent tool mixins (rag, file, shell, browser, scratchpad, screenshot…)
│   │   ├── builder/    # in-core agent (ChatAgent moved to hub/agents/chat/python/)
│   │   ├── code_index/ # CodeIndexToolsMixin — semantic code search (FAISS)
│   │   └── registry.py # Agent registry + KNOWN_TOOLS map
│   │   #   Packaged agents live in hub/agents/<id>/python/: gaia (flagship),
│   │   #   chat (its base class), email. Per-task agents were collapsed into
│   │   #   skills under hub/skills/.
│   ├── api/            # OpenAI-compatible REST API server
│   ├── apps/           # Standalone applications
│   │   ├── webui/      # Agent UI frontend (React/Vite/Electron)
│   │   ├── llm/        # LLM standalone app
│   │   ├── example/    # Reference/starter app
│   │   └── _shared/    # Shared assets for apps
│   ├── audio/          # Audio processing (Whisper ASR, Kokoro TTS)
│   ├── chat/           # Agent SDK (AgentSDK class, prompts, app entry)
│   ├── code_index/     # Code indexing/search backend
│   ├── connectors/     # Connector framework (Google/GitHub OAuth, MCP-server connectors, grants)
│   ├── database/       # DatabaseMixin and DatabaseAgent
│   ├── electron/       # Electron app integration
│   ├── eval/           # Evaluation framework
│   ├── filesystem/     # Filesystem service/utilities
│   ├── governance/     # Governance / guardrails layer
│   ├── img/            # Shared image assets
│   ├── installer/      # Install/init commands (gaia init, lemonade installer)
│   ├── llm/            # LLM backend clients (Lemonade, Claude, OpenAI) + providers/
│   ├── mcp/            # Model Context Protocol servers/clients
│   ├── messaging/      # Messaging adapters (Telegram, …)
│   ├── rag/            # Document retrieval (RAG)
│   ├── sd/             # Stable Diffusion tool mixin (SDToolsMixin)
│   ├── scratchpad/     # Scratchpad tables backend
│   ├── shell/          # Shell integration
│   ├── talk/           # Voice interaction SDK
│   ├── testing/        # Test utilities and fixtures
│   ├── ui/             # Agent UI backend (FastAPI server, routers, SSE, database)
│   ├── utils/          # Utility modules (FileWatcher, parsing)
│   ├── vlm/            # Vision LLM tool mixin (VLMToolsMixin, structured extraction)
│   ├── web/            # Web utilities (search/fetch backend)
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
└── .github/workflows/  # CI/CD pipelines
```

### Console Script Entry Points

Defined in [`setup.py`](setup.py) under `console_scripts`:

| Script | Entry Point | Purpose |
|--------|-------------|---------|
| `gaia` / `gaia-cli` | `gaia.cli:main` | Main CLI — all `gaia <subcommand>` |
| `gaia-mcp` | `gaia.mcp.mcp_bridge:main` | Standalone MCP bridge binary |

`gaia` and `gaia-mcp` are the only console scripts the core wheel ships.

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
- **Evaluation** (`src/gaia/eval/`): Agent eval benchmark with scenario-based testing - see [`docs/guides/eval.mdx`](docs/guides/eval.mdx)

### Agent Implementations

In-core agents live under `src/gaia/agents/`; the rest have moved to standalone hub
packages under `hub/agents/<id>/python/`. The authoritative registry is
[`src/gaia/agents/registry.py`](src/gaia/agents/registry.py); each agent's default model
is set in its own `agent.py` (see [Default Models](#default-models)).

| Agent | Description |
|-------|-------------|
| **GaiaAgent** | The flagship — conversation, documents, data, web, memory, skills — hub (`gaia/`) |
| **ChatAgent** | Multi-profile conversation (chat/doc/file) with RAG; the flagship's base class — hub (`chat/`) |
| **EmailTriageAgent** | Email triage for Gmail (local inference; needs the Google connector) — hub (`email/`) |
| **BuilderAgent** | Scaffolds new agents from templates — in-core (`builder/`) |

Per-task agents (code, analyst, browser, fileio, docqa, doc-search, summarize, jira,
docker, blender, sd, emr, routing) were **deleted**: their capability is the flagship's
tool surface driven by a `SKILL.md` in [`hub/skills/`](hub/skills/). Adding a capability
means writing a skill, not shipping an agent. `hub/agents/{hello-world,word-count,
connectors-demo}` remain as teaching templates and are not catalog agents.

`gaia telegram` is a messaging adapter, not an agent.

### Agent Registry & Tool Mixins

New agents are Python classes inheriting from `Agent` (see [`src/gaia/agents/base/agent.py`](src/gaia/agents/base/agent.py)). Register tools with the `@tool` decorator and compose reusable mixins. [`src/gaia/agents/registry.py`](src/gaia/agents/registry.py) exposes `KNOWN_TOOLS` — a curated map of reusable tool mixins that agents can compose by name:

| Tool name | Mixin | Purpose |
|-----------|-------|---------|
| `rag` | `gaia.agents.tools.rag_tools.RAGToolsMixin` | Document retrieval |
| `code_index` | `gaia.agents.tools.code_index_tools.CodeIndexToolsMixin` | Semantic code search (FAISS) |
| `file_search` | `gaia.agents.tools.file_tools.FileSearchToolsMixin` | Fuzzy/glob file search |
| `file_io` | `gaia.agents.tools.file_io_tools.FileIOToolsMixin` | Read/write/edit files |
| `shell` | `gaia.agents.tools.shell_tools.ShellToolsMixin` | Sandboxed shell commands |
| `screenshot` | `gaia.agents.tools.screenshot_tools.ScreenshotToolsMixin` | Screen capture |
| `filesystem` | `gaia.agents.tools.filesystem_tools.FileSystemToolsMixin` | File system navigation |
| `scratchpad` | `gaia.agents.tools.scratchpad_tools.ScratchpadToolsMixin` | SQL scratchpad tables for data analysis |
| `browser` | `gaia.agents.tools.browser_tools.BrowserToolsMixin` | Web search, page fetch, download |
| `sd` | `gaia.sd.mixin.SDToolsMixin` | Stable Diffusion image generation |
| `vlm` | `gaia.vlm.mixin.VLMToolsMixin` | Vision LLM / structured extraction |
| `skills` | `gaia.agents.tools.skill_library_tools.SkillLibraryToolsMixin` | Model-driven skill library (list/search/install/load/unload) |

When adding a new tool mixin, register it in `KNOWN_TOOLS` so other agents can compose it by name.

### Default Models
- `gaia llm` default: `Gemma-4-E4B-it-GGUF` (`DEFAULT_MODEL_NAME` in [`src/gaia/llm/lemonade_client.py`](src/gaia/llm/lemonade_client.py)). ChatAgent and EmailTriageAgent explicitly use it too.
- Agents that leave `model_id` unset fall back to `Gemma-4-E4B-it-GGUF` — the base `Agent.__init__` default (`model_id or DEFAULT_MODEL_NAME`). That covers GaiaAgent, ChatAgent, BuilderAgent, and the example templates. Every agent shares one model id so switching agents never evicts and cold-reloads the resident model.
- Context window is pinned per device profile, not per agent: `GPU_CTX_SIZE` (65536, GPU/CPU) and `NPU_CTX_SIZE` (32768, the FLM ceiling) in [`src/gaia/llm/lemonade_client.py`](src/gaia/llm/lemonade_client.py). A machine runs one profile, so exactly one `(model, ctx_size)` pair is ever resident.
- Vision: `Gemma-4-E4B-it-GGUF` is the default VLM (VLM mixin + EMR agent); `Qwen3-VL-4B-Instruct-GGUF` also supported
- Image generation (SD): `SDXL-Turbo`

## CLI Commands

All commands are registered in [`src/gaia/cli.py`](src/gaia/cli.py). Run `gaia -h` for the authoritative list.

**Agents & chat:**
- `gaia chat` - Interactive chat with RAG
- `gaia chat --ui` - Launch Agent UI (browser-based, requires `[ui]` extras)
- `gaia chat --ui --ui-port 8080` - Agent UI on custom port
- `gaia talk` - Voice interaction
- `gaia prompt "<text>"` - Single prompt to LLM (with system-prompt support)
- `gaia llm "<text>"` - Simple LLM queries
- `gaia knowledge {search|extract|usage}` - Web knowledge via Tavily (search/extract)
- `gaia email` - Email triage for Gmail (local inference; needs the Google connector)

**Servers & infrastructure:**
- `gaia daemon` - The headless daemon (one machine-wide custody process; supervises sidecar agents)
- `gaia api` - OpenAI-compatible API server
- `gaia mcp {start|stop|status|test|agent|serve|list|tools|test-client}` - MCP bridge (add/remove moved to the connectors framework, #977)
- `gaia schedule {add|list|show|remove|pause|resume|run|daemon}` - Run a skill or prompt on a cron schedule
- `gaia telegram {start|stop|status}` - Telegram messaging adapter
- `gaia connectors` - Manage connectors (Google/GitHub OAuth, MCP servers) and per-agent grants
- `gaia cache {status|clear}` - Cache management

**Setup & utilities:**
- `gaia init` - Setup Lemonade Server and download models
- `gaia install` - Install helper (e.g. Lemonade on first run)
- `gaia uninstall` - Tiered cleanup of `~/.gaia` and caches
- `gaia config {get|set}` - Persistent config in `~/.gaia/config.json`
- `gaia hub` - Browse, install, and uninstall agents from the Agent Hub
- `gaia skill` - Author and manage agent skills (`SKILL.md` capabilities)
- `gaia download` - Download a model
- `gaia kill` - Kill stray GAIA / Lemonade processes
- `gaia test` - Smoke tests
- `gaia youtube --download-transcript <url>` - YouTube utilities (transcript download)
- `gaia stats` - Show statistics from the most recent run
- `gaia memory` - Manage agent memory (onboarding bootstrap, status)
- `gaia diagnostics` - Bundle logs + system info into a tarball for bug reports
- `gaia agent {export|import}` - Manage custom agent bundles

**Evaluation & analysis** (see [`docs/reference/eval.mdx`](docs/reference/eval.mdx)):
- `gaia eval agent` - Run the agent eval benchmark (`--fix` auto-fixes failures)
- `gaia report` - Render eval reports
- `gaia perf-vis` - Visualize performance results

**Standalone binaries** (separate `console_scripts`, not subcommands):
- `gaia-mcp` - Standalone MCP bridge binary

## Documentation Index

All docs are `.mdx` (Mintlify). [`docs/docs.json`](docs/docs.json) is the authoritative
navigation — consult it rather than a hand-maintained copy here. Where things live:

- **Guides** (`docs/guides/`) — one per feature: chat, agent-ui, email, talk, memory, install, custom-agent, hardware-advisor, npu.
- **SDK** (`docs/sdk/`) — `core/` (agent-system, tools, console), `sdks/` (chat, agent-ui, rag, llm, vlm, audio), `infrastructure/` (mcp, api-server).
- **Reference** (`docs/reference/`) — cli, dev, faq, troubleshooting, eval.
- **Specs** (`docs/spec/`), **Deployment** (`docs/deployment/`), **Integrations** (`docs/integrations/`).

## Roadmap & Plans

The roadmap is at [`docs/roadmap.mdx`](docs/roadmap.mdx) ([live site](https://amd-gaia.ai/roadmap)).
Plan documents live in [`docs/plans/`](docs/plans/) (run `ls docs/plans/` for the full
set — Agent UI, setup-wizard, security-model, email/calendar, messaging, autonomy-engine,
agent-hub, skill-format, OEM bundling, desktop-installer, MCP, CUA, Docker, and more).
Browse the directory rather than a partial list here.

**Key architectural decisions (April 2026):**
- **GaiaAgent** rename planned (#696) — not yet landed; the chat agent class is still `ChatAgent` (`hub/agents/chat/python/gaia_agent_chat/agent.py`)
- Voice-first is P0 enabling technology (#702)
- No context compaction — memory + RAG handles long conversations
- Configuration dashboard + Observability dashboard as separate Agent UI panels
- MCP servers primary for email/calendar (not browser automation)
- Signal is Phase 1 messaging priority (privacy-first)

## Issue Response Guidelines

Writing anything that gets posted to GitHub — an issue reply, PR comment, discussion
response, or review? Use the **`github-issue-response` skill**
(`.claude/skills/github-issue-response/SKILL.md`). It carries the security-escalation
protocol, when to escalate to @kovtcharov-amd, per-response-type length caps, the
doc-link map, and worked good/bad examples.

Two rules are important enough to restate here:

- **Never discuss vulnerability details publicly.** Point the reporter at a private
  advisory (https://github.com/amd/gaia/security/advisories/new) and tag
  @kovtcharov-amd. No exploit steps, no proof-of-concept, in any public thread. A
  `🔒 SECURITY CONCERN` line and the maintainer tag always stay visible — never
  collapsed inside a `<details>` block.
- **Output style comes from [How You Communicate](#how-you-communicate)**, same as
  everywhere else. Plain language first, technical depth underneath.

Automated PR-review *policy* — severity tiers, the nit cap, skip rules, length caps —
lives in [`REVIEW.md`](REVIEW.md), which is the single source of truth for review
scoring. Don't fork it into another file.

## Claude Agents

Specialized agents live in `.claude/agents/` (20 total). Each agent file is the authoritative source for its scope, when-to-use / when-NOT-to-use triggers, and conventions — the summaries below are a pointer, not a replacement.

### Development
- **gaia-agent-builder** — Creating a new GAIA agent (Python class). Not for tuning an existing agent's prompt or adding a single tool.
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
- **voice-engineer** — Whisper ASR, Kokoro TTS, Talk SDK, real-time audio.
- **lemonade-specialist** — Lemonade Server / provider adapter, NPU/GPU optimisation, model selection.
- **prompt-engineer** — System prompts, tool docstrings, eval-judge prompts inside GAIA.

### Infrastructure
- **frontend-developer** — React/Vite/Electron Agent UI and standalone apps.
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

## Learned Skills

**Read the matching skill before starting related work.** Every skill under
`.claude/skills/<name>/SKILL.md` is auto-discovered by its `description` and invoked with
the `Skill` tool — run `ls .claude/skills/` to see the current set. They cover releases,
testing, hub-agent porting and integration, eval scorecards, the weekly audit workflow,
LemonadeClient changes, GitHub responses, and presentations.

This list is deliberately not enumerated here — a hand-maintained copy drifts the moment
someone adds a skill. If a skill exists, Claude already sees its description.

**Adding one?** It must be a directory with a `SKILL.md` inside
(`.claude/skills/<name>/SKILL.md`). A bare `.md` at the skills root is silently ignored —
it never loads and the `Skill` tool can't invoke it. (`gaia-presentation-assets/` has no
`SKILL.md` on purpose — it is a shared asset directory for the two presentation skills.)
