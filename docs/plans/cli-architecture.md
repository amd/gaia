# CLI Architecture: Cyclopts + Textual Hybrid

> **Status:** Draft
> **Date:** 2025-02-05
> **Branch:** `kalin/cli`

## Overview

Migrate the GAIA CLI from a monolithic argparse-based `cli.py` (5,900 lines) to a two-layer architecture:

- **Layer 1 — Cyclopts:** Fast argument parsing, auto-generated help pages, lazy subcommand loading
- **Layer 2 — Textual:** Rich terminal UI for interactive agent sessions (chat, code, talk)

Non-interactive commands stay lightweight (no TUI overhead). Interactive commands launch a full Textual application with streaming markdown, smooth scrolling, and flicker-free updates.

## Motivation

| Problem | Current State | Target |
|---------|--------------|--------|
| `gaia --help` is slow | Imports all agents, LLM clients, RAG on startup | <200ms, lazy imports |
| Agent output is noisy | Dumps thought/goal/plan/JSON per step | One-line spinner per tool, streamed answer |
| No smooth streaming | Rich `Live` flickers on rapid updates | Textual `MarkdownStream` — sub-1ms incremental rendering |
| Can't select/copy code | Terminal copies line numbers, box chars | Textual clean text selection |
| 5,900-line monolith | All 28+ commands in one file | Per-command modules, lazy-loaded |
| Basic input | `input()` or prompt_toolkit | Textual `Input` with markdown editor, fuzzy file picker |

## Architecture

```
gaia (entry point)
│
├── cyclopts.App()              ← parses args, routes to command
│   ├── cli/chat.py             ← lazy-loaded on `gaia chat`
│   ├── cli/code.py             ← lazy-loaded on `gaia code`
│   ├── cli/llm.py              ← lazy-loaded on `gaia llm`
│   ├── cli/prompt.py           ← lazy-loaded on `gaia prompt`
│   ├── cli/talk.py             ← lazy-loaded on `gaia talk`
│   ├── cli/eval.py             ← lazy-loaded on `gaia eval`
│   ├── cli/mcp.py              ← lazy-loaded on `gaia mcp`
│   ├── cli/api.py              ← lazy-loaded on `gaia api`
│   └── cli/*.py                ← one file per command group
│
├── Interactive commands (chat, code, talk)
│   └── Textual App             ← full TUI
│       ├── Markdown widget     ← streaming LLM responses
│       ├── RichLog widget      ← tool execution output
│       ├── Input widget        ← user prompt
│       ├── LoadingIndicator    ← thinking state
│       └── Agent backend       ← subprocess or in-process
│
└── Non-interactive commands (llm, prompt, eval, cache, ...)
    └── Rich console            ← simple print, no TUI overhead
```

## Command Classification

### Interactive → Textual TUI

| Command | Current Handler | TUI Behavior |
|---------|----------------|-------------|
| `gaia chat` | `interactive_mode()` with prompt_toolkit | Streaming markdown chat, RAG status, document list |
| `gaia code` / `gaia-code` | `input()` loop | Streaming markdown, inline code diffs, test output |
| `gaia talk` | `TalkSDK.start_voice_session()` | Audio visualizer, transcript display, streaming response |
| `gaia api` | FastAPI server loop | Server status dashboard, request log |

### Non-interactive → Rich Console (no TUI)

| Command | Behavior |
|---------|----------|
| `gaia prompt <msg>` | Stream response to stdout, exit |
| `gaia llm <msg>` | Stream response to stdout, exit |
| `gaia summarize` | Progress bar → output file |
| `gaia eval` | Run evaluation → print results table |
| `gaia download` | Progress bar → done |
| `gaia cache status/clear` | Print status, exit |
| `gaia mcp status/stop/test` | Print result, exit |
| `gaia init/install/uninstall` | Step-by-step output |
| `gaia kill` | Kill process, confirm |
| All other commands | Simple output → exit |

### Subprocess-delegated (unchanged)

