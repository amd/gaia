# Flagship-branch review follow-ups

Design findings from the deep review of `feat/gaia-flagship-agent-2804`
(PRs #2932/#2964/#2963) that were deliberately **not** fixed in that branch —
each was judged follow-up material: real debt, wrong moment. Items fixed on the
branch itself (skill-library mixin promotion, code-index root, MRO order,
palette/question hit-tests, diff transport caps, per-PR gaia-agent CI) are not
repeated here.

Ordered by expected cost-of-delay.

## 1. `Agent.record_turn` — one owner for conversation history

`Agent.conversation_history` is a public uncapped list with four external
writers and three different windows: `gaia_agent/stdio.py` appends and trims to
12 pairs, `gaia_agent/server.py` replaces it wholesale from pushed context, and
`gaia/ui/agent_loop.py` / `gaia/ui/_chat_helpers.py` rebuild it per request.
The same agent therefore remembers differently over stdio than over HTTP.

Fix: `Agent.record_turn(query, answer)` owning the append and the window,
called from `_process_query_impl`'s exit. Transports that push a full
transcript keep replacing; streaming transports stop hand-rolling. Touches
four tested transports — its own change with its own eval run.

## 2. One readiness gate, not two

`tui/internal/ui/preflight/` is the real gate (fix ladder, per-row remedies,
ctx-size checks); `root/preflight.go` skips it for non-daemon transports, so
the flagship agent gets `chat/setup.go` — a second, weaker, hand-rolled gate
inlined into `ChatModel`. "What does ready mean" has two homes that will
diverge.

Fix: lift the row/fix ladder behind a `Checker` interface consumed by
`preflight.Model`, with the daemon transport and `gaia init --check` as two
implementations. `chat/setup.go` collapses to a check provider and five
`setup*` fields leave `ChatModel`.

## 3. `ChatModel` decomposition (58 fields)

Extract the three cleanest sub-models first — `setup` (pairs with item 2),
`memory` (3 fields + `memoryview.go`), `modelChip` (9 `lemonade*`/`model*`
fields + `modelcmd.go`/`lemonadechip.go`). ~17 fields off the struct, no
behavior change.

## 4. `focusOwner()` — one answer to "which overlay owns input"

Keyboard priority, mouse routing, and `overlayOpen()` each hard-code a
different overlay list, so `confirmation` gets the keyboard but is invisible
to the mouse. One `focusOwner()` enum consulted by all three; ~40 lines.
While there: decide whether a pending question should keep suppressing
drag-select with no banner (today it captures the mouse silently).

## 5. `render: "diff"` in the SSE contract

The TUI structurally sniffs a `diff` field out of tool results because the
file-edit tools never set `render`. The web Agent UI renders nothing for the
same edit. Register the file-edit tools in `_render_tool_map`, emit
`render: "diff"` with `{title, unified}`, teach `ChatView.tsx` the type, and
drop the sniff. Contract §4.3 addition — needs the doc updated in the same
change.

## 6. Split `BinaryPolicy`'s two shapes

`Subcommand` is one dataclass impersonating two types — four fields apply only
to subcommand-style CLIs (`gh`), four only to positional ones (`pytest`), and a
field set in the wrong mode is silently ignored. Split into `SubcommandRule` /
`PositionalRule` behind `BinaryPolicy.validate(argv)`, then migrate the
hard-coded `git` branch in `shell_tools.py` into the table as proof (then
`wmic`). The module's "adding a CLI is a data entry" claim becomes true for
already-supported shapes.

## 7. Shared `_EmbeddingIndex` for SkillLoader / ToolLoader

~70 near-verbatim lines (content-keyed embed cache, batch/single embed,
one-shot session disable). The selection policies genuinely differ and must
not be merged — extract the mechanical half as a collaborator both loaders
hold. Composition, not a base class.

## 8. Session-registry extraction (third copy is the trigger)

`gaia_agent/session_registry.py` mirrors `gaia_agent_email.agent_routes._SessionRegistry`
by declared intent — 252 lines of TTL + LRU + eviction that already bit once
(505d900c). Extract one shared registry (likely `src/gaia/daemon/sidecars/`)
before a third agent copies it.

## 9. `GET /control/v1/transcript`

Both control-API drivers (`src/gaia/eval/session_eval.py` and the
testing-the-gaia-agent skill's `driver.py`) scrape the rendered screen for
`"▶ You:"` — a cosmetic change silently rescores evals. Add a structured
last-turn endpoint and point both drivers at it.

## 10. Smaller items

- Migrate `RootModel`'s hand-rolled help fields onto `components.HelpState`
  (help is currently implemented twice), and stop `components/helpoverlay.go`
  hard-coding chat's command vocabulary — `chat` should supply its help body.
- `narrate.go`'s curated phrase table duplicates `event_narration.py`'s; trim
  the Go side to verb derivation and let Python own the vocabulary.
- The diff card (`cards/diff.go`) draws a `┌─┐` frame in a UI that removed
  every panel border; `noborders_test.go` only catches `Border(lipgloss.`
  declarations, so hand-drawn glyph boxes in `cards/` slip past.
- Windows CI for `tests/unit/` — ~620 tests fail on a Windows checkout today
  (POSIX-only fixtures: `os.geteuid`, unix sockets). Either mark them
  `skipif(win32)` and add a windows job for the rest, or accept Linux-only
  unit CI explicitly somewhere discoverable.
