# GAIA flagship agent — findings and v2 scope

Living document, written while stress-testing the flagship agent through the Go
TUI. **Nothing under "Scope for v2" has been implemented** — it is a tally for
review before any architectural change is made.

Companion to [`TESTING_STATUS.md`](TESTING_STATUS.md), which is the per-session
test log. This file is the *analysis*: what is broken, what is missing, and what
shape a fix should take.

---

## 1. Bugs found and fixed

| # | What the user saw | Real cause | Commit |
|---|---|---|---|
| 1 | "The tool only surfaces 2 of the 30 skills per call" | `_create_tool_message` re-truncated every structured tool result to a hardcoded **2,000 chars**, after `_handle_large_tool_result` had already fitted it to the device budget (20K NPU / 40K GPU). The second gate made the first dead code for anything over 2 KB. | `f6245a5b` |
| 2 | "Your script has a bug: `height - 100` fails with `unsupported operand type(s) for +: 'float' and 'str'`" — the script was fine | The model sent `timeout: "120"` quoted. Nothing coerced tool arguments to their annotated types, so the string reached `subprocess.run(timeout="120")` and the stdlib raised from deep inside. | `05f5973e` |
| 3 | `execute_python_file` "timing out intermittently even on retries" | No `stdin=subprocess.DEVNULL` — the child inherited a pipe nobody writes to and blocked until the timeout. Identical to a fault already fixed in `shell_tools.py`. | `05f5973e` |
| 4 | Tool output silently empty on non-ASCII | `text=True` decodes with the locale codec (cp1252 here); one out-of-range byte raised. Hit `execute_python_file` and three window-listing subprocesses. | `05f5973e` |
| 5 | A stray grey block hanging off the right edge of any line with inline code | Glamour wraps inside an inline code span without closing it, so the line's wrap padding inherits the code background. | `90704983` |
| 6 | No tokens/sec in the dev footer | The canonical event translator forwarded `steps`/`tools_used`/`elapsed` and dropped `tokens`/`ttft`. The feature was built; its input was thrown away one layer earlier. | `9e66519d` |

### The pattern worth acting on

**Four of the six produced a confident wrong answer rather than an error.** The
agent told the user their script was broken, told them only two skills existed,
and told them shell access was unavailable — all while the actual fault was
invisible from the transcript. Any harness that judges the agent by whether its
answer *reads* plausible passes all four.

This is the strongest argument for §4.6 (self-critique) and for keeping
ground-truth verification mandatory in the test skill.

Bugs 1 and 2 are also both *old*. #1's hardcoded 2,000 predates the device-aware
budget by several releases, so every list-returning tool has been capped at
roughly two items for a long time. Neither had a test, because the tests that
exist assert the first gate's behaviour and stop there.

---

## 2. Open bugs and UI/UX issues

| # | Issue | Status |
|---|---|---|
| A | Header says `claude`, not *which* Claude. Must name Sonnet 5 / Opus 5 / local Gemma-4. | Delegated |
| B | No `/model` command — the backend is fixed at launch. | Delegated |
| C | Dev mode shows no Lemonade version or health. | Delegated |
| D | No first-run setup; nothing re-runnable via `/setup`. | Delegated |
| E | Memory is only observable by *asking the agent*, which summarises and embellishes. | Delegated (`/memory`, read-only) |
| F | Skill listing is an unreadable comma-run that wraps mid-word (`testing-`/`the-`/`gaia-agent`). | Open |
| G | The agent's own log reports `model_id=Gemma-4-E4B-it-GGUF` while running `--use-claude`. Model identity is not single-sourced. | Open — see §4.1 |
| H | Text is not selectable/copyable with the mouse across platforms. | Open — see §3.1 |
| I | A turn once arrived with the word "life" prepended to the driver's text on a fresh launch. | Unreproduced |

### 3.1 Text selection and copy (issue H)

The TUI runs in Bubble Tea's alternate screen with mouse reporting, which takes
mouse drag away from the terminal's own selection. That is why text cannot be
swiped and copied the way it can in a normal shell.

There is already a `clipboard.go` with a "copy the last code block" path, so
clipboard access itself is solved. The gap is *arbitrary* selection. Options, in
rough order of cost:

