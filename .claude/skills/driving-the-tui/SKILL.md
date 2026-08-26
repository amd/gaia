---
name: driving-the-tui
description: Use when testing or validating the GAIA TUI (tui/) by actually running it — driving screens, sending queries to an agent, verifying UX. Covers the loopback control API, waiting on state instead of sleeping, and the capture mistakes that produce false bug reports.
---

# Driving the live TUI

The TUI exposes a loopback control API so an assistant can operate it and read
what a user would see. **Use it. Never sleep.**

## Start it

```bash
/path/to/gaia --control-port 8815     # --control also works (auto-assigns)
```

Off by default: `--control` is `false`, binds `127.0.0.1` only (`control.Host`),
and is bearer-token authenticated (token in `~/.gaia/tui/control.json`, mode
0600, compared with `subtle.ConstantTimeCompare`). Verified unreachable from the
host's LAN address. Launch it in **its own Terminal window** — a long-lived
process started from a background tool call gets SIGKILLed (exit 137).

### Launch it in Windows Terminal, never bare `cmd.exe`

**A `cmd.exe` window spawned by `Start-Process` lands in legacy conhost, which
reports no colour support — and the TUI then renders with no colour and no
syntax highlighting at all.** It is not a rendering bug and it is not the
capture stripping ANSI; the process genuinely emits zero escape sequences.

The chain: `theme.Init()` and `components.PrimeRenderer()` resolve the palette
once at startup (`prepareTerminal`, `internal/ui/app.go`). `detectStyle()
(components/markdown.go)` returns `styles.NoTTYStyle` when the terminal reports
no colour, glamour then drops every chroma token, and `answerPanelStyle` paints
each code line one flat grey. Everything *looks* right except that code blocks
are monochrome — which reads as "syntax highlighting is broken".

Launch through `wt.exe` so the console is Windows Terminal (truecolor):

```bash
powershell.exe -NoProfile -Command \
  "Start-Process wt.exe -ArgumentList @('new-tab','--title','GAIA','cmd.exe','/k','<abs path>\run.bat')"
```

Two traps in that line: a `--title` containing a space breaks `wt`'s own
argument parsing (`error 0x80070002`), so keep it one word; and put the env vars
(`GAIA_TUI_HOME`, `PYTHONPATH`, `GAIA_AGENT_LOG`) inside the `.bat`, because a
new tab handed to an already-running Windows Terminal inherits *that* process's
environment, not your shell's.

To check colour rather than guess: `GET /control/v1/screen?format=ansi` and
count `\x1b`. Zero on a frame that should be styled means the profile
degraded — relaunch under `wt.exe`.

## Endpoints

`/control/v1/` — `status` · `screen` · `keys` · `text` · **`wait`** · `frames` · `resize`

## The rule: wait, don't sleep

`POST /control/v1/wait` blocks **server-side** until a condition holds:
`{"contains": "..."}`, `{"absent": "..."}`, or `{"state": {...}}` (ANDed), with
`timeout_ms`. A turn takes 12-90s depending on the model and tools; a fixed
`sleep` is either a wasted minute or a half-rendered capture. Waiting on
`absent: "streaming"` returns the instant the turn ends.

`/tmp/drive.sh` (recreate if missing — /tmp is cleared) should expose:
`keys` · `text` · `resize` · `wait <s>` · `gone <s>` · `ask <text>` · `screen [lo hi]`,
where `ask` types, submits, and blocks on `gone streaming`.

## Capture mistakes that cause false bug reports

- **Always `screen 0 999`.** A cropped capture once produced a fabricated
  "uninstall silently does nothing" — the status line is the second-to-last row.
- **`resize` FIRST** or cols/rows are 0 and everything wraps wrongly.
- **`format=plain` strips ANSI** — but `format=ansi` does not, and it is the
  fastest way to settle a colour question. Count `\x1b` in the result: a styled
  frame returns dozens (a healthy header is
  `\x1b[1;38;2;181;224;141mGAIA`), and **zero means the colour profile
  degraded at launch** — see the Windows Terminal note above, not a renderer bug.
- **Check the screen you are actually on** before sending keys. Keys sent to the
  wrong screen do nothing and read as "the binding is broken".
- `screencapture` is blocked on this machine. Use the control API text.

## Know which build you are driving

Stacked branches are siblings; no single branch contains everything. A leaf
build shows behaviour already fixed elsewhere — this produced a bug report for
something fixed on another branch. Build from a merged integration branch, or
say explicitly which slice you tested.

Similarly, `mode: user` runs the **published frozen sidecar**, which is routinely
older than source (2.4 vs 2.6). Confirm with
`gaia daemon start-agent <id> --mode dev` when testing source behaviour, and
check `api_version` in `GET /daemon/v1/agents`.

## The card/context trap

Cards are rendered by the TUI from `tool_result.render`, not by the sidecar. The
transcript pushed back as `context` must therefore carry a compact record of what
was displayed, or a follow-up referring to a visible row ("when is that one?")
resolves against nothing. See `SSEClient.appendTurn` / `displayedCard`.

## Reporting what you saw

Write results per [CLAUDE.md → How You
Communicate](../../../CLAUDE.md#how-you-communicate): open with whether the thing works, in
one plain sentence, then the captures and `file:line` detail beneath it. Say plainly which
screens you never reached — an unstated gap reads as a pass.
