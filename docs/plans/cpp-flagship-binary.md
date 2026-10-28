# C++ flagship: `gaia-agent`, one tiny native binary

**Status:** Implemented (see "What landed" below)
**Date:** 2026-10-27
**Builds on:** [`cpp-framework-parity.md`](cpp-framework-parity.md) (P3.3 skill manager, P5.1
reference agent) and [`gaia-agent-readiness.md`](gaia-agent-readiness.md) (the refusal spine).

## Why this matters

The Python flagship has moved a long way since the C++ framework last caught up: persistent
memory, a skill library the model loads on demand, web research, a SQL scratchpad, paged tool
output, a 150-step budget, and Gemma-4 as the default model. The C++ side still ships only
`gaia-bash`, a shell-scripting specialist on a 20-step, 4K-truncation, Qwen3-4B setup. After this
work, `gaia-agent` is a single self-contained executable — about 3 MB, no runtime DLLs, no Python
— that behaves like the flagship: same tool names, same skill catalogue, same memory model, same
defaults, talking to the same Lemonade server.

## What exists vs. what the flagship needs

Measured against the Python flagship (`hub/agents/gaia/python/gaia_agent/agent.py` and the
`ChatAgent` "full" profile it composes) on `main` at `71ea912bc`.

| Capability | Python flagship | C++ today | This plan |
|---|---|---|---|
| Default model | `default_model` in `~/.gaia/config.json`, else `Gemma-4-E4B-it-GGUF` | `Qwen3-4B-GGUF` hard-coded | Port `resolve_default_chat_model` |
| Context / steps | 64K GPU ctx, 150 steps (`GAIA_AGENT_MAX_STEPS`) | 16K, 20 steps | Port both, strict env parsing |
| Lemonade auth | `LEMONADE_API_KEY` → `Authorization: Bearer` | none | Add |
| Large tool output | Indexed head/tail + `read_tool_output(artifact, entry, offset, limit)` | Clipped to 4,000 chars, rest lost | Port the artifact store and pager |
| Memory | `remember`/`recall`/`update_memory`/`forget`/`search_past_conversations`, SQLite + FTS5 + embeddings, credential scrub, dated notes in the prompt | none | Port, with hybrid (embedding + BM25) recall |
| Skills | Discovery across bundled / `~/.gaia/skills` / `.claude/skills`, catalogue in the prompt, `list_skills`/`load_skill`/`unload_skill`/`skill_status`, always-on `gaia-voice` | Parser and skill sets only; nothing discovers, loads, or injects (#2800) | Port, with the refusal spine (#2799) |
| Files | `read_file`/`write_file`/`edit_file` (read-first), `search_file`, `search_file_content`, `browse_directory`, `get_file_info` | `file_read`/`file_write`/`file_edit`/`file_search` | Flagship-named tools over the existing core |
| Shell / code | `run_shell_command`, `run_python` | `bash_execute` (agent-private) | Promote shell to core; add `run_python` |
| Web | `fetch_webpage`, `search_web` (DuckDuckGo) | none | Port; WinHTTP on Windows so HTTPS needs no OpenSSL |
| Data | `create_table`/`insert_data`/`query_data`/`list_tables`/`drop_table` | `Database` class only | Port the scratchpad with Python's limits and SELECT-only rule |
| Misc | `sleep`, fast mode (`GAIA_FAST`), `<think>` handling | none | Port |
| Interfaces | TUI, CLI, stdio JSONL, HTTP | REPL/TUI, one-shot, JSONL, OpenAI API, MCP (in `gaia-bash`) | Reuse all of them |
| Size | ~200 MB frozen Python sidecar | 2.9 MB unstripped `gaia-bash` | `tiny` preset: `-Os`, section GC, strip, static |

## Decisions

1. **One binary.** The CMake target is `gaia-agent`, the name the TUI launches for the
   flagship; it is released and installed as `gaia-native` so it never shadows the Python
   flagship's own `gaia-agent` sidecar on PATH. `gaia-bash` is removed: one harness, as in Python.
2. **Same tool names and argument names as Python.** Skills are written against the Python
   flagship (`tools_required: [read_file, run_python, …]`), so a C++ binary that calls them
   `file_read` would fail every skill silently. The flagship registers the Python names.
3. **Skills are embedded.** The starter pack (`hub/skills/`) and the flagship's always-on
   `gaia-voice` are compiled into the binary (~85 KB of text), so "single binary" stays true.
   On-disk roots still win in Python's precedence order: embedded → `~/.gaia/skills` →
   `./.claude/skills` → `~/.claude/skills` (imports, read-only, kept out of the catalogue).
4. **The refusal spine ships with the loader, not after it.** A skill that ships code tools
   (`metadata.gaia.tools`) is refused, because a C++ process cannot import `tools.py`. A
   permission no bridge covers is refused with Python's exact rules: `network`/`mcp` are
   bridged, `shell:execute:<bin>` is bridged only for the binaries Python polices, anything
   else (`filesystem`, `database`, `desktop`, `env`, bare `shell:execute`) is refused. Every
   refusal names the skill and the reason, and `--list-skills` shows it.
5. **Memory has its own file and the same ranking.** Recall fuses embedding and BM25 ranks with
   Python's weighted RRF; when the embedder is unavailable it ranks by keywords and says so in
   the result. The store lives in `~/.gaia/native/memory.db` so the two runtimes never migrate
   each other's schema; the column names match Python's so a later merge is a copy, not a
   translation. The credential scrub is a full port, Luhn check included — a secret must never
   reach the database from either runtime.
6. **HTTPS without a TLS library.** Web tools go through WinHTTP on Windows (a system DLL) and
   the system `curl` elsewhere. The transport is fixed per platform and named in the tool's
   error when it cannot work; nothing silently switches.
7. **Stricter, not looser, on the shell.** Python lets a loaded skill run its granted binary
   without a prompt. C++ does not port the per-subcommand command policies, so every
   `run_shell_command` and `run_python` call asks, every time, unless the user approved that
   tool for this session or ran with `--yes`. Non-interactive runs refuse
   confirm-gated tools unless `--yes` is given.
8. **New behavior is opt-in on `AgentConfig`.** Output paging, step-budget notices and the
   think-block split are config fields the flagship turns on, so the examples and the
   existing tests see byte-identical prompts and results.
9. **Tiny by construction.** A `tiny` CMake preset (TUI off, `-Os`, `-ffunction-sections`
   + `--gc-sections`, stripped, static runtime) and a zig toolchain file, so
   `pip install ziglang cmake ninja && cmake --preset tiny && cmake --build --preset tiny`
   produces the binary on any host and cross-compiles to Linux (static musl) and macOS.

## Out of scope (named so nobody assumes it is done)

- Image generation, audio transcription, email and other connectors, browser automation, and
  screenshots. Skills that need them stay loadable by name but are left out of the catalogue.
- Dynamic per-turn tool and skill selection (off by default in the Python flagship too).
- `set_loop_state` (Agent UI autonomy plumbing) and the skill hub verbs (install/capture/remove).
- Per-binary shell command policies (decision 7 replaces them with confirm-every-call).
- Learned skill deltas.

## Work breakdown

Each step lands with unit tests in `cpp/tests/`; nothing merges with a skipped test.

1. **Runtime defaults** — `gaia/flagship_config.h`: GAIA home and config dir, `config.json`
   `default_model`, `GAIA_AGENT_MAX_STEPS` / `GAIA_CTX_SIZE` (positive int or a loud error),
   `GAIA_FAST`, `LEMONADE_API_KEY` bearer header on every Lemonade request.
2. **Tool output paging** — `gaia/tool_output.h`: artifact store (64 MiB, 1 h TTL, 8,000-char
   pages), `read_tool_output`, context-proportional threshold (`ctx × 30000/32768`, target ⅔),
   wired into `Agent` behind `AgentConfig::toolOutputPaging`.
3. **Loop parity** — `<think>` split and `reasoning_content` handling, step-budget notices at
   20/10/5 remaining, one no-tools closing answer at the cap, non-object tool arguments turned
   into a retry prompt (3 strikes), behind config flags.
4. **Memory** — `gaia/memory.h`: schema, the five tools, credential scrub (patterns, card
   grouping + Luhn + issuer prefix), conversation log with `[secret removed]`, recall-time
   redaction, the dated-notes block in the system prompt.
5. **Skills runtime** — `gaia/skill_manager.h`: roots, embedded pack, shadowing, refusals,
   catalogue text and `LOADED SKILLS` block byte-matching Python, a `SkillLoader`
   implementation for `Agent::loadSkillSet`, and the four skill tools.
6. **Flagship toolbelt** — `gaia/flagship_tools.h`: Python-named file tools over the core,
   `search_file`, `search_file_content`, `browse_directory`, `get_file_info`,
   `run_shell_command` (promoted shell), `run_python`, `fetch_webpage`, `search_web`, the
   scratchpad, `sleep`. Paths confined to `allowed_paths` (home by default,
   `GAIA_ALLOWED_PATHS` narrows), secret files refused on read and write.
7. **`gaia-agent` binary** — `cpp/agents/gaia/`: interactive REPL (TUI when built in),
   one-shot `gaia-agent "question"`, `--json-events`, `--serve` (OpenAI-compatible), `--mcp`,
   `--fast`, `--model`, `--skill`, `--list-skills`, `--resume`,
   `--list-sessions`, `--yes`, `--version`. The HTTP and MCP servers move from
   `agents/bash/` to `agents/common/`.
8. **Build** — `GAIA_TINY` option, `CMakePresets.json` (`tiny`, `dev`), `cmake/zig-toolchain.cmake`,
   CI that builds the `tiny` presets (Linux and Windows on every change, all four platforms
   on a release tag) and fails a binary over 6 MB.
9. **Tests and proof** — unit tests per module; an end-to-end gtest driving the flagship
   through a mock Lemonade (tool call → paged result → answer, skill load, memory round
   trip); the real CLI run against the mock server and, where available, a live Lemonade.
10. **Docs** — `cpp/README.md`, `docs/cpp/gaia-agent.mdx` (+ `docs/docs.json`), and status
    notes in the two parity plans.

## What landed beyond the first cut

- **RAG and the code index.** `LemonadeClient::embeddings()` made the retrieval half possible:
  `index_document`, `query_documents`, `query_specific_file`, `index_directory`,
  `search_indexed_chunks`, `summarize_document`, and `index_codebase` / `search_code_index`.
  Memory recall became hybrid (Python's weighted RRF) with per-turn auto-recall.
- **Documents.** PDF (filter chains, ToUnicode CMaps, glyph widths), DOCX, XLSX and PPTX text,
  matching pypdf at 99.5–100% word recall on 127 pages of repo fixtures.
- **`analyze_data_file`**, the Python data tool, for CSV/TSV/XLSX.
- **Vision.** `analyze_image` and `answer_question_about_image` on the session model, and scanned
  PDF pages (JPEG, or 8-bit gray/RGB pixels re-encoded as PNG) transcribed in `read_file` and
  `index_document`.
- **Final-answer guards** from Python: tool markup as text, narrated next steps, planning-only
  replies, unopened workspace files, and cut-off replies.
- **The TUI's stdio protocol** (`--stdio`), checked against `tests/fixtures/stdio/gaia_stdio_wire.json`
  and driven end to end by the real Go TUI.
- **`gaia-bash` removed**: one harness, as in Python. Its HTTP and MCP servers moved to
  `cpp/agents/common/`.
- **Distribution**: `release_gaia_native.yml` publishes per-platform assets and `SHA256SUMS`;
  `cpp/packaging/install.{sh,ps1}` install from them.

## Risks

- **Prompt drift from Python.** The catalogue and loaded-skills blocks are copied verbatim and
  pinned by tests; the system prompt is a condensed port of the "full" profile, so a C++ run is
  not an eval-comparable flagship run. The C++ binary has no committed eval baseline yet.
- **Regex dialect.** `std::regex` has no lookbehind; the card and PIN rules are reimplemented
  as scanners and tested against the same positive and negative cases Python's tests use.
- **Binary size creep.** Guarded by the CI size budget, not by hope.
