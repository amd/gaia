---
name: analyzing-claude-sessions
description: Analyze your own local Claude Code session transcripts to find what you actually use the agent for, how effective it is, where it fails, and what it costs. Use when asked to mine Claude Code sessions, extract use-cases or workflows from session history, measure agent effectiveness or error rates, analyze token/cost usage, or produce a report on how an agent is really being used.
---

# Analyzing your Claude Code sessions

Claude Code writes a full JSONL transcript of every session to `~/.claude/projects/`.
That is a complete record of what an agent was asked to do, every tool call it made,
what failed, and what it cost. This skill turns that exhaust into an evidence-backed
report.

**The core pipeline is deterministic Python** (`gaia.factory.harvest`) — no LLM, no
network. Two optional steps do call a model through the local Claude Code CLI:
`classify` (use-case labels) and `synthesize` (the written analysis). Everything derived is written to `~/.gaia/cache/factory/` and
must never be committed — transcripts contain absolute paths, branch names, and whatever
the user pasted into a prompt.

## Run it

Redirect into the cache directory, never into a repository working tree. These tables
are built from real transcripts, and an untracked `tables.md` sitting at a repo root is
one `git add -A` away from being published.

```bash
FACTORY=~/.gaia/cache/factory

# 1. Extract. Deterministic, no LLM, no network. Roughly linear in transcript count.
python -m gaia.factory.harvest.scan

# 2. Tables. Absolute counts and share-of-total for every figure.
python -m gaia.factory.harvest.report > "$FACTORY/tables.md"

# 3. With use-case labels (see "Classifying intent" below):
python -m gaia.factory.harvest.report --labels "$FACTORY/labels.txt" > "$FACTORY/tables.md"

# 4. Per-request prompt size and local KV-cache memory. Re-reads the raw
#    transcripts, because steps 1-2 aggregate per session and that hides how
#    large any single request got.
python -m gaia.factory.harvest.context --labels "$FACTORY/labels.txt" > "$FACTORY/context.md"

# 5. What each proposed fix would actually save, in tokens and dollars.
python -m gaia.factory.harvest.savings > "$FACTORY/savings.md"

# 6. Optional, needs the Claude Code CLI: label use cases, then write the analysis.
python -m gaia.factory.harvest.classify
python -m gaia.factory.harvest.synthesize > "$FACTORY/analysis.md"
```

Only `scan` takes `--root` (transcripts elsewhere); `scan` and `classify` write with
`--out`. Every other step reads the cache with `--cache`, and `context` and `savings`
also take `--projects` if the raw transcripts are not under `~/.claude/projects`.

Outputs in `~/.gaia/cache/factory/`:

| File | Contents |
|---|---|
| `traces.jsonl` | One normalized trace per session: ordered steps, outcomes, tokens, subagents |
| `intents.jsonl` | One line per session: goal, title, steps, turns, tokens, cost, duration |
| `stats.json` | Every aggregate, including the `effectiveness` and `error_profile` blocks |

## The five things that will mislead you

Learned by getting each one wrong first.

**1. Subagents are not sessions.** A delegated `Task`/`Agent` run gets its own transcript
under `<session-uuid>/subagents/`. A plain `*/*.jsonl` glob misses them entirely, and they
can carry a large share of all tool calls in a delegation-heavy corpus. `iter_traces`
attaches them to the parent; use `Trace.walk()` to include them and say explicitly which
scope a number covers.

**2. Identity must hash the full arguments.** Keying a tool call on one argument (the file
path, say) makes consecutive *different* edits to one file look identical. That single
mistake overstated a "thrash" metric ~40×. `Step.arg_hash` covers the whole argument
object; `arg_digest` is for display only. Never compute identity from the digest.

**3. Order your error taxonomy specific-before-generic.** Matching is first-wins, and a
timeout also carries a non-zero exit code. With `command_failed` above `timeout`, real
timeouts — often the largest failure class — get filed as generic command failures.

**4. Rank savings by carried tokens, not by call count.** A prompt is the whole
conversation so far, so a result emitted at step 5 of a 50-step run is re-sent 45 more
times. That carry multiplier decides what a fix is worth. Expect the two rankings to
disagree: re-reads can be a large share of reads by count yet a small share of tokens,
while budgeting oversized results is worth several times more. `savings.py`
computes both; report the token figure and say which one a claim rests on.