| Command | Behavior |
|---------|----------|
| `gaia blender` | Spawns Blender subprocess |
| `gaia jira` | Spawns Jira agent subprocess |
| `gaia docker` | Spawns Docker agent subprocess |
| `gaia sd` | Spawns Stable Diffusion subprocess |

## Layer 1: Cyclopts Migration

### Current State

- `cli.py:663-760` — `main()` creates argparse parser with 28+ subcommands
- `cli.py:685-751` — Parent parser with 12 global flags
- `cli.py:14-27` — Top-level imports (LemonadeClient, AgentConsole, etc.) block startup

### Target Structure

```
src/gaia/
├── cli/
│   ├── __init__.py          # cyclopts.App() definition, global flags
│   ├── chat.py              # gaia chat
│   ├── code.py              # gaia code
│   ├── prompt.py            # gaia prompt
│   ├── llm.py               # gaia llm
│   ├── talk.py              # gaia talk
│   ├── api.py               # gaia api
│   ├── mcp.py               # gaia mcp {start,stop,status,test}
│   ├── eval.py              # gaia eval, gaia batch-experiment
│   ├── download.py          # gaia download
│   ├── summarize.py         # gaia summarize
│   ├── cache.py             # gaia cache {status,clear}
│   ├── setup.py             # gaia init, install, uninstall
│   ├── utils.py             # gaia kill, youtube, test, etc.
│   └── _common.py           # shared flag definitions, helpers
├── cli.py                   # DEPRECATED — kept as shim during migration
```

### Cyclopts Lazy Loading

```python
# src/gaia/cli/__init__.py
import cyclopts

app = cyclopts.App(
    name="gaia",
    help="GAIA - Generative AI on AMD hardware",
    help_format="markdown",
)

# Lazy-loaded command modules — NOT imported until the command is invoked
app.command(chat, group="Agents")       # from .chat import chat
app.command(code, group="Agents")       # from .code import code
app.command(prompt, group="Quick")      # from .prompt import prompt
app.command(llm, group="Quick")         # from .llm import llm

# ... etc
```

With cyclopts lazy loading, `gaia --help` only imports cyclopts itself (~10ms). The `chat` module (and its Textual/agent dependencies) isn't imported until `gaia chat` is actually invoked.

### Global Flags

```python
# src/gaia/cli/_common.py
from dataclasses import dataclass

@dataclass
class GlobalOptions:
    use_claude: bool = False
    use_chatgpt: bool = False
    claude_model: str = "claude-sonnet-4-20250514"
    base_url: str | None = None
    model: str | None = None
    max_steps: int = 100
    trace: bool = False
    stats: bool = False
    stream: bool = False
    debug: bool = False
    no_lemonade_check: bool = False
    logging_level: str = "INFO"
```

Cyclopts natively supports dataclass-as-arguments, so this replaces the manual argparse parent parser.

## Layer 2: Textual TUI for Interactive Agents

### Chat TUI Design

```
╭─ GAIA Chat ────────────────────────────────────────────────╮
│                                                             │
│  **Assistant**                                              │
│  Based on the documents, the main entry point is            │
│  `cli.py` which uses argparse...                            │
│                                                             │
│  ```python                                                  │
│  def main():                                                │
│      parser = ArgumentParser()                              │
│  ```                                                        │
│                                                             │
│  ─── Searching documents... ────────────────────── 1.2s ──  │
│                                                             │
│  **Assistant**                                              │
│  I found 3 relevant sections...                             │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│  📎 3 documents indexed │ Qwen3.5-35B │ Step 2/100     │
├─────────────────────────────────────────────────────────────┤
│  > _                                                        │
╰─────────────────────────────────────────────────────────────╯
```

### Key Widgets

| Widget | Purpose | Textual Class |
|--------|---------|---------------|
| Chat history | Scrollable conversation | `VerticalScroll` + `Markdown` per message |
| Streaming response | Live LLM output | `Markdown` with `get_stream()` |
| Tool status | "Searching documents..." | `RichLog` or custom `Static` widget |
| Loading state | Pulsating dots while thinking | `LoadingIndicator` |
| User input | Prompt with history | `Input` |
| Status bar | Model, step count, doc count | `Footer` or custom `Static` |

