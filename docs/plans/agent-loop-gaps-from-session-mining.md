# Agent-loop gaps, measured from Claude Code session transcripts

GAIA's agent loop is missing five things a strong harness has, and the transcripts say
which ones matter. The biggest is not a tool — it is that **the loop lets the model
announce it finished without ever observing the result of what it changed**. That single
gap accounts for the largest cluster of human corrections in the corpus: in 32 of 115
interactive sessions (27.8%) the user's follow-up was some form of "did you actually do
it / go check", and in half of those the agent's immediately preceding turn had already
claimed the work was done.

The second finding is a negative one worth as much as the positives: **GAIA already
handles parallel tool calls correctly**, and the todo-list mechanism that agent
frameworks are currently rushing to build was used in 4.4% of traces here. Two items
that look like obvious gaps are not gaps.

---

## Corpus and method

| | |
|---|---|
| Snapshot | 2026-09-05 (the corpus grows while it is analysed; counts drift between runs) |
| Window | 2026-08-04 → 2026-09-05 |
| Sessions | 345 top-level, across 129 project directories matching `…Work-gaia*` |
| Traces incl. subagents | 833 (492 subagent transcripts) |
| Tool calls | 52,307 resolved; 2,096 failed (4.01%) |
| Model calls | 44,410 |
| Human follow-up turns | 681 unique, in 115 sessions (the other 230 sessions are one-shot, no human present) |

Extraction is deterministic Python over the raw JSONL (`gaia.factory.harvest` plus
purpose-built scripts); no LLM in the measurement path. Everything derived lives in
`~/.gaia/cache/factory/loopgap/` and is not committed — transcripts carry absolute paths,
branch names, and pasted content.

**Honesty constraints that bound every number below.**

- Nothing in a transcript states whether a task succeeded. Tool-failure rate is not
  task-failure rate.
- Correction clusters are regex proxies over human turns, hand-verified by reading all
  681. Precision per cluster is stated where it is below ~90%.
- Harness-injected user records (task notifications, context-update banners, compaction
  summaries, cron-refired standing prompts) were filtered out; 150 byte-identical
  re-fires of the same prompt within a session were deduped. Skipping either step
  inflates every correction count several-fold.
- **The corpus is skewed.** This developer's dominant workflow is multi-agent PR/CI
  orchestration, not solo coding. Shell is 65.8% of all tool calls. Clusters that
  concentrate in one or two sessions are marked as such and ranked low.

### Correction clusters, ranked by sessions affected

| Cluster | Turns | % of follow-ups | Sessions | % of 115 interactive | % of all 345 |
|---|---:|---:|---:|---:|---:|
| Premature stop ("continue", "why did you stop") | 57 | 8.4 | 46 | 40.0 | 13.3 |
| **Unverified completion claim** | 48 | 7.0 | 32 | **27.8** | 9.3 |
| Verbosity / register | 41 | 6.0 | 26 | 22.6 | 7.5 |
| Scope drift / wrong target | 28 | 4.1 | 21 | 18.3 | 6.1 |
| Stall — blocked, no visible progress | 19 | 2.8 | 12 | 10.4 | 3.5 |
| Manual handoff (user runs it themselves) | 34 | 5.0 | 11 | 9.6 | 3.2 |
| Repeated instruction ignored | 7 | 1.0 | 6 | 5.2 | 1.7 |
| Lost work / overwrite | 4 | 0.6 | 3 | 2.6 | 0.9 |
| Any hard correction (rows 2, 4, 5, 7, 8) | 106 | 15.6 | 50 | 43.5 | 14.5 |

Column glossary: *turns* = unique human follow-up turns matching the cluster (a turn can
match more than one); *sessions* = distinct sessions containing ≥1 such turn;
*% interactive* = of the 115 sessions where a human ever replied; *% all* = of all 345.

---

## Ranked recommendations

### 1. Completion gate: refuse to finish on an unobserved mutation

**Value: highest. Effort: medium. Belongs in the base `Agent` loop.**

**Evidence.** 48 correction turns across 32 sessions (27.8% of interactive) ask whether
work claimed done was actually done. In 24 of those 48 (50%) the immediately preceding
assistant turn contained a completion token (`done`, `pushed`, `fixed`, `complete`, ✅).
The adjacent "premature stop" cluster — 46 sessions, 40.0% of interactive, where the
user's entire next turn is `continue` — is the same failure seen from the other side: the
loop ended while work remained.

