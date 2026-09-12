---
name: driving-the-tui
description: Use when testing or validating the GAIA TUI (tui/) by actually running it — driving screens, clicking and scrolling with the mouse, sending queries to an agent, capturing screenshots and video of a session, and reading its diagnostics, logs and traces. Covers the loopback control API end to end, running a session isolated from the user's own, waiting on state instead of sleeping, and the false-pass traps that make a broken build look tested.
---

# Driving the live TUI

The TUI exposes a loopback control API so an assistant can operate it and read
what a user would see. **Use it. Never sleep.**

## Isolate memory before you start it — every time

**Set `GAIA_MEMORY_DB` to a throwaway file in every drive.** The agent behind the
TUI writes to the user's real `~/.gaia/memory.db` by default, and anything you say
while driving becomes a permanent fact about the user:

```bash
export GAIA_MEMORY_DB=/tmp/gaia-drive/memory.db     # delete between runs
```

A drive once planted a persona's overdue deadline; days later the user said
"sweet!" and got *"Priya needs that Fernbrook deck ASAP."* back. The false bug
reports this skill exists to prevent have a mirror image — a real report caused
by a test.

`gaia eval agent` already resets memory between scenarios; a hand-driven session
has no such cleanup, so isolation has to come from the environment. A blank value,
or one naming a directory, is a startup error rather than a fall back to the real
store — if the agent refuses to start, fix the path, don't unset the variable.
`GAIA_HOME` selects `$GAIA_HOME/memory.db` when `GAIA_MEMORY_DB` is unset; it
does not relocate config, logs, or every other `~/.gaia` path. Config uses
`GAIA_CONFIG_DIR`. Use a separate OS user or container for complete isolation.

## Run it beside the user's own session, not through it

The daemon is machine-wide and the sidecar is shared. A TUI launched in dev mode
hands that daemon **its own** source directory, so driving a checkout can swap
the agent out from under a session the user is in the middle of using. Give the
drive its own daemon instead:

```bash
export GAIA_DAEMON_HOME=/tmp/gaia-drive/daemon   # own daemon + own sidecar
export GAIA_TUI_HOME=/tmp/gaia-drive/tui         # own control.json + token
export GAIA_MEMORY_DB=/tmp/gaia-drive/memory.db  # see above
```

With those set, `ps aux | grep gaia-tui` before and after should show the user's
processes untouched. The readiness gate still needs `gaia-agent` on `PATH`; a
one-line shim that execs `python -m gaia_agent.server` from the checkout under
test is enough, and is how you pin *which* source the drive exercises.

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

`/control/v1/` — `status` · `screen` · `keys` · **`mouse`** · `text` · **`wait`** ·
`frames` · **`recording`** · `resize`

| Endpoint | What it is for |
|---|---|
| `GET status` | Where the session is, plus `state.chat` diagnostics (below) |
| `GET screen?format=plain\|ansi\|svg` | The frame as text, as styled text, or as a picture |
| `POST keys` | `{"keys": ["ctrl+t", "enter"]}` |
| `POST mouse` | `{"events": [{"action": "click", "x": 24, "y": 16}]}` |
| `POST text` | Type into the composer |
| `POST wait` | Block until a condition holds — never sleep |
| `GET frames` | The raw frame history as JSON |
| `GET recording?limit=N` | That history as one looping animated SVG |
| `POST resize` | Set the terminal size (do this FIRST) |

`view` is one of `splash` · `preflight` · `chat` · `unknown` — there is no hub
screen to navigate, since `gaia-tui` boots straight into one agent's chat behind
a readiness gate. On `preflight`, `blocker` names the row refusing the launch
(`binary`, `lemonade`, `model`, `daemon`, `sidecar`, `mailbox`); wait on
`{"state": {"view": "chat"}}` rather than a screen substring. `esc` quits on
`preflight` (no screen behind it) and clears the composer on an idle `chat`.

## Proof: screenshots and video

`screencapture` is blocked on this machine, and a plain-text dump cannot show
colour, alignment, or which cell a link sits in. The control API renders the
frames itself instead.

```bash
# a still — a real picture of the current frame
curl -sH "Authorization: Bearer $TOK" \
  "http://127.0.0.1:$PORT/control/v1/screen?format=svg" -o shot.svg

# the session so far, replayed at the speed it happened, looping
curl -sH "Authorization: Bearer $TOK" \
  "http://127.0.0.1:$PORT/control/v1/recording?limit=45" -o session.svg
```

Both return the SVG document itself, not JSON — write it straight to a file.
SVG rather than PNG so there is no font to ship and no rasteriser to depend on;
every box-drawing glyph and emoji renders because the viewer draws it.

There is **no start/stop for recording**: the state keeps every frame it draws
with the millisecond it was drawn, so the interesting moment is already captured
by the time you realise it was interesting. `limit` picks how many of the most
recent frames to include (the ring holds 200). `since` skips ahead — take
`latest_seq` from `GET frames` before an action, then pass it as `since`
afterwards to get a clip of just that action.

Send them with `SendUserFile`. A still is ~8KB; a 45-frame clip is ~270KB.

## Mouse

```bash
curl -sX POST -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d '{"events":[{"action":"wheel_up","x":40,"y":10}]}' \
  "http://127.0.0.1:$PORT/control/v1/mouse"
```