### Streaming Pattern

```python
from textual.app import App, ComposeResult
from textual.widgets import Markdown, Input, Footer, VerticalScroll
from textual.worker import Worker

class GaiaChatApp(App):
    CSS = """
    VerticalScroll { height: 1fr; }
    Input { dock: bottom; }
    Footer { dock: bottom; }
    """

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="chat")
        yield Input(placeholder="Ask a question...", id="prompt")
        yield Footer()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value
        event.input.clear()
        # Add user message
        chat = self.query_one("#chat")
        chat.mount(Markdown(f"**You:** {query}"))
        # Stream agent response
        self.stream_response(query)

    @work(thread=True)
    async def stream_response(self, query: str) -> None:
        chat = self.query_one("#chat")
        md = Markdown()
        self.call_from_thread(chat.mount, md)
        stream = Markdown.get_stream(md)

        # Run agent in background, stream chunks
        async for chunk in self.agent.process_query_stream(query):
            if chunk.type == "text":
                await stream.write(chunk.text)
            elif chunk.type == "tool_start":
                # Show tool status line
                pass
            elif chunk.type == "tool_end":
                # Clear tool status
                pass

        await stream.stop()
```

### Backend Communication

Two options:

**Option A: In-process (simpler, recommended for v1)**
- Agent runs in the same Python process as the Textual app
- Use Textual workers (`@work(thread=True)`) to run agent in background thread
- Agent yields chunks via async generator
- Simpler, lower latency, easier to debug

**Option B: Subprocess with JSON protocol (Toad-style)**
- Agent runs as separate subprocess
- Frontend/backend communicate via JSON over stdin/stdout
- Better isolation, agent can't crash the UI
- Can run agent remotely
- More complex, higher latency, harder to debug

**Recommendation:** Start with Option A. Migrate to Option B later if isolation becomes necessary (e.g., for sandboxed code execution).

### Agent Output Abstraction

The existing `OutputHandler` interface (`console.py:44`) already provides the right abstraction. Create a new implementation:

```python
class TextualOutputHandler(OutputHandler):
    """Routes agent output to Textual widgets."""

    def __init__(self, app: GaiaChatApp):
        self.app = app

    def print_streaming_text(self, text: str):
        self.app.stream_chunk(text)

    def start_progress(self, message: str):
        self.app.show_tool_status(message)

    def stop_progress(self):
        self.app.hide_tool_status()

    def print_final_answer(self, answer: str):
        # No-op — answer already streamed via print_streaming_text
        pass

    # Suppress verbose output (thought, goal, plan, step headers)
    def print_thought(self, thought): pass
    def print_goal(self, goal): pass
    def print_plan(self, plan, current_step): pass
    def print_step_header(self, step, limit): pass
    def pretty_print_json(self, data, title): pass
```

This plugs into the existing agent system with zero changes to agent logic.

### Windows Considerations

- Textual inline mode (`app.run(inline=True)`) is **not supported on Windows**
- Full-screen TUI mode works fine on Windows Terminal, PowerShell, cmd.exe
- Windows Terminal recommended for best rendering (supports 24-bit color, Unicode)
- Legacy conhost.exe has limited Unicode support — Textual degrades gracefully

## Migration Plan

### Phase 1: Split cli.py (no framework change)

**Goal:** Break the monolith into per-command modules while keeping argparse.

1. Create `src/gaia/cli/` package
2. Move each command handler to its own file
3. Lazy-import agent code inside handler functions
4. Keep `src/gaia/cli.py` as a thin shim calling `cli/__init__.py`
5. Update `setup.py` entry points

**Validation:** `gaia --help` startup time drops to <200ms.

### Phase 2: Migrate to Cyclopts

**Goal:** Replace argparse with cyclopts for cleaner code and Rich help pages.

1. Install cyclopts: `uv pip install cyclopts`
2. Rewrite `cli/__init__.py` with `cyclopts.App()`
3. Convert each command module from argparse to cyclopts decorators
4. Enable lazy loading for all subcommands
5. Implement `GlobalOptions` dataclass for shared flags
6. Remove argparse imports

