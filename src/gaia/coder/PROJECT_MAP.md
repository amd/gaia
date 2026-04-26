# PROJECT_MAP.md — Map of `amd/gaia` (the project I am building)

> The subsystem map of the project I help build. Injected into every system
> prompt alongside `GAIA.md` and `ARCHITECTURE.md`. Hand-curated for now;
> auto-maintained from the RAG index in a later phase. See
> `docs/plans/coder-agent.mdx` §6.5 for the contract.

## What GAIA is, in one paragraph

**GAIA (Generative AI Is Awesome)** is AMD's open-source framework for
running generative-AI applications locally on AMD hardware, with
specialized optimizations for Ryzen AI processors with NPU support.
External site: <https://amd-gaia.ai>. The product runs on end-user
machines via Lemonade Server; my own runtime is cloud-hosted (Anthropic
Claude) because *I am tooling for building GAIA, not part of GAIA itself*.

## Subsystem map — `src/gaia/`

| Module | Purpose | Key files / classes |
|--------|---------|---------------------|
| `agents/` | GAIA *product* agents shipped to users. Inherit `gaia.agents.base.Agent`. | `base/agent.py`, `chat/`, `code/`, `blender/`, `jira/`, `docker/`, `summarize/`, `emr/`, `routing/`, `sd/`, `registry.py` |
| `agents/base/` | Foundational `Agent` class, `MCPAgent` mixin, `ApiAgent` mixin, `@tool` decorator, `AgentConsole`. **Reuse before you reinvent.** | `agent.py`, `tools.py`, `console.py`, `mcp_agent.py`, `api_agent.py` |
| `api/` | OpenAI-compatible REST server for any registered GAIA agent. | `server.py`, `routes/` |
| `apps/` | Standalone applications (Electron / web). | `webui/` (React/Vite/Electron), `jira/`, `llm/`, `summarize/`, `docker/`, `example/` |
| `audio/` | Whisper ASR + Kokoro TTS. | `whisper_asr.py`, `kokoro_tts.py` |
| `chat/` | Agent SDK (`AgentSDK` — formerly `ChatSDK`), prompt composers. | `sdk.py`, `prompts/` |
| `cli.py` | Main `gaia <subcommand>` entry. **Do not confuse with `gaia.coder.cli`.** | one big argparse tree |
| `code_index/` | FAISS-backed semantic code index for the `semantic_search` tool. | `sdk.py`, `parsers.py` |
| `coder/` | **My own source.** Engineering tooling — see `ARCHITECTURE.md`. | `agent.py`, `llm.py`, `repl.py`, `cli.py`, `self_fix/`, `review/`, `tools/`, `stores/` |
| `database/` | `DatabaseMixin` + `DatabaseAgent`. | `mixin.py` |
| `electron/` | Electron-app integration. | `bridge.py` |
| `eval/` | Evaluation framework. The `ClaudeClient` wrapper I use is here. | `claude.py`, `config.py`, `eval.py`, `batch_experiment.py` |
| `installer/` | `gaia init` and Lemonade installer. | `init.py` |
| `llm/` | LLM-backend clients with provider routing. | `lemonade_client.py`, `providers/claude.py`, `providers/openai_provider.py`, `factory.py` |
| `mcp/` | MCP servers + bridge. | `mcp_bridge.py`, `servers/` |
| `rag/` | Document RAG (PDFs, etc.) — distinct from `code_index/`. | `sdk.py`, `pdf_utils.py` |
| `sd/` | Stable Diffusion mixin. | `mixin.py` |
| `talk/` | Voice-interaction SDK. | `sdk.py` |
| `ui/` | Agent UI backend (FastAPI + SSE) for `apps/webui/`. | `server.py`, `routers/`, `sse_handler.py` |
| `vlm/` | Vision-LLM mixin. | `mixin.py` |

## Public-API surface (what a change might break)

* **CLI:** `gaia <subcommand>` (in `src/gaia/cli.py`). Authoritative list:
  `gaia -h`. Subcommands include `chat`, `talk`, `code`, `summarize`,
  `blender`, `jira`, `docker`, `sd`, `api`, `mcp`, `cache`, `init`,
  `install`, `download`, `kill`, `test`, `yt`, `template`,
  `eval {fix-code|agent}`, `gt`, `generate`, `batch-exp`, `report`,
  `visualize`, `perf-vis`.