Actions: `wheel_up` · `wheel_down` · `click` · `double_click` · `right_click` ·
`move`. Coordinates are absolute screen cells in the same frame `/screen`
reports, so the way to click something is to read the screen, find the row and
column, and click that:

```bash
curl -sH "Authorization: Bearer $TOK" "…/screen?format=plain" | python3 -c "
import sys,json,re
for i,l in enumerate(json.load(sys.stdin)['screen'].splitlines()):
    m = re.search(r'https://\S+', l)
    if m: print(i, (m.start()+m.end())//2); break"
```

**A double click is two presses, not a distinct event** — that is what a
terminal sends and what the app has to recognise.

**Injection bypasses the terminal's mouse gate.** Events go straight into the
program's message queue, so they arrive even when the app has *released* the
mouse and a real user's click would reach nothing. The endpoint refuses with
`mouse_not_captured` while `state.chat.mouse_owner` is `terminal`, precisely so
this cannot report a pass for a gesture nobody can make. If you need to prove
what the terminal is doing, read the escape sequences off the pty (below).

## Diagnostics: `state.chat`

`GET status` carries the transcript's own state, and it is what tells a real
defect from a mis-aimed test:

```json
{"scroll_y": 8, "content_rows": 56, "at_bottom": false, "follow_tail": false,
 "mouse_owner": "app", "mouse_motion": "cell", "select_mode": false,
 "viewport_rows": 33, "header_rows": 2, "help_open": false, "messages": 6}
```

- **`scroll_y` / `at_bottom` / `follow_tail`** — "I scrolled and nothing moved"
  is indistinguishable from "I was already at the top" without these. A scroll
  that registered moves `scroll_y` and clears `follow_tail`.
- **`content_rows` vs `viewport_rows`** — when content fits the window there is
  *nothing to scroll*, and a scroll test there passes vacuously. Check this
  before concluding the wheel works; fill the transcript first if it does not.
- **`mouse_owner` / `select_mode`** — "my click did nothing" versus "the app
  never had the mouse".
- **`header_rows`** — how many screen rows sit above the transcript. Add it to a
  content row to get a screen row to click.

## What the control API cannot see: the pty

Whether the app asked the *terminal* for the mouse is not model state — it is
bytes on the wire. Run the TUI under a pty with a real size and read them:

```python
pid, fd = pty.fork()                      # child: os.execvpe(...)
fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 130, 0, 0))
```

A `script`-spawned pty has size 0x0, Bubble Tea then lays nothing out, and
`resize` refuses to grow past it — which reads as "the TUI is broken" when it is
the harness. Then grep the captured stream:

| Sequence | Means |
|---|---|
| `\e[?1049h` | Alt screen entered |
| `\e[?1002h` / `\e[?1002l` | App took / released the mouse (cell-motion) |
| `\e[?1003h` | All-motion — an overlay wants hover |
| `\e[?1006h` | SGR extended coordinates |
| `\e]52;c;<base64>` | A clipboard write (Ctrl+Y, Ctrl+B, double-click copy) |
| `\e]8;;<uri>\e\\` | A hyperlink the terminal should make clickable |

Decode an OSC 52 payload to prove *what* was copied, not merely that something
was. A click that should open a browser is best checked by putting a stub
`open` (or `xdg-open`) earlier on `PATH` that appends its argument to a file —
you get the exact URL, and no browser windows.

## Logs, traces, and errors

- `GAIA_AGENT_LOG=<path>` — the agent's own log for the session.
- `--trace=<path.jsonl>` — what the agent actually did, step by step.
- `--dev` — puts the model chip, step count and the per-turn footnote
  (`elapsed · ttft · tokens · tok/s · steps`) on screen, and routes agent
  logging to DEBUG.
- A failed action surfaces as a status line in the transcript rather than a
  crash, so `screen?format=plain` is where an error shows up. Read it before
  concluding a keystroke did nothing.

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
- **A test that cannot fail is not a pass.** Scrolling a transcript that fits
  the window, clicking where no link is, waiting on a condition already true —
  each returns success having proved nothing. Assert the precondition first
  (`content_rows > viewport_rows`), and where you can, run the negative case
  too: click one column off the link and confirm nothing opens.
- **Prove the gate, not just the happy path.** A click that works is half the
  story; the other half is that it does *not* work where it must not — under an
  open `/help` panel, or on prose. Both of those were real defects here, and
  both passed every unit test before a live drive caught them.
- `screencapture` is blocked on this machine. Use `screen?format=svg`.

## Know which build you are driving

Stacked branches are siblings; no single branch contains everything. A leaf
build shows behaviour already fixed elsewhere — this produced a bug report for
something fixed on another branch. Build from a merged integration branch, or
say explicitly which slice you tested.

Similarly, `mode: user` runs the **published frozen sidecar**, which is routinely
older than source (2.4 vs 2.6). When testing an agent from a checkout, set the
same mode on the TUI launch so its ensure request agrees with the sidecar:

```bash
GAIA_EMAIL_AGENT_MODE=dev /path/to/gaia-tui --control-port 8815
```

Use `GAIA_GAIA_AGENT_MODE=dev` for the flagship `gaia` agent. The TUI resolves
the caller checkout and sends the per-agent source directory to the daemon;
starting a dev sidecar separately while the TUI still defaults to `user`
creates a mode conflict. Check `api_version` in `GET /daemon/v1/agents`.