1. **Stop capturing mouse motion** (or make it toggleable). The terminal's native
   selection comes back for free and works identically on Windows Terminal,
   iTerm2, and Linux terminals — the cross-platform story is the terminal's
   problem, not ours. Cost: lose any hover/drag affordance that depends on motion
   events.
2. **A "copy mode"** — a key that suspends mouse capture and says so in the
   status bar, like tmux's copy mode. Keeps both behaviours, costs a mode.
3. **In-app selection** — render a selection highlight and own the clipboard
   write. Most control, most work, and has to re-implement word/line semantics
   users already know.

Recommendation: (1) or (2). (3) is a lot of work to reproduce something the
terminal already does well. Worth checking which mouse modes the TUI actually
needs before choosing.

---

## 3. Capability audit vs. the competition

Measured against what the user named — OpenCLAW, Hermes, Claude Code — while
staying runnable locally on Lemonade. A dedicated competitive analysis is in
flight; this is the inventory it builds on.

| Capability | Today | Gap |
|---|---|---|
| PC navigation / filesystem | `file_search`, `filesystem`, `file_io` mixins | — |
| Shell / cmd | `shell` mixin, gated allowlist | — |
| System specs | `system_context.py` writes them into memory at init | Not a tool the agent can call on demand |
| Document parsing | RAG SDK exists and is strong | **Not enabled by default on the flagship** — §4.2 |
| Search across folders | `file_search` + `code_index` (FAISS) | Not wired to documents |
| Skills: find / load / unload | Works, including vanilla Claude Code skills with no GAIA metadata | — |
| Skills: execute | Works (after bugs 2–4) | — |
| Voice | `talk` SDK, Whisper + Kokoro via Lemonade | Not reachable from the flagship or the TUI — §4.3 |
| Autonomy | None — pure request/response | §4.4 |
| Write a script, keep it as a tool | None | §4.5 |
| Edit its own skills | None | §4.5 |
| Procedural memory ("how I did this") | **Partially built and unreachable** — §4.5 |
| Orchestrate other models / agents | None | §4.7 |

---

## 4. Scope for v2 (not implemented — for review)

### 4.1 Single-source the model identity
The agent, the `[PARSE]` log line, and the TUI header each derive "which model"
from a different place, and they already disagree — the log says Gemma while the
session runs on Claude. One resolved value, owned by the agent, announced to the
host, rendered by the TUI. Prerequisite for A, B, C and G.

**Size: S. Do this first** — three delegated tasks all touch the header, and this
is the thing they should agree on.

### 4.2 Document pipeline on by default
The RAG SDK is the strongest asset the flagship is not using. Enable the document
pipeline by default and let skills complement rather than replace it.