* **Console scripts** (separate binaries, in `setup.py`):
  `gaia` / `gaia-cli`, `gaia-mcp`, `gaia-emr`, `gaia-code` (legacy
  Next.js scaffolder — unrelated to me), `gaia-coder` (me).
* **REST API:** OpenAI-compatible at `/v1/chat/completions`,
  `/v1/completions`, `/v1/models`. Served by `gaia api`.
* **`KNOWN_TOOLS` registry:** declared in `src/gaia/agents/registry.py`.
  Adding a new mixin requires adding the row so YAML-manifest agents
  can opt in.
* **Agent UI IPC:** between `src/gaia/ui/` (Python) and
  `src/gaia/apps/webui/` (TypeScript / Electron). Breaking either side
  breaks the UI.

## In-flight initiatives

* `feat/coder-interactive` — interactive REPL (foundation merged
  2026-04-25; this branch).
* `feat/coder-self-fix-llm-wired` — wire `CoderLLM` into self-fix
  triage / critique / classify-failure / edit-hunks. Removes four
  raising stub clients.
* `feat/coder-cli-stubs-real` — real implementations for
  `doctor / status / audit / spend / introspect / egress / skill`.
* `feat/coder-safety-and-pass2` — Pass-2 pytest-glob fix +
  `enforce_action` safety seam at `fixer` and `publisher` boundaries +
  logger refactor (`logging` → `gaia.logger.get_logger`).
* `feat/coder-docs-repl` — user-facing guide for the REPL +
  plan-status update.
* `feat/coder-semantic-search` — FAISS-backed `semantic_search` tool
  (parent branch; merged into `feat/coder-interactive` via fork).

## Architectural decisions (ADRs) — short form

* **Coder is sui generis.** `gaia.coder.CoderAgent` does **not** inherit
  from `gaia.agents.base.Agent` — composition only, where it helps.
  Different lifecycle (long-lived daemon vs. per-call ReAct), different
  LLM backend (cloud vs. local), different review contract.
* **Default model is Sonnet.** `claude-sonnet-4-6` for the REPL; the
  seven-pass review and adversarial pass explicitly upgrade to
  `claude-opus-4-7-20251001`. Cost-routing optimisation is deferred
  until we have telemetry.
* **Coder lives in `src/gaia/coder/`, not `src/gaia/agents/`.** Top-level
  signals "major subsystem"; not under `agents/` because I am not a GAIA
  *product* agent.
* **`gaia-code` ≠ `gaia-coder`.** The legacy Next.js scaffolder keeps its
  name and is left untouched. I use `gaia-coder`. Do not rename.
* **Branch contract:** I integrate on `coder`, never on `main`.
  Branch-protection rules on `main` and the §5.7 contract enforce this.

## Known gotchas

* **`pypdf` before `PyPDF2`** per #495. The RAG SDK tries `pypdf` first
  and only falls back to `PyPDF2` for legacy PDFs.
* **Lemonade context size:** the code agent needs `--ctx-size 32768`
  for non-trivial work.
* **`SearchToolsMixin` already inherits `FileToolsMixin`** — listing
  both as bases triggers an MRO error. Compose `SearchToolsMixin` only.
* **`@tool` registration is side-effecting.** The mixins only populate
  the global registry when `register_*_tools()` runs. Test fixtures
  must call it before exercising `build_anthropic_tools()`.
* **Pass 2's `_infer_test_paths`** historically passed `tests/**/test_X.py`
  to pytest as a literal — pytest does not glob `**`. Fixed in
  `feat/coder-safety-and-pass2`.

## Open questions about the project

Areas where my understanding is thin (I'll flag these in the weekly standup):

* The exact contract for `gaia.code_index.CodeIndexSDK.index_repository()`
  cache invalidation — when does the FAISS index need rebuilding?
* The handoff between `RoutingAgent` and individual product agents —
  is there a documented protocol or just convention?
* Electron UI ↔ FastAPI backend message shape — is there a typed
  contract somewhere or is it implicit from `sse_handler.py`?
