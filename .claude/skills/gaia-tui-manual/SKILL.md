---
name: gaia-tui-manual
description: The GAIA terminal UI (gaia-tui) — how to build it, launch it with a real pty, drive it through the loopback control API, choose a local or cloud model provider, and read its logs. Use when running, testing, debugging, or documenting the TUI, when a preflight row blocks a launch, when driving the TUI from a script or assistant, or when a TUI question would otherwise be answered by guessing at the Go source.
---

# GAIA Terminal UI

Two manuals carry the detail. **Read the matching one before touching the
TUI** — both are written from verified runs, not from reading the source.

- **[User manual](../../../docs/guides/tui.mdx)** (`docs/guides/tui.mdx`) —
  install, the setup gate and what each row means, choosing where inference
  runs, day-to-day use, the permission prompt, troubleshooting.
- **[Developer manual](../../../docs/reference/tui-dev.mdx)**
  (`docs/reference/tui-dev.mdx`) — the three-process architecture, building,
  the full control-API reference, pty allocation, logs and traces, and running
  a local model on a dev box.

`.claude/skills/driving-the-tui/` covers the same control API from the
assistant's side; the developer manual is the reference it defers to for
endpoint shapes.

## The five things that waste the most time

Each of these cost a real debugging session. They are in the manuals too — this
is the short list worth carrying in your head.

1. **The agent subprocess outlives your edits.** `gaia-tui` spawns `gaia-agent`
   once. Change Python under `src/gaia/` or `hub/agents/` and the running
   session keeps executing the old code — a before/after measurement taken
   without a restart is two measurements of the same build. Restart the TUI.

2. **`keys` takes an array.** `{"keys": ["p"]}`. A bare string is rejected with
   `cannot unmarshal string into Go struct field keysRequest.keys of type
   []string`. `text` types but does not submit — follow with `["enter"]`.

3. **It needs a real pty.** `script` fails on a socket
   (`tcgetattr/ioctl: Operation not supported`), and driving Terminal.app via
   `osascript` can time out with `-1712` and leave nothing running. Allocate
   the pty yourself with `pty.fork()`, size it with `TIOCSWINSZ`, and drain the
   fd or the child blocks. Call `resize` first in any drive script.

4. **Wait, never sleep** — `POST /control/v1/wait` blocks server-side on
   `contains` / `absent` / `state`. But `{"state":{"streaming":false}}` can
   match the instant *before* a turn starts; confirm streaming went true first,
   or poll `status`.

5. **The "Language model" preflight row includes the embedder.** A machine with
   a working chat model still blocks on the ~300 MB
   `user.embeddinggemma-300m-GGUF`, while the row says "Several GB".
   `gaia init --check` names the exact missing id.

## Before reporting a TUI bug

- Capture the **whole** screen. A cropped capture once produced a fabricated
  "uninstall silently does nothing" — the status line is the second-to-last row.
- Check `view` in `status` before sending keys; keys sent to the wrong screen do
  nothing and read as a broken binding.
- Check the `TOOL_LOADER` lines in `~/.gaia/logs/gaia-agent.log` before
  believing the agent when it says a capability "isn't available" — it may
  simply not have been selected for that turn.
- Say which branch you built. Stacked branches are siblings; a leaf build shows
  behaviour already fixed elsewhere.