The user's framing is worth keeping verbatim: **tools should be held in memory
and searched when needed** rather than all registered up front. There is already
a dynamic tool loader with LRU recency in the base agent (`_on_tool_invoked`,
#1449), so the mechanism exists in embryo. This is the change that makes "add
more tools" stop costing accuracy, which is what unblocks everything in §4.7.

**Size: M for the pipeline, L for search-on-demand tooling.**

### 4.3 Talk mode on Lemonade
Whisper (ASR) and Kokoro (TTS) are already Lemonade-served, and `src/gaia/talk/`
exists. Scope: a voice loop in the TUI — push-to-talk in, streamed TTS out.
Entirely local, which is the differentiator no cloud agent can match.

Note the interaction with §4.6: a spoken answer cannot be skimmed, so length
discipline and self-critique matter far more in voice than on screen.

**Size: M.**

### 4.4 Autonomy — a goal queue, not just a cron

Today the agent waits for a query like every other agent. Two mechanisms are
needed, and they are not the same thing.

**Scheduling is the easy half.** Hermes and OpenCLAW both have cron; GAIA has
`gaia schedule` already. The agent should be able to schedule its *own* heartbeat
or trigger, which is a small extension of what exists.

**The state machine is the real feature.** The user's framing, which is the right
one: after each execution step the agent should *derive its own next action* from
what just happened, and keep going while it has next steps — including after it
has completed the goal it was given. It should infer goals from the user and from
its environment, and hold them as a **queue of goals in memory that it manages
itself**.

That is a change to the agent loop, not a scheduler feature:

- **A next-action derivation step.** After a step completes, ask "what does this
  imply I should do next?" — distinct from the existing plan/step machinery,
  which executes a plan rather than revising the goal.
- **A goal queue with real lifecycle** — proposed, accepted, active, blocked,
  done, abandoned — persisted in memory so it survives a restart the way facts
  already do. `MemoryStore` has the shape for this (`reminder` is already a
  category, `get_upcoming` already exists and is unused by the flagship).
- **Goal inference from interaction**, not just from an explicit request. The
  memory store's `get_activity_timeline` and `get_tool_stats` are the raw
  material and neither is currently read.
- **A stopping rule, and a rule for interrupting the user.** An agent that always
  finds a next step is a runaway; one that never speaks up is a background
  process. This is the hard part, and it is a judgement problem, not a plumbing
  problem.

Local-inference note: derivation costs a model call per step, and on Gemma-4-E4B
that roughly doubles a turn. The derivation prompt wants to be small and cheap —
possibly a much smaller model than the one doing the work, which ties into §4.7.

`src/gaia/governance/` and the autonomy-engine plan in `docs/plans/` are the
existing groundwork; the competitive analysis is tasked with reconciling against
that plan explicitly.

**Size: L for the loop change, S for self-scheduling. The v2 headline feature.**

### 4.5 Self-extension — and a correction to my earlier note

I initially wrote that procedural memory did not exist. That was wrong. It is
**built but unreachable**:

- `MemoryStore.put_skill`, `search_skills`, `supersede_skill` exist
  (`memory_store.py:2532`, `:2735`).
- `skill` is a first-class category in `VALID_CATEGORIES`.
- `src/gaia/agents/base/skill_synthesis.py:696` writes them, and
  `MemoryMixin.recall_skill` / `get_recalled_skills_system_prompt` reads them
  back into the prompt.
- **But the LLM has only five memory tools** — `remember`, `recall`,
  `update_memory`, `forget`, `search_past_conversations`. None of them writes a
  skill.

So the agent can be *given* procedural memories and will recall them, but it can
never record one from a task it just completed. The write path is synthesis-only.
That reframes the work from "build procedural memory" to "let the agent author
its own", which is much smaller than it looked.

On top of that:
- **Write a script, keep it as a tool.** The agent can write and run a script;
  nothing lets it register one for reuse. `put_skill` plus the dynamic tool
  loader is most of the machinery.
- **Edit its own skills.** Copy a skill into memory, revise it, use the revision
  — the loop that lets the agent get better at a task it just did badly.

**Size: M, and higher leverage than its size suggests.**

### 4.6 A critique-and-review state

The user's proposal: an explicit state where the agent reviews its own output and
decides whether to improve it, skipped for trivial turns and applied to code
edits, content creation, and long answers.

**The evidence from this session supports it.** Four of six bugs surfaced as a
confident wrong answer. Specifically, the agent asserted that the *user's script*
had a bug when the fault was in its own tool call — a claim a review step asking
"is the error actually in the thing I'm blaming?" could plausibly have caught,
because both the tool call and the error text were in its own context.

Design notes:
- **Gate it, don't always run it.** "17 times 23" must not cost two model calls.
  A cheap trigger: answer length, whether any tool mutated state, whether a file
  was written, whether the answer asserts a *cause*.
- **Review is not a second draft.** The valuable form is a specific check —
  "does this claim follow from what I observed?" — not "make it better", which
  invites padding.
- **It must be measured, not assumed.** Extra steps cost latency, and on Gemma-4
  a second pass costs roughly a whole turn. I have not yet measured whether it
  improves accuracy on this agent; that measurement belongs in
  `TESTING_STATUS.md` before the state ships.

**Size: M. Recommend prototyping behind a flag and measuring before committing.**

### 4.7 GAIA as a super-agent — routing across models, hardware, and agents

The user's framing: GAIA orchestrates, dispatching a task to whichever
endpoint is best at it — a local Gemma on NPU for cheap/private work, a frontier
model for hard reasoning, or another *agent* (Claude Code, Hermes) for work that
agent is specialised in. Via the Lemonade orchestration framework, or via the
tool/skill/SDK surface.

What exists to build on: `src/gaia/llm/factory.py` already selects providers, a
`RoutingAgent` already picks agents, and the MCP layer already lets GAIA call
external tool servers. What is missing is a *policy* layer — how does it decide?
— and honest cost/latency/privacy accounting per endpoint.

Two hazards worth stating up front:
- **The single Lemonade model slot.** One `(model, ctx_size)` pair is resident at
  a time; routing between two local models means eviction and a cold reload.
  Cross-endpoint routing is cheap; cross-*local-model* routing is not.
- **Privacy is a routing constraint, not a preference.** "Run this locally
  because it contains my passphrase" has to be expressible, especially given §6.

**Size: L. v2 for the mechanism, v3 for good policy.** Sequence it after §4.2 —
routing is a tool-selection problem, and search-on-demand tooling is the
foundation.

### 4.8 Memory management UI beyond read-only

`/memory` is being built read-only on purpose. Edit, delete, and add come next,
and the M9 findings below say why they matter: a user needs to be able to delete
a stale note and redact a secret without asking the agent to do it.

**Size: S once the read view exists.**

---

### 4.9 The stated aim: the most human agent there is — and why that is an architecture problem

The user's goal for GAIA is to be **the most human agent on the planet**, and to
get there **by innovating at the architecture level** rather than by tuning
prompts. That framing is worth taking literally, because it rules some things in
and a lot of things out.

Prompt work cannot produce it. A warmer system prompt makes a *pleasant*
stateless agent, and this session shows why that is not enough: the flagship
already answers warmly and it still told the user their script was broken when
its own tool call was at fault, still could not see 28 of its 30 skills, and
still recited a passphrase back when asked what it remembered. None of those are
tone problems.

What "human" decomposes into, mechanically — and every one is a structural
change:

| Human quality | The mechanism it actually needs | Where it lands here |
|---|---|---|
| Remembers you without being told | Memory that compounds and is trusted | §4.5, §6 |
| Gets better at what you do together | Writes its own procedures from experience | §4.5 |
| Notices, and brings things up | Goal derivation + a rule for interrupting | §4.4 |
| Knows when it is wrong | Self-critique before asserting a cause | §4.6 |
| Doesn't repeat what you just said | History owned by the agent, not the transport | §5.4 |
| Admits a limit instead of guessing | Failure honesty (the gaia-voice skill starts this) | partly built |
| Speaks and listens | Voice loop on local Whisper + Kokoro | §4.3 |
| Keeps your secrets | Classification and redaction in memory | §6.2 — **currently absent** |

Two observations from this session that bear directly on the aim:

1. **Trust is the binding constraint, not warmth.** An agent that remembers is
   only pleasant to use if what it remembers is *right*. Ours holds a stale rule
   it invented about its own tooling and a plaintext passphrase it was never
   asked to keep. Fix trust before adding charm; a confidently wrong companion is
   worse than a blank one.
2. **The self-critique state is the highest-leverage "human" feature available
   cheaply.** Four of six bugs this session surfaced as confident wrong answers.
   Knowing when you might be wrong is a recognisably human quality *and* the
   thing that would have caught them.

The architectural bet worth making explicit: **the differentiator is the state
machine and the memory model, not the model weights.** GAIA cannot win on raw
capability against a frontier model, and does not need to — it can own
continuity, initiative, and running on your own hardware. That is a design
position, and it is the one every recommendation in §4 is pointed at.

---

## 5. Architectural limitations observed

1. **Truncation budgets follow the local hardware, not the active model.**
   `truncation_budget()` reads `NPU_CTX_SIZE` / `GPU_CTX_SIZE`. Running on Claude
   Sonnet (200K context) the agent still truncates tool results at 40,000 chars
   for no reason. The budget should follow the *model in use*.

2. **Two independent truncation gates with no shared owner.** Bug #1 existed
   because two functions both truncate and neither knew about the other. One
   place should own "how much of this result may the model see".

3. **No type contract between the model's JSON and the tool signature.** Bug #2
   was one instance. The tool schema is generated *from* annotations, but nothing
   validated *against* them until this session.

4. **Conversation history was transport-specific.** The HTTP surface appended to
   `conversation_history`; the stdio transport did not, so the TUI agent was
   amnesiac (fixed earlier this session). History belongs to the agent, not to
   whichever transport happens to be in front of it. **Bug #6 is the same shape**
   — two copies of the event translator that disagreed about which measurements a
   finished turn reports. Worth auditing for other per-transport divergence.

5. **Structured results have no presentation layer.** The agent gets a JSON blob
   and improvises prose, which is why 30 skills came out as a comma-run wrapping
   mid-word. Patched for now with instructions in the tool docstring and the
   gaia-voice skill, but instructing a model to format is a weaker contract than
   rendering it. The TUI already has a `cards` package for exactly this.

6. **A skill can ship scripts GAIA cannot provision.** Anthropic's `pdf` and
   `xlsx` skills both expect their Python dependencies present. Neither
   `reportlab` nor `openpyxl` is installed, and `pip` is blocked by the shell
   allowlist — correctly, but with no alternative. The agent worked around it
   both times by hand-writing raw PDF and raw OOXML, which is remarkable and
   produced genuinely valid files (verified: `%PDF-1.4`, and a valid xlsx zip
   with real `SUM(B2:C2)` formulas). It is not a strategy. A skill needs a
   declared, reviewable way to state its runtime dependencies and get them
   installed into a sandbox — otherwise "load any skill" holds for discovery and
   quietly fails for execution.

7. **The help overlay is at a hard 20-line budget and out of room.** Enforced by
   `TestHelpTextFitsItsBudget`, and `chatHelpText` was exactly full — Ctrl+Y and
   Ctrl+B were undocumented as a result, in a panel whose test claims it lists
   every binding. I freed lines by compressing, but three delegated tasks are
   each adding a command. The overlay needs paging or scrolling before the next
   feature, not another compression pass.

---

## 6. Memory findings from the stress test

Memory passed every functional check, including recall after a process restart.
The problems are all about what *else* it holds.

1. **The agent poisons its own memory with transient facts.** It had stored
   "`execute_python_file` times out intermittently on reportlab PDF scripts" —
   true for ten minutes, false now, and phrased as a durable rule. The earlier
   fix (`_is_transient_error`) covers the *auto-store* path; it does nothing when
   the model calls `remember` about a transient failure itself. Options: apply the
   same transient classifier to the `remember` tool, or give self-observations a
   confidence that decays (`apply_confidence_decay` already exists and appears
   unused by the flagship).

2. **Secrets are stored in clear text and recited on request.** A passphrase from
   an earlier RAG probe came back verbatim when asked "what do you remember about
   me?". Nothing classified it, nothing redacted it, and there is no way for a
   user to see or remove it without the agent's cooperation. **This needs a
   decision before v2 ships**: refuse to store credential-shaped content, redact
   on recall, or both. It also makes §4.7's privacy-aware routing a requirement
   rather than a nicety.

3. **An unexplained persona leaked into an answer** — "here's what I've got on
   you, Jordan-style rundown". No session content mentions Jordan. Either a
   cross-context memory row or model confabulation; the read-only `/memory` view
   will settle which.

4. **Memory is only observable through the model.** Asking the agent what it
   remembers returns a *summary*, subject to omission and embellishment — which
   is exactly how findings 1–3 nearly went unnoticed. Direct observability is not
   a nice-to-have for a system whose value proposition is accumulated state.

### What memory has that nothing uses

Reading the store's API surface against what the flagship actually calls, several
capabilities are built and idle: `apply_confidence_decay`, `get_upcoming`
(reminders), `get_activity_timeline`, `get_tool_stats` / `get_tool_summary`,
`search_skills` / `put_skill` (§4.5), `consolidate_old_sessions`,
`get_items_for_reconciliation`.

That is a lot of "grows with you" machinery sitting behind five tools. Before
designing new memory features, the cheaper question is which of these to wire up.