**Why it is a loop problem and not a prompt problem.** A prompt instruction to "verify
before claiming done" is advisory and competes with everything else in context. Worse, in
GAIA it is actively unreliable: `skill_loader` re-evaluates each loaded skill's body every
turn and collapses it to a one-line menu entry when cosine similarity to the current query
falls under 0.20 (#2848). A verification rule living in a skill can silently stop being in
context halfway through the task it exists to govern. The loop cannot.

**Concrete shape.** The loop already knows which tools mutate — `_MUTATION_TOOLS`
(`agent.py:252`) drives mutation-call dedup. Add one bit of per-query state: set `dirty`
when a mutating tool succeeds, clear it when any non-mutating tool runs afterwards. When
the model proposes a final answer while `dirty` is set, inject exactly one system turn
naming the unobserved change and requiring either an observation or an explicit "I have
not verified this" in the answer. One injection, not a retry loop — a second refusal would
be the silent-degradation pattern the repo bans.

**Generality.** "Mutation without subsequent observation" is domain-free. It reads the
same for a file write, an HTTP POST, a DB migration, or a device flash. Nothing about it
is specific to source code, version control, or this repo.

---

### 2. Widen stuck-detection past byte-identical repeats

**Value: medium-high. Effort: low — the scaffolding already exists.**
**Belongs in the base `Agent` loop.**

**Evidence.** Of 2,062 failures that had a following call: 63.4% retried the **same tool**,
but only **1.3% used byte-identical arguments**. GAIA's loop detector keys on
`(tool_name, str(tool_args))` exact-match with `max_consecutive_repeats=4`
(`agent.py:5643`, `5654`), so it fires on 1.3% of the retry population and misses the
62.1% that vary an argument each attempt. Failure streaks of length ≥3 occur 53 times in
the corpus (34× len-3, 9× len-4, 10× len-5-plus).

Separately, the `error_count >= 3` bail at `agent.py:5315` sits **only** in the
tool-call-*parse*-error branch. The tool-*execution*-error path increments `error_count`
(`agent.py:5763`) and logs it, then transitions to `STATE_ERROR_RECOVERY` and continues —
with no ceiling. A tool that fails differently every time can consume the whole step
budget.

**Concrete shape.** Two small changes: (a) count consecutive failures per tool name
regardless of arguments, and (b) apply the existing `error_count >= 3` bail to the
execution path, producing an actionable "I am stuck on `<tool>`; last three errors were
X, Y, Z" answer rather than a silent budget burn. Both reuse counters already in the loop.

Encouragingly, the recovery signal is good: 85.9% of failures are followed by a
*successful* call, so the goal is bounding the tail, not suppressing retries.

---

### 3. Blind-overwrite guard on whole-file writes

**Value: high (prevents the worst failure class). Effort: low.**
**Ledger in the base `Agent` loop; enforcement in the file-I/O mixin.**

**Evidence.** Claude Code's harness refused **336** attempts to write a file the agent had
never read ("File has not been read yet") and **63** attempts to write a file changed since
it was read — together 8.6% of all 4,624 edit calls, and the third-largest error class in
the corpus (418 of 2,096 failures, 19.9%). Those are 399 prevented overwrites. The
user-visible tail of the same problem, when it is *not* prevented, is the `lost_work`
cluster: "you overwrote a lot of solid results", "now I lost all the token economics".

**GAIA's current position is half-safe, and the safe half should be credited.**
`edit_python_file` / `edit_file` re-read the file at edit time and require an exact
`old_content` substring match (`file_io_tools.py:338-345`), so a blind edit is already
impossible. `write_python_file` / `write_file` have no such check
(`file_io_tools.py:188-268`) — they validate the path, take a `.bak`, and overwrite. The
backup is a real mitigation and a genuine advantage over harnesses that have none, but
recovery-after-the-fact is not prevention, and the model is never told the file changed.

**Concrete shape.** The loop keeps a `path → content-hash-when-last-seen` ledger, written
by any tool that reads or writes a file. A whole-file write to a path absent from the
ledger, or whose on-disk hash no longer matches, returns an actionable error naming the
path and the required next action. The ledger belongs in the loop, not the mixin, so that
RAG, shell, and file-I/O all share one view of what the agent has actually seen.

**Generality.** This is optimistic concurrency control on any addressable resource — file,
config key, remote record. Nothing about it is file-system-specific.

---

### 4. Background execution with progress and polling

**Value: high. Effort: high. Loop primitive + a process mixin.**

**Evidence, two independent measurements pointing the same way.**

*Failures:* timeouts are the **single largest tool-failure class** — 555 of 2,096 (26.5%).
The most-timed-out binary is `sleep` (249, 44.8% of timed-out shell calls): the agent's
only way to wait for external work is to block itself. Separately, 4,561 shell calls
(13.2%) explicitly raised their timeout above the harness default, and **2,572 of them
(7.5% of all shell calls) asked for more than 180 seconds** — GAIA's hard
`DEFAULT_TOOL_TIMEOUT` ceiling (`agent.py:140`).

*Humans:* the stall cluster — 19 turns across 12 sessions (10.4% of interactive) — is
entirely people asking a blocked agent what it is doing: "what is taking so long", "you've
been running for hours", "this has been running for almost 40 mins, what's the result?",
"stop sleeping and get the job done".

**GAIA today.** `_call_tool_bounded` (`agent.py:3149`) runs each tool in a **daemon thread
joined with a timeout**. On expiry it raises `ToolExecutionTimeout` and returns — but
Python cannot kill the thread, so the work continues invisibly, holding whatever resources
it holds, with no handle to poll or cancel. Cancellation is checked only at the top of a
loop iteration (`agent.py:4577`) and per streamed token (`4982`), never mid-tool.

**Concrete shape.** A tool may return a *handle* instead of a result; the loop records it,
keeps taking steps, and surfaces completion as an observation on a later step. That needs
three things in the loop — a handle registry, a "still running: N" line in the step header
the console already prints, and cancellation that reaches a running handle — plus mixin
support for starting and polling a process.

**Effort is genuinely high** and it interacts with the confirmation gate and with
cancellation semantics. It is ranked fourth for that reason, not because the evidence is
weaker.

---

### 5. Sub-agent delegation

**Value: high. Effort: very high. New capability alongside the base `Agent`.**

**Evidence.** 119 of 345 sessions delegate (34.5%), spawning 488 subagent runs — 4.1 per
delegating session. Those subagents execute **33.2% of all tool calls in the corpus**
(17,352 of 52,307). A median subagent run is 33 tool calls (p90 = 63, max = 123): a real
unit of work, not a lookup. Subagents also fail slightly *less* than main sessions (3.26%
vs 4.38%), so delegation is not degrading quality here.

**GAIA has none.** Grepping `src/gaia/agents/base/` and `src/gaia/agents/tools/` for
delegation, sub-agent, or spawn concepts returns exactly one hit, and it is the word
"delegates" in a `console.py` docstring.

**Be honest about the first benefit.** The obvious pitch is parallelism, but the measured
one is **context containment**: a third of all tool work — and the tool results it drags
in — never enters the parent's context at all. On a device pinned to a 32K (NPU) or 64K
(GPU) window, that is the difference between finishing a long task and hitting
`_shrink_messages_for_overflow`. Parallel speedup on a single local model slot is a
secondary and much weaker claim, since one model serves everything.

---

### 6. Raise or make explicit the step budget

**Value: medium. Effort: trivial.**

`DEFAULT_MAX_STEPS = 50` (`agent.py:105`). In this corpus, main sessions took a median of
**55 tool-bearing loop iterations** (p90 = 209, max = 1,199): **54.1% of sessions would
have hit GAIA's default ceiling.** Counting raw tool calls rather than iterations, 59.1%
exceed 50 and 33.6% exceed 100.

Hitting the limit produces `_generate_max_steps_message` — a summary, not an error — which
from the user's seat is indistinguishable from the agent deciding it was finished. That is
a plausible contributor to the largest cluster in the table (premature stop, 40.0% of
interactive sessions), though the transcripts cannot separate "the agent stopped early"
from "the harness stopped it" for Claude Code, so this link is inference, not measurement.

Two low-cost fixes, neither of which is "make the number bigger and hope": raise the
default to cover the measured p50 with headroom, and make budget exhaustion *visibly
distinct* from completion in the returned answer and in the console.

---

## Already fine — do not build these

**Parallel tool calls are handled correctly.** 17.8% of tool-bearing model responses carry
more than one `tool_use` block, and those batches contain **33.0% of all tool calls**;
84.5% of traces use at least one. GAIA's native fan-out (`agent.py:5600-5623`) drains the
whole batch inside **one** loop iteration with **one** LLM round-trip and charges **one**
step. Serialising the corpus's batches into one model call each would have cost **+22.7%
model round-trips** — on a local device, that many extra full-context prefills. GAIA
already avoids that. The only thing left on the table is *concurrent execution* of the
batch for wall-clock; since batches are overwhelmingly `Bash` (3,766) and `Read` (891),
both fast and local, that is a modest win and not worth the reentrancy risk today.

**Do not build a TodoWrite clone.** Todo/plan tooling was used in **36 of 814 traces
(4.4%)** despite a median session of 64 tool calls. GAIA already has a *richer* mechanism —
`STATE_EXECUTING_PLAN` with `$PREV` / `$STEP_N` parameter substitution
(`agent.py:3074-3109`) — and the evidence does not support investing further. The user's
"continue" turns are not a symptom of lost task state; they follow completion claims and
stalls, which items 1 and 4 address.

**The confirmation gate is good, and structurally better than what produced 92
`user_rejected` errors in this corpus.** GAIA's is declarative
(`confirmation_required_tools`, per-registry `requires_confirmation`,
`_confirmation_denied_error` explaining the denial back to the model) rather than a
per-call prompt. Leave it alone. The one adjacent finding worth noting is the
`manual_handoff` cluster (34 turns), but 18 of 34 sit in a single session about cloud
credentials — that is capability scope, which a sibling task owns, not loop design.

**Result truncation is sized about right.** Tool results are p50 = 648 chars, p90 = 6,074,
p99 = 30,364, max = 104,924. Only 2.02% exceed 20,000 chars — GAIA's default
`_truncate_large_content` budget. The 20K cap clips a genuine tail rather than routine
traffic.

**The deliberate absence of context compaction is not hurting.** 266 results over 40K
chars across 52K calls, and one-shot `_shrink_messages_for_overflow` on overflow. Nothing
in the friction data points at compaction as the missing piece.

---

## Explicitly out of scope for the loop

**Verbosity (41 turns, 26 sessions, 22.6% of interactive)** is the third-largest cluster —
"summarize in plain english" appears repeatedly, alongside "too much text", "still too
long", "that is a lazy response". It is real and it generalises ("the agent over-explains
and buries the answer"), but it is a **system-prompt and console concern**, not a loop
invariant. It belongs where the repo's own communication rule already lives. Note also
that 7 of the 41 come from one session, so the true rate is lower than the headline.

**Scope drift (28 turns, 21 sessions, 18.3% of interactive)** — "STOP, you drifted onto
macOS work", "the flagship is NOT gaia bash", "don't touch agent UI". The instinct is a
loop-level scope enforcer; the evidence does not support one. Almost every instance is the
user *narrowing* a goal mid-task, which is new information, not agent error. Precision on
this cluster is ~74% before filtering standing/cron prompts and meeting transcripts (38
raw → 28 verified), the weakest of any cluster here. Low priority, and the honest read is
that better up-front goal capture — an existing prompt/skill concern — would help more than
loop machinery.

---

## Summary

| # | Change | Where | Value | Effort | Headline evidence |
|---|---|---|---|---|---|
| 1 | Completion gate on unobserved mutations | base loop | Highest | Med | 27.8% of interactive sessions; 50% follow a completion claim |
| 2 | Stuck-detection past identical repeats + execution-error bail | base loop | Med-high | **Low** | detector catches 1.3% of a 63.4% retry population |
| 3 | Blind-overwrite guard (read-ledger) | loop state + file mixin | High | **Low** | 399 overwrites prevented elsewhere; 8.6% of edit calls |
| 4 | Background execution, progress, cancel | loop + process mixin | High | High | timeouts = 26.5% of all failures; 7.5% of shell calls want >180s |
| 5 | Sub-agent delegation | new capability | High | V. high | 33.2% of all tool calls; 34.5% of sessions |
| 6 | Step budget: raise, and distinguish exhaustion from completion | base loop | Med | **Trivial** | 54.1% of sessions exceed 50 iterations |

Items 2, 3 and 6 are together perhaps a few hundred lines against machinery that already
exists, and they cover the two failure modes that destroy user trust fastest: silent data
loss and a stuck loop that looks like a finished one. Item 1 is the one that would move the
largest correction cluster.