**5. Harness turns are not human turns.** Hook feedback and system reminders appear as
`user` records. They match correction patterns like "stop" and inflated a "user corrected
the model" metric 8×. Filter on the `isMeta` field; a `startswith("<")` heuristic does not
catch them.

## Classifying intent

`scan` produces `intents.jsonl` but assigns no use-case. Run
`python -m gaia.factory.harvest.classify`, which batches the sessions, labels each
from its **opening instruction** against a closed taxonomy, and writes
`$FACTORY/labels.txt` as `<8-char-session-prefix> <use-case>` — one tag per line, the
format `report --labels` and `context --labels` read. Then re-run both with `--labels`.

Three properties matter, and they are enforced rather than advised:

- **The taxonomy is closed.** A batch that invents tags makes its per-use-case tables
  incomparable with every other batch.
- **Nothing partial is written.** A batch whose labels do not cover it exactly is an
  error; `labels.txt` appears only once every batch has validated.
- **`no_instruction` is assigned locally.** A session whose transcript carries no
  opening instruction cannot be classified from one, and folding those into `other`
  hides them among sessions that did state a goal.

Classify from the first user message, never the auto-generated title — the title
summarises what happened, which leaks the outcome into the label.

To label by hand, write the same two-column file yourself. It carries session-id
prefixes, so it belongs in the cache directory like everything else derived. `report`
checks those prefixes against the corpus either way: nothing matching is an error, and
partial coverage is stated in every use-case table so the labelled subset is never read
as the whole corpus.

## What to actually report

Raw counts alone mislead. The findings that carried signal in the reference corpus:

- **Token composition, not just totals.** Cache-read vs cache-write vs output. Agentic
  coding is overwhelmingly context, with output a rounding error.
- **Binary frequency inside shell commands.** The single richest signal — it shows which
  tools the model *actually* reaches for versus which ones it was given.
- **Failure rate per tool, not corpus-wide.** A corpus-wide average hides wide per-tool
  variation — always break failure rate out per tool.
- **Failure rate by position in the session.** Tests whether reliability decays as context
  fills. Measure it rather than assuming decay; it may well be flat.
- **What happens after a failure** — recovery rate and streak length separate "handles
  errors well" from "gets stuck".
- **Main-session vs subagent rates**, which isolates the cost of write capability.

## Honesty requirements

These are not optional; the analysis is worthless without them.

- **There is no ground truth for task success.** Nothing in a transcript says whether the
  goal was met. Never present a tool-failure rate as a task-failure rate.
- **Friction signals are regex proxies.** Corrections and interrupts are evidence, not
  proof. Compare rates across use-cases; do not quote absolutes as fact.
- **Cost is API-equivalent**, not money spent, if the sessions ran on a subscription.
- **Duration is unusable past the median** — a session left open overnight reports the
  whole night.
- **The corpus grows while you analyse it.** The session doing the analysis is itself
  being recorded, so counts drift between runs. Timestamp the snapshot.
- **Every derived table needs a column glossary.** A column nobody can define is a column
  nobody should trust.

## Privacy

`scan` covers **every Claude Code project on the machine**, not just the current repo:
`~/.claude/projects/` holds one subdirectory per project, and there is no per-project
filter — `--root` relocates the scan, it cannot narrow it. `tables.md` never breaks the
count down by project, so check `top_projects` in `stats.json` to see the mix before
sharing anything.

Transcripts contain absolute paths, branch names, repository content, and any secret
pasted into a prompt. The pipeline writes only to `~/.gaia/cache/factory/`. If a report is
shared, put it somewhere private and scrub paths first. Nothing derived from a corpus
belongs in a public repository.

## Verifying the analysis

Before publishing, have a subagent adversarially review the *extraction code* against the
corpus — not just the prose. Four of the reference report's numbers were wrong on first
pass, including its headline, and every one was a bug in the metric rather than a mistake
in the writing. Ask specifically: does each metric measure what its name claims, and is
the denominator the one the sentence implies?
