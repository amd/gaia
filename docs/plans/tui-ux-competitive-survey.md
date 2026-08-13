# Agent TUI UX survey — what GAIA's TUI is missing

**Date:** 2026-08-12
**Scope:** interaction and presentation features in leading terminal AI-agent UIs, ranked by
value ÷ implementation cost for GAIA's Go + Bubble Tea v1.3.10 TUI talking to a local
Lemonade model over SSE.

**Method.** Six parallel research agents reading primary sources — source trees via the
GitHub API, official docs, CHANGELOGs, release notes, and issue trackers sorted by
reactions. Five completed; the sixth (a Go-library capability inventory) was interrupted.
See [What could not be verified](#7-what-could-not-be-verified) before acting on anything
here.

**Agents studied:** Claude Code, OpenAI Codex CLI, opencode, Charm Crush, Gemini CLI,
Aider, Goose, toad, Hermes.

---

## 1. Four findings that reframe the rest

### Crush is no longer portable as code

Its `go.mod` is `charm.land/bubbletea/v2 v2.0.8` + `lipgloss/v2` + `glamour/v2` +
`charmbracelet/ultraviolet` (a screen-buffer compositor, still on a pseudo-version). The
tree moved to `internal/ui/` in the `CRUSH_NEW_UI` rewrite
([PR #1652](https://github.com/charmbracelet/crush/pull/1652)) — the commonly-cited paths
`internal/tui/exp/diffview` and `internal/tui/exp/anim` do not exist.

**Bubble Tea v1 ended at v1.3.10, 2025-09-17** — our version, with ~11 months of no
upstream work. Crush is still the highest-value target, but as *techniques to port*, not
packages to import. Two of its files port with near-zero effort anyway (see
[recommendation 10](#10-vendor-crushs-animgo--timergo-and-ship-reduced-motion)).

`charm.land/*` is a vanity import path, not a new project —
`charm.land/bubbletea/v2` and `github.com/charmbracelet/bubbletea/v2` are the same module
at the same version.

### Three projects independently invented the same streaming-markdown fix

Aider's `mdstream.py`, Gemini CLI's `findLastSafeSplitPoint`, and Crush's
`findSafeMarkdownBoundary` all split the stream into a provably-stable prefix rendered
once and an unstable tail repainted per tick. Three teams, three languages, one answer.
Details in [recommendation 5](#5-fix-streaming-markdown-cost-before-it-bites).

### Copy/paste is the #1 complaint everywhere — and a renderer rewrite does not fix it

opencode replaced Bubble Tea with a Zig renderer and copy/paste is *still* their loudest
thread ([#4283](https://github.com/anomalyco/opencode/issues/4283), 110 👍, **122
comments**; refiled as [#13984](https://github.com/anomalyco/opencode/issues/13984), 55
comments, still unresolved). The cause is grabbing the mouse and reimplementing selection
— a design decision, not a framework limitation.

### "Hermes" is a real agent TUI, and toad does not do what it's reputed to

[`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent) is a Python
runtime with a React/Ink front-end over a JSON-RPC `tui_gateway`. Its own tracker groups
it with toad. *Caveat: the subagent reported 229k stars / 31k open issues, which was not
re-verified and is implausibly large. Treat existence as likely, metrics as suspect.*

**Correction on toad:** it does **not** avoid the alternate screen. It is a conventional
full-screen Textual app that reimplements scrollback internally (`VerticalScroll` widget +
`ui.prune_low_mark = 1500`). Proof chain: `app.run()` is called with no arguments
(`src/toad/cli.py:162`), Textual 8.2.7 defaults `inline=False`, and the standard Linux
driver writes `\x1b[?1049h`. McGugan's argument was never "don't take the screen" — it was
"don't do line-erase-and-rewrite in the normal buffer."

---

## 2. Top 10 recommendations

### 1. Turn-completion notification + live terminal title

**What.** Ring the bell / fire an OSC 9 desktop notification when a turn ends, and keep the
terminal *title* updated with live status so the tab label shows progress.

**Who does it well.**

- **Codex** — `tui.notifications`, `notification_method = auto|osc9|bel`,
  `notification_condition = unfocused|always`. Its `/title` config (`tui.terminal_title`,
  default `["spinner","project"]`) uses a deliberately *smaller* status vocabulary than
  the in-app header — `Working | WaitingForBackgroundTerminal | Thinking` — because "the
  title needs short, stable labels."
- **Claude Code** — desktop notification by default only in Ghostty/Kitty/iTerm2, else
  `preferredNotifChannel: "terminal_bell"`. In screen-reader mode it also rings when *any
  tool exceeding 5 seconds* finishes.
- **Goose** — sets OSC 0 to `🪿 <dirname>` and **strips control chars first to prevent
  escape injection**. Copy that detail.

**Why for a 60–120 s turn.** This is the whole ballgame. At two minutes the user tabs away
every single time. Nothing else on this list changes the felt experience as much per line
of code.

**Bubble Tea cost.** Trivial. `tea.SetWindowTitle(string) Cmd` should exist in v1 (verify
against 1.3.10). OSC 9 is a raw escape write.

**Gotcha.** tmux needs `set -g allow-passthrough on` or neither the notification nor the
title reaches the outer terminal.

---

### 2. Follow-state as an explicit mode, with a "↓ N new" affordance

**What.** A real `follow bool`, set false on any user scroll, set true only by an explicit
key. Never inferred from "am I at the bottom?"

**Who does it well.** **Claude Code** fullscreen: scrolling up pauses auto-follow, a
floating `Jump to bottom` button appears showing `3 new messages`, `Ctrl+End` re-arms —
and the button's keyboard hint *adapts to what the keyboard can actually send* (on a
MacBook where `Ctrl+End` can't reach the app it suggests `Fn+↓`). Permission prompts scroll
into view regardless of the setting.

**Why.** This is Crush's #1 recurring bug class — ~10 issues over 10 months that **survived
a full UI rewrite**:

- [#2481](https://github.com/charmbracelet/crush/issues/2481) — auto-follow permanently
  stops; scrolling back down does not re-arm it. Window resize triggers it.
- [#2195](https://github.com/charmbracelet/crush/issues/2195) — a user kept re-prompting
  because a mouse wheel during streaming had silently teleported them to an old part of
  the conversation.
- [#2770](https://github.com/charmbracelet/crush/issues/2770) (open) — forced auto-scroll
  causes "deep mind fatigue."

Aider [#4332](https://github.com/Aider-AI/aider/issues/4332) states the requirement best:

> I want to review the output as fast as my brain works, while the LM runs ahead at its
> speed, allowing us to work concurrently.

At 60–120 s that is not a nice-to-have — it is the only way to use the time.

**Bubble Tea cost.** Low–medium. The trap is recomputing follow from offsets after a resize
or an expand/collapse. Don't.

---

### 3. Post-hoc expand/collapse — as an outcome *policy*, not a boolean

**What.** Truncate tool output aggressively in the main view, but let the user expand any
completed step later. Default the auto-expansion by *outcome*.

**Who does it well.**

- **toad** has the best design: `tools.expand ∈ Never / Always / Success only / Fail only /
  Fail and success`, **default `fail`**, plus a hard-coded carve-out in source: *"Don't
  auto expand reads, as it can generate a lot of noise."* Header shows `▼`/`▶`, dimmed to
  30 % opacity when there's nothing to expand; `space` on the focused block or click the
  header. Status affordance: `⌛` pending, red `failed` pill, green `✔`.
- **Crush** caps output at `responseContextHeight = 10` with a `+N lines` footer, and has a
  `Compactable` interface (`SetCompact(bool)`) that renders a tool as a single-line header.
- **Claude Code** fullscreen: click to expand, and "Only messages that have more to show
  are clickable."

**Why.** On a slow local model the tool log *is* the content. Expand-failures /
collapse-successes is exactly the right default, and post-hoc expansion is what lets you
truncate hard without hiding anything.

**Bubble Tea cost.** Medium — you need a block cursor in the transcript. Keyboard-first
(`j`/`k` or `tab` to move, `space` to toggle) is much cheaper than mouse, and is what Crush
and toad both ship. For mouse, `lrstanley/bubblezone` (v1-compatible — verify); note Crush
deliberately does *not* use it and hand-rolls `list.ItemIndexAtPosition(x,y)`.

---

### 4. Keep the raw markdown source alongside every rendered block — and copy *that*

**What.** Thread the pre-Glamour string through the message struct. Copy operations read
the source, never the rendered cells.

**Who does it well / evidence.**

Crush [#2853](https://github.com/charmbracelet/crush/issues/2853), maintainer verbatim:

> Due to the way glamour parses the markdown and then renders it it's currently quite
> tricky to reverse the process. I'm not sure there honestly is a good way apart from
> completely rewriting the rendering pipeline.

Claude Code's `/btw` overlay documents the same lesson in its own help text: `c` copies
*"as raw Markdown. Use this instead of mouse selection, which captures the hard-wrapped
terminal rendering rather than the source text."* Its `/copy [N]` shows **an interactive
picker to select individual code blocks or the full response**, and `w` writes to a file
instead of the clipboard — "useful over SSH."

Codex shipped a whole `/raw` mode (Alt+R, `tui.raw_output_mode`) that flips the transcript
to unbroken lines *"so terminal selection copies their source faithfully."*

**toad's unique idea, worth stealing outright: "copy to prompt."** A block action that
appends the block's *source* into the prompt editor and refocuses it. Nobody else has it,
and it is the best interaction for iterating on something the agent just produced.

**Why.** #1/#2 complaint in every tracker surveyed (Claude Code
[#18170](https://github.com/anthropics/claude-code/issues/18170), 284 👍). And GAIA is a
general assistant — lifting the answer out is the primary use.

**Bubble Tea cost.** Near-zero *now*, intractable later. **This is the one decision on this
list that cannot be deferred.** For SSH, copy Codex's backend cascade: over SSH prefer tmux
passthrough then OSC 52 (cap the payload — they use 100 KB); locally try the native
clipboard first.

---

### 5. Fix streaming-markdown cost before it bites

Three parts, ordered by cost:

| Part | Detail | Cost |
|---|---|---|
| **Per-width Glamour renderer cache + mutex** | `glamour.TermRenderer` is **not safe for concurrent `Render` calls** — goldmark's `BlockStack` carries state across the public API. Crush memoizes `mdCache map[int]*glamour.TermRenderer` and holds a per-renderer mutex keyed by pointer. Invalidate both maps atomically on theme change. | trivial — do regardless |
| **Adaptive throttle** | Aider: `min_delay = clamp(render_time × 10, 1/20 s, 2 s)`. As the message grows and re-rendering gets expensive, the refresh rate automatically backs off. ~5 lines. | trivial |
| **Stable-prefix cache** | Render only the unstable tail per tick. Aider uses a fixed 6-line window; Gemini's `findLastSafeSplitPoint` (last `\n\n` outside a code fence) is the more correct rule — a fixed window *will* cut a fence in half. Crush's `findSafeMarkdownBoundary` also proves no list/table/blockquote/setext is open, with incremental `O(delta)` re-validation. | medium |

The warning Crush embeds in source:

> Two renders concatenated are NOT generally equal to a single render of the whole
> document — glamour's wrap state is reset between calls. The boundary check is therefore
> deliberately conservative; whenever it has the slightest doubt the call falls back to a
> full render.

**Why.** Only applies if Glamour re-renders per stream tick — but if it does, the cost is
quadratic over a 60–120 s turn. Crush
[#2918](https://github.com/charmbracelet/crush/issues/2918) is the failure mode:
`renderThinking` called `Render(fullThinking)` *before* the collapsed check and cleared the
cache every tick, producing high CPU/RAM on long reasoning traces.

**Also read:** McGugan's [Efficient streaming of Markdown in the
terminal](https://willmcgugan.github.io/streaming-markdown/) — block finalization,
in-place widget update, incremental parsing from the last block's start line (keeps parse
time under 1 ms regardless of document length), and buffering between token source and
widget.

---

### 6. A transcript pager with search — and truncation notices that point at it

**What.** A `Ctrl+T`-style overlay over the full transcript with `/` search, plus every
ellipsis telling you how to get there.

**Who does it well.**

- **Codex** — `… +{omitted} lines (ctrl + t to view transcript)`. **The truncation notice
  names its own escape hatch.** The pager renders committed cells *plus a live tail* of the
  in-flight cell, cached on `(width, active-cell revision, stream-continuation, animation
  tick)`.
- **Claude Code** — `less`-like: `/` search, `n`/`N`, `j`/`k`, `g`/`G`, `{`/`}` to jump
  between prompts, `Ctrl+U`/`Ctrl+D`, `?` for help. **Two escape hatches worth more than
  the pager itself:** `[` dumps the whole conversation into the terminal's *native*
  scrollback with tool output expanded (so `Cmd+F` and tmux copy-mode work), and `v` writes
  it to a temp file and opens `$EDITOR`.

**Do `v` → `$EDITOR` first.** It is ~30 lines and buys search, selection, and copy for
free. Crush's maintainers give the same answer to "I want a real pager"
([#2952](https://github.com/charmbracelet/crush/issues/2952),
[#426](https://github.com/charmbracelet/crush/issues/426)). opencode
[#4714](https://github.com/anomalyco/opencode/issues/4714) (44 👍) is an open
"no find-in-scrollback" complaint.

**Bubble Tea cost.** Medium for the pager; trivial for `$EDITOR`. Nobody ships a reusable
in-transcript-search component — lazygit, k9s, and gh dash all hand-roll it. Use
`bubbles/viewport` + a match index.

---

### 7. A priority-ordered, width-fitting status line with context %

**What.** Each item declares a data width; walk them in priority order, keep the
high-priority ones unconditionally, drop the rest when the budget runs out, append `…`.

**Who does it well.**

- **Gemini CLI**'s `Footer.tsx` does exactly that; `workspace` gets the remainder (min 20)
  and is the only shrinkable column. Items: workspace, git-branch, sandbox, model-name,
  context-used, quota, memory-usage, session-id, hostname, auth, code-changes (`+N -M`),
  token-count.
- **Codex** has 26 named status-line items, a documented default of 3, and this rule:
  **unavailable items are omitted, not placeholdered, "so the line remains compact and
  stable."** Its collapse cascade is explicit about priority — in queue mode it drops the
  context indicator *before* the queue hint.
- **Hermes** uses three width tiers (≥76 / 52–75 / <52 cols) and caches git branch on mtime
  **so it updates when you checkout in another terminal**.
- Colour-coded context fill is near-universal: Goose green <50 / yellow <85 / red ≥85;
  Hermes green <50 / yellow <80 / orange <95 / red ≥95; Crush prepends a warning icon
  at >80 %.

**Why for GAIA.** We have a status bar. The delta is the width cascade plus a context
indicator — and on a local model with `NPU_CTX_SIZE = 32768` pinned, "how much room is
left" is *more* actionable than for a cloud model, because the wall arrives far sooner.

**Bubble Tea cost.** Low. String assembly + `ansi.StringWidth`.

---

### 8. Steer vs queue, editable queued messages, mid-turn command availability

Three separable pieces beyond our single queued follow-up.

**Who does it well.**

**Codex** draws the sharpest line: **Enter while working = *steer*** (injected into the
running turn); **Tab while working = *queue*** (runs as the next turn). Footer switches to
`Tab to queue message`, collapsing to `Tab to queue` on narrow terminals. `Alt+Up` /
`Shift+Left` edits the queued message. Its state is four collections, including
`rejected_steers_queue` for steers that hit a non-steerable turn and must be retried first.

**Gemini CLI** shows up to 3 dim truncated previews headed `Queued (press ↑ to edit):` then
`... (+N more)`, and flushes them **joined with `\n\n` as one combined prompt**.

**The cheapest high-value piece is the third one.** Codex declares three orthogonal bits
per slash command — `supports_inline_args()`, `available_during_task()`,
`available_in_side_conversation()` — so the popup **greys out what can't run right now
instead of failing after Enter**. Claude Code has the same idea as a documented exemption
list (`/status`, `/usage`, `/tasks`, `/btw`, `/model`, `/effort` run immediately mid-turn;
everything else queues).

Codex's slash-command popup is also deliberately **not alpha-sorted**. From source:
`DO NOT ALPHA-SORT! Enum order is presentation order in the popup, so more frequently used
commands should be listed first.`

**Why.** At 120 s the user *will* think of something mid-turn. Being able to see, edit, and
cancel the queued message is the difference between a feature and a trap. Claude Code's
[#50246](https://github.com/anthropics/claude-code/issues/50246) (223 reactions) is people
asking for exactly this.

**Bubble Tea cost.** Visible/editable queue + command availability bits: low. Steering
depends entirely on the SSE contract.

---

### 9. Approvals: three actions, session-scoped allow, always-visible mode, unoverridable floor

**Three actions, not two.** Crush: `allow` / `allow_session` / `deny`, navigated with
`←`/`→`/`tab`. Gemini's edit dialog adds a fourth axis: `Allow once` / `Allow for this
session` / `Allow for this file in all future sessions` / `Modify with external editor` /
`No, suggest changes (esc)`.

**Surface the mode redundantly.** Claude Code: badge strings `⏸ manual mode on` /
`⏵⏵ accept edits on` / `⏵⏵ bypass permissions on`, **plus the input-box border colour
changes per mode**. Crush surfaces YOLO in four places at once — flag, `ctrl+y`, palette
entry, and the editor gutter becomes a warning icon with the placeholder reading
`"Yolo mode!"`. Gemini renders YOLO in **error red**.

**The Windows fallback.** Claude Code binds mode-cycle to `Alt+M` instead of `Shift+Tab`
where the runtime doesn't enable VT input mode. Copy that.

**The best idea, from Hermes:** an `UNRECOVERABLE_BLOCKLIST` that sits *below* YOLO **with
no override flag** — `rm -rf /`, `--no-preserve-root`, fork bombs, `mkfs.*` on mounted
root, `dd if=/dev/zero of=/dev/sd*`, piping untrusted URLs to `sh` — plus a user-editable
deny-glob list that also pre-empts YOLO. Aider has a smaller version of the same instinct:
`--yes-always` maps to `"y"` **except where `explicit_yes_required` is set, which becomes
`"n"` — so shell commands are never auto-run by `--yes-always`.**

**Size the dialog to its content.** Crush: diff-carrying dialogs get 80 % of the window
(max 180 cols), simple prompts 60 % × 50 % (max 100), and below 77 × 20 it forces
fullscreen.

**Trap.** Crush [#149](https://github.com/charmbracelet/crush/issues/149) /
[#1405](https://github.com/charmbracelet/crush/issues/1405) — modal dialogs cover the
transcript, so **users approve blind**. Retrofitting a hide required
`interface{ IsVisible() }` type assertions the contributor himself called *"an ugly
hack."* Design dialogs as hideable from day one. Also
[#2397](https://github.com/charmbracelet/crush/issues/2397): the buttons *look* clickable
but mouse events aren't routed to them.

**Bubble Tea cost.** Low–medium. `bubbles/key` + a small dialog model.

---

### 10. Vendor Crush's `anim.go` + `timer.go`, and ship reduced-motion

[`internal/ui/anim/anim.go`](https://raw.githubusercontent.com/charmbracelet/crush/main/internal/ui/anim/anim.go)
is 566 lines whose only dependencies are `lipgloss.Foreground` and `tea.Tick` — **it
compiles against v1 essentially as-is.** What you get:

- 20 fps; gradient ramp built with `BlendHcl` (HCL specifically "to stay in gamut")
- **Every frame's every column pre-rendered through Lip Gloss into `[][]string` at
  construction**, so `Render()` is a lookup-table walk. Frames cached globally, keyed by
  `xxh3(Settings)` — two spinners with identical settings share them.
- Staggered per-column fade-in seeded from `xxh3(id + settingsHash)`, so different messages
  shimmer differently but *deterministically* (their stated reason: byte-stable golden
  tests)
- `Suffix func() string` — how the elapsed timer gets in without the anim knowing about
  time. When a suffix is present the animated ellipsis is suppressed "to avoid visual
  competition between the animated dots and the timer."
- `NoScramble` mode "for non-LLM contexts where scrambled glyphs imply 'thinking' rather
  than 'running'."

**Steal the generation counter specifically.** `Start()` bumps `gen`; `Animate()` drops any
`StepMsg` whose `Gen` doesn't match. The source comment names the bug: re-arming a spinner
that still had a live tick chain *"would run two chains concurrently and render a doubled
animation."* You will hit this.

`common/timer.go` is 52 lines — copy verbatim. **Codex's addition: pause the timer while an
approval dialog is up** (`pause_timer`/`resume_timer`) so blocked time doesn't inflate the
number.

**Reduced motion is not optional at our turn length.** Crush
[#1147](https://github.com/charmbracelet/crush/issues/1147): the spinner alone cost
**~3 Mbps of SSH traffic and 100 % CPU** — closed NOT_PLANNED, which is the wrong answer.
Codex's `tui.animations = false` degrades correctly: plain text, and the leading activity
bullet **disappears entirely rather than freezing**. Claude Code has
`prefersReducedMotion`; Gemini has `ui.showSpinner`.

---

### Honourable mentions

Cheap, and skipped the top 10 only on ranking.

| Item | Detail |
|---|---|
| **`$EDITOR` escape hatch** | Crush's `openEditor` is ~30 lines: `os.CreateTemp` → `charmbracelet/x/editor`.`Command()` (resolves `$EDITOR`/`$VISUAL` *and* per-editor cursor-position flags) → `tea.ExecProcess` → read back. **v1-portable.** Bound to `Ctrl+O` in Crush, `Ctrl+G` in Codex and Gemini. |
| **Row-aware middle-out truncation** | Codex wraps output *first*, measures each line's row cost via `Paragraph::line_count(width)`, then splits head/tail budgets. Rationale in-source: *"a single logical line containing a long URL wraps to several viewport rows"* and *"a small number of very long lines cannot flood the viewport."* Naive line-count truncation gets this wrong. |
| **Large-paste placeholder** | Claude Code's exact rule: >800 chars **or** >2 lines → `[Pasted text #1 +120 lines]`, cached under `~/.claude/paste-cache/`. Gemini adds `Ctrl+O` to expand it in place. See anti-pattern 1 — ship the expand affordance in the same commit as the collapse. |
| **Aider's `/tokens`** | The best status output surveyed: a cost-attributed context breakdown, sorted by size, where **every row carries a remediation hint** (`use /clear to clear`, `/drop to remove`) and the "remaining" line escalates info → error → "window exhausted". |
| **Session export** | Alt-screen debt: the transcript dies on exit. Goose has `session export` (JSON/Markdown), opencode has `/export`, Claude Code has `/export`. **Crush has none, and it is a repeated complaint.** |
| **Accessibility** | Claude Code's is thorough and cheap to imitate: `--ax-screen-reader` prints a banner then **holds the UI back for 3 s** so the reader can finish; flat text, no box drawing, static spinners, tables read as `Header: value`, line labels `you:` / `claude:` / `tool:` / `Permission Required:`, and **menus become numbered lists**. Plus `NO_COLOR`/`--no-color` and daltonized themes. toad's diff view ships `+`/`-` annotations *"to improve readability for color blind users"* — a one-line change. |

---

## 3. Notable technique: how Codex keeps native scrollback

Worth recording because it is the one genuinely different architecture in the survey.

Codex writes finalized history into the terminal's **real** scrollback. From
`insert_history.rs`:

> Codex uses the terminal scrollback itself for finalized chat history, so inserting a
> history cell is an escape-sequence operation rather than a normal ratatui render.

Mechanism per insertion:

1. Set scroll region to `1 .. viewport_top` (`SetScrollRegion`)
2. `MoveTo(0, viewport_top - 1)`, then for each wrapped line emit `\r\n` + styled spans —
   lines scroll off the *top* of that region into native scrollback
3. `ResetScrollRegion`, restore cursor — the operation is deliberately cursor-neutral
4. The whole draw is wrapped in `crossterm::SynchronizedUpdate` (DECSET 2026)

**A repo-wide search for `EnableMouseCapture` returns zero hits.** That single decision is
why native selection keeps working.

**The price.** Terminal scrollback is not a retained widget tree, so a width resize must
*rebuild* it from source: 75 ms debounce, in-memory cells as source of truth, and a
per-emulator row cap (VS Code 1000, WezTerm 3500, Windows Terminal 9001, Alacritty 10000).
Even with all that, resize reflow is a recognisable bug family in their tracker, and there
are ~25 open emulator-specific scrollback issues. See anti-pattern 13.

---

## 4. Comparison table

Legend: ● has it · ◐ partial / config-gated · ○ absent · ? unverified

| Feature | Claude Code | Codex | opencode | Crush | Gemini CLI | Aider | Goose | toad | Hermes | **GAIA now** |
|---|---|---|---|---|---|---|---|---|---|---|
| **Rendering** |
| Native scrollback (no alt-screen for chat) | ◐ classic only | ● scroll-region+RI | ○ | ○ | ◐ non-alt default | ● | ● | ○ | ○ | ? |
| Mouse capture (costs native selection) | ◐ fullscreen | **○ zero calls** | ● | ● | ◐ `Ctrl+S` | ○ | ○ | ● | ● | ? |
| Stable-prefix streaming optimization | ? | ● line-queue | ? | ● | ● | ● | ◐ | ● | ? | ○ |
| Adaptive throttle / catch-up | ? | ● hysteresis | ? | ○ | ○ | ● | ○ | ● buffered | ? | ○ |
| Reduced-motion / animations off | ● | ● | ? | ○ | ● | ● `--no-pretty` | ● env | ? | ? | ○ |
| **Transcript** |
| Post-hoc expand of a completed step | ● click | **○** Ctrl+T only | ? | ● `space` | ● `Ctrl+O` | ○ | ◐ `/r` global | ● | ● `/details` | ○ |
| Expand policy by outcome | ○ | ○ | ○ | ○ | ○ | ○ | ○ | **●** | ◐ | ○ |
| In-transcript search | ● `/` `n`/`N` | ◐ pager only | **○** #4714 | ○ | ○ | ○ | ○ | ? | ? | ○ |
| Transcript pager overlay | ● | ● Ctrl+T | ? | ○ | ○ | ○ | ○ | n/a | ? | ○ |
| Dump transcript to native scrollback | ● `[` | ○ | ○ | ○ | ○ | n/a | ○ | ○ | ○ | ○ |
| Export conversation | ● `/export` | ○ | ● `/export` | **○** | ● `/chat share` | ◐ history.md | ● JSON/MD | ◐ SVG/copy | ● | ? |
| **Copy** |
| Copies raw source, not rendered | ● `/copy`,`c` | ● `/raw` mode | ○ | ◐ msg only | ? | ● `/copy` | ? | ● | ? | ○ |
| Code-block picker on copy | ● + `w`→file | ○ | ○ | ○ | ○ | ○ | ○ | ● block menu | ? | ○ |
| **Copy-to-prompt** | ○ | ○ | ○ | ○ | ○ | ○ | ○ | **●** | ○ | ○ |
| OSC 52 for SSH | ● | ● capped | ? | ◐ buggy | ◐ opt-in | ○ | ? | ● | ? | ○ |
| **Input** |
| Queue while busy | ◐ cmds only | ● `Tab` | ? | ● | ● `Tab` | ○ | ○ | ? | ? | ● 1 msg |
| Edit/cancel queued message | ? | ● `Alt+↑` | ? | ? | ● `↑` | n/a | n/a | ? | ? | ○ |
| **Steer into running turn** | ○ | **● `Enter`** | ○ | ○ | ○ | ○ | ○ | ○ | ○ | ○ |
| Per-command mid-turn availability | ● list | ● 3 bits | ? | ? | ? | ○ | ○ | ? | ? | ○ |
| Slash menu + fuzzy filter | ● | ● non-alpha | ● `ctrl+p` | ● `ctrl+p` | ● | ● | ◐ tab-complete | ● | ● | ? |
| `@`-file completion | ● | ● | ● frecency | ● 4-tier | ● gitignore-aware | ◐ identifiers | ◐ path fallback | ● | ● | ? |
| `$EDITOR` escape hatch | ● `Ctrl+G` | ● `Ctrl+G` | ? | ● `Ctrl+O` | ● `Ctrl+G` | ● `C-x C-e` | ● | ○ #199 | ? | ? |
| Large-paste collapse | ● 800ch/2ln | ● →`.txt` | ● | ? | ● + expand | ○ | ○ | ? | ? | ? |
| …with expand affordance | **○** #3412 | **○** #25144 | **○** #8501 | ? | ● `Ctrl+O` | n/a | n/a | ? | ? | n/a |
| Draft stash | ● `Ctrl+S` | ○ | ○ | ○ | ○ | ○ | ○ | ○ | ● `Ctrl+S` | ○ |
| **Diff** |
| Unified w/ line numbers | ● | ● | ● | ● | ● | ● | ● | ● | ? | ? |
| Split / side-by-side | ○ | ○ | ● auto | ● auto @140col | ○ | ○ | ○ | ● auto | ? | ○ |
| Syntax highlighting in diff | ? | ● syntect, per-hunk | ? | ● chroma + cache | ● | ● pygments | ● bat | ● | ? | ? |
| Per-hunk accept/reject | **○** #33932 | ○ | ○ | ○ | ○ | ○ | ○ | ○ | ○ | ○ |
| **Approvals** |
| Modes w/ visible badge | ● 6 | ● 4 | ? | ◐ YOLO ×4 | ● 4 | ○ | ● 4 | ● | ● 3 | ? |
| "Allow for this session" | ● | ● | ? | ● | ● | ◐ `(D)on't ask` | ● | ? | ● | ? |
| Unoverridable blocklist under YOLO | ○ | ○ | ○ | ○ | ○ | ◐ shell carve-out | ◐ ext-mgmt | ○ | **●** | ○ |
| LLM-judged risk classification | ● Sonnet-5 | ○ | ○ | ○ | ○ | ○ | ● smart_approve | ○ | ● smart | ○ |
| **Status** |
| Context % / bar | ● `/context` grid | ● footer | **○** #13003 | ● sidebar | ● footer | ● `/tokens` | ● bar | ● v0.6.19+ | ● 4-band | ? |
| Cost counter | ● | ● | ○ | ● | ● quota | ● per-msg | ◐ opt-in | ● | ● | ? |
| Width-tiered / priority collapse | ◐ | ● | ? | ◐ 120 col | **●** | ○ | ○ | ○ | ● 3 tiers | ? |
| User-configurable items | ● `/statusline` | ● 26 items | ? | **○** | ● `ui.footer` | ○ | ○ | ○ | ? | ○ |
| Timer pauses on approval | ? | **●** | ? | ○ | ● freezes | n/a | n/a | n/a | ? | ○ |
| Completion notification | ● | ● osc9/bel | ● plugin | ◐ config | ○ | ● bell | ○ | ? | ? | ○ |
| Live terminal title | ? | ● `/title` | ? | ○ | ○ | ○ | ● OSC 0 | ? | ? | ○ |
| **Session** |
| Resume | ● | ● + picker | ● | ● | ● browser + search | ◐ history | ● SQLite | ? | ● | ? |
| Fork / branch | ● 4 verbs | ● `/fork` | ● | ○ | ○ | ○ | ● `--fork` | ○ | ● | ○ |
| Rewind / checkpoint restore | ● `Esc Esc` | ● `Esc Esc` fork | ● `/undo` git | ○ | ● `/restore` shadow-git | ● `/undo` git | ◐ `--edit` | ○ | ? | ○ |
| **Misc** |
| Configurable keybindings | ● | ● `/keymap` | ● leader | **○** #737 | ● | ○ | ○ | ○ | ? | ? |
| Vim mode | ● | ● | **○** #1764 181👍 | **○** #1199 | ● | ● `--vim` | ○ | ◐ #197 | ? | ○ |
| Screen-reader mode | **●** thorough | ○ | ○ | ○ | ● | ◐ dumb-term | ○ | ? | ? | ○ |
| Light / colorblind theme | ● daltonized | ● tmTheme | ● 33 themes | **○** #755 | ● | ● | ● | ● annotations | ● 100 skins | ? |

> The GAIA column is `?` in many rows because this was a read-only external survey — the
> TUI source was not read. Fill it in before prioritising.

---

## 5. Anti-patterns — mistakes with receipts

### 1. Collapsing a large paste with no way to view or edit it

Three separate projects, three of their loudest threads:

- Claude Code [#3412](https://github.com/anthropics/claude-code/issues/3412) (305) +
  [#23134](https://github.com/anthropics/claude-code/issues/23134) (147)
- opencode [#8501](https://github.com/anomalyco/opencode/issues/8501) (**230 👍, 35
  comments** — "brutal for voice-to-text")
- Codex [#25144](https://github.com/openai/codex/issues/25144) (87 👍, 56 comments — long
  pastes silently become `.txt` attachments with no opt-out)

Only Gemini shipped the expand affordance. **Ship collapse and expand in the same commit,
or don't ship collapse.**

### 2. Auto-copy-on-select that also auto-deselects

Crush [#2376](https://github.com/charmbracelet/crush/issues/2376), maintainer
@andreynering:

> I can confirm this is by design. Once selected, the text is automatically copied to
> clipboard and deselected.

Users hate it — you can't shift-click to extend. opencode
[#10490](https://github.com/anomalyco/opencode/issues/10490): accidental clipboard
overwrites, breaks screen-sharing, no way to disable. Crush
[#3092](https://github.com/charmbracelet/crush/issues/3092) is worst-of-both: over SSH it
reports success, copies nothing, **and** suppresses the client's native copy-on-select.

### 3. Mouse capture as an unconditional default

Crush [#373](https://github.com/charmbracelet/crush/issues/373) states it plainly:

> basically as long as this uses bubbletea, you can't both get scrollback and also get
> mouse select.

Crush [#2254](https://github.com/charmbracelet/crush/issues/2254): *"Since crush doesn't
support native terminal scrollback, tmux's copy-mode is unusable."* Codex made the opposite
call — zero `EnableMouseCapture` calls repo-wide — and that single decision avoids the
entire complaint class.

If you do capture: ship a `mouse: off` toggle, and **document the Shift-bypass in `--help`
and the README**. Crush's docs folder has no troubleshooting page at all, which is exactly
why #373 → #695 → #1758 → #3429 is the same question asked four times in twelve months.

### 4. Cute spinner verbs as a non-optional default

Claude Code [#23430](https://github.com/anthropics/claude-code/issues/23430): *"Spinner
Status Words feel unprofessional and dismissive"*, plus
[#71483](https://github.com/anthropics/claude-code/issues/71483) and
[#64098](https://github.com/anthropics/claude-code/issues/64098) asking for neutral static
text. Gemini CLI has ~130 witty phrases and **defaults `ui.loadingPhrases` to `"off"`**.
Goose gates its ~60 behind `GOOSE_RANDOM_THINKING_MESSAGES=false`.

**For an AMD enterprise audience this matters more than for a hobbyist tool.** Codex's
answer is better than either: derive the header from the model's own reasoning heading
(`extract_first_bold` on the reasoning buffer), falling back to the literal string
`"Working"`.

### 5. Forced auto-scroll during generation

Crush [#2770](https://github.com/charmbracelet/crush/issues/2770) (open) — "deep mind
fatigue," you can't read from the top while it streams. Aider
[#2972](https://github.com/Aider-AI/aider/issues/2972),
[#4263](https://github.com/Aider-AI/aider/issues/4263) (prompt_toolkit swallows PgUp/PgDn
so there is **no keyboard route to the top of a long response**). Gemini
[#20814](https://github.com/google-gemini/gemini-cli/issues/20814):

> this "scroll to the top" behaviour happens every few seconds automatically… it is
> impossible to read the plan in the terminal

with a reply: *"same issue… main reason why I don't use Gemini CLI much."*

### 6. A truncation hint that names a command which doesn't exist

Goose prints `"... (N lines hidden, /toggle to show all)"`. The command is `/r`. Keep
affordance strings and keybindings in one source of truth.

### 7. Fixed truncation with no configurability

Codex [#4550](https://github.com/openai/codex/issues/4550) (43 👍) asks for configurable
folding; Claude Code [#12589](https://github.com/anthropics/claude-code/issues/12589) asks
for a configurable collapse threshold. Both are hard-coded and unconfigurable.

### 8. Closing a performance complaint as NOT_PLANNED

Crush [#1147](https://github.com/charmbracelet/crush/issues/1147) — ~3 Mbps of SSH traffic
and 100 % CPU **for the spinner** — closed NOT_PLANNED. Also
[#1746](https://github.com/charmbracelet/crush/issues/1746): 120–150 % CPU *while idle*,
answered with "the whole UI code was rewritten focusing on performance."

### 9. Not restoring the terminal on every exit path

- Crush [#2109](https://github.com/charmbracelet/crush/issues/2109) (open): mouse-tracking
  escapes flood the terminal after `Ctrl+C`; user must run `reset`.
- [#2255](https://github.com/charmbracelet/crush/issues/2255): after a panic, WezTerm left
  with no working mouse and the wrong cursor shape.
- [#3260](https://github.com/charmbracelet/crush/issues/3260) (open): a capability probe
  **kills the entire `foot` terminal window** on startup.

### 10. `len()` instead of grapheme width

Crush shipped this bug ([#1485](https://github.com/charmbracelet/crush/issues/1485),
@meowgorithm: *"we were reading bytes (with `len()`) rather than graphemes"*), fixed it,
and **shipped it again** ([#1845](https://github.com/charmbracelet/crush/issues/1845),
fixed upstream in [glamour PR #499](https://github.com/charmbracelet/glamour/pull/499)).
[#2717](https://github.com/charmbracelet/crush/issues/2717) still drops the first char of
the status bar on pure ASCII.

Worst of all, [#1613](https://github.com/charmbracelet/crush/issues/1613): rendering
correctness depended on `$LANG` — clean under `en_US.UTF-8`, broken under `zh_CN.UTF-8`.
**Put CJK and emoji strings in the golden fixtures.**

### 11. Hard-coding the palette and the keybindings

Crush [#755](https://github.com/charmbracelet/crush/issues/755) (light/colorblind mode,
31 👍) has been open a year; [#737](https://github.com/charmbracelet/crush/issues/737)
(keybinding conflicts with multiplexers) 23 👍. Crush's `ctrl+p` alone makes it reportedly
unusable in Zellij and the VS Code integrated terminal. An ANSI-16 passthrough theme is the
light-mode answer and the accessibility answer in one.

### 12. Assuming a rewrite fixes copy/paste

opencode's stated rationale for deleting 28,760 lines of Bubble Tea was *"performance and
capability issues"* ([v1.0.0
notes](https://github.com/anomalyco/opencode/releases/tag/v1.0.0); the commit is literally
titled `DELETE GO BUBBLETEA CRAP HOORAY`). They got real wins — flicker essentially gone,
no resize complaints, Yoga flexbox layout, 32k → 4k LOC. But **copy/paste and CJK/emoji
width bugs survived the rewrite intact.**

Their unstated-but-important mitigation for rewrite regressions is worth copying: a hard
version pin (`opencode upgrade 1.0.0`, downgrade to `0.15.31`) plus "open an issue and
we'll add it back quickly."

### 13. The alt-screen question has no free answer — don't cargo-cult either side

toad (Python/Textual) and Hermes (React/Ink) independently chose alt-screen + differential
updates, and *both cite flicker elimination as the reason*; Hermes even markets "no
scrollback clutter after you quit" as a feature. Codex went the other way and pays for it
with a dedicated resize-reflow subsystem plus ~25 open emulator-specific scrollback bugs.

McGugan's actual criticism is narrower than it is usually quoted as:

> These apps update the terminal by removing the previous lines and writing new output
> (even if only a single line needs to change). This is a surprisingly expensive operation
> in terminals, and has a high likelihood you will see a partial frame—which will be
> perceived as flicker.

The enemy is line-erase-and-rewrite in the normal buffer, not the alt screen.

**If you go alt-screen, the debt you must pay is export + in-app search + copy — all three,
or the transcript dies on exit.**

---

## 6. If you only do three things

1. **Recommendation 4** — raw source alongside rendered blocks. The only irreversible one.
2. **Recommendation 1** — notify on completion. Best value/cost ratio at our turn length.
3. **Recommendation 2** — explicit follow-state. The bug every competitor still has.

---

## 7. What could not be verified

Read this before acting on anything above.

- **The Go-library capability inventory was not completed.** The dedicated agent was
  interrupted. Unresolved: whether `tea.SetWindowTitle` exists in v1.3.10; whether
  `bubblezone` is v1-compatible and what the zero-width-marker cost is; whether
  `charmbracelet/x/ansi` ships an OSC 52 helper; whether `tea.Println`/`tea.Printf` can
  write into real scrollback under v1 and with what constraints; and best-in-class Go
  fuzzy/diff libraries beyond what Crush happens to use (`sahilm/fuzzy`,
  `aymanbagabas/go-udiff`). **Every library claim above that isn't attributed to Crush's
  `go.mod` is unconfirmed.**
- **Crush's `internal/ui/diffview` is NOT importable** — it lives under `internal/`, and
  `charmbracelet/x/exp` has no `diffview`. It must be vendored. The good news: its only v2
  dependency is `lipgloss/v2` and the API surface it uses all exists in v1. It is a pure
  fluent builder returning a string — no `tea.Model`. Its golden-test matrix
  (`{Unified,Split}` × `{Default, Narrow, SmallWidth, LargeWidth, CustomContextLines,
  MultipleHunks, NoLineNumbers, NoSyntaxHighlight}`) doubles as a spec.
- **Claude Code's source is closed.** Its spinner format, collapse threshold, and queue
  behaviour come from issue-tracker pastes and third-party bundle extractions, not docs.
  The `+N lines (ctrl+o to expand)` string and the display-side collapse threshold are real
  per issues but undocumented and unconfigurable.
- **Hermes's metrics are suspect** — 229k stars / 31k open issues did not pass a smell
  test and were not re-verified. Existence and feature list came back with source URLs.
- **toad has been dormant since 2026-05-26** — ~2.5 months, no commits on ~25 branches,
  funding unresolved. Its ideas are worth stealing; the project is not a safe dependency.
- **Aider's last commit to `main` was 2026-05-22**, last tagged release v0.86.0
  (2025-08-09), 1,333 open issues, 458 open PRs. Its `mdstream.py` technique is still the
  clearest single explanation of stable-prefix streaming; the project's health is another
  matter.
- **Goose moved.** `block/goose` 301s to `aaif-goose/goose`, and docs moved from
  `block.github.io/goose` to `goose-docs.ai`. Reaction counts did not survive the move —
  the highest-reaction *open* issue in the whole repo has 3 👍, so reactions are a dead
  signal there.
- **GAIA's own column in the comparison table is `?` in many rows.** This was a read-only
  external survey; the TUI source was not read.
- **All research was delegated to subagents.** Their reports were cross-checked against
  each other where they overlapped (the three-way convergence on stable-prefix streaming;
  the toad alt-screen contradiction), but the primary sources were not independently
  re-fetched.

---

## 8. Sources

**Claude Code** — [Interactive mode](https://code.claude.com/docs/en/interactive-mode) ·
[Fullscreen rendering](https://code.claude.com/docs/en/fullscreen) ·
[Permission modes](https://code.claude.com/docs/en/permission-modes) ·
[Checkpointing](https://code.claude.com/docs/en/checkpointing) ·
[Status line](https://code.claude.com/docs/en/statusline) ·
[Terminal config](https://code.claude.com/docs/en/terminal-config) ·
[Model config](https://code.claude.com/docs/en/model-config) ·
[Accessibility](https://code.claude.com/docs/en/accessibility) ·
[Tools reference](https://code.claude.com/docs/en/tools-reference) · issues
[#826](https://github.com/anthropics/claude-code/issues/826)
[#1913](https://github.com/anthropics/claude-code/issues/1913)
[#3412](https://github.com/anthropics/claude-code/issues/3412)
[#8477](https://github.com/anthropics/claude-code/issues/8477)
[#18170](https://github.com/anthropics/claude-code/issues/18170)
[#23134](https://github.com/anthropics/claude-code/issues/23134)
[#23430](https://github.com/anthropics/claude-code/issues/23430)
[#33932](https://github.com/anthropics/claude-code/issues/33932)
[#50246](https://github.com/anthropics/claude-code/issues/50246)

**Codex CLI** —
[`insert_history.rs`](https://github.com/openai/codex/blob/main/codex-rs/tui/src/insert_history.rs) ·
[`transcript_reflow.rs`](https://github.com/openai/codex/blob/main/codex-rs/tui/src/transcript_reflow.rs) ·
[`status_indicator_widget.rs`](https://github.com/openai/codex/blob/main/codex-rs/tui/src/status_indicator_widget.rs) ·
[`shimmer.rs`](https://github.com/openai/codex/blob/main/codex-rs/tui/src/shimmer.rs) ·
[`streaming/`](https://github.com/openai/codex/tree/main/codex-rs/tui/src/streaming) ·
[`keymap.rs`](https://github.com/openai/codex/blob/main/codex-rs/tui/src/keymap.rs) ·
[`diff_render.rs`](https://github.com/openai/codex/blob/main/codex-rs/tui/src/diff_render.rs) ·
[`bottom_pane/footer.rs`](https://github.com/openai/codex/blob/main/codex-rs/tui/src/bottom_pane/footer.rs) ·
[`paste_burst.rs`](https://github.com/openai/codex/blob/main/codex-rs/tui/src/bottom_pane/paste_burst.rs) ·
[`clipboard_copy.rs`](https://github.com/openai/codex/blob/main/codex-rs/tui/src/clipboard_copy.rs) ·
issues [#9203](https://github.com/openai/codex/issues/9203)
[#25144](https://github.com/openai/codex/issues/25144)
[#27644](https://github.com/openai/codex/issues/27644)
[#4550](https://github.com/openai/codex/issues/4550)

**Crush** —
[`internal/ui/AGENTS.md`](https://raw.githubusercontent.com/charmbracelet/crush/main/internal/ui/AGENTS.md)
← *read this first* ·
[`anim/anim.go`](https://raw.githubusercontent.com/charmbracelet/crush/main/internal/ui/anim/anim.go) ·
[`diffview/diffview.go`](https://raw.githubusercontent.com/charmbracelet/crush/main/internal/ui/diffview/diffview.go) ·
[`chat/streaming_markdown.go`](https://raw.githubusercontent.com/charmbracelet/crush/main/internal/ui/chat/streaming_markdown.go) ·
[`common/markdown.go`](https://raw.githubusercontent.com/charmbracelet/crush/main/internal/ui/common/markdown.go) ·
[`common/timer.go`](https://raw.githubusercontent.com/charmbracelet/crush/main/internal/ui/common/timer.go) ·
[`list/list.go`](https://raw.githubusercontent.com/charmbracelet/crush/main/internal/ui/list/list.go) ·
[`dialog/permissions.go`](https://raw.githubusercontent.com/charmbracelet/crush/main/internal/ui/dialog/permissions.go) ·
[go.mod](https://github.com/charmbracelet/crush/blob/main/go.mod) · issues
[#373](https://github.com/charmbracelet/crush/issues/373)
[#755](https://github.com/charmbracelet/crush/issues/755)
[#1147](https://github.com/charmbracelet/crush/issues/1147)
[#1613](https://github.com/charmbracelet/crush/issues/1613)
[#2254](https://github.com/charmbracelet/crush/issues/2254)
[#2376](https://github.com/charmbracelet/crush/issues/2376)
[#2481](https://github.com/charmbracelet/crush/issues/2481)
[#2770](https://github.com/charmbracelet/crush/issues/2770)
[#2853](https://github.com/charmbracelet/crush/issues/2853)
[#2918](https://github.com/charmbracelet/crush/issues/2918)

**opencode** —
[v1.0.0 release notes](https://github.com/anomalyco/opencode/releases/tag/v1.0.0) ·
[PR #2685 "opentui"](https://github.com/anomalyco/opencode/pull/2685) ·
[commit f68374ad](https://github.com/anomalyco/opencode/commit/f68374ad2223ddc213bdea9519ca6a699819ee0e) ·
[thdxr: 32k→4k LOC](https://x.com/thdxr/status/1973888039454961927) ·
[anomalyco/opentui](https://github.com/anomalyco/opentui) · issues
[#4283](https://github.com/anomalyco/opencode/issues/4283)
[#8501](https://github.com/anomalyco/opencode/issues/8501)
[#10490](https://github.com/anomalyco/opencode/issues/10490)

**Gemini CLI** —
[`Footer.tsx`](https://github.com/google-gemini/gemini-cli/blob/main/packages/cli/src/ui/components/Footer.tsx) ·
[`markdownUtilities.ts`](https://github.com/google-gemini/gemini-cli/blob/main/packages/cli/src/ui/utils/markdownUtilities.ts) ·
[`useFlickerDetector.ts`](https://github.com/google-gemini/gemini-cli/blob/main/packages/cli/src/ui/hooks/useFlickerDetector.ts) ·
[`DiffRenderer.tsx`](https://github.com/google-gemini/gemini-cli/blob/main/packages/cli/src/ui/components/messages/DiffRenderer.tsx) ·
[`useMessageQueue.ts`](https://github.com/google-gemini/gemini-cli/blob/main/packages/cli/src/ui/hooks/useMessageQueue.ts) ·
[keyboard-shortcuts](https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/keyboard-shortcuts.md) ·
issues [#10673](https://github.com/google-gemini/gemini-cli/issues/10673)
[#20814](https://github.com/google-gemini/gemini-cli/issues/20814)
[#22004](https://github.com/google-gemini/gemini-cli/issues/22004)
[#5009](https://github.com/google-gemini/gemini-cli/issues/5009)

**Aider** — [`mdstream.py`](https://github.com/Aider-AI/aider/blob/main/aider/mdstream.py) ·
[`waiting.py`](https://github.com/Aider-AI/aider/blob/main/aider/waiting.py) ·
[`diffs.py`](https://github.com/Aider-AI/aider/blob/main/aider/diffs.py) ·
[`io.py`](https://github.com/Aider-AI/aider/blob/main/aider/io.py) ·
[edit formats](https://aider.chat/docs/more/edit-formats.html) · issues
[#3196](https://github.com/Aider-AI/aider/issues/3196)
[#3854](https://github.com/Aider-AI/aider/issues/3854)
[#4332](https://github.com/Aider-AI/aider/issues/4332)

**Goose** — [aaif-goose/goose](https://github.com/aaif-goose/goose) (moved from
`block/goose`) ·
[permissions](https://goose-docs.ai/docs/guides/managing-tools/goose-permissions/) ·
[adjust tool output](https://goose-docs.ai/docs/guides/managing-tools/adjust-tool-output) ·
[ACP + new TUI](https://goose-docs.ai/blog/2026/04/08/goose-acp-and-new-tui)

**toad / McGugan** — [Announcing Toad](https://willmcgugan.github.io/announcing-toad/) ·
[**Efficient streaming of Markdown in the
terminal**](https://willmcgugan.github.io/streaming-markdown/) ← *the four-step algorithm* ·
[Toad released](https://willmcgugan.github.io/toad-released/) ·
[batrachianai/toad](https://github.com/batrachianai/toad) ·
[textual-diff-view](https://github.com/batrachianai/textual-diff-view)

**Hermes** — [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) ·
[TUI docs](https://hermes-agent.nousresearch.com/docs/user-guide/tui) ·
[security docs](https://hermes-agent.nousresearch.com/docs/user-guide/security)

**Module verification** —
[bubbletea @latest = v1.3.10](https://proxy.golang.org/github.com/charmbracelet/bubbletea/@latest) ·
[bubbletea/v2 @latest = v2.0.8](https://proxy.golang.org/github.com/charmbracelet/bubbletea/v2/@latest)