**Validation:** `gaia --help` shows Rich-formatted help. All commands work identically.

### Phase 3: Textual TUI for Chat

**Goal:** Replace the interactive chat loop with a Textual application.

1. Install textual: `uv pip install textual`
2. Create `src/gaia/tui/chat.py` with `GaiaChatApp`
3. Implement `TextualOutputHandler` (see above)
4. Wire `gaia chat` (no `--query` flag) to launch `GaiaChatApp().run()`
5. Keep `gaia chat --query "..."` as non-interactive (Rich console output)
6. Port RAG document status, tool status indicators to TUI widgets

**Validation:** `gaia chat` launches TUI. Streaming works. Tool calls show status. Code blocks are selectable.

### Phase 4: Textual TUI for Code

**Goal:** Code agent gets the same treatment.

1. Create `src/gaia/tui/code.py` with `GaiaCodeApp`
2. Add diff visualization widget for file edits
3. Add test output panel
4. Wire `gaia code` and `gaia-code` to launch TUI
5. Port step-through debugging to TUI (interactive approve/reject)

### Phase 5: MinimalConsole for Non-interactive

**Goal:** Non-interactive commands get clean, minimal output.

1. Create `MinimalConsole(OutputHandler)` in `console.py`
2. One-line spinner per tool call, streamed final answer
3. No emoji, no panels, no JSON dumps
4. Make this the default for `gaia prompt`, `gaia llm`, pipe detection
5. `--verbose` flag restores current `AgentConsole` behavior

## Dependencies

### New Dependencies

| Package | Version | Size | Purpose |
|---------|---------|------|---------|
| `cyclopts` | >=3.0 | ~50KB | CLI framework |
| `textual` | >=4.0 | ~2MB | TUI framework (includes Rich) |

### Removed Dependencies

| Package | Reason |
|---------|--------|
| `prompt_toolkit` | Replaced by Textual `Input` widget |

### Unchanged

| Package | Reason |
|---------|--------|
| `rich` | Transitive dep of both cyclopts and textual, already installed |

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Textual adds startup overhead for non-interactive commands | Medium | High | Only import textual inside interactive command handlers (lazy) |
| Windows Terminal rendering issues | Low | Medium | Textual handles Windows gracefully; test on cmd.exe, PowerShell, Windows Terminal |
| Breaking change for users scripting `gaia` output | Medium | Medium | Keep `--json` output mode unchanged; only change human-readable output |
| Cyclopts is less mature than Click/Typer | Low | Low | Cyclopts is actively maintained, API is stable, used in production projects |
| Agent backend blocks Textual event loop | Medium | High | Use `@work(thread=True)` workers; agent runs in background thread |
| 44+ commands is a large migration surface | High | Medium | Phase 1 (split) is mechanical; Phase 2 (cyclopts) is command-by-command |

## Success Metrics

| Metric | Current | Target |
|--------|---------|--------|
| `gaia --help` startup | ~1-2s | <200ms |
| `gaia chat` first prompt ready | ~3-5s | <1s (lazy model load) |
| Time to first token visible | Buffered until complete | <500ms (streaming) |
| Lines of output per agent step | 10-15 lines | 1 line (spinner) |
| User can copy code from output | Broken (copies box chars) | Clean copy |
| cli.py file size | 5,900 lines | <100 lines (shim), ~200 lines per command module |

## References

- [Cyclopts documentation](https://cyclopts.readthedocs.io/)
- [Cyclopts lazy loading](https://cyclopts.readthedocs.io/en/latest/lazy_loading.html)
- [Textual documentation](https://textual.textualize.io/)
- [Textual Markdown streaming](https://willmcgugan.github.io/streaming-markdown/)
- [Toad — universal agent TUI](https://willmcgugan.github.io/announcing-toad/)
- [Textual performance internals](https://textual.textualize.io/blog/2024/12/12/algorithms-for-high-performance-terminal-apps/)
- [PAR LLAMA — Textual LLM chat](https://github.com/paulrobello/parllama)
